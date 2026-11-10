// Paints the chat-flow state: a session card in the conversation, the live activity block, the sessions rail, and
// the drill-in of one session. Decisions go to the same approval route the existing card uses; this view does not
// invent a second meaning for approve or deny.

import { api } from "./api.js";
import { blankActivity, cardsOf, childSessions, presentActivity, questionAnswer, sessionsNewest } from "./chatflow.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { toastError } from "./ui.js";

const STATUS_WORD = {
  queued: "Queued",
  running: "Running",
  blocked: "Waiting",
  done: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};

/**
 * @param {{
 *   onOpen: (id: string) => void,
 *   onBack: () => void,
 *   onHideRail: () => void,
 *   onToggle: (id: string) => void,
 *   onDecide: (id: string, approve: boolean, note: string) => void,
 * }} actions
 */
export function createChatFlowView(actions) {
  const sessions = el("div", { class: "flow-sessions" });
  const activity = el("div", { class: "flow-activity" });
  const drill = el("div", { class: "flow-drill", hidden: true });
  const railList = el("div", { class: "flow-rail-list" });
  let painting = false;
  const rail = el(
    "aside",
    { class: "flow-rail", "aria-label": "Sessions" },
    el("div", { class: "flow-rail-head" }, el("span", null, "Sessions"), el("button", { class: "icon-btn", type: "button", "aria-label": "Close sessions", onclick: () => actions.onHideRail() }, icon("x"))),
    railList,
  );

  /** @param {import("./chatflow.js").FlowState} state */
  function paint(state) {
    painting = true;
    paintSessions(state);
    paintActivity(state);
    paintRail(state);
    paintDrill(state);
    painting = false;
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintSessions(state) {
    const rows = childSessions(state, state.rootId);
    sessions.hidden = rows.length === 0 || state.view.kind === "session";
    sessions.replaceChildren(...rows.map((session) => sessionCard(session, () => actions.onOpen(session.id))));
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintActivity(state) {
    if (state.view.kind === "session") {
      activity.hidden = true;
      return;
    }
    const raw = state.activity[state.rootId];
    if (raw === undefined) {
      activity.hidden = true;
      activity.replaceChildren();
      return;
    }
    const shown = presentActivity(raw);
    const busy = shown.thinkingPhase === "live" || shown.answerPhase === "live" || shown.current?.status === "running" || shown.wait !== "none";
    if (!busy && shown.steps.length === 0 && shown.progress.length === 0 && shown.thinking === "") {
      activity.hidden = true;
      activity.replaceChildren();
      return;
    }
    activity.hidden = false;
    const open = shown.fold === "open";
    const head = shown.current !== null ? shown.current.label : shown.wait === "question" ? "Waiting for your answer" : shown.wait === "approval" ? "Waiting for your approval" : shown.thinkingPhase === "live" ? "Thinking" : "Activity";
    const details = el("details", { class: "flow-activity-block", open });
    details.addEventListener("toggle", () => {
      if (painting) {
        return;
      }
      const now = /** @type {HTMLDetailsElement} */ (details).open ? "open" : "closed";
      if (now !== shown.fold) {
        actions.onToggle(state.rootId);
      }
    });
    details.append(
      el("summary", null, el("span", { class: "flow-kicker" }, busy ? "Now" : "Activity"), el("span", { class: "flow-activity-now" }, head), icon("chevron")),
      el(
        "div",
        { class: "flow-activity-body" },
        shown.thinking === "" ? null : el("pre", { class: "flow-thinking" }, shown.thinking),
        shown.progress.length === 0 ? null : el("ul", { class: "flow-progress" }, shown.progress.map((line) => el("li", null, line))),
        shown.steps.length === 0 ? null : el("ol", { class: "flow-steps" }, shown.steps.slice(-8).map(stepRow)),
      ),
    );
    activity.replaceChildren(details);
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintRail(state) {
    const rows = sessionsNewest(state);
    rail.hidden = rows.length === 0;
    railList.replaceChildren(
      ...rows.map((session) => {
        const button = el(
          "button",
          { class: `flow-rail-row s-${session.status}`, type: "button", onclick: () => actions.onOpen(session.id) },
          el("span", { class: `flow-dot s-${session.status}`, "aria-hidden": "true" }),
          el("span", { class: "flow-rail-title" }, session.title),
          el("span", { class: `flow-status s-${session.status}` }, STATUS_WORD[session.status]),
        );
        if (state.view.kind === "session" && state.view.id === session.id) {
          button.setAttribute("aria-current", "true");
        }
        return button;
      }),
    );
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintDrill(state) {
    if (state.view.kind !== "session") {
      drill.hidden = true;
      drill.replaceChildren();
      return;
    }
    const session = state.sessions[state.view.id];
    if (session === undefined) {
      drill.hidden = true;
      return;
    }
    const activityOf = presentActivity(state.activity[session.id] ?? blankActivity());
    const questions = cardsOf(state, session.id).filter(isOpenQuestion);
    const reports = cardsOf(state, session.id).filter(isReport);
    drill.hidden = false;
    drill.replaceChildren(
      el(
        "div",
        { class: "flow-drill-bar" },
        el("button", { class: "btn ghost sm", type: "button", onclick: () => actions.onBack() }, "Back"),
        el("span", { class: `flow-status s-${session.status}` }, STATUS_WORD[session.status]),
      ),
      el("h2", { class: "flow-drill-title" }, session.title),
      session.prompt === "" ? null : el("pre", { class: "flow-prompt" }, session.prompt),
      activityOf.thinking === "" ? null : el("pre", { class: "flow-thinking" }, activityOf.thinking),
      activityOf.progress.length === 0 ? null : el("ul", { class: "flow-progress" }, activityOf.progress.map((line) => el("li", null, line))),
      activityOf.steps.length === 0 ? null : el("ol", { class: "flow-steps" }, activityOf.steps.map(stepRow)),
      ...reports.map((card) => el("a", { class: "flow-report", href: card.href }, el("span", { class: "flow-kicker" }, "Page"), el("span", null, card.title))),
      ...childSessions(state, session.id).map((child) => sessionCard(child, () => actions.onOpen(child.id))),
      ...questions.map((card) => questionCard(card, actions.onDecide)),
    );
  }

  return { sessions, activity, drill, rail, paint };
}

/** @param {import("./chatflow.js").FlowCard} card @returns {card is import("./chatflow.js").QuestionCard} */
function isOpenQuestion(card) {
  return card.kind === "question" && card.phase === "open";
}

/** @param {import("./chatflow.js").FlowCard} card @returns {card is import("./chatflow.js").ReportCard} */
function isReport(card) {
  return card.kind === "report";
}

/** @param {import("./chatflow.js").Session} session @param {() => void} onOpen */
function sessionCard(session, onOpen) {
  return el(
    "button",
    { class: `flow-session s-${session.status}`, type: "button", onclick: onOpen },
    el("span", { class: "flow-kicker" }, "Session"),
    el("span", { class: "flow-session-title" }, session.title),
    el("span", { class: `flow-status s-${session.status}` }, STATUS_WORD[session.status]),
    el("span", { class: "flow-session-open" }, "Open", icon("chevron")),
  );
}

/** @param {import("./chatflow.js").FlowStep} step */
function stepRow(step) {
  return el("li", { class: `flow-step s-${step.status}` }, el("span", { class: "flow-step-label" }, step.label), el("span", { class: "flow-step-status" }, step.status));
}

/**
 * Option buttons and a free-text line. Sending uses the note the approval route already stores; it does not
 * change what an approval means.
 * @param {import("./chatflow.js").QuestionCard} card
 * @param {(id: string, approve: boolean, note: string) => void} onDecide
 */
function questionCard(card, onDecide) {
  const note = el("input", { class: "input", type: "text", maxlength: "2000", placeholder: card.options.length > 0 ? "Or write your own answer" : "Your answer", "aria-label": "Your answer" });
  const ticks = card.multiple ? card.options.map((option) => el("input", { type: "checkbox", value: option.label })) : [];
  const send = (labels) => {
    const answer = questionAnswer(labels, note.value);
    if (answer === "") {
      note.focus();
      return;
    }
    onDecide(card.id, true, answer);
  };
  const options = card.multiple
    ? card.options.map((option, index) => el("label", { class: "choice" }, ticks[index], option.label))
    : card.options.map((option) => el("button", { class: "choice", type: "button", onclick: () => send([option.label]) }, option.label, option.recommended ? el("span", { class: "badge b-info" }, "Recommended") : null));
  return el(
    "div",
    { class: "flow-question" },
    el("span", { class: "flow-kicker" }, "Question"),
    el("p", { class: "flow-question-prompt" }, card.prompt),
    options.length === 0 ? null : el("div", { class: "choices" }, options),
    el(
      "div",
      { class: "flow-question-actions" },
      note,
      el("button", { class: "btn", type: "button", onclick: () => onDecide(card.id, false, "") }, "Skip"),
      el(
        "button",
        {
          class: "btn primary",
          type: "button",
          onclick: () => send(ticks.filter((tick) => tick.checked).map((tick) => tick.value)),
        },
        "Send answer",
      ),
    ),
  );
}

/** The decision route the conversation's approval card already posts to. */
export async function decideApproval(id, approve, note) {
  try {
    await api.post(`/approvals/${encodeURIComponent(id)}/decision`, { approve, note });
  } catch (error) {
    toastError(error);
  }
}
