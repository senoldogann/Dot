// The policy's reason for asking, said as one plain sentence (item 22 of the 2026-11-26 review). The policy writes its
// reasons for the log ("aggressive preset still asks for: privilege", policy/engine.py `preset_verdict`); an approval
// card says what Dot wants to do and, in this sentence, why it asks, and folds the policy's own words under Details. Pure,
// so it can be read alone (tests/js/approvalwords.test.js).

import { flagWords, knownFlag } from "./flagwords.js";
import { listAnd, t } from "./i18n.js";

const CAUTIOUS_REASON = "cautious preset asks before anything that is not read-only";
const PRESET_REASON = /^(?:balanced|aggressive) preset (?:still )?asks for: (.+)$/;

/** What one name in a preset's list of reasons says of the step, as a phrase that follows "this": a flag of the policy
 * (`privilege`) or one of the three other names `preset_verdict` writes. */
function nameWords(name) {
  if (name === "outside the workspace") {
    return flagWords("outside_workspace");
  }
  if (name === "destructive outside the workspace") {
    return t("approvalwords.destructiveOutside");
  }
  if (name === "high risk") {
    return t("approvalwords.highRisk");
  }
  return knownFlag(name) ? flagWords(name) : null;
}

/**
 * The sentence for a policy reason that names a preset, or null when the reason is anything else (a rule's own words, the
 * review model's warning, a flag with no phrase of its own), which a card shows as it is.
 * @param {string} reason
 * @returns {string | null}
 */
export function plainReason(reason) {
  if (reason === CAUTIOUS_REASON) {
    return t("approvalwords.cautious");
  }
  const found = PRESET_REASON.exec(reason);
  if (found === null) {
    return null;
  }
  const names = found[1]
    .split(",")
    .map((name) => name.trim())
    .filter((name) => name !== "");
  if (names.length === 0) {
    return null;
  }
  const phrases = names.map(nameWords);
  // A name the policy raises that has no phrase here is shown as the policy wrote it, never as a program's name in a sentence.
  if (phrases.some((phrase) => phrase === null)) {
    return null;
  }
  return t("approvalwords.asksBecause", { what: listAnd(phrases) });
}

/** The risk level of a step in words: the policy's `high`, else the level it names. */
export function riskWords(severity) {
  return severity === "high" ? t("approvalwords.riskHigh") : severity === "medium" ? t("approvalwords.riskMedium") : severity === "low" ? t("approvalwords.riskLow") : severity;
}

const REASON_MARK = " — ";

/**
 * A step's summary without the policy's own reason for asking at its end ("Run: sudo -n true (…) — aggressive preset
 * still asks for: privilege"), for a line that has room for the step alone: a chat's row in the sidebar. A summary whose
 * tail is not such a reason (a dash in a question's own words) is kept whole.
 * @param {string} summary
 * @returns {string}
 */
export function withoutPolicyReason(summary) {
  const at = summary.lastIndexOf(REASON_MARK);
  if (at === -1) {
    return summary;
  }
  return plainReason(summary.slice(at + REASON_MARK.length).trim()) === null ? summary : summary.slice(0, at).trim();
}
