// The Markdown the answers are written in, read by markdown.js: pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { openFence, parseBlocks, parseInline, readsAsMarkdown, sourceLines } from "../../src/dotagent/server/static/js/markdown.js";

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

test("a fence says its language by the first word after its mark, whatever else the writer added", () => {
  assert.deepEqual(openFence('```python title="service.py"'), { indent: 0, marker: "```", language: "python", title: "service.py" });
  assert.equal(openFence("```js {1,3}").language, "js");
  assert.deepEqual([openFence("``` python:main.py").language, openFence("``` python:main.py").title], ["python", "main.py"]);
  assert.equal(openFence("```js title='a b.js'").title, "a b.js");
  assert.equal(openFence("```js").title, "");
  assert.equal(openFence("~~~ruby").language, "ruby");
  assert.equal(openFence("   ```").indent, 3);
  assert.equal(openFence("```").language, "");
  // A line of code in a line is no fence; neither is a mark with fewer than three.
  assert.equal(openFence("```x``` and more"), null);
  assert.equal(openFence("``not a fence"), null);
});

test("a fence with more than a language after its mark opens its block, and the block after it is read as itself", () => {
  const blocks = parseBlocks(['```python title="service.py"', "def greet(): pass", "```", "", "```text", "words", "```"].join("\n"));
  assert.deepEqual(
    blocks.map((block) => [block.type, block.language, block.text]),
    [
      ["code", "python", "def greet(): pass"],
      ["code", "text", "words"],
    ],
  );
});

test("code stands as far in from its fence as the fence stands in from the margin, and a longer fence closes a shorter one only if as long", () => {
  const indented = parseBlocks(["  ```sh", "  npm install", "    nested", "  ```"].join("\n"));
  assert.equal(indented[0].text, "npm install\n  nested");
  const outer = parseBlocks(["````md", "```js", "x", "```", "````"].join("\n"));
  assert.deepEqual({ type: outer[0].type, text: outer[0].text }, { type: "code", text: "```js\nx\n```" });
});

test("lists nest, and the blocks under an item belong to it", () => {
  const blocks = parseBlocks(
    [
      "1. Install:",
      "   ```bash",
      "   npm install",
      "   ```",
      "2. Run the checks",
      "   - unit tests",
      "     - with coverage",
      "   - smoke tests",
      "3. Ship it.",
    ].join("\n"),
  );
  assert.equal(blocks.length, 1);
  const [list] = blocks;
  assert.deepEqual({ type: list.type, ordered: list.ordered, start: list.start, items: list.items.length }, { type: "list", ordered: true, start: 1, items: 3 });
  assert.deepEqual(
    list.items.map((item) => item.blocks.map((block) => block.type)),
    [["code"], ["list"], []],
  );
  assert.equal(list.items[0].blocks[0].text, "npm install");
  const checks = list.items[1].blocks[0];
  assert.deepEqual({ ordered: checks.ordered, items: checks.items.length }, { ordered: false, items: 2 });
  assert.equal(checks.items[0].blocks[0].items.length, 1);
});

test("a numbered list keeps the number it starts at, and goes on past a blank line between its items", () => {
  const split = parseBlocks(["1. one", "", "2. two", "", "3. three"].join("\n"));
  assert.deepEqual(split.map((block) => [block.type, block.items.length]), [["list", 3]]);
  const later = parseBlocks(["3. three", "4. four"].join("\n"));
  assert.equal(later[0].start, 3);
  // A list cut by a block that is not under an item starts again where the numbers say.
  const cut = parseBlocks(["1. one", "```", "x", "```", "2. two"].join("\n"));
  assert.deepEqual(cut.map((block) => block.type === "list" ? ["list", block.start] : [block.type]), [["list", 1], ["code"], ["list", 2]]);
});

test("a bullet after a numbered item, at its own margin, starts another list", () => {
  const blocks = parseBlocks(["1. one", "- bullet"].join("\n"));
  assert.deepEqual(blocks.map((block) => block.ordered), [true, false]);
});

test("a task item says whether it is done and keeps its words", () => {
  const [list] = parseBlocks(["- [ ] write the tests", "- [x] read the code", "- [X] shout"].join("\n"));
  assert.deepEqual(list.items.map((item) => item.checked), [false, true, true]);
  assert.equal(list.items[0].inline[0].value, "write the tests");
  const [plain] = parseBlocks("- [link](https://example.com)");
  assert.equal(plain.items[0].checked, null);
});

test("lists nest only so deep, so that a text of nothing but nested marks stays cheap", () => {
  const lines = Array.from({ length: 200 }, (_, depth) => `${" ".repeat(depth * 2)}- level ${depth}`);
  const started = performance.now();
  const blocks = parseBlocks(lines.join("\n"));
  assert.ok(performance.now() - started < 500);
  assert.equal(blocks[0].type, "list");
});

test("a table's rule line says how its columns align, with a dash or more in each cell", () => {
  const [table] = parseBlocks(["| Tool | Does | Notes |", "|:--|:-:|--:|", "| a | b | c |"].join("\n"));
  assert.equal(table.type, "table");
  assert.deepEqual(table.aligns, ["left", "center", "right"]);
  const [plain] = parseBlocks(["A | B", "-|-", "1 | 2"].join("\n"));
  assert.deepEqual({ type: plain.type, aligns: plain.aligns }, { type: "table", aligns: [null, null] });
  // A line with a pipe over a rule of dashes is a table; over a single dash it is not.
  assert.equal(parseBlocks("one | two\n-")[0].type, "paragraph");
});

test("a heading is read to the sixth level, without the hashes that close it", () => {
  const blocks = parseBlocks(["##### Fifth", "###### Sixth ######"].join("\n\n"));
  assert.deepEqual(blocks.map((block) => [block.level, block.inline[0].value]), [[5, "Fifth"], [6, "Sixth"]]);
});

test("strikethrough, bold and italics with underscores, and an address in angle brackets", () => {
  const parts = parseInline("a ~~gone~~, __loud__, _soft_, snake_case_name and <https://example.com/x>.");
  assert.deepEqual(
    parts.map((part) => [part.type, part.value]),
    [
      ["text", "a "],
      ["strike", "gone"],
      ["text", ", "],
      ["bold", "loud"],
      ["text", ", "],
      ["italic", "soft"],
      ["text", ", snake_case_name and "],
      ["link", "https://example.com/x"],
      ["text", "."],
    ],
  );
  assert.equal(parts.find((part) => part.type === "link").href, "https://example.com/x");
  assert.equal(readsAsMarkdown("~~done~~"), true);
});

test("a quote holds the blocks written in it", () => {
  const [quote] = parseBlocks(["> Steps:", "> - one", "> - two", ">", "> ```", "> code", "> ```"].join("\n"));
  assert.equal(quote.type, "quote");
  assert.deepEqual(quote.blocks.map((block) => block.type), ["paragraph", "list", "code"]);
});

test("the composer's copy of the text reads the same fences and headings, and hands every letter back", () => {
  const text = ['```python title="x.py"', "a ~~b~~ __c__ _d_", "```", "###### six ~~gone~~ _soft_ __loud__", "A | B", "-|-"].join("\n");
  const lines = sourceLines(text);
  assert.deepEqual(lines.map((line) => line.kind), ["fence", "block", "fence", "heading", "table-head", "table-rule"]);
  assert.equal(lines.map((line) => line.pieces.map((one) => one.text).join("")).join("\n"), text);
  const emphasis = sourceLines("~~gone~~ __loud__ _soft_ **bold** *it*")[0].pieces;
  assert.deepEqual(emphasis.filter((one) => one.kind === "strong" || one.kind === "em").map((one) => one.text), ["loud", "soft", "bold", "it"]);
});

test("a backslash before a mark makes it a letter, except in code, which keeps the backslash", () => {
  const parts = parseInline("2 \\* 3 \\* 4, \\_not soft\\_, \\[not a link](x), `\\*raw\\*` and a\\\\b");
  assert.deepEqual(
    parts.map((part) => [part.type, part.value]),
    [
      ["text", "2 * 3 * 4, _not soft_, [not a link](x), "],
      ["code", "\\*raw\\*"],
      ["text", " and a\\b"],
    ],
  );
});

test("a private-use character of a writer's (an icon font's glyph) stays itself, in whatever block it is", () => {
  // The first plane's private use area holds the glyphs of icon fonts (U+E02A sits where `*` would be hidden at U+E000),
  // the last plane's those of Nerd Fonts' Material Design icons (U+F002A, where `*` would be hidden at U+F0000).
  const parts = parseInline("prompt \ue02a\u{f002a}\u{f005f} done \\* and \ue02a");
  assert.deepEqual(
    parts.map((part) => [part.type, part.value]),
    [["text", "prompt \ue02a\u{f002a}\u{f005f} done * and \ue02a"]],
  );
  // A line that uses the first blocks of the area still hides its marks in one it does not: the glyphs and the escaped
  // marks come back as they were, in text and in code.
  const crowded = parseInline("\u{f002a} \\* `\\_` \u{f00aa} \u{f0130}");
  assert.deepEqual(
    crowded.map((part) => [part.type, part.value]),
    [
      ["text", "\u{f002a} * "],
      ["code", "\\_"],
      ["text", " \u{f00aa} \u{f0130}"],
    ],
  );
});

test("a break written as a tag is a break, and the entities a writer spells out are the marks they stand for", () => {
  const parts = parseInline("a<br>b<br/>c &lt;tag&gt; &amp; &quot;q&quot;");
  assert.deepEqual(
    parts.map((part) => [part.type, part.value]),
    [
      ["text", "a"],
      ["break", ""],
      ["text", "b"],
      ["break", ""],
      ["text", 'c <tag> & "q"'],
    ],
  );
  // Code keeps what is written in it.
  assert.deepEqual(parseInline("`a<br>&lt;`").map((part) => part.value), ["a<br>&lt;"]);
});

test("a picture written as a link is its words as a link, and nothing is drawn for it", () => {
  const parts = parseInline("see ![the chart](https://example.com/chart.png) here");
  assert.deepEqual(parts.map((part) => [part.type, part.value, part.href]), [
    ["text", "see ", undefined],
    ["link", "the chart", "https://example.com/chart.png"],
    ["text", " here", undefined],
  ]);
});
