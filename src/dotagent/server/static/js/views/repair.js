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
import { t } from "../i18n.js";

const STATUS = {
  open: t("views.repair.open"),
  working: t("views.repair.beingFixed"),
  fixed: t("views.repair.fixed"),
  waiting: t("views.repair.waitsForYou"),
  declined: t("views.repair.notDotS"),
  closed: t("views.repair.closed"),
};
// Where a pull request stands on GitHub, read again when the page opens (item 958): what it reads, and its tone.
const PR_STATES = { merged: [t("views.repair.merged"), "good"], closed: [t("views.repair.closed"), undefined] };
const ENDS = {
  merged: t("views.repair.merged"),
  merged_unpulled: t("views.repair.merged"),
  ready: t("views.repair.readyForYou"),
  review_blocked: t("views.repair.reviewStoppedIt"),
  main_moved: t("views.repair.mainMovedLook"),
  out_of_time: t("views.repair.nightRanOut"),
  needs_owner: t("views.repair.needsYou"),
  declined: t("views.repair.notADefect"),
  checks_failed: t("views.repair.checksFailed"),
  no_answer: t("views.repair.noAnswer"),
  limited: t("views.repair.planLimit"),
  failed: t("views.repair.failed"),
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
  ["default", t("views.repair.claudeCodeSDefault")],
  ["fable", "Fable"],
  ["opus", "Opus"],
  ["sonnet", "Sonnet"],
  ["haiku", "Haiku"],
];
const EFFORTS = [
  ["default", t("views.repair.default")],
  ["low", t("views.repair.low")],
  ["medium", t("views.repair.medium")],
  ["high", t("views.repair.high")],
  ["xhigh", t("views.repair.extraHigh")],
  ["max", t("views.repair.max")],
];
// Why a session that asked for fast mode ran at the standard speed, as Claude Code says it.
const FAST_OFF = { extra_usage_disabled: t("views.repair.theAccountHasNoExtra") };
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
  return url === "" ? null : el("a", { class: "btn sm", href: url, target: "_blank", rel: "noopener" }, icon("external"), t("views.repair.pullRequest"));
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
      ? t("views.repair.aNightIsUnderWay")
      : readable(working.title)
    : view.sessions.length === 0
      ? t("views.repair.noNightYet")
      : t("views.repair.lastSessionAgo", { ago: ago(view.sessions[0].started_at) });
  const tiles = setCard(
    tile(t("views.repair.selfRepair"), on ? t("views.repair.onTime", { time: view.time }) : t("views.repair.off"), on ? (view.merge === "on_pass" ? t("views.repair.mergedOnceEveryCheckPasses") : t("views.repair.mergedByYou")) : t("views.repair.setRepairRepoToTurn")),
    tile(view.running ? t("views.repair.workingOn") : t("views.repair.now"), view.running ? t("views.repair.working") : t("views.repair.idle"), now),
    tile(t("views.repair.fixed"), String(count(view.problems, "fixed")), t("views.repair.declinedClosed", { declined: count(view.problems, "declined"), closed: count(view.problems, "closed") })),
    tile(t("views.repair.waitsForYou"), String(count(view.problems, "waiting")), t("views.repair.countOpenProblems", { count: count(view.problems, "open") })),
  );
  tiles.classList.add("stat-card");
  tiles.setAttribute("role", "group");
  tiles.setAttribute("aria-label", t("views.repair.overview"));
  const account = setRow({
    title: "Claude Code",
    lead: view.account === null ? t("views.repair.notSignedInOnThis") : `${view.account.email}${view.account.organization === "" ? "" : ` · ${view.account.organization}`}`,
    control: on
      ? el(
          "button",
          { class: "btn sm", type: "button", disabled: view.running || view.account === null, onclick: runNow },
          icon("play"),
          view.running ? t("views.repair.working2") : t("views.repair.runNow"),
        )
      : null,
  });
  return el("div", { class: "stack" }, tiles, setCard(account));
}

// A step of a session at work as the page names it (item 939): a mark and a verb by its kind.
const STEP_MARKS = { read: "R", edit: "✎", write: "+", run: "$", search: "⌕", plan: "≡", other: "•" };
const STEP_VERBS = { read: t("views.repair.read"), edit: t("views.repair.edited"), write: t("views.repair.wrote"), run: t("views.repair.ran"), search: t("views.repair.searched"), plan: t("views.repair.planned"), other: t("views.repair.used") };
// The newest steps the card shows before "All steps", and how often the page reads the night while one is at work.
const NOW_STEPS = 4;
const POLL_MS = 4000;
// What a session waits on between its rounds, in the card's head.
const PHASE_WORDS = { review: t("views.repair.waitingForTheReview"), checks: t("views.repair.waitingForTheChecks") };
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
  const model = live.model === "default" ? t("views.repair.claudeCodeSModel") : `${live.model[0].toUpperCase()}${live.model.slice(1)}`;
  const effort = live.effort === "default" ? "" : ` · ${live.effort}`;
  return el(
    "section",
    { class: "card repair-now", "aria-label": t("views.repair.workingNow") },
    el(
      "div",
      { class: "repair-now-head" },
      el("span", { class: "repair-live", "aria-hidden": "true" }),
      el("b", null, PHASE_WORDS[live.phase] ?? (live.kind === "look" ? t("views.repair.lookingAgain") : t("views.repair.working"))),
      el("span", { class: "hint" }, " · ", el("span", { class: "repair-elapsed", dataset: { since: String(live.started_at) } }, elapsed(live.started_at, Date.now() / 1000)), t("views.repair.roundRound", { round: live.round })),
      el("span", { class: "grow" }),
      el("button", { class: "btn sm danger", type: "button", onclick: onStop }, icon("stop"), t("views.repair.stop")),
    ),
    el("div", { class: "repair-now-title", title: live.title }, readable(live.title)),
    el("div", { class: "hint" }, `${model}${effort} · ${live.account}`),
    steps.length === 0
      ? el("p", { class: "hint repair-now-wait" }, t("views.repair.starting"))
      : el("ol", { class: "repair-steps", "aria-label": t("views.repair.itsNewestSteps") }, steps.map((step, index) => stepLine(step, live.phase === "working" && index === steps.length - 1))),
    live.steps.length > NOW_STEPS
      ? el(
          "button",
          { class: "btn ghost sm", type: "button", "aria-expanded": String(allSteps), onclick: onAll },
          allSteps ? t("views.repair.fewerSteps") : t("views.repair.allLengthSteps", { length: live.steps.length }),
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
    return t("views.repair.theLastSessionRanIn", { ago: ago(seen.at) });
  }
  const why = seen.why === null ? "" : `: ${FAST_OFF[seen.why] ?? seen.why.replaceAll("_", " ")}`;
  return t("views.repair.theLastSessionRanAt", { why });
}

/** How Claude Code works for Dot: its model, its effort and fast mode, each saved as it changes. */
function workerCard(view, save) {
  const worker = view.worker;
  const fast = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(worker.fast),
    "aria-label": t("views.repair.fastMode"),
    onclick: () => save({ ...worker, fast: !worker.fast }),
  });
  const seen = fastSaid(view.fast_seen);
  return setCard(
    view.worker_lost ? setRow({ title: t("views.repair.chooseAgain"), lead: t("views.repair.yourLastChoiceCannotBe") }) : null,
    setRow({ title: t("views.repair.model"), lead: t("views.repair.ofEachSessionAndSecond"), control: picker(t("views.repair.model"), MODELS, worker.model, (model) => save({ ...worker, model })) }),
    setRow({ title: t("views.repair.effort"), lead: t("views.repair.howHardItThinksBefore"), control: picker(t("views.repair.effort"), EFFORTS, worker.effort, (effort) => save({ ...worker, effort })) }),
    setRow({ title: t("views.repair.fastMode"), lead: t("views.repair.fasterAnswersBilledToYour", { value: seen === "" ? "" : ` ${seen}` }), control: fast }),
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
  card.setSummary(el("span", { class: "hint" }, `${words} · ${worker.effort === "default" ? t("views.repair.defaultEffort") : worker.effort}${worker.fast ? " · fast" : ""}`));
  return card.element;
}

/** How a problem's status reads: its pull request's state on GitHub when that was merged or closed and settled it
 * (item 958), else its status: a problem open again is open, whatever an older pull request of it came to. */
function statusBadge(problem) {
  const shown = PR_STATES[problem.pr_state];
  if (shown !== undefined && (problem.status === "fixed" || problem.status === "closed")) {
    return badge(shown[0], shown[1]);
  }
  return badge(STATUS[problem.status] ?? problem.status, problem.status === "fixed" ? "good" : problem.status === "waiting" ? "warn" : undefined);
}

/** The row of a problem: its words, how often and how lately it was seen, its status and its pull request. */
function problemRow(problem) {
  const seen = t("views.repair.replaceSeenCountLastAgo", { replace: problem.kind.replace("_", " "), count: problem.count, ago: ago(problem.last_at) });
  return setRow({
    title: el("span", { class: "repair-title", title: problem.title }, readable(problem.title)),
    lead: seen,
    control: el("span", { class: "repair-status" }, statusBadge(problem), prLink(problem.pr_url)),
    body: note(problem.note),
  });
}

/**
 * How a session's end reads now (item 940): as it ended, but for one whose problem was settled since by another session
 * or by the owner, which reads "Fixed later" (fixed) or "Let go" (not Dot's), never a warning that waits on nobody.
 */
function endBadge(session, status) {
  // Its pull request merged or closed on GitHub since (item 958), by the owner or another session's replay of it.
  const shown = PR_STATES[session.pr_state];
  if (shown !== undefined) {
    return badge(shown[0], shown[1]);
  }
  const settled = session.end === "merged" || session.end === "merged_unpulled" || session.end === "declined";
  if (!settled && status === "fixed") {
    return badge(t("views.repair.fixedLater"), "good");
  }
  if (!settled && status === "declined") {
    return badge(t("views.repair.letGo"), undefined);
  }
  return badge(ENDS[session.end] ?? session.end, END_TONE[session.end]);
}

/** The row of a session: its problem, how it ended (as it reads now), why in a line, what it used, and its pull request. */
function sessionRow(session, titles, statuses) {
  const used = t("views.repair.roundsRoundValueRoundK", { rounds: session.rounds, value: session.rounds === 1 ? "" : "s", round: Math.round((session.input_tokens + session.output_tokens) / 1000), account: session.account });
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
      el("div", { class: "repair-night-head" }, el("span", null, day), el("span", { class: "hint" }, t("views.repair.lengthSessionValue", { length: list.length, value: list.length === 1 ? "" : "s" }))),
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
      toast("ok", t("views.repair.savedTheNextNightWorks"));
      paint();
    });
  }

  async function stopNight() {
    const sure = await confirmDialog({
      title: t("views.repair.stopTheNight"),
      message: t("views.repair.theSessionOfClaudeCode"),
      confirmLabel: t("views.repair.stop"),
      danger: true,
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await attempt(async () => {
      view = await api.post("/repair/stop", {});
      toast("ok", t("views.repair.theNightIsStopped"));
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
        toast("error", t("views.repair.selfRepairCouldNotBe", { value: error instanceof Error ? error.message : String(error) }));
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
      toast("ok", t("views.repair.aNightOfRepairsStarted"));
      paint();
    });
  }

  function nextUp(open) {
    if (open.length === 0) {
      return emptyPanel("check", t("views.repair.nothingInLine"), t("views.repair.dotHasFoundNothingGoing"));
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
      showAll ? t("views.repair.showFewer") : t("views.repair.showAllLength", { length: open.length }),
    );
    return el("div", { class: "stack" }, card, more);
  }

  function paint() {
    const titles = new Map(view.problems.map((problem) => [problem.id, problem.title]));
    const statuses = new Map(view.problems.map((problem) => [problem.id, problem.status]));
    const waiting = view.problems.filter((problem) => problem.status === "waiting");
    const open = view.problems.filter((problem) => problem.status === "open" || problem.status === "working").sort((a, b) => b.count - a.count || b.last_at - a.last_at);
    const closed = view.problems.filter((problem) => problem.status === "fixed" || problem.status === "declined" || problem.status === "closed");
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections", role: "region", "aria-label": t("views.repair.selfRepair") },
        nowSlot,
        overview(view, runNow),
        waiting.length === 0 ? null : setSection({ title: t("views.repair.waitsForYou"), lead: t("views.repair.aFixThatNeedsYour"), body: setCard(...waiting.map(problemRow)) }),
        setSection({
          title: t("views.repair.nights"),
          lead: t("views.repair.eachSessionTheProblemHow"),
          body: view.sessions.length === 0 ? emptyPanel("clock", t("views.repair.noNightYet"), "") : el("div", { class: "stack" }, ...nights(view.sessions, titles, statuses)),
        }),
        setSection({ title: t("views.repair.nextInLine"), lead: t("views.repair.whatDotSRecordShows"), body: nextUp(open) }),
        closed.length === 0 ? null : setSection({ title: t("views.repair.closed"), body: el("details", { class: "repair-closed" }, el("summary", null, t("views.repair.lengthFixedClosedOrNot", { length: closed.length })), setCard(...closed.map(problemRow))) }),
        workerFold(view, saveWorker),
      ),
    );
    paintNow();
  }

  async function load() {
    try {
      // Opening the page reads where its pull requests stand on GitHub first (item 958); the polls below do not.
      view = await api.post("/repair/settle", {});
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

  ctx.head.setLead(t("views.repair.eachNightClaudeCodeFixes"));
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
