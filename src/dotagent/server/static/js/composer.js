// What the two composers share (the new chat's and the run's dock): the box grows with its text, Enter sends and
// Shift+Enter breaks a line, and `/` at the start of a word opens the slash menu of commands, skills and MCP tools
// (slash.js says what it offers; this file draws it and listens to the keys, after the APG combobox pattern: the
// arrows move, Enter and Tab pick, Escape closes, letters filter).

import { api } from "./api.js";
import { el } from "./dom.js";
import { filterGroups, paletteGroups, replaceSlashWord, slashWord } from "./slash.js";

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
