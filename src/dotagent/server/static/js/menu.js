// A menu of actions (item 130): opened at the pointer by a right-click, or at the button
// that opens it; its items are buttons that a click or Enter runs. The arrow keys, Home and End move between them;
// Escape or Tab closes it and gives the focus back to where it came from; a click outside it, a scroll that moves what
// opened it, a change of the window's size or of the page closes it, and so does any scroll once what opened it has
// left the page. One is open at a time.

import { el } from "./dom.js";
import { icon } from "./icons.js";

// The menu keeps this far from the window's edges.
const EDGE_PX = 8;

let current = null;

/** Close the menu that is open, if one is. */
export function closeMenu() {
  if (current !== null) {
    current.close(false);
  }
}

/**
 * Closes the menu open now when what opened it is inside one of `roots`: a list painted again takes its rows away, and
 * with them the menus opened from them, but not the menu of another part of the page (the sidebar's list, painted again
 * whenever a run starts or ends, took the composer's "+" menu away 50 ms after it opened).
 * @param {...Node} roots
 */
export function closeMenuFrom(...roots) {
  if (current?.anchor && roots.some((root) => root.contains(current.anchor))) {
    current.close(false);
  }
}

/** One entry of a menu: a choice, or the line between two groups of them. */
/** @typedef {"separator" | { label: string, icon: string, danger?: boolean, title?: string, run: () => void }} MenuEntry */

/** Whether the menu open now is the one `button` opened. */
export function menuOpenedBy(button) {
  return current !== null && current.button === button;
}

/**
 * `openMenu({ label, items, at, back, button })`: `items` are `{ label, icon, danger, title, run }`, with `"separator"`
 * between groups; `at` is the point `{ x, y }` of the window where the menu's corner goes, moved in where the window
 * ends; `back` is the element the focus goes back to when a key closes the menu; `button`, when a button opened it, is
 * left to close it again, and says the menu is open. A chosen item closes the menu and gives the focus back, then
 * runs, and may move the focus on.
 * @param {{ label: string, items: MenuEntry[], at: { x: number, y: number }, back: HTMLElement | null, button?: HTMLElement | null }} menu
 */
export function openMenu({ label, items, at, back, button }) {
  closeMenu();
  const choices = [];
  const element = el(
    "div",
    // Focusable, so that a click between its items keeps the keys with it.
    { class: "menu", role: "menu", "aria-label": label, tabindex: "-1" },
    items.map((item) => {
      if (item === "separator") {
        return el("div", { class: "menu-separator", role: "separator" });
      }
      const choice = el(
        "button",
        // `title` says more of the choice where the pointer rests on it (a schedule's Run now: it does real work).
        { class: item.danger ? "menu-item danger" : "menu-item", type: "button", role: "menuitem", tabindex: "-1", title: item.title ?? null },
        icon(item.icon),
        el("span", null, item.label),
      );
      choice.addEventListener("click", () => {
        close(true);
        item.run();
      });
      choices.push(choice);
      return choice;
    }),
  );

  function move(event) {
    const index = choices.indexOf(document.activeElement);
    const last = choices.length - 1;
    const next = {
      ArrowDown: index === last ? 0 : index + 1,
      ArrowUp: index <= 0 ? last : index - 1,
      Home: 0,
      End: last,
    }[event.key];
    if (next !== undefined) {
      event.preventDefault();
      choices[next].focus();
    } else if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      close(true);
    }
  }

  function outside(event) {
    if (!element.contains(event.target) && (button === undefined || !button.contains(event.target))) {
      close(false);
    }
  }

  // A scroll closes the menu only when it moves what opened it: the conversation that scrolls as an answer comes in
  // does not move the composer's "+", and its menu stays open.
  const anchor = button ?? back;
  /** @param {Event} event */
  function away(event) {
    // An anchor that left the page (its row painted again) is in no part of it: any scroll closes its menu.
    if (event.type === "scroll" && anchor?.isConnected && event.target instanceof Node && !event.target.contains(anchor)) {
      return;
    }
    close(false);
  }

  function close(giveBack) {
    if (current !== handle) {
      return;
    }
    current = null;
    element.remove();
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("scroll", away, true);
    window.removeEventListener("resize", away);
    window.removeEventListener("hashchange", away);
    if (button !== undefined) {
      button.setAttribute("aria-expanded", "false");
    }
    if (giveBack && back.isConnected) {
      back.focus();
    }
  }

  const handle = { button, anchor, close };
  current = handle;
  element.addEventListener("keydown", move);
  document.body.append(element);
  const left = Math.min(at.x, window.innerWidth - element.offsetWidth - EDGE_PX);
  const top = Math.min(at.y, window.innerHeight - element.offsetHeight - EDGE_PX);
  element.style.left = `${Math.max(EDGE_PX, left)}px`;
  element.style.top = `${Math.max(EDGE_PX, top)}px`;
  if (button !== undefined) {
    button.setAttribute("aria-expanded", "true");
  }
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("scroll", away, true);
  window.addEventListener("resize", away);
  window.addEventListener("hashchange", away);
  choices[0]?.focus({ preventScroll: true });
}

/**
 * A row's "…" (item 848): a button that opens the menu of `items()` (read at each press, `MenuEntry` as `openMenu`
 * takes them) under it, and closes it when pressed again, so that a row shows one action and keeps the rest a press
 * away. `name` says what the actions are of, for the button's name ("More for <name>") and the menu's.
 * @param {{ name: string, items: () => MenuEntry[] }} more
 */
export function moreButton({ name, items }) {
  const button = el(
    "button",
    { class: "btn ghost sm icon-only row-more", type: "button", title: "More", "aria-label": `More for ${name}`, "aria-haspopup": "menu", "aria-expanded": "false" },
    icon("ellipsis"),
  );
  button.addEventListener("click", () => {
    if (menuOpenedBy(button)) {
      closeMenu();
      return;
    }
    const box = button.getBoundingClientRect();
    openMenu({ label: name, items: items(), at: { x: box.left, y: box.bottom + 4 }, back: button, button });
  });
  return button;
}
