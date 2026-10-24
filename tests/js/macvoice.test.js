// What the page knows of the Mac's writing down (item 626), read in the background as the page starts: nothing waits for it,
// a Mac that has not answered is one that cannot, and a Mac that could not be asked leaves the page as it was and says why.
// The API client is the test's own: a read that the test lets come when it wants.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createMacVoice } from "../../src/dotagent/server/static/js/macvoice.js";

/** A daemon the test answers: each `GET` it is asked is kept, and `answer(...)` or `refuse(...)` settles the oldest. */
function daemon() {
  const asked = [];
  return {
    asked,
    get(path) {
      return new Promise((resolve, reject) => asked.push({ path, resolve, reject }));
    },
    answer(body) {
      asked.shift().resolve(body);
    },
    refuse(error) {
      asked.shift().reject(error);
    },
  };
}

/** What the page logs for whoever looks into a failure: kept, so that a test of a failure says nothing to the terminal. */
function logged() {
  const lines = { warn: [], error: [] };
  const was = { warn: console.warn, error: console.error };
  console.warn = (...words) => lines.warn.push(words);
  console.error = (...words) => lines.error.push(words);
  return {
    lines,
    restore() {
      console.warn = was.warn;
      console.error = was.error;
    },
  };
}

test("until the Mac has answered it is one that cannot, and the read waits for nobody", async () => {
  const mac = daemon();
  const voice = createMacVoice(mac.get);
  assert.deepEqual(voice.macVoice(), { transcribe: false, why: null, failed: null, known: false });
  const reading = voice.loadMacVoice();
  // The read is on its way, and the page goes on: what it knows is what it knew.
  assert.deepEqual(mac.asked.map((one) => one.path), ["/voice"]);
  assert.equal(voice.macVoice().known, false);
  assert.equal(voice.macVoice().transcribe, false);
  mac.answer({ transcribe: true, why: null });
  await reading;
  assert.deepEqual(voice.macVoice(), { transcribe: true, why: null, failed: null, known: true });
});

test("what the Mac lacks is kept as it said it, and the listeners are told once the answer is in", async () => {
  const mac = daemon();
  const voice = createMacVoice(mac.get);
  const told = [];
  voice.onMacVoice((answer) => told.push(answer));
  const reading = voice.loadMacVoice();
  assert.deepEqual(told, []);
  mac.answer({ transcribe: false, why: "This Mac needs macOS 26 or later" });
  await reading;
  assert.deepEqual(told, [
    { transcribe: false, why: "This Mac needs macOS 26 or later", failed: null, known: true },
  ]);
  assert.equal(voice.macVoice(), told[0]);
});

test("a Mac that could not be asked leaves the page as it was, says why, logs it, and the read still settles", async () => {
  const log = logged();
  try {
    const mac = daemon();
    const voice = createMacVoice(mac.get);
    const told = [];
    voice.onMacVoice((answer) => told.push(answer));
    const reading = voice.loadMacVoice();
    mac.refuse(Object.assign(new Error("Cannot reach the Dot daemon. Is it running?"), { status: 0 }));
    // It settles: nothing rejects into the page's start, where nobody waits for it.
    await reading;
    assert.deepEqual(voice.macVoice(), {
      transcribe: false,
      why: null,
      failed: "Cannot reach the Dot daemon. Is it running?",
      known: true,
    });
    assert.equal(told.length, 1);
    assert.equal(log.lines.warn.length, 1);
    assert.match(String(log.lines.warn[0][0]), /could not be asked whether it writes recordings down/);
  } finally {
    log.restore();
  }
});

test("a read later than the first replaces the answer, and the listeners that were stopped hear nothing", async () => {
  const mac = daemon();
  const voice = createMacVoice(mac.get);
  const kept = [];
  const stopped = [];
  voice.onMacVoice((answer) => kept.push(answer.transcribe));
  const stop = voice.onMacVoice((answer) => stopped.push(answer.transcribe));
  const first = voice.loadMacVoice();
  mac.answer({ transcribe: false, why: "Install Apple's developer tools on the Mac: xcode-select --install" });
  await first;
  stop();
  // The owner installed the tools and the page read again (a retry after the daemon was out of reach).
  const second = voice.loadMacVoice();
  mac.answer({ transcribe: true, why: null });
  await second;
  assert.deepEqual(kept, [false, true]);
  assert.deepEqual(stopped, [false]);
  assert.equal(voice.macVoice().transcribe, true);
  assert.equal(voice.macVoice().why, null);
});

test("a listener that fails is logged and does not keep the others from hearing the answer", async () => {
  const log = logged();
  try {
    const mac = daemon();
    const voice = createMacVoice(mac.get);
    const heard = [];
    voice.onMacVoice(() => {
      throw new Error("a listener of the card failed");
    });
    voice.onMacVoice((answer) => heard.push(answer.transcribe));
    const reading = voice.loadMacVoice();
    mac.answer({ transcribe: true, why: null });
    await reading;
    assert.deepEqual(heard, [true]);
    assert.equal(log.lines.error.length, 1);
    assert.match(String(log.lines.error[0][0]), /a listener of the Mac's answer failed/);
  } finally {
    log.restore();
  }
});
