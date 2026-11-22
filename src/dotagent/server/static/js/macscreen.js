// The Mac's screen in the run panel's Screen tab (item 1322): live whenever the tab is on screen, whether a task works
// or not, as the Watch page shows it. It starts when the section comes into sight (the tab chosen, the panel open) and
// stops when it leaves it or the page goes out of sight, so nothing is captured while nobody looks; Pause holds it until
// Watch live is pressed. macOS shows its screen recording mark while it is on.

import { el } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";
import { createScreenWatch } from "./screenwatch.js";

/** The section for the Screen tab. Returns `element` and `dispose()`. */
export function createMacScreen() {
  const canvas = el("canvas", { class: "watch-screen mac-screen-canvas", "aria-label": t("watch.screen") });
  const status = el("p", { class: "watch-status hint", role: "status" });
  const retry = el("button", { class: "btn sm", type: "button", hidden: true, onclick: () => begin() }, t("watch.retry"));
  const toggle = el("button", { class: "btn ghost sm mac-screen-toggle", type: "button", onclick: () => flip() });
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
  const body = el("div", { class: "watch-stage mac-screen" }, canvas, el("div", { class: "watch-foot" }, status, retry));
  const card = panelSection({ id: "mac-screen", title: t("screen.mac"), actions: el("span", { class: "mac-screen-actions" }, toggle, full), body });

  let disposed = false;
  let inSight = false;
  // Held by the reader: nothing starts by itself until Watch live is pressed.
  let paused = false;

  function say(text, canRetry) {
    if (disposed) {
      return;
    }
    status.textContent = text;
    status.hidden = text === "";
    retry.hidden = !canRetry;
  }

  const watch = createScreenWatch({ canvas, say });

  function paint() {
    toggle.textContent = paused ? t("screen.resume") : t("screen.pause");
    toggle.setAttribute("aria-pressed", String(paused));
  }

  function begin() {
    if (!disposed && !paused && inSight && document.visibilityState === "visible" && !watch.running()) {
      void watch.start();
    }
  }

  function end(words) {
    watch.stop();
    say(words, false);
  }

  function flip() {
    paused = !paused;
    paint();
    if (paused) {
      end(t("screen.paused"));
    } else {
      begin();
    }
  }

  const observer = new IntersectionObserver((entries) => {
    inSight = entries.some((entry) => entry.isIntersecting);
    if (inSight) {
      begin();
    } else if (watch.running()) {
      end("");
    }
  });
  observer.observe(canvas);

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      if (watch.running()) {
        end(t("watch.paused"));
      }
    } else {
      begin();
    }
  }
  document.addEventListener("visibilitychange", onVisibility);
  paint();
  say(t("screen.idle"), false);

  return {
    element: card.element,
    dispose() {
      disposed = true;
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      watch.stop();
    },
  };
}
