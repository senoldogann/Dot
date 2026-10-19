// How Dot gets your attention when you are not looking: the tab title counts the approvals that wait, and desktop
// notifications (off until you switch them on) say when Dot needs an answer or left you a question, a run has ended, a
// campaign ended by itself or a schedule missed a firing, unless you are looking at that run (its page open, the tab
// visible, the window active; item 75), or you asked to be told even then. They say what the daemon's pushes say
// (notify/push.py), word for word.

import { api } from "./api.js";
import { faceDataUrl, overallState } from "./face.js";
import { readPref, writePref } from "./prefs.js";
import { getSnapshot, onChange, onLifecycle } from "./state.js";

const NOTIFY_KEY = "dot.notify";
// Whether to tell about a run even while its page is in view (item 75); off until switched on.
const ALWAYS_KEY = "dot.notify.always";
// Whether this browser is subscribed to the daemon's web push: then the pushes tell, and the page itself stays quiet.
const PUSH_KEY = "dot.push";
const BODY_CHARS = 140;
const FINISHED_TITLE = { done: "A run is done", failed: "A run failed", cancelled: "A run was stopped" };
const ASKING_TITLE = { question: "Dot has a question", handoff: "Dot needs your help" };
const DRAFT_TITLE = { mail: "Dot drafted an email", message: "Dot drafted a message" };

const FINISHED_FACE = { done: "done", failed: "failed" };
// The line breaks of Python's str.splitlines, so that the first line of a result is the one the push shows.
// biome-ignore lint/suspicious/noControlCharactersInRegex: the control characters are the line breaks str.splitlines knows
const LINE_BREAK = /\r\n|[\n\v\f\r\x1c-\x1e\x85\u2028\u2029]/;

let pageTitle = "Dot";

/** The tab's title counts the approvals that wait; its icon is the face in the state of the whole of Dot. */
function paintTitle() {
  const snapshot = getSnapshot();
  const waiting = snapshot.approvals.length;
  document.title = waiting === 0 ? pageTitle : `(${waiting}) ${pageTitle}`;
  const link = document.querySelector('link[rel="icon"]');
  if (link instanceof HTMLLinkElement) {
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

/** Whether to tell about a run even while you are looking at it. */
export function tellEvenWhenLooking() {
  return readPref(ALWAYS_KEY) === "on";
}

export function setTellEvenWhenLooking(on) {
  writePref(ALWAYS_KEY, on ? "on" : "off");
  return tellEvenWhenLooking();
}

/** Whether you are looking at the run `runId`: its own page is open (`hash`), the tab is visible and the window is the
 * active one. Pure, so the rule can be read alone; `lookingAt` reads the page. */
function isLooking(runId, { hash, visibility, focused }) {
  return visibility === "visible" && focused && hash === `#/runs/${runId}`;
}

function lookingAt(runId) {
  return isLooking(runId, { hash: location.hash, visibility: document.visibilityState, focused: document.hasFocus() });
}

/** Whether a notification about `runId` is worth sending: notifications are on, the daemon does not push to this
 * browser, and you are not looking at the run (or asked to be told even then). The tab title counts either way. */
function worthATell(runId) {
  return notificationsOn() && readPref(PUSH_KEY) !== "on" && (tellEvenWhenLooking() || !lookingAt(runId));
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

/** The first line of `text` that is not blank; nothing when there is none. */
function firstLine(text) {
  return text.split(LINE_BREAK).find((line) => line.trim() !== "") ?? "";
}

async function tellAboutEnd(event) {
  const { run } = await api.get(`/runs/${event.run_id}`);
  if (run.parent_run_id !== null || !worthATell(run.id)) {
    return;
  }
  tell({
    title: FINISHED_TITLE[event.data.status] ?? "A run has ended",
    body: shorten(firstLine(typeof event.data.result === "string" ? event.data.result : "")),
    tag: `run-${run.id}`,
    route: `#/runs/${run.id}`,
    state: FINISHED_FACE[event.data.status] ?? "idle",
  });
}

/** A draft a run wrote for the owner to send (item 314), in the words the phone hears (`notify/push.py`). */
function tellAboutDraft(event) {
  const { id, channel, to, subject, body } = event.data;
  tell({
    title: DRAFT_TITLE[channel] ?? "Dot drafted a message",
    body: shorten(`To ${to.join(", ")}: ${channel === "mail" ? subject : body}`),
    tag: `draft-${id}`,
    route: `#/runs/${event.run_id}`,
    state: "waiting",
  });
}

function tellAboutCampaign(event) {
  const { campaign_id: id, title, status, note } = event.data;
  const achieved = status === "achieved";
  tell({
    title: achieved ? "A campaign is achieved" : "A campaign stopped",
    body: shorten(`${title}: ${note}`),
    tag: `campaign-${id}`,
    route: `#/campaigns/${id}`,
    state: achieved ? "done" : "idle",
  });
}

function tellAboutMissedFiring(event) {
  const { schedule_id: id, name, status } = event.data;
  tell({ title: "A schedule did not run", body: shorten(`${name}: ${status}`), tag: `schedule-${id}`, route: "#/schedules", state: "failed" });
}

function tellAboutLearningStalled(event) {
  tell({ title: "Dot's learning has stopped", body: shorten(event.data.reason), tag: "learning-stalled", route: "#/settings/memory", state: "failed" });
}

/** What a failure to notify costs: nothing but a line in the console, so it never stops the page from updating. */
function onLifecycleEvent(event) {
  try {
    if (!worthATell(event.run_id)) {
      return;
    }
    if (event.kind === "approval_requested" || event.kind === "question_left") {
      tellAboutApproval(event);
    } else if (event.kind === "run_finished") {
      tellAboutEnd(event).catch((error) => console.warn("could not tell about a finished run", error));
    } else if (event.kind === "draft_created") {
      tellAboutDraft(event);
    } else if (event.kind === "campaign_ended") {
      tellAboutCampaign(event);
    } else if (event.kind === "schedule_missed") {
      tellAboutMissedFiring(event);
    } else if (event.kind === "learning_stalled") {
      tellAboutLearningStalled(event);
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
