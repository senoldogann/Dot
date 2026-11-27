// The plan of a run as the page draws it (items 203 and 644): its targets and its steps, in the Plan tab of the side
// panel and in the bubble that the plan's part of the line over the composer opens.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { anchoredPopover } from "./popover.js";
import { t } from "./i18n.js";
import { planAtEnd, planEnd, planHead } from "./planwords.js";

const STEP_MARKS = { done: "check", failed: "x", skipped: "arrowRight", stopped: "stop" };
// How a done step stands against the run's record when nothing of it shows the step done (`proof`, item 1745).
const UNSHOWN = new Set(["none", "unverified"]);
const STEP_LABELS = {
  pending: t("planbubble.notStarted"),
  in_progress: t("planbubble.inProgress"),
  done: t("planbubble.done"),
  failed: t("planbubble.failed"),
  skipped: t("planbubble.skipped"),
  stopped: t("planwords.stoppedStep"),
};

/** Whether `step` is marked done and no tool result of the run shows it (item 1745); a plan stored before has no
 * `proof`. */
export function unshownStep(step) {
  return step.status === "done" && UNSHOWN.has(step.proof ?? "");
}

/** The targets of an open-ended goal, the first line of the plan: what the run works toward and how it measures it. */
export function planTargets(targets) {
  return el("div", { class: "plan-targets", title: t("planbubble.targets") }, icon("target"), el("span", { class: "text" }, targets));
}

/** One step of the plan: its mark, its text, the steps it waits for, "not verified" for a done step no tool result
 * shows, and, under a closed step, the evidence the run recorded for it. Plans stored before evidence existed have
 * none. */
export function planStep(step) {
  const markIcon = STEP_MARKS[step.status];
  const mark = el("span", { class: "mark" }, markIcon === undefined ? null : icon(markIcon));
  const evidence = step.evidence ?? "";
  // Plans stored before dependencies existed have none.
  const dependsOn = step.depends_on ?? [];
  const line = el("span", { class: "line" }, el("span", { class: "text" }, step.text));
  if (dependsOn.length > 0) {
    line.append(el("span", { class: "after", title: t("planbubble.waitsForTheseSteps") }, t("planbubble.afterJoin", { join: dependsOn.join(", ") })));
  }
  // A step marked done that no tool result of the run shows (item 1745): the run's record says so, beside its words.
  if (unshownStep(step)) {
    line.append(el("span", { class: "unshown", title: t("planbubble.noToolResultOfThe") }, t("planbubble.notVerified")));
  }
  const body = el("span", { class: "body" }, line);
  if (evidence !== "") body.append(el("span", { class: "evidence", title: t("planbubble.evidence") }, evidence));
  return el("div", { class: `plan-step ${step.status}`, title: STEP_LABELS[step.status] ?? step.status }, mark, body);
}

/**
 * The plan's head (the owner's report of 2026-11-28): how many steps are done of how many, where the run stands ("Working
 * on step 3", "Failed at step 2") and a thin bar of how far it came, red once the run failed.
 * @param {{ status: string }[]} steps the steps as the run left them
 * @param {"working" | "done" | "failed" | "stopped"} end
 */
export function planHeadView(steps, end) {
  const head = planHead(steps, end);
  const fill = el("span", { class: "plan-bar-fill" });
  fill.style.width = head.total === 0 ? "0%" : `${(100 * head.done) / head.total}%`;
  return el(
    "div",
    { class: "plan-head", "data-end": end },
    el("span", { class: "plan-head-count" }, head.count),
    el("span", { class: "plan-head-state" }, head.state),
    el("span", { class: "plan-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(head.total), "aria-valuenow": String(head.done), "aria-label": head.count }, fill),
  );
}

/**
 * What the plan's bubble and a phone's sheet of it (item 1383) hold: the head (how far the plan came and where the run
 * stands), its targets, its steps on a line that joins their marks, as the run left them (a step in progress when the
 * run failed or was stopped says so), and the way to the Plan tab, a button that calls `openTab()`. `status` is the run's.
 */
export function planContent({ steps, targets, status, openTab }) {
  const end = planEnd(status);
  const shown = planAtEnd(steps, end);
  return [
    planHeadView(shown, end),
    ...(targets === "" ? [] : [planTargets(targets)]),
    el("div", { class: "plan-list plan-timeline plan-popover-steps" }, ...shown.map(planStep)),
    el("button", { class: "plan-popover-open", type: "button", onclick: openTab }, t("planbubble.openInTheSidePanel"), icon("chevron")),
  ];
}

/**
 * The plan over the composer (item 644): a click on `anchor` (the capsule's plan part) opens it as a bubble in `frame`
 * (the dock), with how far the plan has come, its targets, its steps as the Plan tab draws them, and the way to that tab
 * (`onOpenTab()`); a second click, Escape or a click outside closes it. It follows the plan and the run's status while it
 * is open. Returns `paint(steps, targets, status)`, `close()` and `dispose()`.
 */
export function planBubble({ anchor, frame, onOpenTab }) {
  let steps = [];
  let targets = "";
  /** @type {string | null} */
  let status = null;
  const popover = anchoredPopover({ anchor, frame, label: t("planbubble.plan"), build });

  function build() {
    return planContent({
      steps,
      targets,
      status,
      openTab: () => {
        popover.close();
        onOpenTab();
      },
    });
  }

  return {
    paint(nextSteps, nextTargets, nextStatus) {
      steps = nextSteps;
      targets = nextTargets;
      status = nextStatus;
      if (popover.isOpen()) {
        popover.repaint();
      }
    },
    close: () => popover.close(),
    dispose: () => popover.dispose(),
  };
}
