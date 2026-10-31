// What a waiting approval card says, each thing once (item 872, after OpenMausBot's quiet approvals). The policy's
// summary of a step is its action, the whole command or target in it, its reasons in brackets, then " — " and why Dot
// asks ("Run: cd … && vercel build --prod (runs vercel; vercel puts a project's change live) — On its own initiative,
// Dot asks …"). The card shows the command in a box of its own and, for the questions no rule answers, why it asks in a
// line of its own: said again in the card's words, the owner read the same command and the same reason twice.

import { capital } from "./flagwords.js";

const REASON_MARK = " — ";

// The policy's reasons that the line under a card says in its own words (`_INITIATIVE_REASON` and `OWNERS_REASON` of
// policy/engine.py; tests/unit/test_static_ui.py holds them equal). Only these are left out of the card's words: any
// other reason is the step's own (a payment's amount and caps, a guard's why), and what the review model passed on with
// it is its warning; both stay (the pre-push review of item 872).
export const FIXED_REASONS = {
  initiative: "On its own initiative, Dot asks before it sends a message for you, posts in your name or puts a project's change live, in every mode",
  owners: "In yolo too, Dot asks you before it pays, sends a message or mail for you or posts in your name",
};

/**
 * The words of a waiting card: `text`, the action without the `target` its box shows (what the brackets say, when that
 * is all that is left), `reason`, why Dot asks, without the `said` sentence the card's own line says (one of
 * FIXED_REASONS, or "") and with whatever follows it, and `cut`, whether the target was taken out, which alone lets the
 * text stand for a chip (`unsaid`).
 *
 * The target is taken out only where it is the action's whole object: right after the label ("Run: "), up to its
 * brackets, the reason or the end. Anywhere else (an MCP call's server, which its arguments may hold too: "gmail" in
 * "ali@gmail.com") the summary is kept as it is, whole (the second pre-push review of item 872). With no target, only
 * the line's own sentence is taken off its end: an action may hold " — " itself ("Click 'Save — draft'").
 * @param {string} summary
 * @param {string} target
 * @param {string} said
 * @returns {{ text: string, reason: string, cut: boolean }}
 */
export function cardWords(summary, target, said) {
  if (target === "") {
    const at = said === "" ? -1 : summary.indexOf(REASON_MARK + said);
    if (at === -1) {
      return { text: summary.trim(), reason: "", cut: false };
    }
    const after = summary.slice(at + REASON_MARK.length + said.length).replace(/^[.;,]\s*/, "").trim();
    return { text: summary.slice(0, at).trim(), reason: after, cut: false };
  }
  const label = summary.indexOf(": ");
  const at = label === -1 ? -1 : label + 2;
  const after = at === -1 ? -1 : at + target.length;
  const next = after === -1 ? "" : summary.slice(after);
  const whole = at !== -1 && summary.startsWith(target, at) && (next === "" || next.startsWith(" (") || next.startsWith(REASON_MARK));
  if (!whole) {
    return { text: summary.trim(), reason: "", cut: false };
  }
  const mark = summary.indexOf(REASON_MARK, after);
  const action = mark === -1 ? summary : summary.slice(0, mark);
  const why = mark === -1 ? "" : summary.slice(mark + REASON_MARK.length).trim();
  const reason = said !== "" && why.startsWith(said) ? why.slice(said.length).replace(/^[.;,]\s*/, "").trim() : why;
  const rest = action.slice(after).trim();
  // "Run: (runs vercel; …)" is what the brackets say; "Run:" alone says nothing the box does not.
  const bracketed = rest.match(/^\((.*)\)$/s);
  return { text: bracketed !== null ? capital(bracketed[1].trim()) : rest, reason, cut: true };
}

/**
 * The chips whose words the card's text does not say already: "Puts a project's change live" is not shown again as a
 * chip under the text "Runs vercel; vercel puts a project's change live".
 * @param {string[]} words
 * @param {string} text
 * @returns {string[]}
 */
export function unsaid(words, text) {
  // Whole words only: a short chip inside a longer word of the text is not said by it.
  const escaped = (word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return words.filter((word) => !new RegExp(`(^|[^\\p{L}\\p{N}])${escaped(word)}($|[^\\p{L}\\p{N}])`, "iu").test(text));
}
