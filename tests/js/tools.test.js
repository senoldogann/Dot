// What a flow's summary says of a run's tool calls (tools.js): "Ran 2 commands and read 1 file".

import assert from "node:assert/strict";
import { test } from "node:test";

import { callKind, countKinds, describeArguments, flowWords, toolWords } from "../../src/dotagent/server/static/js/tools.js";

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

test("a tool's row names it in words: Dot's in its own, an MCP tool by its server and what it does", () => {
  assert.equal(toolWords("edit_file"), "Edit file");
  assert.equal(toolWords("update_plan"), "Update plan");
  assert.equal(toolWords("web_search"), "Web search");
  assert.equal(toolWords("skill"), "Use skill");
  // The server's name is taken off the tool's own, and the server is named as a title.
  assert.equal(toolWords("mcp__tavily__tavily_search"), "Tavily · Search");
  assert.equal(toolWords("mcp__linear__create_issue"), "Linear · Create issue");
  assert.equal(toolWords("mcp__microsoft-learn__microsoft_docs_search"), "Microsoft Learn · Microsoft docs search");
  // A tool named for its server alone keeps its name.
  assert.equal(toolWords("mcp__tavily__tavily"), "Tavily · Tavily");
  // Any other tool reads in words of its name, and a name that is a property of every object is no tool's words.
  assert.equal(toolWords("draft_reply"), "Draft reply");
  assert.equal(toolWords("constructor"), "Constructor");
});
