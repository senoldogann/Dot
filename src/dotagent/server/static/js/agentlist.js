// The agents of a chat (item 227), after T3 Code's "Previous agents": in the side panel's Agents tab, the runs the chat's
// runs handed part of their work to, newest first, each with its face and state, its goal, its provider's mark, model
// and state in a word, what it is doing or did last when the conversation on the page knows (item 296, after the rows
// of T3 Code's Agents sheet), and how long it took or has taken so far; a few at first, the rest on demand. A row
// opens the helper's own page.

import { actorFace } from "./actors.js";
import { api } from "./api.js";
import { clip, duration, el, modelName } from "./dom.js";
import { faceState } from "./face.js";
import { actionLine, stateWord } from "./helpercard.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";
import { providerMark, providerOf } from "./providers.js";

// The rows shown before "Show N more".
const FIRST_ROWS = 5;
// The most rows the list asks the daemon for.
const LIMIT = 200;
const GOAL_CHARS = 160;

/** How long `run` took, or has taken at `now` (seconds). */
function took(run, now) {
  return duration(Math.max((run.finished_at ?? now) - run.created_at, 0));
}

/** What `helper` is doing or did last, for the row's last line: empty while nothing is known. */
function doingWords(helper) {
  const line = helper === null ? null : actionLine(helper);
  return line === null ? "" : `${line[0]}: ${line[1]}`;
}

/**
 * The list of the agents of the chat `threadId`: `element` (its section, for the tab), `refresh()` (read the list again;
 * resolves to the number of agents), `fail(text)` (say why the list could not be read) and `tick(now)` (the time of
 * the agents that work counts on, `now` in seconds, and what they do with it). `helper(runId)` is what the conversation
 * on the page keeps of a helper as it works (timeline.js), null for one it does not show.
 */
export function agentList({ threadId, helper }) {
  const rows = el("div", { class: "agent-list" });
  const more = el("button", { class: "btn ghost sm agent-more", type: "button", hidden: true, onclick: () => expand() });
  const empty = el("p", { class: "hint", hidden: true }, "When Dot hands part of the work to a helper, the helper shows here.");
  const section = panelSection({ id: "run-agents", title: "Previous agents", actions: null, body: el("div", { class: "stack tight" }, empty, rows, more) });
  let runs = [];
  let expanded = false;
  // The time and the last line of each row whose agent works, to count on.
  let working = [];

  function row(run, now) {
    const ring = el("span", { class: `status-ring s-${run.status}`, role: "img", "aria-label": stateWord(run.status) });
    const time = el("span", { class: "agent-time" }, took(run, now));
    const doing = el("span", { class: "agent-doing" });
    doing.textContent = doingWords(helper(run.id));
    if (run.finished_at === null) {
      working.push({ run, time, doing });
    }
    return el(
      "a",
      { class: "agent-row", href: `#/runs/${run.id}`, title: run.goal },
      el("span", { class: "child-face" }, actorFace(run.agent_id ?? run.id, faceState(run.status), null), ring),
      el(
        "span",
        { class: "agent-main" },
        el("span", { class: "agent-title" }, clip(run.goal, GOAL_CHARS)),
        el(
          "span",
          { class: "agent-meta" },
          providerMark(providerOf(run.model)),
          el("span", { class: "agent-model" }, modelName(run.model)),
          el("span", { class: `agent-state s-${run.status}` }, stateWord(run.status)),
        ),
        doing,
      ),
      time,
      el("span", { class: "chev" }, icon("chevron")),
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
    for (const { run, time, doing } of working) {
      time.textContent = took(run, now);
      doing.textContent = doingWords(helper(run.id));
    }
  }

  return { element: section.element, refresh, fail, tick };
}
