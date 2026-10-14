// The side panel of the run page: the details and the plan of the run and the screens the agent looked at, in tabs
// after T3 Code's side panel (item 203), one at a time, the one chosen last kept per browser. On a wide page it sits
// beside the conversation when the reader wants it (see layout.js), sliding in and out (item 220); on a narrow page it is
// a drawer that the button in the header slides in over the conversation. A file the conversation carries or names
// takes the panel while it is open (item 195, filepanel.js).

import { imageUrl } from "./api.js";
import { el, motionMs } from "./dom.js";
import { fileView } from "./filepanel.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";
import { readPref, writePref } from "./prefs.js";
import { setShowInspector, showInspector } from "./layout.js";
import { onThemeChange } from "./theme.js";
import { showImage } from "./ui.js";

// The page needs this much width for a readable column next to the panel.
const MIN_WIDTH_PX = 960;
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const STRIP_LIMIT = 24;
// The tab the reader chose last, for every run page of this browser.
const TAB_KEY = "dot.panelTab";
// The keys that move between the tabs, as a tab list's do.
const TAB_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End"]);

/**
 * The panel for a run page whose frame is `layout`, in `tabs` (`[{ id, label, sections }]`, `sections` a list of
 * nodes); the screenshots go at the end of the tab `screensIn`. The frame gets `data-inspector="on"` while the panel
 * sits beside the conversation and `"off"` otherwise, and `data-file="on"` while a file takes it. Returns `element`
 * (the panel) and `scrim` (the veil behind the drawer), both to put inside the frame, `toggle` (the header button that
 * shows or hides the panel), `arrange()` (call it once the frame is on the page), `addImage(name)`, `setCount(tabId,
 * text)` (a short count beside a tab's name; "" for none), `showFile(source, opener)` (see `fileView`; `opener` gets
 * the focus back when the file closes) and `dispose()`.
 */
export function createInspector({ layout, tabs, screensIn }) {
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
  const screens = panelSection({
    id: "run-screen",
    title: "Screenshots",
    actions: caption,
    body: el("div", { class: "stack tight" }, empty, preview, strip),
  }).element;
  const closeButton = el(
    "button",
    { class: "btn ghost sm icon-only inspector-close", type: "button", title: "Close", "aria-label": "Close the details", onclick: () => setDrawer(false) },
    icon("x"),
  );

  // ------------------------------------------------------------------------------------------------- tabs
  const tabNodes = tabs.map(({ id, label, sections }) => {
    const count = el("span", { class: "inspector-count", hidden: true });
    const button = el(
      "button",
      {
        class: "inspector-tab",
        type: "button",
        role: "tab",
        id: `inspector-tab-${id}`,
        "aria-controls": `inspector-panel-${id}`,
        "aria-selected": "false",
        tabindex: "-1",
        onclick: () => choose(id, false),
      },
      el("span", null, label),
      // A space between the name and the count, so that a screen reader says "Plan 1/3".
      " ",
      count,
    );
    const panel = el(
      "div",
      { class: "inspector-panel", role: "tabpanel", id: `inspector-panel-${id}`, "aria-labelledby": button.id, hidden: true },
      ...sections,
      id === screensIn ? screens : null,
    );
    return { id, button, panel, count };
  });
  const tabList = el(
    "div",
    { class: "inspector-tabs", role: "tablist", "aria-label": "Run details", onkeydown: walkTabs },
    tabNodes.map((tab) => tab.button),
  );
  const head = el("div", { class: "inspector-head" }, tabList, closeButton);
  const element = el(
    "aside",
    { class: "inspector", "aria-label": "Run details", hidden: true },
    head,
    tabNodes.map((tab) => tab.panel),
  );

  function show(id) {
    for (const tab of tabNodes) {
      const on = tab.id === id;
      tab.button.setAttribute("aria-selected", String(on));
      tab.button.tabIndex = on ? 0 : -1;
      tab.panel.hidden = !on;
    }
  }

  /** Show the tab `id` and keep it as the reader's choice; `focus` moves the focus to its button, as the keys do. */
  function choose(id, focus) {
    show(id);
    writePref(TAB_KEY, id);
    if (focus) {
      tabNodes.find((tab) => tab.id === id)?.button.focus();
    }
  }

  // The arrows go to the tab beside (round the ends), Home and End to the first and the last, each shown at once.
  function walkTabs(event) {
    const index = tabNodes.findIndex((tab) => tab.button === document.activeElement);
    if (!TAB_KEYS.has(event.key) || index === -1) {
      return;
    }
    event.preventDefault();
    const last = tabNodes.length - 1;
    const next = { ArrowLeft: index === 0 ? last : index - 1, ArrowRight: index === last ? 0 : index + 1, Home: 0, End: last };
    choose(tabNodes[next[event.key]].id, true);
  }

  function setCount(id, text) {
    const tab = tabNodes.find((node) => node.id === id);
    if (tab !== undefined) {
      tab.count.textContent = text;
      tab.count.hidden = text === "";
    }
  }

  const kept = readPref(TAB_KEY);
  show(tabNodes.some((tab) => tab.id === kept) ? kept : tabNodes[0].id);
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
    setCount(screensIn, String(names.length));
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
    element.dataset.mode = room ? "docked" : "drawer";
    if (room) {
      clearTimeout(hideTimer);
      hideTimer = null;
      scrim.hidden = true;
      delete element.dataset.open;
      delete scrim.dataset.open;
      dock(wanted);
    } else {
      stopLeaving();
      layout.dataset.inspector = "off";
      showDrawer(drawerOpen);
    }
    const shown = room ? wanted : drawerOpen;
    toggle.setAttribute("aria-pressed", String(shown));
    toggle.title = shown ? "Hide the details" : "Show the details";
  }

  // Beside the conversation the panel slides in as it is shown and out before it hides (item 220): the conversation
  // makes room for it at once and takes the room back once the slide has had its time; under reduced motion it comes
  // and goes at once.
  let leaveTimer = null;
  function dock(wanted) {
    if (wanted) {
      stopLeaving();
      layout.dataset.inspector = "on";
      element.hidden = false;
      return;
    }
    if (element.hidden || REDUCED_MOTION.matches) {
      stopLeaving();
      hideDocked();
      return;
    }
    if (leaveTimer === null) {
      element.dataset.leaving = "true";
      leaveTimer = setTimeout(() => {
        leaveTimer = null;
        hideDocked();
      }, motionMs("--motion-panel"));
    }
  }

  function stopLeaving() {
    clearTimeout(leaveTimer);
    leaveTimer = null;
    delete element.dataset.leaving;
  }

  function hideDocked() {
    delete element.dataset.leaving;
    element.hidden = true;
    layout.dataset.inspector = "off";
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
    // A panel that was hidden before the file goes with it at once: its slide would show the tabs, not the file.
    if (measuredWidth >= MIN_WIDTH_PX && !showInspector()) {
      stopLeaving();
      hideDocked();
    }
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
    setCount,
    showFile,
    dispose: () => {
      stopLeaving();
      dropFile();
      observer.disconnect();
      window.removeEventListener("keydown", onKey);
      stopTheme();
    },
  };
}
