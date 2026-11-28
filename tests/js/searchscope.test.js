// The chips of the chat search (searchscope.js): which finds each chip shows, All's "See all", the word a message find
// opens its chat at, and the bots a search finds. The same rules as the phone's (DotEngine FaceAndSearchTests).

import assert from "node:assert/strict";
import { test } from "node:test";

import { ALL_SHOWN, botsMatching, cutShort, markedWord, SEARCH_SCOPES, scopedFinds, snippetPiece } from "../../src/dotagent/server/static/js/searchscope.js";

const finds = { bots: ["b1"], messages: ["m1", "m2", "m3", "m4"], files: ["f1", "f2"] };

test("All shows a few of each kind and each chip only its own", () => {
  assert.deepEqual(SEARCH_SCOPES, ["all", "messages", "bots", "files"]);
  assert.deepEqual(scopedFinds(finds, "all"), { bots: ["b1"], messages: finds.messages.slice(0, ALL_SHOWN), files: ["f1", "f2"] });
  assert.deepEqual(scopedFinds(finds, "messages"), { bots: [], messages: finds.messages, files: [] });
  assert.deepEqual(scopedFinds(finds, "bots"), { bots: ["b1"], messages: [], files: [] });
  assert.deepEqual(scopedFinds(finds, "files"), { bots: [], messages: [], files: ["f1", "f2"] });
  assert.deepEqual(cutShort(finds), new Set(["messages"]));
});

test("a message find opens its chat at its first marked word, where its longest piece is", () => {
  assert.equal(markedWord("we booked the [Lastikçi] garage"), "Lastikçi");
  assert.equal(markedWord("...[tax] and [form]"), "tax");
  assert.equal(markedWord("no mark"), null);
  assert.equal(markedWord("an empty [] mark"), null);
  assert.equal(snippetPiece("...the [tax] form is due...ok"), "the tax form is due");
});

test("a bot is found by its name or role, every word, folded", () => {
  const agents = [
    { record: { name: "Vergi Uzmanı", role: "Files the taxes" } },
    { record: { name: "Writer", role: "Drafts posts" } },
  ];
  assert.deepEqual(
    botsMatching(agents, "VERGI uzmani").map((agent) => agent.record.name),
    ["Vergi Uzmanı"],
  );
  assert.deepEqual(
    botsMatching(agents, "drafts").map((agent) => agent.record.name),
    ["Writer"],
  );
  assert.deepEqual(botsMatching(agents, "drafts taxes"), []);
  assert.deepEqual(botsMatching(agents, "  "), []);
});
