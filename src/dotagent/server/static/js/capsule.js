// The line over the composer (items 297, 352 and 360): how long the run has worked, or that it waits for you; how far
// its plan has come (item 644: a tap opens the plan over the composer); how its helpers stand; how many messages wait
// in the queue. Each part is the way to where it is told of. It says neither what the run does now, which the
// conversation shows, nor how many commands run in the chat, which stand right under it (item 360). The line shows on
// every width while one of its parts has something to say, and goes once none has; a part comes and goes as what it
// tells does. The run is stopped from the composer's send alone, a Stop while the box is empty (item 120); once it is
// pressed the line says it stops until the run ends.

import { el, elapsed, setText } from "./dom.js";
import { t, tCount } from "./i18n.js";
import { icon } from "./icons.js";
import { flockState } from "./timeline.js";
import { waitCounts } from "./waitcounts.js";

/**
 * The line. A tap on a part calls `onWork()`, `onHelpers()` or `onQueued()`; the plan's part is `plan`,
 * whose popover (planbubble.js) opens on its click. Returns `element`, `plan`, `stopping()` (Stop was pressed: the line
 * says the run stops until it no longer works) and `paint({ status, since, now, plan, helpers, queued })`:
 * the run's status and when it began (seconds), the time now, how many of its plan's steps are done of how many
 * (`{ done, total }`, or null), the statuses of its helpers, the statuses of those it stopped waiting for (`released`)
 * and the messages queued.
 * `status` is null for a run that no longer works.
 */
export function createCapsule({ onWork, onHelpers, onQueued, onStop }) {
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
  const element = el("div", { class: "capsule", role: "group", "aria-label": t("capsule.label"), hidden: true }, work, plan, helpers, queued);
  // The line over the bar while the run waits for subagents (item 1331), as a developer tool says it: "Waiting on 2
  // subagents" and what the first of them does now, Details (the Agents tab) and Stop.
  const waitText = el("span", { class: "wait-line-text" });
  const waitNow = el("span", { class: "wait-line-now" });
  const wait = el(
    "div",
    { class: "wait-line", hidden: true, role: "group", "aria-label": t("capsule.waitLabel") },
    el("span", { class: "wait-line-mark", "aria-hidden": "true" }, icon("circleDashed")),
    el("span", { class: "wait-line-main" }, waitText, waitNow),
    el("button", { class: "wait-line-button", type: "button", onclick: onHelpers }, t("capsule.details")),
    el("button", { class: "wait-line-button", type: "button", onclick: onStop }, t("capsule.stop")),
  );
  let markFor = null;
  // Stop was pressed and the run has not ended yet.
  let halting = false;

  function paint({ status, since, now, plan: steps = null, helpers: statuses, helpersNow = [], released = [], queued: waiting }) {
    const working = status !== null;
    if (!working) {
      halting = false;
    }
    const total = steps?.total ?? 0;
    plan.hidden = !working || total === 0;
    setText(planText, total === 0 ? "" : t("capsule.plan", { done: steps.done, total }));
    plan.setAttribute("aria-label", total === 0 ? t("capsule.planLabel") : t("capsule.planDone", { done: steps.done, total }));
    const blocked = status === "blocked";
    work.hidden = !working;
    work.dataset.state = blocked ? "waiting" : "working";
    if (working && markFor !== work.dataset.state) {
      markFor = work.dataset.state;
      workMark.replaceChildren(icon(blocked ? "bell" : "circleDashed"));
    }
    const took = elapsed(now - since);
    setText(
      workText,
      halting ? t("capsule.stopping") : blocked ? t("work.waitingForYou") : status === "queued" ? t("work.queued") : t("capsule.working", { took }),
    );
    const { live, letGo, waitedFor } = waitCounts(statuses, released);
    // A helper the run stopped waiting for (the owner wrote to it, or it asked for no wait: item 1351) works in the
    // background: the line says the run waits only while one of its live helpers is still waited for.
    wait.hidden = !working || live === 0 || halting;
    wait.setAttribute("aria-label", t(waitedFor > 0 ? "capsule.waitLabel" : "capsule.backgroundLabel"));
    setText(
      waitText,
      live === 0 ? "" : waitedFor > 0 ? tCount(waitedFor, "capsule.waitOne", "capsule.waitMany") : tCount(letGo, "capsule.backgroundOne", "capsule.backgroundMany"),
    );
    const first = helpersNow[0];
    setText(waitNow, first === undefined ? "" : `${first.title}: ${first.words}`);
    helpers.hidden = !working || statuses.length === 0;
    setText(helpersText, flockState(statuses));
    queued.hidden = waiting === 0;
    setText(queuedText, waiting === 0 ? "" : t("capsule.queued", { count: waiting }));
    element.hidden = !working && waiting === 0;
  }

  return {
    element,
    wait,
    plan,
    paint,
    stopping: () => {
      halting = true;
      setText(workText, t("capsule.stopping"));
    },
  };
}
