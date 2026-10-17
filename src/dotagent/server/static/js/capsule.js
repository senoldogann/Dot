// The line over the composer (items 297 and 352), after the line over T3 Code's: how long the run has worked and what
// it does now, or that it waits for you; how its helpers stand; how many messages wait in the queue and how many
// commands run in the chat. Each part is the way to where it is told of. The line shows on every width while one of
// its parts has something to say, and goes once none has; a part comes and goes as what it tells does.

import { duration, el, plural, setText } from "./dom.js";
import { icon } from "./icons.js";
import { flockState } from "./timeline.js";

/**
 * The line. A tap on a part calls `onWork()`, `onHelpers()`, `onQueued()` or `onCommands()`. Returns `element` and
 * `paint({ status, since, now, helpers, activity, queued, commands })`: the run's status and when it began (seconds),
 * the time now, the statuses of its helpers, what the run does now ("" between its steps), the messages queued and the
 * commands at work in its chat. `status` is null for a run that no longer works.
 */
export function createCapsule({ onWork, onHelpers, onQueued, onCommands }) {
  const workMark = el("span", { class: "capsule-mark" });
  const workText = el("span", { class: "capsule-time" });
  const activityText = el("span", { class: "capsule-activity" });
  const work = el("button", { class: "capsule-part capsule-work", type: "button", onclick: onWork }, workMark, workText, activityText);
  const helpersText = el("span");
  const helpers = el("button", { class: "capsule-part", type: "button", onclick: onHelpers }, icon("users"), helpersText);
  const queuedText = el("span");
  const queued = el("button", { class: "capsule-part capsule-queued", type: "button", onclick: onQueued }, icon("listOrdered"), queuedText);
  const commandsText = el("span");
  const commands = el("button", { class: "capsule-part capsule-commands", type: "button", onclick: onCommands }, icon("terminal"), commandsText);
  // A group, not a live region: its time changes every second, which a screen reader would read out each time.
  const element = el("div", { class: "capsule", role: "group", "aria-label": "How the run stands", hidden: true }, work, helpers, queued, commands);
  let markFor = null;

  function paint({ status, since, now, helpers: statuses, activity, queued: waiting, commands: running }) {
    const working = status !== null;
    const blocked = status === "blocked";
    work.hidden = !working;
    work.dataset.state = blocked ? "waiting" : "working";
    if (working && markFor !== work.dataset.state) {
      markFor = work.dataset.state;
      workMark.replaceChildren(icon(blocked ? "bell" : "circleDashed"));
    }
    setText(workText, blocked ? "Waiting for you" : `Working ${duration(Math.max(now - since, 0))}`);
    const doing = working && !blocked ? activity : "";
    activityText.hidden = doing === "";
    setText(activityText, doing);
    helpers.hidden = !working || statuses.length === 0;
    setText(helpersText, flockState(statuses));
    queued.hidden = waiting === 0;
    setText(queuedText, waiting === 0 ? "" : `${waiting} queued`);
    commands.hidden = running === 0;
    setText(commandsText, running === 0 ? "" : plural(running, "command", "commands"));
    element.hidden = !working && waiting === 0 && running === 0;
  }

  return { element, paint };
}
