// The two choices of a run besides its goal: who answers the questions the safety policy raises (approvals) and whether a
// checker keeps the run working until its goal is really reached (goal mode). The daemon has a default for both. A choice
// made on the home page is kept per browser and goes with every run started from there until it is changed again; a person
// who never chose anything leaves both to the daemon. On a chat's page the choices start from the open run's and are held
// by the page for the next follow-up (item 133). A third switch makes a task a campaign (Keep going until done); it is
// chosen for one goal, so it is never kept.

import { el, switchRow } from "./dom.js";
import { icon } from "./icons.js";
import { segmented } from "./panels.js";
import { readPref, writePref } from "./prefs.js";
import { getSnapshot } from "./state.js";

const APPROVALS_KEY = "dot.approvals";
const GOAL_MODE_KEY = "dot.goalMode";

export const APPROVAL_OPTIONS = [
  { id: "ask", label: "Ask", hint: "You answer every question the safety policy raises" },
  { id: "auto", label: "Auto", hint: "A review model answers in your place; what it is unsure about still comes to you" },
  { id: "yolo", label: "YOLO", hint: "Nobody is asked: what the policy would ask runs at once and is recorded" },
];
const YOLO_NOTE = "YOLO: nothing asks for confirmation, so this run acts without waiting for you. It also reads secrets, sends files out, pays and signs in by itself.";
const GOAL_MODE_HINT = "A checker compares the answer with the goal and sends the run back to work until the goal is really reached";
const STANDING_HINT = "Keep going until done: Dot works on the goal in sessions until a check confirms the outcome or you stop it";
const STANDING_NOTE = "Keep going is on: Dot works on this goal in sessions until a check confirms it; Campaigns shows it.";

/** The words for an approval mode, for chips and facts. */
export function approvalLabel(id) {
  const option = APPROVAL_OPTIONS.find((candidate) => candidate.id === id);
  return option === undefined ? id : option.label;
}

/** The approval mode this browser chose, or null when it never chose one. */
function savedApprovals() {
  const stored = readPref(APPROVALS_KEY);
  return APPROVAL_OPTIONS.some((option) => option.id === stored) ? stored : null;
}

/** Whether this browser chose goal mode on (true) or off (false), or null when it never chose. */
function savedGoalMode() {
  const stored = readPref(GOAL_MODE_KEY);
  if (stored === "on") {
    return true;
  }
  return stored === "off" ? false : null;
}

/** The home's choice of approvals, kept per browser: `read()` gives it, or null when none was made; `write(id)` keeps it. */
export const KEPT_APPROVALS = { read: savedApprovals, write: (id) => writePref(APPROVALS_KEY, id) };

/** The home's choice of goal mode, kept per browser, as `KEPT_APPROVALS`. */
export const KEPT_GOAL_MODE = { read: savedGoalMode, write: (on) => writePref(GOAL_MODE_KEY, on ? "on" : "off") };

/** A choice held by one page from `initial`, as `KEPT_APPROVALS` reads and writes: what a chat's page holds for its next
 * follow-up (item 133), and tells the daemon, which keeps it for the chat (item 199). */
export function heldChoice(initial) {
  let value = initial;
  return {
    read: () => value,
    write: (next) => {
      value = next;
    },
  };
}

function pick(caption, control) {
  return el("div", { class: "composer-pick", role: "group", "aria-label": caption }, el("span", { class: "composer-pick-label" }, caption), control);
}

/**
 * The control that picks the approval mode of a new run, its choice in `kept` (`KEPT_APPROVALS` or a `heldChoice`).
 * `value()` is the mode chosen, or null while it follows the daemon (that is what a run is started with, so a later
 * change of the daemon's default still applies); `sync()` shows the choice or the daemon's default again after the status
 * changed; `warning` is the line to show with the control. `onPaint(mode)` runs whenever the mode shown changes, with
 * null while neither a choice nor the daemon's status is known.
 */
export function approvalsPicker(onPaint, kept) {
  function shown() {
    const { status } = getSnapshot();
    return kept.read() ?? (status === null ? null : status.approvals);
  }
  const control = segmented(APPROVAL_OPTIONS, shown(), (id) => {
    kept.write(id);
    paint();
  });
  const warning = el("div", { class: "note warn", hidden: true }, icon("bolt"), el("span", null, YOLO_NOTE));
  // The mode `onPaint` was told last: `sync()` comes with every status update, and the composer's pill is drawn again
  // only when the mode it shows changed (item 310).
  let told;

  function paint() {
    control.element.dataset.mode = String(control.value());
    warning.hidden = control.value() !== "yolo";
    if (control.value() !== told) {
      told = control.value();
      onPaint(told);
    }
  }

  paint();
  return {
    element: pick("Approvals", control.element),
    warning,
    value: kept.read,
    sync: () => {
      control.set(shown());
      paint();
    },
  };
}

/**
 * The switch that makes a new task a campaign: Dot keeps going on its goal in sessions until the outcome is verified. It
 * starts off on every page. `value()` is whether it is on; `setAvailable(false)` hides it and turns it off, for the kinds of
 * run that cannot be a campaign (a chat, research); `onToggle(on)` runs after every change; `note` is the one line to show
 * under the composer while it is on.
 */
export function standingPicker(onToggle) {
  let on = false;
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Keep going until done" });
  const element = switchRow(pick("Keep going", toggle), toggle);
  element.title = STANDING_HINT;
  const note = el("div", { class: "composer-note", hidden: true }, icon("target"), el("span", null, STANDING_NOTE));

  function set(value) {
    on = value;
    toggle.setAttribute("aria-checked", String(on));
    note.hidden = !on;
    onToggle(on);
  }

  toggle.addEventListener("click", () => set(!on));
  return {
    element,
    note,
    value: () => on,
    setAvailable: (available) => {
      element.hidden = !available;
      if (!available && on) {
        set(false);
      }
    },
  };
}

/**
 * The switch that turns goal mode on or off for a new run, its choice in `kept` (`KEPT_GOAL_MODE` or a `heldChoice`).
 * `value()`, `sync()` as for the approvals; `setAvailable(false)` hides it for the kind of run that has no goal mode (a
 * chat).
 */
export function goalModePicker(kept) {
  function shown() {
    const { status } = getSnapshot();
    return kept.read() ?? status?.goal_mode === true;
  }
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Goal mode" });
  const element = switchRow(pick("Goal mode", toggle), toggle);
  element.title = GOAL_MODE_HINT;

  function paint() {
    toggle.setAttribute("aria-checked", String(shown()));
  }

  toggle.addEventListener("click", () => {
    kept.write(!shown());
    paint();
  });
  paint();
  return {
    element,
    value: kept.read,
    sync: paint,
    setAvailable: (available) => {
      element.hidden = !available;
    },
  };
}
