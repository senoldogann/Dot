// The loops of a chat (item 281): right above the composer, as the commands at work are (background.js), a row for each
// schedule of the chat, made with "/loop 30m <message>" or by a run of it (item 291): its message, how often it asks
// again, when it asks next, and a Stop that deletes the schedule. A run of the loop can end it itself once the work is
// done for good. The daemon tells of no change of a schedule, so the rows are read again when the chat's runs come and
// go, and as the page comes back.

import { api } from "./api.js";
import { clip, debounce, el } from "./dom.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, confirmDialog } from "./ui.js";

const NAME_CHARS = 80;
const RUN_MOMENTS = new Set(["run_queued", "run_finished"]);
const READ_AGAIN_MS = 400;
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

/** How often a loop asks again, in words, for the rhythms "/loop" makes (scheduler/loops.py); another schedule of the
 * chat (one a run made) is said as the daemon says it. */
function rhythmWords(view) {
  const cron = view.record.cron ?? "";
  const minutes = /^\*\/(\d+) \* \* \* \*$/.exec(cron);
  if (minutes !== null) {
    return `every ${minutes[1]} minutes`;
  }
  if (cron === "0 * * * *") {
    return "every hour";
  }
  const hours = /^0 \*\/(\d+) \* \* \*$/.exec(cron);
  return hours !== null ? `every ${hours[1]} hours` : view.when;
}

/**
 * The line of the loops of the chat `threadId`, which goes right above the composer. Returns `{ element, refresh(),
 * dispose() }`: `refresh()` reads the loops again (after "/loop" made one).
 */
export function chatLoops({ threadId }) {
  const list = el("ul", { class: "background-list" });
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
    unread,
    list,
  );

  function row(view) {
    const name = clip(view.record.name.replace(/^Loop: /, ""), NAME_CHARS);
    const next = view.next_local === null ? "" : ` · next ${view.next_local}`;
    const stop = el(
      "button",
      { class: "btn ghost sm", type: "button", "aria-label": `Stop the loop ${name}`, onclick: () => stopLoop(view, name) },
      "Stop",
    );
    return el(
      "li",
      { class: "background-row", "data-schedule": view.record.id, title: view.record.name },
      el("span", { class: "status-dot", "aria-hidden": "true" }),
      el("span", { class: "background-label" }, el("span", { class: "background-word" }, "Loop: "), el("span", { class: "background-command" }, name)),
      el("span", { class: "background-time" }, `${rhythmWords(view)}${next}`),
      stop,
    );
  }

  async function load() {
    try {
      const views = await api.get("/schedules");
      const loops = views.filter((view) => view.record.thread_id === threadId && view.record.enabled);
      unread.hidden = true;
      list.replaceChildren(...loops.map(row));
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
