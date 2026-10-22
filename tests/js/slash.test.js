// The words of the composer's menu, read by slash.js: pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { filterGroups, menuSections, paletteGroups } from "../../src/dotagent/server/static/js/slash.js";

const COMMANDS = [{ label: "/btw", hint: "Ask on the side" }];
const PALETTE = {
  skills: [{ name: "excel", description: "Workbooks", icon: "table", source: "builtin", imported: false }],
  servers: [
    { name: "tavily", mark: null, tools: [{ name: "tavily_search", description: "Search the web" }] },
    { name: "context7", mark: null, tools: [{ name: "resolve-library-id", description: "Find a library" }] },
  ],
};

test("a menu is drawn in sections: the commands, the skills and one of the MCP tools of every server", () => {
  const sections = menuSections(paletteGroups(COMMANDS, PALETTE));
  assert.deepEqual(
    sections.map((section) => [section.title, section.items.length]),
    [
      ["Commands", 1],
      ["Skills", 1],
      ["MCP tools", 2],
    ],
  );
});

test("a tool is found and shown with its server, unless its name already begins with it", () => {
  const [, , tools] = menuSections(paletteGroups(COMMANDS, PALETTE));
  assert.deepEqual(
    tools.items.map((item) => item.label),
    ["tavily_search", "context7 / resolve-library-id"],
  );
  // The server's name finds the tools of a server whose tool names do not carry it.
  const found = menuSections(filterGroups(paletteGroups(COMMANDS, PALETTE), "context7"));
  assert.deepEqual(found.map((section) => section.title), ["MCP tools"]);
});

test("the at menu keeps its groups as they are: the servers, the accounts", () => {
  const sections = menuSections([
    { title: "MCP servers", kind: "plugin", items: [{ label: "tavily" }] },
    { title: "Accounts", kind: "account", items: [{ label: "a@b.c" }] },
  ]);
  assert.deepEqual(sections.map((section) => section.title), ["MCP servers", "Accounts"]);
});
