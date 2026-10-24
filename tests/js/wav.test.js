// What a recording of the composer's microphone is made of (item 626), read by capture.js and recording.js: how loud a
// block of the microphone's sound is, how the blocks are brought down to 16 kHz mono 16-bit, and the WAV file the Mac is
// sent. Pure functions, so they run under Node without a page.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_RECORDING_S,
  MAX_SAMPLES,
  RECORDING_RATE,
  base64Of,
  createResampler,
  joinedSamples,
  loudness,
  wavOf,
} from "../../src/dotagent/server/static/js/wav.js";

/** `count` samples of a sine wave of `frequency` hertz at `rate`, `amplitude` from 0 to 1. */
function sine(frequency, rate, count, amplitude = 0.5) {
  return Float32Array.from({ length: count }, (_, index) => amplitude * Math.sin((2 * Math.PI * frequency * index) / rate));
}

/** What the resampler makes of `input` pushed in blocks of `size`. */
function resampled(input, inputRate, size) {
  const resampler = createResampler(inputRate);
  const pieces = [];
  for (let at = 0; at < input.length; at += size) {
    pieces.push(resampler.push(input.subarray(at, at + size)));
  }
  return Int16Array.from(pieces.flatMap((piece) => [...piece]));
}

test("how loud a block is: nothing for a silence, the wave's height over the root of two for a sine, 1 for a full square", () => {
  assert.equal(loudness(new Float32Array(0)), 0);
  assert.equal(loudness(new Float32Array(2048)), 0);
  assert.equal(loudness(Float32Array.from({ length: 100 }, (_, index) => (index % 2 === 0 ? 1 : -1))), 1);
  assert.ok(Math.abs(loudness(sine(440, 48000, 48000, 0.5)) - 0.5 / Math.SQRT2) < 0.001);
  assert.ok(Math.abs(loudness(sine(440, 48000, 48000, 0.02)) - 0.02 / Math.SQRT2) < 0.0005);
});

test("the WAV file has the standard 44-byte header of 16 kHz mono 16-bit sound, and the samples after it, little-endian", () => {
  const samples = Int16Array.from([0, 1, -1, 32767, -32768, 258]);
  const bytes = wavOf(samples);
  const view = new DataView(bytes.buffer);
  const text = (at, length) => String.fromCharCode(...bytes.subarray(at, at + length));
  assert.equal(bytes.length, 44 + samples.length * 2);
  assert.equal(text(0, 4), "RIFF");
  assert.equal(view.getUint32(4, true), bytes.length - 8);
  assert.equal(text(8, 8), "WAVEfmt ");
  assert.deepEqual(
    [16, 20, 22, 24, 28, 32, 34].map((at) => (at === 16 || at === 24 || at === 28 ? view.getUint32(at, true) : view.getUint16(at, true))),
    [16, 1, 1, RECORDING_RATE, RECORDING_RATE * 2, 2, 16],
  );
  assert.equal(text(36, 4), "data");
  assert.equal(view.getUint32(40, true), samples.length * 2);
  assert.deepEqual([...bytes.subarray(44)], [0, 0, 1, 0, 255, 255, 255, 127, 0, 128, 2, 1]);
  assert.deepEqual(
    Array.from({ length: samples.length }, (_, index) => view.getInt16(44 + index * 2, true)),
    [...samples],
  );
  // A header for another rate says it, and a recording of nothing is the header alone.
  assert.equal(new DataView(wavOf(samples, 8000).buffer).getUint32(24, true), 8000);
  assert.equal(wavOf(new Int16Array(0)).length, 44);
});

test("a constant sound stays as loud, a sample in three or in 2.75 is the mean of what falls in its stretch", () => {
  const half = new Float32Array(4800).fill(0.5);
  const down = resampled(half, 48000, 4800);
  assert.equal(down.length, 1600);
  assert.ok(down.every((sample) => sample === 16384), "a constant 0.5 is 16384 everywhere");
  // 44.1 kHz does not divide by 16: the stretches have parts of samples at their ends, and the sum is still the mean.
  const odd = resampled(new Float32Array(44100).fill(-0.25), 44100, 1000);
  assert.ok(odd.length >= 15999 && odd.length <= 16000, `${odd.length} samples`);
  assert.ok(odd.every((sample) => Math.abs(sample + 8192) <= 1), "a constant -0.25 is -8192 everywhere");
  // Nothing past full scale: it is held there.
  assert.deepEqual([...resampled(Float32Array.from([2, 2, 2, -2, -2, -2]), 48000, 6)], [32767, -32768]);
});

test("the samples are the same whatever blocks the sound came in", () => {
  const input = Float32Array.from({ length: 48000 }, (_, index) => Math.sin(index * 0.01) * Math.cos(index * 0.0007) * 0.8);
  const whole = resampled(input, 48000, input.length);
  for (const size of [1, 7, 128, 2048, 5000]) {
    const blocks = resampled(input, 48000, size);
    assert.equal(blocks.length, whole.length, `blocks of ${size}`);
    assert.deepEqual([...blocks], [...whole], `blocks of ${size}`);
  }
  const odd = Float32Array.from({ length: 44100 }, (_, index) => Math.sin(index * 0.013) * 0.6);
  const oddWhole = resampled(odd, 44100, odd.length);
  for (const size of [3, 100, 2048]) {
    const blocks = resampled(odd, 44100, size);
    assert.equal(blocks.length, oddWhole.length, `44.1 kHz in blocks of ${size}`);
    assert.deepEqual([...blocks], [...oddWhole], `44.1 kHz in blocks of ${size}`);
  }
});

test("a block shorter than a stretch makes nothing and is kept for the next", () => {
  const resampler = createResampler(48000);
  const made = [];
  for (let index = 0; index < 6; index += 1) {
    made.push(...resampler.push(Float32Array.from([0.5])));
  }
  assert.deepEqual(made, [16384, 16384]);
});

test("a tone keeps its pitch and height, and a sound above the new rate is not folded down at full strength", () => {
  const rate = 44100;
  const voice = resampled(sine(440, rate, rate), rate, 2048);
  let crossings = 0;
  for (let index = 1; index < voice.length; index += 1) {
    if (voice[index - 1] < 0 && voice[index] >= 0) {
      crossings += 1;
    }
  }
  // 440 hertz for a second: 440 rises through zero.
  assert.ok(Math.abs(crossings - 440) <= 2, `${crossings} cycles`);
  const peak = Math.max(...voice.map(Math.abs));
  assert.ok(peak > 0.97 * 0.5 * 32767 && peak <= 0.5 * 32767 + 1, `peak ${peak}`);
  // 12 kHz at 48 kHz is above half of 16 kHz: picking every third sample would keep all of it as a 4 kHz tone; the mean of
  // the three keeps a third.
  const high = sine(12000, 48000, 48000, 0.5);
  const folded = resampled(high, 48000, 2048);
  const rms = Math.sqrt([...folded].reduce((sum, sample) => sum + (sample / 32768) ** 2, 0) / folded.length);
  assert.ok(rms < 0.4 * loudness(high), `${rms} against ${loudness(high)}`);
});

test("the pieces of a recording are joined, and cut at the five minutes the Mac takes", () => {
  assert.equal(MAX_RECORDING_S, 300);
  assert.equal(MAX_SAMPLES, 300 * 16000);
  assert.equal(joinedSamples([]).length, 0);
  assert.deepEqual([...joinedSamples([Int16Array.from([1, 2]), new Int16Array(0), Int16Array.from([3])])], [1, 2, 3]);
  assert.deepEqual([...joinedSamples([Int16Array.from([1, 2, 3]), Int16Array.from([4, 5, 6])], 4)], [1, 2, 3, 4]);
  assert.deepEqual([...joinedSamples([Int16Array.from([1, 2, 3])], 2)], [1, 2]);
  const long = joinedSamples([new Int16Array(MAX_SAMPLES), new Int16Array(RECORDING_RATE * 10)]);
  assert.equal(long.length, MAX_SAMPLES);
});

test("bytes become base64 as the daemon reads it, of any length, in pieces a browser takes", () => {
  for (const length of [0, 1, 2, 3, 4, 100, 0x8000 - 1, 0x8000, 0x8000 + 1, 0x10000 + 5]) {
    const bytes = Uint8Array.from({ length }, (_, index) => (index * 31 + 7) % 256);
    assert.equal(base64Of(bytes), Buffer.from(bytes).toString("base64"), `${length} bytes`);
  }
  // A recording of five minutes is nearly ten million bytes.
  const big = Uint8Array.from({ length: 9_600_044 }, (_, index) => index % 251);
  const text = base64Of(big);
  assert.equal(text.length, Math.ceil(big.length / 3) * 4);
  assert.equal(text.slice(0, 40), Buffer.from(big.subarray(0, 30)).toString("base64"));
});
