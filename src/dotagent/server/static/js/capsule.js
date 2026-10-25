// The line over the composer (items 297, 352 and 360): how long the run has worked, or that it waits for you; how far
// its plan has come (item 644: a tap opens the plan over the composer); how its helpers stand; how many messages wait
// in the queue. Each part is the way to where it is told of. It says neither what the run does now, which the
// conversation shows, nor how many commands run in the chat, which stand right under it (item 360). The line shows on
// every width while one of its parts has something to say, and goes once none has; a part comes and goes as what it
// tells does.

import { duration, el, setText } from "./dom.js";
import { icon } from "./icons.js";
import { flockState } from "./timeline.js";

/**
 * The line. A tap on a part calls `onWork()`, `onHelpers()` or `onQueued()`; the plan's part is `plan`, whose popover
 * (planbubble.js) opens on its click. Returns `element`, `plan` and
 * `paint({ status, since, now, plan, helpers, queued })`: the run's status and when it began (seconds), the time now,
 * how many of its plan's steps are done of how many (`{ done, total }`, or null), the statuses of its helpers and the
 * messages queued. `status` is null for a run that no longer works.
 */
export function createCapsule({ onWork, onHelpers, onQueued }) {
  const workMark = el("span", { class: "capsule-mark" });
  const workText = el("span", { class: "capsule-time" });
  const work = el("button", { class: "capsule-part capsule-work", type: "button", onclick: onWork }, workMark, workText);
  const planText = el("span");
  const plan = el("button", { class: "capsule-part capsule-plan", type: "button", hidden: true }, icon("squareCheck"), planText);
  const helpersText = el("span");
  const helpers = el("button", { class: "capsule-part", type: "button", onclick: onHelpers }, icon("users"), helpersText);
  const queuedText = el("span");
  const queued = el("button", { class: "capsule-part capsule-queued", type: "button", onclick: onQueued }, icon("listOrdered"), queuedText);
  // A group, not a live region: its time changes every second, which a screen reader would read out each time.
  const element = el("div", { class: "capsule", role: "group", "aria-label": "How the task stands", hidden: true }, work, plan, helpers, queued);
  let markFor = null;

  function paint({ status, since, now, plan: steps = null, helpers: statuses, queued: waiting }) {
    const working = status !== null;
    const total = steps?.total ?? 0;
    plan.hidden = !working || total === 0;
    setText(planText, total === 0 ? "" : `Plan ${steps.done}/${total}`);
    plan.setAttribute("aria-label", total === 0 ? "Plan" : `Plan, ${steps.done} of ${total} steps done`);
    const blocked = status === "blocked";
    work.hidden = !working;
    work.dataset.state = blocked ? "waiting" : "working";
    if (working && markFor !== work.dataset.state) {
      markFor = work.dataset.state;
      workMark.replaceChildren(icon(blocked ? "bell" : "circleDashed"));
    }
    setText(workText, blocked ? "Waiting for you" : `Working ${duration(Math.max(now - since, 0))}`);
    helpers.hidden = !working || statuses.length === 0;
    setText(helpersText, flockState(statuses));
    queued.hidden = waiting === 0;
    setText(queuedText, waiting === 0 ? "" : `${waiting} queued`);
    element.hidden = !working && waiting === 0;
  }

  return { element, plan, paint };
}
