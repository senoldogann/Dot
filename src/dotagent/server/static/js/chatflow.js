// The chat's structured transcript: sessions, the live activity of a run, and the cards that stand in the
// conversation. One pure transition owns every change. The desktop window and the phone's web view both paint the
// state this module returns; neither keeps a second copy of it.
//
// A card's phase is the only place a question, an approval or a report can be. A session's status is the only place a
// delegated run can be. Nothing here decides whether a step is allowed: an approval card records the decision the
// daemon already stored.

/** @typedef {"queued" | "running" | "blocked" | "done" | "failed" | "cancelled"} SessionStatus */

/**
 * @typedef {{
 *   kind: "session",
 *   id: string,
 *   parentId: string,
 *   title: string,
 *   prompt: string,
 *   status: SessionStatus,
 * }} Session
 */

/** @typedef {"running" | "done" | "failed"} StepStatus */

/**
 * @typedef {{
 *   id: string,
 *   tool: string,
 *   label: string,
 *   status: StepStatus,
 * }} FlowStep
 */

/** @typedef {"idle" | "live" | "done"} StreamPhase */

/** @typedef {"none" | "question" | "approval"} WaitKind */

/** @typedef {"open" | "closed"} Fold */

/**
 * @typedef {{
 *   fold: Fold,
 *   thinking: { text: string, phase: StreamPhase },
 *   steps: FlowStep[],
 *   progress: { id: string, text: string }[],
 *   answer: { text: string, phase: StreamPhase },
 *   wait: WaitKind,
 * }} Activity
 */

/**
 * @typedef {{ label: string, description: string, recommended: boolean }} QuestionOption
 */

/** @typedef {"open" | "answered" | "skipped" | "expired"} QuestionPhase */

/**
 * @typedef {{
 *   kind: "question",
 *   id: string,
 *   runId: string,
 *   prompt: string,
 *   options: QuestionOption[],
 *   multiple: boolean,
 *   phase: QuestionPhase,
 *   answer: string,
 * }} QuestionCard
 */

/** @typedef {"pending" | "approved" | "denied" | "expired"} ApprovalPhase */

/**
 * @typedef {{
 *   kind: "approval",
 *   id: string,
 *   runId: string,
 *   summary: string,
 *   severity: string,
 *   phase: ApprovalPhase,
 *   note: string,
 * }} ApprovalCard
 */

/**
 * @typedef {{
 *   kind: "report",
 *   id: string,
 *   runId: string,
 *   title: string,
 *   href: string,
 * }} ReportCard
 */

/**
 * @typedef {{
 *   kind: "link",
 *   id: string,
 *   runId: string,
 *   title: string,
 *   href: string,
 * }} LinkCard
 */

/** @typedef {QuestionCard | ApprovalCard | ReportCard | LinkCard} FlowCard */

/** @typedef {{ kind: "chat" } | { kind: "session", id: string }} FlowView */

/**
 * @typedef {{
 *   rootId: string,
 *   sessions: Record<string, Session>,
 *   sessionOrder: string[],
 *   activity: Record<string, Activity>,
 *   cards: FlowCard[],
 *   view: FlowView,
 * }} FlowState
 */

/**
 * A stored event of a run, as the stream hands it to the page.
 * @typedef {{ kind: string, run_id: string, ts?: number, data?: Record<string, unknown> }} FlowRecord
 */

/** @typedef {{ type: "record", record: FlowRecord } | { type: "open_session", id: string } | { type: "close_session" } | { type: "toggle_activity", id: string }} FlowEvent */

const TERMINAL = new Set(["done", "failed", "cancelled"]);
const SESSION_STATUS = new Set(["queued", "running", "blocked", "done", "failed", "cancelled"]);
const PROGRESS_LINE = /^(?:progress|update|status|note)\s*[:：]\s*(.+)$/i;
const THINKING_BLOCK = /<(?:thinking|think)>([\s\S]*?)<\/(?:thinking|think)>/gi;
const LABEL_KEYS = ["title", "goal", "command", "url", "path", "query", "name"];
// The join of several chosen options, the same string the approval card sends (`CHOICES_JOIN` in approvals.js).
const CHOICES_JOIN = "; ";

/** @returns {Activity} */
export function blankActivity() {
  return {
    fold: "open",
    thinking: { text: "", phase: "idle" },
    steps: [],
    progress: [],
    answer: { text: "", phase: "idle" },
    wait: "none",
  };
}

/** @param {string} rootId @returns {FlowState} */
export function emptyFlow(rootId) {
  return { rootId, sessions: {}, sessionOrder: [], activity: {}, cards: [], view: { kind: "chat" } };
}

/**
 * Pull a streamed reply apart: a thinking block, progress lines, and the answer that remains.
 * Progress is never left inside the answer.
 * @param {string} text
 */
export function splitStream(text) {
  const thoughts = [];
  const without = text.replace(THINKING_BLOCK, (_all, body) => {
    const kept = String(body).trim();
    if (kept !== "") {
      thoughts.push(kept);
    }
    return "";
  });
  const progress = [];
  const answer = [];
  for (const line of without.split("\n")) {
    const match = PROGRESS_LINE.exec(line.trim());
    if (match !== null && match[1].trim() !== "") {
      progress.push(match[1].trim());
    } else {
      answer.push(line);
    }
  }
  return { thinking: thoughts.join("\n\n"), progress, answer: answer.join("\n").replace(/^\n+|\n+$/g, "") };
}

/**
 * The one text a question card sends when the reader chose `labels` and wrote `written`.
 * @param {string[]} labels
 * @param {string} written
 */
export function questionAnswer(labels, written) {
  return [labels.filter((label) => label.trim() !== "").join(CHOICES_JOIN), written.trim()].filter((part) => part !== "").join("\n");
}

/**
 * What the activity block shows: stored thinking and steps, plus progress lines lifted out of the answer.
 * @param {Activity} activity
 */
export function presentActivity(activity) {
  const split = splitStream(activity.answer.text);
  const thinking = [activity.thinking.text.trim(), split.thinking].filter((part) => part !== "").join("\n\n");
  return {
    fold: activity.fold,
    thinking,
    thinkingPhase: activity.thinking.phase,
    progress: [...activity.progress.map((item) => item.text), ...split.progress],
    steps: activity.steps,
    current: currentStep(activity.steps),
    answer: split.answer,
    answerPhase: activity.answer.phase,
    wait: activity.wait,
  };
}

/** @param {FlowStep[]} steps */
function currentStep(steps) {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    if (steps[index].status === "running") {
      return steps[index];
    }
  }
  return steps.length === 0 ? null : steps[steps.length - 1];
}

/** Sessions newest first. @param {FlowState} state */
export function sessionsNewest(state) {
  return [...state.sessionOrder].reverse().map((id) => state.sessions[id]);
}

/** Sessions whose parent is `parentId`, in start order. @param {FlowState} state @param {string} parentId */
export function childSessions(state, parentId) {
  return state.sessionOrder.map((id) => state.sessions[id]).filter((session) => session.parentId === parentId);
}

/** @param {FlowState} state @param {string} runId */
export function cardsOf(state, runId) {
  return state.cards.filter((card) => card.runId === runId);
}

/**
 * The next state. A transition that cannot happen (a finished session told to run, a decision for a card that is
 * not open, a session that was never started) returns the same state.
 * @param {FlowState} state
 * @param {FlowEvent} event
 * @returns {FlowState}
 */
export function transition(state, event) {
  switch (event.type) {
    case "record":
      return applyRecord(state, event.record);
    case "open_session":
      return state.sessions[event.id] === undefined ? state : { ...state, view: { kind: "session", id: event.id } };
    case "close_session":
      return state.view.kind === "chat" ? state : { ...state, view: { kind: "chat" } };
    case "toggle_activity":
      return mapActivity(state, event.id, (activity) => ({ ...activity, fold: activity.fold === "open" ? "closed" : "open" }));
    default:
      return state;
  }
}

/** @param {FlowState} state @param {FlowRecord} record @returns {FlowState} */
function applyRecord(state, record) {
  const data = record.data ?? {};
  const runId = record.run_id;
  switch (record.kind) {
    case "run_queued":
      return queueSession(state, runId, data);
    case "run_started":
      return startSession(state, runId);
    case "run_finished":
      return finishRun(state, runId, data);
    case "delta":
      return applyDelta(state, runId, data);
    case "tool_call":
      return startStep(state, runId, data);
    case "tool_result":
      return finishStep(state, runId, data);
    case "notify":
      return addProgress(state, runId, `notify:${String(data.id ?? record.ts ?? runId)}`, textOf(data, "title", "message"));
    case "plan":
      return applyPlan(state, runId, data);
    case "stall":
      return addProgress(state, runId, `stall:${runId}:${record.ts ?? state.cards.length}`, textOf(data, "detail"));
    case "approval_requested":
      return ask(state, runId, data, true);
    case "question_left":
      return ask(state, runId, data, false);
    case "approval_decided":
    case "approval_auto":
      return decide(state, data);
    case "page_shown":
      return showPage(state, runId, data);
    default:
      return state;
  }
}

/** @param {Record<string, unknown>} data @param {string} key */
function stringField(data, key) {
  const value = data[key];
  return typeof value === "string" ? value : "";
}

/** @param {Record<string, unknown>} data @param {...string} keys */
function textOf(data, ...keys) {
  return keys
    .map((key) => stringField(data, key).trim())
    .filter((part) => part !== "")
    .join(" — ");
}

/** @param {unknown} status */
function asSessionStatus(status) {
  return typeof status === "string" && SESSION_STATUS.has(status) ? /** @type {SessionStatus} */ (status) : null;
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function queueSession(state, runId, data) {
  if (runId === state.rootId || state.sessions[runId] !== undefined) {
    return state;
  }
  const parent = stringField(data, "parent_run_id");
  const parentId = parent !== "" ? parent : state.rootId;
  const prompt = stringField(data, "goal");
  const named = stringField(data, "agent_name").trim();
  /** @type {Session} */
  const session = {
    kind: "session",
    id: runId,
    parentId,
    title: named !== "" ? named : clipTitle(prompt),
    prompt,
    status: "queued",
  };
  return { ...state, sessions: { ...state.sessions, [runId]: session }, sessionOrder: [...state.sessionOrder, runId] };
}

/** @param {string} prompt */
function clipTitle(prompt) {
  const line = prompt.trim().split("\n")[0] ?? "";
  if (line === "") {
    return "Session";
  }
  return line.length > 80 ? `${line.slice(0, 79)}…` : line;
}

/** @param {FlowState} state @param {string} runId @returns {FlowState} */
function startSession(state, runId) {
  const session = state.sessions[runId];
  if (session === undefined || TERMINAL.has(session.status) || session.status === "running") {
    return state;
  }
  /** @type {Session} */
  const running = { ...session, status: "running" };
  return { ...state, sessions: { ...state.sessions, [runId]: running } };
}

/** @param {FlowState} state @param {string} runId @param {SessionStatus} status @returns {FlowState} */
function setSessionStatus(state, runId, status) {
  const session = state.sessions[runId];
  if (session === undefined || TERMINAL.has(session.status) || session.status === status) {
    return state;
  }
  /** @type {Session} */
  const next = { ...session, status };
  return { ...state, sessions: { ...state.sessions, [runId]: next } };
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function finishRun(state, runId, data) {
  const status = asSessionStatus(data.status) ?? "failed";
  /** @type {SessionStatus} */
  const ended = status === "queued" || status === "running" || status === "blocked" ? "failed" : status;
  let next = setSessionStatus(state, runId, ended);
  const result = stringField(data, "result");
  next = mapActivity(next, runId, (activity) => ({
    ...activity,
    thinking: activity.thinking.phase === "live" ? { ...activity.thinking, phase: "done" } : activity.thinking,
    steps: activity.steps.map((step) => (step.status === "running" ? { ...step, status: /** @type {StepStatus} */ (ended === "done" ? "done" : "failed") } : step)),
    answer: { text: result !== "" ? result : activity.answer.text, phase: "done" },
    wait: "none",
  }));
  return next;
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function applyDelta(state, runId, data) {
  const kind = stringField(data, "kind");
  const text = stringField(data, "text");
  if (kind === "reset") {
    return mapActivity(state, runId, (activity) => ({
      ...activity,
      thinking: { text: "", phase: "idle" },
      answer: activity.answer.phase === "done" ? activity.answer : { text: "", phase: "idle" },
    }));
  }
  if (kind === "thinking") {
    return mapActivity(state, runId, (activity) => ({
      ...activity,
      thinking: { text: activity.thinking.text + text, phase: "live" },
    }));
  }
  if (kind === "text") {
    return mapActivity(state, runId, (activity) => ({
      ...activity,
      thinking: activity.thinking.phase === "live" ? { ...activity.thinking, phase: "done" } : activity.thinking,
      answer: { text: activity.answer.text + text, phase: "live" },
    }));
  }
  return state;
}

/** @param {Record<string, unknown>} args */
function stepLabel(tool, args) {
  const bare = tool.replace(/^mcp__.*?__/, "");
  for (const key of LABEL_KEYS) {
    const value = args[key];
    if (typeof value === "string" && value.trim() !== "") {
      const line = value.trim().split("\n")[0];
      return `${bare}: ${line.length > 80 ? `${line.slice(0, 79)}…` : line}`;
    }
  }
  return bare;
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function startStep(state, runId, data) {
  const tool = stringField(data, "tool");
  const callId = stringField(data, "call_id");
  if (tool === "" || callId === "") {
    return state;
  }
  const args = data.args !== null && typeof data.args === "object" ? /** @type {Record<string, unknown>} */ (data.args) : {};
  let next = ensureSession(state, runId);
  if (tool === "notify") {
    next = addProgress(next, runId, `notify:${callId}`, textOf(args, "title", "message"));
  }
  return mapActivity(next, runId, (activity) => {
    if (activity.steps.some((step) => step.id === callId)) {
      return activity;
    }
    return { ...activity, steps: [...activity.steps, { id: callId, tool, label: stepLabel(tool, args), status: "running" }] };
  });
}

/** @param {FlowState} state @param {string} runId */
function ensureSession(state, runId) {
  if (runId === state.rootId || state.sessions[runId] !== undefined) {
    return state;
  }
  /** @type {Session} */
  const session = { kind: "session", id: runId, parentId: state.rootId, title: "Session", prompt: "", status: "running" };
  return { ...state, sessions: { ...state.sessions, [runId]: session }, sessionOrder: [...state.sessionOrder, runId] };
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function finishStep(state, runId, data) {
  const callId = stringField(data, "call_id");
  if (callId === "") {
    return state;
  }
  /** @type {StepStatus} */
  const status = data.ok === false ? "failed" : "done";
  return mapActivity(ensureSession(state, runId), runId, (activity) => {
    const known = activity.steps.some((step) => step.id === callId);
    const steps = known
      ? activity.steps.map((step) => (step.id === callId && step.status === "running" ? { ...step, status } : step))
      : [...activity.steps, { id: callId, tool: stringField(data, "tool") || "tool", label: stringField(data, "tool") || "tool", status }];
    return { ...activity, steps };
  });
}

/** @param {FlowState} state @param {string} runId @param {string} id @param {string} text */
function addProgress(state, runId, id, text) {
  const line = text.trim();
  if (line === "") {
    return state;
  }
  return mapActivity(state, runId, (activity) => {
    const at = activity.progress.findIndex((item) => item.id === id);
    if (at === -1) {
      return { ...activity, progress: [...activity.progress, { id, text: line }] };
    }
    if (activity.progress[at].text === line) {
      return activity;
    }
    return { ...activity, progress: activity.progress.map((item, index) => (index === at ? { id, text: line } : item)) };
  });
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function applyPlan(state, runId, data) {
  if (!Array.isArray(data.steps)) {
    return state;
  }
  let next = state;
  data.steps.forEach((step, index) => {
    if (step === null || typeof step !== "object") {
      return;
    }
    const row = /** @type {Record<string, unknown>} */ (step);
    const label = stringField(row, "text").trim();
    const status = stringField(row, "status");
    if (label === "") {
      return;
    }
    next = addProgress(next, runId, `plan:${runId}:${index}`, status === "" ? label : `${status}: ${label}`);
  });
  return next;
}

/** @param {unknown} option */
function asOption(option) {
  if (typeof option === "string") {
    const label = option.trim();
    return label === "" ? null : { label, description: "", recommended: false };
  }
  if (option === null || typeof option !== "object") {
    return null;
  }
  const row = /** @type {Record<string, unknown>} */ (option);
  const label = stringField(row, "label").trim();
  if (label === "") {
    return null;
  }
  return { label, description: stringField(row, "description"), recommended: row.recommended === true };
}

/**
 * A question or an approval. `holds` is true when the run waits on it (`approval_requested`); a question the run
 * left (`question_left`) stays open and the run goes on.
 * @param {FlowState} state
 * @param {string} runId
 * @param {Record<string, unknown>} data
 * @param {boolean} holds
 */
function ask(state, runId, data, holds) {
  const id = stringField(data, "approval_id");
  if (id === "" || state.cards.some((card) => card.id === id)) {
    return state;
  }
  const args = data.args !== null && typeof data.args === "object" ? /** @type {Record<string, unknown>} */ (data.args) : {};
  const severity = stringField(data, "severity");
  let next = state;
  if (severity === "question") {
    const options = Array.isArray(args.options) ? args.options.map(asOption).filter((option) => option !== null) : [];
    /** @type {QuestionCard} */
    const card = {
      kind: "question",
      id,
      runId,
      prompt: stringField(args, "question") || stringField(data, "summary"),
      options,
      multiple: args.multiple === true,
      phase: "open",
      answer: "",
    };
    next = { ...state, cards: [...state.cards, card] };
    if (!holds) {
      return next;
    }
    next = setSessionStatus(next, runId, "blocked");
    return mapActivity(next, runId, (activity) => ({ ...activity, wait: "question" }));
  }
  /** @type {ApprovalCard} */
  const card = { kind: "approval", id, runId, summary: stringField(data, "summary"), severity: severity || "medium", phase: "pending", note: "" };
  next = { ...state, cards: [...state.cards, card] };
  if (!holds) {
    return next;
  }
  if (runId !== state.rootId) {
    next = setSessionStatus(next, runId, "blocked");
  }
  return mapActivity(next, runId, (activity) => ({ ...activity, wait: "approval" }));
}

/** @param {FlowState} state @param {Record<string, unknown>} data */
function decide(state, data) {
  const id = stringField(data, "approval_id");
  const status = stringField(data, "status");
  const note = stringField(data, "note");
  const index = state.cards.findIndex((card) => card.id === id);
  if (index === -1) {
    return state;
  }
  const card = state.cards[index];
  if (card.kind === "report" || card.kind === "link") {
    return state;
  }
  if (card.kind === "question" ? card.phase !== "open" : card.phase !== "pending") {
    return state;
  }
  /** @type {FlowCard} */
  let settled;
  if (card.kind === "question") {
    const phase = status === "approved" ? "answered" : status === "expired" ? "expired" : "skipped";
    settled = { ...card, phase, answer: note };
  } else {
    const phase = status === "approved" || status === "denied" || status === "expired" ? status : "expired";
    settled = { ...card, phase, note };
  }
  const cards = state.cards.map((item, at) => (at === index ? settled : item));
  let next = { ...state, cards };
  const stillWaiting = cards.some((item) => item.runId === card.runId && (item.kind === "question" ? item.phase === "open" : item.kind === "approval" && item.phase === "pending"));
  if (!stillWaiting) {
    next = mapActivity(next, card.runId, (activity) => (activity.wait === "none" ? activity : { ...activity, wait: "none" }));
    next = setSessionStatus(next, card.runId, "running");
  }
  return next;
}

/** @param {FlowState} state @param {string} runId @param {Record<string, unknown>} data */
function showPage(state, runId, data) {
  const pageId = stringField(data, "page_id");
  if (pageId === "" || state.cards.some((card) => card.kind === "report" && card.id === pageId)) {
    return state;
  }
  /** @type {ReportCard} */
  const card = { kind: "report", id: pageId, runId, title: stringField(data, "title") || "Page", href: `/pages/${encodeURIComponent(pageId)}` };
  return { ...state, cards: [...state.cards, card] };
}

/**
 * @param {FlowState} state
 * @param {string} runId
 * @param {(activity: Activity) => Activity} update
 */
function mapActivity(state, runId, update) {
  const current = state.activity[runId] ?? blankActivity();
  const next = update(current);
  if (next === current) {
    return state;
  }
  return { ...state, activity: { ...state.activity, [runId]: next } };
}
