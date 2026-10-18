// One row of the activity, as the home's Today strip (item 72) draws it: an icon for its kind, its title, a line of
// facts (what it is, its channel, its detail, its steps or its cost) and, at the side, its state and how long ago. The
// lifecycle events that change the lists are named here too.

import { ago, compact, duration, el } from "./dom.js";
import { actorFace } from "./actors.js";
import { faceState } from "./face.js";
import { icon } from "./icons.js";
import { statusBadge } from "./panels.js";

export const LIFECYCLE_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided", "takeover", "handback"]);

export const KIND_ICON = {
  run: "bolt",
  delegated: "branch",
  campaign: "target",
  approval: "shield",
  question: "help",
  takeover: "hand",
  schedule: "calendar",
  reflection: "sparkle",
  lesson: "sparkle",
  journal: "file",
};

export const KIND_WORD = {
  run: "run",
  delegated: "delegated run",
  campaign: "campaign session",
  approval: "approval",
  question: "question",
  takeover: "taken over",
  schedule: "schedule",
  reflection: "reflection",
  lesson: "lesson",
  journal: "journal",
};

/** The facts of a row, in order: what it is, its channel, its detail, then its steps while it works or its cost once it ended. */
export function rowFacts(item) {
  const facts = [KIND_WORD[item.kind] ?? item.kind, item.channel, item.detail].filter((part) => part !== "");
  if (item.steps !== null && item.steps > 0 && item.minutes === null) {
    facts.push(item.steps === 1 ? "1 step" : `${item.steps} steps`);
  }
  if (item.minutes !== null) {
    // A run that ended: how long it took and what it cost in tokens.
    facts.push(duration(item.minutes * 60), `${compact(item.tokens)} tokens`);
  }
  return facts;
}

/** The row as a link to what it is about; `child` draws it nested under the run that delegated it. */
export function activityRow(item, child) {
  const facts = rowFacts(item);
  // A thing that is due, a lesson or the journal has no state to badge.
  const badge = item.status === "scheduled" || item.status === "" ? null : statusBadge(item.status);
  return el(
    "a",
    { class: child ? "activity-row activity-child" : "activity-row", href: item.href, "aria-label": `${item.title} (${facts.join(", ")})` },
    item.actor_id === null ? el("span", { class: "activity-icon" }, icon(KIND_ICON[item.kind] ?? "bolt")) : actorFace(item.actor_id, faceState(item.status), null),
    el("span", { class: "activity-main" }, el("span", { class: "activity-title" }, item.title), el("span", { class: "small muted meta-facts" }, ...facts.map((fact) => el("span", null, fact)))),
    el("span", { class: "activity-side" }, badge, el("span", { class: "small muted" }, ago(item.since))),
  );
}
