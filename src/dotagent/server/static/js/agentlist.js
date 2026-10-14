// The agents of a chat (item 227), after T3 Code's "Previous agents": in the side panel's Agents tab, the runs the chat's
// runs handed part of their work to, newest first, each with its face and state, its goal, its state and model in a
// word, and how long it took or has taken so far; a few at first, the rest on demand. A row opens the helper's own page.

import { actorFace } from "./actors.js";
import { api } from "./api.js";
import { clip, duration, el, modelName } from "./dom.js";
import { faceState } from "./face.js";
import { stateWord } from "./helpercard.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

// The rows shown before "Show N more".
const FIRST_ROWS = 5;
// The most rows the list asks the daemon for.
const LIMIT = 200;
const GOAL_CHARS = 160;

/** How long `run` took, or has taken at `now` (seconds). */
function took(run, now) {
  return duration(Math.max((run.finished_at ?? now) - run.created_at, 0));
}

/**
 * The list of the agents of the chat `threadId`: `element` (its section, for the tab), `refresh()` (read the list again;
 * resolves to the number of agents), `fail(text)` (say why the list could not be read) and `tick(now)` (the time of
 * the agents that work counts on, `now` in seconds).
 */
export function agentList({ threadId }) {
  const rows = el("div", { class: "agent-list" });
  const more = el("button", { class: "btn ghost sm agent-more", type: "button", hidden: true, onclick: () => expand() });
  const empty = el("p", { class: "hint", hidden: true }, "When Dot hands part of the work to a helper, the helper shows here.");
  const section = panelSection({ id: "run-agents", title: "Previous agents", actions: null, body: el("div", { class: "stack tight" }, empty, rows, more) });
  let runs = [];
  let expanded = false;
  // The time of each row whose agent works, to count on.
  let working = [];

  function row(run, now) {
    const ring = el("span", { class: `status-ring s-${run.status}`, role: "img", "aria-label": stateWord(run.status) });
    const time = el("span", { class: "agent-time" }, took(run, now));
    if (run.finished_at === null) {
      working.push({ run, time });
    }
    return el(
      "a",
      { class: "agent-row", href: `#/runs/${run.id}`, title: run.goal },
      el("span", { class: "child-face" }, actorFace(run.agent_id ?? run.id, faceState(run.status), null), ring),
      el(
        "span",
        { class: "agent-main" },
        el("span", { class: "agent-title" }, clip(run.goal, GOAL_CHARS)),
        el("span", { class: "agent-meta" }, `${stateWord(run.status)} · ${modelName(run.model)}`),
      ),
      time,
    );
  }

  function paint() {
    const now = Date.now() / 1000;
    working = [];
    const shown = expanded ? runs : runs.slice(0, FIRST_ROWS);
    rows.replaceChildren(...shown.map((run) => row(run, now)));
    rows.hidden = runs.length === 0;
    empty.hidden = runs.length > 0;
    const left = runs.length - shown.length;
    more.hidden = left === 0;
    more.textContent = `Show ${left} more`;
  }

  function expand() {
    expanded = true;
    paint();
    // The focus goes to the first row that came, so the keyboard goes on where the list grew.
    rows.children[FIRST_ROWS]?.focus();
  }

  async function refresh() {
    runs = await api.get(`/threads/${encodeURIComponent(threadId)}/helpers?limit=${LIMIT}`);
    paint();
    return runs.length;
  }

  function fail(text) {
    working = [];
    rows.replaceChildren(el("div", { class: "note warn" }, icon("alert"), el("span", null, text)));
    rows.hidden = false;
    empty.hidden = true;
    more.hidden = true;
  }

  function tick(now) {
    for (const { run, time } of working) {
      time.textContent = took(run, now);
    }
  }

  return { element: section.element, refresh, fail, tick };
}
