// What the run's Context sections say of a tool call and of a memory change (contextpanel.js): the pages and searches
// a run read, and the notes it saved, each once and newest first.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addMemory,
  addReadings,
  addUsage,
  languageOf,
  readingsOf,
  usageOf,
} from "../../src/dotagent/server/static/js/contextpanel.js";

test("a page a tool opened or read, as a line and the address it points at", () => {
  assert.deepEqual(readingsOf("web_fetch", { url: "https://docs.python.org/3/library/asyncio.html?x=1#top" }), [
    {
      kind: "page",
      text: "docs.python.org/3/library/asyncio.html",
      url: "https://docs.python.org/3/library/asyncio.html?x=1#top",
    },
  ]);
  // An MCP tool reads by its own name, after the server's.
  assert.deepEqual(readingsOf("mcp__web-research__web_fetch", { url: "https://www.example.com/" }), [
    { kind: "page", text: "example.com/", url: "https://www.example.com/" },
  ]);
  assert.deepEqual(readingsOf("browser_open", { url: "http://example.com/guide" }), [
    { kind: "page", text: "example.com/guide", url: "http://example.com/guide" },
  ]);
  // A bare host gets https://, as the browser's tools give it; a loopback one http://.
  assert.deepEqual(readingsOf("browser_open", { url: "example.com/guide" }), [
    { kind: "page", text: "example.com/guide", url: "https://example.com/guide" },
  ]);
  assert.deepEqual(readingsOf("web_fetch", { url: "localhost:8080/notes" }), [
    { kind: "page", text: "localhost:8080/notes", url: "http://localhost:8080/notes" },
  ]);
  // A crawl reads its root; each page of it is the same site.
  assert.deepEqual(readingsOf("web_crawl", { url: "https://example.com/docs/" }), [
    { kind: "page", text: "example.com/docs/", url: "https://example.com/docs/" },
  ]);
});

test("a search is its query, and an extract is each page it read", () => {
  assert.deepEqual(readingsOf("web_search", { query: "  rust tokio select  " }), [{ kind: "search", text: "rust tokio select" }]);
  assert.deepEqual(readingsOf("mcp__web-research__web_search", { query: "sqlite fts5" }), [{ kind: "search", text: "sqlite fts5" }]);
  assert.deepEqual(readingsOf("web_extract", { sources: ["https://example.com/a", "notes.md", "https://example.com/b"] }), [
    { kind: "page", text: "example.com/a", url: "https://example.com/a" },
    { kind: "page", text: "example.com/b", url: "https://example.com/b" },
  ]);
});

test("only a page the browser can open is a reading", () => {
  assert.deepEqual(readingsOf("web_fetch", { url: "javascript:alert(1)" }), []);
  assert.deepEqual(readingsOf("web_fetch", { url: "file:///etc/passwd" }), []);
  assert.deepEqual(readingsOf("web_fetch", { url: "not an address" }), []);
  assert.deepEqual(readingsOf("web_fetch", {}), []);
  assert.deepEqual(readingsOf("web_search", { query: "   " }), []);
  assert.deepEqual(readingsOf("shell", { command: "curl https://example.com" }), []);
  assert.deepEqual(readingsOf("read_file", { path: "/tmp/page.html" }), []);
});

test("the readings keep the newest first, each once, and never grow past their limit", () => {
  const first = readingsOf("web_search", { query: "one" });
  const second = readingsOf("web_fetch", { url: "https://example.com/a" });
  const again = readingsOf("web_search", { query: "one" });
  const list = addReadings(addReadings([], first), second);
  assert.deepEqual(addReadings(list, again), [
    { kind: "search", text: "one" },
    { kind: "page", text: "example.com/a", url: "https://example.com/a" },
  ]);
  const many = Array.from({ length: 12 }, (_, index) => ({ kind: "search", text: `query ${index}` }));
  assert.equal(addReadings([], many, 8).length, 8);
  assert.equal(addReadings([], many, 8)[0].text, "query 0");
});

test("a tool call says which skill or MCP server the run used, and nothing of the run's own tools", () => {
  assert.deepEqual(usageOf("skill", { name: "accessibility-review" }), { kind: "skill", name: "accessibility-review" });
  assert.deepEqual(usageOf("skill", { name: "  " }), null);
  assert.deepEqual(usageOf("skill", {}), null);
  // A skill read in parts counts when it begins, not once per part.
  assert.deepEqual(usageOf("skill", { name: "long-read", start: 12000 }), null);
  assert.deepEqual(usageOf("skill", { name: "long-read", start: 0 }), { kind: "skill", name: "long-read" });
  assert.deepEqual(usageOf("mcp__github__list_repos", {}), { kind: "server", name: "github" });
  assert.deepEqual(usageOf("mcp__web-research__web_fetch", {}), { kind: "server", name: "web-research" });
  // A tool of no server, and the run's own tools, use nothing.
  assert.deepEqual(usageOf("mcp__", {}), null);
  assert.deepEqual(usageOf("web_fetch", {}), null);
  assert.deepEqual(usageOf("shell", { command: "mcp__github__list_repos" }), null);
});

test("the used skills and servers are listed once, in the order they were first used, with their calls counted", () => {
  const skill = usageOf("skill", { name: "review" });
  const server = usageOf("mcp__github__list_repos", {});
  const list = addUsage(addUsage([], skill), server);
  assert.deepEqual(list, [
    { kind: "skill", name: "review", count: 1 },
    { kind: "server", name: "github", count: 1 },
  ]);
  assert.deepEqual(addUsage(list, usageOf("mcp__github__get_file", {})), [
    { kind: "skill", name: "review", count: 1 },
    { kind: "server", name: "github", count: 2 },
  ]);
  // A skill and a server of the same name are two rows.
  assert.equal(addUsage(list, { kind: "server", name: "review" }).length, 3);
});

test("a file's own name or extension names its language's mark, and only a kind with no mark falls back", () => {
  assert.equal(languageOf("/tmp/probe_office2.py"), "python");
  assert.equal(languageOf("src/app.TSX"), "typescript");
  assert.equal(languageOf("notes/README.md"), "markdown");
  assert.equal(languageOf("Cargo.toml"), "toml");
  assert.equal(languageOf("src/lib.rs"), "rust");
  assert.equal(languageOf("index.html"), "html5");
  assert.equal(languageOf("settings.yml"), "yaml");
  // Dockerfile and CMakeLists.txt are named by their whole name, with no extension to read.
  assert.equal(languageOf("deploy/Dockerfile"), "docker");
  assert.equal(languageOf("build/CMakeLists.txt"), "cmake");
  assert.equal(languageOf("Makefile"), null);
  assert.equal(languageOf("notes.unknown"), null);
  assert.equal(languageOf(".gitignore"), null);
});

test("a note changed again is told once, as it stands now, newest first", () => {
  const saved = { id: "mem_1", change: "Saved", words: "The user prefers short answers." };
  const changed = { id: "mem_1", change: "Updated", words: "The user prefers short answers, in Turkish." };
  const other = { id: "mem_2", change: "Saved", words: "The office is closed on Friday." };
  const list = addMemory(addMemory([], saved), other);
  assert.deepEqual(addMemory(list, changed), [changed, other]);
  const many = Array.from({ length: 8 }, (_, index) => ({ id: `mem_${index}`, change: "Saved", words: `${index}` }));
  assert.equal(addMemory([], many[0], 6).length, 1);
  assert.equal(addMemory(many, { id: "mem_new", change: "Saved", words: "new" }, 6).length, 6);
  assert.equal(addMemory(many, { id: "mem_new", change: "Saved", words: "new" }, 6)[0].id, "mem_new");
});
