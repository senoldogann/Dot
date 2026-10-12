// A menu of actions (item 130), after T3 Code's thread menu: opened at the pointer by a right-click, or at the button
// that opens it; its items are buttons that a click or Enter runs. The arrow keys, Home and End move between them;
// Escape or Tab closes it and gives the focus back to where it came from; a click outside it, a scroll, a change of the
// window's size or of the page closes it. One is open at a time.

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

/** Whether the menu open now is the one `button` opened. */
export function menuOpenedBy(button) {
  return current !== null && current.button === button;
}

/**
 * `openMenu({ label, items, at, back, button })`: `items` are `{ label, icon, danger, run }`, with `"separator"`
 * between groups; `at` is the point `{ x, y }` of the window where the menu's corner goes, moved in where the window
 * ends; `back` is the element the focus goes back to when a key closes the menu; `button`, when a button opened it, is
 * left to close it again, and says the menu is open. A chosen item closes the menu and gives the focus back, then
 * runs, and may move the focus on.
 */
export function openMenu({ label, items, at, back, button }) {
  closeMenu();
  const choices = [];
  const element = el(
    "div",
    { class: "menu", role: "menu", "aria-label": label },
    items.map((item) => {
      if (item === "separator") {
        return el("div", { class: "menu-separator", role: "separator" });
      }
      const choice = el(
        "button",
        { class: item.danger ? "menu-item danger" : "menu-item", type: "button", role: "menuitem", tabindex: "-1" },
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

  function away() {
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

  const handle = { button, close };
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
