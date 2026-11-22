// The Mac's screen, live, drawn on a canvas (item 1264, shared by the Watch page and the run panel's Screen tab, item
// 1322): the daemon sends the main display as H.264 (`GET /api/screen/live`, dotagent/desktop/live.py) and the browser's
// own decoder (WebCodecs) draws it. The stream is the reader's alone: nothing is captured while `stop()` was called or
// the caller never started it, and the daemon stops its capture a second or two after the last reader goes.

import { binaryStream } from "./api.js";
import { t } from "./i18n.js";
import { joined, KEY, splitFrames, splitKey, codecOf } from "./livestream.js";

/**
 * Draw the Mac's screen on `canvas`. `say(text, canRetry)` is told what stands in place of a picture ("" once one is
 * drawn). Returns `start()` (begin, or begin again), `stop()` (end the stream and say nothing) and `running()`.
 */
export function createScreenWatch({ canvas, say }) {
  const painter = canvas.getContext("2d");
  let controller = null;
  let decoder = null;
  let configured = "";
  let drawn = false;

  function draw(frame) {
    if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
    }
    painter.drawImage(frame, 0, 0);
    frame.close();
    if (!drawn) {
      drawn = true;
      say("", false);
    }
  }

  function decode(frame) {
    if (frame.kind === KEY) {
      const { avcc, data } = splitKey(frame.payload);
      const config = Array.from(avcc).join(",");
      if (config !== configured) {
        decoder.configure({ codec: codecOf(avcc), description: avcc, optimizeForLatency: true });
        configured = config;
      }
      decoder.decode(new EncodedVideoChunk({ type: "key", timestamp: frame.timestamp, data }));
    } else if (configured !== "") {
      decoder.decode(new EncodedVideoChunk({ type: "delta", timestamp: frame.timestamp, data: frame.payload }));
    }
  }

  function stop() {
    controller?.abort();
    controller = null;
    if (decoder !== null && decoder.state !== "closed") {
      decoder.close();
    }
    decoder = null;
    configured = "";
    drawn = false;
  }

  function fail(which, error) {
    if (which !== controller || which.signal.aborted) {
      return;
    }
    stop();
    say(error === null ? t("watch.ended") : t("watch.failed", { reason: error.message ?? String(error) }), true);
  }

  async function start() {
    stop();
    if (typeof VideoDecoder === "undefined") {
      say(t("watch.unsupported"), false);
      return;
    }
    const mine = new AbortController();
    controller = mine;
    say(t("watch.connecting"), false);
    decoder = new VideoDecoder({ output: draw, error: (error) => fail(mine, error) });
    try {
      const reader = (await binaryStream("/screen/live", mine.signal)).getReader();
      let rest = new Uint8Array(0);
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        const split = splitFrames(joined(rest, value));
        rest = split.rest;
        for (const frame of split.frames) {
          decode(frame);
        }
      }
      fail(mine, null);
    } catch (error) {
      fail(mine, error);
    }
  }

  return { start, stop, running: () => controller !== null };
}
