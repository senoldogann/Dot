// What a run changed, in the words of its card under the answer (item 597): pure functions, so they run under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { accountWords, changedWords, outsideWords, shownEdits, undoQuestion } from "../../src/dotagent/server/static/js/runchanges.js";

/** A file of the run's undo plan (`GET /runs/{id}/actions`): what taking it back does, and whether it can. */
function edit(path, action, state = "ready", tool = action === "delete" ? "write_file" : "edit_file") {
  return { path, action, state, tool, reason: null, run_id: "run_1", noted_at: 1, changes: 1 };
}

test("the card shows the owner's files, not the run's notes nor a file as it was before", () => {
  const edits = [
    edit("/Users/a/site/index.html", "delete"),
    edit("/Users/a/notes.md", "restore", "ready", "write_notes"),
    edit("/Users/a/plan.md", "restore", "as_before"),
    edit("/Users/a/site/style.css", "restore", "conflict"),
  ];
  assert.deepEqual(
    shownEdits(edits).map((one) => one.path),
    ["/Users/a/site/index.html", "/Users/a/site/style.css"],
  );
});

test("what the run did is said in a few words: created, changed, or both", () => {
  assert.equal(changedWords([edit("a", "delete")]), "Created a file");
  assert.equal(changedWords([edit("a", "restore"), edit("b", "restore"), edit("c", "restore")]), "Changed 3 files");
  assert.equal(changedWords([edit("a", "delete"), edit("b", "delete"), edit("c", "restore")]), "Created 2 files and changed 1");
});

test("Undo asks what goes back, what is deleted and what stays as it is", () => {
  const restored = edit("plan.md", "restore");
  const created = edit("index.html", "delete");
  const since = edit("style.css", "restore", "conflict");
  assert.equal(undoQuestion([restored, created], [restored, created]), "A file goes back as it was before the task, and the file it created is deleted.");
  assert.equal(
    undoQuestion([created, edit("b.html", "delete")], [created, edit("b.html", "delete"), since]),
    "The 2 files it created are deleted. A file changed since stays as it is.",
  );
  // One that cannot go back for another reason is not said to have changed since.
  assert.equal(
    undoQuestion([created], [created, edit("big.bin", "restore", "cannot")]),
    "The file it created is deleted. A file that cannot go back stays as it is.",
  );
});

test("the steps no undo can take back are counted by what ran, in one sentence that claims nothing of what they did (item 1404)", () => {
  assert.equal(outsideWords([]), "");
  assert.equal(outsideWords([{ category: "shell", steps: 0 }]), "");
  assert.equal(outsideWords([{ category: "shell", steps: 1 }]), "Dot also ran 1 command. Undo takes back files only, so it cannot take this back.");
  assert.equal(
    outsideWords([
      { category: "shell", steps: 3 },
      { category: "browser", steps: 2 },
    ]),
    "Dot also ran 3 commands and 2 browser actions. Undo takes back files only, so it cannot take these back.",
  );
  assert.equal(
    outsideWords([
      { category: "shell", steps: 1 },
      { category: "browser", steps: 1 },
      { category: "desktop", steps: 1 },
      { category: "message", steps: 2 },
      { category: "app", steps: 1 },
    ]),
    "Dot also ran 1 command, 1 browser action, 1 desktop action, 2 message steps and 1 connected app action. Undo takes back files only, so it cannot take these back.",
  );
  // A category this page does not know is left out, never drawn as a word of its own.
  assert.equal(outsideWords([{ category: "teleport", steps: 4 }]), "");
});

/** A run's account (`account` of `GET /runs/{id}/actions`, item 1746) with nothing in it but what is given. */
function account(over) {
  return {
    calls: 0,
    failed_calls: 0,
    changed: [],
    changed_count: 0,
    commands: 0,
    sent: [],
    sent_count: 0,
    acts: { verified: 0, contradicted: 0, uncertain: 0, not_checked: 0, failed: 0 },
    plan_done: 0,
    unshown: [],
    open_steps: [],
    ...over,
  };
}

test("the account says what was seen to hold, what was sent and what is left, from the record (item 1746)", () => {
  assert.deepEqual(accountWords(account({})), []);
  assert.deepEqual(accountWords(account({ acts: { verified: 4, contradicted: 0, uncertain: 1, not_checked: 2, failed: 1 } })), [
    "Checked on the page or the screen: 4 seen to hold, 3 not (2 not checked, 1 uncertain). 1 step failed.",
  ]);
  assert.deepEqual(accountWords(account({ acts: { verified: 2, contradicted: 0, uncertain: 0, not_checked: 0, failed: 0 } })), [
    "Checked on the page or the screen: 2 seen to hold.",
  ]);
  assert.deepEqual(
    accountWords(account({ sent: ["Send a mail to a@b.c"], sent_count: 1, unshown: ["2. Book the table"], open_steps: ["3. Tell Ada"] })),
    ["Sent 1 thing: Send a mail to a@b.c.", "Marked done with no tool result that shows it: 2. Book the table.", "Left open: 3. Tell Ada."],
  );
});
