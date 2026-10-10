// Dot's computer: the page of the run's browser as Chrome paints it, and the handover of that browser to the reader.
// Nothing is cast while the view is closed; the card is shown for a run that is going on and keeps its last frame once
// the run has ended.

import { api, sse } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

/**
 * The card for the run `runId`, which is `active` (still going on) and `held` (the reader has its browser). Returns
 * `element`, `setHeld(flag)` (the timeline calls it on the takeover and handback events), `setActive(flag)` (the stream
 * stops with the run) and `dispose()`.
 */
export function createComputer({ runId, active, held }) {
  let isActive = active;
  let isHeld = held;
  let controller = null;
  let objectUrl = null;

  const image = el("img", { class: "computer-image", alt: "The page of Dot's browser", hidden: true });
  const empty = el("div", { class: "hint" }, "Watch the page Dot's browser is on. Nothing is sent while the view is closed.");
  const screen = el("div", { class: "computer-screen" }, image, empty);
  const address = el("div", { class: "small muted computer-url" });
  const who = el("span", { class: "computer-who" });
  const handButton = el("button", { class: "btn sm primary", type: "button", onclick: () => toggleHold() });
  const pill = el("div", { class: "computer-pill" }, icon("hand"), who, handButton);
  const watchButton = el("button", { class: "btn ghost sm", type: "button", "aria-pressed": "false", onclick: () => (controller === null ? start() : stop()) });
  const element = el(
    "section",
    { class: "card card-pad stack tight computer", "aria-label": "Dot's computer" },
    el("div", { class: "row" }, el("strong", { class: "grow" }, "Dot's computer"), watchButton),
    screen,
    address,
    pill,
  );

  function paint() {
    const watching = controller !== null;
    watchButton.textContent = watching ? "Close the view" : "Watch";
    watchButton.setAttribute("aria-pressed", String(watching));
    watchButton.disabled = !isActive;
    if (image.hidden) {
      empty.textContent = watching
        ? "Waiting for the page: the first frame comes when the run's browser paints."
        : "Watch the page Dot's browser is on. Nothing is sent while the view is closed.";
    }
    who.textContent = isHeld ? "You have control" : "Dot has control";
    handButton.textContent = isHeld ? "Return control" : "Take over";
    handButton.title = isHeld ? "Let the run go on; it is told the page may have changed" : "Hold the run at its next tool call and use the browser yourself";
    pill.hidden = !isActive;
    pill.dataset.held = String(isHeld);
  }

  function show(frame) {
    const bytes = Uint8Array.from(atob(frame.jpeg_b64), (char) => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
    if (objectUrl !== null) {
      URL.revokeObjectURL(objectUrl);
    }
    objectUrl = url;
    image.src = url;
    image.hidden = false;
    empty.hidden = true;
    address.textContent = frame.url;
  }

  function start() {
    if (controller !== null || !isActive) {
      return;
    }
    controller = new AbortController();
    const { signal } = controller;
    paint();
    (async () => {
      try {
        for await (const message of sse(`/runs/${encodeURIComponent(runId)}/screencast`, signal)) {
          if (message.event === "frame") {
            show(message.data);
          }
        }
      } catch (error) {
        if (!signal.aborted) {
          toast("error", `The view of Dot's computer stopped: ${error.message}`);
        }
      } finally {
        if (controller !== null && controller.signal === signal) {
          controller = null;
        }
        paint();
      }
    })();
  }

  function stop() {
    if (controller !== null) {
      controller.abort();
      controller = null;
    }
    paint();
  }

  async function toggleHold() {
    handButton.disabled = true;
    try {
      const detail = await api.post(`/runs/${encodeURIComponent(runId)}/${isHeld ? "handback" : "takeover"}`, {});
      isHeld = detail.held;
    } catch (error) {
      toast("error", error.message);
    } finally {
      handButton.disabled = false;
      paint();
    }
  }

  paint();
  return {
    element,
    setHeld(flag) {
      isHeld = flag;
      paint();
    },
    setActive(flag) {
      isActive = flag;
      if (!flag) {
        stop();
      }
      paint();
    },
    dispose() {
      stop();
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    },
  };
}
