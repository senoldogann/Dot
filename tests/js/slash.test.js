// The words of the composer's menu, read by slash.js: pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { filterGroups, menuSections, paletteGroups } from "../../src/dotagent/server/static/js/slash.js";

const COMMANDS = [{ label: "/btw", hint: "Ask on the side" }];
const PALETTE = {
  skills: [{ name: "excel", description: "Workbooks", icon: "table", source: "builtin", imported: false }],
  // A tool by the name the model calls it (mcp/naming.py), as the palette lists it.
  servers: [
    { name: "tavily", mark: null, tools: [{ name: "mcp__tavily__tavily_search", description: "Search the web" }] },
    { name: "context7", mark: null, tools: [{ name: "mcp__context7__resolve-library-id", description: "Find a library" }] },
    { name: "web-research", mark: null, tools: [{ name: "mcp__web-research__web_crawl_results", description: "Crawl" }] },
  ],
};

test("a menu is drawn in sections: the commands, the skills and one of the MCP tools of every server", () => {
  const sections = menuSections(paletteGroups(COMMANDS, PALETTE));
  assert.deepEqual(
    sections.map((section) => [section.title, section.items.length]),
    [
      ["Commands", 1],
      ["Skills", 1],
      ["MCP tools", 3],
    ],
  );
});

test("a tool is named in words, its server's first, and found by those words or the names the model uses", () => {
  const [, , tools] = menuSections(paletteGroups(COMMANDS, PALETTE));
  // Item 581: "web-research / mcp__web-research__web_crawl_results" before.
  assert.deepEqual(
    tools.items.map((item) => item.label),
    ["Tavily · Search", "Context7 · Resolve library id", "Web Research · Web crawl results"],
  );
  const labels = (word) =>
    menuSections(filterGroups(paletteGroups(COMMANDS, PALETTE), word)).flatMap((section) => section.items.map((item) => item.label));
  assert.deepEqual(labels("crawl"), ["Web Research · Web crawl results"]);
  assert.deepEqual(labels("web-research"), ["Web Research · Web crawl results"]);
  assert.deepEqual(labels("mcp__context7"), ["Context7 · Resolve library id"]);
});

test("the at menu keeps its groups as they are: the servers, the accounts", () => {
  const sections = menuSections([
    { title: "MCP servers", kind: "plugin", items: [{ label: "tavily" }] },
    { title: "Accounts", kind: "account", items: [{ label: "a@b.c" }] },
  ]);
  assert.deepEqual(sections.map((section) => section.title), ["MCP servers", "Accounts"]);
});
