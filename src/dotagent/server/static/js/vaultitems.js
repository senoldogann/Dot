// What Settings › Vault says of an item and of an import (item 621), apart from the page: the words of a row, whether an item
// answers a search, and what an import did in the words the page shows. Pure; tried without a browser
// (tests/js/vaultitems.test.js).

import { foldSearch, } from "./dom.js";
import { BRAND_NAMES } from "./vaultcard.js";
import { t, tCount } from "./i18n.js";

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
    return { title: item.label, sub: item.username ?? t("vaultitems.noUsername") };
  }
  if (item.kind === "card") {
    const card = `${BRAND_NAMES[item.brand ?? "other"] ?? BRAND_NAMES.other} •••• ${item.last4}`;
    return item.nickname ? { title: item.nickname, sub: card } : { title: card, sub: t("vaultitems.card") };
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
 * @param {{ format: string, rows: number, imported: number, updated?: number, updated_logins?: string[], skipped: Array<{ reason: string, count: number }>, notes_left_out: number, stopped: string | null }} report
 */
export function reportWords(report) {
  const lines = [t("vaultitems.importedPartFromFormatS", { part: tCount(report.imported, "vaultitems.countLogin", "vaultitems.countLogins"), format: report.format })];
  // Logins already saved whose password the file changed (item 791).
  const updated = report.updated ?? 0;
  if (updated > 0) {
    const named = (report.updated_logins ?? []).join(", ");
    lines.push(
      t("vaultitems.updatedThePasswordOfPart", { part: tCount(updated, "vaultitems.countLogin", "vaultitems.countLogins"), value: named ? `: ${named}` : "" }),
    );
  }
  const skipped = report.skipped.reduce((sum, one) => sum + one.count, 0);
  if (skipped > 0) {
    lines.push(t("vaultitems.leftOutSkippedOfRows", { skipped, rows: report.rows, join: report.skipped.map((one) => `${one.reason} (${one.count})`).join(", ") }));
  }
  if (report.notes_left_out > 0) {
    lines.push(t("vaultitems.partCameWithoutNotesWhich", { part: tCount(report.notes_left_out, "vaultitems.countLogin", "vaultitems.countLogins") }));
  }
  if (report.stopped !== null) {
    lines.push(t("vaultitems.theImportStoppedStoppedThe", { stopped: report.stopped }));
  }
  lines.push(t("vaultitems.deleteTheCsvFileNow"));
  return lines;
}
