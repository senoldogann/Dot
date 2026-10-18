// What a flow's summary says of a run's tool calls (tools.js): "Ran 2 commands and read 1 file".

import assert from "node:assert/strict";
import { test } from "node:test";

import { callKind, countKinds, describeArguments, flowWords } from "../../src/dotagent/server/static/js/tools.js";

test("a tool's kind: by name, by prefix, else other", () => {
  assert.equal(callKind("shell"), "command");
  assert.equal(callKind("terminal_send"), "command");
  assert.equal(callKind("read_file"), "read");
  assert.equal(callKind("edit_file"), "write");
  assert.equal(callKind("web_fetch"), "page");
  assert.equal(callKind("browser_click"), "browser");
  assert.equal(callKind("desktop_type"), "desktop");
  assert.equal(callKind("memory_save"), "memory");
  assert.equal(callKind("something_new"), "other");
  // A name that is a property of every object is no tool.
  assert.equal(callKind("constructor"), "other");
});

test("the counts of a group of calls", () => {
  assert.deepEqual(countKinds(["shell", "shell", "read_file", "browser_click"]), { command: 2, read: 1, browser: 1 });
  assert.deepEqual(countKinds([]), {});
});

test("the words of the counts, in one order whatever the order of the calls", () => {
  assert.equal(flowWords({ read: 1, command: 2 }), "Ran 2 commands and read 1 file");
  assert.equal(flowWords({ read: 1 }), "Read 1 file");
  assert.equal(flowWords({ search: 1, other: 2, page: 3 }), "Searched the web once, opened 3 pages and used 2 tools");
  assert.equal(flowWords({ desktop: 2 }), "Used the desktop 2 times");
  assert.equal(flowWords({}), "");
  assert.equal(flowWords({ command: 0 }), "");
});

test("the line for a call's arguments comes from its most telling one", () => {
  assert.equal(describeArguments({ command: "ls -la" }), "ls -la");
  assert.equal(describeArguments({ action: "type", text: "hello" }), "type hello");
  assert.equal(describeArguments({ action: "click" }), "click");
  assert.equal(describeArguments({ count: 3 }), "");
  assert.equal(describeArguments(null), "");
  assert.equal(describeArguments("words"), "");
  assert.equal(describeArguments({ url: `https://example.com/${"a".repeat(300)}` }).length, 200);
});
