// Every word of the interface goes through the catalogue (item 19 of the 2026-11-26 review): the web UI was English on
// most screens of a Turkish owner's Dot, because a module could write its words in its code and nothing said so. A string
// that reaches the screen and reads as English must be a `t("key")` of lang/en-*.js with its Turkish in lang/tr-*.js; a
// line the program uses and no person reads stands in tests/js/i18n-exempt.json with its reason.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { EN } from "../../src/dotagent/server/static/js/lang/en.js";
import { TR } from "../../src/dotagent/server/static/js/lang/tr.js";
import { hardcodedWords, modulesOf, sourceOf, STATIC_ROOT } from "./literals.js";

/** @type {Record<string, string>} the lines that are not for the screen, each with why */
const EXEMPT = JSON.parse(readFileSync(new URL("./i18n-exempt.json", import.meta.url), "utf8"));

// Modules that cannot or need not use the catalogue: the catalogue itself, the two scripts the page runs before its
// modules load (they paint the first frame and have no import), and the drawing code of Dot's face, which holds no words.
const OUTSIDE = (name) => name.startsWith("lang/") || name.startsWith("character/") || name === "appearance/rules.js" || name === "boot.js";

test("no module writes a line of English that reaches the screen in its code", () => {
  const found = modulesOf(STATIC_ROOT)
    .filter((name) => !OUTSIDE(name))
    .flatMap((name) => hardcodedWords(sourceOf(name)).filter(({ text }) => !Object.hasOwn(EXEMPT, text)).map(({ line, text }) => `${name}:${line} ${JSON.stringify(text.slice(0, 80))}`));
  assert.deepEqual(
    found,
    [],
    "These lines are written in the code, so they read English in every language. Make each one a key of lang/en-views.js or lang/en-app.js with its Turkish in lang/tr-views.js or lang/tr-app.js, and use t(key); a line only the program reads goes in tests/js/i18n-exempt.json with its reason.",
  );
});

test("every line of tests/js/i18n-exempt.json still stands in the code", () => {
  const written = new Set(
    modulesOf(STATIC_ROOT)
      .filter((name) => !OUTSIDE(name))
      .flatMap((name) => hardcodedWords(sourceOf(name)).map(({ text }) => text)),
  );
  // An exempt line stands as a literal that would be flagged, or as one the scan does not look at (a comparison, a
  // property of the program): both are fine while the text is in some module.
  const everything = modulesOf(STATIC_ROOT).map((name) => sourceOf(name)).join("\n");
  const gone = Object.keys(EXEMPT).filter((text) => !written.has(text) && !everything.includes(text.replace(/\n/g, "\\n").replace(/"/g, '\\"')) && !everything.includes(text.split("\n")[0]));
  assert.deepEqual(gone, [], "These exempt lines are no longer in any module: delete them from tests/js/i18n-exempt.json.");
});

test("a sentence of English is never kept as its own Turkish", () => {
  const same = Object.keys(EN).filter((key) => EN[key] === TR[key] && EN[key].trim().split(/\s+/).length >= 4);
  assert.deepEqual(same, [], "These keys have English words as their Turkish: translate them in lang/tr-*.js.");
});
