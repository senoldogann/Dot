// The versions of a learned skill, compared and said in words (item 585).

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  countChanges,
  describeVersion,
  markOf,
  proposalEffects,
  proposalTitle,
  stepChanges,
  stepsSummary,
} from "../../src/dotagent/server/static/js/skilldiff.js";

test("the steps of a version against the version before it read as kept, dropped and added", () => {
  const before = ["Open the inbox.", "Send the first mail.", "Close it."];
  const after = ["Open the inbox.", "Send the mails.", "Count the replies.", "Close it."];
  assert.deepEqual(stepChanges(before, after), [
    { sign: " ", text: "Open the inbox." },
    { sign: "-", text: "Send the first mail." },
    { sign: "+", text: "Send the mails." },
    { sign: "+", text: "Count the replies." },
    { sign: " ", text: "Close it." },
  ]);
});

test("a version with no step of the other's is all dropped and all added, and two empty ones differ in nothing", () => {
  assert.deepEqual(stepChanges(["a", "b"], []), [
    { sign: "-", text: "a" },
    { sign: "-", text: "b" },
  ]);
  assert.deepEqual(stepChanges([], ["a"]), [{ sign: "+", text: "a" }]);
  assert.deepEqual(stepChanges([], []), []);
  assert.deepEqual(stepChanges(["a", "b"], ["a", "b"]), [
    { sign: " ", text: "a" },
    { sign: " ", text: "b" },
  ]);
});

test("a step that moved is dropped where it was and added where it is, and a repeated step is matched once each", () => {
  assert.deepEqual(stepChanges(["a", "b", "c"], ["b", "c", "a"]), [
    { sign: "-", text: "a" },
    { sign: " ", text: "b" },
    { sign: " ", text: "c" },
    { sign: "+", text: "a" },
  ]);
  assert.deepEqual(countChanges(stepChanges(["x", "x"], ["x"])), { kept: 1, added: 0, dropped: 1 });
});

test("the changes are counted", () => {
  const changes = stepChanges(["a", "b", "c"], ["a", "d", "e", "c"]);
  assert.deepEqual(countChanges(changes), { kept: 2, added: 2, dropped: 1 });
  assert.deepEqual(countChanges([]), { kept: 0, added: 0, dropped: 0 });
});

test("how a version came to be is said by its change, with what it took in or the words it was made at", () => {
  const note = (change, more = {}) => ({ change, from_version: null, merged: [], words: null, ...more });
  assert.equal(describeVersion(note("learned")), "Learned from a run");
  assert.equal(describeVersion(note("relearned")), "Rewritten by a later run");
  assert.equal(describeVersion(note("taught")), "Taught by you");
  assert.equal(describeVersion(note("restored", { from_version: 2 })), "Restored from version 2");
  assert.equal(describeVersion(note("merged", { merged: ["learned-a", "learned-b"] })), "Merged with learned-a, learned-b");
  assert.equal(describeVersion(note("merged")), "Merged with another skill");
  assert.equal(describeVersion(note("revised", { words: "Open the inbox first." })), "Revised at your word: Open the inbox first.");
  assert.equal(describeVersion(note("revised")), "Revised at your word");
  assert.equal(describeVersion(note("something else")), "something else");
});

test("a version's mark says where its steps come from, and nothing for steps offered as written", () => {
  assert.equal(markOf(false), null);
  assert.equal(markOf(true), "From outside content");
  assert.equal(markOf(null), "Origin unknown");
});

test("a proposal says what it does to each skill: a merge puts the others to rest, kept whole", () => {
  const merge = { kind: "merge", names: ["learned-a", "learned-b"] };
  assert.equal(proposalTitle(merge), "Merge learned-a and learned-b");
  assert.deepEqual(proposalEffects(merge), [
    "learned-a gets all of the above as a new version; the file it has now is kept as the version before.",
    "learned-b rests, kept whole: wake it to offer it again, or restore learned-a's earlier version to undo the merge.",
  ]);
  const rewrite = { kind: "rewrite", names: ["learned-a"] };
  assert.equal(proposalTitle(rewrite), "Change learned-a");
  assert.deepEqual(proposalEffects(rewrite), [
    "learned-a gets all of the above as a new version; the file it has now is kept as the version before.",
  ]);
});

test("the steps a proposal writes are counted from the steps field alone, and the other fields do not count", () => {
  const fields = [
    { field: "name", lines: [{ sign: " ", text: "learned-a" }] },
    { field: "description", lines: [{ sign: "-", text: "old" }, { sign: "+", text: "new" }] },
    {
      field: "steps",
      lines: [
        { sign: " ", text: "Open the tracker." },
        { sign: "-", text: "Write it." },
        { sign: "+", text: "Write and send it." },
        { sign: "+", text: "Count the replies." },
      ],
    },
    { field: "parameters", lines: [{ sign: "+", text: "<week>: the week" }] },
  ];
  assert.equal(stepsSummary(fields, "learned-a"), "3 steps: 2 new, 1 dropped, 1 kept from learned-a.");
  assert.equal(
    stepsSummary([{ field: "steps", lines: [{ sign: "+", text: "Only." }] }], "learned-a"),
    "1 step: 1 new, 0 dropped, 0 kept from learned-a.",
  );
  assert.equal(stepsSummary([], "learned-a"), "0 steps: 0 new, 0 dropped, 0 kept from learned-a.");
});
