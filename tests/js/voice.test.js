// The words of the voice (item 574), read by voice.js: what a dictation hears, how it fills the box, and how an answer is
// turned into what is said. Pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dictated,
  languageTag,
  localVoice,
  problemWords,
  sentenceChunks,
  voicePlace,
  spokenText,
  transcriptOf,
} from "../../src/dotagent/server/static/js/voice.js";

/** A result of the engine: its words, and whether it is settled. */
function heard(transcript, isFinal) {
  return Object.assign([{ transcript, confidence: 0.9 }], { isFinal });
}

test("the voice follows the page's language unless one is chosen, a bare language given its region", () => {
  // Item 593: Chrome's recognition can hear nothing in a tag without its region.
  assert.equal(languageTag("page", "tr", "en-US"), "tr-TR");
  assert.equal(languageTag("page", "en", "tr-TR"), "en-US");
  assert.equal(languageTag("page", "pt", ""), "pt");
  assert.equal(languageTag("page", "", "fi-FI"), "fi-FI");
  assert.equal(languageTag("page", "", ""), "en-US");
  assert.equal(languageTag("tr-TR", "en", "en-US"), "tr-TR");
});

test("what the engine heard is two texts: the words it is sure of and those it may still change", () => {
  assert.deepEqual(transcriptOf([heard("hello there", true), heard(" how are", false)]), {
    final: "hello there",
    interim: "how are",
  });
  assert.deepEqual(transcriptOf([heard("one", true), heard("  two ", true), heard("", false)]), {
    final: "one two",
    interim: "",
  });
  assert.deepEqual(transcriptOf([]), { final: "", interim: "" });
});

test("the box holds what it held, then the words heard, and says where the words that may still change begin", () => {
  assert.deepEqual(dictated("", "hello", "wor"), { value: "hello wor", interimFrom: 6 });
  assert.deepEqual(dictated("Dear Ada,", "thanks", ""), { value: "Dear Ada, thanks", interimFrom: null });
  assert.deepEqual(dictated("Dear Ada,\n", "thanks", "for"), { value: "Dear Ada,\nthanks for", interimFrom: 17 });
  assert.deepEqual(dictated("kept", "", ""), { value: "kept", interimFrom: null });
  // A box with words of an earlier dictation heard on is rebuilt from what it held when this one began.
  assert.equal(dictated("kept", "one two", "").value, "kept one two");
});

test("the place the microphone's words name: Dot.app, a Mac's browser, or a phone's and any other", () => {
  const mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15";
  assert.equal(voicePlace("desktop", mac, 0), "app");
  assert.equal(voicePlace(undefined, mac, 0), "mac");
  // An iPad that asks for the desktop's pages says it is a Mac, with a touch screen.
  assert.equal(voicePlace(undefined, mac, 5), "other");
  assert.equal(voicePlace(undefined, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari", 5), "other");
});

test("an error of the engine says what to do where the page is, and the owner's own stop says nothing", () => {
  assert.match(problemWords("not-allowed", "mac"), /The browser has not allowed the microphone/);
  assert.match(problemWords("not-allowed", "mac"), /System Settings › Privacy & Security › Microphone/);
  // Item 593: in Dot.app macOS gives the microphone, not a browser; macOS Dictation types into the box meanwhile.
  assert.match(problemWords("not-allowed", "app"), /^macOS has not given Dot the microphone/);
  for (const place of ["app", "mac"]) {
    assert.match(problemWords("not-allowed", place), /macOS Dictation types into the box/);
  }
  // A phone has no System Settings nor Fn key: its words name its own settings and its keyboard's microphone.
  for (const code of ["not-allowed", "no-speech", "audio-capture"]) {
    assert.doesNotMatch(problemWords(code, "other"), /System Settings|Fn|macOS/);
  }
  assert.match(problemWords("not-allowed", "other"), /keyboard's own microphone/);
  for (const place of ["app", "mac", "other"]) {
    assert.equal(problemWords("service-not-allowed", place), problemWords("not-allowed", place));
    assert.match(problemWords("audio-capture", place), /No microphone/);
    assert.match(problemWords("language-not-supported", place), /choose another/);
    assert.equal(problemWords("aborted", place), null);
  }
  // Nothing heard says so, and what to check: a microphone kept from the app or the browser looks like a silence.
  assert.match(problemWords("no-speech", "mac"), /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.match(problemWords("no-speech", "app"), /^Nothing was heard\. Check that Dot may use the microphone/);
  assert.match(problemWords("no-speech", "other"), /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.equal(problemWords("something-new", "mac"), "Speech recognition stopped: something-new.");
});

test("an answer is read without its code, its marks and its addresses", () => {
  const answer = [
    "## Plan",
    "",
    "Do **this** first, then `that`, and *later* the rest.",
    "",
    "```python",
    "print('not read')",
    "```",
    "",
    "- one item",
    "1. a numbered one",
    "",
    "See [the docs](https://example.com/docs) or https://example.org/x for more.",
    "",
    "| name | size |",
    "| --- | --- |",
    "| a | 1 |",
  ].join("\n");
  assert.equal(
    spokenText(answer),
    "Plan. Do this first, then that, and later the rest. one item. a numbered one. See the docs or link for more. name, size. a, 1.",
  );
});

test("a line that ends a sentence keeps its mark, one that does not gets a full stop", () => {
  assert.equal(spokenText("Done\nAll set!\nWhat next?"), "Done. All set! What next?");
  assert.equal(spokenText(""), "");
  assert.equal(spokenText("```\nonly code\n```"), "");
});

test("a snake_case name keeps its underscores and a lone star stays", () => {
  assert.equal(spokenText("Use read_file on 2 * 3."), "Use read_file on 2 * 3.");
});

test("an answer is said in pieces that end where a sentence does", () => {
  assert.deepEqual(sentenceChunks("One. Two. Three.", 8), ["One.", "Two.", "Three."]);
  assert.deepEqual(sentenceChunks("One. Two. Three.", 100), ["One. Two. Three."]);
  assert.deepEqual(sentenceChunks("", 100), []);
});

test("a sentence longer than a piece is cut at a space, and no piece is longer than the size", () => {
  const long = `${"word ".repeat(100)}end.`;
  const pieces = sentenceChunks(long, 50);
  assert.ok(pieces.length > 1);
  assert.ok(pieces.every((piece) => piece.length <= 50), pieces.map((piece) => piece.length).join(","));
  assert.equal(pieces.join(" ").replace(/\s+/g, " "), long.replace(/\s+/g, " "));
  // One word with no space in it is cut at the size.
  assert.deepEqual(sentenceChunks("x".repeat(25), 10), ["x".repeat(10), "x".repeat(10), "x".repeat(5)]);
});

test("a voice kept on this Mac is chosen before a network one, the language's own region first", () => {
  const voices = [
    { name: "Google English", lang: "en-US", localService: false },
    { name: "Samantha", lang: "en-US", localService: true },
    { name: "Daniel", lang: "en-GB", localService: true },
    { name: "Yelda", lang: "tr_TR", localService: true },
    { name: "Google Türkçe", lang: "tr-TR", localService: false },
  ];
  assert.equal(localVoice("en-US", voices).name, "Samantha");
  assert.equal(localVoice("en-GB", voices).name, "Daniel");
  // A region the Mac has no voice for is read by a voice of the language, and a bare language by the first of its voices.
  assert.equal(localVoice("en-AU", voices).name, "Samantha");
  assert.equal(localVoice("en", voices).name, "Samantha");
  assert.equal(localVoice("TR-tr", voices).name, "Yelda");
});

test("with no voice of the language kept on this Mac none is chosen, and the browser chooses by the language", () => {
  const voices = [
    { name: "Google Suomi", lang: "fi-FI", localService: false },
    { name: "Samantha", lang: "en-US", localService: true },
  ];
  assert.equal(localVoice("fi-FI", voices), null);
  assert.equal(localVoice("de-DE", voices), null);
  assert.equal(localVoice("fi-FI", []), null);
});
