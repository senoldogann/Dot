// The agents of a chat (item 227): the runs the chat's runs handed part of their work to, newest first, each with its
// face and state, its goal, its provider's mark, model and state in a word, what it is doing or did last when the
// conversation on the page knows (item 296), and how long it took or has taken so far; a few at first, the rest on
// demand. A row opens the helper's own page.
//
// Since item 1332 the panel tells them as a developer tool's details panel does: a Lineage section, "Lineage · 2
// running", with the task that leads and, under it, its subagents that work, each a mark, its title, how long it has
// worked and a dot for how it stands; and a row "Previous agents" with how many failed and how many there are, whose
// fold holds the rows of those that ended. The Agents tab has all of it; the Task tab a compact Lineage whose
// "Previous agents" row opens the Agents tab.

import { actorFace } from "./actors.js";
import { api } from "./api.js";
import { clip, duration, el, modelName, setText } from "./dom.js";
import { t } from "./i18n.js";
import { faceState } from "./face.js";
import { actionLine, helperCard, stateWord } from "./helpercard.js";
import { icon } from "./icons.js";
import { providerMark, providerOf } from "./providers.js";

// The rows shown before "Show N more".
const FIRST_ROWS = 5;
// The most rows the list asks the daemon for.
const LIMIT = 200;
const GOAL_CHARS = 160;
const TITLE_CHARS = 80;
// The states of a run that has not ended: it counts as running in the Lineage.
const LIVE = new Set(["queued", "running", "ending", "blocked"]);

/** How long `run` took, or has taken at `now` (seconds). */
function took(run, now) {
  return duration(Math.max((run.finished_at ?? now) - run.created_at, 0));
}

/** An age in few characters: "42s", "8m", "3h", "2d". */
function shortAge(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) {
    return `${whole}s`;
  }
  if (whole < 3600) {
    return `${Math.floor(whole / 60)}m`;
  }
  return whole < 86400 ? `${Math.floor(whole / 3600)}h` : `${Math.floor(whole / 86400)}d`;
}

/** What `helper` is doing or did last, for the row's last line: empty while nothing is known. */
function doingWords(helper) {
  const line = helper === null ? null : actionLine(helper);
  return line === null ? "" : `${line[0]}: ${line[1]}`;
}

/**
 * The agents of the chat `threadId`. `lead` is the task of the page, `{ id, goal, actor, since, state() }` (`actor` the id
 * of the face it wears, `since` when it began in seconds, `state()` its status while it works, else null), and
 * `helper(runId)` is what the conversation on the page keeps of a helper as it works (timeline.js), null for one it does
 * not show. `onOpenAgents()` opens the Agents tab (the compact Lineage's "Previous agents" goes there).
 *
 * Returns `lineage({ compact })` (a Lineage section: `{ element }`; each call a view of its own, all painted from the one
 * list; the full one, for the Agents tab, holds the fold of the runs that ended, so it is made once), `refresh()` (read the list again; resolves to
 * the number of agents), `fail(text)` (say why the list could not be read) and `tick(now)` (the time of the agents that
 * work counts on, `now` in seconds, and what they do with it).
 */
export function agentList({ threadId, helper, lead, onOpenAgents }) {
  const rows = el("div", { class: "agent-list" });
  const more = el("button", { class: "btn ghost sm agent-more", type: "button", hidden: true, onclick: () => expand() });
  const previousBody = el("div", { class: "agent-previous-body", id: `agent-previous-${threadId}`, hidden: true }, rows, more);
  // The card a row shows when the pointer rests on it or the focus comes to it, as in the conversation (item 226).
  const card = helperCard(previousBody);
  let runs = [];
  let expanded = false;
  let problem = null;
  // The views to paint again with the list.
  const views = new Set();

  const live = (run) => run.finished_at === null && LIVE.has(helper(run.id)?.status ?? run.status);
  const running = () => runs.filter(live);
  const ended = () => runs.filter((run) => !live(run));
  const failed = () => ended().filter((run) => (helper(run.id)?.status ?? run.status) === "failed").length;

  /** A row of a run that ended: its face, goal, model, state and how long it took; the way to its page. */
  function row(run, now) {
    const status = helper(run.id)?.status ?? run.status;
    const ring = el("span", { class: `status-ring s-${status}`, role: "img", "aria-label": stateWord(status) });
    const time = el("span", { class: "agent-time" }, took(run, now));
    const doing = el("span", { class: "agent-doing" });
    doing.textContent = doingWords(helper(run.id));
    const link = el(
      "a",
      { class: "agent-row", href: `#/runs/${run.id}` },
      el("span", { class: "child-face" }, actorFace(run.agent_id ?? run.id, faceState(status), null), ring),
      el(
        "span",
        { class: "agent-main" },
        el("span", { class: "agent-title" }, clip(run.goal, GOAL_CHARS)),
        el(
          "span",
          { class: "agent-meta" },
          providerMark(providerOf(run.model)),
          el("span", { class: "agent-model" }, modelName(run.model)),
          el("span", { class: `agent-state s-${status}` }, stateWord(status)),
        ),
        doing,
      ),
      time,
      el("span", { class: "chev" }, icon("chevron")),
    );
    const known = cardHelper(run);
    card.attach(link, known, () => actorFace(run.agent_id ?? run.id, faceState(known.status), null));
    return link;
  }

  /** What the card tells of `run`: what the conversation keeps of it when it shows it, else what the list read. */
  function cardHelper(run) {
    return (
      helper(run.id) ?? {
        goal: run.goal ?? "",
        model: run.model ?? null,
        effort: run.effort ?? null,
        speed: run.speed ?? "standard",
        status: run.status,
        began: run.created_at,
        ended: run.finished_at ?? null,
        action: null,
        calls: 0,
      }
    );
  }

  // ------------------------------------------------------------------------------------------- lineage views
  /** One row of the Lineage: a mark, a title, an age and a dot for how it stands. */
  function lineageRow({ id, actor, title, since, status, doing = "", lead: isLead }) {
    const dot = el("span", { class: `lineage-dot s-${status}`, role: "img", "aria-label": stateWord(status) });
    const age = el("span", { class: "lineage-age" }, shortAge(Date.now() / 1000 - since));
    const mark = el("span", { class: "lineage-mark", "aria-hidden": "true" }, actorFace(actor, faceState(status), null));
    const text = el("span", { class: "lineage-title" }, clip(title, TITLE_CHARS));
    const node = isLead
      ? el("div", { class: "lineage-row lineage-lead", "aria-current": "true" }, mark, text, age, dot)
      : el("a", { class: "lineage-row", href: `#/runs/${id}`, title: `${title}${doing === "" ? "" : `\n${doing}`}` }, mark, text, age, dot);
    return { node, age, dot, since, id, isLead };
  }

  function view({ compact }) {
    const title = el("h3", { class: "panel-section-title" });
    const list = el("div", { class: "lineage-list" });
    const previousLabel = el("span", { class: "lineage-previous-label" }, t("lineage.previous"));
    const previousFailed = el("span", { class: "lineage-previous-failed" });
    const previousCount = el("span", { class: "lineage-previous-count" });
    const previous = el(
      "button",
      { class: "lineage-row lineage-previous", type: "button" },
      el("span", { class: "lineage-mark", "aria-hidden": "true" }, icon("users")),
      previousLabel,
      previousFailed,
      previousCount,
      el("span", { class: "chev" }, icon("chevron")),
    );
    if (compact) {
      previous.addEventListener("click", () => onOpenAgents());
    } else {
      previous.setAttribute("aria-controls", previousBody.id);
      previous.addEventListener("click", () => {
        previousBody.hidden = !previousBody.hidden;
        previous.setAttribute("aria-expanded", String(!previousBody.hidden));
        previous.dataset.open = String(!previousBody.hidden);
      });
      previous.setAttribute("aria-expanded", "false");
    }
    const note = el("p", { class: "hint lineage-note", hidden: true }, t("lineage.empty"));
    // Why the list could not be read (`fail`): the full view says it, in place of what it would tell of the list.
    const trouble = el("p", { class: "note warn lineage-problem", hidden: true });
    const element = el(
      "section",
      { class: "panel-section lineage", "data-compact": String(compact), hidden: compact },
      el("div", { class: "panel-section-head" }, title),
      el("div", { class: "panel-section-body" }, list, compact ? null : trouble, note, compact ? null : previousBody),
    );
    // The full view puts the fold of the ended ones after its "Previous agents" row.
    if (!compact) {
      previousBody.before(previous);
    } else {
      note.before(previous);
    }
    let rowsShown = [];

    function paint() {
      const liveRuns = running();
      const done = ended();
      const leadState = lead.state();
      rowsShown = [
        lineageRow({
          id: lead.id,
          actor: lead.actor,
          title: lead.goal,
          since: lead.since,
          status: leadState ?? "done",
          lead: true,
        }),
        ...liveRuns
          .slice()
          .reverse()
          .map((run) =>
            lineageRow({
              id: run.id,
              actor: run.agent_id ?? run.id,
              title: run.goal,
              since: run.created_at,
              status: helper(run.id)?.status ?? run.status,
              doing: doingWords(helper(run.id)),
              lead: false,
            }),
          ),
      ];
      list.replaceChildren(...rowsShown.map((each) => each.node));
      setText(title, liveRuns.length === 0 ? t("lineage.title") : t("lineage.titleRunning", { count: liveRuns.length }));
      const bad = failed();
      previous.hidden = done.length === 0;
      setText(previousFailed, bad === 0 ? "" : t("lineage.failed", { count: bad }));
      previousFailed.hidden = bad === 0;
      setText(previousCount, String(done.length));
      // A compact view is for a task with subagents: with none, it is not shown. The full one always is, and says what
      // will be there once Dot hands part of the work to one.
      note.hidden = runs.length > 0 || problem !== null;
      setText(trouble, problem ?? "");
      trouble.hidden = problem === null;
      element.hidden = compact && runs.length === 0;
      if (!compact && runs.length === 0) {
        previous.hidden = true;
      }
    }

    function tick(now) {
      for (const each of rowsShown) {
        setText(each.age, shortAge(now - each.since));
        if (!each.isLead) {
          const run = runs.find((candidate) => candidate.id === each.id);
          const status = run === undefined ? "running" : (helper(run.id)?.status ?? run.status);
          each.dot.className = `lineage-dot s-${status}`;
        } else {
          each.dot.className = `lineage-dot s-${lead.state() ?? "done"}`;
        }
      }
    }

    const handle = { element, paint, tick };
    views.add(handle);
    paint();
    return handle;
  }

  // ------------------------------------------------------------------------------------------- previous agents
  function paintRows() {
    card.hide();
    const now = Date.now() / 1000;
    const done = ended();
    const shown = expanded ? done : done.slice(0, FIRST_ROWS);
    const nodes = shown.map((run) => row(run, now));
    rows.replaceChildren(...nodes);
    rows.hidden = done.length === 0;
    const left = done.length - shown.length;
    more.hidden = left === 0;
    setText(more, t("agentlist.showLeftMore", { left }));
  }

  function paint() {
    paintRows();
    for (const each of views) {
      each.paint();
    }
  }

  function expand() {
    expanded = true;
    paintRows();
    // The focus goes to the first row that came, so the keyboard goes on where the list grew.
    rows.children[FIRST_ROWS]?.focus();
  }

  async function refresh() {
    runs = await api.get(`/threads/${encodeURIComponent(threadId)}/helpers?limit=${LIMIT}`);
    problem = null;
    paint();
    return runs.length;
  }

  function fail(text) {
    problem = text;
    // Painted again, so that what a view says of the list (its empty note, its fold) stands beside the problem, not
    // over it; the runs it read before stay as they were.
    for (const each of views) {
      each.paint();
    }
  }

  // Every second while the run works; what reads the same is not written again (item 310).
  function tick(now) {
    for (const each of views) {
      each.tick(now);
    }
    card.refresh(now);
  }

  return {
    lineage: (options) => view(options),
    refresh,
    fail,
    tick,
    // How the Lineage stands again when what the conversation knows changed (a subagent that ended).
    repaint: paint,
  };
}
