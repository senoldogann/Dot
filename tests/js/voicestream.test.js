// The stream of a recording to the Mac (item 969), with a Mac of the test's own: the pieces go in their order, a quarter
// of a second at the least and two seconds at the most, the tick sends what is left before it ends the stream, a piece
// that could not be sent loses the recording, and a stream dropped before it opened is dropped at the Mac once it has.

import assert from "node:assert/strict";
import { test } from "node:test";

import { MOST_PIECE_SAMPLES, PIECE_SAMPLES, openStream } from "../../src/dotagent/server/static/js/voicestream.js";

const arrive = () => new Promise((resolve) => setImmediate(resolve));

/** A Mac that keeps the calls it was given: `open` answers when `opened()` is called; `feed` fails with `failing`. */
function macOf() {
  const mac = { calls: [], failing: null, opened: () => {} };
  const opening = new Promise((resolve) => {
    mac.opened = () => resolve("stream-1");
  });
  mac.stream = {
    open: (language) => {
      mac.calls.push(["open", language]);
      return opening;
    },
    feed: async (name, piece, data) => {
      mac.calls.push(["feed", name, piece, Buffer.from(data, "base64").length / 2]);
      if (mac.failing !== null) {
        throw mac.failing;
      }
    },
    end: async (name) => {
      mac.calls.push(["end", name]);
      return "merhaba";
    },
    drop: async (name) => {
      mac.calls.push(["drop", name]);
    },
  };
  return mac;
}

test("the pieces go in their order once a quarter second waits, two seconds at the most, and the tick sends the rest", async () => {
  const mac = macOf();
  const stream = openStream(mac.stream, "tr-TR", (error) => mac.calls.push(["failed", error.message]));
  // Sound recorded while the stream opens waits for it.
  stream.push(new Int16Array(3 * MOST_PIECE_SAMPLES));
  assert.deepEqual(mac.calls, [["open", "tr-TR"]]);
  mac.opened();
  await arrive();
  stream.push(new Int16Array(PIECE_SAMPLES - 1));
  await arrive();
  assert.deepEqual(mac.calls.slice(1), [
    ["feed", "stream-1", 0, MOST_PIECE_SAMPLES],
    ["feed", "stream-1", 1, MOST_PIECE_SAMPLES],
    ["feed", "stream-1", 2, MOST_PIECE_SAMPLES],
  ]);
  assert.equal(await stream.finish(), "merhaba");
  assert.deepEqual(mac.calls.slice(4), [
    ["feed", "stream-1", 3, PIECE_SAMPLES - 1],
    ["end", "stream-1"],
  ]);
});

test("a piece that could not be sent loses the recording: it is said at once, and the tick ends nothing", async () => {
  const mac = macOf();
  const stream = openStream(mac.stream, "tr-TR", (error) => mac.calls.push(["failed", error.message]));
  mac.opened();
  mac.failing = new Error("the Mac went away");
  stream.push(new Int16Array(PIECE_SAMPLES));
  await arrive();
  // Said at once, while the owner may still speak, and once; nothing more is sent.
  assert.deepEqual(mac.calls.at(-1), ["failed", "the Mac went away"]);
  stream.push(new Int16Array(PIECE_SAMPLES));
  await arrive();
  assert.equal(mac.calls.filter(([call]) => call === "feed").length, 1);
  await assert.rejects(stream.finish(), /the Mac went away/);
  assert.equal(mac.calls.filter(([call]) => call === "end").length, 0);
  assert.equal(mac.calls.filter(([call]) => call === "failed").length, 1);
});

test("a stream dropped before it opened is dropped at the Mac once it has, and is sent nothing", async () => {
  const mac = macOf();
  const stream = openStream(mac.stream, "tr-TR", (error) => mac.calls.push(["failed", error.message]));
  stream.push(new Int16Array(PIECE_SAMPLES));
  stream.cancel();
  stream.cancel();
  mac.opened();
  await arrive();
  assert.deepEqual(mac.calls, [
    ["open", "tr-TR"],
    ["drop", "stream-1"],
  ]);
  await assert.rejects(stream.finish(), /dropped/);
});
