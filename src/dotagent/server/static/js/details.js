// The facts of one run for the side panel: its state, the agent it is a chat with, the model and the reasoning level it ran
// with, its approvals, the campaign it is a session of, the work it did and how long it took. The run page's top bar
// shows none of them (item 120). The facts that never change are written once; `paint` brings the others up to date.

import { compact, copyText, dateTimeFull, duration, el, modelName } from "./dom.js";
import { levelLabel } from "./efforts.js";
import { icon } from "./icons.js";
import { panelSection, statusBadge } from "./panels.js";
import { approvalLabel } from "./runmodes.js";
import { attempt, toast } from "./ui.js";

function fact(label, value) {
  return [el("dt", null, label), value];
}

/**
 * The details card of `run`, of the chat with `agent` (`{ id, name, role }`, or null) and a session of `campaign`
 * (`{ id, title, session }`, or null); `keepGoing` says that its goal asked it not to stop until it is done, which put
 * it in goal mode (item 132). `paint(state)` takes what changes while the run goes, as
 * `{ status, model, steps, calls, failed, input, output, finishedAt }`; `finishedAt` is null until the run is over.
 */
export function createDetails(run, agent, campaign, keepGoing) {
  const status = el("dd");
  const model = el("dd");
  const turns = el("dd");
  const calls = el("dd", { class: "meta-facts" });
  const tokens = el("dd", { class: "meta-facts" });
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

  const card = panelSection({
    id: "run-details",
    title: "Details",
    actions: null,
    body: el(
      "dl",
      { class: "facts" },
      fact("Status", status),
      agent === null ? null : fact("Agent", el("dd", null, el("a", { href: `#/agents/${agent.id}`, title: agent.role }, agent.name))),
      fact("Mode", el("dd", null, run.mode)),
      fact("Model", model),
      fact("Reasoning", el("dd", null, run.effort === null ? "Model default" : levelLabel(run.effort))),
      run.speed === "fast" ? fact("Speed", el("dd", null, "Fast mode, on the steps of a model that has it")) : null,
      fact("Approvals", el("dd", null, approvalLabel(run.approvals))),
      fact("Goal mode", el("dd", null, run.goal_mode ? (keepGoing ? "On, asked not to stop" : "On") : "Off")),
      campaign === null
        ? null
        : fact("Campaign", el("dd", null, el("a", { href: `#/campaigns/${campaign.id}` }, campaign.title), `, session ${campaign.session}`)),
      fact("Model turns", turns),
      fact("Tool calls", calls),
      fact("Tokens", tokens),
      fact("Duration", took),
      fact("Started", el("dd", null, dateTimeFull(run.created_at))),
      fact("Run id", el("dd", { class: "run-id" }, el("code", { class: "mono" }, run.id), copy)),
      parent,
    ),
  });
  const element = card.element;
  element.setAttribute("aria-label", "Details");

  function paint(state) {
    status.replaceChildren(statusBadge(state.status));
    model.textContent = state.model === "" ? "Default" : modelName(state.model);
    model.title = state.model;
    turns.textContent = String(state.steps);
    calls.replaceChildren(...[el("span", null, String(state.calls)), ...(state.failed === 0 ? [] : [el("span", null, `${state.failed} failed`)])]);
    tokens.replaceChildren(el("span", null, `${compact(state.input)} in`), el("span", null, `${compact(state.output)} out`));
    const end = state.finishedAt === null ? Date.now() / 1000 : state.finishedAt;
    took.textContent = duration(Math.max(0, end - run.created_at));
  }

  return { element, paint };
}
