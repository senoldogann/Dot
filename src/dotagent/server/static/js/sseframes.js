// The frames of a server-sent event stream and the time rules of reading one (item 1522). Pure, so that they run under Node
// without a page: api.js reads the bytes. The page reads its streams with `fetch` and a stream reader and not with
// `EventSource`, because an `EventSource` cannot see a comment line, which is what Dot's keepalive is (`: keepalive` every
// 15 s, and `: open` at once): a socket that died without a word could then be told from a quiet stream by nothing.

/** A connection that has answered nothing within this long is dropped and made again. */
export const CONNECT_TIMEOUT_MS = 8000;
/** An open stream that has said nothing, not even a keepalive, for this long is dropped and made again: the daemon writes
 * one every 15 s, so this is two missed and a little more. */
export const IDLE_TIMEOUT_MS = 35_000;
/** How long a stream waits before it is made again after it broke, one after the other; the last is the wait from then on.
 * A page back in view and a phone back on the network do not wait at all. */
export const RECONNECT_WAITS_MS = [500, 1000, 2000, 4000, 8000, 15_000];

/**
 * The wait before the stream is made again after it broke `failures` times in a row (0 for the first break): 0.5, 1, 2, 4,
 * 8 and then 15 seconds.
 * @param {number} failures
 */
export function reconnectWait(failures) {
  return RECONNECT_WAITS_MS[Math.min(Math.max(failures, 0), RECONNECT_WAITS_MS.length - 1)];
}

/**
 * Whether a stream that has said nothing since `heardAt` is to be dropped at `now`.
 * @param {number} heardAt when the last bytes came (ms), the connection's start while none has
 * @param {number} now
 * @param {boolean} open whether the connection has answered (its headers came)
 */
export function dropped(heardAt, now, open) {
  return now - heardAt >= (open ? IDLE_TIMEOUT_MS : CONNECT_TIMEOUT_MS);
}

/**
 * One frame of the stream: `id` is the event's id, 0 for a live piece the daemon does not store (it writes no `id` line),
 * `event` its name (`message` when it has none) and `data` what its `data` lines hold, read as JSON. A frame whose data is
 * not JSON has `bad` (the text) and no `data`: it is told of and skipped, not a break of the stream.
 * @typedef {{ id: number, event: string, data: any } | { id: number, event: string, bad: string }} Frame
 */

/**
 * A parser of the stream's text, which comes in chunks cut anywhere: `push(text)` takes the next chunk and returns the
 * frames it completed, in order. A frame is complete at its blank line; a line may end in `\n`, `\r\n` or `\r`, and a
 * `\r\n` cut between two chunks is one line end. A comment line (`:`) and a field it does not know (`retry`) change nothing.
 * `pending()` says that a frame is cut: the connection dropped while it was written, and what was read of it is not an
 * event (`reset()` forgets it for the next connection).
 */
export function createParser() {
  let buffer = "";
  let started = false;
  let id = 0;
  let event = "message";
  /** @type {string[]} */
  let data = [];
  let seen = false;

  function begin() {
    id = 0;
    event = "message";
    data = [];
    seen = false;
  }

  /** @returns {Frame | null} */
  function finish() {
    const had = seen;
    const lines = data;
    const frame = { id, event };
    begin();
    if (!had || lines.length === 0) {
      return null;
    }
    const text = lines.join("\n");
    try {
      return { ...frame, data: JSON.parse(text) };
    } catch {
      return { ...frame, bad: text };
    }
  }

  /** @param {string} line */
  function field(line) {
    if (line.startsWith(":")) {
      return;
    }
    const colon = line.indexOf(":");
    const name = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (name === "id") {
      id = /^\d+$/.test(value) ? Number(value) : 0;
      seen = true;
    } else if (name === "event") {
      event = value === "" ? "message" : value;
      seen = true;
    } else if (name === "data") {
      data.push(value);
      seen = true;
    }
  }

  begin();
  return {
    /** @param {string} text @returns {Frame[]} */
    push(text) {
      buffer += text;
      if (!started && buffer.length > 0) {
        started = true;
        if (buffer.startsWith("\uFEFF")) {
          buffer = buffer.slice(1);
        }
      }
      /** @type {Frame[]} */
      const frames = [];
      for (;;) {
        const end = buffer.search(/\r\n|\n|\r/);
        if (end === -1) {
          break;
        }
        const ending = buffer.slice(end).startsWith("\r\n") ? 2 : 1;
        // A `\r` that ends the chunk may be the first half of a `\r\n`: it is read when the next chunk says.
        if (buffer[end] === "\r" && end + 1 === buffer.length) {
          break;
        }
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + ending);
        if (line === "") {
          const frame = finish();
          if (frame !== null) {
            frames.push(frame);
          }
        } else {
          field(line);
        }
      }
      return frames;
    },
    /** The stream ended: a `\r` held back in case a `\n` followed it was a line end after all. A frame that is cut is not an
     * event: nothing else is given. @returns {Frame[]} */
    end() {
      if (!buffer.endsWith("\r")) {
        return [];
      }
      return this.push("\n");
    },
    /** Whether a frame was begun and not finished: bytes of it were read, its blank line was not. */
    pending() {
      return seen || buffer !== "";
    },
    /** Forget a cut frame, as a new connection starts from the event after the last whole one. */
    reset() {
      buffer = "";
      started = false;
      begin();
    },
  };
}
