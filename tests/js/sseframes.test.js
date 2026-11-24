import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CONNECT_TIMEOUT_MS,
  createParser,
  dropped,
  IDLE_TIMEOUT_MS,
  RECONNECT_WAITS_MS,
  reconnectWait,
} from "../../src/dotagent/server/static/js/sseframes.js";

/** What the daemon writes: a comment at once, two events, a keepalive, a live piece that has no id. */
const STREAM =
  ': open\n\nid: 7\nevent: tool_call\ndata: {"id":7,"kind":"tool_call"}\n\n: keepalive\n\n' +
  'event: reply_delta\ndata: {"kind":"reply_delta","text":"Hel"}\n\nid: 8\nevent: tool_result\ndata: {"id":8}\n\n';

const EXPECTED = [
  { id: 7, event: "tool_call", data: { id: 7, kind: "tool_call" } },
  { id: 0, event: "reply_delta", data: { kind: "reply_delta", text: "Hel" } },
  { id: 8, event: "tool_result", data: { id: 8 } },
];

/** The frames a parser gives for `chunks`, read one after the other. */
function read(chunks) {
  const parser = createParser();
  return [...chunks.flatMap((chunk) => parser.push(chunk)), ...parser.end()];
}

test("a stream read whole gives its events, and its comments give none", () => {
  assert.deepEqual(read([STREAM]), EXPECTED);
});

test("a stream cut anywhere gives the same events: every split point of it", () => {
  for (let at = 0; at <= STREAM.length; at += 1) {
    assert.deepEqual(read([STREAM.slice(0, at), STREAM.slice(at)]), EXPECTED, `split at ${at}`);
  }
});

test("a stream that comes a character at a time gives the same events", () => {
  assert.deepEqual(read([...STREAM]), EXPECTED);
});

test("lines may end in CRLF or in CR, and a CRLF cut between two chunks is one line end", () => {
  const crlf = STREAM.replaceAll("\n", "\r\n");
  assert.deepEqual(read([crlf]), EXPECTED);
  assert.deepEqual(read([STREAM.replaceAll("\n", "\r")]), EXPECTED);
  for (let at = 0; at <= crlf.length; at += 1) {
    assert.deepEqual(read([crlf.slice(0, at), crlf.slice(at)]), EXPECTED, `crlf split at ${at}`);
  }
});

test("a comment line, as the daemon's keepalive is, is no event and does not end one that is begun", () => {
  assert.deepEqual(read([": keepalive\n\n"]), []);
  assert.deepEqual(read([": open\n\n: keepalive\n\n: keepalive\n\n"]), []);
  // A comment inside a frame is read past.
  assert.deepEqual(read(['id: 3\n: keepalive\ndata: {"a":1}\n\n']), [{ id: 3, event: "message", data: { a: 1 } }]);
});

test("the lines of a frame's data are joined by a newline before they are read", () => {
  assert.deepEqual(read(['data: {"a":\ndata: 1}\n\n']), [{ id: 0, event: "message", data: { a: 1 } }]);
  assert.deepEqual(read(['data: {"text":"a\\nb"}\n\n']), [{ id: 0, event: "message", data: { text: "a\nb" } }]);
});

test("an id is a number; a frame with none, or with one that is not, has id 0, and ids do not carry over", () => {
  assert.deepEqual(
    read(['id: 12\ndata: {"n":1}\n\ndata: {"n":2}\n\nid: x\ndata: {"n":3}\n\nid:\ndata: {"n":4}\n\n']).map((frame) => frame.id),
    [12, 0, 0, 0],
  );
});

test("the event name is `message` when the frame names none, and one space after the colon is dropped", () => {
  assert.equal(read(['data: {"a":1}\n\n'])[0].event, "message");
  assert.equal(read(['event: run_finished\ndata: {"a":1}\n\n'])[0].event, "run_finished");
  assert.equal(read(['event:run_finished\ndata:{"a":1}\n\n'])[0].event, "run_finished");
  assert.equal(read(['event:  two\ndata: {"a":1}\n\n'])[0].event, " two");
});

test("a frame with no data, a field the page does not know and a line with no colon change nothing", () => {
  assert.deepEqual(read(["event: ping\n\n"]), []);
  assert.deepEqual(read(['retry: 3000\ndata: {"a":1}\n\n']), [{ id: 0, event: "message", data: { a: 1 } }]);
  assert.deepEqual(read(['nonsense\ndata: {"a":1}\n\n']), [{ id: 0, event: "message", data: { a: 1 } }]);
});

test("data that is not JSON is a frame told of, not a break of the stream, and the next frame is read", () => {
  const frames = read(["data: not json\n\n", 'data: {"a":1}\n\n']);
  assert.equal(frames.length, 2);
  assert.equal(frames[0].bad, "not json");
  assert.equal("data" in frames[0], false);
  assert.deepEqual(frames[1].data, { a: 1 });
});

test("a byte order mark at the start of the stream is not part of its first line", () => {
  assert.deepEqual(read(['﻿data: {"a":1}\n\n']), [{ id: 0, event: "message", data: { a: 1 } }]);
});

test("a frame cut by a drop is not an event, and is known to be cut", () => {
  const parser = createParser();
  assert.deepEqual(parser.push('id: 9\nevent: tool_call\ndata: {"id":9,"ki'), []);
  assert.equal(parser.pending(), true);
  // The next connection starts from the event after the last whole one: what was cut is forgotten.
  parser.reset();
  assert.equal(parser.pending(), false);
  assert.deepEqual(parser.push('id: 9\nevent: tool_call\ndata: {"id":9}\n\n'), [{ id: 9, event: "tool_call", data: { id: 9 } }]);
});

test("the end of a stream gives the line a held CR ended, and never a frame that is cut", () => {
  const parser = createParser();
  assert.deepEqual(parser.push('data: {"a":1}\r\r'), []);
  assert.deepEqual(parser.end(), [{ id: 0, event: "message", data: { a: 1 } }]);
  const cut = createParser();
  cut.push('data: {"a":1}\r');
  assert.deepEqual(cut.end(), []);
  assert.equal(cut.pending(), true);
});

test("a stream that ends between two frames has nothing cut", () => {
  const parser = createParser();
  parser.push(STREAM);
  assert.equal(parser.pending(), false);
  parser.push(": keepalive\n\n");
  assert.equal(parser.pending(), false);
});

test("a drop in the middle of a line is cut as well", () => {
  const parser = createParser();
  parser.push("id: 4");
  assert.equal(parser.pending(), true);
});

test("a long stream of events read in odd pieces gives every event once and in order", () => {
  let text = "";
  for (let id = 1; id <= 300; id += 1) {
    text += `id: ${id}\nevent: tool_result\ndata: {"id":${id},"x":"${"é".repeat(id % 7)}"}\n\n`;
    if (id % 10 === 0) {
      text += ": keepalive\n\n";
    }
  }
  const chunks = [];
  for (let at = 0; at < text.length; at += 37) {
    chunks.push(text.slice(at, at + 37));
  }
  const frames = read(chunks);
  assert.equal(frames.length, 300);
  assert.deepEqual(
    frames.map((frame) => frame.id),
    Array.from({ length: 300 }, (_, index) => index + 1),
  );
});

test("a stream is made again after 0.5, 1, 2, 4, 8 and then 15 seconds", () => {
  assert.deepEqual(RECONNECT_WAITS_MS, [500, 1000, 2000, 4000, 8000, 15_000]);
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 40].map(reconnectWait), [500, 1000, 2000, 4000, 8000, 15_000, 15_000, 15_000]);
  assert.equal(reconnectWait(-3), 500);
});

test("a connection that answered nothing in 8 seconds, and a stream that said nothing in 35, are dropped", () => {
  assert.equal(CONNECT_TIMEOUT_MS, 8000);
  assert.equal(IDLE_TIMEOUT_MS, 35_000);
  assert.equal(dropped(0, 7999, false), false);
  assert.equal(dropped(0, 8000, false), true);
  assert.equal(dropped(0, 34_999, true), false);
  assert.equal(dropped(0, 35_000, true), true);
  // The daemon writes a keepalive every 15 s: one missed is not a drop, two and a little more are.
  assert.equal(dropped(0, 30_000, true), false);
  assert.ok(IDLE_TIMEOUT_MS > 2 * 15_000);
});
