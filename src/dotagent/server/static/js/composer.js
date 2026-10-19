// What the three composers share (the new chat's, the run's dock and the agent's page): one frame with the box that
// grows with its text, the attached files and the picks under it and a bar of pills; Enter sends and Shift+Enter breaks
// a line, or ⌘Enter sends and Enter breaks it, as Settings › General chooses (item 230); `/`
// at the start of a word opens the slash menu of commands, skills and MCP tools (slash.js says what it offers; this
// file draws it and listens to the keys, after the APG combobox pattern: the arrows move, Enter and Tab pick, Escape
// closes, letters filter); the "+" opens the menu that adds files and picks plugins and skills for the message (item
// 318, picks.js); a draft keeps the text, the ready files and the picks in this browser until they are sent. The
// markdown in the box shows as it reads, its marks quiet (mirror.js, item 207).

import { api } from "./api.js";
import { SEND_KEY, settingValue } from "./appearance/settings.js";
import { createAttachments } from "./attachments.js";
import { el, setText } from "./dom.js";
import { icon, toolIconName } from "./icons.js";
import { markdownMirror } from "./mirror.js";
import { arePicks, createPicks, pickColour } from "./picks.js";
import { anchoredPopover } from "./popover.js";
import { clearPref, readPref, writePref } from "./prefs.js";
import { takesSnapshots } from "./snapshots.js";
import { filterGroups, menuWord, paletteGroups, pickGroups, replaceMenuWord } from "./slash.js";

const DRAFT_PREFIX = "dot.draft.";
// The draft key of a new chat's composer.
export const NEW_CHAT_DRAFT = "home";
// The tallest box of a chat's composer, on its page (views/run.js) and on the curtain while Dot opens (waiting.js).
export const CHAT_BOX_PX = 160;

/** The draft kept under `key` (`{ text, files, picks }`, `picks` missing from a draft of before item 318), or null when
 * there is none or it is not one. */
function readDraft(key) {
  const stored = readPref(`${DRAFT_PREFIX}${key}`);
  if (stored === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(stored);
    if (typeof parsed.text === "string" && Array.isArray(parsed.files) && (parsed.picks === undefined || arePicks(parsed.picks))) {
      return parsed;
    }
  } catch (error) {
    console.warn("a draft could not be read and is dropped", { key, error });
  }
  clearPref(`${DRAFT_PREFIX}${key}`);
  return null;
}

/** Keep `draft` (`{ text, files }`, and `picks` when it has some) as the draft of the composer of `draftKey`, which
 * takes it up when it is next made: a message a rewind gave back (item 282). */
export function keepDraft(draftKey, draft) {
  writePref(`${DRAFT_PREFIX}${draftKey}`, JSON.stringify(draft));
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
 * menu with `commands` (`{ label: "/btw", hint, insert }`, or `run()` for one that acts on the composer), the at menu
 * of the MCP servers, their tools and the skills (item 367) and the markdown it holds shown under it; `edited()` runs
 * after a pick of the menu changed the text, as the input event does after typing, and `picked(pick)` after a skill or
 * a tool was picked for the message (`{ kind, name }`, item 318). Returns the menu element and the box's field (the box
 * with its markdown under it) to place in the composer's frame, `autosize()`, to call after any change of the text, and
 * `dispose()`. */
function wireComposer(textarea, { maxPx, send, commands, edited, picked }) {
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

  function itemIconName(item) {
    if (item.kind === "command") {
      return item.label === "/loop" ? "refresh" : "terminal";
    }
    if (item.kind === "plugin") {
      return "plug";
    }
    if (item.kind === "skill") {
      return "sparkles";
    }
    if (item.kind === "tool") {
      return toolIconName(item.label);
    }
    return "sparkles";
  }

  function itemMark(item) {
    const iconName = itemIconName(item);
    const span = el("span", { class: "slash-mark", dataset: { kind: item.kind ?? "command" } }, icon(iconName));
    if (item.kind === "plugin" || item.kind === "skill" || item.kind === "tool") {
      const tintName = item.serverName ?? item.label;
      span.style.setProperty("--pick-tint", `var(--character-${pickColour(tintName)})`);
    }
    return span;
  }

  function paint() {
    menu.replaceChildren();
    const groups = filterGroups(range.trigger === "@" ? pickGroups(palette) : paletteGroups(commands, palette), range.word);
    shown = groups.flatMap((group) => group.items);
    active = Math.min(active, Math.max(shown.length - 1, 0));
    for (const group of groups) {
      menu.append(el("div", { class: "slash-group" }, group.title));
      for (const item of group.items) {
        const at = shown.indexOf(item);
        const option = el(
          "div",
          { class: "slash-option", role: "option", id: `${menu.id}-${item.id}`, "aria-selected": String(at === active) },
          itemMark(item),
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
      // The at menu holds nothing but what the palette lists: until it is read, it says so rather than that nothing
      // matches.
      const loading = palette === null && range.trigger === "@";
      menu.append(el("div", { class: "slash-note" }, loading ? "Reading the MCP servers and skills…" : `Nothing matches ${range.trigger}${range.word}.`));
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
    const replaced = replaceMenuWord(textarea.value, start, caret, item.insert ?? "");
    textarea.value = replaced.text;
    textarea.setSelectionRange(replaced.caret, replaced.caret);
    close();
    autosize();
    edited();
    textarea.focus();
    if (typeof item.run === "function") {
      item.run();
    }
    if (item.pick !== undefined) {
      picked(item.pick);
    }
  }

  // The menu follows the caret: open on a slash word or an at word (item 367), closed anywhere else.
  function follow() {
    const found = menuWord(textarea.value, textarea.selectionStart);
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
    if (event.key === "Enter" && !event.shiftKey && (settingValue(SEND_KEY) === "enter" || event.metaKey || event.ctrlKey)) {
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
 * with its text up to `maxPx`), the attached files and the picks (item 318) in rows under the box, then one bar: the
 * "+" that opens the file picker directly (documents, photos and screenshots alike; a phone opens its own sheet),
 * the rows the composer adds (`addMenuItems`) under a "More" menu when there are any (e.g. Save skill), the `options`
 * row for the pills (app.css keeps the mode at the left and sends the rest to the right)
 * and the round send button (`sendLabel` its words). `commands` are the slash menu's own; `send()` runs on Enter and on the button.
 * `draftKey` (or null) keeps the text and the ready files under `dot.draft.<key>` until `clear()` after a send.
 * `listener` (`{ focus(on), typing() }`, or null) hears the box, for a face that listens. The frame carries
 * `data-empty` while the box holds no text, so the send rests quiet. `setStop(handler)` (item 120) turns the send into a
 * Stop, a round button in the danger colour with a square, while the box holds no text and no file: pressed, it runs
 * `handler`; a word typed or a file listed (a long paste among them, item 269) brings the send back, and
 * `setStop(null)` ends it. Returns `element`, `textarea`, `attachments`,
 * `picks` (picks.js: `body()` goes into the message's request, `sent()` once a message that starts a run went),
 * `addMenuItems(build)` (`build(close)` gives the composer's own rows of the "More" menu each time it opens; `close()`
 * closes it), `options`,
 * `sendButton`, `setSendLabel(text)`, `setStop(handler)`, `autosize()`, `watchText(watcher)` (runs `watcher()` after
 * every change of the text, a clear among them, and returns what stops it; item 259), `clear()` (the text and the
 * files; the picks wait for `picks.sent()`), `pauseDraft()` and `resumeDraft()` (while the box holds what is not the
 * draft, a queued message being edited, item 376, the draft stays as it was) and `dispose()`.
 */
export function composerFrame({ label, boxLabel, placeholder, maxPx, commands, send, sendLabel, draftKey, listener }) {
  const textarea = el("textarea", { class: "textarea", rows: "1", placeholder, "aria-label": boxLabel });
  const element = el("section", { class: "card composer", "aria-label": label });
  const key = draftKey === null ? null : `${DRAFT_PREFIX}${draftKey}`;

  let drafting = true;

  function keep() {
    if (key === null || !drafting) {
      return;
    }
    const files = attachments.ready();
    if (textarea.value.trim() === "" && files.length === 0 && picks.count() === 0) {
      clearPref(key);
      return;
    }
    writePref(key, JSON.stringify({ text: textarea.value, files, picks: picks.saved() }));
  }

  // A file listed or taken away keeps the draft and repaints the send: with a file there it is never a Stop.
  function filesChanged() {
    keep();
    paintEmpty();
  }

  const attachments = createAttachments({ dropTarget: element, input: textarea, onChange: filesChanged });
  const picks = createPicks({ onChange: keep });
  const sendText = el("span", { class: "label" }, sendLabel);
  const arrow = icon("arrowUp");
  const square = icon("stop");
  const sendButton = el("button", { class: "btn primary send", type: "button", title: sendLabel, "aria-label": sendLabel }, arrow, sendText);
  const options = el("div", { class: "composer-options" });
  let sendWords = sendLabel;
  let stop = null;

  function stopping() {
    return stop !== null && textarea.value.trim() === "" && attachments.count() === 0;
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

  const wired = wireComposer(textarea, { maxPx, send, commands, edited, picked: (pick) => picks.add(pick.kind, pick.name) });
  const plusButton = el(
    "button",
    { class: "btn ghost sm plus-button", type: "button", title: "Add files", "aria-label": "Add files", onclick: () => attachments.openPicker() },
    icon("plus"),
  );
  // Rows added by caller views (e.g. Save skill in the open run view), under a "More" menu that only shows while there
  // are any; the "+" itself opens the file picker directly.
  const menuItems = [];
  const moreButton = el(
    "button",
    { class: "btn ghost sm more-button", type: "button", title: "More", "aria-label": "More", hidden: true },
    icon("ellipsis"),
  );

  const moreMenu = anchoredPopover({
    anchor: moreButton,
    frame: element,
    label: "More",
    build: () => [...menuItems.flatMap((build) => build(moreMenu.close))],
  });
  element.append(wired.menu, attachments.list, picks.list, attachments.picker, el("div", { class: "composer-bar" }, plusButton, moreButton, wired.field, options, sendButton));
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
    if (draft.picks !== undefined) {
      picks.restore(draft.picks);
    }
    // The box measures its text once it is on the page.
    requestAnimationFrame(wired.autosize);
  }
  paintEmpty();
  // SnapShots (item 405): a box with a draft of its own takes the captures the daemon made, the ones that waited
  // and each new one, as files of the draft.
  const stopSnapshots =
    draftKey === null
      ? () => undefined
      : takesSnapshots((files, animated) => {
          attachments.arrive(files, animated);
          filesChanged();
        });

  return {
    element,
    textarea,
    attachments,
    picks,
    addMenuItems: (build) => {
      menuItems.push(build);
      moreButton.hidden = false;
      moreMenu.repaint();
    },
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
      // What the draft keeps now: nothing, or the picks a steer left for the next message.
      keep();
    },
    pauseDraft: () => {
      drafting = false;
    },
    resumeDraft: () => {
      drafting = true;
    },
    dispose: () => {
      stopSnapshots();
      moreMenu.dispose();
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
      setText(valueNode, text);
      setText(extraNode, extra);
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
