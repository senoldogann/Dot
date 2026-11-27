// A message the chat search found (searchfinds.js): its chat opens on the run it is of (`#/runs/<id>`), scrolled to the
// message, which glows for a moment (`.find-hit`). The find waits here between the click and the chat's first frame.

import { foldSearch } from "./dom.js";
import { snippetPiece } from "./searchscope.js";

// How long the found message glows.
const GLOW_MS = 2400;

/** @type {{ runId: string, word: string, snippet: string } | null} */
let pending = null;

/**
 * Keep the find for the chat about to open on `runId`.
 * @param {string} runId
 * @param {string} word
 * @param {string} snippet
 */
export function findOnOpen(runId, word, snippet) {
  pending = { runId, word, snippet };
}

/**
 * Scroll the chat drawn in `root` to the message the search found for `runId`, if one waits: the first message or
 * answer whose words hold the snippet's longest piece, else the first that holds its word; it glows for a moment.
 * Whether one was found.
 * @param {HTMLElement} root
 * @param {string} runId
 */
export function revealFind(root, runId) {
  if (pending === null || pending.runId !== runId) {
    return false;
  }
  const { word, snippet } = pending;
  pending = null;
  const blocks = [...root.querySelectorAll(".bubble, .prose")];
  const piece = foldSearch(snippetPiece(snippet));
  const folded = foldSearch(word);
  const holds = (needle) => blocks.find((block) => needle !== "" && foldSearch(block.textContent ?? "").includes(needle));
  const found = holds(piece) ?? holds(folded);
  if (found === undefined) {
    return false;
  }
  found.scrollIntoView({ block: "center" });
  found.classList.add("find-hit");
  setTimeout(() => found.classList.remove("find-hit"), GLOW_MS);
  return true;
}
