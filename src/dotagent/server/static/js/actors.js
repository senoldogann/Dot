// The faces of the actors (items 83 and 102). Every actor (Dot, an agent, a delegated run, a campaign, a schedule) is a
// character (character/): an agent wears the look the owner chose for it, Dot its own, and an actor without a look the
// shape and colour its id's hue gives it, one of a ring of eight, so it keeps its face wherever it shows: the rows of a
// chat, the sidebar, the Campaigns and Schedules pages, the bar of its own page. A face is a still frame; its state
// (face.js) picks the animation posed and the expression worn.
import { api } from "./api.js";
import { mountCharacter } from "./character/character.js";
import { SHAPE_IDS } from "./character/skins.js";

export const HUES = 8;
export const DOT_ID = "dot";
// The colour of each hue, among the character's twelve; the shape of a hue is the shape of the same index.
const FALLBACK_COLOURS = ["red", "orange", "green", "teal", "blue", "violet", "pink", "brown"];
// Dot before the owner chooses: the round face in the amber of its old badge.
const DOT_LOOK = { shape: "circle", expression: "neutral", colour: "amber" };
// How a face's state shows: the animation posed and, at rest, the expression that replaces the look's own.
export const FACE_OF_STATE = {
  idle: { animation: "idle", expression: null },
  working: { animation: "idle", expression: "attentive" },
  waiting: { animation: "notify", expression: null },
  done: { animation: "idle", expression: "happy" },
  failed: { animation: "idle", expression: "sad" },
};
// The looks the page knows, by actor id: filled from the agent lists; an actor not here wears its fallback.
const looks = new Map();
// The character behind each face on the page, for a change of state.
const handles = new WeakMap();

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

/** The look an actor without one wears: Dot its own, any other the shape and colour of its hue. */
export function fallbackLook(id) {
  if (id === DOT_ID) {
    return DOT_LOOK;
  }
  const hue = hueOf(id);
  return { shape: SHAPE_IDS[hue], expression: "neutral", colour: FALLBACK_COLOURS[hue] };
}

/** Keep an actor's look for the faces drawn later; `null` forgets it. */
export function rememberLook(id, look) {
  if (look === null) {
    looks.delete(id);
  } else {
    looks.set(id, look);
  }
}

/** The look of an actor: `look` when the caller has the record, else what the page remembers, else the fallback. */
export function lookOf(id, look) {
  if (look !== null) {
    return look;
  }
  return looks.get(id) ?? fallbackLook(id);
}

/** Whether the actor wears a look of its own rather than the fallback. */
export function hasLook(id, look) {
  return look !== null || looks.has(id);
}

/** Read every agent's look from the daemon, Dot's included, so the faces wear them. */
export async function loadLooks() {
  const agents = await api.get("/agents");
  for (const { record } of agents) {
    rememberLook(record.id, record.look);
  }
}

function expressionFor(look, state) {
  return FACE_OF_STATE[state].expression ?? look.expression;
}

/** The face of an actor in a state (face.js): a still character wearing `look`, the remembered look or the fallback. */
export function actorFace(id, state, look) {
  const chosen = lookOf(id, look);
  const fragment = document.createDocumentFragment();
  const handle = mountCharacter(fragment, {
    shape: chosen.shape,
    expression: expressionFor(chosen, state),
    colour: chosen.colour,
    size: 40,
    moving: false,
    state: FACE_OF_STATE[state].animation,
    tight: true,
    phase: 0,
  });
  const face = handle.element;
  face.classList.add("avatar");
  face.dataset.state = state;
  if (!hasLook(id, look)) {
    face.dataset.hue = String(hueOf(id));
  }
  handles.set(face, { handle, look: chosen });
  return face;
}

/** Dot's own face in a state. */
export function dotFace(state) {
  return actorFace(DOT_ID, state, null);
}

/** Change the state a face shows: the expression and the animation follow. */
export function setActorState(face, state) {
  face.dataset.state = state;
  const entry = handles.get(face);
  if (entry === undefined) {
    return;
  }
  entry.handle.setExpression(expressionFor(entry.look, state));
  entry.handle.setState(FACE_OF_STATE[state].animation);
}
