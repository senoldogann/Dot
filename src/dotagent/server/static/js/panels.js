// Small building blocks the pages share: headings, the loading, empty and failed states, status badges, segmented controls
// and the blocks that fold away.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";

const STATUS_LABEL = { blocked: "waiting" };
const FOLD_KEY_PREFIX = "dot.fold.";

/** The heading of a page: a title, an optional one-line subtitle and optional controls on the right. */
export function pageHead(title, subtitle, actions) {
  return el(
    "div",
    { class: "page-head" },
    el("div", { class: "grow" }, el("h1", { class: "page-title" }, title), subtitle === "" ? null : el("div", { class: "page-sub" }, subtitle)),
    actions,
  );
}

/** A spinner for a place that is still loading, to sit inside a card. */
export function inlineLoading() {
  return el("div", { class: "empty", role: "status", "aria-label": "Loading" }, el("div", { class: "spinner" }));
}

/** A failed load, with the reason and a way to try again, to sit inside a card. */
export function inlineError(error, retry) {
  return el(
    "div",
    { class: "empty" },
    icon("alert"),
    el("div", { class: "row-title" }, "This could not be loaded"),
    el("div", null, error.message),
    el("div", { class: "cluster" }, el("button", { class: "btn", type: "button", onclick: retry }, icon("refresh"), "Try again")),
  );
}

export function loadingPanel() {
  return el("div", { class: "card" }, inlineLoading());
}

export function emptyPanel(iconName, title, text) {
  return el(
    "div",
    { class: "card empty" },
    icon(iconName),
    el("div", { class: "row-title" }, title),
    text === "" ? null : el("div", null, text),
  );
}

export function errorPanel(error, retry) {
  return el("div", { class: "card" }, inlineError(error, retry));
}

/** The badge for the status of a run; a run that waits for an approval reads "waiting". */
export function statusBadge(status) {
  return el("span", { class: `badge s-${status}` }, STATUS_LABEL[status] ?? status);
}

/**
 * A row of buttons of which exactly one is pressed. `options` are `{ id, label, hint }`; `onSelect(id)` runs after a click.
 * Returns the element, `value()`, the id that is pressed now, and `set(id)`, which presses a button without calling `onSelect`.
 */
export function segmented(options, selected, onSelect) {
  let current = selected;
  const buttons = new Map();
  const element = el("div", { class: "segmented", role: "group" });

  function paint() {
    for (const [id, button] of buttons) {
      button.setAttribute("aria-pressed", String(id === current));
    }
  }

  for (const option of options) {
    const button = el("button", { type: "button", title: option.hint }, option.label);
    button.addEventListener("click", () => {
      current = option.id;
      paint();
      onSelect(option.id);
    });
    buttons.set(option.id, button);
    element.append(button);
  }
  paint();
  return {
    element,
    value: () => current,
    set: (id) => {
      current = id;
      paint();
    },
  };
}

/**
 * Whether a foldable block is open: what the person chose last time (kept per browser under `id`), else `openByDefault`.
 * `flip()` toggles it, remembers the new state and returns it.
 */
function foldState(id, openByDefault) {
  const key = `${FOLD_KEY_PREFIX}${id}`;
  const stored = readPref(key);
  let open = stored === null ? openByDefault : stored === "open";
  return {
    isOpen: () => open,
    flip() {
      open = !open;
      writePref(key, open ? "open" : "closed");
      return open;
    },
  };
}

/**
 * A card whose body folds away. The title is a button (`aria-expanded`); `setSummary(...nodes)` puts a short line next to it
 * that stays in sight while the card is folded. `actions` sit beside the button, outside it, so using them never folds the
 * card. Returns the element and `setSummary`.
 */
export function foldCard({ id, title, actions, body, openByDefault }) {
  const state = foldState(id, openByDefault);
  const summary = el("span", { class: "fold-summary" });
  const content = el("div", { class: "fold-body", id: `fold-${id}` }, body);
  const toggle = el(
    "button",
    { class: "fold-toggle", type: "button", "aria-controls": content.id },
    el("span", { class: "chev" }, icon("chevron")),
    el("span", { class: "fold-title" }, title),
    summary,
  );
  const element = el("section", { class: "card fold" }, el("div", { class: "card-head" }, toggle, actions), content);

  function paint() {
    const open = state.isOpen();
    toggle.setAttribute("aria-expanded", String(open));
    content.hidden = !open;
    element.dataset.open = String(open);
  }

  toggle.addEventListener("click", () => {
    state.flip();
    paint();
  });
  paint();
  return { element, setSummary: (...nodes) => summary.replaceChildren(...nodes.filter((node) => node !== null)) };
}

/** A plain block with a folding head, for a group inside a card: `head` is what the button shows, `body` what folds. */
export function foldGroup({ id, head, body, openByDefault }) {
  const state = foldState(id, openByDefault);
  const content = el("div", { class: "fold-body", id: `fold-${id}` }, body);
  const toggle = el("button", { class: "fold-toggle", type: "button", "aria-controls": content.id }, el("span", { class: "chev" }, icon("chevron")), head);
  const element = el("div", { class: "fold-group" }, toggle, content);

  function paint() {
    const open = state.isOpen();
    toggle.setAttribute("aria-expanded", String(open));
    content.hidden = !open;
    element.dataset.open = String(open);
  }

  toggle.addEventListener("click", () => {
    state.flip();
    paint();
  });
  paint();
  return element;
}

/** A labelled form field: the label, the control and an optional hint underneath. */
export function field(label, control, hint) {
  return el(
    "label",
    { class: "field" },
    el("span", { class: "field-label" }, label),
    control,
    hint === "" ? null : el("span", { class: "hint" }, hint),
  );
}
