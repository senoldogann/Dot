// Dot's face as the state: one character (actors.js), five states, the same wherever it shows. `idle` is still,
// `working` wears the attentive look and pulses, `waiting` wears the notification pastille, `done` smiles for a moment,
// `failed` is sad. The favicon and the notification icon carry the same face, drawn as a standalone SVG. The name Dot
// answers to in the greeting and at the head of a run is a preference of this browser.

import { DOT_ID, FACE_OF_STATE, lookOf } from "./actors.js";
import { stillSvg } from "./character/still.js";
import { readPref, writePref } from "./prefs.js";

const NAME_KEY = "dot.name";
const DEFAULT_NAME = "Dot";
export const NAME_CHARS = 24;
const STATE_OF_STATUS = { queued: "working", running: "working", blocked: "waiting", failed: "failed" };

/** The state the face shows for a run being looked at. A run that ended done, or was cancelled, rests. */
export function faceState(status) {
  return STATE_OF_STATUS[status] ?? "idle";
}

/** The state of the face at the moment a run ends: the tick for a run that ended done, else as `faceState`. */
export function faceStateAtEnd(status) {
  return status === "done" ? "done" : faceState(status);
}

/** What the whole of Dot is at: waiting when something waits for you, working while a run is active, else idle. */
export function overallState(snapshot) {
  if (snapshot.approvals.length > 0) {
    return "waiting";
  }
  const active = snapshot.status === null ? 0 : snapshot.status.active_runs.length;
  return active > 0 ? "working" : "idle";
}

/** The face as a standalone SVG document, for the favicon and the notification icon: Dot's character in the state. */
export function faceSvg(state) {
  const { animation, expression } = FACE_OF_STATE[state];
  return stillSvg(lookOf(DOT_ID, null), animation, expression, state);
}

export function faceDataUrl(state) {
  return `data:image/svg+xml,${encodeURIComponent(faceSvg(state))}`;
}

/** The name Dot answers to here. */
export function dotName() {
  const saved = readPref(NAME_KEY);
  return saved === null || saved.trim() === "" ? DEFAULT_NAME : saved;
}

/** Set the name; blank goes back to Dot. Returns the name in use. */
export function setDotName(name) {
  const trimmed = name.trim().slice(0, NAME_CHARS);
  writePref(NAME_KEY, trimmed === "" ? DEFAULT_NAME : trimmed);
  return dotName();
}
