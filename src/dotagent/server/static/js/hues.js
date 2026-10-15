// The ring of eight (item 83): an actor without a look of its own (a delegated run, a campaign, a schedule, an agent
// not yet dressed) wears the shape and the colour its id's hash picks, so it keeps its face wherever it shows. Pure:
// the unit test runs it in node against a Python mirror of the hash.
import { SHAPE_IDS } from "./character/skins.js";

export const HUES = 8;
// The colour of each hue, among the character's twelve; the shape of a hue is the shape of the same index.
export const HUE_COLOURS = ["red", "orange", "green", "teal", "blue", "violet", "pink", "brown"];

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

/** The look of a hue: its shape, the neutral expression, its colour. */
export function hueLook(hue) {
  return { shape: SHAPE_IDS[hue], expression: "neutral", colour: HUE_COLOURS[hue] };
}

/** The look of a chat with Dot (item 277): a shape and a colour of the ring, each picked from its id's hash on its own,
 * so that the creatures of the chats differ more than the eight hues do. */
export function chatLook(id) {
  const hash = hashId(id);
  return { shape: SHAPE_IDS[hash % HUES], expression: "neutral", colour: HUE_COLOURS[Math.floor(hash / HUES) % HUES] };
}
