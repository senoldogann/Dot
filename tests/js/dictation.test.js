// The dictation of a box (item 603), with a recognition engine of the test's own and its clock: the silence is counted
// from the moment the engine began to listen, never through the questions a browser asks the first time (an iPhone asks
// twice), and an engine is never stopped before it began nor left on for nobody.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createDictation, readAloud } from "../../src/dotagent/server/static/js/voice.js";

/** A recognition engine that does what it is told and says only what the test makes it say. */
class FakeRecognition extends EventTarget {
  static made = [];

  constructor() {
    super();
    this.calls = [];
    FakeRecognition.made.push(this);
  }

  start() {
    this.calls.push("start");
  }

  stop() {
    this.calls.push("stop");
  }

  abort() {
    this.calls.push("abort");
  }

  /** The engine says `type`, with the fields its event carries (`results`, `error`). */
  say(type, fields = {}) {
    this.dispatchEvent(Object.assign(new Event(type), fields));
  }
}

/** The browser's voice, which keeps what it was told: the pieces it speaks and how often its queue was cancelled. */
const synth = {
  spoken: [],
  cancelled: 0,
  speak(utterance) {
    this.spoken.push(utterance.text);
  },
  cancel() {
    this.cancelled += 1;
  },
  getVoices: () => [],
};
globalThis.SpeechSynthesisUtterance = class extends EventTarget {
  constructor(text) {
    super();
    this.text = text;
  }
};

// A phone's browser: what voice.js reads of the page when a dictation runs.
globalThis.window = { SpeechRecognition: FakeRecognition, speechSynthesis: synth };

/** The page loaded anew: what its earlier dictations did is forgotten (item 616). */
function freshPage() {
  globalThis.document = {
    documentElement: { dataset: {} },
    visibilityState: "visible",
    addEventListener() {},
    removeEventListener() {},
  };
}
freshPage();
Object.defineProperty(globalThis, "navigator", {
  value: { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari", maxTouchPoints: 5 },
  configurable: true,
});

/** A result of the engine: its words, and whether it is settled. */
function heard(transcript, isFinal) {
  return Object.assign([{ transcript, confidence: 0.9 }], { isFinal });
}

/** A dictation of an empty box on a page loaded anew, what it told the page, and the engine it made last. */
function dictationOf() {
  freshPage();
  const seen = { shown: [], states: [], problems: [], offers: [], began: 0, sounds: [] };
  const dictation = createDictation({
    language: () => "tr-TR",
    text: () => "",
    show: (value) => seen.shown.push(value),
    settled: () => {},
    state: (listening) => seen.states.push(listening),
    problem: (words, offer = null) => {
      seen.problems.push(words);
      seen.offers.push(offer);
    },
    began: () => {
      seen.began += 1;
    },
    sound: (loud) => seen.sounds.push(loud),
  });
  return { dictation, seen, engine: () => FakeRecognition.made.at(-1) };
}

test("the silence is counted from the engine's start, never through the questions the browser asks first", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  assert.deepEqual(engine().calls, ["start"]);
  assert.deepEqual(seen.states, [true]);
  // The first time, Safari asks for its speech recognition and the page for the microphone; the owner reads both.
  t.mock.timers.tick(20_000);
  assert.deepEqual(engine().calls, ["start"], "the engine was stopped while the browser asked");
  assert.deepEqual(seen.problems, []);
  engine().say("start");
  t.mock.timers.tick(5_999);
  assert.deepEqual(engine().calls, ["start"]);
  t.mock.timers.tick(1);
  assert.deepEqual(engine().calls, ["start", "stop"]);
  // Begun, it heard nothing in its silence, and says so with what to check.
  engine().say("end");
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.equal(dictation.listening(), false);
});

test("a second press while the browser still asks drops the engine, and one that begins anyway is dropped again", () => {
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  dictation.toggle();
  // Nothing to settle before it began: dropped, not stopped, and nothing said.
  assert.deepEqual(engine().calls, ["start", "abort"]);
  assert.equal(dictation.listening(), false);
  assert.deepEqual(seen.problems, []);
  // Safari starts it once the owner allows it, whatever was asked of it meanwhile: the microphone is not left on.
  engine().say("start");
  assert.deepEqual(engine().calls, ["start", "abort", "abort"]);
  engine().say("result", { results: [heard("merhaba", true)] });
  assert.deepEqual(engine().calls, ["start", "abort", "abort", "abort"]);
  assert.deepEqual(seen.shown, [], "an engine the dictation left wrote into the box");
  assert.deepEqual(seen.problems, []);
});

test("an engine that ends before it began says nothing of a silence, and a refusal says why", () => {
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("end");
  assert.deepEqual(seen.problems, []);
  assert.equal(dictation.listening(), false);
  dictation.start();
  engine().say("error", { error: "not-allowed" });
  engine().say("end");
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^The browser has not allowed the microphone/);
});

test("the microphone opening, or a word heard, says the engine began: a second press then settles what it heard", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("audiostart");
  t.mock.timers.tick(6_000);
  assert.deepEqual(engine().calls, ["start", "stop"]);
  engine().say("end");
  // An engine that says nothing of its start but hears a word has begun.
  dictation.start();
  engine().say("result", { results: [heard("merhaba", true)] });
  dictation.toggle();
  assert.deepEqual(engine().calls, ["start", "stop"]);
  assert.deepEqual(seen.shown, ["merhaba"]);
  engine().say("end");
  assert.equal(seen.problems.length, 1, "the owner's own stop said something");
});

test("the recorder is told once that the engine began, and whenever it hears a sound or a silence", () => {
  // Item 605: the clock starts when the engine listens, and the wave moves while it hears a sound.
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  assert.equal(seen.began, 0);
  engine().say("start");
  engine().say("audiostart");
  assert.equal(seen.began, 1);
  engine().say("soundstart");
  engine().say("speechend");
  engine().say("result", { results: [heard("merhaba", false)] });
  assert.deepEqual(seen.sounds, [true, false, true]);
  // An engine the dictation left tells the recorder nothing.
  dictation.abort();
  engine().say("soundstart");
  assert.deepEqual(seen.sounds, [true, false, true]);
});

test("once a word is heard, a pause may last longer before the dictation ends by itself with what it heard", (t) => {
  // Item 605: six seconds with no word say nothing was heard; after a word the owner may stop to think.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("start");
  t.mock.timers.tick(3_000);
  engine().say("result", { results: [heard("merhaba", true)] });
  t.mock.timers.tick(14_999);
  assert.deepEqual(engine().calls, ["start"]);
  t.mock.timers.tick(1);
  assert.deepEqual(engine().calls, ["start", "stop"]);
  engine().say("end");
  assert.deepEqual(seen.problems, []);
  assert.deepEqual(seen.shown, ["merhaba"]);
});

test("the engine's own \"aborted\" is no stop of the owner's: a page's first dictation that heard nothing says so", (t) => {
  // Item 616: Safari answers the stop of a dictation that heard nothing with "aborted", where Chrome says "no-speech";
  // the dictation's own abort lets the engine go first, so an "aborted" it hears is the engine's. It said nothing.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  freshPage();
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("start");
  t.mock.timers.tick(6_000);
  assert.deepEqual(engine().calls, ["start", "stop"]);
  engine().say("error", { error: "aborted" });
  engine().say("end");
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.deepEqual(seen.offers, [null]);
  assert.equal(dictation.listening(), false);
});

test("on an iPhone, a dictation that hears nothing after the page's first says it is Safari's and offers to reload", () => {
  // Item 616: Safari on an iPhone hears the first dictation after the page loads, and at times a later one opens the
  // microphone (its start and audiostart) and hears nothing at all (WebKit bugs 317741 and 321436): the owner speaks to a
  // flat wave. Whoever stops it, it says so, and how to get the microphone back.
  freshPage();
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("start");
  engine().say("result", { results: [heard("merhaba", true)] });
  dictation.stop();
  engine().say("end");
  assert.deepEqual(seen.problems, []);
  dictation.start();
  engine().say("start");
  engine().say("audiostart");
  dictation.stop();
  engine().say("error", { error: "aborted" });
  engine().say("end");
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Safari heard nothing this time\./);
  assert.match(seen.problems[0], /reload the page/);
  assert.deepEqual(seen.offers, [{ reload: true }]);
});

test("a later dictation that hears nothing on a computer, or on an iPhone's page anew, says only that nothing was heard", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const iphone = navigator.userAgent;
  freshPage();
  Object.defineProperty(globalThis, "navigator", {
    value: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140", maxTouchPoints: 0 },
    configurable: true,
  });
  try {
    const { dictation, seen, engine } = dictationOf();
    for (let round = 0; round < 2; round += 1) {
      dictation.start();
      engine().say("start");
      t.mock.timers.tick(6_000);
      engine().say("error", { error: "no-speech" });
      engine().say("end");
    }
    assert.equal(seen.problems.length, 2);
    assert.ok(seen.problems.every((words) => words.startsWith("Nothing was heard.")), seen.problems.join("\n"));
    assert.deepEqual(seen.offers, [null, null]);
  } finally {
    Object.defineProperty(globalThis, "navigator", {
      value: { userAgent: iphone, maxTouchPoints: 5 },
      configurable: true,
    });
  }
});

test("the owner's tick that the engine never answers ends the dictation with what it heard, or says it heard nothing", () => {
  // Item 616: a deaf Safari may answer no stop at all; the recorder's wait then ends it (microphone.js), which said
  // nothing either.
  freshPage();
  const { dictation, seen, engine } = dictationOf();
  dictation.start();
  engine().say("start");
  engine().say("result", { results: [heard("merhaba", true)] });
  dictation.stop();
  dictation.end();
  assert.deepEqual(engine().calls, ["start", "stop", "abort"]);
  assert.equal(dictation.listening(), false);
  assert.deepEqual(seen.shown, ["merhaba"]);
  assert.deepEqual(seen.problems, []);
  dictation.start();
  engine().say("start");
  dictation.stop();
  dictation.end();
  assert.equal(dictation.listening(), false);
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Safari heard nothing this time\./);
  // A dictation that is over has nothing to end.
  dictation.end();
  assert.equal(seen.problems.length, 1);
});

test("a dictation stops a reading aloud before it listens", () => {
  // Item 616: Safari on an iPhone does not hear while it speaks, and a microphone that hears the reading would write the
  // answer into the box.
  freshPage();
  let ended = 0;
  const handle = readAloud("The answer is here.", "en-US", () => {
    ended += 1;
  }, () => {});
  assert.notEqual(handle, null);
  const cancelled = synth.cancelled;
  const { dictation } = dictationOf();
  dictation.start();
  assert.equal(synth.cancelled, cancelled + 1);
  assert.equal(ended, 1);
});

test("an iPad that asks for a computer's pages is an iPad: a later dictation that hears nothing says it is Safari's", () => {
  const iphone = navigator.userAgent;
  freshPage();
  Object.defineProperty(globalThis, "navigator", {
    value: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15", maxTouchPoints: 5 },
    configurable: true,
  });
  try {
    const { dictation, seen, engine } = dictationOf();
    for (let round = 0; round < 2; round += 1) {
      dictation.start();
      engine().say("start");
      dictation.stop();
      engine().say("error", { error: "aborted" });
      engine().say("end");
    }
    // The first, the owner's own stop of a silence, says nothing; the second is Safari's deafness.
    assert.equal(seen.problems.length, 1);
    assert.match(seen.problems[0], /^Safari heard nothing this time\./);
  } finally {
    Object.defineProperty(globalThis, "navigator", {
      value: { userAgent: iphone, maxTouchPoints: 5 },
      configurable: true,
    });
  }
});
