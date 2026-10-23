// What a run changed, in the words of its card under the answer (item 597): pure functions, so they run under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { changedWords, shownEdits, undoQuestion } from "../../src/dotagent/server/static/js/runchanges.js";

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
  assert.equal(undoQuestion([restored, created], [restored, created]), "A file goes back as it was before the run, and the file it created is deleted.");
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
