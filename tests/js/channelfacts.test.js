// What Settings › Channels says of what iMessage sees of Messages' database (item 595), read by channelfacts.js: sizes,
// moments, the rows of facts and the line Check now says. Pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { byteSize, linkFact, lookWords, moment, seenFacts } from "../../src/dotagent/server/static/js/channelfacts.js";

// 2026-10-23 14:02:11 UTC, and the zone every moment is told in so that the test does not depend on the machine's.
const NOW = Date.UTC(2026, 9, 23, 14, 5, 0) / 1000;
const AT = (hour, minute, second, day = 23) => Date.UTC(2026, 9, day, hour, minute, second) / 1000;
const ZONE = "UTC";

/** What a channel that has not looked sees: nothing. */
const UNSEEN = {
  looked_at: null,
  cursor: null,
  newest: null,
  chat_db: null,
  chat_db_wal: null,
  command: null,
  error: null,
  cursor_reset: null,
  warning: null,
};

/** What a channel that has looked and read up to row 312 sees. */
const LOOKED = {
  looked_at: AT(14, 4, 57),
  cursor: 312,
  newest: { rowid: 312, dated: AT(14, 2, 11), seen_at: AT(14, 2, 13) },
  chat_db: { size: 412_300_000, changed_at: AT(14, 0, 3) },
  chat_db_wal: { size: 4_096_000, changed_at: AT(14, 2, 12) },
  command: { rowid: 310, at: AT(13, 58, 40) },
  error: null,
  cursor_reset: null,
  warning: null,
};

test("a size is written as Finder writes it, in powers of a thousand", () => {
  assert.equal(byteSize(0), "0 B");
  assert.equal(byteSize(512), "512 B");
  assert.equal(byteSize(999), "999 B");
  assert.equal(byteSize(1000), "1.0 KB");
  assert.equal(byteSize(4096), "4.1 KB");
  assert.equal(byteSize(32_768), "32.8 KB");
  assert.equal(byteSize(412_300_000), "412 MB");
  assert.equal(byteSize(1_234_000_000), "1.2 GB");
});

test("a moment is its time today and its day and time on another day", () => {
  assert.equal(moment(AT(14, 2, 11), NOW, ZONE), "14:02:11");
  assert.equal(moment(AT(0, 0, 5), NOW, ZONE), "00:00:05");
  assert.equal(moment(AT(23, 10, 9, 22), NOW, ZONE), "22 Oct, 23:10:09");
  assert.equal(moment(AT(8, 0, 0, 1), NOW, ZONE), "1 Oct, 08:00:00");
});

test("before the first look the facts say Dot has not looked", () => {
  assert.deepEqual(seenFacts(UNSEEN, NOW, ZONE), [
    ["Newest message", "Dot has not looked yet."],
    ["Read up to", "not set yet"],
    ["Last looked", "never"],
    ["chat.db", "not looked at yet"],
    ["chat.db-wal", "not looked at yet"],
    ["Last command", "none yet"],
    ["Last error", "none"],
  ]);
});

test("a look leaves the newest message, the cursor, the files, the last command and the last error as facts", () => {
  const error = { message: "the Messages database could not be read", at: AT(14, 3, 0) };
  assert.deepEqual(seenFacts({ ...LOOKED, error }, NOW, ZONE), [
    ["Newest message", "row 312, dated 14:02:11, first seen 14:02:13"],
    ["Read up to", "row 312"],
    ["Last looked", "14:04:57"],
    ["chat.db", "412 MB, changed 14:00:03"],
    ["chat.db-wal", "4.1 MB, changed 14:02:12"],
    ["Last command", "row 310, taken 13:58:40"],
    ["Last error", "the Messages database could not be read (14:03:00)"],
  ]);
});

test("a database with no message, a row with no date and files that are not there are said as they are", () => {
  const empty = { ...UNSEEN, looked_at: AT(14, 4, 57), cursor: 0, chat_db: LOOKED.chat_db };
  const facts = Object.fromEntries(seenFacts(empty, NOW, ZONE));
  assert.equal(facts["Newest message"], "none: the database holds no message.");
  assert.equal(facts["chat.db-wal"], "none: nothing waits in it");
  const dateless = { ...LOOKED, newest: { rowid: 9, dated: null, seen_at: AT(14, 2, 13) } };
  assert.equal(Object.fromEntries(seenFacts(dateless, NOW, ZONE))["Newest message"], "row 9, with no date, first seen 14:02:13");
  const gone = { ...LOOKED, chat_db: null };
  assert.equal(Object.fromEntries(seenFacts(gone, NOW, ZONE))["chat.db"], "not found");
});

test("what Check now says is the command it took, the error it met, or the newest message it saw", () => {
  const on = { on: true, handles: ["+358401234567"], seen: LOOKED };
  // A command taken by this look: its row differs from the last one before the press.
  assert.deepEqual(lookWords({ ...on, seen: { ...LOOKED, command: { rowid: 313, at: AT(14, 5, 0) } } }, 310, NOW, ZONE), [
    "ok",
    "Dot took row 313 as a command.",
  ]);
  assert.deepEqual(lookWords(on, 310, NOW, ZONE), [
    "ok",
    "Dot looked: the newest message is row 312, dated 14:02:11. Nothing new to run.",
  ]);
  const failed = { ...LOOKED, error: { message: "macOS keeps it from Dot", at: LOOKED.looked_at } };
  assert.deepEqual(lookWords({ ...on, seen: failed }, 310, NOW, ZONE), ["error", "Dot could not look: macOS keeps it from Dot"]);
  // An error from an earlier moment is not this look's.
  const earlier = { ...LOOKED, error: { message: "an old one", at: LOOKED.looked_at - 60 } };
  assert.equal(lookWords({ ...on, seen: earlier }, 310, NOW, ZONE)[0], "ok");
});

test("a look with the switch off or nothing paired says that no message is read", () => {
  assert.equal(
    lookWords({ on: false, handles: ["+358401234567"], seen: LOOKED }, 310, NOW, ZONE)[1],
    "Dot looked: the newest message is row 312, dated 14:02:11. iMessage is off, so no message is read.",
  );
  assert.equal(
    lookWords({ on: true, handles: [], seen: LOOKED }, 310, NOW, ZONE)[1],
    "Dot looked: the newest message is row 312, dated 14:02:11. No number is paired, so no message is read.",
  );
  const empty = { ...UNSEEN, looked_at: AT(14, 4, 57), cursor: 0 };
  assert.equal(
    lookWords({ on: true, handles: ["+358401234567"], seen: empty }, null, NOW, ZONE)[1],
    "Dot looked: the database holds no message. Nothing new to run.",
  );
});

test("a cursor that was moved down to the newest row is a fact beside the one it changed, and only once it was", () => {
  const moved = { ...LOOKED, cursor: 4, newest: { rowid: 4, dated: AT(14, 2, 11), seen_at: AT(14, 2, 13) }, cursor_reset: { from_row: 300, to_row: 4, at: AT(14, 2, 12) } };
  const facts = seenFacts(moved, NOW, ZONE);
  assert.deepEqual(
    facts.map(([label]) => label),
    ["Newest message", "Read up to", "Moved down", "Last looked", "chat.db", "chat.db-wal", "Last command", "Last error"],
  );
  assert.equal(
    Object.fromEntries(facts)["Moved down"],
    "from row 300 to row 4 at 14:02:12: the newest message was below where Dot had read, so Messages' database was rebuilt or restored. What it holds was not run.",
  );
  // A channel that never had to is told by seven facts, as it always was.
  assert.equal(seenFacts(LOOKED, NOW, ZONE).length, 7);
});

test("the links in Dot's messages are a fact: the name they open at, or why there are none", () => {
  assert.deepEqual(linkFact("https://mac.example.ts.net/"), ["Links in messages", "open at https://mac.example.ts.net/"]);
  const [label, none] = linkFact(null);
  assert.equal(label, "Links in messages");
  assert.match(none, /^none: /);
  assert.match(none, /\[server\] reach/);
  assert.match(none, /open Dot on your Mac/);
});
