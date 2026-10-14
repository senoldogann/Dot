// Dot's computer: the page of the run's browser as Chrome paints it, and the handover of that browser to the reader.
// Nothing is cast while the view is closed; the card is shown for a run that is going on and keeps its last frame once
// the run has ended, when Watch goes too (there is no page left to watch).

import { api, sse } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";
import { toast } from "./ui.js";

// While the view has no frame it keeps the shape of a screen and says why (item 220): a mark, a title and a hint, by
// the state of the run and of the view. A run that has ended has no page left to watch (item 111): Watch is gone, and
// so is the hint that offers it. A run of a dotx command works in that command's browser, which the daemon does not
// cast (item 188).
const EMPTY_STATES = {
  "no-page": { mark: "globe", title: "No page yet", hint: "The view starts by itself when the run opens a page in its browser." },
  waiting: { mark: "monitor", title: "Waiting for the page", hint: "The first frame comes when the run's browser paints." },
  closed: { mark: "eye", title: "The view is closed", hint: "Watch shows the page Dot's browser is on. Nothing is sent while the view is closed." },
  ended: { mark: "clock", title: "The run has ended", hint: "Its browser's page is shown here only while a run is going on." },
  elsewhere: { mark: "terminal", title: "Working elsewhere", hint: "The run works in a dotx command, whose browser is not shown here." },
};

/**
 * The section of the panel's Screen tab for the run `runId`, which is `active` (still going on in the daemon),
 * `elsewhere` (going on in a dotx command) and `held` (the reader has its browser); `name` is the actor whose browser
 * it is ("Dot has control"), `sizes` the size chooser of the panel (stagesize.js), above the view beside the page's
 * address.
 * Returns `element`, `setHeld(flag)` (the timeline calls it on the takeover and handback events), `setActive(flag)` (the
 * stream stops with the run) and `dispose()`.
 */
export function createComputer({ runId, active, elsewhere, held, name, sizes }) {
  let isActive = active;
  let isElsewhere = elsewhere;
  let isHeld = held;
  let hasPage = false;
  let controller = null;
  let objectUrl = null;

  const image = el("img", { class: "computer-image", alt: "The page of Dot's browser", hidden: true });
  const emptyMark = el("span", { class: "computer-empty-mark", "aria-hidden": "true" });
  const emptyTitle = el("div", { class: "computer-empty-title" });
  const emptyHint = el("div", { class: "hint" });
  const empty = el("div", { class: "computer-empty" }, emptyMark, emptyTitle, emptyHint);
  const screen = el("div", { class: "computer-screen" }, image, empty);
  const address = el("div", { class: "small muted computer-url" });
  const who = el("span", { class: "computer-who" });
  const handButton = el("button", { class: "btn sm primary", type: "button", onclick: () => toggleHold() });
  const pill = el("div", { class: "computer-pill" }, icon("hand"), who, handButton);
  const watchButton = el("button", { class: "btn ghost sm", type: "button", "aria-pressed": "false", onclick: () => (controller === null ? start() : stop()) });
  // A section of the panel: the watch button sits beside the title, and the toolbar under it says where the browser is.
  const card = panelSection({
    id: "run-computer",
    title: "Dot's computer",
    actions: watchButton,
    body: el("div", { class: "stack tight computer-body" }, el("div", { class: "computer-toolbar" }, address, sizes), screen, pill),
  });
  const element = card.element;
  element.classList.add("computer");
  element.setAttribute("aria-label", "Dot's computer");

  function paint() {
    const watching = controller !== null;
    watchButton.textContent = watching ? "Close the view" : "Watch";
    watchButton.setAttribute("aria-pressed", String(watching));
    watchButton.hidden = !isActive;
    if (image.hidden) {
      showEmpty(watching ? "waiting" : isElsewhere ? "elsewhere" : !isActive ? "ended" : hasPage ? "closed" : "no-page");
    }
    who.textContent = isHeld ? "You have control" : `${name} has control`;
    handButton.textContent = isHeld ? "Return control" : "Take over";
    handButton.title = isHeld ? "Let the run go on; it is told the page may have changed" : "Hold the run at its next tool call and use the browser yourself";
    pill.hidden = !isActive;
    pill.dataset.held = String(isHeld);
  }

  function showEmpty(state) {
    if (empty.dataset.state === state) {
      return;
    }
    const { mark, title, hint } = EMPTY_STATES[state];
    empty.dataset.state = state;
    emptyMark.replaceChildren(icon(mark));
    emptyTitle.textContent = title;
    emptyHint.textContent = hint;
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
        const frames = sse(`/runs/${encodeURIComponent(runId)}/screencast`, signal, () => undefined);
        for await (const message of frames) {
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
    /** The run's browser has a page (its first browser step): the view starts by itself while the run works. */
    notePage() {
      hasPage = true;
      start();
    },
    setHeld(flag) {
      isHeld = flag;
      paint();
    },
    setActive(flag) {
      isActive = flag;
      if (!flag) {
        isElsewhere = false;
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
