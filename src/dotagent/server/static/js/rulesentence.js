// A rule drawn as the sentence it reads as (item 622): "When Dot wants to run a command that mentions “rm”, it asks you
// first.", the answer as the coloured badge that ends it. The list of the rules and the adder, which reads a draft back
// before it is added, draw the same sentence (the words are rulewords.js's).

import { el } from "./dom.js";
import { ruleParts } from "./rulewords.js";

/** The sentence of `rule` (`{ tool_glob, match_regex, behavior, description }`) as one inline element. */
export function ruleSentence(rule) {
  const { what, answer } = ruleParts(rule);
  return el(
    "span",
    { class: "rule-sentence" },
    "When Dot wants to ",
    what.map((part) => ("code" in part ? el("code", { class: "mono", title: part.title }, part.code) : part.text)),
    ", it ",
    // The badge and the full stop that follows it stay together, so that the stop never stands alone on a line.
    el("span", { class: "rule-answer" }, el("span", { class: `badge ${answer.tone}`, title: answer.text }, answer.says), "."),
  );
}
