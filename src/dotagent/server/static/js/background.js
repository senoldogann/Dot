// The commands at work in a chat (item 229): right above the composer, a row
// for each terminal session of the chat whose program runs, with its command, how long it has run and a Stop. The dot
// of a command in the background pulses: when it ends, its chat is told, and that starts Dot's next run there. The
// daemon keeps the sessions, so every open page shows them as they open and end; a list the page could not read says
// so, with Read again.

import { api } from "./api.js";
import { clip, duration, el, setText } from "./dom.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, confirmDialog } from "./ui.js";

const TERMINALS_CHANGED = "terminals_changed";
const COMMAND_CHARS = 120;
const TICK_MS = 1000;

/** How long the program of `session` has run at `now` (seconds). */
function took(session, now) {
  return duration(Math.max(now - session.started_at, 0));
}

/**
 * The line of the commands at work in the chat `threadId`, which goes right above the composer. Returns `{ element,
 * dispose() }`.
 */
export function backgroundWork({ threadId }) {
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
    { class: "background-work", role: "region", "aria-label": "Commands at work", "aria-live": "polite", hidden: true },
    unread,
    list,
  );
  // The time of each row, to count on.
  let shown = [];
  let ticker = null;

  function row(session, now) {
    const command = clip(session.command, COMMAND_CHARS);
    const time = el("span", { class: "background-time" }, took(session, now));
    const stop = el(
      "button",
      { class: "btn ghost sm", type: "button", "aria-label": `Stop ${command}`, onclick: () => stopSession(session) },
      "Stop",
    );
    shown.push({ session, time });
    return el(
      "li",
      { class: "background-row", "data-wakes": String(session.background), title: session.command },
      el("span", { class: "status-dot", "aria-hidden": "true" }),
      el(
        "span",
        { class: "background-label" },
        el("span", { class: "background-word" }, "Running: "),
        el("span", { class: "background-command" }, command),
        session.background ? el("span", { class: "visually-hidden" }, ". Dot is told when it ends") : null,
      ),
      time,
      stop,
    );
  }

  function tick() {
    const now = Date.now() / 1000;
    for (const { session, time } of shown) {
      setText(time, took(session, now));
    }
  }

  function paint(sessions) {
    const now = Date.now() / 1000;
    shown = [];
    list.replaceChildren(...sessions.map((session) => row(session, now)));
    element.hidden = sessions.length === 0 && unread.hidden;
    if (shown.length > 0 && ticker === null) {
      ticker = setInterval(tick, TICK_MS);
    } else if (shown.length === 0 && ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  async function load() {
    try {
      const sessions = await api.get(`/threads/${encodeURIComponent(threadId)}/terminals`);
      unread.hidden = true;
      paint(sessions.filter((session) => !session.ended));
    } catch (error) {
      problem.textContent = `The commands at work could not be read: ${error.message}`;
      unread.hidden = false;
      element.hidden = false;
    }
  }

  async function stopSession(session) {
    const confirmed = await confirmDialog({
      title: "Stop this command?",
      message: `It ends with everything it started, and Dot is not told: ${clip(session.command, COMMAND_CHARS)}`,
      confirmLabel: "Stop",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await attempt(async () => {
      await api.del(`/threads/${encodeURIComponent(threadId)}/terminals/${encodeURIComponent(session.id)}`);
      await load();
    });
  }

  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === TERMINALS_CHANGED && event.data.thread_id === threadId) {
      load();
    }
  });
  const stopBack = onStreamBack(load);

  load();
  return {
    element,
    dispose: () => {
      stopLifecycle();
      stopBack();
      if (ticker !== null) {
        clearInterval(ticker);
        ticker = null;
      }
    },
  };
}
