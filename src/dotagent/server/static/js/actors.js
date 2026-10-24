// The faces of the actors (items 83 and 102). Every actor (Dot, an agent, a delegated run, a campaign, a schedule) is a
// character (character/): an agent wears the look the owner chose for it, Dot its own, and an actor without a look the
// shape and colour its id's hue gives it (hues.js), so it keeps its face wherever it shows: the rows of a chat, the
// sidebar, the Campaigns and Schedules pages, the bar of its own page. A face is a still frame; its state (face.js)
// picks the animation posed and the expression worn.
import { api } from "./api.js";
import { mountCharacter } from "./character/character.js";
import { el } from "./dom.js";
import { hueLook, hueOf } from "./hues.js";
import { readPref, writePref } from "./prefs.js";

export const DOT_ID = "dot";
// Whether the big faces move (item 103): the home's, the run page's, the preview's; off leaves every face a still frame.
const MOVE_KEY = "dot.characters";
// A wink now and then while a live face rests: the pause between two, and the wink's own length.
const WINK_EVERY_MS = [6000, 11000];
const WINK_MS = 1600;
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

/** The look an actor without one wears: Dot its own, any other the shape and colour of its hue. */
export function fallbackLook(id) {
  if (id === DOT_ID) {
    return DOT_LOOK;
  }
  return hueLook(hueOf(id));
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
function hasLook(id, look) {
  return look !== null || looks.has(id);
}

/** Read every agent's look from the daemon, Dot's included, so the faces wear them. */
export async function loadLooks() {
  const agents = await api.get("/agents");
  for (const { record } of agents) {
    rememberLook(record.id, record.look);
  }
}

/** Whether the faces that can move do, kept in this browser; on until switched off. */
export function facesMove() {
  return readPref(MOVE_KEY) !== "off";
}

export function setFacesMove(value) {
  writePref(MOVE_KEY, value ? "on" : "off");
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

/** The faces a team wears (item 610): those of its first `limit` agents, `team.agents` (their ids), each in the look the
 * page remembers for it; a team with no agent left wears a face of its own, from its id, so that it is never bare. */
function memberFaces(team, state, limit) {
  const shown = team.agents.slice(0, limit);
  return shown.length === 0 ? [actorFace(team.id, state, null)] : shown.map((id) => actorFace(id, state, null));
}

/**
 * The faces of a team's agents laid over one another (item 610), `limit` of them and the rest counted ("+2"), as the
 * sidebar lays the faces of what a chat delegated: on the team's card, its page and its chats. `team` is `{ id, agents }`
 * (a record of the team, or the `team` of a chat). The faces are decoration: whoever puts the stack beside the team's
 * name tells a screen reader how many agents there are.
 */
export function teamFaces(team, state, limit) {
  const more = Math.max(team.agents.length - limit, 0);
  const stack = el(
    "span",
    { class: "face-stack", "aria-hidden": "true", dataset: { count: String(team.agents.length), state } },
    ...memberFaces(team, state, limit).map((face) => el("span", { class: "face-stack-item" }, face)),
    more > 0 ? el("span", { class: "face-stack-more" }, `+${more}`) : null,
  );
  handles.set(stack, { faces: [...stack.querySelectorAll(".avatar")] });
  return stack;
}

/**
 * The face of a chat with a team, where a row or a bar has room for one creature (item 610): the faces of its first two
 * agents, one up and left of the other, in the square a creature takes; one agent wears its face alone. It changes
 * state as a creature does (`setActorState`).
 */
export function teamDuo(team, state) {
  const faces = memberFaces(team, state, 2);
  const duo = el(
    "span",
    { class: "team-duo", "aria-hidden": "true", dataset: { count: String(faces.length), state } },
    ...faces.map((face) => el("span", { class: "team-duo-face" }, face)),
  );
  handles.set(duo, { faces });
  return duo;
}

/**
 * A moving face of an actor (the home): wearing `look` or the remembered one, in bloub's idle cycle with a wink now and
 * then while at rest, a still frame with the moving faces off. Returns `element`, `look(direction)`, `express(id)` and
 * `dispose()`.
 */
export function liveFace(id, state, look, size) {
  const chosen = lookOf(id, look);
  const fragment = document.createDocumentFragment();
  const handle = mountCharacter(fragment, {
    shape: chosen.shape,
    expression: expressionFor(chosen, state),
    colour: chosen.colour,
    size,
    moving: facesMove(),
    state: FACE_OF_STATE[state].animation,
    label: "",
  });
  const face = handle.element;
  face.classList.add("avatar", "live");
  face.dataset.state = state;
  handles.set(face, { handle, look: chosen });
  let timer = 0;
  function winkLater() {
    const [low, high] = WINK_EVERY_MS;
    timer = setTimeout(() => {
      if (face.dataset.state === "idle" && face.dataset.still === "false") {
        handle.setState("wink");
        timer = setTimeout(() => {
          if (face.dataset.state === "idle") {
            handle.setState("idle");
          }
          winkLater();
        }, WINK_MS);
      } else {
        winkLater();
      }
    }, low + Math.random() * (high - low));
  }
  winkLater();
  return {
    element: face,
    // Where the face looks when something outside drives it (the composer); null gives the head back to its pose.
    look: (direction) => handle.setLook(direction),
    // The expression to wear for a moment; null returns to the look's own for the state the face is in.
    express: (expression) => handle.setExpression(expression ?? expressionFor(chosen, face.dataset.state)),
    dispose: () => {
      clearTimeout(timer);
      handle.destroy();
    },
  };
}

/** Change the state a face shows: the expression and the animation follow. The faces of a team change together. */
export function setActorState(face, state) {
  face.dataset.state = state;
  const entry = handles.get(face);
  if (entry === undefined) {
    return;
  }
  if (entry.faces !== undefined) {
    for (const each of entry.faces) {
      setActorState(each, state);
    }
    return;
  }
  entry.handle.setExpression(expressionFor(entry.look, state));
  entry.handle.setState(FACE_OF_STATE[state].animation);
}
