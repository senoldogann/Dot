// What a recording of the composer's microphone is made of (item 626), with no page in it: the samples the browser gives
// in blocks of Float32, how loud a block is, the way they are brought down to 16 kHz mono 16-bit, and the WAV file the
// Mac is sent. The Mac reads it with macOS's own dictation (dotagent/voice), which takes a WAV and not what a
// MediaRecorder writes: its formats differ by browser, and WebM is not a format the Mac reads.

/** The rate the recording is sent at, and what the Mac's dictation model works at best. */
export const RECORDING_RATE = 16000;
/** The longest recording the Mac takes: five minutes, and `MAX_SAMPLES` of them (dotagent/voice/wav.py). */
export const MAX_RECORDING_S = 300;
export const MAX_SAMPLES = MAX_RECORDING_S * RECORDING_RATE;

/**
 * The root of the mean of the squares of `block`, a block of samples from -1 to 1: how loud it is, 0 for silence and 1 for
 * a wave as loud as the file can hold.
 * @param {ArrayLike<number>} block
 */
export function loudness(block) {
  if (block.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let index = 0; index < block.length; index += 1) {
    sum += block[index] * block[index];
  }
  return Math.sqrt(sum / block.length);
}

/**
 * A resampler from `inputRate` to `outputRate`, for blocks that come one after another: `push(block)` gives the 16-bit
 * samples that can be made so far, and keeps the rest of the input for the next block. Each output sample is the mean of
 * the input that falls in its stretch of time, which keeps what lies above the new rate from folding down into the voice
 * as a bare pick of every third sample would. The stretches are counted in whole units of 1 / (`inputRate` x `outputRate`)
 * of a second, so that where a stretch ends is never a matter of a float's last bit: the samples, and how many there are,
 * are the same whatever the blocks it was pushed in.
 * @param {number} inputRate
 * @param {number} [outputRate]
 */
export function createResampler(inputRate, outputRate = RECORDING_RATE) {
  // The input not yet used, the number of input samples dropped before it, and how many output samples were made.
  let pending = new Float32Array(0);
  let dropped = 0;
  let made = 0;
  return {
    /** @param {Float32Array} block */
    push(block) {
      const joined = new Float32Array(pending.length + block.length);
      joined.set(pending);
      joined.set(block, pending.length);
      const seen = dropped + joined.length;
      const out = [];
      // Output sample `made` is the stretch [made x inputRate, (made + 1) x inputRate) of the units, and input sample
      // `index` the stretch [index x outputRate, (index + 1) x outputRate): it is whole once the input reaches its end.
      while ((made + 1) * inputRate <= seen * outputRate) {
        const from = made * inputRate;
        const to = from + inputRate;
        let sum = 0;
        for (let index = Math.floor(from / outputRate); index * outputRate < to; index += 1) {
          const shared = Math.min((index + 1) * outputRate, to) - Math.max(index * outputRate, from);
          sum += joined[index - dropped] * shared;
        }
        out.push(toInt16(sum / inputRate));
        made += 1;
      }
      // What lies before the start of the next stretch is not needed again.
      const used = Math.floor((made * inputRate) / outputRate);
      pending = joined.slice(used - dropped);
      dropped = used;
      return Int16Array.from(out);
    },
  };
}

/** A sample from -1 to 1 as 16 bits, past either end held at it. */
function toInt16(sample) {
  const held = Math.max(-1, Math.min(1, sample));
  return Math.round(held < 0 ? held * 32768 : held * 32767);
}

/**
 * `samples` (16-bit mono, at `rate`) as a WAV file: the standard 44-byte header and the samples after it, little-endian.
 * @param {Int16Array} samples
 * @param {number} [rate]
 */
export function wavOf(samples, rate = RECORDING_RATE) {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const write = (at, text) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(at + index, text.charCodeAt(index));
    }
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(44 + index * 2, samples[index], true);
  }
  return bytes;
}

/**
 * The pieces of a recording, one after another, as one list of samples, at most `limit` of them: a recording longer than
 * the Mac takes is cut at its end rather than refused.
 * @param {readonly Int16Array[]} pieces
 * @param {number} [limit]
 */
export function joinedSamples(pieces, limit = MAX_SAMPLES) {
  const total = Math.min(
    limit,
    pieces.reduce((sum, piece) => sum + piece.length, 0),
  );
  const joined = new Int16Array(total);
  let at = 0;
  for (const piece of pieces) {
    if (at >= total) {
      break;
    }
    const part = piece.length > total - at ? piece.subarray(0, total - at) : piece;
    joined.set(part, at);
    at += part.length;
  }
  return joined;
}

/**
 * `bytes` as base64, for the JSON the daemon takes (the way an attachment is sent): in pieces, since a call of
 * `String.fromCharCode` with ten million arguments is more than a browser takes.
 * @param {Uint8Array} bytes
 */
export function base64Of(bytes) {
  const piece = 0x8000;
  let text = "";
  for (let at = 0; at < bytes.length; at += piece) {
    text += String.fromCharCode(...bytes.subarray(at, at + piece));
  }
  return btoa(text);
}
