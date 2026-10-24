// The dictation of a box (item 603), with a recognition engine of the test's own and its clock: the silence is counted
// from the moment the engine began to listen, never through the questions a browser asks the first time (an iPhone asks
// twice), and an engine is never stopped before it began nor left on for nobody.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createDictation } from "../../src/dotagent/server/static/js/voice.js";

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

// A phone's browser: what voice.js reads of the page when a dictation runs.
globalThis.window = { SpeechRecognition: FakeRecognition };
globalThis.document = {
  documentElement: { dataset: {} },
  visibilityState: "visible",
  addEventListener() {},
  removeEventListener() {},
};
Object.defineProperty(globalThis, "navigator", {
  value: { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari", maxTouchPoints: 5 },
  configurable: true,
});

/** A result of the engine: its words, and whether it is settled. */
function heard(transcript, isFinal) {
  return Object.assign([{ transcript, confidence: 0.9 }], { isFinal });
}

/** A dictation of an empty box, what it told the page, and the engine it made last. */
function dictationOf() {
  const seen = { shown: [], states: [], problems: [] };
  const dictation = createDictation({
    language: () => "tr-TR",
    text: () => "",
    show: (value) => seen.shown.push(value),
    settled: () => {},
    state: (listening) => seen.states.push(listening),
    problem: (words) => seen.problems.push(words),
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
