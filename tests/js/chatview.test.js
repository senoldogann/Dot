// How a chat shows a message and the events around it: a link is plain text in a link's colour that opens in the browser, and a
// compaction or other internal event stays out of the conversation until the details view is on.

import assert from "node:assert/strict";
import test from "node:test";

import { visibleInConversation } from "../../src/dotagent/server/static/js/chatnoise.js";
import { pillsInMessage } from "../../src/dotagent/server/static/js/linkpill.js";

const MESSAGE = "See [the notes](https://example.com/docs) and https://github.com/acme/app/pull/1001 for the change.";

test("a markdown link and a bare GitHub pull request render as links that open externally", () => {
  const pills = pillsInMessage(MESSAGE);
  assert.equal(pills.length, 2);
  const [notes, pull] = pills;
  assert.equal(notes.label, "the notes");
  assert.equal(notes.href, "https://example.com/docs");
  assert.equal(notes.host, "example.com");
  assert.equal("icon" in notes, false);
  assert.equal("favicon" in notes, false);
  assert.equal(notes.target, "_blank");
  assert.match(notes.rel, /noopener/);
  assert.equal(notes.external, true);
  assert.equal(pull.label, "#1001");
  assert.equal(pull.href, "https://github.com/acme/app/pull/1001");
  assert.equal(pull.host, "github.com");
  assert.equal(pull.external, true);
  assert.equal(pull.target, "_blank");
});

test("a bare GitHub issue is a link of its number, and a named link keeps its words", () => {
  const [issue, named] = pillsInMessage(
    "https://github.com/acme/app/issues/42 and [the pull request](https://github.com/acme/app/pull/1001).",
  );
  assert.equal(issue.label, "#42");
  assert.equal(issue.href, "https://github.com/acme/app/issues/42");
  assert.equal(named.label, "the pull request");
  assert.equal(named.href, "https://github.com/acme/app/pull/1001");
});

test("a compaction stays out of the conversation until details are on, and the run's other events stay in it", () => {
  // As the store records one (store/models.py EventRecord, agent/loop.py's compacted data).
  const compacted = {
    id: 7,
    run_id: "run_a",
    ts: 1,
    kind: "compacted",
    data: { summarised_messages: 6, compaction: 1, tokens_before: 120000, tokens_after: 18000, method: "model" },
  };
  assert.equal(visibleInConversation(compacted), false);
  assert.equal(visibleInConversation(compacted, { details: true }), true);
  for (const kind of ["assistant", "tool_call", "tool_result", "user_message"]) {
    assert.equal(visibleInConversation({ id: 8, run_id: "run_a", ts: 2, kind, data: {} }), true);
  }
});
