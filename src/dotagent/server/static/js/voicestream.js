// The stream of a recording to the Mac (item 969): the page sends what the microphone hears in pieces while the owner
// speaks, so that the Mac writes it down meanwhile and its words come a moment after the tick. The stream is opened as
// the microphone is pressed, before it has opened, so that the program that writes down is ready by the first word.
//
// Nothing here knows the daemon: its four calls are given (macvoice.js `macStream`), so that a test drives the stream
// with its own.

import { MAX_SAMPLES, RECORDING_RATE, base64Of, joinedSamples, pcmOf } from "./wav.js";

// A piece goes once it holds this much sound, a quarter of a second, or at the tick with what is left; what came while
// one was on its way goes in the next, two seconds at the most in one (dotagent/voice/wav.py `MAX_PIECE_BYTES`).
export const PIECE_SAMPLES = RECORDING_RATE / 4;
export const MOST_PIECE_SAMPLES = 2 * RECORDING_RATE;

/**
 * @typedef {object} MacStream The daemon's calls of a stream (`POST /api/voice/streams` and the rest).
 * @property {(language: string) => Promise<string>} open the stream's name
 * @property {(name: string, piece: number, data: string) => Promise<void>} feed a piece of base64 samples
 * @property {(name: string) => Promise<string>} end the words
 * @property {(name: string) => Promise<void>} drop
 */

/**
 * A stream in `language` to the Mac by `mac`'s calls, opened now. `push(samples)` gives it 16 kHz 16-bit samples as they
 * are recorded; `finish()` sends what is left, ends the stream and resolves to the words, or rejects with the first
 * error of any call (a piece that could not be sent loses the recording: the Mac would write down a recording with a
 * hole in it); `cancel()` drops it, at the Mac too, and nothing more is sent.
 * @param {MacStream} mac
 * @param {string} language
 */
export function openStream(mac, language) {
  /** @type {Int16Array[]} */
  let pending = [];
  let pendingSamples = 0;
  let sent = 0;
  let piece = 0;
  /** @type {string | null} */
  let name = null;
  /** @type {Promise<void> | null} */
  let flight = null;
  /** @type {Error | null} */
  let failed = null;
  let ending = false;
  let cancelled = false;

  const opening = mac.open(language).then(
    (opened) => {
      name = opened;
      if (cancelled) {
        dropAtTheMac(opened);
        return;
      }
      pump();
    },
    (error) => {
      failed = error;
    },
  );

  function dropAtTheMac(opened) {
    mac.drop(opened).catch((error) => console.warn("a stream to the Mac could not be dropped", error));
  }

  /** Send the next piece when none is on its way and enough waits (or the tick came). */
  function pump() {
    if (name === null || flight !== null || failed !== null || cancelled || pendingSamples === 0) {
      return;
    }
    if (!ending && pendingSamples < PIECE_SAMPLES) {
      return;
    }
    const all = joinedSamples(pending, pendingSamples);
    const samples = all.subarray(0, MOST_PIECE_SAMPLES);
    const rest = all.subarray(samples.length);
    pending = rest.length === 0 ? [] : [rest];
    pendingSamples = rest.length;
    const number = piece;
    piece += 1;
    flight = mac.feed(name, number, base64Of(pcmOf(samples))).then(
      () => {
        flight = null;
        pump();
      },
      (error) => {
        flight = null;
        failed = error;
      },
    );
  }

  return {
    /** @param {Int16Array} samples */
    push(samples) {
      // Five minutes are the most the Mac takes: what goes past them is cut off.
      const taken = samples.subarray(0, Math.max(0, MAX_SAMPLES - sent));
      if (ending || cancelled || taken.length === 0) {
        return;
      }
      sent += taken.length;
      pending.push(taken);
      pendingSamples += taken.length;
      pump();
    },
    async finish() {
      ending = true;
      await opening;
      for (;;) {
        if (failed !== null) {
          throw failed;
        }
        if (cancelled || name === null) {
          throw new Error("the stream to the Mac was dropped");
        }
        if (flight !== null) {
          await flight;
          continue;
        }
        if (pendingSamples > 0) {
          pump();
          continue;
        }
        break;
      }
      return mac.end(name);
    },
    cancel() {
      if (cancelled) {
        return;
      }
      cancelled = true;
      if (name !== null) {
        dropAtTheMac(name);
      }
    },
  };
}
