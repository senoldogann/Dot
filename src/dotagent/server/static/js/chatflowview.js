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
  /** The open question cards of the drill-in by their id, kept as they are while the flow repaints, so that what is
   * written in one, ticked and focused stays: every event of the run repaints it. */
  /** @type {Map<string, HTMLElement>} */
  const questionNodes = new Map();
  /** @type {Map<string, HTMLButtonElement>} */
  const sessionNodes = new Map();
  /** @type {Map<string, HTMLButtonElement>} */
  const railNodes = new Map();
  const activityKicker = el("span", { class: "flow-kicker" });
  const activityNow = el("span", { class: "flow-activity-now" });
  const thinkingNode = el("pre", { class: "flow-thinking" });
  const progressList = el("ul", { class: "flow-progress" });
  const stepsList = el("ol", { class: "flow-steps" });
  const activityDetails = el(
    "details",
    { class: "flow-activity-block" },
    el("summary", null, activityKicker, activityNow, icon("chevron")),
    el("div", { class: "flow-activity-body" }, thinkingNode, progressList, stepsList),
  );
  activity.append(activityDetails);
  let activityRun = "";
  activityDetails.addEventListener("toggle", () => {
    if (painting || activityRun === "") {
      return;
    }
    actions.onToggle(activityRun);
  });
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
    placeSessions(sessions, sessionNodes, rows, (session) => sessionCard(session, () => actions.onOpen(session.id)), fillSession);
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintActivity(state) {
    activityRun = state.rootId;
    if (state.view.kind === "session") {
      activity.hidden = true;
      return;
    }
    const raw = state.activity[state.rootId];
    const shown = raw === undefined ? null : presentActivity(raw);
    const busy = shown !== null && (shown.thinkingPhase === "live" || shown.answerPhase === "live" || shown.current?.status === "running" || shown.wait !== "none");
    const quiet = shown === null || (!busy && shown.steps.length === 0 && shown.progress.length === 0 && shown.thinking === "");
    activity.hidden = quiet;
    if (shown === null || quiet) {
      return;
    }
    const head = shown.current !== null ? shown.current.label : shown.wait === "question" ? "Waiting for your answer" : shown.wait === "approval" ? "Waiting for your approval" : shown.thinkingPhase === "live" ? "Thinking" : "Activity";
    activityKicker.textContent = busy ? "Now" : "Activity";
    activityNow.textContent = head;
    if (activityDetails.open !== (shown.fold === "open")) {
      activityDetails.open = shown.fold === "open";
    }
    thinkingNode.hidden = shown.thinking === "";
    thinkingNode.textContent = shown.thinking;
    fillLines(progressList, shown.progress, (line) => el("li", null, line), (item, line) => {
      item.textContent = line;
    });
    fillLines(stepsList, shown.steps.slice(-8), stepRow, fillStep);
  }

  /** @param {import("./chatflow.js").FlowState} state */
  function paintRail(state) {
    const rows = sessionsNewest(state);
    rail.hidden = rows.length === 0;
    placeSessions(
      railList,
      railNodes,
      rows,
      (session) =>
        el(
          "button",
          { class: `flow-rail-row s-${session.status}`, type: "button", onclick: () => actions.onOpen(session.id) },
          el("span", { class: "flow-dot", "aria-hidden": "true" }),
          el("span", { class: "flow-rail-title" }, session.title),
          el("span", { class: "flow-status" }, STATUS_WORD[session.status]),
        ),
      (node, session) => {
        node.className = `flow-rail-row s-${session.status}`;
        const dot = node.querySelector(".flow-dot");
        if (dot !== null) {
          dot.className = `flow-dot s-${session.status}`;
        }
        const title = node.querySelector(".flow-rail-title");
        if (title !== null) {
          title.textContent = session.title;
        }
        const status = node.querySelector(".flow-status");
        if (status !== null) {
          status.className = `flow-status s-${session.status}`;
          status.textContent = STATUS_WORD[session.status];
        }
        if (state.view.kind === "session" && state.view.id === session.id) {
          node.setAttribute("aria-current", "true");
        } else {
          node.removeAttribute("aria-current");
        }
      },
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
      ...questions.map((card) => keptQuestion(card)),
    );
    for (const id of [...questionNodes.keys()]) {
      if (!questions.some((card) => card.id === id)) {
        questionNodes.delete(id);
      }
    }
  }

  /**
   * The question card of `card`, the one already drawn when there is one.
   * @param {import("./chatflow.js").QuestionCard} card
   * @returns {HTMLElement}
   */
  function keptQuestion(card) {
    const kept = questionNodes.get(card.id);
    if (kept !== undefined) {
      return kept;
    }
    const made = questionCard(card, actions.onDecide);
    questionNodes.set(card.id, made);
    return made;
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

/**
 * Keep one node per session and only move a node when its order changed, so a repaint does not pull a node out of
 * the page.
 * @param {HTMLElement} parent
 * @param {Map<string, HTMLButtonElement>} nodes
 * @param {import("./chatflow.js").Session[]} rows
 * @param {(session: import("./chatflow.js").Session) => HTMLButtonElement} make
 * @param {(node: HTMLButtonElement, session: import("./chatflow.js").Session) => void} fill
 */
function placeSessions(parent, nodes, rows, make, fill) {
  const keep = new Set(rows.map((session) => session.id));
  for (const [id, node] of nodes) {
    if (!keep.has(id)) {
      node.remove();
      nodes.delete(id);
    }
  }
  rows.forEach((session, index) => {
    let node = nodes.get(session.id);
    if (node === undefined) {
      node = make(session);
      nodes.set(session.id, node);
    } else {
      fill(node, session);
    }
    if (parent.children[index] !== node) {
      parent.insertBefore(node, parent.children[index] ?? null);
    }
  });
}

/** @param {HTMLButtonElement} node @param {import("./chatflow.js").Session} session */
function fillSession(node, session) {
  node.className = `flow-session s-${session.status}`;
  const title = node.querySelector(".flow-session-title");
  if (title !== null) {
    title.textContent = session.title;
  }
  const status = node.querySelector(".flow-status");
  if (status !== null) {
    status.className = `flow-status s-${session.status}`;
    status.textContent = STATUS_WORD[session.status];
  }
}

/**
 * @template T
 * @param {HTMLElement} list
 * @param {T[]} lines
 * @param {(line: T) => HTMLElement} make
 * @param {(item: Element, line: T) => void} fill
 */
function fillLines(list, lines, make, fill) {
  while (list.childElementCount > lines.length) {
    list.lastElementChild?.remove();
  }
  lines.forEach((line, index) => {
    const item = list.children[index];
    if (item === undefined) {
      list.append(make(line));
    } else {
      fill(item, line);
    }
  });
}

/** @param {Element} item @param {import("./chatflow.js").FlowStep} step */
function fillStep(item, step) {
  item.className = `flow-step s-${step.status}`;
  const label = item.querySelector(".flow-step-label");
  if (label !== null) {
    label.textContent = step.label;
  }
  const status = item.querySelector(".flow-step-status");
  if (status !== null) {
    status.textContent = step.status;
  }
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
