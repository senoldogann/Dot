// The recording of a box (item 626), with a microphone and a Mac of the test's own, and its clock: the silence is counted
// from the moment the microphone opened, never through the question the browser asks, a sound is told by how loud a
// block is, the tick sends what was heard and no more, and every end lets the microphone and what it held go.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HOLD_MS, LOUD_LEVEL, createRecording, macLanguage } from "../../src/dotagent/server/static/js/recording.js";
import { PAUSE_MS, SILENCE_MS, readAloud } from "../../src/dotagent/server/static/js/voice.js";
import { MAX_SAMPLES, RECORDING_RATE } from "../../src/dotagent/server/static/js/wav.js";

const RATE = 48000;
const BLOCK = 2048;
// A block is 2048 samples: 43 milliseconds at 48 kHz.
const BLOCK_MS = (BLOCK / RATE) * 1000;
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";

// What the page logs for whoever looks into a failure (recording.js): kept, so that a test of a failure says nothing to the
// terminal, and the test of the failures can see that it was logged.
const warnings = [];
console.warn = (...words) => {
  warnings.push(words);
};

/** A block of the microphone's sound: a sine wave of `amplitude` (its loudness is that over the root of two); 0 is a silence. */
function block(amplitude = 0.3) {
  return Float32Array.from({ length: BLOCK }, (_, index) => amplitude * Math.sin(index * 0.2));
}

/** The browser's voice, which keeps what it was told: a reading aloud is stopped by `cancel`. */
const synth = {
  cancelled: 0,
  speak() {},
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
globalThis.window = { speechSynthesis: synth };

// What a page tells a recording of itself: where it is, and when it goes out of sight.
let listeners = [];

/** A page loaded anew on `userAgent`. */
function freshPage(userAgent = IPHONE) {
  listeners = [];
  globalThis.document = {
    documentElement: { dataset: {} },
    visibilityState: "visible",
    addEventListener(type, handler) {
      if (type === "visibilitychange") {
        listeners.push(handler);
      }
    },
    removeEventListener(_type, handler) {
      listeners = listeners.filter((one) => one !== handler);
    },
  };
  Object.defineProperty(globalThis, "navigator", { value: { userAgent, maxTouchPoints: 5 }, configurable: true });
}

/** The page goes out of sight. */
function leave() {
  globalThis.document.visibilityState = "hidden";
  for (const handler of [...listeners]) {
    handler();
  }
}

/** Let what is on its way (a microphone opening, the Mac's answer) arrive. */
const arrive = () => new Promise((resolve) => setImmediate(resolve));

/**
 * A recording of an empty box (or one that holds `held`) on a page loaded anew, what it told the page, the microphones it
 * asked for, and the Mac it sent to. Each microphone asked for is `mic.calls[n]`: `open()` and `fail(error)` answer the
 * browser's question, `stopped` counts how often it was let go, and `block(samples)` is its sound.
 */
function recordingOf({ held = "", rate = RATE, userAgent = IPHONE } = {}) {
  freshPage(userAgent);
  const seen = { shown: [], states: [], problems: [], began: 0, sounds: [], writing: [], settled: 0 };
  const mic = { calls: [], last: () => mic.calls.at(-1) };
  const mac = { sent: [], answer: () => {}, refuse: () => {} };
  const capture = (onBlock, onEnded) => {
    const call = { stopped: 0, block: onBlock, ended: onEnded };
    mic.calls.push(call);
    return new Promise((resolve, reject) => {
      call.open = () =>
        resolve({
          rate,
          stop: async () => {
            call.stopped += 1;
          },
        });
      call.fail = reject;
    });
  };
  const send = (language, wav) =>
    new Promise((resolve, reject) => {
      mac.sent.push({ language, wav });
      mac.answer = resolve;
      mac.refuse = reject;
    });
  const recording = createRecording({
    language: () => "tr-TR",
    text: () => held,
    show: (value, interimFrom) => seen.shown.push([value, interimFrom]),
    settled: () => {
      seen.settled += 1;
    },
    state: (listening) => seen.states.push(listening),
    problem: (words) => seen.problems.push(words),
    began: () => {
      seen.began += 1;
    },
    sound: (loud) => seen.sounds.push(loud),
    writing: (on) => seen.writing.push(on),
    capture,
    send,
  });
  return { recording, seen, mic, mac };
}

/** A recording that listens: the microphone asked for has opened. */
async function listening(options) {
  const made = recordingOf(options);
  made.recording.start();
  made.mic.last().open();
  await arrive();
  return made;
}

/** What a WAV file of the page's says of itself: its header, the samples it holds and the loudest. */
function readWav(wav) {
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
  const text = (at, length) => String.fromCharCode(...wav.subarray(at, at + length));
  const samples = (wav.length - 44) / 2;
  let peak = 0;
  for (let index = 0; index < samples; index += 1) {
    peak = Math.max(peak, Math.abs(view.getInt16(44 + index * 2, true)));
  }
  return {
    head: [text(0, 4), text(8, 8), text(36, 4)],
    format: [view.getUint16(20, true), view.getUint16(22, true), view.getUint32(24, true), view.getUint16(34, true)],
    declared: view.getUint32(40, true),
    samples,
    peak,
  };
}

test("the microphone is asked for in the press, and nothing is timed while the browser asks", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic } = recordingOf();
  recording.start();
  // In the press itself: iOS starts an audio context only in a touch.
  assert.equal(mic.calls.length, 1);
  assert.deepEqual(seen.states, [true]);
  // The first time the browser asks for the microphone as long as the owner reads.
  t.mock.timers.tick(60_000);
  assert.equal(recording.listening(), true);
  assert.deepEqual(seen.problems, []);
  assert.equal(seen.began, 0);
  // A second press while it asks does not ask again.
  recording.start();
  assert.equal(mic.calls.length, 1);
  mic.last().open();
  await arrive();
  assert.equal(seen.began, 1);
  // The silence is counted from the moment it opened.
  t.mock.timers.tick(SILENCE_MS - 1);
  assert.deepEqual(seen.problems, []);
  recording.abort();
});

test("six seconds with no sound say nothing was heard, send nothing and let the microphone go", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  mic.last().block(block(0));
  mic.last().block(block(0.004));
  t.mock.timers.tick(SILENCE_MS - 1);
  assert.equal(recording.listening(), true);
  t.mock.timers.tick(1);
  assert.equal(recording.listening(), false);
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.deepEqual(seen.states, [true, false]);
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(mac.sent, []);
  assert.deepEqual(seen.shown, []);
});

test("a sound, then a pause of fifteen seconds, is the owner's tick: the recording goes to the Mac and the words into the box", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  t.mock.timers.tick(5_000);
  mic.last().block(block());
  // The pause is counted from the last sound, and is longer than the silence that says nothing was heard.
  t.mock.timers.tick(SILENCE_MS + 1);
  assert.equal(recording.listening(), true);
  t.mock.timers.tick(PAUSE_MS - SILENCE_MS - 2);
  mic.last().block(block());
  t.mock.timers.tick(PAUSE_MS - 1);
  assert.equal(mac.sent.length, 0);
  t.mock.timers.tick(1);
  assert.equal(mac.sent.length, 1);
  mac.answer("merhaba dünya");
  await arrive();
  assert.deepEqual(seen.shown, [["merhaba dünya", null]]);
  assert.deepEqual(seen.problems, []);
  assert.deepEqual(seen.states, [true, false]);
  assert.equal(recording.listening(), false);
});

test("the tick sends what was heard, 16 kHz mono 16-bit, in the language asked, and lets the microphone go at once", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  for (let index = 0; index < 10; index += 1) {
    mic.last().block(block());
  }
  for (let index = 0; index < 5; index += 1) {
    mic.last().block(block(0));
  }
  recording.toggle();
  // The microphone is let go before the Mac answers: the light of an iPhone goes out as the tick is pressed.
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(seen.writing, [true]);
  assert.equal(recording.listening(), true);
  assert.deepEqual(seen.states, [true]);
  assert.equal(mac.sent.length, 1);
  assert.equal(mac.sent[0].language, "tr-TR");
  const wav = readWav(mac.sent[0].wav);
  assert.deepEqual(wav.head, ["RIFF", "WAVEfmt ", "data"]);
  assert.deepEqual(wav.format, [1, 1, RECORDING_RATE, 16]);
  // Fifteen blocks of 2048 samples at 48 kHz are 10240 at 16 kHz.
  assert.equal(wav.samples, 10240);
  assert.equal(wav.declared, wav.samples * 2);
  assert.ok(wav.peak > 0.25 * 32767 && wav.peak <= 0.31 * 32767, `peak ${wav.peak}`);
  // A press while the Mac writes does not send it again, nor does the clock of an end the tick has made.
  recording.toggle();
  recording.stop();
  assert.equal(mac.sent.length, 1);
  mac.answer("merhaba");
  await arrive();
  assert.deepEqual(seen.writing, [true, false]);
  assert.deepEqual(seen.states, [true, false]);
  assert.equal(seen.settled, 1);
});

test("the words go into the box after what it held", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening({ held: "Dear Ada," });
  mic.last().block(block());
  recording.stop();
  mac.answer("  thanks for the plan \n");
  await arrive();
  assert.deepEqual(seen.shown, [["Dear Ada, thanks for the plan", null]]);
});

test("a tick before the first block of sound came in has nothing to send: it is dropped, says nothing and lets the microphone go", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  recording.stop();
  assert.deepEqual(mac.sent, []);
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(seen.states, [true, false]);
  assert.deepEqual(seen.writing, []);
  assert.deepEqual(seen.problems, []);
  assert.deepEqual(seen.shown, []);
  assert.equal(recording.listening(), false);
  // A block that comes after it is not heard.
  mic.last().block(block());
  assert.deepEqual(seen.sounds, []);
});

test("a tick after quiet blocks only sends what was recorded, and the Mac's word that it heard nothing is told", async (t) => {
  // The review of item 626: the owner's tick is a request, and a voice too quiet to count as a sound (the level is a guess
  // without a real microphone) is not thrown away unheard: the Mac is asked, and says when it heard nothing.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  for (let index = 0; index < 10; index += 1) {
    mic.last().block(block(0.004));
  }
  // Never as loud as a sound is: the wave stayed flat and the pause timer was never moved.
  assert.deepEqual(seen.sounds, []);
  recording.stop();
  assert.equal(mac.sent.length, 1, "the owner's tick sent nothing");
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(seen.writing, [true]);
  const wav = readWav(mac.sent[0].wav);
  // Ten blocks of 2048 samples at 48 kHz, brought down to 16 kHz.
  assert.equal(wav.samples, Math.floor((10 * 2048) / 3));
  assert.ok(wav.peak > 0 && wav.peak < 0.01 * 32767, `peak ${wav.peak}`);
  mac.answer("");
  await arrive();
  assert.deepEqual(seen.shown, []);
  assert.deepEqual(seen.writing, [true, false]);
  assert.equal(seen.problems.length, 1);
  assert.match(seen.problems[0], /^Nothing was heard\. Check that the browser may use the microphone/);
  assert.deepEqual(seen.states, [true, false]);
  assert.equal(recording.listening(), false);
});

test("the cross sends nothing, lets the microphone go and leaves the box as it was", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  mic.last().block(block());
  recording.abort();
  assert.deepEqual(mac.sent, []);
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(seen.states, [true, false]);
  assert.deepEqual(seen.shown, []);
  assert.equal(recording.listening(), false);
  // What the microphone still gives is not heard, and a silence the cross left behind says nothing.
  const sounds = seen.sounds.length;
  mic.last().block(block());
  t.mock.timers.tick(PAUSE_MS * 2);
  assert.equal(seen.sounds.length, sounds);
  assert.deepEqual(seen.problems, []);
  assert.deepEqual(mac.sent, []);
});

test("a press while the browser still asks drops it, and the microphone that opens after is let go at once", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = recordingOf();
  recording.start();
  recording.toggle();
  assert.deepEqual(seen.states, [true, false]);
  assert.equal(recording.listening(), false);
  assert.deepEqual(mac.sent, []);
  // The browser, once the owner allows it, gives the microphone anyway: it is let go, and nothing began.
  mic.last().open();
  await arrive();
  assert.equal(mic.last().stopped, 1);
  assert.equal(seen.began, 0);
  mic.last().block(block());
  assert.deepEqual(seen.sounds, []);
  t.mock.timers.tick(PAUSE_MS * 2);
  assert.deepEqual(seen.problems, []);
  // A recording started after it is the page's own again, and the one before leaves nothing in its way.
  recording.start();
  mic.last().open();
  await arrive();
  assert.equal(seen.began, 1);
  assert.equal(mic.calls.length, 2);
  assert.equal(mic.last().stopped, 0);
  recording.abort();
});

for (const [end, how] of [
  ["the tick, answered", async ({ recording, mac }) => { recording.stop(); mac.answer("merhaba"); await arrive(); }],
  ["the tick, refused", async ({ recording, mac }) => { recording.stop(); mac.refuse(new Error("no")); await arrive(); }],
  ["the cross", async ({ recording }) => recording.abort()],
  ["the page out of sight", async () => leave()],
  ["the microphone taken away", async ({ mic, mac }) => { mic.last().ended(); mac.answer("merhaba"); await arrive(); }],
  ["a silence", async (_made, t) => t.mock.timers.tick(SILENCE_MS)],
  ["dispose", async ({ recording }) => recording.dispose()],
]) {
  test(`every end lets the microphone go and what it heard: ${end}`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const made = await listening();
    // Heard nothing yet for the silence, and something for the others.
    if (end !== "a silence") {
      made.mic.last().block(block());
    }
    await how(made, t);
    assert.equal(made.mic.last().stopped, 1, "the microphone was not let go");
    assert.equal(made.recording.listening(), false);
    assert.equal(made.seen.states.at(-1), false);
    // Nothing it held is heard again: a block that comes after the end does nothing, and no timer is left.
    const sounds = made.seen.sounds.length;
    made.mic.last().block(block());
    t.mock.timers.tick(PAUSE_MS * 20);
    assert.equal(made.seen.sounds.length, sounds);
    assert.equal(made.mac.sent.length, end.startsWith("the tick") || end === "the microphone taken away" ? 1 : 0);
    assert.deepEqual(made.seen.problems.length, end === "the tick, refused" || end === "a silence" ? 1 : 0);
  });
}

test("the page out of sight drops a recording that listens, and not one the Mac is writing down", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const first = await listening();
  first.mic.last().block(block());
  leave();
  assert.deepEqual(first.seen.states, [true, false]);
  assert.deepEqual(first.mac.sent, []);
  assert.equal(first.mic.last().stopped, 1);
  const second = await listening();
  second.mic.last().block(block());
  second.recording.stop();
  leave();
  // The microphone is already let go; the Mac's answer is waited for, and goes into the box.
  assert.equal(second.recording.listening(), true);
  second.mac.answer("still here");
  await arrive();
  assert.deepEqual(second.seen.shown, [["still here", null]]);
});

test("a microphone the browser refuses, or has none of, says where to allow it, or that there is none", async () => {
  const asked = async (error) => {
    const made = recordingOf();
    made.recording.start();
    made.mic.last().fail(error);
    await arrive();
    assert.deepEqual(made.seen.states, [true, false]);
    assert.equal(made.mic.last().stopped, 0);
    return made.seen.problems;
  };
  const refused = await asked(Object.assign(new Error("denied"), { name: "NotAllowedError" }));
  assert.equal(refused.length, 1);
  assert.match(refused[0], /^The browser has not allowed the microphone on this page\. Allow it for this address in the browser's settings, and the browser in the device's settings\./);
  assert.match(refused[0], /keyboard's own microphone types into the box/);
  assert.doesNotMatch(refused[0], /speech recognition|System Settings|Fn/);
  assert.deepEqual(await asked(Object.assign(new Error("none"), { name: "NotFoundError" })), ["No microphone was found."]);
  assert.match((await asked(Object.assign(new Error("busy"), { name: "NotReadableError" })))[0], /another app may be using it/);
  assert.deepEqual(await asked(new Error("boom")), ["The microphone could not start: boom"]);
});

test("a microphone that cannot even be asked for says why, and the press leaves no recording", () => {
  freshPage();
  const problems = [];
  const states = [];
  const recording = createRecording({
    language: () => "tr-TR",
    text: () => "",
    show() {},
    settled() {},
    state: (on) => states.push(on),
    problem: (words) => problems.push(words),
    capture() {
      throw Object.assign(new Error("no audio here"), { name: "NotSupportedError" });
    },
    send: async () => "",
  });
  recording.start();
  assert.deepEqual(problems, ["The microphone could not start: no audio here"]);
  assert.deepEqual(states, [true, false]);
  assert.equal(recording.listening(), false);
});

test("what goes wrong on the way to the Mac is said in plain words, and the box keeps its words", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const told = async (refusal) => {
    const made = await listening();
    made.mic.last().block(block());
    made.recording.stop();
    if (refusal === undefined) {
      made.mac.answer("");
    } else {
      made.mac.refuse(refusal);
    }
    await arrive();
    assert.deepEqual(made.seen.shown, [], "the box was written into");
    assert.deepEqual(made.seen.states, [true, false]);
    assert.deepEqual(made.seen.writing, [true, false]);
    assert.equal(made.seen.problems.length, 1);
    return made.seen.problems[0];
  };
  const before = warnings.length;
  const unreachable = Object.assign(new Error("Cannot reach the Dot daemon. Is it running?"), { status: 0 });
  assert.match(await told(unreachable), /^The Mac could not be reached, so what you said was not written down\. Check that Dot is running on it/);
  const language = Object.assign(new Error("This Mac cannot write down tr-TR: choose another language"), {
    status: 422,
    context: { code: "unsupported-language", language: "tr-TR" },
  });
  assert.equal(await told(language), "The Mac cannot write down this language. Choose another in Settings › General › Voice.");
  const failed = Object.assign(new Error("The Mac could not write the recording down"), { status: 422, context: { code: "failed" } });
  assert.equal(await told(failed), "The Mac could not write the recording down");
  // Not words for the owner: told as a failure in plain words, whatever the daemon's own message was.
  const unreadable = Object.assign(new Error("the recording is not a WAV file"), { status: 422, context: {} });
  assert.equal(await told(unreadable), "The Mac could not write down what you said.");
  // Nothing in what the Mac sent back says no words: nothing was heard, and the words of the page's own say what to check.
  assert.match(await told(undefined), /^Nothing was heard\. Check that the browser may use the microphone/);
  // Each refusal was logged with its error, for whoever looks into it; an answer with no words is no failure.
  assert.equal(warnings.length - before, 4);
  assert.deepEqual(
    warnings.slice(before).map(([what]) => what),
    Array(4).fill("a recording could not be written down"),
  );
});

test("an answer that comes after the cross writes nothing into the box, and nothing twice", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  mic.last().block(block());
  recording.stop();
  recording.abort();
  assert.deepEqual(seen.states, [true, false]);
  mac.answer("too late");
  await arrive();
  assert.deepEqual(seen.shown, []);
  assert.deepEqual(seen.states, [true, false]);
  assert.deepEqual(seen.writing, [true, false]);
});

test("a sound is a block as loud as the level, it counts for a while after, and a quiet block is none", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic } = await listening();
  assert.equal(LOUD_LEVEL, 0.01);
  // A sine of height 0.02 is 0.014 loud, one of height 0.01 is 0.007: a quiet voice is heard, a quiet room is not.
  mic.last().block(block(0.01));
  assert.deepEqual(seen.sounds, []);
  mic.last().block(block(0.02));
  assert.deepEqual(seen.sounds, [true]);
  // It counts for HOLD_MS of the microphone's own time after the block: the pause between two words is no silence.
  const held = Math.floor(HOLD_MS / BLOCK_MS);
  for (let index = 0; index < held - 1; index += 1) {
    mic.last().block(block(0));
  }
  assert.deepEqual(seen.sounds, [true]);
  for (let index = 0; index < 3; index += 1) {
    mic.last().block(block(0));
  }
  assert.deepEqual(seen.sounds, [true, false]);
  mic.last().block(block());
  assert.deepEqual(seen.sounds, [true, false, true]);
  recording.abort();
  // The cross ends the sound too.
  assert.deepEqual(seen.sounds, [true, false, true, false]);
});

test("a recording ends at the five minutes the Mac takes, as the tick, with no more than that", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { seen, mic, mac } = await listening();
  const loud = block();
  for (let index = 0; index < 7031; index += 1) {
    mic.last().block(loud);
  }
  assert.equal(mac.sent.length, 0);
  // 7031 blocks are 299.9 seconds: the next ones go past the five minutes.
  for (let index = 0; index < 3; index += 1) {
    mic.last().block(loud);
  }
  assert.equal(mac.sent.length, 1);
  const wav = readWav(mac.sent[0].wav);
  assert.equal(wav.samples, MAX_SAMPLES);
  assert.equal(wav.declared, MAX_SAMPLES * 2);
  assert.equal(mic.last().stopped, 1);
  assert.deepEqual(seen.writing, [true]);
  mac.answer("done");
  await arrive();
});

test("the clock ends a recording at five minutes too, when the microphone gives its sound slowly", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { mic, mac } = await listening();
  // A sound now and then keeps the pause from ending it, so the five minutes do.
  for (let seconds = 0; seconds < 290; seconds += 10) {
    mic.last().block(block());
    t.mock.timers.tick(10_000);
  }
  mic.last().block(block());
  t.mock.timers.tick(9_999);
  assert.equal(mac.sent.length, 0);
  mic.last().block(block());
  t.mock.timers.tick(1);
  assert.equal(mac.sent.length, 1);
});

test("a microphone the browser takes away is written down if it was heard, and says so if it was not", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const heard = await listening();
  heard.mic.last().block(block());
  heard.mic.last().ended();
  assert.equal(heard.mac.sent.length, 1);
  assert.deepEqual(heard.seen.problems, []);
  const silent = await listening();
  silent.mic.last().ended();
  assert.deepEqual(silent.mac.sent, []);
  assert.match(silent.seen.problems[0], /^The microphone was taken away while it listened/);
  assert.deepEqual(silent.seen.states, [true, false]);
});

test("end() waits for the Mac as long as it takes, and a press while it writes sends nothing more", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { recording, seen, mic, mac } = await listening();
  mic.last().block(block());
  recording.toggle();
  recording.end();
  t.mock.timers.tick(PAUSE_MS * 20);
  assert.equal(recording.listening(), true);
  assert.deepEqual(seen.problems, []);
  assert.equal(mac.sent.length, 1);
  mac.answer("at last");
  await arrive();
  assert.deepEqual(seen.shown, [["at last", null]]);
  // And when it has ended, there is nothing to end.
  recording.end();
  assert.deepEqual(seen.states, [true, false]);
});

test("a recording stops a reading aloud before it listens", () => {
  const { recording } = recordingOf();
  readAloud("The answer is here.", "en-US", () => {}, () => {});
  const cancelled = synth.cancelled;
  recording.start();
  assert.equal(synth.cancelled, cancelled + 1);
  recording.abort();
});

test("the page's voice is given the Mac as two or three letters of the language and two of the region", () => {
  assert.equal(macLanguage("tr-TR"), "tr-TR");
  assert.equal(macLanguage("tr_TR"), "tr-TR");
  assert.equal(macLanguage("EN-us"), "en-US");
  assert.equal(macLanguage("zh-Hans-CN"), "zh-CN");
  assert.equal(macLanguage("en-GB-oxendict"), "en-GB");
  assert.equal(macLanguage("pt"), "pt");
  assert.equal(macLanguage("sr-Latn"), "sr");
  assert.equal(macLanguage("es-419"), "es");
  assert.equal(macLanguage("yue-CN"), "yue-CN");
});
