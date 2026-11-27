// What Dot says of what you do with its tells (item 1743): pure, so it runs under Node. `GET /api/tells` answers the
// rows `{ kind, subject, counts }` (notify/tells.py): how many tells went out and how many were acted on, opened,
// dismissed, ignored, still waiting for their half hour or have no chat to open, and the share that was opened or acted on.

import { t, tCount } from "./i18n.js";

const KIND_LABELS = {
  brief: t("tellwords.morningBrief"),
  checkin: t("tellwords.eveningCheckIn"),
  watcher: t("tellwords.watchers"),
  initiative: t("tellwords.onItsOwn"),
  run_end: t("tellwords.endsOfYourTasks"),
  schedule_end: t("tellwords.endsOfScheduledTasks"),
  system: t("tellwords.otherNewsFromDot"),
};

/** The counts of `rows` added together. */
export function sumCounts(rows) {
  const total = { sent: 0, acted: 0, opened: 0, dismissed: 0, ignored: 0, pending: 0, unmeasured: 0, precision: null };
  for (const row of rows) {
    for (const key of Object.keys(total)) {
      if (key !== "precision") {
        total[key] += row.counts[key];
      }
    }
  }
  const answered = total.acted + total.opened + total.dismissed + total.ignored;
  total.precision = answered === 0 ? null : (total.acted + total.opened) / answered;
  return total;
}

/** The rows of one kind, and of one subject of it (a watcher's id) when `subject` is given. */
export function rowsOf(rows, kind, subject) {
  return rows.filter((row) => row.kind === kind && (subject === undefined || row.subject === subject));
}

/**
 * One line of what came of the tells in `counts`: "12 told · 8 opened or acted on · 2 dismissed · 2 ignored · 80% read",
 * the parts with none left out, and "Nothing told yet" for none. A tell that opens no chat cannot be seen opened: it is
 * counted apart ("3 with no chat to open") and left out of the share.
 */
export function countsLine(counts) {
  if (counts.sent === 0) {
    return t("tellwords.nothingToldYet");
  }
  const parts = [t("tellwords.sentTold", { sent: counts.sent })];
  const read = counts.acted + counts.opened;
  const wanted = [
    [read, t("tellwords.readOpenedOrActedOn", { read })],
    [counts.dismissed, t("tellwords.dismissedDismissed", { dismissed: counts.dismissed })],
    [counts.ignored, t("tellwords.ignoredIgnored", { ignored: counts.ignored })],
    [counts.pending, t("tellwords.pendingWaiting", { pending: counts.pending })],
    [counts.unmeasured, t("tellwords.unmeasuredWithNoChatTo", { unmeasured: counts.unmeasured })],
  ];
  for (const [number, words] of wanted) {
    if (number > 0) {
      parts.push(words);
    }
  }
  if (counts.precision !== null) {
    parts.push(t("tellwords.roundRead", { round: Math.round(counts.precision * 100) }));
  }
  return parts.join(" · ");
}

const KINDS = ["brief", "checkin", "watcher", "initiative", "run_end", "schedule_end", "system"];

/** The lines of the panel (tellspanel.js): one for each kind that told anything, in the order of the kinds. */
export function tellLines(report) {
  return KINDS.map((kind) => ({ kind, counts: sumCounts(rowsOf(report.rows, kind)) }))
    .filter((line) => line.counts.sent > 0)
    .map((line) => ({ kind: line.kind, label: KIND_LABELS[line.kind], text: countsLine(line.counts) }));
}

/** The heading of the panel: the days it counts. */
export function windowWords(days) {
  return t("tellwords.inTheLastPart", { part: tCount(days, "tellwords.countDay", "tellwords.countDays") });
}
