// A view of code (item 2017): no line wraps, it scrolls sideways, the numbers stand at its left, its words coloured by
// diffview.js's scanner. A change's diff and a new file (changes.js) and a source file in the file panel (filepanel.js)
// are drawn by it.

import { highlightLines } from "./diffview.js";
import { el } from "./dom.js";
import { appendInPages } from "./lazylist.js";

/** How many rows a code view draws at a time: a file of thousands of lines draws its first rows at once and the next
 * as the reader scrolls to them. */
const ROWS_AT_A_TIME = 400;

/**
 * A line of a code view: its kind (a line added, removed or kept, a hunk's head, a line of a file), its numbers (the
 * old and the new for a diff, the one for a file) and its text.
 * @typedef {{ kind: "hunk" | "added" | "removed" | "context" | "file", numbers: (number | null)[], text: string }} CodeLine
 */

/** The coloured pieces of a line.
 * @param {import("./diffview.js").CodeToken[]} tokens */
function tokenNodes(tokens) {
  return tokens.map((token) => (token.kind === "plain" ? token.text : el("span", { class: `tok-${token.kind}` }, token.text)));
}

/** One row of a code view: its numbers, a sign for a line added or removed, its coloured text; a hunk's head as a quiet
 * band that names where the next lines stand.
 * @param {CodeLine} line
 * @param {import("./diffview.js").CodeToken[]} tokens
 * @param {boolean} signed */
function codeRow(line, tokens, signed) {
  if (line.kind === "hunk") {
    return el("div", { class: "code-row code-hunk", role: "separator" }, el("span", { class: "code-hunk-text" }, line.text === "" ? "⋯" : `⋯ ${line.text}`));
  }
  const sign = line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " ";
  // A file's line is `code-file`: `.code-line` is a box of its own elsewhere (app.css).
  return el(
    "div",
    { class: `code-row code-${line.kind}` },
    el("span", { class: "code-nums", "aria-hidden": "true" }, ...line.numbers.map((number) => el("span", { class: "code-num" }, number === null ? "" : String(number)))),
    signed ? el("span", { class: "code-sign", "aria-hidden": "true" }, sign) : null,
    el("span", { class: "code-text" }, ...tokenNodes(tokens)),
  );
}

/** The tokens of each line, coloured in `language` (none: plain), each hunk read on its own.
 * @param {CodeLine[]} lines
 * @param {string | null} language
 * @returns {import("./diffview.js").CodeToken[][]} */
function lineTokens(lines, language) {
  if (language === null) {
    return lines.map((line) => [{ kind: "plain", text: line.text }]);
  }
  const tokens = [];
  let start = 0;
  for (let index = 0; index <= lines.length; index += 1) {
    if (index === lines.length || lines[index].kind === "hunk") {
      tokens.push(...highlightLines(lines.slice(start, index).map((line) => line.text), language));
      if (index < lines.length) {
        tokens.push([]);
      }
      start = index + 1;
    }
  }
  return tokens;
}

/** A view of code: no line wraps, it scrolls sideways, the numbers stand at its left; the rows are drawn
 * `ROWS_AT_A_TIME` at a time as the reader scrolls. `signed` gives each row its + or −.
 * @param {CodeLine[]} lines
 * @param {string | null} language
 * @param {boolean} signed */
export function codeView(lines, language, signed) {
  const tokens = lineTokens(lines, language);
  const widest = Math.max(1, ...lines.flatMap((line) => line.numbers.map((number) => String(number ?? "").length)));
  const rows = el("div", { class: "code-lines" });
  const view = el("div", { class: "code-view", tabindex: "0", dataset: { signed: String(signed) } }, rows);
  // The numbers' column is as wide as the longest number (CSSOM: the page's policy refuses a style attribute).
  view.style.setProperty("--num-width", `${widest}ch`);
  appendInPages(rows, lines.length, ROWS_AT_A_TIME, (index) => codeRow(lines[index], tokens[index], signed), view);
  return view;
}

/** The lines of a file's text, numbered from 1.
 * @param {string} text
 * @returns {CodeLine[]} */
export function fileLines(text) {
  return text.split("\n").map((line, index) => ({ kind: "file", numbers: [index + 1], text: line }));
}
