// What every page shares: the daemon status, the approvals that wait for an answer, and the live event stream.

import { api, eventNotShown, follow, tellOutOfDate, unreachable } from "./api.js";
import { debounce } from "./dom.js";

const REFRESH_EVERY_MS = 15000;
// After a break of the stream, or once the page is back in view, the network may not answer every request at once (a
// phone that wakes): for this long a read that got no answer is read again a moment later, quietly, and told only if it
// fails after that (item 286).
const SETTLE_MS = 10000;
const READ_AGAIN_AFTER_MS = 2500;

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

// Until when the page settles after a break or a return into view (`SETTLE_MS`).
let settlingUntil = 0;

function settle() {
  settlingUntil = Date.now() + SETTLE_MS;
}

/** Whether a read that failed with `error` is read again in a moment rather than told: one that got no answer while the
 * page settles. */
function readSoonInstead(error) {
  if (!unreachable(error) || Date.now() >= settlingUntil) {
    return false;
  }
  readAgainSoon();
  return true;
}

/**
 * Every page that follows the stream reads again what it shows, each even when one before it fails to; a read that
 * fails, at once or later, is told like an event the page could not show (item 160), unless it got no answer while the
 * page settles: it is read again then (item 286).
 */
function readAgain(event) {
  for (const listener of backListeners) {
    Promise.resolve()
      .then(listener)
      .catch((error) => {
        if (!readSoonInstead(error)) {
          eventNotShown("/stream", { id: 0, event }, error);
        }
      });
  }
}

const readAgainSoon = debounce(() => readAgain("settled"), READ_AGAIN_AFTER_MS);

/**
 * Tell that a page could not read again what it shows, after an event of the stream or once the stream is back: logged,
 * and told in a toast while the stream is live (item 160). While the stream is down the page says so already, and it
 * reads again once the stream is back; a read that got no answer while the page settles is read again a moment later
 * (item 286). The page keeps what it showed.
 */
export function readFailed(what, error) {
  console.warn(`${what} could not be read again`, error);
  if (readSoonInstead(error)) {
    return;
  }
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
  const onBack = () => {
    if (document.visibilityState === "visible") {
      settle();
    }
  };
  document.addEventListener("visibilitychange", onBack);
  signal.addEventListener("abort", () => document.removeEventListener("visibilitychange", onBack), { once: true });
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
          settle();
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
