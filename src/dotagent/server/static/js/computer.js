// Dot's computer: the page of the run's browser as Chrome paints it, and the handover of that browser to the reader.
// Nothing is cast while the view is closed or the page is out of sight, and a view whose stream broke comes back by
// itself (`watchInView`); the card is shown for a run that is going on and keeps its last frame once the run has ended,
// when Watch goes too (there is no page left to watch). On a phone it also opens over the whole page (item 298), and
// "Use it here" uses the page from this screen while the reader holds the run (item 475). Zoom, or a tap on the page
// where it is only watched, opens the view in the full-screen viewer, live (item 1071): two fingers, a double tap or −
// and + zoom it, to read a page's small print on a phone.

import { api, frameUrl, watchInView } from "./api.js";
import { createControl } from "./control.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";
import { toast } from "./ui.js";
import { openViewer } from "./viewer.js";
import { t } from "./i18n.js";

// While the view has no frame it keeps the shape of a screen and says why (item 220): a mark, a title and a hint, by
// the state of the run and of the view. A run that has ended has no page left to watch (item 111): Watch is gone, and
// so is the hint that offers it. A run of a dotx command works in that command's browser, which the daemon does not
// cast (item 188).
const EMPTY_STATES = {
  "no-page": { mark: "globe", title: t("computer.noPageYet"), hint: t("computer.theViewStartsByItself") },
  waiting: { mark: "monitor", title: t("computer.waitingForThePage"), hint: t("computer.theFirstPictureComesWhen") },
  closed: { mark: "eye", title: t("computer.theViewIsClosed"), hint: t("computer.watchShowsThePageDot") },
  ended: { mark: "clock", title: t("computer.theTaskHasEnded"), hint: t("computer.itsBrowserSPageIs") },
  elsewhere: { mark: "terminal", title: t("computer.workingElsewhere"), hint: t("computer.thisTaskWasStartedFrom") },
};

/**
 * The section of the panel's Screen tab for the run `runId`, which is `active` (still going on in the daemon),
 * `elsewhere` (going on in a dotx command) and `held` (the reader has its browser); `name` is the actor whose browser
 * it is ("Dot has control"), `sizes` the size chooser of the panel (stagesize.js), above the view beside the page's
 * address.
 * Returns `element`, `setHeld(flag)` (the timeline calls it on the takeover and handback events), `setActive(flag)` (the
 * stream stops with the run), `openFull()` (show it over the whole page, the button of a phone's bar), `useHere()`
 * (hold the run and use its page from this screen, item 475) and `dispose()`.
 */
export function createComputer({ runId, active, elsewhere, held, name, sizes }) {
  let isActive = active;
  let isElsewhere = elsewhere;
  let isHeld = held;
  let hasPage = false;
  let controller = null;
  let objectUrl = null;

  const image = el("img", { class: "computer-image", alt: t("computer.thePageOfDotS"), hidden: true });
  const emptyMark = el("span", { class: "computer-empty-mark", "aria-hidden": "true" });
  const emptyTitle = el("div", { class: "computer-empty-title" });
  const emptyHint = el("div", { class: "hint" });
  const empty = el("div", { class: "computer-empty" }, emptyMark, emptyTitle, emptyHint);
  const screen = el("div", { class: "computer-screen" }, image, empty);
  const address = el("div", { class: "small muted computer-url" });
  // The view in the full-screen viewer (item 1071): a picture of its own that takes every frame while it is open.
  let zoomed = null;
  let zoomDialog = null;
  const zoomButton = el(
    "button",
    { class: "btn ghost sm icon-only computer-zoom", type: "button", title: t("computer.zoom"), "aria-label": t("computer.zoomIntoDotSComputer"), hidden: true, onclick: () => openZoom() },
    icon("maximize"),
  );
  const who = el("span", { class: "computer-who" });
  const handButton = el("button", { class: "btn sm primary", type: "button", onclick: () => toggleHold() });
  // Using the page from this screen (item 475): it opens over the whole page and the reader's taps go to it. A screen
  // that is touched has it first; one with a mouse, Take over first, which brings Chrome up on the Mac.
  const useButton = el(
    "button",
    { class: "btn sm", type: "button", title: t("computer.holdTheTaskAndUse"), onclick: () => useHere() },
    t("computer.useItHere"),
  );
  const controlNote = el("span", { class: "small muted computer-control-note", role: "status" });
  const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
  // The buttons stand together, so that a narrow panel puts them on a line of their own under the words (item 512).
  const actions = el("span", { class: "computer-actions" }, ...(coarse ? [useButton, handButton] : [handButton, useButton]));
  const pill = el("div", { class: "computer-pill" }, icon("hand"), who, actions, controlNote);
  let control = null;
  const watchButton = el("button", { class: "btn ghost sm", type: "button", "aria-pressed": "false", onclick: () => (controller === null ? start() : stop()) });
  // A section of the panel: the watch button sits beside the title, and the toolbar under it says where the browser is.
  const card = panelSection({
    id: "run-computer",
    title: t("computer.dotSComputer"),
    actions: watchButton,
    body: el("div", { class: "stack tight computer-body" }, el("div", { class: "computer-toolbar" }, address, zoomButton, sizes), screen, pill),
  });
  const element = card.element;
  element.classList.add("computer");
  element.setAttribute("aria-label", t("computer.dotSComputer"));

  function paint() {
    const watching = controller !== null;
    watchButton.textContent = watching ? t("computer.closeTheView") : t("computer.watch");
    watchButton.setAttribute("aria-pressed", String(watching));
    watchButton.hidden = !isActive;
    if (image.hidden) {
      showEmpty(watching ? "waiting" : isElsewhere ? "elsewhere" : !isActive ? "ended" : hasPage ? "closed" : "no-page");
    }
    who.textContent = isHeld ? t("computer.youHaveControl") : t("computer.nameHasControl", { name });
    handButton.textContent = isHeld ? t("computer.returnControl") : t("computer.takeOver");
    handButton.title = isHeld ? t("computer.letTheTaskGoOn") : t("computer.holdTheTaskAtIts");
    useButton.hidden = control !== null;
    // The view is zoomed while it is only watched: while it is used here, a tap is the page's.
    zoomButton.hidden = image.hidden || control !== null;
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
    const url = frameUrl(frame.jpeg_b64);
    if (objectUrl !== null) {
      URL.revokeObjectURL(objectUrl);
    }
    objectUrl = url;
    image.src = url;
    if (zoomed !== null) {
      zoomed.src = url;
    }
    const first = image.hidden;
    image.hidden = false;
    empty.hidden = true;
    address.textContent = frame.url;
    if (first) {
      paint();
    }
  }

  // The view over the whole screen, zoomed (item 1071): the page as it is drawn, frame by frame, until it is closed.
  function openZoom() {
    if (zoomed !== null || image.hidden || control !== null) {
      return;
    }
    // The viewer's own picture, fitted to its stage and not dragged by the browser (as ui.js shows a screenshot).
    const picture = el("img", { class: "viewer-picture-image", alt: t("computer.thePageOfDotS"), src: image.src, draggable: "false" });
    zoomed = picture;
    zoomDialog = openViewer({
      title: address.textContent || t("computer.dotSComputer"),
      content: picture,
      page: false,
      onClose: () => {
        zoomed = null;
        zoomDialog = null;
        zoomButton.focus({ preventScroll: true });
      },
    });
  }
  // A tap on the page that is only watched zooms it; one on the page used here is the page's (control.js).
  image.addEventListener("click", () => {
    if (control === null) {
      openZoom();
    }
  });

  function start() {
    if (controller !== null || !isActive) {
      return;
    }
    controller = new AbortController();
    const { signal } = controller;
    paint();
    (async () => {
      try {
        await watchInView({
          path: `/runs/${encodeURIComponent(runId)}/screencast`,
          signal,
          onMessage: (message) => {
            if (message.event === "frame") {
              show(message.data);
            }
          },
        });
      } catch (error) {
        if (!signal.aborted) {
          toast("error", t("computer.theViewOfDotS", { message: error.message }));
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
      if (!isHeld) {
        endUse();
      }
    } catch (error) {
      toast("error", error.message);
    } finally {
      handButton.disabled = false;
      paint();
    }
  }

  // Using the page from this screen (item 475): the run is held without bringing anything up on the Mac, the view
  // opens over the whole page, and the reader's taps, scrolls and keys go to the page until they give the run back.
  async function useHere() {
    if (!isActive || control !== null) {
      return;
    }
    useButton.disabled = true;
    try {
      if (!isHeld) {
        const detail = await api.post(`/runs/${encodeURIComponent(runId)}/takeover/screen`, {});
        isHeld = detail.held;
      }
      if (isHeld) {
        openFull();
        full.forUse = true;
        startControl();
      }
    } catch (error) {
      toast("error", error.message);
    } finally {
      useButton.disabled = false;
      paint();
    }
  }

  function startControl() {
    if (control !== null) {
      return;
    }
    control = createControl({ runId, screen, image, onRefused: refused });
    handButton.before(control.keyboard);
    controlNote.textContent = t("computer.yourTapsGoToThe");
    paint();
  }

  function stopControl() {
    if (control === null) {
      return;
    }
    control.stop();
    control = null;
    controlNote.textContent = "";
    paint();
  }

  // The run given back, or over: the page is used here no more, and the view Use it here opened over the whole page
  // closes with it, back to the run's page.
  function endUse() {
    stopControl();
    if (full?.forUse) {
      full.dialog.close();
    }
  }

  /** What the daemon said when it refused the input: a step the run was taking when it was held keeps the page a
   * moment, and a run given back from elsewhere is used here no more. */
  function refused(error) {
    if (error.status === 409 && error.message.includes("still going")) {
      controlNote.textContent = t("computer.dotIsFinishingAStep");
      return;
    }
    if (error.status === 409) {
      stopControl();
    }
    toast("error", error.message);
  }

  // Over the whole page (item 298): the card moves into a dialog of its own,
  // the page's address on top, the view as wide as the screen and the pill at the foot, and goes back to the panel when
  // the dialog closes. The view starts as it opens, for a run that is going on.
  let full = null;
  function openFull() {
    if (full !== null) {
      return;
    }
    const closeButton = el(
      "button",
      { class: "icon-btn computer-full-close", type: "button", title: t("computer.close"), "aria-label": t("computer.closeDotSComputer"), onclick: () => full?.dialog.close() },
      icon("x"),
    );
    const dialog = el("dialog", { class: "computer-full", "aria-label": t("computer.dotSComputer") }, closeButton);
    full = { dialog, home: element.parentNode, next: element.nextSibling, forUse: false };
    dialog.append(element);
    dialog.addEventListener("close", closeFull);
    document.body.append(dialog);
    dialog.showModal();
    closeButton.focus();
    start();
  }

  function closeFull() {
    if (full === null) {
      return;
    }
    // Closed, the view shows no page to tap: the run stays held until Return control.
    stopControl();
    const { dialog, home, next } = full;
    full = null;
    home.insertBefore(element, next);
    dialog.remove();
  }

  paint();
  return {
    element,
    openFull,
    useHere,
    /** The run's browser has a page (its first browser step): the view starts by itself while the run works. */
    notePage() {
      hasPage = true;
      start();
    },
    setHeld(flag) {
      isHeld = flag;
      if (!flag) {
        endUse();
      }
      paint();
    },
    setActive(flag) {
      isActive = flag;
      if (!flag) {
        isElsewhere = false;
        endUse();
        stop();
      }
      paint();
    },
    dispose() {
      // A page left with the view zoomed leaves no viewer over the next one.
      zoomDialog?.close();
      full?.dialog.close();
      stopControl();
      stop();
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    },
  };
}
