// Why a step ran (item 468): what the policy decided of it and who answered, in words, from the decision the step's
// result keeps (`tool_result`'s `decision`). Pure, so it can be read alone (tests/js/why.test.js).

import { flagWords } from "./flagwords.js";
import { t } from "./i18n.js";

// The kinds of action that only observe (policy/types.py READ_ONLY_KINDS): a plain allow of the preset is not worth a
// line on them, as nearly every step of a run is one.
const READ_ONLY_KINDS = new Set(["read", "network", "browser_read", "browser_navigate", "desktop_observe", "memory_read", "mcp_read", "delegate", "meta"]);

/** Whether a tool row says why its step ran: an acting step always, one that only observed when more than the preset
 * decided it. A step a yolo run took without asking (item 500) says nothing, as the UI does not warn about the mode. A
 * step recorded before item 468 has no decision, and says nothing. */
export function showsWhy(decision, kind) {
  if (decision === null || decision === undefined || decision.answered_by === "yolo") {
    return false;
  }
  return !READ_ONLY_KINDS.has(kind) || decision.source !== "preset" || decision.answered_by !== "policy";
}

/** A reason without the full stop it may end with, so a sentence ends once. */
function bare(reason) {
  return reason.replace(/[.\s]+$/, "");
}

function sentence(decision) {
  const reason = bare(decision.reason);
  switch (decision.answered_by) {
    case "reviewer":
      return t("why.theReviewerApprovedItDot", { reason });
    case "user":
      return decision.source === "supervised" ? t("why.supervisedYouApprovedIt") : t("why.youApprovedItDotS", { reason });
    default:
      if (decision.source === "rule") {
        return t("why.allowedByYourRuleReason", { reason });
      }
      if (decision.source === "preset") {
        // The aggressive preset's reason is its own name: said once, not as "the aggressive preset: aggressive preset".
        return reason.toLowerCase() === `${decision.preset} preset` ? t("why.allowedByThePresetSafety", { preset: decision.preset }) : t("why.allowedByThePresetSafety2", { preset: decision.preset, reason });
      }
      return t("why.allowedReason", { reason });
  }
}

/** The words of why a step ran, with the flags its action raised. */
export function whyText(decision, flags) {
  return flags.length === 0 ? sentence(decision) : t("why.sentenceItCountsAsJoin", { sentence: sentence(decision), join: flags.map(flagWords).join(", ") });
}
