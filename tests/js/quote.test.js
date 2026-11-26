import assert from "node:assert/strict";
import { test } from "node:test";

import { quoteInto, splitQuote } from "../../src/dotagent/server/static/js/quotebutton.js";

test("a quote starts the draft, each line behind > and a blank line after it", () => {
  assert.equal(quoteInto("", "First line\nSecond line"), "> First line\n> Second line\n\n");
  assert.equal(quoteInto("and you?", "Hello"), "> Hello\n\nand you?");
});

test("a second quote replaces the first and keeps what was typed", () => {
  const once = quoteInto("my words", "One");
  assert.equal(quoteInto(once, "Two"), "> Two\n\nmy words");
});

test("blank runs fold, a long message is cut with an ellipsis, nothing to quote leaves the draft", () => {
  assert.equal(quoteInto("", "a\n\n\n\nb"), "> a\n>\n> b\n\n");
  const long = quoteInto("", "x".repeat(400));
  assert.equal(long, `> ${"x".repeat(279)}…\n\n`);
  assert.equal(quoteInto("keep", "   \n  "), "keep");
});

test("a command and what is typed are read after the quote, and a quote alone says nothing is typed", () => {
  assert.deepEqual(splitQuote("> One\n> Two\n\n/btw why?"), { quote: "One\nTwo", words: "/btw why?" });
  assert.deepEqual(splitQuote("/loop 5m go"), { quote: null, words: "/loop 5m go" });
  assert.equal(splitQuote(quoteInto("", "Hello")).words.trim(), "");
});
