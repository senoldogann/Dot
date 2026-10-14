// The card of a delegated run (item 226), after T3 Code's: resting the pointer on a helper's row in the conversation,
// or moving the keyboard's focus to it, shows what the row has no room for: the helper's whole goal, its model and
// reasoning level, how it stands and for how long, and what it is doing or did last. One card serves the rows of a
// conversation, beside the row it tells of; it follows the helper as it works, at the second the row's time counts on,
// stays by its row as the conversation scrolls, and goes with the pointer, the focus, a click on the row or Escape. It
// needs a pointer that rests: on a touch screen a tap opens the row, as before.

import { clip, duration, el, modelName } from "./dom.js";
import { levelLabel } from "./efforts.js";
import { providerMark, providerOf } from "./providers.js";

const FINE_POINTER = window.matchMedia("(hover: hover) and (pointer: fine)");
// The pointer rests this long on a row before its card shows, so that a pass over the rows shows none.
const REST_MS = 400;
// The card stands this far from its row, and at least this far from the window's edges.
const GAP_PX = 6;
const EDGE_PX = 8;
const GOAL_CHARS = 400;
// A helper's state, in a word.
const STATE_WORDS = {
  queued: "Queued",
  running: "Running",
  blocked: "Waiting for you",
  done: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};

let cards = 0;

/** A helper's state in a word, for its card and the side panel's list of agents (item 227). */
export function stateWord(status) {
  return STATE_WORDS[status] ?? status;
}

/** The model of `helper` in words: the model's name, then its reasoning level and fast mode when it has them. */
function modelLine(helper) {
  const parts = [modelName(helper.model)];
  if (helper.effort !== null) {
    parts.push(levelLabel(helper.effort));
  }
  if (helper.speed === "fast") {
    parts.push("Fast");
  }
  return parts.join(" · ");
}

/** What `helper` is doing, as a label and the words: the call that is out, else the model's turn; once it ended, its
 * last call. `null` when there is nothing to say. */
function actionLine(helper) {
  if (helper.ended !== null) {
    return helper.action === null ? null : ["Last", helper.action];
  }
  if (helper.calls > 0 && helper.action !== null) {
    return ["Now", helper.action];
  }
  return helper.action === null ? ["Now", "Starting"] : ["Now", "Thinking"];
}

/**
 * The card of the helpers of a conversation, put in `container` (it stands on the window, over the conversation).
 * `refresh(now)` paints it again as the helper stands at `now` (seconds), when it shows: the conversation calls it
 * when its rows' times count on and when an event comes.
 * `attach(row, helper)` makes `row`, a helper's summary, show it; `helper` is the object the conversation keeps up to
 * date as the helper works: `{ goal, model, effort, speed, status, began, ended, action, calls }` (`model` a
 * `provider:model` reference, `effort` null when the run has no level, `began` and `ended` in seconds, `ended` null
 * while it works, `action` its last call in words, null before any, and `calls` the calls that are out).
 */
export function helperCard(container) {
  cards += 1;
  const face = el("span", { class: "helper-card-face" });
  const goal = el("span", { class: "helper-card-goal" });
  const model = el("div", { class: "helper-card-row helper-card-meta" });
  const ring = el("span", { class: "status-ring", "aria-hidden": "true" });
  const state = el("span", null);
  const label = el("span", { class: "helper-card-label" });
  const action = el("span", { class: "helper-card-action" });
  const doing = el("div", { class: "helper-card-row" }, label, action);
  const card = el(
    "div",
    { class: "helper-card", role: "tooltip", id: `helper-card-${cards}`, hidden: true },
    el("div", { class: "helper-card-head" }, face, goal),
    model,
    el("div", { class: "helper-card-row helper-card-meta" }, ring, state),
    doing,
  );
  container.append(card);

  let shown = null;
  let resting = null;
  let listening = false;
  // The state the card's face was drawn in: a face drawn again starts its motion again.
  let faceDrawn = null;

  /** Put the card under the row, or over it where the window has no room below, inside the window. */
  function place() {
    const rect = shown.row.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      hide();
      return;
    }
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    const left = Math.max(EDGE_PX, Math.min(rect.left, window.innerWidth - width - EDGE_PX));
    const below = rect.bottom + GAP_PX;
    const top = below + height <= window.innerHeight - EDGE_PX ? below : Math.max(EDGE_PX, rect.top - GAP_PX - height);
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  function paint(now) {
    const helper = shown.helper;
    if (faceDrawn !== helper.status) {
      face.replaceChildren(shown.face());
      faceDrawn = helper.status;
    }
    goal.textContent = clip(helper.goal, GOAL_CHARS);
    // A helper whose first event the page did not read has no model to name.
    model.hidden = helper.model === null;
    if (helper.model !== null) {
      model.replaceChildren(providerMark(providerOf(helper.model)), el("span", null, modelLine(helper)));
    }
    ring.className = `status-ring s-${helper.status}`;
    const until = helper.ended ?? now;
    state.textContent = `${stateWord(helper.status)} · ${duration(Math.max(until - helper.began, 0))}`;
    const line = actionLine(helper);
    doing.hidden = line === null;
    if (line !== null) {
      label.textContent = line[0];
      action.textContent = line[1];
    }
  }

  // Escape that takes the card away is kept by it: the side panel, which answers Escape after the page, keeps its file.
  function onKey(event) {
    if (event.key === "Escape" && !event.defaultPrevented) {
      event.preventDefault();
      hide();
    }
  }

  function onScroll() {
    place();
  }

  function show(row, helper, faceOf) {
    clearTimeout(resting);
    resting = null;
    if (shown !== null && shown.row !== row) {
      hide();
    }
    shown = { row, helper, face: faceOf };
    faceDrawn = null;
    paint(Date.now() / 1000);
    card.hidden = false;
    place();
    row.setAttribute("aria-describedby", card.id);
    if (!listening) {
      listening = true;
      document.addEventListener("keydown", onKey);
      window.addEventListener("scroll", onScroll, { capture: true, passive: true });
      window.addEventListener("resize", onScroll, { passive: true });
    }
  }

  function hide() {
    clearTimeout(resting);
    resting = null;
    if (shown === null) {
      return;
    }
    shown.row.removeAttribute("aria-describedby");
    shown = null;
    card.hidden = true;
    listening = false;
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("scroll", onScroll, { capture: true });
    window.removeEventListener("resize", onScroll);
  }

  /** Make `row` show the card of `helper`; `faceOf()` gives the helper's face as it stands, for the card. */
  function attach(row, helper, faceOf) {
    row.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "touch" || !FINE_POINTER.matches) {
        return;
      }
      clearTimeout(resting);
      resting = setTimeout(() => show(row, helper, faceOf), REST_MS);
    });
    row.addEventListener("pointerleave", () => {
      if (shown === null || shown.row === row) {
        hide();
      }
    });
    row.addEventListener("focus", () => {
      if (row.matches(":focus-visible")) {
        show(row, helper, faceOf);
      }
    });
    row.addEventListener("blur", () => {
      if (shown !== null && shown.row === row) {
        hide();
      }
    });
    // A click opens or closes the row: what it shows takes the card's place.
    row.addEventListener("click", hide);
  }

  function refresh(now) {
    if (shown !== null) {
      paint(now);
      place();
    }
  }

  return { attach, hide, refresh };
}
