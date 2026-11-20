// Watch (item 1264): the Mac's screen, live, whether a task works or not. The daemon sends the main display as H.264
// (`GET /api/screen/live`, dotagent/desktop/live.py) and the browser's own decoder (WebCodecs) draws it, in HD and at
// up to 60 frames a second. Nothing is captured while no page watches: the stream stops when this page closes or goes
// out of sight, and starts again when it is back.

import { binaryStream } from "../api.js";
import { el } from "../dom.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { joined, KEY, splitFrames, splitKey, codecOf } from "../livestream.js";
import { pageHead } from "../panels.js";

export function mount(root) {
  const canvas = el("canvas", { class: "watch-screen", "aria-label": t("watch.screen") });
  const status = el("p", { class: "watch-status hint", role: "status" });
  const retry = el("button", { class: "btn sm", type: "button", hidden: true, onclick: () => start() }, t("watch.retry"));
  const full = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: t("watch.fullScreen"),
      "aria-label": t("watch.fullScreen"),
      hidden: !document.fullscreenEnabled,
      onclick: () => void canvas.requestFullscreen().catch(() => undefined),
    },
    icon("maximize"),
  );
  const stage = el("div", { class: "watch-stage" }, canvas, el("div", { class: "watch-foot" }, status, retry));
  root.append(pageHead(t("watch.title"), t("watch.about"), full), stage);

  const painter = canvas.getContext("2d");
  let controller = null;
  let decoder = null;
  let configured = "";
  let disposed = false;

  function say(text, canRetry) {
    status.textContent = text;
    status.hidden = text === "";
    retry.hidden = !canRetry;
  }

  function draw(frame) {
    if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
    }
    painter.drawImage(frame, 0, 0);
    frame.close();
    if (status.textContent !== "") {
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

  function fail(which, error) {
    if (disposed || which !== controller || which.signal.aborted) {
      return;
    }
    stop();
    say(error === null ? t("watch.ended") : t("watch.failed", { reason: error.message ?? String(error) }), true);
  }

  // A page out of sight sends nothing: the stream stops, and starts again once the page is back.
  function onVisibility() {
    if (document.visibilityState === "hidden") {
      stop();
      say(t("watch.paused"), false);
    } else if (controller === null) {
      void start();
    }
  }
  document.addEventListener("visibilitychange", onVisibility);
  void start();
  return () => {
    disposed = true;
    document.removeEventListener("visibilitychange", onVisibility);
    stop();
  };
}
