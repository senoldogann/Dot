// Who Dot is to its owner (item 420): the name the owner calls it by and the owner's own, kept by the daemon
// (GET and PUT /api/assistant), so that every browser says the same. This browser keeps a copy for what draws before
// the daemon answers (face.js's dotName, presence.js's owner); a name it saved before the daemon kept them moves there
// once.

import { api } from "./api.js";
import { dotName, setDotName } from "./face.js";
import { MAC_OWNER_KEY, ownerName, setOwnerName } from "./presence.js";
import { readPref, writePref } from "./prefs.js";
import { toast } from "./ui.js";

const DEFAULT_NAME = "Dot";
// Set once this browser's names went to the daemon, so that a later change elsewhere is never undone from here.
const MOVED_KEY = "dot.names.moved";

let current = null;
const listeners = new Set();

/** The names as the daemon last said them, or null before it answered. */
export function assistant() {
  return current;
}

/** Call `listener(view)` each time the names change; returns what stops it. */
export function onAssistant(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function keep(view) {
  current = view;
  setDotName(view.name);
  setOwnerName(view.owner);
  writePref(MAC_OWNER_KEY, view.mac_name ?? "");
  for (const listener of listeners) {
    listener(view);
  }
  return view;
}

/** The daemon's names, after moving there the ones this browser kept before (once). */
export async function loadAssistant() {
  const view = await api.get("/assistant");
  if (readPref(MOVED_KEY) !== null) {
    return keep(view);
  }
  const changes = {};
  if (view.name === DEFAULT_NAME && dotName() !== DEFAULT_NAME) {
    changes.name = dotName();
  }
  if (view.owner === "" && ownerName() !== "") {
    changes.owner = ownerName();
  }
  if (Object.keys(changes).length === 0) {
    writePref(MOVED_KEY, "1");
    return keep(view);
  }
  try {
    const moved = await api.put("/assistant", changes);
    writePref(MOVED_KEY, "1");
    return keep(moved);
  } catch (error) {
    // The daemon's names stand; the owner hears why this browser's did not move, and can give them in Settings.
    writePref(MOVED_KEY, "1");
    toast("error", `The names this browser kept could not move to Dot: ${error.message}`);
    return keep(view);
  }
}

/** Change what `changes` gives (`name`, `owner`, `introduced`); returns the names as they are now. */
export async function saveAssistant(changes) {
  return keep(await api.put("/assistant", changes));
}
