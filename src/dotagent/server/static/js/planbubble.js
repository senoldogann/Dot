// The plan of a run as the page draws it (items 203 and 644): its targets and its steps, in the Plan tab of the side
// panel and in the bubble that the plan's part of the line over the composer opens.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { anchoredPopover } from "./popover.js";

const STEP_MARKS = { done: "check", failed: "x", skipped: "arrowRight" };
const STEP_LABELS = { pending: "Not started", in_progress: "In progress", done: "Done", failed: "Failed", skipped: "Skipped" };

/** The targets of an open-ended goal, the first line of the plan: what the run works toward and how it measures it. */
export function planTargets(targets) {
  return el("div", { class: "plan-targets", title: "Targets" }, icon("target"), el("span", { class: "text" }, targets));
}

/** One step of the plan: its mark, its text, the steps it waits for and, under a closed step, the evidence the run
 * recorded for it. Plans stored before evidence existed have none. */
export function planStep(step) {
  const markIcon = STEP_MARKS[step.status];
  const mark = el("span", { class: "mark" }, markIcon === undefined ? null : icon(markIcon));
  const evidence = step.evidence ?? "";
  // Plans stored before dependencies existed have none.
  const dependsOn = step.depends_on ?? [];
  const line = el("span", { class: "line" }, el("span", { class: "text" }, step.text));
  if (dependsOn.length > 0) {
    line.append(el("span", { class: "after", title: "Waits for these steps" }, `after ${dependsOn.join(", ")}`));
  }
  const body = el("span", { class: "body" }, line);
  if (evidence !== "") body.append(el("span", { class: "evidence", title: "Evidence" }, evidence));
  return el("div", { class: `plan-step ${step.status}`, title: STEP_LABELS[step.status] ?? step.status }, mark, body);
}

/**
 * The plan over the composer (item 644): a click on `anchor` (the capsule's plan part) opens it as a bubble in `frame`
 * (the dock), with how far the plan has come, its targets, its steps as the Plan tab draws them, and the way to that tab
 * (`onOpenTab()`); a second click, Escape or a click outside closes it. It follows the plan while it is open. Returns
 * `paint(steps, targets)`, `close()` and `dispose()`.
 */
export function planBubble({ anchor, frame, onOpenTab }) {
  let steps = [];
  let targets = "";
  const popover = anchoredPopover({ anchor, frame, label: "Plan", build });

  function build() {
    const done = steps.filter((step) => step.status === "done").length;
    const openTab = () => {
      popover.close();
      onOpenTab();
    };
    const count = el("span", { class: "plan-popover-count" }, `${done} / ${steps.length}`);
    return [
      el("div", { class: "plan-popover-head" }, el("span", { class: "plan-popover-title" }, "Plan"), count),
      ...(targets === "" ? [] : [planTargets(targets)]),
      el("div", { class: "plan-list plan-popover-steps" }, ...steps.map(planStep)),
      el("button", { class: "plan-popover-open", type: "button", onclick: openTab }, "Open in the side panel", icon("chevron")),
    ];
  }

  return {
    paint(nextSteps, nextTargets) {
      steps = nextSteps;
      targets = nextTargets;
      if (popover.isOpen()) {
        popover.repaint();
      }
    },
    close: () => popover.close(),
    dispose: () => popover.dispose(),
  };
}
