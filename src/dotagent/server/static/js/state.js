// What every page shares: the daemon status, the approvals that wait for an answer, and the live event stream.

import { api, eventNotShown, follow } from "./api.js";
import { debounce } from "./dom.js";

const REFRESH_EVERY_MS = 15000;

const snapshot = {
  status: null,
  approvals: [],
  stream: "connecting",
};

const changeListeners = new Set();
const lifecycleListeners = new Set();

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

export async function refresh() {
  const [status, approvals] = await Promise.all([api.get("/status"), api.get("/approvals?status=pending&limit=100")]);
  snapshot.status = status;
  snapshot.approvals = approvals;
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
        snapshot.stream = state;
        announce();
        if (state === "live") {
          refreshSoon();
        }
      },
      onMessage: (message) => {
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
