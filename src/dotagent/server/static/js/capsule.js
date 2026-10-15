// The line over a phone's composer (item 297), after the capsule over T3 Code's: how long the run has worked, or that it
// waits for you, and how its helpers stand. Each part is the way to where it is told of, and the line shows only while
// the run works. T3 Code's line also counts the queued messages; Dot's queue has a banner of its own right under the
// line, which says it. A wider page tells the same in the conversation and the side panel, and shows no line (app.css).

import { duration, el } from "./dom.js";
import { icon } from "./icons.js";
import { flockState } from "./timeline.js";

/**
 * The line. A tap on a part calls `onWork()` or `onHelpers()`. Returns `element` and `paint({ status, since, now,
 * helpers })`: the run's status and when it began (seconds), the time now and the statuses of its helpers. `status` is
 * null for a run that no longer works.
 */
export function createCapsule({ onWork, onHelpers }) {
  const workMark = el("span", { class: "capsule-mark" });
  const workText = el("span", { class: "capsule-time" });
  const work = el("button", { class: "capsule-part capsule-work", type: "button", onclick: onWork }, workMark, workText);
  const helpersText = el("span");
  const helpers = el("button", { class: "capsule-part", type: "button", onclick: onHelpers }, icon("users"), helpersText);
  // A group, not a live region: its time changes every second, which a screen reader would read out each time.
  const element = el("div", { class: "capsule", role: "group", "aria-label": "How the run stands", hidden: true }, work, helpers);
  let markFor = null;

  function paint({ status, since, now, helpers: statuses }) {
    const working = status !== null;
    const waiting = status === "blocked";
    work.hidden = !working;
    work.dataset.state = waiting ? "waiting" : "working";
    if (working && markFor !== work.dataset.state) {
      markFor = work.dataset.state;
      workMark.replaceChildren(icon(waiting ? "bell" : "circleDashed"));
    }
    workText.textContent = waiting ? "Waiting for you" : `Working ${duration(Math.max(now - since, 0))}`;
    helpers.hidden = !working || statuses.length === 0;
    helpersText.textContent = flockState(statuses);
    element.hidden = !working;
  }

  return { element, paint };
}
