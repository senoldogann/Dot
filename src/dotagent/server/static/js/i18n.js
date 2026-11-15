// The language of the interface's own words (English or Turkish): Settings › General chooses it, kept in this browser
// as the other interface settings are, and English until it is chosen. It never follows the browser, the model nor
// what a conversation is written in, so that a page reads the same on every Mac, a test's among them; the words of
// the conversation stay as they were written.
// The words themselves are in lang/en.js and lang/tr.js, one catalogue each with the same keys (`tsc` holds a
// Turkish catalogue to every English key, and tests/js/i18n.test.js checks both ways and the placeholders). A change of
// the language reloads the page, so that every screen is drawn in it at once.

import { EN } from "./lang/en.js";
import { TR } from "./lang/tr.js";
import { readPref } from "./prefs.js";

/** @typedef {keyof typeof EN} WordKey */
/** @typedef {"en" | "tr"} UiLanguage */

/** Where the chosen language is kept in this browser. */
export const UI_LANGUAGE_KEY = "dot.uiLanguage";

/** The languages the interface speaks, each named in itself. */
export const UI_LANGUAGES = [
  { id: "en", label: "English" },
  { id: "tr", label: "Türkçe" },
];

/** @type {Record<UiLanguage, Record<WordKey, string>>} */
const CATALOGUES = { en: EN, tr: TR };
// The locale of each language's dates and numbers: Dot's English writes "5 Oct, 14:03" (en-GB) as it always did.
/** @type {Record<UiLanguage, string>} */
const LOCALES = { en: "en-GB", tr: "tr-TR" };

/**
 * The language of the interface: the one kept under `UI_LANGUAGE_KEY`, English when none is.
 * @returns {UiLanguage}
 */
function chosenLanguage() {
  return readPref(UI_LANGUAGE_KEY) === "tr" ? "tr" : "en";
}

const LANGUAGE = chosenLanguage();

/** The language the page is drawn in, read once as it loads. */
export function uiLanguage() {
  return LANGUAGE;
}

/** The locale of the page's dates and numbers ("en-GB" or "tr-TR"). */
export function uiLocale() {
  return LOCALES[LANGUAGE];
}

/**
 * Fill `{name}` in `template` with `values[name]`. A name the values lack is an error, never a blank on the screen.
 * @param {string} template
 * @param {Record<string, string | number>} values
 * @param {string} key the catalogue's key, for the error
 * @returns {string}
 */
export function fill(template, values, key) {
  return template.replace(/\{(\w+)\}/g, (_whole, name) => {
    if (!Object.hasOwn(values, name)) {
      throw new Error(`the words "${key}" need a value for {${name}}`);
    }
    return String(values[name]);
  });
}

/**
 * The words `key` in the interface's language, with its `{name}` places filled from `values`.
 * @param {WordKey} key
 * @param {Record<string, string | number>} [values]
 * @returns {string}
 */
export function t(key, values = {}) {
  return fill(CATALOGUES[LANGUAGE][key], values, key);
}

/**
 * The words of `one` for a count of 1, else of `many`, with `{count}` filled; other places from `values`.
 * @param {number} count
 * @param {WordKey} one
 * @param {WordKey} many
 * @param {Record<string, string | number>} [values]
 * @returns {string}
 */
export function tCount(count, one, many, values = {}) {
  return t(count === 1 ? one : many, { ...values, count });
}

if (typeof document !== "undefined") {
  document.documentElement.lang = LANGUAGE;
}
