// The character beside the chat (item 103): the run's agent as a moving character at the head of the side panel,
// working, waiting, finishing and sleeping with the run (mood.js), and glancing aside toward the live view when the
// run reads a page. Still when faces do not move (actors.js facesMove) or under reduced motion.
import { facesMove, lookOf } from "./actors.js";
import { mountCharacter } from "./character/character.js";
import { STATE_BY_ID } from "./character/states.js";
import { el } from "./dom.js";
import { entranceOf, restingMood } from "./mood.js";

const ASIDE = { yaw: 38, pitch: -16, mix: 1, spin: 0, wander: 0.3 };
const ASIDE_MS = 2500;

/** `createCharacterCard({ id, look, status, endedAt, size })` returns `{ element, onStatus(status, endedAt), lookAside(),
 * dispose }`; `onStatus` with the status unchanged does nothing. */
export function createCharacterCard({ id, look, status, endedAt, size }) {
  const chosen = lookOf(id, look);
  const stage = el("div", { class: "character-stage" });
  const element = el("section", { class: "card character-card", "aria-label": "The face of the run" }, stage);
  const handle = mountCharacter(stage, { ...chosen, size, moving: facesMove(), label: "The face of the run" });
  let current = null;
  let timer = 0;
  let asideTimer = 0;

  function endedFor(at) {
    return at === null ? 0 : Math.max(0, Date.now() / 1000 - at);
  }

  function rest(status, at) {
    const mood = restingMood(status, endedFor(at));
    handle.setExpression(mood.expression ?? chosen.expression);
    handle.setState(mood.animation);
    element.dataset.mood = mood.animation;
  }

  function onStatus(status, at) {
    if (status === current) {
      return;
    }
    const entrance = entranceOf(current, status);
    current = status;
    clearTimeout(timer);
    if (entrance === null) {
      rest(status, at);
      return;
    }
    handle.setExpression(chosen.expression);
    handle.setState(entrance);
    element.dataset.mood = entrance;
    timer = setTimeout(() => rest(status, at), STATE_BY_ID.get(entrance).duration * 1000);
  }

  function lookAside() {
    handle.setLook(ASIDE);
    clearTimeout(asideTimer);
    asideTimer = setTimeout(() => handle.setLook(null), ASIDE_MS);
  }

  onStatus(status, endedAt);
  return {
    element,
    onStatus,
    lookAside,
    dispose: () => {
      clearTimeout(timer);
      clearTimeout(asideTimer);
      handle.destroy();
    },
  };
}
