// What every page shares: the daemon status, the approvals that wait for an answer, and the live event stream.

import { api, eventNotShown, follow, tellOutOfDate } from "./api.js";
import { debounce } from "./dom.js";

const REFRESH_EVERY_MS = 15000;

const snapshot = {
  status: null,
  approvals: [],
  stream: "connecting",
  // The daemon serves other pages than this one was loaded from (Dot was updated meanwhile): it is loaded again to
  // match them.
  outdated: false,
};
// What the pages were when this page read the daemon's status first (`pages` of `GET /status`).
let loadedPages = null;

const changeListeners = new Set();
const lifecycleListeners = new Set();
const backListeners = new Set();

export function getSnapshot() {
  return snapshot;
}

function announce() {
  for (const listener of changeListeners) {
    listener(snapshot);
  }
}

/** Run `listener` whenever the status, the approvals or the stream state change. Returns the function that stops it. */
export function onChange(listener) {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

/** Run `listener` for every run and approval event of the daemon. Returns the function that stops it. */
export function onLifecycle(listener) {
  lifecycleListeners.add(listener);
  return () => lifecycleListeners.delete(listener);
}

/**
 * Run `listener` each time the lifecycle stream is live again after a break. The stream replays nothing, and what
 * changed meanwhile may have sent no event at all (a restart closes the runs it finds active), so a page reads again
 * what it shows from the stream's events (item 160). Returns the function that stops it.
 */
export function onStreamBack(listener) {
  backListeners.add(listener);
  return () => backListeners.delete(listener);
}

export async function refresh() {
  const [status, approvals] = await Promise.all([api.get("/status"), api.get("/approvals?status=pending&limit=100")]);
  snapshot.status = status;
  snapshot.approvals = approvals;
  loadedPages ??= status.pages;
  snapshot.outdated = status.pages !== loadedPages;
  announce();
}

const refreshSoon = debounce(() => {
  refresh().catch((error) => console.warn("status refresh failed", error));
}, 120);

let modelsPending = null;

/** The models each provider offers; asked once, because providers can be slow to answer. */
export function loadModels() {
  if (modelsPending === null) {
    modelsPending = api.get("/models");
    modelsPending.catch(() => {
      modelsPending = null;
    });
  }
  return modelsPending;
}

export function forgetModels() {
  modelsPending = null;
}

/**
 * Every page that follows the stream reads again what it shows, each even when one before it fails to; a read that
 * fails, at once or later, is told like an event the page could not show (item 160).
 */
function readAgain(event) {
  for (const listener of backListeners) {
    Promise.resolve()
      .then(listener)
      .catch((error) => eventNotShown("/stream", { id: 0, event }, error));
  }
}

/**
 * Tell that a page could not read again what it shows, after an event of the stream or once the stream is back: logged,
 * and told in a toast while the stream is live (item 160). While the stream is down the page says so already, and it
 * reads again once the stream is back. The page keeps what it showed.
 */
export function readFailed(what, error) {
  console.warn(`${what} could not be read again`, error);
  if (snapshot.stream === "live") {
    const why = error instanceof Error ? error.message : String(error);
    tellOutOfDate(`${what} could not be read again: ${why}. Reload the page to see it.`);
  }
}

/** Follow the lifecycle stream of the daemon until `signal` aborts; also refreshes on a timer as a safety net. */
export async function runLifecycle(signal, onAuthRejected) {
  const timer = setInterval(() => {
    if (document.visibilityState === "visible") {
      refreshSoon();
    }
  }, REFRESH_EVERY_MS);
  signal.addEventListener("abort", () => clearInterval(timer), { once: true });
  const onFocus = () => refreshSoon();
  window.addEventListener("focus", onFocus);
  signal.addEventListener("abort", () => window.removeEventListener("focus", onFocus), { once: true });
  try {
    await follow({
      path: "/stream",
      after: 0,
      signal,
      isFinished: () => false,
      onState: (state) => {
        const back = state === "live" && snapshot.stream === "reconnecting";
        snapshot.stream = state;
        announce();
        if (state === "live") {
          refreshSoon();
        }
        if (back) {
          readAgain("reconnected");
        }
      },
      onMessage: (message) => {
        if (message.event === "resync") {
          // The stream fell behind and lost events: what every page shows is read again, as after a break.
          readAgain("resync");
          refreshSoon();
          return;
        }
        // Each listener gets the event even when one before it fails to show it.
        for (const listener of lifecycleListeners) {
          try {
            listener(message.data);
          } catch (error) {
            eventNotShown("/stream", message, error);
          }
        }
        refreshSoon();
      },
    });
  } catch (error) {
    if (error.status === 401) {
      onAuthRejected();
      return;
    }
    throw error;
  }
}
