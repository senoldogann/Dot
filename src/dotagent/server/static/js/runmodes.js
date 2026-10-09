// The two choices of a run besides its goal: who answers the questions the safety policy raises (approvals) and whether a
// checker keeps the run working until its goal is really reached (goal mode). The daemon has a default for both. A choice
// made on the home page is kept per browser and goes with every run started from there until it is changed again; a person
// who never chose anything leaves both to the daemon. A third switch makes a task a campaign (Keep going until done); it is
// chosen for one goal, so it is never kept.

import { el } from "./dom.js";
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
const STANDING_NOTE = "Keep going: Dot works on this goal in sessions, also while you are away and after restarts, until a check confirms the outcome. Campaigns in the sidebar shows its progress; stop it there.";

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

function pick(caption, control) {
  return el("div", { class: "composer-pick", role: "group", "aria-label": caption }, el("span", { class: "composer-pick-label" }, caption), control);
}

/**
 * The control that picks the approval mode of a new run. `value()` is the mode this browser chose, or null while it follows the
 * daemon (that is what a run is started with, so a later change of the daemon's default still applies); `sync()` shows the
 * choice or the daemon's default again after the status changed; `warning` is the line to show under the composer.
 */
export function approvalsPicker() {
  function shown() {
    const { status } = getSnapshot();
    return savedApprovals() ?? (status === null ? null : status.approvals);
  }
  const control = segmented(APPROVAL_OPTIONS, shown(), (id) => {
    writePref(APPROVALS_KEY, id);
    paint();
  });
  const warning = el("div", { class: "note warn", hidden: true }, icon("bolt"), el("span", null, YOLO_NOTE));

  function paint() {
    control.element.dataset.mode = String(control.value());
    warning.hidden = control.value() !== "yolo";
  }

  paint();
  return {
    element: pick("Approvals", control.element),
    warning,
    value: savedApprovals,
    sync: () => {
      control.set(shown());
      paint();
    },
  };
}

/**
 * The switch that makes a new task a campaign: Dot keeps going on its goal in sessions until the outcome is verified. It
 * starts off on every page. `value()` is whether it is on; `setAvailable(false)` hides it and turns it off, for the kinds of
 * run that cannot be a campaign (a chat, research); `onToggle(on)` runs after every change; `note` is the line to show under
 * the composer while it is on.
 */
export function standingPicker(onToggle) {
  let on = false;
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Keep going until done" });
  const element = pick("Keep going", toggle);
  element.title = STANDING_HINT;
  const note = el("div", { class: "note", hidden: true }, icon("target"), el("span", null, STANDING_NOTE));

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
 * The switch that turns goal mode on or off for a new run. `value()`, `sync()` as for the approvals; `setAvailable(false)`
 * hides it for the kind of run that has no goal mode (a chat).
 */
export function goalModePicker() {
  function shown() {
    const { status } = getSnapshot();
    return savedGoalMode() ?? (status !== null && status.goal_mode);
  }
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Goal mode" });
  const element = pick("Goal mode", toggle);
  element.title = GOAL_MODE_HINT;

  function paint() {
    toggle.setAttribute("aria-checked", String(shown()));
  }

  toggle.addEventListener("click", () => {
    writePref(GOAL_MODE_KEY, shown() ? "off" : "on");
    paint();
  });
  paint();
  return {
    element,
    value: savedGoalMode,
    sync: paint,
    setAvailable: (available) => {
      element.hidden = !available;
    },
  };
}
