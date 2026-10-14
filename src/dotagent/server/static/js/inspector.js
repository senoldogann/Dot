// The side panel of the run page: the details and the plan of the run and the screens the agent looked at. On a wide page it
// sits beside the conversation when the reader wants it (see layout.js); on a narrow page it is a drawer that the button in
// the header slides in over the conversation. A file the conversation carries or names takes the panel while it is open
// (item 195, filepanel.js).

import { imageUrl } from "./api.js";
import { el, motionMs } from "./dom.js";
import { fileView } from "./filepanel.js";
import { icon } from "./icons.js";
import { foldCard } from "./panels.js";
import { setShowInspector, showInspector } from "./layout.js";
import { onThemeChange } from "./theme.js";
import { showImage } from "./ui.js";

// The page needs this much width for a readable column next to the panel.
const MIN_WIDTH_PX = 960;
const STRIP_LIMIT = 24;

/**
 * The panel for a run page whose frame is `layout`, with `content` (a list of nodes) above the screens. The frame gets
 * `data-inspector="on"` while the panel sits beside the conversation and `"off"` otherwise, and `data-file="on"` while a
 * file takes it. Returns `element` (the panel) and `scrim` (the veil behind the drawer), both to put inside the frame,
 * `toggle` (the header button that shows or hides the panel), `arrange()` (call it once the frame is on the page),
 * `addImage(name)`, `showFile(source, opener)` (see `fileView`; `opener` gets the focus back when the file closes) and
 * `dispose()`.
 */
export function createInspector({ layout, content }) {
  const names = [];
  const thumbs = [];
  let selected = -1;
  let pinned = false;
  let currentUrl = null;
  let measuredWidth = -1;
  let drawerOpen = false;

  const empty = el("div", { class: "hint" }, "Screenshots from the desktop and the browser show up here as they are taken.");
  const caption = el("span", { class: "small muted" });
  const preview = el("button", { class: "inspector-shot", type: "button", hidden: true });
  const strip = el("div", { class: "inspector-strip", hidden: true });
  const screenCard = foldCard({
    id: "run-screen",
    title: "Screen",
    actions: caption,
    body: el("div", { class: "stack tight" }, empty, preview, strip),
    openByDefault: false,
  });
  const screens = screenCard.element;
  const closeButton = el(
    "button",
    { class: "btn ghost sm icon-only", type: "button", title: "Close", "aria-label": "Close the details", onclick: () => setDrawer(false) },
    icon("x"),
  );
  const head = el("div", { class: "inspector-head" }, el("strong", { class: "grow" }, "Run details"), closeButton);
  const element = el("aside", { class: "inspector", "aria-label": "Run details", hidden: true }, head, content, screens);
  const scrim = el("div", { class: "inspector-scrim", hidden: true, onclick: () => setDrawer(false) });
  const toggle = el(
    "button",
    { class: "btn ghost sm icon-only", type: "button", title: "Show the details", "aria-label": "Run details", "aria-pressed": "false" },
    icon("panelRight"),
  );

  preview.addEventListener("click", () => {
    if (currentUrl !== null) {
      showImage(currentUrl, caption.textContent);
    }
  });

  // ---------------------------------------------------------------------------------------------- screens
  function label(index) {
    return `Screenshot ${index + 1} of ${names.length}`;
  }

  function select(index) {
    selected = index;
    currentUrl = null;
    caption.textContent = label(index);
    preview.setAttribute("aria-label", `${label(index)}, show at full size`);
    const image = el("img", { alt: label(index) });
    preview.replaceChildren(image);
    imageUrl(names[index]).then(
      (url) => {
        if (selected === index) {
          currentUrl = url;
          image.src = url;
        }
      },
      (error) => {
        if (selected === index) {
          preview.replaceChildren(el("span", { class: "small muted" }, `Image unavailable: ${error.message}`));
        }
      },
    );
    for (const thumb of thumbs) {
      thumb.button.setAttribute("aria-pressed", String(thumb.index === index));
    }
  }

  function addThumb(index) {
    const image = el("img", { alt: "", loading: "lazy" });
    const button = el(
      "button",
      {
        class: "inspector-thumb",
        type: "button",
        "aria-pressed": "false",
        "aria-label": `Screenshot ${index + 1}`,
        onclick: () => {
          pinned = index !== names.length - 1;
          select(index);
        },
      },
      image,
    );
    imageUrl(names[index]).then(
      (url) => {
        image.src = url;
      },
      (error) => {
        console.warn("a screenshot could not be loaded", error);
        button.disabled = true;
      },
    );
    thumbs.push({ index, button });
    strip.append(button);
    if (thumbs.length > STRIP_LIMIT) {
      thumbs.shift().button.remove();
    }
    strip.scrollLeft = strip.scrollWidth;
  }

  function addImage(name) {
    if (names.includes(name)) {
      return;
    }
    names.push(name);
    empty.hidden = true;
    preview.hidden = false;
    strip.hidden = false;
    addThumb(names.length - 1);
    if (pinned) {
      caption.textContent = label(selected);
    } else {
      select(names.length - 1);
    }
  }

  // --------------------------------------------------------------------------------------------- placement
  /** Show the panel in the way the width allows: beside the conversation (when wanted) or as a drawer (when opened). */
  function arrange() {
    measuredWidth = layout.clientWidth;
    const room = measuredWidth >= MIN_WIDTH_PX;
    if (room) {
      drawerOpen = false;
    }
    const wanted = showInspector() || file !== null;
    layout.dataset.inspector = room && wanted ? "on" : "off";
    element.dataset.mode = room ? "docked" : "drawer";
    if (room) {
      clearTimeout(hideTimer);
      hideTimer = null;
      element.hidden = !wanted;
      scrim.hidden = true;
      delete element.dataset.open;
      delete scrim.dataset.open;
    } else {
      showDrawer(drawerOpen);
    }
    const shown = room ? wanted : drawerOpen;
    toggle.setAttribute("aria-pressed", String(shown));
    toggle.title = shown ? "Hide the details" : "Show the details";
  }

  // The drawer slides (item 85): shown first, then opened once its first frame is laid out so the slide plays; closing,
  // it hides when the slide has had its time (the base duration), under reduced motion too.
  let hideTimer = null;
  function showDrawer(open) {
    clearTimeout(hideTimer);
    hideTimer = null;
    if (open) {
      element.hidden = false;
      scrim.hidden = false;
      element.getBoundingClientRect();
      element.dataset.open = "true";
      scrim.dataset.open = "true";
      return;
    }
    element.dataset.open = "false";
    scrim.dataset.open = "false";
    if (!element.hidden) {
      hideTimer = setTimeout(() => {
        element.hidden = true;
        scrim.hidden = true;
        hideTimer = null;
      }, motionMs("--motion-base"));
    }
  }

  function setDrawer(open) {
    if (!open && file !== null) {
      closeFile();
      return;
    }
    drawerOpen = open;
    arrange();
    (open ? closeButton : toggle).focus();
  }

  // ------------------------------------------------------------------------------------------------- a file
  // A file takes the panel: beside the conversation, at half of the page when the panel has only its own width, or over
  // all of a narrow page. Closing it gives the panel back as it was, and the focus to what opened it.
  let file = null;

  function dropFile() {
    const dropped = file;
    if (dropped !== null) {
      file = null;
      dropped.view.dispose();
      dropped.view.element.remove();
      delete element.dataset.file;
      delete layout.dataset.file;
    }
    return dropped;
  }

  function showFile(source, opener) {
    const drawerBefore = file === null ? drawerOpen : file.drawerBefore;
    dropFile();
    const view = fileView(source, closeFile);
    file = { view, opener, drawerBefore };
    element.append(view.element);
    element.dataset.file = "on";
    layout.dataset.file = "on";
    if (measuredWidth < MIN_WIDTH_PX) {
      drawerOpen = true;
    }
    arrange();
    view.element.focus({ preventScroll: true });
  }

  function closeFile() {
    const dropped = dropFile();
    if (dropped === null) {
      return;
    }
    drawerOpen = dropped.drawerBefore;
    arrange();
    (dropped.opener.isConnected ? dropped.opener : toggle).focus();
  }

  toggle.addEventListener("click", () => {
    if (file !== null) {
      closeFile();
      return;
    }
    if (measuredWidth >= MIN_WIDTH_PX) {
      setShowInspector(!showInspector());
      arrange();
      return;
    }
    setDrawer(!drawerOpen);
  });

  // On the window, so that what answers Escape on the page (a menu, a popover) does so first and keeps it.
  function onKey(event) {
    if (event.key !== "Escape" || event.defaultPrevented) {
      return;
    }
    if (file !== null) {
      event.preventDefault();
      closeFile();
    } else if (drawerOpen) {
      setDrawer(false);
    }
  }

  window.addEventListener("keydown", onKey);

  // Only a change of width can change the answer, and the height of the page changes with every event of the run.
  const observer = new ResizeObserver(() => {
    if (layout.clientWidth !== measuredWidth) {
      arrange();
    }
  });
  observer.observe(layout);
  const stopTheme = onThemeChange(arrange);

  return {
    element,
    scrim,
    toggle,
    arrange,
    addImage,
    showFile,
    dispose: () => {
      dropFile();
      observer.disconnect();
      window.removeEventListener("keydown", onKey);
      stopTheme();
    },
  };
}
