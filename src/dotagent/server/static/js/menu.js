// A menu of actions (item 130): opened at the pointer by a right-click, or at the button
// that opens it; its items are buttons that a click or Enter runs. The arrow keys, Home and End move between them;
// Escape or Tab closes it and gives the focus back to where it came from; a click outside it, a scroll that moves what
// opened it, a change of the window's size or of the page closes it, and so does any scroll once what opened it has
// left the page. One is open at a time.

import { el } from "./dom.js";
import { icon } from "./icons.js";

// The menu keeps this far from the window's edges.
const EDGE_PX = 8;
// A phone held in the hand (item 1079): no pointer rests, and the screen is narrow. A menu there is a sheet from the
// foot of the screen, as an iPhone's action sheets are, its choices as tall as a finger needs.
const PHONE = window.matchMedia("(hover: none) and (max-width: 700px)");

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
  if (PHONE.matches) {
    openSheet({ label, items, back, button });
    return;
  }
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
 * The menu as a sheet from the foot of the screen (item 1079): its name over its choices, a group of choices between
 * two separators a block of its own, and Cancel at the foot; a tap beside it, Escape or a choice closes it.
 * @param {{ label: string, items: MenuEntry[], back: HTMLElement | null, button?: HTMLElement | null }} menu
 */
function openSheet({ label, items, back, button }) {
  /** @type {HTMLButtonElement[][]} */
  const groups = [[]];
  for (const item of items) {
    if (item === "separator") {
      groups.push([]);
      continue;
    }
    const choice = el(
      "button",
      { class: item.danger ? "sheet-action danger" : "sheet-action", type: "button", role: "menuitem", title: item.title ?? null },
      icon(item.icon),
      el("span", null, item.label),
    );
    choice.addEventListener("click", () => {
      close(true);
      item.run();
    });
    groups[groups.length - 1].push(choice);
  }
  const cancel = el("button", { class: "sheet-action sheet-cancel", type: "button" }, el("span", null, "Cancel"));
  cancel.addEventListener("click", () => close(true));
  const sheet = el(
    "dialog",
    { class: "message-sheet menu-sheet", "aria-label": label, tabindex: "-1" },
    el("div", { class: "sheet-handle", "aria-hidden": "true" }),
    el("div", { class: "sheet-facts" }, label),
    // The choices are a menu, as the pointer's menu is, to a screen reader; Cancel is the sheet's own.
    el(
      "div",
      { class: "sheet-menu", role: "menu", "aria-label": label },
      groups.filter((group) => group.length > 0).map((group) => el("div", { class: "sheet-actions", role: "group" }, group)),
    ),
    el("div", { class: "sheet-actions" }, cancel),
  );
  // A tap on the veil around the sheet is a tap on the dialog itself.
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) {
      close(false);
    }
  });
  sheet.addEventListener("cancel", (event) => {
    event.preventDefault();
    close(true);
  });
  // However it closed (a browser closes a dialog on Escape without a cancel it may skip), the sheet goes with it.
  sheet.addEventListener("close", () => close(false));

  function close(giveBack) {
    if (current !== handle) {
      return;
    }
    current = null;
    sheet.close();
    sheet.remove();
    window.removeEventListener("hashchange", away);
    if (button !== undefined && button !== null) {
      button.setAttribute("aria-expanded", "false");
    }
    if (giveBack && back?.isConnected) {
      back.focus({ preventScroll: true });
    }
  }
  const away = () => close(false);

  const handle = { button, anchor: button ?? back, close };
  current = handle;
  document.body.append(sheet);
  sheet.showModal();
  // The sheet takes the focus, not its first choice: a phone drew the ring of a focused choice round it, as if it
  // were chosen (the owner's screenshot of 2026-11-09, item 1086).
  sheet.focus({ preventScroll: true });
  if (button !== undefined && button !== null) {
    button.setAttribute("aria-expanded", "true");
  }
  window.addEventListener("hashchange", away);
}

// How long a finger rests on a row before it is a hold, how far it may move meanwhile, and how long after the hold
// its click (iOS sends none, Android one) is the hold's.
const HOLD_MS = 450;
const SLOP_PX = 10;
const AFTER_HOLD_MS = 400;

/**
 * Open the menu `menu()` gives when a finger holds `row` (item 1079), as a right-click opens it with a mouse: an
 * iPhone sends no right-click for a long press. The hold's click does nothing else.
 * @param {HTMLElement} row
 * @param {() => { label: string, items: MenuEntry[], back: HTMLElement | null }} menu
 */
export function openOnHold(row, menu) {
  let timer = 0;
  let start = { x: 0, y: 0 };
  let swallowUntil = 0;
  const cancel = () => clearTimeout(timer);
  row.addEventListener("pointerdown", (event) => {
    swallowUntil = 0;
    if (event.pointerType !== "touch" || !PHONE.matches) {
      return;
    }
    start = { x: event.clientX, y: event.clientY };
    timer = window.setTimeout(() => {
      const { label, items, back } = menu();
      if (items.length === 0) {
        return;
      }
      swallowUntil = Date.now() + AFTER_HOLD_MS;
      openMenu({ label, items, at: { x: start.x, y: start.y }, back });
    }, HOLD_MS);
  });
  row.addEventListener("pointermove", (event) => {
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > SLOP_PX) {
      cancel();
    }
  });
  row.addEventListener("pointerup", cancel);
  row.addEventListener("pointercancel", cancel);
  row.addEventListener(
    "click",
    (event) => {
      if (Date.now() < swallowUntil) {
        swallowUntil = 0;
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true,
  );
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
