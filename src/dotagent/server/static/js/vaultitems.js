// What Settings › Vault says of an item and of an import (item 621), apart from the page: the words of a row, whether an item
// answers a search, and what an import did in the words the page shows. Pure; tried without a browser
// (tests/js/vaultitems.test.js).

import { foldSearch, plural } from "./dom.js";
import { BRAND_NAMES } from "./vaultcard.js";

// The details of a contact in the order a row says them.
const ADDRESS_ORDER = ["name", "address", "address2", "city", "region", "postal_code", "country", "phone", "email"];
const DETAILS_IN_A_ROW = 3;

/**
 * What an item is called in its row, and the line under that: a login by its site over its username, a card by its nickname
 * (else its brand and last four) over its brand and last four, a contact by its label over the first of its details.
 * @param {{ kind: string, label: string, username: string | null, brand: string | null, last4: string | null, nickname: string | null, fields: Record<string, string> }} item
 */
export function rowWords(item) {
  if (item.kind === "login") {
    return { title: item.label, sub: item.username ?? "No username" };
  }
  if (item.kind === "card") {
    const card = `${BRAND_NAMES[item.brand ?? "other"] ?? BRAND_NAMES.other} •••• ${item.last4}`;
    return item.nickname ? { title: item.nickname, sub: card } : { title: card, sub: "Card" };
  }
  const details = ADDRESS_ORDER.map((name) => item.fields[name]).filter((value) => value !== undefined && value !== "");
  return { title: item.label, sub: details.slice(0, DETAILS_IN_A_ROW).join(" · ") };
}

/**
 * Whether `item` has every word of `words` (already folded, see `foldSearch`) among what its row says and what the database
 * keeps of it.
 * @param {any} item
 * @param {string[]} words
 */
export function itemMatches(item, words) {
  if (words.length === 0) {
    return true;
  }
  const { title, sub } = rowWords(item);
  const own = [item.label, item.website, item.username, item.nickname, item.last4, item.brand, title, sub, ...Object.values(item.fields)];
  const haystack = foldSearch(own.filter((part) => typeof part === "string" && part !== "").join(" "));
  return words.every((word) => haystack.includes(word));
}

/**
 * What an import did, in the words the page shows: how many logins went in, how many rows were left out and why, the logins
 * that lost their notes, why it stopped when the Keychain ended it, and that the file is to be deleted.
 * @param {{ format: string, rows: number, imported: number, skipped: Array<{ reason: string, count: number }>, notes_left_out: number, stopped: string | null }} report
 */
export function reportWords(report) {
  const lines = [`Imported ${plural(report.imported, "login", "logins")} from ${report.format}'s file.`];
  const skipped = report.skipped.reduce((sum, one) => sum + one.count, 0);
  if (skipped > 0) {
    lines.push(`Left out ${skipped} of ${report.rows} rows: ${report.skipped.map((one) => `${one.reason} (${one.count})`).join(", ")}.`);
  }
  if (report.notes_left_out > 0) {
    lines.push(`${plural(report.notes_left_out, "login", "logins")} came without notes, which were too long to keep.`);
  }
  if (report.stopped !== null) {
    lines.push(`The import stopped: ${report.stopped}. The ones before it are kept; import the file again to add the rest.`);
  }
  lines.push("Delete the CSV file now: it holds your passwords in plain text. Empty the Trash too.");
  return lines;
}
