// The chips of the chat search (the owner's ask of 2026-11-27, after Grok's): All, Messages, Bots, Files, and which finds
// each shows. The same rules as the phone's (DotEngine `scopedFinds`, `cutShort`, `markedWord`). Pure: no page here.

import { foldSearch } from "./dom.js";

/** The chips, in their order. Dot has no group chats to show apart: a chat with a team is a chat. */
export const SEARCH_SCOPES = ["all", "messages", "bots", "files"];

/** How many finds of each kind All shows before its "See all" (the kind's chip). */
export const ALL_SHOWN = 3;

/**
 * The finds a chip shows: All a few of each kind, the others every find of their kind and nothing else. `finds` is
 * `{ bots, messages, files }`; the chats found by their titles are the list's own, which Bots and Files hide.
 * @param {{ bots: unknown[], messages: unknown[], files: unknown[] }} finds
 * @param {string} scope
 */
export function scopedFinds(finds, scope) {
  if (scope === "all") {
    return { bots: finds.bots.slice(0, ALL_SHOWN), messages: finds.messages.slice(0, ALL_SHOWN), files: finds.files.slice(0, ALL_SHOWN) };
  }
  return {
    bots: scope === "bots" ? finds.bots : [],
    messages: scope === "messages" ? finds.messages : [],
    files: scope === "files" ? finds.files : [],
  };
}

/**
 * The kinds All cut short, each shown whole under its chip ("See all").
 * @param {{ bots: unknown[], messages: unknown[], files: unknown[] }} finds
 * @returns {Set<string>}
 */
export function cutShort(finds) {
  return new Set(["bots", "messages", "files"].filter((kind) => finds[/** @type {"bots" | "messages" | "files"} */ (kind)].length > ALL_SHOWN));
}

/**
 * The word of a search snippet to find in its chat when the find opens it: the first the daemon marked `[...]`; null
 * for a snippet with no mark.
 * @param {string} snippet
 * @returns {string | null}
 */
export function markedWord(snippet) {
  const match = /\[([^\]]*)\]/.exec(snippet);
  const word = match === null ? "" : match[1].trim();
  return word === "" ? null : word;
}

/**
 * The longest piece of a snippet, its marks and "..." taken out: the words the found message holds as they are.
 * @param {string} snippet
 */
export function snippetPiece(snippet) {
  const pieces = snippet
    .replaceAll("[", "")
    .replaceAll("]", "")
    .split("...")
    .map((piece) => piece.trim());
  return pieces.reduce((longest, piece) => (piece.length > longest.length ? piece : longest), "");
}

/**
 * The agents whose name or role holds every word of `query`, folded (case, accents and the Turkish i do not matter).
 * @template {{ record: { name: string, role: string } }} T
 * @param {T[]} agents
 * @param {string} query
 * @returns {T[]}
 */
export function botsMatching(agents, query) {
  const words = foldSearch(query).split(/\s+/).filter((word) => word !== "");
  if (words.length === 0) {
    return [];
  }
  return agents.filter(({ record }) => {
    const text = foldSearch(`${record.name} ${record.role}`);
    return words.every((word) => text.includes(word));
  });
}
