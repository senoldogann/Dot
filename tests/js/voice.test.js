// The words of the voice (item 574), read by voice.js: what a dictation hears, how it fills the box, and how an answer is
// turned into what is said. Pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  QUIET_BAR,
  allowPane,
  allowToast,
  appleTouch,
  captureOffer,
  captureProblem,
  clockText,
  deafWords,
  dictated,
  languageTag,
  listeningWay,
  localVoice,
  macOffer,
  nextWave,
  problemOffer,
  problemWords,
  sendProblem,
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

test("the links, the rule and the pipes of a table, and the spaces around them, are read as they were", () => {
  // Item 612 made their patterns read in linear time; what they say did not change.
  assert.equal(spokenText("see [the docs](https://example.com/docs) and ![a chart](chart.png)"), "see the docs and a chart.");
  assert.equal(spokenText("Text with [one](https://a.b/c)[two](https://a.b/d)."), "Text with onetwo.");
  assert.equal(spokenText("[[1](https://example.com/a)]"), "[1].");
  assert.equal(spokenText("[docs](https://example.com/?q[]=1)"), "docs.");
  assert.equal(spokenText("[wiki](https://en.wikipedia.org/wiki/Python_(programming_language)) end"), "wiki) end.");
  // What is not a link stays as written: an open bracket, an address with no end, a bracket inside the words.
  assert.equal(spokenText("[open [x] bracket and a [real](https://x.y/z) link"), "[open [x] bracket and a real link.");
  assert.equal(spokenText("[a](https://x.y/z and [b](https://x.y/y)"), "[a](link and b.");
  assert.equal(spokenText("[a [b](https://x.y/z)"), "a [b.");
  assert.equal(spokenText("![a [b] c](img.png) and [d]"), "![a [b] c](img.png) and [d].");
  // A table: its rule is not read, its cells are, with or without the spaces and tabs around the pipes.
  assert.equal(spokenText("| a | | c |"), "a, , c.");
  assert.equal(spokenText("a \t|  b"), "a, b.");
  assert.equal(spokenText("a   |   b   |   c"), "a, b, c.");
  assert.equal(spokenText("  | a |  \n  :--- |  ---: |  \n| b | c |"), "a. b, c.");
  assert.equal(spokenText("|---|---|\n|a|b|"), "a, b.");
  // A row with an empty cell between its dashes is no rule.
  assert.equal(spokenText("-- | | --"), "--, , --.");
  assert.equal(spokenText("   \n  \t  \n"), "");
});

test("an answer of eighty thousand characters of one kind is turned into words in a moment, whichever kind", () => {
  // Item 612: the pattern of the links read to the end of the line from each "[" of a run of them, the rule of a table and
  // the pipes from each space of a run of spaces: 80,000 of them took seconds, on the page's thread, as the answer came.
  const size = 80_000;
  const kinds = {
    "a run of [": "[".repeat(size),
    "a run of [ and then ](": `${"[".repeat(size)}](`,
    "a run of ![": "![".repeat(size / 2),
    "a run of [a": "[a".repeat(size / 2),
    "a run of [a](": "[a](".repeat(size / 4),
    "a run of [a](b": "[a](b".repeat(size / 5),
    "a run of spaces": " ".repeat(size),
    "spaces, then a word": `${" ".repeat(size)}word`,
    "tabs, then a word": `${"\t".repeat(size)}word`,
    "a rule of dashes, then spaces and a word": `--${" ".repeat(size)}word`,
    "pipes and spaces": "| ".repeat(size / 2),
    "a pipe, spaces, a pipe": `|${" ".repeat(size)}|`,
    "lines of spaces": " \n".repeat(size / 2),
  };
  for (const [kind, text] of Object.entries(kinds)) {
    const started = performance.now();
    spokenText(text);
    const took = performance.now() - started;
    // The quadratic patterns took seconds; a loaded Mac takes a few hundred milliseconds for a kind at most (item 606).
    assert.ok(took < 1000, `${kind}: ${took.toFixed(0)} ms for ${text.length} characters`);
  }
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

test("the recorder's clock reads minutes and seconds since the engine began", () => {
  // Item 605.
  assert.equal(clockText(0), "0:00");
  assert.equal(clockText(5_400), "0:05");
  assert.equal(clockText(62_000), "1:02");
  assert.equal(clockText(760_000), "12:40");
  assert.equal(clockText(-50), "0:00");
});

test("the recorder's wave moves one bar to the left, a tall one coming in while a sound is heard and a dot in a silence", () => {
  // Item 605.
  const quiet = [QUIET_BAR, QUIET_BAR, QUIET_BAR];
  assert.deepEqual(nextWave(quiet, false, () => 0.5), [QUIET_BAR, QUIET_BAR, QUIET_BAR]);
  const loud = nextWave(quiet, true, () => 0.5);
  assert.equal(loud.length, 3);
  assert.deepEqual(loud.slice(0, 2), [QUIET_BAR, QUIET_BAR]);
  assert.ok(Math.abs(loud[2] - 0.65) < 1e-9, String(loud[2]));
  assert.equal(nextWave(quiet, true, () => 0)[2], 0.3);
  assert.equal(nextWave(quiet, true, () => 1)[2], 1);
  assert.deepEqual(nextWave([0.9, 0.5, 0.2], false, () => 0.5), [0.5, 0.2, QUIET_BAR]);
});

// ----- how the page listens (item 626) ------------------------------------------------------------------------------------

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
const MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15";
const MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36";
const FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0";

/** The way the page listens in a browser that `recognition` and `recording` describe, with the Mac able or not. */
function wayOf(userAgent, touchPoints, { recognition = true, recording = true, mac = true } = {}) {
  return listeningWay({ recognition, recording, mac, userAgent, touchPoints });
}

test("an iPhone or an iPad records for the Mac when the Mac can write down, and keeps Safari's recognition when it cannot", () => {
  assert.equal(wayOf(IPHONE, 5), "recording");
  assert.equal(wayOf("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1", 5), "recording");
  // An iPad that asks for the computer's pages says it is a Mac with a touch screen.
  assert.equal(wayOf(MAC_SAFARI, 5), "recording");
  // Where the Mac cannot, the recognition stays, with the toast of item 616: better than no microphone.
  assert.equal(wayOf(IPHONE, 5, { mac: false }), "recognition");
  // A page that cannot record (an address that is not secure has no microphone to open) keeps the recognition too.
  assert.equal(wayOf(IPHONE, 5, { recording: false }), "recognition");
  // And an iPhone with neither has no microphone at all.
  assert.equal(wayOf(IPHONE, 5, { recognition: false, mac: false }), null);
  assert.equal(wayOf(IPHONE, 5, { recognition: false, recording: false }), null);
});

test("a browser with no speech recognition records when the Mac can write down, and has no microphone when it cannot", () => {
  assert.equal(wayOf(FIREFOX, 0, { recognition: false }), "recording");
  assert.equal(wayOf(FIREFOX, 0, { recognition: false, mac: false }), null);
  assert.equal(wayOf(FIREFOX, 0, { recognition: false, recording: false }), null);
});

test("everywhere else, a Mac's Safari or Chrome and Dot.app, the browser's recognition stays whatever the Mac can", () => {
  for (const mac of [true, false]) {
    assert.equal(wayOf(MAC_SAFARI, 0, { mac }), "recognition");
    assert.equal(wayOf(MAC_CHROME, 0, { mac }), "recognition");
    assert.equal(wayOf("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0", 0, { mac }), "recognition");
    // A phone that is not Apple's: Chrome on Android hears every time.
    assert.equal(wayOf("Mozilla/5.0 (Linux; Android 15; Pixel 9) Chrome/140.0 Mobile Safari/537.36", 5, { mac }), "recognition");
  }
});

test("an iPhone, an iPod and an iPad are Apple's touch screens, and a Mac with one touch point is not", () => {
  assert.equal(appleTouch(IPHONE, 5), true);
  assert.equal(appleTouch("Mozilla/5.0 (iPod touch; CPU iPhone OS 17_0 like Mac OS X)", 5), true);
  assert.equal(appleTouch(MAC_SAFARI, 5), true);
  assert.equal(appleTouch(MAC_SAFARI, 1), false);
  assert.equal(appleTouch(MAC_SAFARI, 0), false);
  assert.equal(appleTouch(MAC_CHROME, 0), false);
});

test("what the Mac lacks to write down what is said is offered as one sentence, and as none where it lacks nothing", () => {
  assert.equal(macOffer(null), "");
  assert.equal(macOffer({ transcribe: true, why: null }), "");
  assert.equal(macOffer({ transcribe: false, why: null }), "");
  assert.equal(
    macOffer({ transcribe: false, why: "This Mac needs macOS 26 or later" }),
    "A Mac that writes down what you say hears every time. This Mac needs macOS 26 or later.",
  );
  assert.equal(
    macOffer({ transcribe: false, why: "Install Apple's developer tools on the Mac: xcode-select --install" }),
    "A Mac that writes down what you say hears every time. Install Apple's developer tools on the Mac: xcode-select --install.",
  );
});

test("the toast of a dictation Safari did not hear says what the Mac lacks to write down instead, when it says anything", () => {
  const words = "Safari heard nothing this time.";
  assert.equal(deafWords(words, null), words);
  assert.equal(deafWords(words, { transcribe: false, why: null }), words);
  assert.equal(
    deafWords(words, { transcribe: false, why: "Install Apple's developer tools on the Mac: xcode-select --install" }),
    "Safari heard nothing this time. A Mac that writes down what you say hears every time. Install Apple's developer tools on the Mac: xcode-select --install.",
  );
});

test("a refused or missing microphone is told where to allow it in the words of the place, and with no word of speech recognition", () => {
  const refused = (name, place) => captureProblem({ name, message: "x" }, place);
  for (const name of ["NotAllowedError", "PermissionDeniedError", "SecurityError"]) {
    assert.equal(
      refused(name, "other"),
      "The browser has not allowed the microphone on this page. Allow it for this address in the browser's settings, and the browser in the device's settings. The keyboard's own microphone types into the box meanwhile.",
    );
  }
  assert.match(refused("NotAllowedError", "mac"), /System Settings › Privacy & Security › Microphone\. macOS Dictation types into the box/);
  assert.match(refused("NotAllowedError", "app"), /^macOS has not given Dot the microphone\. Allow Dot in System Settings/);
  for (const place of ["app", "mac", "other"]) {
    assert.doesNotMatch(refused("NotAllowedError", place), /speech recognition/i);
  }
  assert.equal(refused("NotFoundError", "other"), "No microphone was found.");
  assert.equal(refused("NotFoundError", "mac"), "No microphone was found: check the input in System Settings › Sound.");
  assert.match(refused("NotReadableError", "other"), /another app may be using it/);
  // The audio the page's own capture could not start (capture.js): press again, or reload.
  assert.equal(
    refused("InvalidStateError", "other"),
    "The browser would not start the sound for the microphone. Press the microphone again; if that does not help, reload the page.",
  );
  assert.equal(captureProblem(new Error("boom"), "other"), "The microphone could not start: boom");
  assert.equal(captureProblem(undefined, "other"), "The microphone could not start: the browser gave no reason");
});

test("what a recording met on its way to the Mac is said in plain words: unreachable, a language it cannot, or its own words", () => {
  assert.match(sendProblem({ status: 0, message: "Cannot reach the Dot daemon. Is it running?" }), /^The Mac could not be reached, so what you said was not written down\./);
  assert.equal(
    sendProblem({ status: 422, message: "This Mac cannot write down xx-YY", context: { code: "unsupported-language" } }),
    "The Mac cannot write down this language. Choose another in Settings › General › Voice.",
  );
  assert.equal(sendProblem({ status: 422, message: "The Mac took longer than 60 seconds", context: { code: "timeout" } }), "The Mac took longer than 60 seconds");
  for (const code of ["unavailable", "no-audio", "failed"]) {
    assert.equal(sendProblem({ status: 422, message: `words for the owner of ${code}`, context: { code } }), `words for the owner of ${code}`);
  }
  // What is not the Mac's own to say is no words for the owner: a request it could not read, a daemon that failed.
  assert.equal(sendProblem({ status: 422, message: "the recording is not a WAV file", context: {} }), "The Mac could not write down what you said.");
  assert.equal(sendProblem({ status: 500, message: "Dot failed on an error it did not expect", context: { error: "x" } }), "The Mac could not write down what you said.");
  assert.equal(sendProblem({ status: 422, message: "", context: { code: "failed" } }), "The Mac could not write down what you said.");
  assert.equal(sendProblem(undefined), "The Mac could not write down what you said.");
  // None of it says what the page does: no file format, no program, no model.
  for (const words of [sendProblem({ status: 0 }), sendProblem({ context: { code: "unsupported-language" } })]) {
    assert.doesNotMatch(words, /WAV|Swift|transcrib|locale/i);
  }
});

// ----- a refusal in Dot.app opens System Settings (item 636) --------------------------------------------------------

const DICTATION_APP = "macOS Dictation types into the box meanwhile: the Dictation key, or Fn twice.";

test("the pane to show is Speech Recognition when the error names it, and Microphone for every other error", () => {
  // WebKit words a refusal of macOS's speech recognition and one of the microphone apart in the event's message.
  for (const message of [
    "Speech recognition service permission check has failed",
    "speech recognition is not allowed",
    "SpeechRecognition was refused",
    "Speech-recognition denied",
  ]) {
    assert.equal(allowPane({ message }), "speech", message);
  }
  for (const message of ["Microphone permission check has failed", "Permission denied", "", "Permission check failed"]) {
    assert.equal(allowPane({ message }), "microphone", message);
  }
  // An error that says nothing (no message at all) is the microphone's.
  assert.equal(allowPane({}), "microphone");
  assert.equal(allowPane(null), "microphone");
  assert.equal(allowPane(undefined), "microphone");
});

test("only Dot.app offers System Settings, and only for macOS refusing the microphone or its speech recognition", () => {
  const speech = { message: "Speech recognition service permission check has failed" };
  for (const code of ["not-allowed", "service-not-allowed"]) {
    assert.deepEqual(problemOffer({ error: code, message: "" }, "app"), { allow: "microphone" });
    assert.deepEqual(problemOffer({ error: code, message: speech.message }, "app"), { allow: "speech" });
    // A browser on a Mac or a phone has no System Settings that the Mac opens for it: today's words alone.
    assert.equal(problemOffer({ error: code, ...speech }, "mac"), undefined);
    assert.equal(problemOffer({ error: code, ...speech }, "other"), undefined);
  }
  // Nothing else that goes wrong is helped by it.
  for (const code of ["no-speech", "aborted", "audio-capture", "network", "language-not-supported", "something-new"]) {
    assert.equal(problemOffer({ error: code, message: "" }, "app"), undefined, code);
  }
  assert.equal(problemOffer({}, "app"), undefined);
});

test("the offer goes with the words of the refusals it is made for and with no other", () => {
  // The offer and the words are of the same errors: what is offered System Settings is told macOS has not given Dot it.
  for (const code of ["not-allowed", "service-not-allowed"]) {
    assert.notEqual(problemOffer({ error: code }, "app"), undefined);
    assert.match(problemWords(code, "app"), /^macOS has not given Dot the microphone or its speech recognition/);
  }
  for (const name of ["NotAllowedError", "PermissionDeniedError", "SecurityError"]) {
    assert.notEqual(captureOffer({ name }, "app"), undefined);
    assert.match(captureProblem({ name }, "app"), /^macOS has not given Dot the microphone\./);
  }
});

test("a refused recording in Dot.app offers System Settings, and every other error of the microphone offers nothing", () => {
  for (const name of ["NotAllowedError", "PermissionDeniedError", "SecurityError"]) {
    assert.deepEqual(captureOffer({ name, message: "Permission denied" }, "app"), { allow: "microphone" });
    assert.deepEqual(captureOffer({ name, message: "Speech recognition was refused" }, "app"), { allow: "speech" });
    assert.equal(captureOffer({ name, message: "x" }, "mac"), undefined);
    assert.equal(captureOffer({ name, message: "x" }, "other"), undefined);
  }
  for (const name of ["NotFoundError", "OverconstrainedError", "NotReadableError", "AbortError", "InvalidStateError", "Error"]) {
    assert.equal(captureOffer({ name, message: "x" }, "app"), undefined, name);
  }
  assert.equal(captureOffer(null, "app"), undefined);
  assert.equal(captureOffer(undefined, "app"), undefined);
});

test("System Settings opened is said calmly, at the pane, naming the other pane too, with macOS Dictation after", () => {
  assert.deepEqual(allowToast({ opened: true, app: "ready" }, "microphone"), {
    kind: "ok",
    words: `System Settings is open at Microphone: switch Dot on there, and under Speech Recognition too. ${DICTATION_APP}`,
  });
  assert.deepEqual(allowToast({ opened: true, app: "ready" }, "speech"), {
    kind: "ok",
    words: `System Settings is open at Speech Recognition: switch Dot on there, and under Microphone too. ${DICTATION_APP}`,
  });
  // A Dot.app the Mac did not find running had the pane shown all the same.
  assert.deepEqual(allowToast({ opened: true, app: "unknown" }, "microphone"), allowToast({ opened: true, app: "ready" }, "microphone"));
});

test("a Dot.app built before Dot asked macOS for the microphone is told so, as an error, with macOS Dictation after", () => {
  const old = allowToast({ opened: false, app: "old" }, "microphone");
  assert.equal(old.kind, "error");
  assert.equal(
    old.words,
    `This Dot.app was built before Dot asked macOS for the microphone, so macOS gives it none, whatever System Settings shows. The next Dot.app asks for it, and Dot offers it as soon as it is out. ${DICTATION_APP}`,
  );
  // The pane asked for does not change what is true of the app.
  assert.deepEqual(allowToast({ opened: false, app: "old" }, "speech"), old);
  assert.doesNotMatch(old.words, /switch Dot on/);
});

test("an answer that says neither leaves the words of the refusal to stand", () => {
  for (const answer of [null, undefined, {}, { opened: false }, { opened: false, app: "ready" }, { app: "ready" }, { opened: "yes" }]) {
    assert.equal(allowToast(answer, "microphone"), null, JSON.stringify(answer));
  }
});
