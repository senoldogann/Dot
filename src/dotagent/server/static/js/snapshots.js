// SnapShots on the page (item 405): the composer on screen takes the captures the daemon keeps for it, as they come (a
// lifecycle event) and when the composer is made (the ones that waited), and the page says why one failed. Only the
// pages of this Mac take them (item 414): a capture is made here, and a phone's page, reached through [server] reach,
// would take it away from the Mac's box. A capture taken while the page is hidden, Dot's tab coming to the front, is
// handed to the box once the page is seen, so its spring is seen too; and a page of this Mac with no box goes to the
// new chat, whose box takes it, when the daemon brought it to the front for the capture.

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

/** The daemon brings Dot's tab to the front with a capture: a page with no box goes to the new chat, whose box takes
 * it, if it is the page that came to the front; the others stay as they are. */
function goToABox() {
  const go = () => {
    if (current === null && document.visibilityState === "visible") {
      location.hash = "#/";
    }
  };
  if (document.visibilityState === "visible") {
    go();
    return;
  }
  const seen = () => {
    clearTimeout(late);
    go();
  };
  const late = setTimeout(() => document.removeEventListener("visibilitychange", seen), SHOWN_WITHIN_MS);
  document.addEventListener("visibilitychange", seen, { once: true });
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
        void claim();
      } else if (event.data.open === true) {
        goToABox();
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
    void claim();
  }
  return () => {
    if (current === take) {
      current = null;
    }
  };
}
