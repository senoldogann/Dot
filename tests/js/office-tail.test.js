import assert from "node:assert/strict";
import { test } from "node:test";

import { tailOf } from "../../src/dotagent/server/static/js/office/tail.js";

test("a short text is shown whole", () => {
  assert.equal(tailOf("**Hi** there"), "**Hi** there");
});

test("a long text keeps its newest whole blocks", () => {
  const text = `${"a ".repeat(450)}\n\n${"b ".repeat(450)}\n\n## Done\n\nAll **good**.`;
  const tail = tailOf(text);
  assert.ok(tail.startsWith("…\n\n"));
  assert.ok(tail.endsWith("## Done\n\nAll **good**."));
  assert.ok(!tail.includes("a a"));
});

test("a long paragraph with no blank line in it is shown whole, never cut in a word", () => {
  const text = `${"word ".repeat(400)}**bold** end`;
  assert.equal(tailOf(text), text);
});

test("a code block written still keeps its opening line and its newest lines", () => {
  const code = Array.from({ length: 300 }, (_, i) => `# line ${i}`).join("\n");
  const tail = tailOf(`Intro\n\n\`\`\`py\n${code}`);
  const lines = tail.split("\n");
  assert.equal(lines[2], "```py");
  assert.equal(lines.at(-1), "# line 299");
  assert.ok(tail.length <= 1610);
});

test("a cut never falls inside a tilde fence or a fence under a list item", () => {
  const fenced = `~~~\n${"x = 1\n\n".repeat(120)}~~~`;
  const listed = `- step\n\n  \`\`\`sh\n${"  echo hi\n\n".repeat(120)}  \`\`\``;
  for (const block of [fenced, listed]) {
    const tail = tailOf(`${"p ".repeat(500)}\n\n${block}\n\nAfter.`);
    assert.ok(tail.includes(block.split("\n")[0]), tail.slice(0, 80));
  }
});
