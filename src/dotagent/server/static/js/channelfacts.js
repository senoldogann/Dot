// What Settings › Channels says of what iMessage sees of Messages' database (item 595), in words and units: sizes as
// Finder writes them, moments as the page reads clock times, the rows of facts in the order the tab shows them, and the
// line a press of Check now says. Pure, so that it runs under Node without a page.

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
 * @typedef {{ on: boolean, handles: string[], seen: Seen, links: string | null }} ImessageView
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
  const file = (found, missing) => (!looked ? "not looked at yet" : found === null ? missing : `${byteSize(found.size)}, changed ${at(found.changed_at)}`);
  let newest = "Dot has not looked yet.";
  if (looked) {
    newest = "none: the database holds no message.";
    if (seen.newest !== null) {
      const dated = seen.newest.dated === null ? "with no date" : `dated ${at(seen.newest.dated)}`;
      newest = `row ${seen.newest.rowid}, ${dated}, first seen ${at(seen.newest.seen_at)}`;
    }
  }
  /** @type {[string, string][]} */
  const moved = seen.cursor_reset
    ? [
        [
          "Moved down",
          `from row ${seen.cursor_reset.from_row} to row ${seen.cursor_reset.to_row} at ${at(seen.cursor_reset.at)}: the newest message was below where Dot had read, so Messages' database was rebuilt or restored. What it holds was not run.`,
        ],
      ]
    : [];
  return [
    ["Newest message", newest],
    ["Read up to", seen.cursor === null ? "not set yet" : `row ${seen.cursor}`],
    ...moved,
    ["Last looked", looked ? at(seen.looked_at) : "never"],
    ["chat.db", file(seen.chat_db, "not found")],
    ["chat.db-wal", file(seen.chat_db_wal, "none: nothing waits in it")],
    ["Last command", seen.command === null ? "none yet" : `row ${seen.command.rowid}, taken ${at(seen.command.at)}`],
    ["Last error", seen.error === null ? "none" : `${seen.error.message} (${at(seen.error.at)})`],
  ];
}

/**
 * What the links in Dot's messages open at (item 611), as a row of the facts: the name a phone reaches Dot by, or, with
 * none, why no link is sent and where the lines send the owner instead.
 * @param {string | null} links
 * @returns {[string, string]}
 */
export function linkFact(links) {
  return [
    "Links in messages",
    links === null
      ? "none: this Mac's own address opens nothing on a phone, and [server] reach names no other, so Dot sends no link and says to open Dot on your Mac (Settings › Phone sets a name)."
      : `open at ${links}`,
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
    return ["error", `Dot could not look: ${seen.error.message}`];
  }
  if (seen.command !== null && seen.command.rowid !== before) {
    return ["ok", `Dot took row ${seen.command.rowid} as a command.`];
  }
  let ending = "Nothing new to run.";
  if (!view.on) {
    ending = "iMessage is off, so no message is read.";
  } else if (view.handles.length === 0) {
    ending = "No number is paired, so no message is read.";
  }
  if (seen.newest === null) {
    return ["ok", `Dot looked: the database holds no message. ${ending}`];
  }
  const dated = seen.newest.dated === null ? "" : `, dated ${moment(seen.newest.dated, now, zone)}`;
  return ["ok", `Dot looked: the newest message is row ${seen.newest.rowid}${dated}. ${ending}`];
}
