// Settings › Self-repair (items 919-921, its look item 932): what Dot's own repairs come to, for the owner to read at a
// glance. An overview leads (whether it is on and when, what it works on now, how many problems are fixed, wait for the
// owner or are open, the account of Claude Code, Run now); then what waits for the owner, each with its pull request and
// why; then the nights, each session by its problem, how it ended and why in a line that opens whole; then the
// problems next in line, the most seen first, a few and the rest a click away, and the closed ones folded. A problem's
// words are the book's, its placeholders (`<n>`, `<quoted>`) read as words. Whether it is on, when, and how it merges
// are `[repair]` in the config file.

import { api } from "../api.js";
import { ago, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { attempt, toast } from "../ui.js";

const STATUS = {
  open: "Open",
  working: "Being fixed",
  fixed: "Fixed",
  waiting: "Waits for you",
  declined: "Not Dot's",
};
const ENDS = {
  merged: "Merged",
  merged_unpulled: "Merged",
  ready: "Ready for you",
  review_blocked: "Review stopped it",
  main_moved: "Main moved: look",
  out_of_time: "Night ran out",
  needs_owner: "Needs you",
  declined: "Not a defect",
  checks_failed: "Checks failed",
  no_answer: "No answer",
  limited: "Plan limit",
  failed: "Failed",
};
// How an end reads at a glance: good, a warning for the owner, or quiet.
const END_TONE = {
  merged: "good",
  merged_unpulled: "good",
  ready: "warn",
  needs_owner: "warn",
  review_blocked: "warn",
  checks_failed: "warn",
  main_moved: "warn",
};
// The problems next in line shown before "Show all".
const NEXT_SHOWN = 5;
// The longest a problem's words are shown on its row; whole on a hover.
const TITLE_CHARS = 110;
// The book's placeholders, as words.
const PLACEHOLDERS = [
  [/<n>/g, "…"],
  [/<quoted>/g, "“…”"],
  [/<path>/g, "a path"],
  [/<url>/g, "a link"],
  [/<id>/g, "…"],
];

/** A problem's words as the owner reads them: the book's placeholders as words, cut to `TITLE_CHARS`. */
export function readable(title) {
  let text = title;
  for (const [pattern, words] of PLACEHOLDERS) {
    text = text.replace(pattern, words);
  }
  return text.length > TITLE_CHARS ? `${text.slice(0, TITLE_CHARS - 1).trimEnd()}…` : text;
}

/** A link to a pull request, or nothing. */
function prLink(url) {
  return url === "" ? null : el("a", { class: "btn sm", href: url, target: "_blank", rel: "noopener" }, icon("external"), "Pull request");
}

function badge(words, tone) {
  return el("span", { class: `badge repair-badge${tone === undefined ? "" : ` repair-${tone}`}` }, words);
}

/** A note of a line, the whole of it a click away when it is longer. */
function note(text) {
  if (text === "") {
    return null;
  }
  return el("details", { class: "repair-note" }, el("summary", null, text), el("p", null, text));
}

/** A tile of the overview: what it counts, its number or word, and a line under it. */
function tile(label, value, sub) {
  return el(
    "div",
    { class: "stat-tile" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-figure" }, el("span", { class: "stat-value" }, value)),
    sub === "" ? null : el("div", { class: "stat-sub", title: sub }, sub),
  );
}

function count(problems, status) {
  return problems.filter((problem) => problem.status === status).length;
}

/** The overview: on or off and when, what it works on now, and the problems by where they stand. */
function overview(view, runNow) {
  const on = view.repo !== "";
  const working = view.problems.find((problem) => problem.status === "working");
  const now = view.running
    ? working === undefined
      ? "A night is under way"
      : readable(working.title)
    : view.sessions.length === 0
      ? "No night yet"
      : `Last session ${ago(view.sessions[0].started_at)}`;
  const tiles = setCard(
    tile("Self-repair", on ? `On, ${view.time}` : "Off", on ? (view.merge === "on_pass" ? "Merged once every check passes" : "Merged by you") : "Set [repair] repo to turn it on"),
    tile(view.running ? "Working on" : "Now", view.running ? "Working" : "Idle", now),
    tile("Fixed", String(count(view.problems, "fixed")), `${count(view.problems, "declined")} not Dot's`),
    tile("Waits for you", String(count(view.problems, "waiting")), `${count(view.problems, "open")} open`),
  );
  tiles.classList.add("stat-card");
  tiles.setAttribute("role", "group");
  tiles.setAttribute("aria-label", "Overview");
  const account = setRow({
    title: "Claude Code",
    lead: view.account === null ? "Not signed in on this Mac: run claude once and sign in." : `${view.account.email}${view.account.organization === "" ? "" : ` · ${view.account.organization}`}`,
    control: on
      ? el(
          "button",
          { class: "btn sm", type: "button", disabled: view.running || view.account === null, onclick: runNow },
          icon("play"),
          view.running ? "Working…" : "Run now",
        )
      : null,
  });
  return el("div", { class: "stack" }, tiles, setCard(account));
}

/** The row of a problem: its words, how often and how lately it was seen, its status and its pull request. */
function problemRow(problem) {
  const seen = `${problem.kind.replace("_", " ")} · seen ${problem.count}× · last ${ago(problem.last_at)}`;
  return setRow({
    title: el("span", { class: "repair-title", title: problem.title }, readable(problem.title)),
    lead: seen,
    control: el("span", { class: "repair-status" }, badge(STATUS[problem.status] ?? problem.status, problem.status === "fixed" ? "good" : problem.status === "waiting" ? "warn" : undefined), prLink(problem.pr_url)),
    body: note(problem.note),
  });
}

/** The row of a session: its problem, how it ended, why in a line, what it used, and its pull request. */
function sessionRow(session, titles) {
  const used = `${session.rounds} round${session.rounds === 1 ? "" : "s"} · ${Math.round((session.input_tokens + session.output_tokens) / 1000)}k tokens · ${session.account}`;
  const title = titles.get(session.problem_id) ?? session.problem_id;
  return setRow({
    title: el("span", { class: "repair-title", title }, readable(title)),
    lead: used,
    control: el("span", { class: "repair-status" }, badge(ENDS[session.end] ?? session.end, END_TONE[session.end]), prLink(session.pr_url)),
    body: note(session.note),
  });
}

/** The sessions by night, the newest night first, each night named by its date. */
function nights(sessions, titles) {
  const byNight = new Map();
  for (const session of sessions) {
    const day = new Date(session.started_at * 1000).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    if (!byNight.has(day)) {
      byNight.set(day, []);
    }
    byNight.get(day).push(session);
  }
  return [...byNight.entries()].map(([day, list]) =>
    el(
      "div",
      { class: "stack repair-night" },
      el("div", { class: "repair-night-head" }, el("span", null, day), el("span", { class: "hint" }, `${list.length} session${list.length === 1 ? "" : "s"}`)),
      setCard(...list.map((session) => sessionRow(session, titles))),
    ),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  let showAll = false;
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function runNow() {
    await attempt(async () => {
      view = await api.post("/repair/now", {});
      toast("ok", "A night of repairs started: Claude Code works on the first problem.");
      paint();
    });
  }

  function nextUp(open) {
    if (open.length === 0) {
      return emptyPanel("check", "Nothing in line", "Dot has found nothing going wrong again and again.");
    }
    const shown = showAll ? open : open.slice(0, NEXT_SHOWN);
    const card = setCard(...shown.map(problemRow));
    if (open.length <= NEXT_SHOWN) {
      return card;
    }
    const more = el(
      "button",
      {
        class: "btn sm repair-more",
        type: "button",
        onclick: () => {
          showAll = !showAll;
          paint();
        },
      },
      showAll ? "Show fewer" : `Show all ${open.length}`,
    );
    return el("div", { class: "stack" }, card, more);
  }

  function paint() {
    const titles = new Map(view.problems.map((problem) => [problem.id, problem.title]));
    const waiting = view.problems.filter((problem) => problem.status === "waiting");
    const open = view.problems.filter((problem) => problem.status === "open" || problem.status === "working").sort((a, b) => b.count - a.count || b.last_at - a.last_at);
    const closed = view.problems.filter((problem) => problem.status === "fixed" || problem.status === "declined");
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections", role: "region", "aria-label": "Self-repair" },
        overview(view, runNow),
        waiting.length === 0 ? null : setSection({ title: "Waits for you", lead: "A fix that needs your look, or one Dot stopped on.", body: setCard(...waiting.map(problemRow)) }),
        setSection({
          title: "Nights",
          lead: "Each session: the problem, how it ended and why.",
          body: view.sessions.length === 0 ? emptyPanel("clock", "No night yet", "") : el("div", { class: "stack" }, ...nights(view.sessions, titles)),
        }),
        setSection({ title: "Next in line", lead: "What Dot's record shows going wrong again and again, the most seen first.", body: nextUp(open) }),
        closed.length === 0 ? null : setSection({ title: "Closed", body: el("details", { class: "repair-closed" }, el("summary", null, `${closed.length} fixed or not Dot's`), setCard(...closed.map(problemRow))) }),
      ),
    );
  }

  async function load() {
    try {
      view = await api.get("/repair");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      paint();
    }
  }

  ctx.head.setLead("Each night Claude Code fixes a problem Dot found in its own record, with a test and a pull request.");
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}
