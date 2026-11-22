// The mark that stands for an agent where its name is read as text (the sidebar's rows and menu, the top bar's
// breadcrumb): a small square of the ink's tint with the name's two letters, no colour, as the reference app's project marks are.

import { el } from "./dom.js";

// What a reader sees as a word's first character: a letter with its marks, an emoji made of several (item 991).
const GRAPHEMES = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** The first character of `word` as a reader sees it. */
function firstGrapheme(word) {
  return GRAPHEMES.segment(word)[Symbol.iterator]().next().value?.segment ?? "";
}

/** A badge's two letters for an agent's name: the initials of its first two words ("Code Reviewer" is CR), else its first
 * two letters ("Dot" is DO), upper case; a letter as a reader sees it, so an emoji or a letter with marks stays whole. */
export function badgeCode(name) {
  const words = name.split(/[\s._-]+/).filter((word) => word !== "");
  if (words.length >= 2) {
    return `${firstGrapheme(words[0])}${firstGrapheme(words[1])}`.toLocaleUpperCase();
  }
  const letters = [...GRAPHEMES.segment(words[0] ?? "")].map((part) => part.segment);
  return letters.slice(0, 2).join("").toLocaleUpperCase();
}

/** The badge of an agent named `name`, hidden from a screen reader: the name beside it says it. */
export function badge(name) {
  return el("span", { class: "side-badge", "aria-hidden": "true" }, badgeCode(name));
}
