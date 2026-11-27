// The words of a rule's try-out on past steps (item 469) and of each rule's use (item 473), pure functions of the
// answers of `POST /rules/preview` and `GET /rules/usage`, so they can be read alone (tests/js/rulepreview.test.js).

import { t, tCount } from "./i18n.js";

/** The day a moment falls on, as the try-out says how far back it read: `21 Sep`. */
function dayOf(seconds) {
  return new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The lines of what the rule would have done: how many steps it fits, the earlier rules that decide some first, what
 * no rule decides (the steps of a run in the mode that asks only to pay or send, and the checks that come before every rule), and how what it would decide
 * compares with what was decided then. */
export function previewLines(view, days) {
  if (view.scanned === 0) {
    return [t("rulepreview.noStepInTheLast", { part: tCount(days, "rulepreview.countDay", "rulepreview.countDays") })];
  }
  const read = view.partial && view.since !== null ? t("rulepreview.theNewestScannedStepsSince", { scanned: view.scanned, dayOf: dayOf(view.since) }) : t("rulepreview.scannedStepsInTheLast", { scanned: view.scanned, part: tCount(days, "rulepreview.countDay", "rulepreview.countDays") });
  if (view.fits === 0) {
    return [t("rulepreview.noStepOfReadFits", { read })];
  }
  const lines = [t("rulepreview.wouldHaveMatchedFitsOf", { fits: view.fits, read })];
  if (view.shadowed_by.length > 0) {
    const firsts = view.shadowed_by.map((shadow) => `${shadow.description} (${shadow.steps})`).join(", ");
    lines.push(t("rulepreview.anEarlierRuleDecidesSome", { firsts }));
  }
  if (view.in_yolo > 0) {
    lines.push(t("rulepreview.inYoloRanInThe", { in_yolo: view.in_yolo }));
  }
  if (view.before_rules > 0) {
    lines.push(t("rulepreview.partSettledBeforeAnyRule", { part: tCount(view.before_rules, "rulepreview.countIs", "rulepreview.countAre") }));
  }
  if (view.would_decide > 0) {
    // A step that did not run and whose record does not say why, and one from before Dot kept its decisions, are alike
    // here: there is nothing to compare them with.
    const unrecorded = view.stopped + view.unknown;
    const changes = [
      view.stricter > 0 ? t("rulepreview.stricterWouldAskMoreThan", { stricter: view.stricter }) : null,
      view.looser > 0 ? t("rulepreview.looserWouldAskLess", { looser: view.looser }) : null,
      view.same > 0 ? t("rulepreview.sameTheSame", { same: view.same }) : null,
      unrecorded > 0 ? t("rulepreview.unrecordedWithNoDecisionOn", { unrecorded }) : null,
    ].filter((part) => part !== null);
    lines.push(t("rulepreview.itWouldDecideWouldDecide", { would_decide: view.would_decide, value: changes.length > 0 ? `: ${changes.join(", ")}` : "" }));
  }
  return lines;
}

/** What happened to a past step then, in a few words: `recorded` with no decision, the gate did not let it through
 * (refused, declined, handed to you) or another run held what it needed. */
export function outcomeLabel(was, ok, recorded) {
  if (was === null) {
    if (recorded) {
      return t("rulepreview.didNotRun");
    }
    return ok ? "ran" : "failed";
  }
  switch (was.answered_by) {
    case "user":
      return t("rulepreview.youApprovedIt");
    case "reviewer":
      return t("rulepreview.theReviewModelApprovedIt");
    case "yolo":
      return t("rulepreview.ranWithoutAsking");
    default:
      return ok ? "ran" : "failed";
  }
}

/** The line under one of your rules of how it was used on the steps of the last days, with today's rules (item 473):
 * how many it fits, how many it decides, and what decides the others. `numberOf(id)` is a rule's place in the list. */
export function usageLine(usage, view, days, numberOf) {
  const read = view.partial && view.since !== null ? t("rulepreview.ofTheNewestScannedSteps", { scanned: view.scanned, dayOf: dayOf(view.since) }) : t("rulepreview.ofTheLastPart", { part: tCount(days, "rulepreview.countDay", "rulepreview.countDays") });
  if (usage.fits === 0) {
    return t("rulepreview.fitsNoStepRead", { read });
  }
  const parts = [
    t("rulepreview.decidesCount", { count: usage.decides }),
    usage.in_yolo > 0 ? t(usage.in_yolo === 1 ? "rulepreview.inYoloOne" : "rulepreview.inYoloMany", { count: usage.in_yolo }) : null,
    usage.before_rules > 0 ? t(usage.before_rules === 1 ? "rulepreview.beforeRulesOne" : "rulepreview.beforeRulesMany", { count: usage.before_rules }) : null,
    ...usage.shadowed_by.map((shadow) => t("rulepreview.ruleNumberofDecidesStepsFirst", { numberOf: numberOf(shadow.rule_id), steps: shadow.steps })),
  ].filter((part) => part !== null);
  return t("rulepreview.fitsStepsRead", { steps: tCount(usage.fits, "rulepreview.countStep", "rulepreview.countSteps"), read, parts: parts.join("; ") });
}
