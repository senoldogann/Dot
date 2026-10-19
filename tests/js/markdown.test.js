// The Markdown the answers are written in, read by markdown.js: pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { parseBlocks, parseInline, readsAsMarkdown } from "../../src/dotagent/server/static/js/markdown.js";

test("inline text keeps code, emphasis and links apart", () => {
  const parts = parseInline("Run `make` for **all** of [the docs](https://example.com/docs).");
  assert.equal(parts.find((part) => part.type === "code").value, "make");
  assert.equal(parts.find((part) => part.type === "bold").value, "all");
  const link = parts.find((part) => part.type === "link");
  assert.deepEqual({ value: link.value, href: link.href }, { value: "the docs", href: "https://example.com/docs" });
  assert.equal(parts.map((part) => part.value).join(""), "Run make for all of the docs.");
});

test("a link to anything but http(s) stays text", () => {
  const parts = parseInline("[click](javascript:alert(1)) and [file](file:///etc/passwd)");
  assert.deepEqual(
    parts.map((part) => part.type),
    parts.map(() => "text"),
  );
  assert.equal(parts.map((part) => part.value).join(""), "[click](javascript:alert(1)) and [file](file:///etc/passwd)");
});

test("an unclosed backtick is text, not a code span that swallows the line", () => {
  const parts = parseInline("a ` b");
  assert.deepEqual(
    parts.map((part) => part.type),
    ["text", "text", "text"],
  );
  assert.equal(parts.map((part) => part.value).join(""), "a ` b");
});

test("blocks: heading, lists, table, fenced code, quote, rule and paragraph", () => {
  const text = [
    "## The sea",
    "",
    "1. rises",
    "2. falls",
    "",
    "- salt",
    "",
    "| Part | Role |",
    "|---|---|",
    "| Tide | Twice a day |",
    "| Current | Carries heat |",
    "",
    "```bash",
    "echo 'the sea'",
    "```",
    "",
    "> a quote",
    "",
    "---",
    "",
    "Plain words.",
  ].join("\r\n");
  const blocks = parseBlocks(text);
  assert.deepEqual(
    blocks.map((block) => block.type),
    ["heading", "list", "list", "table", "code", "quote", "rule", "paragraph"],
  );
  assert.equal(blocks[0].level, 2);
  assert.equal(blocks[1].ordered, true);
  assert.equal(blocks[1].items.length, 2);
  assert.equal(blocks[2].ordered, false);
  assert.equal(blocks[3].header.length, 2);
  assert.equal(blocks[3].rows.length, 2);
  assert.deepEqual({ language: blocks[4].language, text: blocks[4].text }, { language: "bash", text: "echo 'the sea'" });
  assert.equal(blocks[7].inline[0].value, "Plain words.");
});

test("a fence that never closes runs to the end of the text", () => {
  const blocks = parseBlocks("```\nopen\nstill open");
  assert.equal(blocks.length, 1);
  assert.deepEqual({ type: blocks[0].type, text: blocks[0].text }, { type: "code", text: "open\nstill open" });
});

test("what reads as Markdown: structure or emphasis, not plain words or a bare address", () => {
  assert.equal(readsAsMarkdown("Plain words, nothing more."), false);
  assert.equal(readsAsMarkdown("See https://example.com for more."), false);
  assert.equal(readsAsMarkdown("See **this**."), true);
  assert.equal(readsAsMarkdown("# A title"), true);
  assert.equal(readsAsMarkdown("- one\n- two"), true);
  assert.equal(readsAsMarkdown("Run `make`."), true);
});
