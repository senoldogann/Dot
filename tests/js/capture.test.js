// The page's capture of the microphone (item 626), with a Web Audio and a microphone of the test's own: an audio context
// made and resumed in the press, asked once more when iOS puts it aside as it turns to the microphone, an audio worklet
// that hands the page its blocks, and every end of it: the tracks stopped (the light of an iPhone's microphone out), the
// context closed, nothing left to be heard.

import assert from "node:assert/strict";
import { test } from "node:test";

import { canRecord, openCapture } from "../../src/dotagent/server/static/js/capture.js";

/** A microphone's track: live until stopped, and ended by the browser when it takes the microphone away. */
class FakeTrack extends EventTarget {
  constructor() {
    super();
    this.readyState = "live";
  }

  stop() {
    this.readyState = "ended";
  }
}

class FakeStream {
  constructor() {
    this.track = new FakeTrack();
  }

  getTracks() {
    return [this.track];
  }

  getAudioTracks() {
    return [this.track];
  }
}

/** What an audio context does when it is asked to resume: the test sets it for each case. */
let onResume = (context) => {
  context.state = "running";
  return Promise.resolve();
};

class FakeContext {
  static made = [];

  constructor() {
    this.state = "suspended";
    this.sampleRate = 48000;
    this.resumes = 0;
    this.closes = 0;
    this.modules = [];
    this.destination = { name: "destination" };
    this.nodes = [];
    FakeContext.made.push(this);
  }

  get audioWorklet() {
    return { addModule: async (url) => this.modules.push(url) };
  }

  resume() {
    this.resumes += 1;
    return onResume(this);
  }

  async close() {
    this.closes += 1;
    this.state = "closed";
  }

  createGain() {
    const node = { gain: { value: 1 }, connections: [], connect: (to) => node.connections.push(to) };
    this.nodes.push(node);
    return node;
  }

  createMediaStreamSource(stream) {
    const node = { stream, connections: [], connect: (to) => node.connections.push(to) };
    this.nodes.push(node);
    return node;
  }
}

class FakeWorklet {
  static made = [];

  constructor(context, name, options) {
    this.context = context;
    this.name = name;
    this.options = options;
    this.port = { onmessage: null };
    this.connections = [];
    this.disconnected = 0;
    FakeWorklet.made.push(this);
  }

  connect(to) {
    this.connections.push(to);
  }

  disconnect() {
    this.disconnected += 1;
  }
}

/** The microphones the page asked for, and what the browser answers to the next question. */
const microphone = { asked: [], streams: [], refuse: null };
globalThis.window = { AudioContext: FakeContext };
globalThis.AudioWorkletNode = FakeWorklet;
Object.defineProperty(globalThis, "navigator", {
  value: {
    mediaDevices: {
      async getUserMedia(constraints) {
        microphone.asked.push(constraints);
        if (microphone.refuse !== null) {
          throw Object.assign(new Error("the browser would not give the microphone"), { name: microphone.refuse });
        }
        const stream = new FakeStream();
        microphone.streams.push(stream);
        return stream;
      },
    },
  },
  configurable: true,
});

/** Everything as it was: no microphone asked for, no context made, and a context that runs as soon as it is resumed. */
function fresh() {
  microphone.asked = [];
  microphone.streams = [];
  microphone.refuse = null;
  FakeContext.made = [];
  FakeWorklet.made = [];
  onResume = (context) => {
    context.state = "running";
    return Promise.resolve();
  };
}

/** Let what is on its way (the microphone's answer, a promise that settled) arrive. */
const arrive = () => new Promise((resolve) => setImmediate(resolve));

test("the audio context is made and resumed in the press, before the browser has asked for the microphone", async () => {
  fresh();
  let resumedBeforeTheMicrophone = null;
  const getUserMedia = navigator.mediaDevices.getUserMedia;
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    // The press has run this far, and no further: the context exists and was asked to resume.
    resumedBeforeTheMicrophone = FakeContext.made.length === 1 && FakeContext.made[0].resumes === 1;
    return getUserMedia(constraints);
  };
  try {
    const opening = openCapture(() => {});
    assert.equal(FakeContext.made.length, 1);
    assert.equal(FakeContext.made[0].resumes, 1);
    await opening;
  } finally {
    navigator.mediaDevices.getUserMedia = getUserMedia;
  }
  assert.equal(resumedBeforeTheMicrophone, true);
});

test("the microphone is asked for with the browser's own echo cancelling and noise suppression, and its blocks come to the page", async () => {
  fresh();
  const blocks = [];
  const capture = await openCapture((block) => blocks.push(block));
  assert.deepEqual(microphone.asked, [{ audio: { echoCancellation: true, noiseSuppression: true } }]);
  const [context] = FakeContext.made;
  const [worklet] = FakeWorklet.made;
  assert.equal(capture.rate, 48000);
  assert.equal(context.modules.length, 1);
  assert.match(context.modules[0], /\/voicecapture\.js$/);
  assert.equal(worklet.name, "dot-capture");
  assert.deepEqual(worklet.options, { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  // The microphone feeds the worklet, which feeds a gain of zero that goes to the output: nothing is played.
  const source = context.nodes.find((node) => node.stream !== undefined);
  assert.deepEqual(source.connections, [worklet]);
  const [quiet] = context.nodes.filter((node) => node.gain !== undefined);
  assert.equal(quiet.gain.value, 0);
  assert.deepEqual(worklet.connections, [quiet]);
  assert.deepEqual(quiet.connections, [context.destination]);
  // A block the worklet sends reaches the page.
  const block = new Float32Array([0.1, -0.1]);
  worklet.port.onmessage({ data: block });
  assert.deepEqual(blocks, [block]);
  await capture.stop();
});

test("stop lets the microphone go first, then closes the context, once, whatever the number of ends", async () => {
  fresh();
  const blocks = [];
  const capture = await openCapture((block) => blocks.push(block));
  const [context] = FakeContext.made;
  const [worklet] = FakeWorklet.made;
  const [stream] = microphone.streams;
  const ended = capture.stop();
  // The tracks end as the end begins: an iPhone's microphone light goes out before anything awaits.
  assert.equal(stream.track.readyState, "ended");
  assert.equal(worklet.port.onmessage, null);
  assert.equal(worklet.disconnected, 1);
  await ended;
  await capture.stop();
  assert.equal(context.closes, 1);
  assert.equal(context.state, "closed");
  assert.deepEqual(blocks, []);
});

test("a context iOS puts aside as it turns to the microphone is asked to resume once more", async () => {
  fresh();
  const getUserMedia = navigator.mediaDevices.getUserMedia;
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    const stream = await getUserMedia(constraints);
    // iOS changes its audio session as the microphone opens, and puts the page's context aside: the press had resumed it.
    FakeContext.made[0].state = "interrupted";
    return stream;
  };
  try {
    const capture = await openCapture(() => {});
    const [context] = FakeContext.made;
    // Once in the press, once more when the microphone had opened.
    assert.equal(context.resumes, 2);
    assert.equal(context.state, "running");
    assert.equal(capture.rate, 48000);
    assert.equal(FakeWorklet.made.length, 1);
    await capture.stop();
  } finally {
    navigator.mediaDevices.getUserMedia = getUserMedia;
  }
});

test("a context that never runs fails as the page's own error, and lets the microphone and the context go", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  fresh();
  // Safari at times never answers a resume.
  onResume = () => new Promise(() => {});
  const failure = openCapture(() => {}).then(
    () => null,
    (error) => error,
  );
  await arrive();
  t.mock.timers.tick(2000);
  await arrive();
  t.mock.timers.tick(2000);
  const error = await failure;
  assert.equal(error.name, "InvalidStateError");
  assert.match(error.message, /the audio context is suspended, not running/);
  const [context] = FakeContext.made;
  assert.equal(context.resumes, 2);
  assert.equal(context.closes, 1);
  assert.equal(microphone.streams[0].track.readyState, "ended");
  assert.deepEqual(FakeWorklet.made, []);
});

test("a microphone the browser refuses is the browser's own error, and no context is left open", async () => {
  fresh();
  microphone.refuse = "NotAllowedError";
  await assert.rejects(openCapture(() => {}), { name: "NotAllowedError" });
  const [context] = FakeContext.made;
  assert.equal(context.closes, 1);
  assert.deepEqual(microphone.streams, []);
  assert.deepEqual(FakeWorklet.made, []);
});

test("a context that would not resume at all is warned of, not thrown, and the capture goes on if it runs after all", async () => {
  fresh();
  const warnings = [];
  const warn = console.warn;
  console.warn = (...words) => warnings.push(words.join(" "));
  try {
    let first = true;
    onResume = (context) => {
      if (first) {
        first = false;
        context.state = "running";
        return Promise.reject(new Error("resume refused"));
      }
      return Promise.resolve();
    };
    const capture = await openCapture(() => {});
    assert.equal(FakeContext.made[0].state, "running");
    await capture.stop();
  } finally {
    console.warn = warn;
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /would not start/);
});

test("a microphone the browser takes away is told once, and not after the page let it go", async () => {
  fresh();
  let taken = 0;
  const capture = await openCapture(
    () => {},
    () => {
      taken += 1;
    },
  );
  microphone.streams[0].track.dispatchEvent(new Event("ended"));
  assert.equal(taken, 1);
  await capture.stop();
  // The tracks the page stopped end too, and say nothing.
  microphone.streams[0].track.dispatchEvent(new Event("ended"));
  assert.equal(taken, 1);
});

test("a browser can record when it has the microphone, Web Audio and its worklets, and cannot where one is missing", () => {
  fresh();
  assert.equal(canRecord(), true);
  const context = globalThis.window.AudioContext;
  // The prefixed Web Audio of an older Safari counts.
  globalThis.window.AudioContext = undefined;
  globalThis.window.webkitAudioContext = FakeContext;
  assert.equal(canRecord(), true);
  globalThis.window.webkitAudioContext = undefined;
  assert.equal(canRecord(), false);
  // No worklets (an old browser), no worklet node.
  globalThis.window.AudioContext = class {};
  assert.equal(canRecord(), false);
  globalThis.window.AudioContext = context;
  const node = globalThis.AudioWorkletNode;
  globalThis.AudioWorkletNode = undefined;
  assert.equal(canRecord(), false);
  globalThis.AudioWorkletNode = node;
  // An address that is not secure has no microphone to open.
  const devices = navigator.mediaDevices;
  navigator.mediaDevices = undefined;
  assert.equal(canRecord(), false);
  navigator.mediaDevices = {};
  assert.equal(canRecord(), false);
  navigator.mediaDevices = devices;
  assert.equal(canRecord(), true);
});
