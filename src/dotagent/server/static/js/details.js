// The facts of one run for the side panel: its state, the model and the reasoning level it ran with, the work it did and how
// long it took. The facts that never change are written once; `paint` brings the others up to date.

import { compact, copyText, dateTimeFull, duration, el, modelName } from "./dom.js";
import { levelLabel } from "./efforts.js";
import { icon } from "./icons.js";
import { statusBadge } from "./panels.js";
import { approvalLabel } from "./runmodes.js";
import { attempt, toast } from "./ui.js";

function fact(label, value) {
  return [el("dt", null, label), value];
}

/**
 * The details card of `run`. `paint(state)` takes what changes while the run goes, as
 * `{ status, model, steps, calls, failed, input, output, finishedAt }`; `finishedAt` is null until the run is over.
 */
export function createDetails(run) {
  const status = el("dd");
  const model = el("dd");
  const turns = el("dd");
  const calls = el("dd");
  const tokens = el("dd");
  const took = el("dd");
  const copy = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Copy the run id",
      "aria-label": "Copy the run id",
      onclick: () =>
        attempt(async () => {
          await copyText(run.id);
          toast("ok", "Run id copied");
        }),
    },
    icon("copy"),
  );
  const parent =
    run.parent_run_id === null
      ? null
      : fact("Delegated by", el("dd", null, el("a", { href: `#/runs/${run.parent_run_id}` }, "Open the parent run")));

  const element = el(
    "section",
    { class: "card card-pad stack tight", "aria-label": "Details" },
    el("strong", null, "Details"),
    el(
      "dl",
      { class: "facts" },
      fact("Status", status),
      fact("Mode", el("dd", null, run.mode)),
      fact("Model", model),
      fact("Reasoning", el("dd", null, run.effort === null ? "Model default" : levelLabel(run.effort))),
      fact("Approvals", el("dd", null, approvalLabel(run.approvals))),
      fact("Goal mode", el("dd", null, run.goal_mode ? "On" : "Off")),
      fact("Model turns", turns),
      fact("Tool calls", calls),
      fact("Tokens", tokens),
      fact("Duration", took),
      fact("Started", el("dd", null, dateTimeFull(run.created_at))),
      fact("Run id", el("dd", { class: "run-id" }, el("code", { class: "mono" }, run.id), copy)),
      parent,
    ),
  );

  function paint(state) {
    status.replaceChildren(statusBadge(state.status));
    model.textContent = state.model === "" ? "Default" : modelName(state.model);
    model.title = state.model;
    turns.textContent = String(state.steps);
    calls.textContent = state.failed === 0 ? String(state.calls) : `${state.calls} · ${state.failed} failed`;
    tokens.textContent = `${compact(state.input)} in · ${compact(state.output)} out`;
    const end = state.finishedAt === null ? Date.now() / 1000 : state.finishedAt;
    took.textContent = duration(Math.max(0, end - run.created_at));
  }

  return { element, paint };
}
