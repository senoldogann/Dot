// How Dot gets your attention when you are not looking at the page: the tab title counts the approvals that wait, and
// desktop notifications (off until you switch them on) say when Dot needs an answer or a run has ended.

import { api } from "./api.js";
import { readPref, writePref } from "./prefs.js";
import { getSnapshot, onChange, onLifecycle } from "./state.js";

const NOTIFY_KEY = "dot.notify";
const BODY_CHARS = 140;
const FINISHED_TITLE = { done: "A run is done", failed: "A run failed", cancelled: "A run was stopped" };
const ASKING_TITLE = { question: "Dot has a question", handoff: "Dot needs your help" };

let pageTitle = "Dot";

function paintTitle() {
  const waiting = getSnapshot().approvals.length;
  document.title = waiting === 0 ? pageTitle : `(${waiting}) ${pageTitle}`;
}

/** Set the title of the page; the number of approvals that wait comes in front of it. */
export function setPageTitle(text) {
  pageTitle = text;
  paintTitle();
}

export function notificationsAvailable() {
  return typeof Notification === "function";
}

export function notificationsBlocked() {
  return notificationsAvailable() && Notification.permission === "denied";
}

/** Whether notifications are switched on here and the browser lets the page show them. */
export function notificationsOn() {
  return notificationsAvailable() && Notification.permission === "granted" && readPref(NOTIFY_KEY) === "on";
}

/** Switch notifications on (the browser asks for its permission the first time) or off; resolves to whether they are on. */
export async function setNotifications(on) {
  if (!on) {
    writePref(NOTIFY_KEY, "off");
    return false;
  }
  const permission = await Notification.requestPermission();
  writePref(NOTIFY_KEY, permission === "granted" ? "on" : "off");
  return permission === "granted";
}

function shorten(text) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > BODY_CHARS ? `${flat.slice(0, BODY_CHARS - 1)}…` : flat;
}

/** Whether nobody is looking at the page: its tab is in the background or its window is not the active one. */
function awayFromPage() {
  return document.visibilityState === "hidden" || !document.hasFocus();
}

function worthATell() {
  return notificationsOn() && awayFromPage();
}

function tell({ title, body, tag, route }) {
  const note = new Notification(title, { body, tag });
  note.addEventListener("click", () => {
    window.focus();
    location.hash = route;
    note.close();
  });
}

function tellAboutApproval(event) {
  const { approval_id: id, severity, summary, args } = event.data;
  const text = severity === "question" && typeof args.question === "string" ? args.question : summary;
  tell({ title: ASKING_TITLE[severity] ?? "Dot asks for approval", body: shorten(text), tag: `approval-${id}`, route: `#/runs/${event.run_id}` });
}

async function tellAboutEnd(event) {
  const { run } = await api.get(`/runs/${event.run_id}`);
  if (run.parent_run_id !== null || !worthATell()) {
    return;
  }
  tell({ title: FINISHED_TITLE[event.data.status] ?? "A run has ended", body: shorten(run.goal), tag: `run-${run.id}`, route: `#/runs/${run.id}` });
}

/** What a failure to notify costs: nothing but a line in the console, so it never stops the page from updating. */
function onLifecycleEvent(event) {
  try {
    if (!worthATell()) {
      return;
    }
    if (event.kind === "approval_requested") {
      tellAboutApproval(event);
    } else if (event.kind === "run_finished") {
      tellAboutEnd(event).catch((error) => console.warn("could not tell about a finished run", error));
    }
  } catch (error) {
    console.warn("could not notify", error);
  }
}

/** Keep the tab title in step with the approvals that wait and send the notifications; returns the function that stops it. */
export function watchAttention() {
  paintTitle();
  const stopTitle = onChange(paintTitle);
  const stopEvents = onLifecycle(onLifecycleEvent);
  return () => {
    stopTitle();
    stopEvents();
  };
}
