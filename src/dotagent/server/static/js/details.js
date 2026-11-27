// The facts of one run for the side panel's More (item 544): its state, the agent it is a chat with, the model and the
// reasoning level it ran with, its approvals, the campaign it is a session of, the work it did and how long it took,
// its mode, its start and its id. The run page's top bar shows none of them (item 120); a glance needs none of them, so
// they fold away with how the run was set up and what it cost. The facts that never change are written once; `paint`
// brings the others up to date.

import { compact, copyText, dateTimeFull, duration, el, modelName, setText } from "./dom.js";
import { askedLabel } from "./efforts.js";
import { icon } from "./icons.js";
import { statusBadge } from "./panels.js";
import { approvalLabel } from "./runmodes.js";
import { attempt, toast } from "./ui.js";
import { t, tCount } from "./i18n.js";

function fact(label, value) {
  return [el("dt", null, label), value];
}

/**
 * The facts of `run`, of the chat with `agent` (`{ id, name, role }`, or null) or with `team` (`{ id, name }`, or null;
 * item 610) and a session of `campaign` (`{ id, title, session }`, or null), as one list (`element`); `keepGoing` says
 * that its goal asked it not to stop until it is done, which put it in goal mode (item 132), which the list names only
 * when it is on. `paint(state)` takes what changes while the run goes, as
 * `{ status, model, calls, failed, compactions, flagged, input, output, finishedAt }`; `finishedAt` is null until the
 * run is over; `compactions` counts the times the run compacted its context and `flagged` those whose check found
 * something (item 260): the list says it once there is one of either. `title` is the chat's, first in the list, or
 * null for a helper's run; `retitle(text)` names a chat renamed since (item 1101: a phone's bar names whom the chat is
 * with, and its title is here).
 */
export function createDetails(run, title, agent, campaign, keepGoing, team) {
  const titled = el("dd", null, title ?? "");
  const status = el("dd");
  const model = el("dd");
  const calls = el("dd", { class: "meta-facts" });
  const tokens = el("dd", { class: "meta-facts" });
  const contextLabel = el("dt", { hidden: true }, t("details.chatSize"));
  const context = el("dd", { class: "meta-facts", hidden: true });
  const took = el("dd");
  const copy = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: t("details.copyTheTaskId"),
      "aria-label": t("details.copyTheTaskId"),
      onclick: () =>
        attempt(async () => {
          await copyText(run.id);
          toast("ok", t("details.taskIdCopied"));
        }),
    },
    icon("copy"),
  );
  const parent =
    run.parent_run_id === null
      ? null
      : fact(t("details.handedOverBy"), el("dd", null, el("a", { href: `#/runs/${run.parent_run_id}` }, t("details.openTheTaskItCame"))));

  const element = el(
    "dl",
    { class: "facts", id: "panel-run-details", "aria-label": t("details.details") },
    title === null ? null : fact(t("details.title"), titled),
    fact(t("details.status"), status),
    agent === null ? null : fact(t("details.agent"), el("dd", null, el("a", { href: `#/agents/${agent.id}`, title: agent.role }, agent.name))),
    team === null ? null : fact(t("details.team"), el("dd", null, el("a", { href: `#/teams/${team.id}` }, team.name))),
    fact(t("details.model"), model),
    fact(t("details.reasoning"), el("dd", null, run.effort === null ? t("details.modelDefault") : askedLabel(run.effort, run.once))),
    run.speed === "fast" ? fact(t("details.speed"), el("dd", null, t("details.fastModeOnTheSteps"))) : null,
    fact(t("details.approvals"), el("dd", null, approvalLabel(run.approvals))),
    run.goal_mode ? fact(t("details.goalMode"), el("dd", null, keepGoing ? t("details.onAskedNotToStop") : t("details.on"))) : null,
    campaign === null
      ? null
      : fact(t("details.campaign"), el("dd", null, el("a", { href: `#/campaigns/${campaign.id}` }, campaign.title), t("details.sessionSession", { session: campaign.session }))),
    fact(t("details.actionsTaken"), calls),
    fact(t("details.usage"), tokens),
    contextLabel,
    context,
    fact(t("details.duration"), took),
    parent,
    fact(t("details.mode"), el("dd", null, run.mode)),
    fact(t("details.started"), el("dd", null, dateTimeFull(run.created_at))),
    fact(t("details.taskId"), el("dd", { class: "run-id" }, el("code", { class: "mono" }, run.id), copy)),
  );

  // What each fact was drawn from: the list is painted every second while the run works, and a fact is drawn again only
  // when it changed (item 310); a status badge drawn again starts its motion again.
  const drawn = new Map();
  function draw(node, from, build) {
    if (drawn.get(node) !== from) {
      drawn.set(node, from);
      node.replaceChildren(...build());
    }
  }

  function paint(state) {
    draw(status, state.status, () => [statusBadge(state.status)]);
    setText(model, state.model === "" ? t("details.default") : modelName(state.model));
    model.title = state.model;
    draw(calls, `${state.calls} ${state.failed}`, () => [el("span", null, String(state.calls)), ...(state.failed === 0 ? [] : [el("span", null, t("details.failedFailed", { failed: state.failed }))])]);
    draw(tokens, `${compact(state.input)} ${compact(state.output)}`, () => [el("span", null, t("details.compactIn", { compact: compact(state.input) })), el("span", null, t("details.compactOut", { compact: compact(state.output) }))]);
    const compacted = state.compactions === 0 ? t("details.neverSummedUp") : t("details.summedUpPart", { part: tCount(state.compactions, "details.countTime", "details.countTimes") });
    const said = state.compactions > 0 || state.flagged > 0;
    contextLabel.hidden = !said;
    context.hidden = !said;
    draw(context, `${state.compactions} ${state.flagged}`, () => [
      el("span", null, compacted),
      ...(state.flagged === 0 ? [] : [el("span", null, t("details.partHadAProblem", { part: tCount(state.flagged, "details.countSummary", "details.countSummaries") }))]),
    ]);
    const end = state.finishedAt === null ? Date.now() / 1000 : state.finishedAt;
    setText(took, duration(Math.max(0, end - run.created_at)));
  }

  function retitle(text) {
    titled.textContent = text;
  }

  return { element, paint, retitle };
}
