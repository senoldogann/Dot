// The loops of a chat (item 281): right above the composer, as the commands at work are (background.js), a row for each
// schedule of the chat, made with "/loop 30m <message>" or by a run of it (item 291): its message, how often it asks
// again, when it asks next, and a Stop that deletes the schedule, every Stop in one column (item 591). The rows fold
// under a head that says how many loops there are and, folded, when the next one asks (item 591), the choice kept per
// browser. A run of the loop can end it itself once the work is done for good. The daemon tells of no change of a
// schedule, so the rows are read again when the chat's runs come and go, and as the page comes back.

import { api } from "./api.js";
import { clip, debounce, el, setText } from "./dom.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";
import { loopRhythm, nextFiring } from "./rhythm.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, confirmDialog } from "./ui.js";

const NAME_CHARS = 80;
const RUN_MOMENTS = new Set(["run_queued", "run_finished"]);
const READ_AGAIN_MS = 400;
// Whether the loops' rows are folded, kept as the folds of the pages are (panels.js): "open" or "closed".
const FOLD_KEY = "dot.fold.chat-loops";
// "/loop", a rhythm (30m, 2h) and the message that the chat asks again on it (item 281); "/loop" alone or with a word
// that is not a rhythm is said how to write it (LOOP_HOW).
const LOOP_PATTERN = /^\/loop\s+(\d+\s*[mh])\s+([\s\S]+)$/i;
export const LOOP_WORD = /^\/loop(?:\s|$)/i;
export const LOOP_HOW = "Write /loop, how often and the message: /loop 30m check the build";
/** "/loop" in the slash menu (composer.js `commands`) of the box of a run, and of a new chat's (item 400). */
export const LOOP_COMMAND = { label: "/loop", hint: "Ask again on a rhythm until it is done: /loop 30m <message>", insert: "/loop " };

/** The rhythm (`every`, as the daemon reads it) and the message of `text` written as "/loop 30m <message>", or null. */
export function loopOf(text) {
  const found = LOOP_PATTERN.exec(text);
  return found === null ? null : { every: found[1].replace(/\s+/g, ""), message: found[2].trim() };
}

/** How often a loop asks again, in words (rhythm.js: "every 30 minutes", "every day at 10:00"); a rhythm of another
 * form is said as the daemon says it. */
function rhythmWords(view) {
  return loopRhythm(view.record.cron ?? "") ?? view.when;
}

/**
 * The line of the loops of the chat `threadId`, which goes right above the composer. Returns `{ element, refresh(),
 * dispose() }`: `refresh()` reads the loops again (after "/loop" made one).
 */
export function chatLoops({ threadId }) {
  const list = el("ul", { class: "background-list", id: `chat-loops-${threadId}` });
  const count = el("span", { class: "chat-loops-count" });
  const soonest = el("span", { class: "chat-loops-next" });
  const toggle = el(
    "button",
    { class: "fold-toggle chat-loops-toggle", type: "button", "aria-controls": list.id, onclick: () => fold(!folded) },
    el("span", { class: "chev" }, icon("chevron")),
    el("span", { class: "chat-loops-title" }, "Loops"),
    count,
    soonest,
  );
  let folded = readPref(FOLD_KEY) === "closed";
  const problem = el("span", null);
  const unread = el(
    "div",
    { class: "background-unread", hidden: true },
    problem,
    el("button", { class: "btn sm ghost", type: "button", onclick: () => load() }, "Read again"),
  );
  const element = el(
    "section",
    { class: "chat-loops", role: "region", "aria-label": "Loops of this chat", "aria-live": "polite", hidden: true },
    toggle,
    unread,
    list,
  );

  /** Fold the rows away (`shut`) or open them, and keep the choice. */
  function fold(shut) {
    folded = shut;
    writePref(FOLD_KEY, shut ? "closed" : "open");
    paintFold();
  }

  function paintFold() {
    toggle.setAttribute("aria-expanded", String(!folded));
    list.hidden = folded;
    soonest.hidden = !folded || soonest.textContent === "";
    element.dataset.folded = String(folded);
  }

  function row(view) {
    const name = clip(view.record.name.replace(/^Loop: /, ""), NAME_CHARS);
    const at = view.record.next_run_at;
    const next = at === null ? "" : ` · next ${nextFiring(at, Date.now() / 1000)}`;
    const stop = el(
      "button",
      { class: "btn ghost sm", type: "button", "aria-label": `Stop the loop ${name}`, onclick: () => stopLoop(view, name) },
      "Stop",
    );
    // The message over how often it asks again on a phone, beside it in a wider window: the Stop stands in a column of
    // its own at the row's end either way, so that the Stops of the rows stand in one line.
    return el(
      "li",
      { class: "background-row loop-row", "data-schedule": view.record.id, title: view.record.name },
      el("span", { class: "status-dot", "aria-hidden": "true" }),
      el(
        "span",
        { class: "loop-text" },
        el("span", { class: "background-label" }, el("span", { class: "background-word" }, "Loop: "), el("span", { class: "background-command" }, name)),
        el("span", { class: "background-time" }, `${rhythmWords(view)}${next}`),
      ),
      stop,
    );
  }

  async function load() {
    try {
      const views = await api.get("/schedules");
      const loops = views.filter((view) => view.record.thread_id === threadId && view.record.enabled);
      unread.hidden = true;
      list.replaceChildren(...loops.map(row));
      setText(count, String(loops.length));
      const times = loops.map((view) => view.record.next_run_at).filter((at) => at !== null);
      setText(soonest, times.length === 0 ? "" : `next ${nextFiring(Math.min(...times), Date.now() / 1000)}`);
      paintFold();
      element.hidden = loops.length === 0;
    } catch (error) {
      problem.textContent = `The loops of this chat could not be read: ${error.message}`;
      unread.hidden = false;
      element.hidden = false;
    }
  }

  async function stopLoop(view, name) {
    const confirmed = await confirmDialog({
      title: "Stop this loop?",
      message: `Its schedule is deleted, and the chat is not asked again: ${name}`,
      confirmLabel: "Stop",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await attempt(async () => {
      await api.del(`/schedules/${encodeURIComponent(view.record.id)}`);
      await load();
    });
  }

  const readAgain = debounce(() => load(), READ_AGAIN_MS);
  const stopLifecycle = onLifecycle((event) => {
    if (RUN_MOMENTS.has(event.kind)) {
      readAgain();
    }
  });
  const stopBack = onStreamBack(load);

  paintFold();
  load();
  return {
    element,
    refresh: load,
    dispose: () => {
      stopLifecycle();
      stopBack();
    },
  };
}
