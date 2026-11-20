// The frames of Watch's stream (item 1264), as dotagent/desktop/live.py writes them: split however the network cut the
// bytes, a key frame's decoder configuration taken off its front, and the codec named from it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { codecOf, splitFrames, splitKey } from "../../src/dotagent/server/static/js/livestream.js";

/** One frame on the wire: kind, the length of the payload, its time in microseconds (big-endian), then the payload. */
function frame(kind, timestamp, payload) {
  const bytes = new Uint8Array(13 + payload.length);
  const view = new DataView(bytes.buffer);
  view.setUint8(0, kind);
  view.setUint32(1, payload.length);
  view.setBigUint64(5, BigInt(timestamp));
  bytes.set(payload, 13);
  return bytes;
}

test("the frames are split out of the bytes, and the start of one still coming is kept", () => {
  const first = frame(1, 1_000_000, new Uint8Array([1, 2, 3]));
  const second = frame(2, 1_016_666, new Uint8Array([4, 5]));
  const bytes = new Uint8Array([...first, ...second.subarray(0, 7)]);
  const { frames, rest } = splitFrames(bytes);
  assert.equal(frames.length, 1);
  assert.deepEqual({ kind: frames[0].kind, timestamp: frames[0].timestamp, payload: [...frames[0].payload] }, { kind: 1, timestamp: 1_000_000, payload: [1, 2, 3] });
  assert.deepEqual([...rest], [...second.subarray(0, 7)]);
  const next = splitFrames(new Uint8Array([...rest, ...second.subarray(7)]));
  assert.deepEqual(next.frames.map((one) => [one.kind, one.timestamp, [...one.payload]]), [[2, 1_016_666, [4, 5]]]);
  assert.equal(next.rest.length, 0);
});

test("a key frame's configuration comes off its front, and names its codec", () => {
  const avcc = [0x01, 0x64, 0x00, 0x32, 0xff];
  const { avcc: config, data } = splitKey(new Uint8Array([0, avcc.length, ...avcc, 9, 8]));
  assert.deepEqual([...config], avcc);
  assert.deepEqual([...data], [9, 8]);
  assert.equal(codecOf(config), "avc1.640032");
});
