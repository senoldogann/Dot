// What the three composers share (the new chat's, the run's dock and the agent's page): one frame with the box that
// grows with its text, the attached files under it and a bar of pills; Enter sends and Shift+Enter breaks a line, or
// ⌘Enter sends and Enter breaks it, as Settings › General chooses (item 230); `/`
// at the start of a word opens the slash menu of commands, skills and MCP tools (slash.js says what it offers; this
// file draws it and listens to the keys, after the APG combobox pattern: the arrows move, Enter and Tab pick, Escape
// closes, letters filter); a draft keeps the text and the ready files in this browser until they are sent. The markdown
// in the box shows as it reads, its marks quiet (mirror.js, item 207).

import { api } from "./api.js";
import { SEND_KEY, valueOf } from "./appearance/settings.js";
import { createAttachments } from "./attachments.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { markdownMirror } from "./mirror.js";
import { clearPref, readPref, writePref } from "./prefs.js";
import { filterGroups, paletteGroups, replaceSlashWord, slashWord } from "./slash.js";

const DRAFT_PREFIX = "dot.draft.";

/** The draft kept under `key` (`{ text, files }`), or null when there is none or it is not one. */
function readDraft(key) {
  const stored = readPref(`${DRAFT_PREFIX}${key}`);
  if (stored === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(stored);
    if (typeof parsed.text === "string" && Array.isArray(parsed.files)) {
      return parsed;
    }
  } catch (error) {
    console.warn("a draft could not be read and is dropped", { key, error });
  }
  clearPref(`${DRAFT_PREFIX}${key}`);
  return null;
}

// The skills and the MCP tools, asked for once per page and kept; a failure is kept too, so the menu says so.
let paletteLoad = null;

function loadPalette() {
  if (paletteLoad === null) {
    paletteLoad = api.get("/palette").catch((error) => {
      paletteLoad = null;
      throw error;
    });
  }
  return paletteLoad;
}

/** Wire `textarea`: `send()` on the key that sends (Enter, or ⌘Enter: `SEND_KEY`), autosize up to `maxPx`, the slash
 * menu with `commands` (`{ label: "/btw", hint, insert }`, or `run()` for one that acts on the composer) and the
 * markdown it holds shown under it; `edited()` runs after a pick of the menu changed the text, as the input event does
 * after typing. Returns the menu element and the box's field (the box with its markdown under it) to place in the
 * composer's frame, `autosize()`, to call after any change of the text, and `dispose()`. */
export function wireComposer(textarea, { maxPx, send, commands, edited }) {
  const menu = el("div", { class: "slash-menu", role: "listbox", id: `slash-${Math.random().toString(36).slice(2, 8)}`, hidden: true });
  textarea.setAttribute("aria-controls", menu.id);
  textarea.setAttribute("aria-expanded", "false");
  textarea.setAttribute("aria-autocomplete", "list");
  let palette = null;
  let problem = null;
  let shown = [];
  let active = 0;
  let range = null;
  const mirror = markdownMirror(textarea);

  function autosize() {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxPx)}px`;
    mirror.paint();
  }

  function close() {
    menu.hidden = true;
    menu.replaceChildren();
    textarea.setAttribute("aria-expanded", "false");
    textarea.removeAttribute("aria-activedescendant");
    shown = [];
    range = null;
  }

  function paint() {
    menu.replaceChildren();
    const groups = filterGroups(paletteGroups(commands, palette), range.word);
    shown = groups.flatMap((group) => group.items);
    active = Math.min(active, Math.max(shown.length - 1, 0));
    for (const group of groups) {
      menu.append(el("div", { class: "slash-group" }, group.title));
      for (const item of group.items) {
        const at = shown.indexOf(item);
        const option = el(
          "div",
          { class: "slash-option", role: "option", id: `${menu.id}-${item.id}`, "aria-selected": String(at === active) },
          el("span", { class: "slash-label" }, item.label),
          el("span", { class: "slash-hint" }, item.hint),
        );
        option.addEventListener("mousedown", (event) => {
          event.preventDefault();
          pick(item);
        });
        menu.append(option);
      }
    }
    if (problem !== null) {
      menu.append(el("div", { class: "slash-note" }, `Skills and MCP tools could not be loaded: ${problem}`));
    }
    if (shown.length === 0 && problem === null) {
      menu.append(el("div", { class: "slash-note" }, `Nothing matches /${range.word}.`));
    }
    menu.hidden = false;
    textarea.setAttribute("aria-expanded", "true");
    if (shown.length > 0) {
      textarea.setAttribute("aria-activedescendant", `${menu.id}-${shown[active].id}`);
      menu.children[Array.from(menu.children).findIndex((child) => child.id === `${menu.id}-${shown[active].id}`)]?.scrollIntoView({ block: "nearest" });
    } else {
      textarea.removeAttribute("aria-activedescendant");
    }
  }

  function pick(item) {
    const { start } = range;
    const caret = textarea.selectionStart;
    const replaced = replaceSlashWord(textarea.value, start, caret, item.insert ?? "");
    textarea.value = replaced.text;
    textarea.setSelectionRange(replaced.caret, replaced.caret);
    close();
    autosize();
    edited();
    textarea.focus();
    if (typeof item.run === "function") {
      item.run();
    }
  }

  // The menu follows the caret: open on a slash word, closed anywhere else.
  function follow() {
    const found = slashWord(textarea.value, textarea.selectionStart);
    if (found === null) {
      close();
      return;
    }
    const opening = range === null;
    range = found;
    if (opening) {
      active = 0;
    }
    if (palette === null && problem === null) {
      loadPalette().then(
        (loaded) => {
          palette = loaded;
          if (range !== null) {
            paint();
          }
        },
        (error) => {
          problem = error.message;
          if (range !== null) {
            paint();
          }
        },
      );
    }
    paint();
  }

  function onKeydown(event) {
    // WebKit ends the composition before the key that commits it, so that key shows only as keyCode 229 (item 116).
    if (event.isComposing || event.keyCode === 229) {
      return;
    }
    if (!menu.hidden) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (shown.length > 0) {
          active = (active + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
          paint();
        }
        return;
      }
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
        if (shown.length > 0) {
          event.preventDefault();
          pick(shown[active]);
          return;
        }
      }
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
    }
    // Enter sends, with ⌘ or Ctrl or without; where ⌘Enter is the key that sends, Enter alone breaks the line.
    if (event.key === "Enter" && !event.shiftKey && (valueOf(SEND_KEY) === "enter" || event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      send();
    }
  }

  textarea.addEventListener("input", () => {
    autosize();
    follow();
  });
  textarea.addEventListener("keydown", onKeydown);
  textarea.addEventListener("click", follow);
  textarea.addEventListener("blur", close);
  return {
    menu,
    field: mirror.field,
    autosize,
    dispose: () => {
      textarea.removeEventListener("keydown", onKeydown);
      textarea.removeEventListener("click", follow);
      textarea.removeEventListener("blur", close);
      mirror.dispose();
    },
  };
}

/**
 * The frame the composers share (item 115): the slash menu over it, the box (one line, `boxLabel` its name, growing
 * with its text up to `maxPx`), the attached files in a row under the box, then one bar: the attach button, the
 * `options` row for the pills (app.css keeps the mode at the left and sends the rest to the right) and the round send
 * button (`sendLabel` its words). `commands` are the slash menu's own; `send()` runs on Enter and on the button.
 * `draftKey` (or null) keeps the text and the ready files under `dot.draft.<key>` until `clear()` after a send.
 * `listener` (`{ focus(on), typing() }`, or null) hears the box, for a face that listens. The frame carries
 * `data-empty` while the box holds no text, so the send rests quiet. `setStop(handler)` (item 120) turns the send into a
 * Stop, a round button in the danger colour with a square, while the box holds no text: pressed, it runs `handler`; a
 * word typed brings the send back, and `setStop(null)` ends it. Returns `element`, `textarea`, `attachments`,
 * `options`, `sendButton`, `setSendLabel(text)`, `setStop(handler)`, `autosize()`, `watchText(watcher)` (runs
 * `watcher()` after every change of the text, a clear among them, and returns what stops it; item 259), `clear()`
 * and `dispose()`.
 */
export function composerFrame({ label, boxLabel, placeholder, maxPx, commands, send, sendLabel, draftKey, listener }) {
  const textarea = el("textarea", { class: "textarea", rows: "1", placeholder, "aria-label": boxLabel });
  const element = el("section", { class: "card composer", "aria-label": label });
  const key = draftKey === null ? null : `${DRAFT_PREFIX}${draftKey}`;

  function keep() {
    if (key === null) {
      return;
    }
    const files = attachments.ready();
    if (textarea.value.trim() === "" && files.length === 0) {
      clearPref(key);
      return;
    }
    writePref(key, JSON.stringify({ text: textarea.value, files }));
  }

  const attachments = createAttachments({ dropTarget: element, input: textarea, onChange: keep });
  const sendText = el("span", { class: "label" }, sendLabel);
  const arrow = icon("arrowUp");
  const square = icon("stop");
  const sendButton = el("button", { class: "btn primary send", type: "button", title: sendLabel, "aria-label": sendLabel }, arrow, sendText);
  const options = el("div", { class: "composer-options" });
  let sendWords = sendLabel;
  let stop = null;

  function stopping() {
    return stop !== null && textarea.value.trim() === "";
  }

  function paintEmpty() {
    element.toggleAttribute("data-empty", textarea.value.trim() === "");
    const stopShown = stopping();
    element.toggleAttribute("data-stop", stopShown);
    const words = stopShown ? "Stop" : sendWords;
    sendButton.replaceChildren(stopShown ? square : arrow, sendText);
    sendText.textContent = words;
    sendButton.title = stopShown ? "Stop the run" : words;
    sendButton.setAttribute("aria-label", words);
  }

  const watchers = new Set();
  // Every change of the text, typed, put there by the slash menu or cleared, keeps the draft, repaints the send and
  // tells the watchers (`watchText`, item 259).
  function edited() {
    keep();
    paintEmpty();
    for (const watcher of watchers) {
      watcher();
    }
  }

  const wired = wireComposer(textarea, { maxPx, send, commands, edited });
  element.append(wired.menu, wired.field, attachments.list, attachments.picker, el("div", { class: "composer-bar" }, attachments.button, options, sendButton));
  sendButton.addEventListener("click", () => (stopping() ? stop() : send()));

  function onInput() {
    edited();
    listener?.typing();
  }
  const onFocus = () => listener?.focus(true);
  const onBlur = () => listener?.focus(false);
  textarea.addEventListener("input", onInput);
  textarea.addEventListener("focus", onFocus);
  textarea.addEventListener("blur", onBlur);

  const draft = draftKey === null ? null : readDraft(draftKey);
  if (draft !== null) {
    textarea.value = draft.text;
    attachments.restore(draft.files);
    // The box measures its text once it is on the page.
    requestAnimationFrame(wired.autosize);
  }
  paintEmpty();

  return {
    element,
    textarea,
    attachments,
    options,
    sendButton,
    setSendLabel: (text) => {
      sendWords = text;
      paintEmpty();
    },
    setStop: (handler) => {
      stop = handler;
      paintEmpty();
    },
    autosize: wired.autosize,
    watchText: (watcher) => {
      watchers.add(watcher);
      return () => watchers.delete(watcher);
    },
    clear: () => {
      textarea.value = "";
      edited();
      wired.autosize();
      attachments.clear();
      if (key !== null) {
        clearPref(key);
      }
    },
    dispose: () => {
      wired.dispose();
      textarea.removeEventListener("input", onInput);
      textarea.removeEventListener("focus", onFocus);
      textarea.removeEventListener("blur", onBlur);
    },
  };
}

/**
 * A pill of the bar (item 108): an icon (`icon`, a node or null: the mark of a provider, a lock, a brain), the value it
 * holds ("Muse Spark 1.3") and a chevron; `caption` names it for a screen reader ("Model") without showing. `id` names
 * it (`data-pill`). `setValue(text, extra)` paints the value and a muted word after it (or ""); `setIcon(node)` swaps
 * the icon; `setTone(tone)` tints it ("warn", or null).
 */
export function composerPill({ id, caption, value, icon: mark }) {
  const valueNode = el("span", { class: "composer-pill-value" }, value);
  const extraNode = el("span", { class: "composer-pill-extra", hidden: true });
  const iconNode = el("span", { class: "composer-pill-icon" }, mark);
  const element = el(
    "button",
    { class: "composer-pill", type: "button", "data-pill": id },
    iconNode,
    el("span", { class: "visually-hidden" }, caption),
    valueNode,
    extraNode,
    icon("chevron"),
  );
  return {
    element,
    setValue: (text, extra) => {
      valueNode.textContent = text;
      extraNode.textContent = extra;
      extraNode.hidden = extra === "";
    },
    setIcon: (node) => {
      iconNode.replaceChildren(node);
    },
    setTone: (tone) => {
      if (tone === null) {
        delete element.dataset.tone;
      } else {
        element.dataset.tone = tone;
      }
    },
  };
}
