// What the three composers share (the new chat's, the run's dock and the agent's page): one frame with the box that
// grows with its text, the attached files under it and a bar of pills; Enter sends and Shift+Enter breaks a line; `/`
// at the start of a word opens the slash menu of commands, skills and MCP tools (slash.js says what it offers; this
// file draws it and listens to the keys, after the APG combobox pattern: the arrows move, Enter and Tab pick, Escape
// closes, letters filter); a draft keeps the text and the ready files in this browser until they are sent.

import { api } from "./api.js";
import { createAttachments } from "./attachments.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
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

/** Wire `textarea`: `send()` on Enter, autosize up to `maxPx`, and the slash menu with `commands`
 * (`{ label: "/btw", hint, insert }`, or `run()` for one that acts on the composer). Returns the menu element to place
 * in the composer's frame, `autosize()` and `dispose()`. */
export function wireComposer(textarea, { maxPx, send, commands }) {
  const menu = el("div", { class: "slash-menu", role: "listbox", id: `slash-${Math.random().toString(36).slice(2, 8)}`, hidden: true });
  textarea.setAttribute("aria-controls", menu.id);
  textarea.setAttribute("aria-expanded", "false");
  textarea.setAttribute("aria-autocomplete", "list");
  let palette = null;
  let problem = null;
  let shown = [];
  let active = 0;
  let range = null;

  function autosize() {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxPx)}px`;
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
    if (event.isComposing) {
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
    if (event.key === "Enter" && !event.shiftKey) {
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
    autosize,
    dispose: () => {
      textarea.removeEventListener("keydown", onKeydown);
      textarea.removeEventListener("click", follow);
      textarea.removeEventListener("blur", close);
    },
  };
}

/**
 * The frame the composers share: the slash menu over it, the box (`rows` high, `boxLabel` its name, growing with its
 * text up to `maxPx`), the attached files in a row under the box, then the bar with the `options` row on the left and
 * the round send button on the right (`sendLabel` its words). `commands` are the slash menu's own; `send()` runs on
 * Enter and on the button. `draftKey` (or null) keeps the text and the ready files under `dot.draft.<key>` until
 * `clear()` after a send. `listener` (`{ focus(on), typing() }`, or null) hears the box, for a face that listens.
 * Returns `element`, `textarea`, `attachments`, `options`, `sendButton`, `setSendLabel(text)`, `autosize()`, `clear()`
 * and `dispose()`.
 */
export function composerFrame({ label, boxLabel, placeholder, rows, maxPx, commands, send, sendLabel, draftKey, listener }) {
  const textarea = el("textarea", { class: "textarea", rows: String(rows), placeholder, "aria-label": boxLabel });
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
  const sendButton = el("button", { class: "btn primary send", type: "button", title: sendLabel, "aria-label": sendLabel }, icon("arrowUp"), sendText);
  const options = el("div", { class: "composer-options" });
  const wired = wireComposer(textarea, { maxPx, send, commands });
  element.append(wired.menu, textarea, attachments.list, attachments.picker, el("div", { class: "composer-bar" }, options, sendButton));
  sendButton.addEventListener("click", send);

  function onInput() {
    keep();
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

  return {
    element,
    textarea,
    attachments,
    options,
    sendButton,
    setSendLabel: (text) => {
      sendText.textContent = text;
      sendButton.title = text;
      sendButton.setAttribute("aria-label", text);
    },
    autosize: wired.autosize,
    clear: () => {
      textarea.value = "";
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
 * A pill of the bar: a muted caption and the value it holds ("Model", "Muse Spark 1.3"), a chevron after them. `id`
 * names it (`data-pill`). `setValue(text, extra)` paints the value and a muted word after it (or ""); `setTone(tone)`
 * tints it ("warn", or null).
 */
export function composerPill({ id, caption, value }) {
  const valueNode = el("span", { class: "composer-pill-value" }, value);
  const extraNode = el("span", { class: "composer-pill-extra", hidden: true });
  const element = el(
    "button",
    { class: "composer-pill", type: "button", "data-pill": id },
    el("span", { class: "composer-pill-caption" }, caption),
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
    setTone: (tone) => {
      if (tone === null) {
        delete element.dataset.tone;
      } else {
        element.dataset.tone = tone;
      }
    },
  };
}
