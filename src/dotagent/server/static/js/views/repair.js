// Settings › Self-repair (items 919-921, its look item 932): what Dot's own repairs come to, for the owner to read at a
// glance. An overview leads (whether it is on and when, what it works on now, how many problems are fixed, wait for the
// owner or are open, the account of Claude Code, Run now); then how Claude Code works (its model, effort and fast mode,
// item 933); then what waits for the owner, each with its pull request and why; then the nights, each session by its
// problem, how it ended and why in a line that opens whole; then the problems next in line, the most seen first, a few
// and the rest a click away, and the closed ones folded. A problem's words are the book's, its placeholders (`<n>`,
// `<quoted>`) read as words. Whether it is on, when, and how it merges are `[repair]` in the config file.

import { api } from "../api.js";
import { ago, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, foldCard, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

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
// How Claude Code may work for Dot (item 933): its model, by Claude Code's names of the newest of each family, and how
// hard it thinks; "default" is Claude Code's own choice.
const MODELS = [
  ["default", "Claude Code's default"],
  ["fable", "Fable"],
  ["opus", "Opus"],
  ["sonnet", "Sonnet"],
  ["haiku", "Haiku"],
];
const EFFORTS = [
  ["default", "Default"],
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
  ["xhigh", "Extra high"],
  ["max", "Max"],
];
// Why a session that asked for fast mode ran at the standard speed, as Claude Code says it.
const FAST_OFF = { extra_usage_disabled: "the account has no extra usage, which fast mode is billed to" };
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

// A step of a session at work as the page names it (item 939): a mark and a verb by its kind.
const STEP_MARKS = { read: "R", edit: "✎", write: "+", run: "$", search: "⌕", plan: "≡", other: "•" };
const STEP_VERBS = { read: "Read", edit: "Edited", write: "Wrote", run: "Ran", search: "Searched", plan: "Planned", other: "Used" };
// The newest steps the card shows before "All steps", and how often the page reads the night while one is at work.
const NOW_STEPS = 4;
const POLL_MS = 4000;
// What a session waits on between its rounds, in the card's head.
const PHASE_WORDS = { review: "Waiting for the review", checks: "Waiting for the checks" };
// While no night works, the night is read once in this many of those.
const IDLE_TICKS = 8;

/** How long since `since` (epoch seconds), as "4m 12s" or "1h 05m". */
function elapsed(since, now) {
  const seconds = Math.max(0, Math.round(now - since));
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

/** A step of the session at work: its mark, its verb and its words; `current` marks the newest. */
function stepLine(step, current) {
  return el(
    "li",
    { class: `repair-step${current ? " current" : ""}` },
    el("span", { class: "repair-step-mark", "aria-hidden": "true" }, STEP_MARKS[step.kind] ?? "•"),
    el("span", { class: "repair-step-words" }, el("b", null, STEP_VERBS[step.kind] ?? step.kind), " ", el("span", { class: "mono" }, step.words)),
  );
}

/**
 * What the night works on now (item 939): a session or a second look, how long and which round, the problem, the model
 * and whose plan, its newest steps (all of them while `allSteps`; `onAll()` flips it) and Stop (`onStop()`).
 */
function nowCard(live, allSteps, onAll, onStop) {
  const steps = allSteps ? live.steps : live.steps.slice(-NOW_STEPS);
  const model = live.model === "default" ? "Claude Code's model" : `${live.model[0].toUpperCase()}${live.model.slice(1)}`;
  const effort = live.effort === "default" ? "" : ` · ${live.effort}`;
  return el(
    "section",
    { class: "card repair-now", "aria-label": "Working now" },
    el(
      "div",
      { class: "repair-now-head" },
      el("span", { class: "repair-live", "aria-hidden": "true" }),
      el("b", null, PHASE_WORDS[live.phase] ?? (live.kind === "look" ? "Looking again" : "Working")),
      el("span", { class: "hint" }, " · ", el("span", { class: "repair-elapsed", dataset: { since: String(live.started_at) } }, elapsed(live.started_at, Date.now() / 1000)), ` · round ${live.round}`),
      el("span", { class: "grow" }),
      el("button", { class: "btn sm danger", type: "button", onclick: onStop }, icon("stop"), "Stop"),
    ),
    el("div", { class: "repair-now-title", title: live.title }, readable(live.title)),
    el("div", { class: "hint" }, `${model}${effort} · ${live.account}`),
    steps.length === 0
      ? el("p", { class: "hint repair-now-wait" }, "Starting…")
      : el("ol", { class: "repair-steps", "aria-label": "Its newest steps" }, steps.map((step, index) => stepLine(step, live.phase === "working" && index === steps.length - 1))),
    live.steps.length > NOW_STEPS
      ? el(
          "button",
          { class: "btn ghost sm", type: "button", "aria-expanded": String(allSteps), onclick: onAll },
          allSteps ? "Fewer steps" : `All ${live.steps.length} steps`,
        )
      : null,
  );
}

/** A select of `choices` (`[value, words]`) at `value`, which calls `onPick(value)` when another is picked. */
function picker(label, choices, value, onPick) {
  const select = el("select", { class: "select sm", "aria-label": label }, ...choices.map(([key, words]) => el("option", { value: key, selected: key === value }, words)));
  select.addEventListener("change", () => onPick(select.value));
  return select;
}

/** What the last session that asked for fast mode got, in words; "" before one did. */
function fastSaid(seen) {
  if (seen === null) {
    return "";
  }
  if (seen.state === "on") {
    return `The last session ran in fast mode, ${ago(seen.at)}.`;
  }
  const why = seen.why === null ? "" : `: ${FAST_OFF[seen.why] ?? seen.why.replaceAll("_", " ")}`;
  return `The last session ran at the standard speed${why}.`;
}

/** How Claude Code works for Dot: its model, its effort and fast mode, each saved as it changes. */
function workerCard(view, save) {
  const worker = view.worker;
  const fast = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(worker.fast),
    "aria-label": "Fast mode",
    onclick: () => save({ ...worker, fast: !worker.fast }),
  });
  const seen = fastSaid(view.fast_seen);
  return setCard(
    view.worker_lost ? setRow({ title: "Choose again", lead: "Your last choice cannot be read, so the nights wait: pick a model to save a new one." }) : null,
    setRow({ title: "Model", lead: "Of each session and second look.", control: picker("Model", MODELS, worker.model, (model) => save({ ...worker, model })) }),
    setRow({ title: "Effort", lead: "How hard it thinks before it answers.", control: picker("Effort", EFFORTS, worker.effort, (effort) => save({ ...worker, effort })) }),
    setRow({ title: "Fast mode", lead: `Faster answers, billed to your plan as extra usage.${seen === "" ? "" : ` ${seen}`}`, control: fast }),
  );
}

/** How Claude Code works, folded at the page's foot to its model and effort (item 939): it is set once, read often. */
function workerFold(view, save) {
  // A choice that cannot be read stops the nights: it is never folded away, whatever the page last kept (the review).
  if (view.worker_lost) {
    return setSection({ title: "Claude Code", body: workerCard(view, save) });
  }
  const worker = view.worker;
  const card = foldCard({ id: "repair-worker", title: "Claude Code", actions: null, body: workerCard(view, save), openByDefault: false });
  const words = MODELS.find(([key]) => key === worker.model)?.[1] ?? worker.model;
  card.setSummary(el("span", { class: "hint" }, `${words} · ${worker.effort === "default" ? "default effort" : worker.effort}${worker.fast ? " · fast" : ""}`));
  return card.element;
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

/**
 * How a session's end reads now (item 940): as it ended, but for one whose problem was settled since by another session
 * or by the owner, which reads "Fixed later" (fixed) or "Let go" (not Dot's), never a warning that waits on nobody.
 */
function endBadge(session, status) {
  const settled = session.end === "merged" || session.end === "merged_unpulled" || session.end === "declined";
  if (!settled && status === "fixed") {
    return badge("Fixed later", "good");
  }
  if (!settled && status === "declined") {
    return badge("Let go", undefined);
  }
  return badge(ENDS[session.end] ?? session.end, END_TONE[session.end]);
}

/** The row of a session: its problem, how it ended (as it reads now), why in a line, what it used, and its pull request. */
function sessionRow(session, titles, statuses) {
  const used = `${session.rounds} round${session.rounds === 1 ? "" : "s"} · ${Math.round((session.input_tokens + session.output_tokens) / 1000)}k tokens · ${session.account}`;
  const title = titles.get(session.problem_id) ?? session.problem_id;
  return setRow({
    title: el("span", { class: "repair-title", title }, readable(title)),
    lead: used,
    control: el("span", { class: "repair-status" }, endBadge(session, statuses.get(session.problem_id)), prLink(session.pr_url)),
    body: note(session.note),
  });
}

/** The sessions by night, the newest night first, each night named by its date. */
function nights(sessions, titles, statuses) {
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
      setCard(...list.map((session) => sessionRow(session, titles, statuses))),
    ),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  let showAll = false;
  // Whether the card of the night at work lists all its steps (item 939), and its slot, drawn again on its own.
  let allSteps = false;
  // The reads of the night so far, and whether the last one failed (said once).
  let ticks = 0;
  let readFailed = false;
  const nowSlot = el("div", { class: "repair-now-slot" });
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function saveWorker(worker) {
    await attempt(async () => {
      view = await api.put("/repair/worker", worker);
      toast("ok", "Saved: the next night works so.");
      paint();
    });
  }

  async function stopNight() {
    const sure = await confirmDialog({
      title: "Stop the night?",
      message: "The session of Claude Code ends with what it started; its problem stays as it was, to be tried again.",
      confirmLabel: "Stop",
      danger: true,
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await attempt(async () => {
      view = await api.post("/repair/stop", {});
      toast("ok", "The night is stopped.");
      paint();
    });
  }

  /** The card of what the night works on now, in its slot; nothing while none is at work. */
  function paintNow() {
    nowSlot.replaceChildren(
      view.running && view.live !== null
        ? nowCard(
            view.live,
            allSteps,
            () => {
              allSteps = !allSteps;
              paintNow();
            },
            () => attempt(stopNight),
          )
        : [],
    );
  }

  /**
   * Read the night again (item 939): every few seconds while one works, the card alone drawn again, and every
   * IDLE_TICKS of them while none does, so that one the schedule starts shows; the whole page once it starts or ends.
   * A read that fails is said once, until one succeeds (the daemon restarts after a merge): the card keeps the last.
   */
  async function poll() {
    ticks += 1;
    if (disposed || view === null || document.hidden || (!view.running && ticks % IDLE_TICKS !== 0)) {
      return;
    }
    let fresh;
    try {
      fresh = await api.get("/repair");
    } catch (error) {
      if (!readFailed) {
        readFailed = true;
        toast("error", `Self-repair could not be read: ${error instanceof Error ? error.message : String(error)}`);
      }
      return;
    }
    readFailed = false;
    if (disposed) {
      return;
    }
    const changed = fresh.running !== view.running;
    view = fresh;
    if (changed) {
      paint();
    } else if (view.running) {
      paintNow();
    }
  }

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
    const statuses = new Map(view.problems.map((problem) => [problem.id, problem.status]));
    const waiting = view.problems.filter((problem) => problem.status === "waiting");
    const open = view.problems.filter((problem) => problem.status === "open" || problem.status === "working").sort((a, b) => b.count - a.count || b.last_at - a.last_at);
    const closed = view.problems.filter((problem) => problem.status === "fixed" || problem.status === "declined");
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections", role: "region", "aria-label": "Self-repair" },
        nowSlot,
        overview(view, runNow),
        waiting.length === 0 ? null : setSection({ title: "Waits for you", lead: "A fix that needs your look, or one Dot stopped on.", body: setCard(...waiting.map(problemRow)) }),
        setSection({
          title: "Nights",
          lead: "Each session: the problem, how it ended and why.",
          body: view.sessions.length === 0 ? emptyPanel("clock", "No night yet", "") : el("div", { class: "stack" }, ...nights(view.sessions, titles, statuses)),
        }),
        setSection({ title: "Next in line", lead: "What Dot's record shows going wrong again and again, the most seen first.", body: nextUp(open) }),
        closed.length === 0 ? null : setSection({ title: "Closed", body: el("details", { class: "repair-closed" }, el("summary", null, `${closed.length} fixed or not Dot's`), setCard(...closed.map(problemRow))) }),
        workerFold(view, saveWorker),
      ),
    );
    paintNow();
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
  // The time on the card goes on by the second; the night is read again every few seconds while one works.
  const ticker = window.setInterval(() => {
    for (const node of nowSlot.querySelectorAll(".repair-elapsed")) {
      node.textContent = elapsed(Number(node.dataset.since), Date.now() / 1000);
    }
  }, 1000);
  const reader = window.setInterval(() => attempt(poll), POLL_MS);
  return () => {
    disposed = true;
    window.clearInterval(ticker);
    window.clearInterval(reader);
  };
}
