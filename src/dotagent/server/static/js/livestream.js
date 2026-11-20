// The frames of Watch's stream (item 1264), as dotagent/desktop/live.py writes them: what views/watch.js reads out of
// the bytes the network hands it, before the browser's decoder takes them.

export const KEY = 1;
const HEADER_BYTES = 13;

/** The codec string of an `avcC` record: profile, compatibility and level, as WebCodecs names H.264. */
export function codecOf(avcc) {
  return `avc1.${[avcc[1], avcc[2], avcc[3]].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * Split the bytes read so far into the stream's frames: each `{ kind, timestamp, payload }`, and what is left over, the
 * start of a frame still coming. A frame is 13 bytes of header (kind, the length after it, its time in microseconds;
 * big-endian) and its payload.
 */
export function splitFrames(bytes) {
  const frames = [];
  let at = 0;
  while (bytes.length - at >= HEADER_BYTES) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + at, HEADER_BYTES);
    const length = view.getUint32(1);
    if (bytes.length - at - HEADER_BYTES < length) {
      break;
    }
    const start = at + HEADER_BYTES;
    frames.push({ kind: view.getUint8(0), timestamp: Number(view.getBigUint64(5)), payload: bytes.subarray(start, start + length) });
    at = start + length;
  }
  return { frames, rest: bytes.subarray(at) };
}

/** A key frame's payload: the decoder's configuration (`avcC`, after its 2-byte length) and the frame. */
export function splitKey(payload) {
  const length = new DataView(payload.buffer, payload.byteOffset, 2).getUint16(0);
  return { avcc: payload.slice(2, 2 + length), data: payload.subarray(2 + length) };
}

/** `first` and `second` as one run of bytes. */
export function joined(first, second) {
  if (first.length === 0) {
    return second;
  }
  const bytes = new Uint8Array(first.length + second.length);
  bytes.set(first);
  bytes.set(second, first.length);
  return bytes;
}
