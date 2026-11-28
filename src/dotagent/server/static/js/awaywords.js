// The words of "while you were away" (item 466), pure functions of the view of `GET /away`, so they can be read alone
// (tests/js/away.test.js).

import { tCount, t } from "./i18n.js";

/** How the tasks Dot did on its own went (a schedule's, a reply's, an agent's work, a command in the background: item
 * 1424), the campaigns' sessions, the decisions made for the owner and the drafts that wait, in one line; empty when
 * nothing happened. */
export function awayLine(view) {
  const parts = [];
  const own = view.done + view.failed + view.cancelled + view.working;
  if (own > 0) {
    const how = [
      view.done > 0 ? t("awaywords.doneDone", { done: view.done }) : null,
      view.failed > 0 ? t("awaywords.failedFailed", { failed: view.failed }) : null,
      view.cancelled > 0 ? t("awaywords.cancelledStopped", { cancelled: view.cancelled }) : null,
      view.working > 0 ? t("awaywords.workingWorking", { working: view.working }) : null,
    ].filter((part) => part !== null);
    parts.push(`${tCount(own, "awaywords.countTask", "awaywords.countTasks")}: ${how.join(", ")}`);
  }
  if (view.campaign_sessions > 0) {
    parts.push(tCount(view.campaign_sessions, "awaywords.countCampaignSession", "awaywords.countCampaignSessions"));
  }
  const decided = decisionCounts(view.decisions);
  if (decided.total > 0) {
    parts.push(t("awaywords.partMadeForYouDecidedby", { part: tCount(decided.total, "awaywords.countDecision", "awaywords.countDecisions"), decidedBy: decidedBy(decided.yolo, decided.reviewer, decided.denied) }));
  }
  if (view.drafts > 0) {
    parts.push(t("awaywords.partForYou", { part: tCount(view.drafts, "awaywords.countDraftWaits", "awaywords.countDraftsWait") }));
  }
  return parts.join(" · ");
}

/** Who made the decisions, and how many of them said no: `9 without asking, 5 by the reviewer; 2 denied` ("without asking": let through by the mode that asks only to pay). */
export function decidedBy(yolo, reviewer, denied) {
  const who = [yolo > 0 ? t("awaywords.yoloWithoutAsking", { yolo }) : null, reviewer > 0 ? t("awaywords.reviewerByTheReviewer", { reviewer }) : null].filter((part) => part !== null);
  return `${who.join(", ")}${denied > 0 ? t("awaywords.deniedDenied", { denied }) : ""}`;
}

function decisionCounts(decisions) {
  const yolo = decisions.yolo_approved + decisions.yolo_denied;
  const reviewer = decisions.reviewer_approved + decisions.reviewer_denied;
  return { yolo, reviewer, total: yolo + reviewer, denied: decisions.yolo_denied + decisions.reviewer_denied };
}
