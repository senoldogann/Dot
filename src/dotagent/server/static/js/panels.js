// Small building blocks the pages share: headings, the loading, empty and failed states, status badges, segmented controls
// and the blocks that fold away.

import { setStageTitle } from "./stage.js";
import { el, switchRow } from "./dom.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";
import { attempt } from "./ui.js";

const STATUS_LABEL = { blocked: "waiting" };
const FOLD_KEY_PREFIX = "dot.fold.";

/** The heading of a page: a title, an optional one-line subtitle and optional controls on the right. */
export function pageHead(title, subtitle, actions) {
  setStageTitle(el("h1", { class: "stage-h1" }, title));
  return paneHead(subtitle, actions);
}

/** The line under the bar on a page whose name the bar already shows: the description and the actions; nothing when
 * both are empty. */
function paneHead(subtitle, actions) {
  if (subtitle === "" && actions === null) {
    return null;
  }
  return el("div", { class: "page-head" }, el("div", { class: "grow page-sub" }, subtitle), actions);
}

/**
 * The head of a settings tab (item 562): the tab's name, the line that says what it is for and, at the right, what the
 * tab holds and the buttons that belong to the whole page. Returns the element and `setLead(text)`, `setMeta(...nodes)` and
 * `setActions(...nodes)`, which a tab calls once it knows them.
 */
export function settingsHead(title) {
  const lead = el("p", { class: "set-lead" });
  const meta = el("span", { class: "set-meta" });
  const actions = el("div", { class: "set-actions" });
  const element = el(
    "header",
    { class: "set-head" },
    el("div", { class: "set-head-text" }, el("h1", { class: "set-title" }, title), lead),
    el("div", { class: "set-head-side" }, meta, actions),
  );
  const fill = (node, nodes) => node.replaceChildren(...nodes.filter((child) => child !== null && child !== undefined));
  return {
    element,
    setLead: (text) => {
      lead.textContent = text;
    },
    setMeta: (...nodes) => fill(meta, nodes),
    setActions: (...nodes) => fill(actions, nodes),
  };
}

/**
 * A section of a settings tab (item 562): `title` over a card (or whatever `body` is), a `lead` line under the title that
 * says what the section is for, and `actions` at the right of the title. `body` is a node or a list of nodes.
 */
export function setSection({ title, lead = "", actions = null, body }) {
  return el(
    "section",
    { class: "set-section" },
    el(
      "div",
      { class: "set-section-head" },
      el("div", { class: "set-section-text" }, el("h2", { class: "set-section-title" }, title), lead === "" ? null : el("p", { class: "set-section-lead" }, lead)),
      actions,
    ),
    body,
  );
}

/** A card of rows (item 562): each row a line the hairline parts from the one before. */
export function setCard(...rows) {
  return el("div", { class: "set-card" }, rows);
}

/**
 * A row of a settings card (item 562): `title` and, under it, the muted `lead` on the left; `control` (a switch, a select, a
 * button, a group of them) on the right. `body` goes under both, over the row's whole width.
 */
export function setRow({ title, lead = "", control = null, body = null }) {
  return el(
    "div",
    { class: "set-row" },
    el(
      "div",
      { class: "set-row-main" },
      el("div", { class: "set-row-text" }, el("div", { class: "set-row-title" }, title), lead === "" ? null : el("div", { class: "set-row-lead" }, lead)),
      control === null ? null : el("div", { class: "set-row-control" }, control),
    ),
    body,
  );
}

/**
 * The words and mark of a button that opens a form and shuts it again (New rule): `openLabel` with a plus while the
 * form is shut, Close with a cross while it is open; `aria-expanded` says which.
 */
export function paintOpener(button, openLabel, isOpen) {
  button.replaceChildren(icon(isOpen ? "x" : "plus"), isOpen ? "Close" : openLabel);
  button.setAttribute("aria-expanded", String(isOpen));
}

/**
 * The words and mark of a button that opens a form in a dialog (item 615: New schedule, New watcher): a plus and
 * `label`, and `aria-haspopup`, which says that it opens a dialog.
 */
export function markOpener(button, label) {
  button.replaceChildren(icon("plus"), label);
  button.setAttribute("aria-haspopup", "dialog");
}

/** A spinner for a place that is still loading, to sit inside a card. */
export function inlineLoading() {
  return el("div", { class: "empty", role: "status", "aria-label": "Loading" }, el("div", { class: "spinner" }));
}

/** A failed load, with the reason and, when `retry` is given, a way to try again, to sit inside a card. */
export function inlineError(error, retry) {
  return el(
    "div",
    { class: "empty" },
    icon("alert"),
    el("div", { class: "row-title" }, "This could not be loaded"),
    el("div", null, error.message),
    typeof retry === "function" ? el("div", { class: "cluster" }, el("button", { class: "btn", type: "button", onclick: retry }, icon("refresh"), "Try again")) : null,
  );
}

export function loadingPanel() {
  return el("div", { class: "card" }, inlineLoading());
}

/** What stands in a chat's place while its run is read (item 502): the shape of the conversation to come, a message of
 * yours and the answer under it, under Dot's mark and the line that say the chat is opening. The page draws the
 * conversation over it as soon as the run is read; with reduced motion it stands still. */
export function chatLoading() {
  return el(
    "div",
    { class: "chat-loading", role: "status", "aria-label": "Opening this chat" },
    el(
      "div",
      { class: "chat-loading-head", "aria-hidden": "true" },
      el("span", { class: "chat-loading-mark" }, el("span", { class: "chat-loading-ring" }), el("span", { class: "chat-loading-core" })),
      el("span", { class: "chat-loading-label" }, "Opening this chat"),
    ),
    el(
      "div",
      { class: "chat-loading-lines", "aria-hidden": "true" },
      el("span", { class: "chat-loading-block owner" }),
      el("span", { class: "chat-loading-block" }),
      el("span", { class: "chat-loading-block" }),
      el("span", { class: "chat-loading-block" }),
    ),
  );
}

/** The empty state of a page or a list (item 407): `mark` (an icon's name) in a quiet ring, `title` and the line `text`
 * that says what brings something here; "" for no line. */
export function emptyPanel(mark, title, text) {
  return el(
    "div",
    { class: "empty-state", role: "status" },
    el("span", { class: "empty-mark", "aria-hidden": "true" }, icon(mark)),
    el("p", { class: "empty-title" }, title),
    text === "" ? null : el("p", { class: "empty-line" }, text),
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
  const element = el("section", { class: "card fold" }, el("div", { class: "card-head" }, toggle, actions ?? null), content);

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

/**
 * A section of the run's side panel (item 203): a head with its title, a short line that sums it up
 * (`setSummary(...nodes)`) and `actions` at its right, over `body`, which is always open: the panel's tabs choose what
 * is in sight. With `foldable` (item 426) it is `panelFold`'s, open the first time. Its body is `panel-${id}`.
 * Returns the element and `setSummary`, as `foldCard` does.
 */
export function panelSection({ id, title, actions, body, foldable = false }) {
  if (foldable) {
    return panelFold({ id, title, actions, body, openAtFirst: true });
  }
  const summary = el("span", { class: "panel-summary" });
  const content = el("div", { class: "panel-section-body", id: `panel-${id}` }, body);
  const element = el(
    "section",
    { class: "panel-section" },
    el("div", { class: "panel-section-head" }, el("h3", { class: "panel-section-title" }, title), summary, actions ?? null),
    content,
  );
  return { element, setSummary: (...nodes) => summary.replaceChildren(...nodes.filter((node) => node !== null)) };
}

/**
 * A section of the run's side panel whose title is a button that folds its body away (item 426), the choice kept per
 * browser under `dot.fold.panel.${id}`; open the first time when `openAtFirst`. Its body is `panel-${id}`. Returns the
 * element and `setSummary`, as `panelSection` does.
 */
export function panelFold({ id, title, actions, body, openAtFirst }) {
  const summary = el("span", { class: "panel-summary" });
  const content = el("div", { class: "panel-section-body", id: `panel-${id}` }, body);
  const state = foldState(`panel.${id}`, openAtFirst);
  const toggle = el(
    "button",
    { class: "fold-toggle panel-fold-toggle", type: "button", "aria-controls": content.id },
    el("span", { class: "chev" }, icon("chevron")),
    el("span", { class: "fold-title" }, title),
  );
  const element = el(
    "section",
    { class: "panel-section panel-fold" },
    el("div", { class: "panel-section-head" }, el("h3", { class: "panel-section-title" }, toggle), summary, actions ?? null),
    content,
  );
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

/** The switch of a setting: a button that writes the opposite of what `read()` says, and calls `onToggled` once it is done.
 * It starts unchecked: the caller sets `aria-checked` from what it read. */
export function switchToggle({ label, read, write, onToggled }) {
  return el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": "false",
    "aria-label": label,
    // Repainted from what was stored even when the switch failed half way (notifications on, push refused).
    onclick: () =>
      attempt(async () => {
        try {
          await write(!read());
        } finally {
          onToggled();
        }
      }),
  });
}

/**
 * A labelled on/off switch; `write` may be asynchronous. `paint()` shows the current `read()` again after something else
 * changed it; `toggle` and `note` are the button and the hint line, for settings that depend on more than their value.
 */
export function switchSetting({ label, hint, read, write, onToggled }) {
  const toggle = switchToggle({ label, read, write, onToggled });
  const note = el("div", { class: "hint" }, hint);
  const element = el("div", { class: "setting" }, switchRow(el("div", { class: "row" }, el("span", { class: "grow" }, label), toggle), toggle), note);
  return {
    element,
    toggle,
    note,
    paint: () => toggle.setAttribute("aria-checked", String(read())),
  };
}

/**
 * A switch as a row of a settings card (item 562), the title and the muted line on the left and the switch on the right;
 * the row turns it wherever it is tapped. `write` may be asynchronous. Returns the element, the `toggle` and `paint()`, as
 * `switchSetting` does.
 */
export function switchRowSetting({ label, hint, read, write, onToggled }) {
  const toggle = switchToggle({ label, read, write, onToggled });
  return {
    element: switchRow(setRow({ title: label, lead: hint, control: toggle }), toggle),
    toggle,
    paint: () => toggle.setAttribute("aria-checked", String(read())),
  };
}

/** A choice of a few as a row of a settings card (item 562), the buttons on the right: as `choiceSetting` returns it. */
export function choiceRowSetting({ label, hint, options, selected, onSelect }) {
  const choice = segmented(options, selected, onSelect);
  choice.element.setAttribute("aria-label", label);
  return { element: setRow({ title: label, lead: hint, control: choice.element }), choice };
}

/** A choice of a few, as Density is drawn: the label and the buttons on a line, the hint under them. */
export function choiceSetting({ label, hint, options, selected, onSelect }) {
  const choice = segmented(options, selected, onSelect);
  choice.element.setAttribute("aria-label", label);
  const element = el(
    "div",
    { class: "setting" },
    el("div", { class: "row" }, el("span", { class: "grow" }, label), choice.element),
    el("div", { class: "hint" }, hint),
  );
  return { element, choice };
}

/**
 * A setting that is a number on a scale (item 127): its label with the value as `format` writes it ("100%", "200 ms")
 * on one line, the range under them and the hint under that. Every step of a drag is written at once.
 */
export function rangeSetting({ label, hint, min, max, step, format, read, write }) {
  const shown = el("span", { class: "range-value" });
  const input = el("input", {
    class: "range",
    type: "range",
    min: String(min),
    max: String(max),
    step: String(step),
    "aria-label": label,
  });
  function paint() {
    const value = read();
    input.value = String(value);
    input.setAttribute("aria-valuetext", format(value));
    input.style.setProperty("--range-fill", `${((value - min) / (max - min)) * 100}%`);
    shown.textContent = format(value);
  }
  input.addEventListener("input", () => {
    write(Number(input.value));
    paint();
  });
  paint();
  const element = el(
    "div",
    { class: "setting" },
    el("div", { class: "row" }, el("span", { class: "grow" }, label), shown),
    input,
    el("div", { class: "hint" }, hint),
  );
  return { element, input, paint };
}
