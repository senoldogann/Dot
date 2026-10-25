// The words of "while you were away" (item 466), pure functions of the view of `GET /away`, so they can be read alone
// (tests/js/away.test.js).

import { plural } from "./dom.js";

/** How the schedules' tasks went, the campaigns' sessions, the decisions made for the owner and the drafts that wait,
 * in one line; empty when nothing happened. */
export function awayLine(view) {
  const parts = [];
  const scheduled = view.done + view.failed + view.cancelled + view.working;
  if (scheduled > 0) {
    const how = [
      view.done > 0 ? `${view.done} done` : null,
      view.failed > 0 ? `${view.failed} failed` : null,
      view.cancelled > 0 ? `${view.cancelled} stopped` : null,
      view.working > 0 ? `${view.working} working` : null,
    ].filter((part) => part !== null);
    parts.push(`${plural(scheduled, "scheduled task", "scheduled tasks")}: ${how.join(", ")}`);
  }
  if (view.campaign_sessions > 0) {
    parts.push(plural(view.campaign_sessions, "campaign session", "campaign sessions"));
  }
  const decided = decisionCounts(view.decisions);
  if (decided.total > 0) {
    parts.push(`${plural(decided.total, "decision", "decisions")} made for you (${decidedBy(decided.yolo, decided.reviewer, decided.denied)})`);
  }
  if (view.drafts > 0) {
    parts.push(`${plural(view.drafts, "draft waits", "drafts wait")} for you`);
  }
  return parts.join(" · ");
}

/** Who made the decisions, and how many of them said no: `9 by YOLO, 5 by the reviewer; 2 denied`. */
export function decidedBy(yolo, reviewer, denied) {
  const who = [yolo > 0 ? `${yolo} by YOLO` : null, reviewer > 0 ? `${reviewer} by the reviewer` : null].filter((part) => part !== null);
  return `${who.join(", ")}${denied > 0 ? `; ${denied} denied` : ""}`;
}

function decisionCounts(decisions) {
  const yolo = decisions.yolo_approved + decisions.yolo_denied;
  const reviewer = decisions.reviewer_approved + decisions.reviewer_denied;
  return { yolo, reviewer, total: yolo + reviewer, denied: decisions.yolo_denied + decisions.reviewer_denied };
}
