// The composer's shape, as DotNext's composer takes it (item 2015, the phone's items 1924 and 1991): one row while
// what is written is short, with nothing added; the words on a line of their own over the controls once they are long,
// break a line, or the message carries files or picks. Pure, so that it runs under Node without a page.

import { t } from "./i18n.js";

/** The most characters a one-row composer holds before it opens (`ComposerLayout.of` on the phone). */
export const COMPACT_CHARS = 32;

/**
 * "compact" (one row: the "+", the words, the model's mark, the microphone and the send) or "expanded" (the words over
 * the controls, the model named with its level).
 * @param {string} text what the box holds
 * @param {number} files how many files the message carries
 * @param {number} picks how many plugins, accounts and skills are picked for it
 * @returns {"compact" | "expanded"}
 */
export function composerShape(text, files, picks) {
  const long = text.includes("\n") || [...text].length > COMPACT_CHARS;
  return long || files > 0 || picks > 0 ? "expanded" : "compact";
}

/**
 * The words in an empty box, as DotNext's (item 2015): "Message Dot" in a chat with Dot (by the name the owner
 * calls it, item 420), "Ask Scout" in an agent's.
 * @param {string} name who the chat is with
 * @param {boolean} agent whether that is an agent of the owner's rather than Dot
 * @returns {string}
 */
export function emptyBoxWords(name, agent) {
  if (agent) {
    return t("composer.askAgent", { name });
  }
  return name === "Dot" ? t("composer.messageDotPlain") : t("composer.messageDot", { name });
}
