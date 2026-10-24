// The microphone as the page records it itself (item 626), for where the browser's speech recognition cannot be trusted:
// Safari on an iPhone hears the first dictation of a page and at times none after it (WebKit bugs 317741 and 321436),
// while `getUserMedia` and Web Audio work every time. The samples come to the page as blocks from an audio worklet
// (voicecapture.js, which the page's CSP allows as a script of its own origin); recording.js keeps them, wav.js makes the
// file the Mac is sent.

// The worklet, beside this file.
const WORKLET = new URL("./voicecapture.js", import.meta.url).href;
// How long a context that was asked to start may take before the page says it did not (Safari at times never answers).
const START_MS = 2000;

/** The browser's Web Audio, or null where it has none. Safari before 14.1 has it under a prefix. */
function audioContextClass() {
  return window.AudioContext ?? window.webkitAudioContext ?? null;
}

/**
 * Whether this browser can record the microphone itself: `getUserMedia`, Web Audio and its worklets. A page served from an
 * address that is not secure (not https, not localhost) has no `navigator.mediaDevices`, and cannot.
 */
export function canRecord() {
  const Context = audioContextClass();
  return (
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    Context !== null &&
    "audioWorklet" in Context.prototype &&
    typeof AudioWorkletNode === "function"
  );
}

/** `promise`, or nothing once `START_MS` have passed: a context that was asked to start at times never answers. */
function within(promise) {
  let timer = null;
  const late = new Promise((resolve) => {
    timer = setTimeout(resolve, START_MS);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

/**
 * Wait until `context` runs. The press resumed it (`resumed`); iOS may put it aside again as it turns to the microphone
 * (the audio session changes), and a context that is not running then is asked once more, whatever the first answer was.
 * Throws an `InvalidStateError` for one that still does not run: nothing of the microphone's sound would reach the page.
 */
async function untilRunning(context, resumed) {
  await within(resumed);
  if (context.state !== "running") {
    await within(context.resume().catch((error) => console.warn("the audio context of a recording would not start", error)));
  }
  if (context.state !== "running") {
    throw Object.assign(new Error(`the audio context is ${context.state}, not running`), { name: "InvalidStateError" });
  }
}

/**
 * Open the microphone and give `onBlock(samples)` each block of its sound, mono, as Float32 from -1 to 1, at the rate of
 * the returned `{ rate, stop() }`. Call it in the press that asks for the microphone, with nothing before it: iOS starts
 * an audio context only in a touch, so the context is made and resumed here, before the browser asks for the microphone
 * (it may ask for as long as the owner reads), and not after. `stop()` lets the microphone go (its tracks end, which puts
 * the light of an iPhone out), closes the context and drops what it holds; it returns what closing the context returns.
 * `onEnded()` is called when the browser takes the microphone away (a call, another app). Rejects with the browser's own
 * error (`NotAllowedError`, `NotFoundError`, ...) or an `InvalidStateError` of the page's own (the audio would not start),
 * the microphone let go.
 *
 * @param {(samples: Float32Array) => void} onBlock
 * @param {() => void} [onEnded]
 * @returns {Promise<{ rate: number, stop: () => Promise<void> }>}
 */
export function openCapture(onBlock, onEnded = () => {}) {
  const Context = audioContextClass();
  const context = new Context();
  const resumed = context.resume().catch((error) => console.warn("the audio context of a recording would not start", error));
  /** @type {MediaStream | null} */
  let stream = null;
  /** @type {AudioWorkletNode | null} */
  let node = null;
  let done = false;

  /** Let everything go: the tracks first, so that the microphone's light goes out at once. */
  async function stop() {
    if (done) {
      return;
    }
    done = true;
    if (node !== null) {
      node.port.onmessage = null;
      node.disconnect();
    }
    for (const track of stream?.getTracks() ?? []) {
      track.stop();
    }
    await context.close();
  }

  return (async () => {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      for (const track of stream.getAudioTracks()) {
        track.addEventListener("ended", () => {
          if (!done) {
            onEnded();
          }
        });
      }
      await untilRunning(context, resumed);
      await context.audioWorklet.addModule(WORKLET);
      node = new AudioWorkletNode(context, "dot-capture", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      node.port.onmessage = (event) => onBlock(event.data);
      // The node plays nothing: a gain of zero keeps the graph pulled to the output without a sound.
      const quiet = context.createGain();
      quiet.gain.value = 0;
      context.createMediaStreamSource(stream).connect(node);
      node.connect(quiet);
      quiet.connect(context.destination);
      return { rate: context.sampleRate, stop };
    } catch (error) {
      await stop().catch((closing) => console.warn("the audio context of a recording would not close", closing));
      throw error;
    }
  })();
}
