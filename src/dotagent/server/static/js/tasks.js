// The tasks of a chat with several (item 2013, slice 3 of item 1982's design): right above the composer, as the chat's
// loops are (loops.js), a row for each task of the chat (the chat's own, and each lane a request of its own started
// beside it): its title, whether it works, waits for you or ended, a Stop for one that works and the way to it. On a
// phone the rows stand in a sheet that the capsule's "N tasks" opens (dockbar.js). The daemon's tasks of the chat
// (`GET /threads/{id}/tasks`) are read again when a run of any chat starts or ends, and as the page comes back; a chat
// with no lane has no rows, and the line is not drawn. The same words are the phone app's (DotNext).

import { ApiError, api, unreachable } from "./api.js";
import { clip, debounce, el, setText } from "./dom.js";
import { goTo } from "./hop.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { openTasks, taskStateWords } from "./taskwords.js";
import { attempt, toastError } from "./ui.js";

const TITLE_CHARS = 80;
const RUN_MOMENTS = new Set(["run_queued", "run_finished"]);
const READ_AGAIN_MS = 400;
// The dot of a task's row, as the sidebar's dots of runs: at work, waiting for you, or how it ended.
/** @type {Record<string, string>} */
const DOTS = { working: "s-running", waits: "s-blocked", done: "s-done", failed: "s-failed", cancelled: "s-stopped" };

/**
 * The tasks of the chat of `threadId` (the chat itself, or one of its lanes). Returns `{ element, refresh(), present(on),
 * info(), titleOf(threadId), dispose() }`: `info()` is what the last read found (null before one), `titleOf` a task's
 * title by its thread. `onChange(info)` hears of every read.
 * @param {{ threadId: string, onChange: (info: import("./taskwords.js").ChatTasks | null) => void }} options
 */
export function chatTasks({ threadId, onChange }) {
  const list = el("ul", { class: "background-list", id: `chat-tasks-${threadId}` });
  const count = el("span", { class: "chat-loops-count" });
  const head = el(
    "div",
    { class: "chat-tasks-head" },
    el("span", { class: "chat-loops-mark", "aria-hidden": "true" }, icon("layers")),
    el("span", { class: "chat-loops-title" }, t("tasks.title")),
    count,
  );
  // A region, not a live one: it is read again with every run that starts or ends.
  const element = el("section", { class: "chat-tasks", role: "region", "aria-label": t("tasks.title"), hidden: true }, head, list);
  /** @type {import("./taskwords.js").ChatTasks | null} */
  let found = null;
  // A failure to read is told once until a read works again, and nothing is told once the chat is left.
  let told = false;
  let left = false;

  /** @param {import("./taskwords.js").ChatTask} task */
  function row(task) {
    const title = task.own ? t("tasks.thisChat") : clip(task.title, TITLE_CHARS);
    const here = task.thread_id === threadId;
    const stop =
      task.state === "working"
        ? el(
            "button",
            { class: "btn ghost sm", type: "button", "aria-label": t("tasks.stopLabel", { task: title }), onclick: () => stopTask(task) },
            t("tasks.stop"),
          )
        : null;
    const open = here
      ? null
      : el(
          "button",
          { class: "btn ghost sm", type: "button", "aria-label": t("tasks.openLabel", { task: title }), onclick: () => goTo(`#/runs/${task.run_id}`) },
          t("tasks.open"),
        );
    return el(
      "li",
      { class: "background-row loop-row task-row", "data-thread": task.thread_id, "data-state": task.state, "aria-current": here ? "true" : null },
      el("span", { class: `status-dot ${DOTS[task.state === "ended" ? task.status : task.state] ?? "s-done"}`, "aria-hidden": "true" }),
      el(
        "span",
        { class: "loop-text" },
        el("span", { class: "background-label task-title" }, title),
        el("span", { class: "background-time" }, taskStateWords(task)),
      ),
      // Two places always, so that a Stop stands under a Stop and an Open under an Open, whatever a row lacks.
      el("span", { class: "task-actions" }, stop ?? gap(t("tasks.stop")), open ?? gap(t("tasks.open"))),
    );
  }

  /** The room of a button a row does not have, as wide as its words, unseen and out of reach. */
  function gap(/** @type {string} */ words) {
    return el("span", { class: "btn ghost sm task-gap", "aria-hidden": "true" }, words);
  }

  /** @param {import("./taskwords.js").ChatTask} task */
  async function stopTask(task) {
    await attempt(async () => {
      await api.post(`/runs/${task.run_id}/cancel`, null);
      await load();
    });
  }

  async function load() {
    try {
      /** @type {import("./taskwords.js").ChatTasks} */
      const view = await api.get(`/threads/${threadId}/tasks`);
      if (left) {
        return;
      }
      found = view;
      told = false;
      list.replaceChildren(...view.tasks.map(row));
      setText(count, String(openTasks(view.tasks)));
      element.hidden = view.tasks.length === 0;
      onChange(view);
    } catch (error) {
      // A chat deleted since has no tasks; a link that is down keeps the rows, which the stream's return reads again.
      // Anything else is told, and the rows stay as they were read last.
      if (error instanceof ApiError && error.status === 404) {
        found = null;
        list.replaceChildren();
        element.hidden = true;
      } else if (!unreachable(error) && !told && !left) {
        told = true;
        toastError(error);
      }
      if (!left) {
        onChange(found);
      }
    }
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
    present: (/** @type {boolean} */ on) => {
      head.hidden = on;
    },
    info: () => found,
    titleOf: (/** @type {string} */ thread) => {
      const task = found?.tasks.find((each) => each.thread_id === thread);
      if (task === undefined) {
        return t("tasks.aTask");
      }
      return task.own ? t("tasks.thisChat") : clip(task.title, TITLE_CHARS);
    },
    dispose: () => {
      left = true;
      stopLifecycle();
      stopBack();
    },
  };
}
