// Dot's face as the state: one mark, five states, the same wherever it shows. `idle` is still, `working` breathes,
// `waiting` wears an amber ring, `done` shows a green tick for a moment, `failed` is red. The favicon and the
// notification icon carry the same face, drawn as a standalone SVG in the colours of the light look. The name Dot
// answers to in the greeting and at the head of a run is a preference of this browser.

import { readPref, writePref } from "./prefs.js";

const NAME_KEY = "dot.name";
const DEFAULT_NAME = "Dot";
export const NAME_CHARS = 24;
const STATE_OF_STATUS = { queued: "working", running: "working", blocked: "waiting", failed: "failed" };
// The standalone face: the yellow of the owner's bubbles, the mark in dark ink, the ring in the colour of the state.
const FACE_FILL = "#ffcd3c";
const INK = "#1a1a1a";
const RING_OF_STATE = { idle: INK, working: INK, waiting: "#8f5600", done: "#0b7a4b", failed: "#c0262d" };

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

export function setFaceState(face, state) {
  face.setAttribute("data-state", state);
}

/** The face as a standalone SVG document, for the favicon and the notification icon. */
export function faceSvg(state) {
  const ring = RING_OF_STATE[state] ?? INK;
  const muted = state === "idle" ? ' stroke-opacity="0.35"' : "";
  const mark =
    state === "done"
      ? `<path d="M10.5 16.5l3.5 3.5 7.5-7.5" fill="none" stroke="${ring}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`
      : `<circle cx="16" cy="16" r="${state === "working" ? 5.5 : 4.5}" fill="${state === "failed" ? ring : INK}"/>`;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" data-state="${state}">` +
    `<circle cx="16" cy="16" r="16" fill="${FACE_FILL}"/>` +
    `<circle cx="16" cy="16" r="9" fill="none" stroke="${ring}"${muted} stroke-width="2"/>` +
    `${mark}</svg>`
  );
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
