// The interface's words in two languages (i18n.js): the Turkish catalogue has every English key and no other, each
// sentence keeps the places its English one fills, and a place left without a value is an error, never a blank.

import assert from "node:assert/strict";
import test from "node:test";

import { fill, t, uiLanguage, uiLocale } from "../../src/dotagent/server/static/js/i18n.js";
import { EN } from "../../src/dotagent/server/static/js/lang/en.js";
import { TR } from "../../src/dotagent/server/static/js/lang/tr.js";

/** The `{name}` places of a sentence, sorted. */
function places(text) {
  return [...text.matchAll(/\{(\w+)\}/g)].map((found) => found[1]).sort();
}

test("both catalogues have the same keys", () => {
  assert.deepEqual(Object.keys(TR).sort(), Object.keys(EN).sort());
});

test("each Turkish sentence fills the places its English one fills, and none is empty", () => {
  for (const [key, english] of Object.entries(EN)) {
    assert.deepEqual(places(TR[key]), places(english), key);
    assert.notEqual(TR[key].trim(), "", key);
    assert.notEqual(english.trim(), "", key);
  }
});

test("a page that chose no language reads English, its dates as Dot's English writes them", () => {
  assert.equal(uiLanguage(), "en");
  assert.equal(uiLocale(), "en-GB");
  assert.equal(t("intent.steer"), "Add to task");
  assert.equal(t("capsule.working", { took: "5 s" }), "Working 5 s");
});

test("a place without its value is an error, not a blank on the screen", () => {
  assert.throws(() => fill("Working {took}", {}, "capsule.working"), /need a value for \{took\}/);
  assert.equal(fill("{a} and {b}", { a: 1, b: "two" }, "key"), "1 and two");
});
