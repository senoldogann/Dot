// The rules of Settings › Appearance (item 1067), which boot.js paints the first paint with and appearance/settings.js
// every choice after it: run as the page runs them, a classic script, on a page element that records what is put on it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const SOURCE = readFileSync(new URL("../../src/dotagent/server/static/js/appearance/rules.js", import.meta.url), "utf8");

/** The rules as a window of `phone` width loads them. */
function load(phone) {
  const context = { window: { matchMedia: () => ({ matches: phone }) } };
  context.globalThis = context;
  runInNewContext(SOURCE, context);
  return context.DOT_APPEARANCE;
}

/** A page element that keeps its tokens and attributes. */
function element() {
  const tokens = new Map();
  const flags = new Map();
  return {
    tokens,
    flags,
    style: { setProperty: (name, value) => tokens.set(name, value), removeProperty: (name) => tokens.delete(name) },
    setAttribute: (name, value) => flags.set(name, value),
    removeAttribute: (name) => flags.delete(name),
  };
}

const kept = (values) => (key) => values[key] ?? null;

test("nothing kept puts nothing on the page", () => {
  const root = element();
  load(false).paintAll(kept({}), root);
  assert.equal(root.tokens.size, 0);
  assert.equal(root.flags.size, 0);
});

test("each kept value is put on the page as its token or its attribute", () => {
  const root = element();
  load(false).paintAll(
    kept({
      "dot.contrast": "150",
      "dot.chatWidth": "wide",
      "dot.sizeInterface": "16",
      "dot.weightInterface": "300",
      "dot.fontCode": "menlo",
      "dot.fontInterface": "IBM Plex Sans, Avenir",
      "dot.smoothing": "off",
      "dot.wrapCode": "on",
    }),
    root,
  );
  assert.equal(root.flags.get("data-contrast"), "");
  assert.equal(root.tokens.get("--contrast-boost"), "50%");
  assert.equal(root.flags.get("data-chat-width"), "wide");
  assert.equal(root.tokens.get("--type-scale"), String(16 / 14));
  assert.equal(root.tokens.get("--weight-text"), "300");
  assert.equal(root.tokens.get("--mono"), "var(--family-menlo)");
  assert.equal(root.tokens.get("--font"), '"IBM Plex Sans", Avenir, var(--family-system)');
  assert.equal(root.flags.get("data-smoothing"), "off");
  assert.equal(root.flags.get("data-wrap-code"), "");
});

test("what a rule would not keep reads as nothing kept", () => {
  const root = element();
  load(false).paintAll(kept({ "dot.contrast": "37", "dot.fontInterface": "Plex; } body {", "dot.weightCode": "450" }), root);
  assert.equal(root.tokens.size, 0);
  assert.equal(root.flags.size, 0);
});

test("what a browser kept before every face offered the same families reads as what it is now", () => {
  const { rules } = load(false);
  assert.equal(rules.answerFont.parse("theme"), "");
  assert.equal(rules.answerFont.parse("interface"), "interface");
  assert.equal(rules.composerFont.parse("mono"), "mono");
  // A key every object has is no value kept before (the pre-push review of item 1062).
  assert.equal(rules.answerFont.parse("constructor"), "constructor");
  assert.equal(rules.interfaceFont.parse("interface"), "interface");
});

test("a size of the answers kept stays when the interface's brings their default to it", () => {
  const { rules, paintAll } = load(false);
  const read = kept({ "dot.sizeInterface": "17", "dot.sizeAnswers": "18" });
  // 15 × 17 / 14 rounds to 18: the default is the size kept, which is painted all the same.
  assert.equal(rules.answerSize.initial(read), 18);
  const root = element();
  paintAll(read, root);
  assert.equal(root.tokens.get("--answer-size"), "18px");
});

test("a phone's own body and message are 16 px", () => {
  const { rules } = load(true);
  assert.equal(rules.interfaceSize.initial(kept({})), 16);
  assert.equal(rules.answerSize.initial(kept({})), 16);
  assert.equal(rules.interfaceSize.parse("12"), 12);
  assert.equal(load(false).rules.interfaceSize.parse("12"), null);
});
