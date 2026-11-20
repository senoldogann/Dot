// How a chat shows a message and the events around it: a link is a pill that opens in the browser, and a
// compaction or other internal event stays out of the conversation until the details view is on.

import assert from "node:assert/strict";
import test from "node:test";

import { compactionNotice, visibleInConversation } from "../../src/dotagent/server/static/js/chatnoise.js";
import { pillsInMessage } from "../../src/dotagent/server/static/js/linkpill.js";

const MESSAGE = "See [the notes](https://example.com/docs) and https://github.com/acme/app/pull/1001 for the change.";

test("a markdown link and a bare GitHub pull request render as pills that open externally", () => {
  const pills = pillsInMessage(MESSAGE);
  assert.equal(pills.length, 2);
  const [notes, pull] = pills;
  assert.equal(notes.label, "the notes");
  assert.equal(notes.href, "https://example.com/docs");
  assert.equal(notes.host, "example.com");
  assert.equal(notes.icon, "favicon");
  assert.equal(notes.favicon, "/api/favicons/example.com");
  assert.equal(notes.target, "_blank");
  assert.match(notes.rel, /noopener/);
  assert.equal(notes.external, true);
  assert.equal(pull.label, "#1001");
  assert.equal(pull.href, "https://github.com/acme/app/pull/1001");
  assert.equal(pull.host, "github.com");
  assert.equal(pull.icon, "favicon");
  assert.equal(pull.favicon, "/api/favicons/github.com");
  assert.equal(pull.external, true);
  assert.equal(pull.target, "_blank");
  assert.equal(pull.favicon.includes("google.com"), false);
  assert.equal(pull.favicon.includes("duckduckgo"), false);
});

test("a bare GitHub issue is a pill of its number, and a named link keeps its words", () => {
  const [issue, named] = pillsInMessage(
    "https://github.com/acme/app/issues/42 and [the pull request](https://github.com/acme/app/pull/1001).",
  );
  assert.equal(issue.label, "#42");
  assert.equal(issue.href, "https://github.com/acme/app/issues/42");
  assert.equal(named.label, "the pull request");
  assert.equal(named.href, "https://github.com/acme/app/pull/1001");
});

test("a compaction or system event stays out of the conversation until details are on", () => {
  const compacted = {
    kind: "compacted",
    data: { tokens_before: 120000, tokens_after: 18000, summary: "The run read the docs." },
  };
  const system = { kind: "message", role: "system", text: "You are the internal planner." };
  const wake = { kind: "wake", hidden: true, text: "Look at the inbox again." };
  const plumbing = { kind: "tool_call", plumbing: true, tool: "shell" };
  for (const event of [compacted, system, wake, plumbing]) {
    assert.equal(visibleInConversation(event), false);
    assert.equal(visibleInConversation(event, { details: true }), true);
  }
  assert.equal(compactionNotice(), "Earlier messages were condensed");
  assert.equal(visibleInConversation({ kind: "assistant", role: "assistant", text: "Here is the answer." }), true);
});
