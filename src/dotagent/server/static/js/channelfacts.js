// What Settings › Channels says of what iMessage sees of Messages' database (item 595), in words and units: sizes as
// Finder writes them, moments as the page reads clock times, the rows of facts in the order the tab shows them, and the
// line a press of Check now says. Pure, so that it runs under Node without a page.

import { t } from "./i18n.js";

/**
 * @typedef {{ size: number, changed_at: number }} SeenFile
 * @typedef {{ rowid: number, dated: number | null, seen_at: number }} SeenRow
 * @typedef {{
 *   looked_at: number | null,
 *   cursor: number | null,
 *   newest: SeenRow | null,
 *   chat_db: SeenFile | null,
 *   chat_db_wal: SeenFile | null,
 *   command: { rowid: number, at: number } | null,
 *   error: { message: string, at: number } | null,
 *   cursor_reset: { from_row: number, to_row: number, at: number } | null,
 *   warning: string | null,
 * }} Seen
 * @typedef {{ on: boolean, handles: string[], seen: Seen, links: string | null, links_off: boolean }} ImessageView
 */

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/**
 * A size in bytes as Finder writes it, in powers of 1,000: "512 B", "4.1 KB", "412 MB", "1.2 GB".
 * @param {number} bytes
 */
export function byteSize(bytes) {
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 || value >= 100 ? 0 : 1)} ${UNITS[unit]}`;
}

/**
 * A moment (seconds since the epoch) as the page reads it: its time with seconds when it is today ("14:02:11"), else the
 * day before it ("22 Oct, 14:02:11"). `zone` is a time zone's name, for a test; the page's own by default.
 * @param {number} seconds
 * @param {number} [now]
 * @param {string} [zone]
 */
export function moment(seconds, now = Date.now() / 1000, zone = undefined) {
  const when = new Date(seconds * 1000);
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
  const time = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(when);
  if (day.format(when) === day.format(new Date(now * 1000))) {
    return time;
  }
  const date = new Intl.DateTimeFormat("en-GB", { timeZone: zone, day: "numeric", month: "short" }).format(when);
  return `${date}, ${time}`;
}

/**
 * The facts of what the channel sees as rows of a label and what it says, in the order the tab shows them: the newest
 * message of the database and when it was first seen, how far Dot has read, when it last moved down (a row only once
 * it did, item 611), when it last looked, the two files, the last command and the last error.
 * @param {Seen} seen
 * @param {number} [now]
 * @param {string} [zone]
 * @returns {[string, string][]}
 */
export function seenFacts(seen, now = Date.now() / 1000, zone = undefined) {
  const looked = seen.looked_at !== null;
  const at = (/** @type {number} */ seconds) => moment(seconds, now, zone);
  /** @param {SeenFile | null} found @param {string} missing */
  const file = (found, missing) => (!looked ? t("channelfacts.notLookedAtYet") : found === null ? missing : t("channelfacts.bytesizeChangedAt", { byteSize: byteSize(found.size), at: at(found.changed_at) }));
  let newest = t("channelfacts.dotHasNotLookedYet");
  if (looked) {
    newest = t("channelfacts.noneTheDatabaseHoldsNo");
    if (seen.newest !== null) {
      const dated = seen.newest.dated === null ? t("channelfacts.withNoDate") : t("channelfacts.datedAt", { at: at(seen.newest.dated) });
      newest = t("channelfacts.rowRowidDatedFirstSeen", { rowid: seen.newest.rowid, dated, at: at(seen.newest.seen_at) });
    }
  }
  /** @type {[string, string][]} */
  const moved = seen.cursor_reset
    ? [
        [
          t("channelfacts.movedDown"),
          t("channelfacts.fromRowFromRowTo", { from_row: seen.cursor_reset.from_row, to_row: seen.cursor_reset.to_row, at: at(seen.cursor_reset.at) }),
        ],
      ]
    : [];
  return [
    [t("channelfacts.newestMessage"), newest],
    [t("channelfacts.readUpTo"), seen.cursor === null ? t("channelfacts.notSetYet") : t("channelfacts.rowCursor", { cursor: seen.cursor })],
    ...moved,
    [t("channelfacts.lastLooked"), looked ? at(seen.looked_at) : "never"],
    ["chat.db", file(seen.chat_db, t("channelfacts.notFound"))],
    ["chat.db-wal", file(seen.chat_db_wal, t("channelfacts.noneNothingWaitsInIt"))],
    [t("channelfacts.lastCommand"), seen.command === null ? t("channelfacts.noneYet") : t("channelfacts.rowRowidTakenAt", { rowid: seen.command.rowid, at: at(seen.command.at) })],
    [t("channelfacts.lastError"), seen.error === null ? "none" : `${seen.error.message} (${at(seen.error.at)})`],
  ];
}

/**
 * What the facts say of the links in Dot's messages: where they open, or why there are none (`off`: `[channels] links`
 * is off, item 934; else `[server] reach` names no address a phone can open, item 611).
 * @param {string | null} links
 * @param {boolean} off
 * @returns {[string, string]}
 */
export function linkFact(links, off) {
  if (off) {
    return [t("channelfacts.linksInMessages"), t("channelfacts.noneChannelsLinksIsOff")];
  }
  return [
    t("channelfacts.linksInMessages"),
    links === null
      ? t("channelfacts.noneThisMacSOwn")
      : t("channelfacts.openAtLinks", { links }),
  ];
}

/**
 * What a press of Check now says, as the kind and the words of a toast: what was seen. `before` is the row of the last
 * command before the press (null for none), which tells whether the look took one.
 * @param {ImessageView} view
 * @param {number | null} before
 * @param {number} [now]
 * @param {string} [zone]
 * @returns {["ok" | "error", string]}
 */
export function lookWords(view, before, now = Date.now() / 1000, zone = undefined) {
  const { seen } = view;
  if (seen.error !== null && seen.error.at === seen.looked_at) {
    return ["error", t("channelfacts.dotCouldNotLookMessage", { message: seen.error.message })];
  }
  if (seen.command !== null && seen.command.rowid !== before) {
    return ["ok", t("channelfacts.dotTookRowRowidAs", { rowid: seen.command.rowid })];
  }
  let ending = t("channelfacts.nothingNewToRun");
  if (!view.on) {
    ending = t("channelfacts.imessageIsOffSoNo");
  } else if (view.handles.length === 0) {
    ending = t("channelfacts.noNumberIsPairedSo");
  }
  if (seen.newest === null) {
    return ["ok", t("channelfacts.dotLookedTheDatabaseHolds", { ending })];
  }
  const dated = seen.newest.dated === null ? "" : t("channelfacts.datedMoment", { moment: moment(seen.newest.dated, now, zone) });
  return ["ok", t("channelfacts.dotLookedTheNewestMessage", { rowid: seen.newest.rowid, dated, ending })];
}
