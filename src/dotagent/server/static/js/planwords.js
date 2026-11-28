// The plan as its run left it, and what the plan's head says of it (the owner's report of 2026-11-28: a failed run's
// plan still showed its step in progress). Pure, so it runs under Node; the iPhone app's `planAtEnd`, `PlanEnd` and
// `planCurrentStep` (DotEngine) are the same rules.

import { t } from "./i18n.js";

/**
 * How a plan's run stands: "working", "done", "failed" or "stopped", from its status.
 * @param {string | null} status
 * @returns {"working" | "done" | "failed" | "stopped"}
 */
export function planEnd(status) {
  if (status === "done") return "done";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "stopped";
  return "working";
}

/**
 * The steps as the run left them: a step still in progress when the run failed is "failed", when it was stopped
 * "stopped"; at work or done, the steps as they are.
 * @template {{ status: string }} S
 * @param {S[]} steps
 * @param {"working" | "done" | "failed" | "stopped"} end
 * @returns {S[]}
 */
export function planAtEnd(steps, end) {
  if (end === "working" || end === "done") return steps;
  return steps.map((step) => (step.status === "in_progress" ? { ...step, status: end } : step));
}

/**
 * The step the plan stands at (1-based): the one in progress, failed or stopped, else the first not started; null when
 * every step is done or skipped.
 * @param {{ status: string }[]} steps
 * @returns {number | null}
 */
export function planCurrentStep(steps) {
  const at = steps.findIndex((step) => step.status === "in_progress" || step.status === "failed" || step.status === "stopped");
  if (at >= 0) return at + 1;
  const next = steps.findIndex((step) => step.status === "pending");
  return next >= 0 ? next + 1 : null;
}

/**
 * The plan's head: how many steps are done of how many, and where the run stands in words.
 * @param {{ status: string }[]} steps the steps as the run left them (`planAtEnd`)
 * @param {"working" | "done" | "failed" | "stopped"} end
 * @returns {{ done: number, total: number, count: string, state: string }}
 */
export function planHead(steps, end) {
  const done = steps.filter((step) => step.status === "done").length;
  const at = planCurrentStep(steps);
  const state =
    end === "done"
      ? t("planwords.done")
      : end === "failed"
        ? at === null
          ? t("planwords.failed")
          : t("planwords.failedAt", { step: at })
        : end === "stopped"
          ? at === null
            ? t("planwords.stopped")
            : t("planwords.stoppedAt", { step: at })
          : at === null
            ? t("planwords.finishing")
            : t("planwords.workingOn", { step: at });
  return { done, total: steps.length, count: t("planwords.count", { done, total: steps.length }), state };
}
