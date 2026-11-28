// The words of a chat's tasks (item 2013): how a task stands, how many are open, and what the toast says of where a
// message went. Pure, so that it runs under Node without a page (tests/js/tasks.test.js); tasks.js draws them.

import { t } from "./i18n.js";

/**
 * A task of the chat, as the daemon lists it (`ChatTaskView`).
 * @typedef {object} ChatTask
 * @property {string} thread_id
 * @property {string} title
 * @property {string} run_id
 * @property {string} status
 * @property {"working" | "waits" | "ended"} state
 * @property {boolean} own
 */

/**
 * The tasks of a chat (`TasksView`).
 * @typedef {object} ChatTasks
 * @property {string} chat_id
 * @property {string} chat_title
 * @property {number} cap
 * @property {ChatTask[]} tasks
 */

/**
 * What a task's row says of how it stands: working, waiting for you, or how its run ended.
 * @param {ChatTask} task
 * @returns {string}
 */
export function taskStateWords(task) {
  if (task.state === "working") {
    return t("tasks.working");
  }
  if (task.state === "waits") {
    return t("tasks.waits");
  }
  if (task.status === "cancelled") {
    return t("tasks.stopped");
  }
  return task.status === "failed" ? t("tasks.failed") : t("tasks.done");
}

/**
 * How many tasks of the chat are open: at work, or waiting for you. The capsule and the line count these.
 * @param {ChatTask[]} tasks
 */
export function openTasks(tasks) {
  return tasks.filter((task) => task.state !== "ended").length;
}

/**
 * The words of the toast that says where a message sent to a chat with several tasks went (`QueueView.routed`), and
 * whether it offers the way to that task and **Move**; null when nothing need be said (it went where it was sent).
 * @param {{ kind: string, thread_id: string, run_id: string | null, message_id: string | null }} routed
 * @param {string} here the thread of the page
 * @param {string} chatId the chat of the page
 * @param {string} title the task's title
 * @returns {{ text: string, open: boolean, move: boolean } | null}
 */
export function routedWords(routed, here, chatId, title) {
  if (routed.kind === "stopped") {
    return { text: routed.thread_id === chatId && routed.run_id === null ? t("tasks.stoppedAll") : t("tasks.stoppedOne", { task: title }), open: false, move: false };
  }
  if (routed.kind === "new") {
    return { text: t("tasks.started", { task: title }), open: true, move: routed.run_id !== null };
  }
  if (routed.thread_id === here) {
    return null;
  }
  // A steer went into a run already at work on its own task: Move would stop that run, so it is not offered.
  const moves = routed.kind !== "steered" && (routed.message_id !== null || routed.run_id !== null);
  return { text: t("tasks.sent", { task: title }), open: true, move: moves };
}
