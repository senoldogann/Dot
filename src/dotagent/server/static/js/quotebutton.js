// Quoting a message (item 1972): the Quote button in a message's foot, and in the sheet a hold opens, asks the open
// composer to start its text with the message as a "> quote"; the message sent carries it, which the model reads and the
// chat shows as a quote (messages read as markdown). The phone's app does the same with a swipe.

import { el } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";

/** The event the button sends on the document, with the message's words as its detail. */
export const QUOTE_EVENT = "dot-quote";

// The most characters of a message a quote keeps.
const QUOTE_CHARS = 280;

/**
 * `draft` with `words` quoted at its start: each line of the words (folded blank lines, cut at 280 characters with an
 * ellipsis) behind "> ", a blank line, then the draft without a quote it began with, which this one replaces.
 * The draft unchanged when there is nothing to quote.
 * @param {string} draft
 * @param {string} words
 */
export function quoteInto(draft, words) {
  const lines = words.split("\n").map((line) => line.trim());
  const kept = lines.filter((line, index) => !(line === "" && (index === 0 || lines[index - 1] === "")));
  const joined = kept.join("\n").trim();
  if (joined === "") {
    return draft;
  }
  const cut = joined.length > QUOTE_CHARS ? `${joined.slice(0, QUOTE_CHARS - 1)}…` : joined;
  const block = cut.split("\n").map((line) => (line === "" ? ">" : `> ${line}`)).join("\n");
  const rest = draft.split("\n");
  while (rest.length > 0 && rest[0].startsWith(">")) {
    rest.shift();
  }
  if (rest.length > 0 && rest[0] === "") {
    rest.shift();
  }
  return `${block}\n\n${rest.join("\n")}`;
}

/**
 * `text` split into the quote it begins with (its lines without their "> ", `null` when there is none, and there is none
 * unless a blank line follows them) and the words after it. A command ("/btw", "/loop") and "is anything typed" are read from the words, not from the quote.
 * @param {string} text
 * @returns {{ quote: string | null, words: string }}
 */
export function splitQuote(text) {
  const lines = text.split("\n");
  const quoted = [];
  while (lines.length > 0 && lines[0].startsWith(">")) {
    quoted.push(lines.shift().replace(/^> ?/, ""));
  }
  // A quote is the block this module writes: its lines, then a blank one. Words that merely start with ">" (">3 files",
  // ">= 5") are words.
  if (quoted.length === 0 || lines.length === 0 || lines[0] !== "") {
    return { quote: null, words: text };
  }
  lines.shift();
  return { quote: quoted.join("\n"), words: lines.join("\n") };
}

/** The Quote button of a message whose words are `text` (a string, or a function that makes it when pressed). */
export function quoteButton(text) {
  return el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: t("messages.quote"),
      "aria-label": t("messages.quote"),
      onclick: () => document.dispatchEvent(new CustomEvent(QUOTE_EVENT, { detail: typeof text === "function" ? text() : text })),
    },
    icon("message"),
  );
}
