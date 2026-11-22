// Watch (item 1264): the Mac's screen, live, whether a task works or not. The daemon sends the main display as H.264
// (`GET /api/screen/live`, dotagent/desktop/live.py) and the browser's own decoder (WebCodecs) draws it, in HD and at
// up to 60 frames a second. Nothing is captured while no page watches: the stream stops when this page closes or goes
// out of sight, and starts again when it is back.

import { el } from "../dom.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { createScreenWatch } from "../screenwatch.js";
import { pageHead } from "../panels.js";

export function mount(root) {
  const canvas = el("canvas", { class: "watch-screen", "aria-label": t("watch.screen") });
  const status = el("p", { class: "watch-status hint", role: "status" });
  const retry = el("button", { class: "btn sm", type: "button", hidden: true, onclick: () => void watch.start() }, t("watch.retry"));
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

  let disposed = false;

  function say(text, canRetry) {
    if (disposed) {
      return;
    }
    status.textContent = text;
    status.hidden = text === "";
    retry.hidden = !canRetry;
  }

  const watch = createScreenWatch({ canvas, say });

  // A page out of sight sends nothing: the stream stops, and starts again once the page is back.
  function onVisibility() {
    if (document.visibilityState === "hidden") {
      watch.stop();
      say(t("watch.paused"), false);
    } else if (!watch.running()) {
      void watch.start();
    }
  }
  document.addEventListener("visibilitychange", onVisibility);
  void watch.start();
  return () => {
    disposed = true;
    document.removeEventListener("visibilitychange", onVisibility);
    watch.stop();
  };
}
