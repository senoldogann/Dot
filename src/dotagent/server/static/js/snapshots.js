// SnapShots on the page (item 405): the composer on screen takes the captures the daemon keeps for it, as they come (a
// lifecycle event) and when the composer is made (the ones that waited), and the page says why one failed. Only the
// pages of this Mac take them (item 414): a capture is made here, and a phone's page, reached through [server] reach,
// would take it away from the Mac's box. A page claims a capture only once it is in sight, so that the tab the daemon
// brings to the front takes it rather than a hidden one, and its spring is seen; and a page of this Mac with no box goes
// to the new chat, whose box takes it, only when it is the page brought to the front for the capture (it comes into
// sight, or into focus, within a few seconds), never a page the reader has in sight elsewhere.

import { api } from "./api.js";
import { onLifecycle } from "./state.js";
import { toast } from "./ui.js";

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
// How long a page with no box waits to be brought to the front before it leaves the capture to the box made next.
const SHOWN_WITHIN_MS = 3000;

/** Whether the page runs on this Mac (its address is the loopback one), where captures are made. */
function onThisMac(hostname) {
  return LOCAL_HOSTS.has(hostname);
}

// The composer that takes captures: the last one made with a draft of its own.
let current = null;
// Captures claimed while no composer was on screen, or while the page was hidden, for the next delivery.
let held = [];
let wired = false;
let waitingToBeSeen = false;
// Whether a claim already waits for the page to come into sight, so that it claims once when it does.
let claimWaits = false;

async function claim() {
  let claimed;
  try {
    claimed = await api.post("/snapshots/claim", null);
  } catch (error) {
    console.warn("the captures of SnapShots could not be claimed", error);
    return;
  }
  held.push(...claimed.files.map((taken) => ({ files: taken.files, animated: claimed.animations })));
  deliver();
}

/** Hand what is held to the box, once the page is seen. */
function deliver() {
  if (current === null || held.length === 0) {
    return;
  }
  if (document.visibilityState !== "visible") {
    if (!waitingToBeSeen) {
      waitingToBeSeen = true;
      document.addEventListener(
        "visibilitychange",
        () => {
          waitingToBeSeen = false;
          deliver();
        },
        { once: true },
      );
    }
    return;
  }
  const taking = held;
  held = [];
  for (const capture of taking) {
    current(capture.files, capture.animated);
  }
}

/** Run `then` once the page is brought to the front: it comes into sight (it was hidden) or into focus (it was in
 * sight in a window that had not the focus) within a few seconds; never for a page the reader has in focus now. */
function whenBroughtForward(then) {
  if (document.visibilityState === "visible" && document.hasFocus()) {
    return;
  }
  const brought = () => {
    if (document.visibilityState === "visible") {
      stop();
      then();
    }
  };
  const stop = () => {
    clearTimeout(late);
    document.removeEventListener("visibilitychange", brought);
    window.removeEventListener("focus", brought);
  };
  const late = setTimeout(stop, SHOWN_WITHIN_MS);
  document.addEventListener("visibilitychange", brought);
  window.addEventListener("focus", brought);
}

/** Claim the captures once the page is in sight: a hidden tab leaves them to the one the daemon brings forward. */
function claimInSight() {
  if (document.visibilityState === "visible") {
    void claim();
    return;
  }
  if (claimWaits) {
    return;
  }
  claimWaits = true;
  const seen = () => {
    if (document.visibilityState === "visible") {
      document.removeEventListener("visibilitychange", seen);
      claimWaits = false;
      if (current !== null) {
        void claim();
      }
    }
  };
  document.addEventListener("visibilitychange", seen);
}

function wire() {
  if (wired) {
    return;
  }
  wired = true;
  onLifecycle((event) => {
    if (event.kind === "snapshot_taken") {
      if (!onThisMac(location.hostname)) {
        return;
      }
      // Only a page with a box claims: with none, the daemon keeps the capture until a box is made.
      if (current !== null) {
        claimInSight();
      } else if (event.data.open === true) {
        whenBroughtForward(() => {
          if (current === null) {
            location.hash = "#/";
          }
        });
      }
    } else if (event.kind === "snapshot_failed") {
      toast("error", `SnapShots: ${event.data.reason}.`);
    }
  });
}

/** From now, `take(files, animated)` gets the captures: the ones that waited and each new one. Returns what stops it. */
export function takesSnapshots(take) {
  wire();
  current = take;
  if (onThisMac(location.hostname)) {
    claimInSight();
  }
  return () => {
    if (current === take) {
      current = null;
    }
  };
}
