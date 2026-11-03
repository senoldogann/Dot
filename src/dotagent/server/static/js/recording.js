// The composer's microphone when the page records the voice itself (items 626, 969), beside the dictation of voice.js and
// with the same interface, so that microphone.js takes either. It is the way of every device whose Mac can write down
// (voice.js `listeningWay`): the page opens the microphone (capture.js) and, at the same press, a stream to the Mac
// (voicestream.js), which starts the program that writes down; what the microphone hears goes to the Mac as 16 kHz mono
// 16-bit samples while the owner speaks, and the Mac writes it down meanwhile with macOS's own dictation, on the device.
// The owner's tick ends the stream, and the words go into the box a moment after, after what it held, as a dictation's do
// (voice.js `dictated`). Nothing of the voice goes to Apple's or Google's servers, as a browser's own recognition may.
//
// Nothing here knows the browser: the microphone (`capture`) and the Mac's calls (`mac`) are given, so that the timing
// of a recording, what it sends and where it lets go are the same under a test as in a page.

import {
  PAUSE_MS,
  SILENCE_MS,
  captureOffer,
  captureProblem,
  dictated,
  problemWords,
  sendProblem,
  stopReading,
  voicePlace,
} from "./voice.js";
import { openStream } from "./voicestream.js";
import { MAX_RECORDING_S, MAX_SAMPLES, createResampler, loudness } from "./wav.js";

// How loud a block of the microphone's sound is to count as a sound, as the root of its mean square from 0 to 1 (wav.js
// `loudness`). Measured with the speech of `say` through the page's own capture, a Web Audio stream standing in for the
// microphone (no microphone is opened by a test): its blocks of speech are mostly 0.1 to 0.2, its pauses 0, and the blocks
// where a word begins or ends between 0.003 and 0.03. 0.01 is above a quiet room's noise through the browser's noise
// suppression (not measured: that wants a microphone) and under the soft ends of a word, and on the side of hearing a
// quiet voice: a voice missed throws a dictation away, a noise heard only keeps the pause from ending it so soon. The
// owner's tick has the Mac write the recording down whether or not a sound was heard.
export const LOUD_LEVEL = 0.01;
// How long a sound counts after its last loud block, so that the pause between two words is no silence.
export const HOLD_MS = 400;

/**
 * The language tag the Mac takes for `tag`, which the page's voice may give with a script (``zh-Hans-CN``) or without a
 * region (``pt``): two or three letters of the language, and a region of two letters when there is one.
 * @param {string} tag
 */
export function macLanguage(tag) {
  const parts = String(tag).replace(/_/g, "-").split("-");
  const region = parts.slice(1).find((part) => /^[A-Za-z]{2}$/.test(part));
  return region === undefined ? parts[0].toLowerCase() : `${parts[0].toLowerCase()}-${region.toUpperCase()}`;
}

/**
 * The recording of one box. `language()` gives the tag to write down in and `text()` the words the box holds; `show(value,
 * interimFrom)` puts the words written down on top of them (`interimFrom` is always null: nothing is written down while
 * the owner speaks) and `settled()` says that no word is still the engine's to change; `state(listening)` says whether the
 * microphone is on, and `problem(words, offer)` why it could not be, with, in Dot.app, the `{ allow: pane }` of a refused
 * microphone (item 636, voice.js `captureOffer`). `began()` says the microphone is open, for the recorder's clock,
 * `sound(loud)` whether it hears a sound now, for its wave, and `writing(on)` that the recording is with the Mac, which
 * writes it down. `capture(onBlock, onEnded)` opens the microphone (capture.js `openCapture`) and `mac` is the daemon's
 * calls of a stream (voicestream.js `MacStream`); both are given.
 *
 * `start()` opens the microphone and the stream. `stop()` is the owner's tick (the same when a pause ends it): it lets the
 * microphone go and ends the stream, a voice too quiet to count as a sound included (the Mac says when it heard nothing),
 * and the words go into the box when the Mac answers, which `state(false)` then says. Only a tick before the first block
 * of the microphone's sound came in has nothing to write down, and is dropped. `abort()` drops everything, the stream at
 * the Mac too. One
 * that is not touched ends by itself: `SILENCE_MS` after the microphone opened with no sound at all it says nothing was
 * heard and sends nothing; `PAUSE_MS` after the last sound once there was one it is the owner's tick; the recording ends
 * at the five minutes the Mac takes, as the tick; and with the page put out of sight while it listens it is dropped (the
 * microphone is let go). `end()` is there for the dictation's interface and does nothing: a recording is with the Mac
 * until it answers, or the owner's cross drops it.
 */
export function createRecording({
  language,
  text,
  show,
  settled,
  state,
  problem,
  began = () => {},
  sound = (/** @type {boolean} */ _loud) => {},
  writing = (/** @type {boolean} */ _on) => {},
  capture,
  mac,
}) {
  // What the recording is doing: not at all, opening the microphone (the browser may be asking the owner), listening, or
  // with the Mac.
  let phase = "idle";
  // Which recording this is, counted at each start and each end: what a recording that was left behind still has on its
  // way (the microphone opening, the Mac's answer) is ignored by the next.
  let generation = 0;
  let base = "";
  /** @type {{ rate: number, stop: () => Promise<void> } | null} */
  let microphone = null;
  /** @type {ReturnType<typeof createResampler> | null} */
  let resampler = null;
  // The stream to the Mac, from the press until its words came or it was dropped.
  /** @type {ReturnType<typeof openStream> | null} */
  let stream = null;
  let kept = 0;
  // Whether a sound was heard, how long the recording is in the microphone's own time, until when a sound counts and
  // whether the wave was told there is one now.
  let heard = false;
  let heardMs = 0;
  let loudUntil = 0;
  let loud = false;
  let timer = null;
  let limit = null;
  const place = () => voicePlace(document.documentElement.dataset.shell, navigator.userAgent, navigator.maxTouchPoints);

  function arm() {
    clearTimeout(timer);
    timer = setTimeout(silence, heard ? PAUSE_MS : SILENCE_MS);
  }

  /** The silence is long enough: after a sound the owner is done, and with none there is nothing to write down. */
  function silence() {
    if (heard) {
      stop();
    } else {
      finish(problemWords("no-speech", place()));
    }
  }

  /** The microphone lets go: its tracks end, the audio context closes, what it holds is dropped. */
  function release() {
    const open = microphone;
    microphone = null;
    resampler = null;
    kept = 0;
    clearTimeout(timer);
    clearTimeout(limit);
    if (open !== null) {
      open.stop().catch((error) => console.warn("the microphone of a recording would not close", error));
    }
  }

  /** The recording is over; `words` say why to the owner, or null, with the `offer` that goes with them (voice.js
   * `captureOffer`), and nothing of it is kept. */
  function finish(words, offer = undefined) {
    if (phase === "idle") {
      return;
    }
    const wasWriting = phase === "writing";
    generation += 1;
    phase = "idle";
    release();
    stream?.cancel();
    stream = null;
    if (loud) {
      loud = false;
      sound(false);
    }
    if (wasWriting) {
      writing(false);
    }
    state(false);
    settled();
    if (words !== null) {
      problem(words, offer);
    }
  }

  /** A block of the microphone's sound: how loud it is, and its samples kept at 16 kHz. */
  function heardBlock(mine, block) {
    if (mine !== generation || phase !== "listening" || microphone === null || resampler === null) {
      return;
    }
    heardMs += (block.length / microphone.rate) * 1000;
    const sounding = loudness(block) >= LOUD_LEVEL;
    if (sounding) {
      loudUntil = heardMs + HOLD_MS;
      heard = true;
      arm();
    }
    const isLoud = heardMs <= loudUntil;
    if (isLoud !== loud) {
      loud = isLoud;
      sound(loud);
    }
    const samples = resampler.push(block);
    stream?.push(samples);
    kept += samples.length;
    if (kept >= MAX_SAMPLES) {
      stop();
    }
  }

  /** The browser took the microphone away (a call, another app): what was heard is written down, if any was. */
  function taken(mine) {
    if (mine !== generation || phase !== "listening") {
      return;
    }
    if (heard) {
      stop();
    } else {
      finish("The microphone was taken away while it listened: another app, or a call, may have it. Press it again to go on.");
    }
  }

  function start() {
    if (phase !== "idle") {
      return;
    }
    generation += 1;
    const mine = generation;
    phase = "opening";
    base = text();
    heard = false;
    heardMs = 0;
    loudUntil = 0;
    loud = false;
    kept = 0;
    // A microphone that hears the reading of an answer would have it written into the box.
    stopReading();
    state(true);
    // The program that writes down starts now, while the microphone opens: it is ready by the first word.
    stream = openStream(mac, language(), (error) => lost(mine, error));
    let opening;
    try {
      opening = capture(
        (block) => heardBlock(mine, block),
        () => taken(mine),
      );
    } catch (error) {
      finish(captureProblem(error, place()), captureOffer(error, place()));
      return;
    }
    opening.then(
      (opened) => {
        if (mine !== generation) {
          // Dropped while the browser asked: the microphone it gives now is let go at once.
          opened.stop().catch((error) => console.warn("the microphone of a recording would not close", error));
          return;
        }
        microphone = opened;
        resampler = createResampler(opened.rate);
        phase = "listening";
        began();
        arm();
        limit = setTimeout(stop, MAX_RECORDING_S * 1000);
      },
      (error) => {
        if (mine === generation) {
          finish(captureProblem(error, place()), captureOffer(error, place()));
        }
      },
    );
  }

  /** The stream to the Mac failed while the microphone opened or listened: what is said from now on would be lost, so
   * the recording ends at once and says why. After the tick, the tick's own wait says it. */
  function lost(mine, error) {
    if (mine !== generation || (phase !== "opening" && phase !== "listening")) {
      return;
    }
    console.warn("a recording could not be streamed to the Mac", error);
    finish(sendProblem(error));
  }

  /** The owner's tick: the microphone lets go and the stream ends; the Mac has written down all but its last moment. */
  function stop() {
    if (phase === "opening") {
      // Before the microphone opened (the browser still asks) there is nothing recorded: it is dropped.
      abort();
      return;
    }
    if (phase !== "listening") {
      return;
    }
    const mine = generation;
    // A tick before the first block of the microphone's sound came in (it opened a moment ago) has no samples to write
    // down: it is dropped, as one before the microphone opened is. One after quiet blocks alone, a voice that never
    // counted as a sound, is written down as it is: the owner asked for it, and the Mac says whether it heard anything.
    if (kept === 0 || stream === null) {
      abort();
      return;
    }
    const ending = stream;
    phase = "writing";
    release();
    if (loud) {
      loud = false;
      sound(false);
    }
    writing(true);
    ending.finish().then(
      (words) => {
        if (mine !== generation) {
          return;
        }
        // The stream ended at the Mac: nothing is left there to drop.
        stream = null;
        const said = String(words ?? "").trim();
        if (said === "") {
          finish(problemWords("no-speech", place()));
          return;
        }
        const next = dictated(base, said, "");
        show(next.value, next.interimFrom);
        finish(null);
      },
      (error) => {
        if (mine === generation) {
          // Told to the owner in plain words; the error itself is for whoever looks into it.
          console.warn("a recording could not be written down", error);
          finish(sendProblem(error));
        }
      },
    );
  }

  function abort() {
    finish(null);
  }

  /** The page goes out of sight while the microphone is open: it is let go. A recording already with the Mac goes on. */
  function hidden() {
    if (document.visibilityState === "hidden" && (phase === "opening" || phase === "listening")) {
      abort();
    }
  }
  document.addEventListener("visibilitychange", hidden);

  return {
    listening: () => phase !== "idle",
    start,
    stop,
    abort,
    end() {},
    toggle: () => (phase === "idle" ? start() : stop()),
    dispose() {
      abort();
      document.removeEventListener("visibilitychange", hidden);
    },
  };
}
