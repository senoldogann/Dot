// The run told as a conversation (item 1976): the words of a line a call told (workstatus.js narrationPhrase), and the
// time the live line says beside its words (workedFor).

import assert from "node:assert/strict";
import { test } from "node:test";

import { narrationPhrase, workedFor } from "../../src/dotagent/server/static/js/workstatus.js";

test("a page or a site is named by its site, any other call as the live line says it", () => {
  assert.equal(narrationPhrase("browser_open", { url: "https://www.gistsmith.com" }), "Looking at gistsmith.com");
  assert.equal(narrationPhrase("web_fetch", { url: "https://example.com" }), "Looking at example.com");
  assert.equal(narrationPhrase("web_search", { query: "gistsmith contact" }), "Searching the web for “gistsmith contact”");
  assert.equal(narrationPhrase("read_file", { path: "README.md" }), "Reading README.md");
  assert.equal(narrationPhrase("shell", {}), "Running a command");
});

test("work handed to a helper is said in plain words with its title, never delegate or subagent (item 2002)", () => {
  assert.equal(narrationPhrase("delegate", { goal: "Check the dentist's free slots" }), "Handed to a helper: Check the dentist's free slots");
  assert.equal(narrationPhrase("delegate", {}), "Handed to a helper");
});

test("the time is in seconds under a minute and in whole minutes after", () => {
  assert.equal(workedFor(0), "0 s");
  assert.equal(workedFor(59.9), "59 s");
  assert.equal(workedFor(61), "1 min");
  assert.equal(workedFor(3 * 60 + 59), "3 min");
});
