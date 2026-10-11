// The faces of the actors (item 83). Dot is the yellow dot. Every other actor (a delegated run, a campaign, a schedule)
// wears the same mark in a hue of its own, one of a ring of eight chosen by a hash of its id, so it keeps its face
// wherever it shows: the rows of a chat, the sidebar, the Campaigns and Schedules pages, the bar of its own page.
import { avatar } from "./icons.js";

export const HUES = 8;

/** FNV-1a over the UTF-16 code units of `id`: a 32-bit unsigned number, the same for the same id everywhere. */
export function hashId(id) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/** The hue of an actor, 0 to 7, from its id. */
export function hueOf(id) {
  return hashId(id) % HUES;
}

/** The face of an actor other than Dot: the mark in the actor's hue, in the given state (face.js). */
export function actorFace(id, state) {
  const face = avatar(state);
  face.dataset.hue = String(hueOf(id));
  return face;
}
