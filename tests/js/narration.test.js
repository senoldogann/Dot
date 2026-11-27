// The run told as a conversation (item 1976): the words the live line says for a call (workstatus.js callPhrase), and the
// time it says beside them (workedFor).

import assert from "node:assert/strict";
import { test } from "node:test";

import { callPhrase, workedFor } from "../../src/dotagent/server/static/js/workstatus.js";

test("a call is said in plain words, never by a tool's name (owner 2026-11-27)", () => {
  assert.equal(callPhrase("web_search", { query: "gistsmith contact" }), "Searching the web for “gistsmith contact”");
  assert.equal(callPhrase("read_file", { path: "/Users/x/README.md" }), "Reading README.md");
  assert.equal(callPhrase("shell", {}), "Running a command");
  assert.equal(callPhrase("code_run", { code: "print(1)" }), "Working");
  assert.equal(callPhrase("list_dir", { path: "." }), "Working");
});

test("work handed to a helper is said in plain words, never delegate or subagent (item 2002)", () => {
  // The live line says no tool's name: a call with no phrase of its own is "Working" (owner 2026-11-27).
  for (const args of [{ goal: "Check the dentist's free slots" }, {}]) {
    assert.doesNotMatch(callPhrase("delegate", args), /delegat|subagent|child/i);
  }
});

test("the time is in seconds under a minute and in whole minutes after", () => {
  assert.equal(workedFor(0), "0 s");
  assert.equal(workedFor(59.9), "59 s");
  assert.equal(workedFor(61), "1 min");
  assert.equal(workedFor(3 * 60 + 59), "3 min");
});
