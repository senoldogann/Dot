// How Dot gets your attention when you are not looking at the page: the tab title counts the approvals that wait, and
// desktop notifications (off until you switch them on) say when Dot needs an answer or a run has ended.

import { api } from "./api.js";
import { faceDataUrl, overallState } from "./face.js";
import { readPref, writePref } from "./prefs.js";
import { getSnapshot, onChange, onLifecycle } from "./state.js";

const NOTIFY_KEY = "dot.notify";
// Whether this browser is subscribed to the daemon's web push: then the pushes tell, and the page itself stays quiet.
const PUSH_KEY = "dot.push";
const BODY_CHARS = 140;
const FINISHED_TITLE = { done: "A run is done", failed: "A run failed", cancelled: "A run was stopped" };
const ASKING_TITLE = { question: "Dot has a question", handoff: "Dot needs your help" };

const FINISHED_FACE = { done: "done", failed: "failed" };

let pageTitle = "Dot";

/** The tab's title counts the approvals that wait; its icon is the face in the state of the whole of Dot. */
function paintTitle() {
  const snapshot = getSnapshot();
  const waiting = snapshot.approvals.length;
  document.title = waiting === 0 ? pageTitle : `(${waiting}) ${pageTitle}`;
  const link = document.querySelector('link[rel="icon"]');
  if (link !== null) {
    link.href = faceDataUrl(overallState(snapshot));
  }
}

/** Set the title of the page; the number of approvals that wait comes in front of it. */
export function setPageTitle(text) {
  pageTitle = text;
  paintTitle();
}

/** The desktop shell's notification bridge (Tauri), when the page runs inside the shell; null in a browser. */
function shell() {
  return typeof window.__TAURI__ === "object" && window.__TAURI__ !== null && "notification" in window.__TAURI__ ? window.__TAURI__.notification : null;
}

export function notificationsAvailable() {
  return shell() !== null || typeof Notification === "function";
}

export function notificationsBlocked() {
  return shell() === null && notificationsAvailable() && Notification.permission === "denied";
}

/** Whether notifications are switched on here and the browser (or the shell) lets the page show them. */
export function notificationsOn() {
  return notificationsAvailable() && (shell() !== null || Notification.permission === "granted") && readPref(NOTIFY_KEY) === "on";
}

/** Whether this browser can take the daemon's web push: a service worker and a push manager, outside the shell. */
function pushAvailable() {
  return shell() === null && "serviceWorker" in navigator && "PushManager" in window;
}

function keyBytes(base64url) {
  const padded = `${base64url}${"=".repeat((4 - (base64url.length % 4)) % 4)}`.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

/** Subscribe this browser to the daemon's pushes, or drop the subscription; `on` says which. */
async function setPush(on) {
  const registration = await navigator.serviceWorker.ready;
  const current = await registration.pushManager.getSubscription();
  if (!on) {
    if (current !== null) {
      await api.del(`/push/subscriptions?endpoint=${encodeURIComponent(current.endpoint)}`);
      await current.unsubscribe();
    }
    writePref(PUSH_KEY, "off");
    return;
  }
  const { public_key: publicKey } = await api.get("/push/key");
  const subscription = current ?? (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }));
  await api.post("/push/subscriptions", subscription.toJSON());
  writePref(PUSH_KEY, "on");
}

/** Switch notifications on (the browser or the shell asks for its permission the first time) or off; resolves to whether they are on.
 * A browser that can take web push is subscribed as well, so the phone hears from the daemon while the page is closed. */
export async function setNotifications(on) {
  if (!on) {
    writePref(NOTIFY_KEY, "off");
    if (pushAvailable()) {
      await setPush(false);
    }
    return false;
  }
  const bridge = shell();
  const permission = bridge === null ? await Notification.requestPermission() : (await bridge.isPermissionGranted()) ? "granted" : await bridge.requestPermission();
  writePref(NOTIFY_KEY, permission === "granted" ? "on" : "off");
  if (permission === "granted" && pushAvailable()) {
    await setPush(true);
  }
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
  return notificationsOn() && awayFromPage() && readPref(PUSH_KEY) !== "on";
}

/** In the desktop shell the notification is the system's; a click brings Dot to the front, where Waiting for you leads on. */
function tell({ title, body, tag, route, state }) {
  const bridge = shell();
  if (bridge !== null) {
    bridge.sendNotification({ title, body });
    return;
  }
  const note = new Notification(title, { body, tag, icon: faceDataUrl(state) });
  note.addEventListener("click", () => {
    window.focus();
    location.hash = route;
    note.close();
  });
}

function tellAboutApproval(event) {
  const { approval_id: id, severity, summary, args } = event.data;
  const text = severity === "question" && typeof args.question === "string" ? args.question : summary;
  tell({ title: ASKING_TITLE[severity] ?? "Dot asks for approval", body: shorten(text), tag: `approval-${id}`, route: `#/runs/${event.run_id}`, state: "waiting" });
}

async function tellAboutEnd(event) {
  const { run } = await api.get(`/runs/${event.run_id}`);
  if (run.parent_run_id !== null || !worthATell()) {
    return;
  }
  tell({
    title: FINISHED_TITLE[event.data.status] ?? "A run has ended",
    body: shorten(run.goal),
    tag: `run-${run.id}`,
    route: `#/runs/${run.id}`,
    state: FINISHED_FACE[event.data.status] ?? "idle",
  });
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
