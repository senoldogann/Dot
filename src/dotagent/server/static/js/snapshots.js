// SnapShots on the page (item 405): the composer on screen takes the captures the daemon keeps for it, as they come (a
// lifecycle event) and when the composer is made (the ones that waited), and the page says why one failed.

import { api } from "./api.js";
import { onLifecycle } from "./state.js";
import { toast } from "./ui.js";

// The composer that takes captures: the last one made with a draft of its own.
let current = null;
// Captures claimed while no composer was on screen, for the next one.
let held = [];
let wired = false;

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

function deliver() {
  if (current === null) {
    return;
  }
  const taking = held;
  held = [];
  for (const capture of taking) {
    current(capture.files, capture.animated);
  }
}

function wire() {
  if (wired) {
    return;
  }
  wired = true;
  onLifecycle((event) => {
    if (event.kind === "snapshot_taken") {
      // Only a page with a box claims: with none, the daemon keeps the capture until a box is made.
      if (current !== null) {
        void claim();
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
  void claim();
  return () => {
    if (current === take) {
      current = null;
    }
  };
}
