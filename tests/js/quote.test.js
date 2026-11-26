import assert from "node:assert/strict";
import { test } from "node:test";

import { quoteInto, quotedFilesMessage, splitQuote } from "../../src/dotagent/server/static/js/quotebutton.js";

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

test("words that only start with > are words, and a quote is the block with a blank line after it", () => {
  assert.deepEqual(splitQuote(">3 files please"), { quote: null, words: ">3 files please" });
  assert.deepEqual(splitQuote(">= 5\nand more"), { quote: null, words: ">= 5\nand more" });
  assert.deepEqual(splitQuote("> a\n> b"), { quote: null, words: "> a\n> b" });
  assert.deepEqual(splitQuote("> a\n\nmy words"), { quote: "a", words: "my words" });
});

test("a quote leaves typed words that start with > alone, and files with no words keep the quote", () => {
  assert.equal(quoteInto(">3 files please", "Hello"), "> Hello\n\n>3 files please");
  assert.equal(quoteInto(">= 5\nand more", "Hi"), "> Hi\n\n>= 5\nand more");
  assert.equal(quotedFilesMessage("> One\n\n", "See the attached file."), "> One\n\nSee the attached file.");
  assert.equal(quotedFilesMessage("", "See the attached file."), "See the attached file.");
  assert.equal(quotedFilesMessage(">3 files", "See the attached file."), "See the attached file.");
});
