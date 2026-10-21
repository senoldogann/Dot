// The words of a rule's try-out on past steps (item 469) and of each rule's use (item 473), pure functions of the
// answers of `POST /rules/preview` and `GET /rules/usage`, so they can be read alone (tests/js/rulepreview.test.js).

import { plural } from "./dom.js";

/** The day a moment falls on, as the try-out says how far back it read: `21 Sep`. */
function dayOf(seconds) {
  return new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The lines of what the rule would have done: how many steps it fits, the earlier rules that decide some first, what
 * no rule decides (a YOLO run's steps, and the checks that come before every rule), and how what it would decide
 * compares with what was decided then. */
export function previewLines(view, days) {
  if (view.scanned === 0) {
    return [`No step in the last ${plural(days, "day", "days")} reached the policy.`];
  }
  const read = view.partial && view.since !== null ? `the newest ${view.scanned} steps (since ${dayOf(view.since)})` : `${view.scanned} steps in the last ${plural(days, "day", "days")}`;
  if (view.fits === 0) {
    return [`No step of ${read} fits. The rule is narrow, or the pattern is off; only steps still in the database count.`];
  }
  const lines = [`Would have matched ${view.fits} of ${read}.`];
  if (view.shadowed_by.length > 0) {
    const firsts = view.shadowed_by.map((shadow) => `${shadow.description} (${shadow.steps})`).join(", ");
    lines.push(`An earlier rule decides some of them first: ${firsts}.`);
  }
  if (view.in_yolo > 0) {
    lines.push(`${view.in_yolo} ran in YOLO, which asks nothing whatever the rules say.`);
  }
  if (view.before_rules > 0) {
    lines.push(`${plural(view.before_rules, "is", "are")} settled before any rule, by checks no rule changes, such as money, credentials, messages and posts, and Dot's own files.`);
  }
  if (view.would_decide > 0) {
    // A step that did not run and whose record does not say why, and one from before Dot kept its decisions, are alike
    // here: there is nothing to compare them with.
    const unrecorded = view.stopped + view.unknown;
    const changes = [
      view.stricter > 0 ? `${view.stricter} would ask more than they did` : null,
      view.looser > 0 ? `${view.looser} would ask less` : null,
      view.same > 0 ? `${view.same} the same` : null,
      unrecorded > 0 ? `${unrecorded} with no decision on record` : null,
    ].filter((part) => part !== null);
    lines.push(`It would decide ${view.would_decide} of them${changes.length > 0 ? `: ${changes.join(", ")}` : ""}.`);
  }
  return lines;
}

/** What happened to a past step then, in a few words: `recorded` with no decision, the gate did not let it through
 * (refused, declined, handed to you) or another run held what it needed. */
export function outcomeLabel(was, ok, recorded) {
  if (was === null) {
    if (recorded) {
      return "did not run";
    }
    return ok ? "ran" : "failed";
  }
  switch (was.answered_by) {
    case "user":
      return "you approved it";
    case "reviewer":
      return "the review model approved it";
    case "yolo":
      return "ran, YOLO";
    default:
      return ok ? "ran" : "failed";
  }
}

/** The line under one of your rules of how it was used on the steps of the last days, with today's rules (item 473):
 * how many it fits, how many it decides, and what decides the others. `numberOf(id)` is a rule's place in the list. */
export function usageLine(usage, view, days, numberOf) {
  const read = view.partial && view.since !== null ? `of the newest ${view.scanned} steps (since ${dayOf(view.since)})` : `of the last ${plural(days, "day", "days")}`;
  if (usage.fits === 0) {
    return `Fits no step ${read}.`;
  }
  const parts = [
    `decides ${usage.decides}`,
    usage.in_yolo > 0 ? `${usage.in_yolo} ${usage.in_yolo === 1 ? "was" : "were"} in YOLO runs, which ask nothing` : null,
    usage.before_rules > 0 ? `${usage.before_rules} ${usage.before_rules === 1 ? "was" : "were"} settled before any rule` : null,
    ...usage.shadowed_by.map((shadow) => `rule #${numberOf(shadow.rule_id)} decides ${shadow.steps} first`),
  ].filter((part) => part !== null);
  return `Fits ${plural(usage.fits, "step", "steps")} ${read}: ${parts.join("; ")}.`;
}
