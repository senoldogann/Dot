// Settings › Self-improvement (items 393 and 566, docs/harness.md: the harness): what Dot improves around its model, in plain
// words (item 503). An overview of four tiles, the nightly learning with its newest nights (all a click away), the version
// in use with its two texts folded to how many points they hold, the versions with Use and Set aside and a form for one of
// the owner's, which opens in a dialog (item 615), and the lessons the nights kept, both folded to their counts (item
// 938), and how it is set.

import { api } from "../api.js";
import { clip, dateTime, el, joined, nextNight, } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, foldCard, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { readFailed } from "../state.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";
import { poolNightWords } from "../harnessnight.js";
import { codeText } from "./memory.js";
import { t, tCount } from "../i18n.js";

const STATE_TONE = { adopted: "b-ok", probed: "b-warn", candidate: "b-info", retired: "b-muted" };
// A version's state and a night's outcome in the words of the page; the API's own words stay in the docs.
const STATE_WORDS = { adopted: t("views.harness.inUse"), probed: "tried", candidate: "new", retired: t("views.harness.setAside") };
const STEP_TONE = {
  done: "b-ok",
  keep: "b-muted",
  skipped: "b-muted",
  failed: "b-bad",
  calibrating: "b-info",
  unusable: "b-warn",
};
// A night that tried test tasks to find the ones a version can do better on, and proposed nothing (item 719). A night
// that went on without a model's answer it could not use after asking three times (item 1015).
const STEP_WORDS = {
  done: "done",
  keep: t("views.harness.noChange"),
  skipped: "skipped",
  failed: "failed",
  calibrating: t("views.harness.tryingTasks"),
  unusable: t("views.harness.answerUnusable"),
};
// The nights the strip of the overview shows, the newest at its right end.
const STRIP_STEPS = 14;
// A night's cell in the strip, by its outcome: a change made, none, an answer it could not use, or a failure; each is
// named in the legend too.
const STRIP_KIND = {
  done: "done",
  keep: "quiet",
  skipped: "quiet",
  failed: "failed",
  calibrating: "quiet",
  unusable: "unusable",
};
const MADE_BY = { owner: t("views.harness.writtenByYou"), proposer: t("views.harness.proposedByDot"), composer: t("views.harness.combinedByDot") };
const SUMMARY_CHARS = 200;
// How many versions the list shows at first and each time **Show older versions** is pressed (the daemon pages them).
const VERSIONS_PAGE = 50;
// The most versions the daemon gives in one answer.
const MOST_VERSIONS = 500;
// The keys of a version's notes the page reads out, in this order, each with its words.
const NOTE_WORDS = {
  scope: "about",
  moments: t("views.harness.whatInTheTaskIt"),
  contrast: t("views.harness.inTheTestAgainstThe"),
  confirmation: t("views.harness.inTheTestPlayedAgain"),
  decisions: "decisions",
  retired: t("views.harness.setAside"),
  from_run: t("views.harness.learnedFromTask"),
  composed_of: t("views.harness.combinedFrom"),
  tainted: t("views.harness.readContentOthersWrote"),
  tainted_by: t("views.harness.whatItRead"),
};

function capital(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Why a version may come from content others can write (a page, a message), in the daemon's words, none when nothing says
 * so. The daemon reads the version's notes and those of the versions it was combined from, and counts notes that cannot be
 * read as a reason too: this page never reads them as clean. */
function outsideReasons(view, version) {
  return view.from_outside?.[version.id] ?? [];
}

/** The pill of a version that may come from content others can write, beside its state, with the reasons on a hover. */
function outsidePill(reasons) {
  return el(
    "span",
    {
      class: "badge b-warn",
      title: t("views.harness.aLineInThisVersion", { join: reasons.join("; ") }),
    },
    t("views.harness.fromOutsideContent"),
  );
}

/** The pill of a version whose text the daemon's checks refuse now: it is not put in use, and a run never takes it. */
function refusedPill(refusal) {
  return el("span", { class: "badge b-bad", title: t("views.harness.capitalAVersionLikeThis", { capital: capital(refusal) }) }, t("views.harness.refused"));
}

/** A pill with the name of a state or an outcome: green for what is in use or done, red for a failure, grey for the rest. */
function pill(tones, words, state) {
  return el("span", { class: `badge ${tones[state] ?? "b-muted"}` }, capital(words[state] ?? state));
}

function textBlock(text) {
  return text === "" ? el("pre", { class: "harness-text empty" }, t("views.harness.nothing")) : el("pre", { class: "harness-text" }, text);
}

/** The points of a text of a version: a line a point, without the dash it starts with. */
function pointsOf(text) {
  return text
    .split("\n")
    .map((line) => line.replace(/^\s*[-*•]\s+/, "").trim())
    .filter((line) => line !== "");
}

/** A text of a version as a list, a line a point: the dash a line starts with is the list's own mark, and the words
 * between backticks are code. */
function bulletList(text) {
  const lines = pointsOf(text);
  if (lines.length === 0) {
    return el("p", { class: "follow-none" }, t("views.harness.nothing"));
  }
  return el("ul", { class: "follow-list" }, lines.map((line) => el("li", null, codeText(line))));
}

function noteLines(notes) {
  if (notes === "") {
    return [];
  }
  let parsed;
  try {
    parsed = JSON.parse(notes);
  } catch (error) {
    // Dot writes the notes as JSON; ones that are not are a fault, shown as they are and said so.
    console.error("a version's notes could not be read", error);
    return [el("div", { class: "row-text muted" }, t("views.harness.theseNotesCouldNotBe", { notes }))];
  }
  if (parsed === null || typeof parsed !== "object") {
    return [el("div", { class: "row-text muted" }, t("views.harness.theseNotesAreNotIn", { notes }))];
  }
  return Object.entries(NOTE_WORDS)
    .filter(([key]) => key in parsed)
    .map(([key, words]) => {
      const value = parsed[key];
      let text;
      if (key === "tainted") {
        // A candidate written from a run that read what others can author is said so, for the owner to weigh.
        text = value ? t("views.harness.yesTheTaskReadA") : "no";
      } else if (Array.isArray(value)) {
        text = value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("; ");
      } else {
        text = String(value);
      }
      return el("div", { class: "row-text" }, el("span", { class: "muted" }, `${words}: `), text);
    });
}

/** A version as a row of the list: its pill, its summary and, at the right, Texts and notes (the id, who made it and when,
 * and both texts as they stand, in a place under the row) and the buttons that fit its state. */
function versionRow(version, handlers) {
  const buttons = [];
  const reasons = handlers.reasons(version);
  const refusal = handlers.refusal(version);
  // A version the checks refuse is not offered for use: set it aside and write another.
  if (version.state !== "adopted" && refusal === "") {
    buttons.push(el("button", { class: "btn sm", type: "button", onclick: () => handlers.onAdopt(version) }, t("views.harness.use")));
  }
  // The version in use is set aside from What Dot follows now, above; a set-aside one has nothing left to set aside.
  if (version.state === "candidate" || version.state === "probed") {
    buttons.push(el("button", { class: "btn ghost sm", type: "button", onclick: () => handlers.onRetire(version) }, t("views.harness.setAside2")));
  }
  const details = el(
    "div",
    { class: "version-details", id: `version-${version.id}`, hidden: true },
    el(
      "div",
      { class: "version-facts" },
      ...joined([
        el("span", { class: "mono", title: t("views.harness.theVersionSId") }, version.id),
        MADE_BY[version.made_by] ?? `by ${version.made_by}`,
        dateTime(version.created_at),
      ]),
    ),
    refusal === "" ? null : el("div", { class: "row-text" }, el("span", { class: "muted" }, t("views.harness.refused2")), t("views.harness.refusalSetItAsideAnd", { refusal })),
    el("div", { class: "version-label" }, t("views.harness.habits")),
    textBlock(version.habits),
    el("div", { class: "version-label" }, t("views.harness.tips")),
    textBlock(version.tool_guidance),
    ...noteLines(version.notes),
  );
  const toggle = el("button", { class: "version-toggle", type: "button", "aria-expanded": "false", "aria-controls": details.id }, t("views.harness.fullTextAndNotes"));
  toggle.addEventListener("click", () => {
    details.hidden = !details.hidden;
    toggle.setAttribute("aria-expanded", String(!details.hidden));
  });
  return el(
    "div",
    { class: version.state === "retired" ? "set-row version-row retired" : "set-row version-row", dataset: { version: version.id } },
    el(
      "div",
      { class: "harness-line" },
      el("span", { class: "harness-mark" }, pill(STATE_TONE, STATE_WORDS, version.state)),
      el(
        "span",
        { class: "version-summary", title: version.summary },
        clip(version.summary, SUMMARY_CHARS),
        reasons.length > 0 ? el("span", { class: "version-origin" }, outsidePill(reasons)) : null,
        refusal === "" ? null : el("span", { class: "version-origin" }, refusedPill(refusal)),
      ),
      el("span", { class: "version-tools" }, toggle, ...buttons),
    ),
    details,
  );
}

function stepFacts(step) {
  if (step.outcome === "") {
    return {};
  }
  try {
    return JSON.parse(step.outcome);
  } catch (error) {
    // Dot writes the record as JSON; one that is not is a fault, said in the row, never a blank.
    console.error("a step's record could not be read", step.id, error);
    return { unreadable: t("views.harness.thisStepSRecordCould", { message: error.message }) };
  }
}

/** What a night's row says under its date: what it read and how many versions it proposed. */
function stepMeta(step, facts, candidates, digest) {
  if (digest) {
    return [t("views.harness.aDigestOfTheDay")];
  }
  return [
    // A night that only worked on the pool (its record names what has headroom) takes no task, whatever it came
    // to: tried, failed or nothing left to learn, and the words say which (items 719, 762).
    // A night with nothing to tell a better text from luck (item 931) took no run either: it is left for later.
    facts.no_signal === true
      ? t("views.harness.nothingToTellABetter")
      : step.from_run === null
        ? (poolNightWords(step.state, facts) ?? t("views.harness.noTaskToLearnFrom"))
        : el("a", { href: `#/runs/${step.from_run}` }, t("views.harness.theTaskItLearnedFrom")),
    t("views.harness.partProposed", { part: tCount(candidates, "views.harness.countVersion", "views.harness.countVersions") }),
  ];
}

/** A night of learning as a row: how it ended, when, the run it learned from and what it decided. */
function stepRow(step) {
  const facts = stepFacts(step);
  const said = step.error ?? facts.unreadable ?? facts.decision ?? facts.reason ?? "";
  const candidates = Array.isArray(facts.candidates) ? facts.candidates.length : 0;
  // A night that read the day's real runs (item 987) proposes nothing: its line says what went wrong in them.
  const digest = typeof facts.digest === "object" && facts.digest !== null;
  return el(
    "div",
    { class: "set-row", dataset: { step: step.id } },
    el(
      "div",
      { class: "harness-line top" },
      el("span", { class: "harness-mark" }, pill(STEP_TONE, STEP_WORDS, step.state)),
      el(
        "div",
        { class: "harness-main" },
        el("div", { class: "harness-title" }, dateTime(step.started_at)),
        el(
          "div",
          { class: "harness-meta" },
          ...joined(stepMeta(step, facts, candidates, digest)),
        ),
        said === "" ? null : el("div", { class: "harness-said" }, said),
        // The week's word (item 988): a proposal from the week's patterns, its trial on real runs, or how it ended.
        typeof facts.week === "string" ? el("div", { class: "harness-said" }, facts.week) : null,
        // The answers of a model the night went on without (item 1015), each with why, but the week's, said above.
        ...(Array.isArray(facts.unusable) ? facts.unusable : [])
          .filter((why) => typeof why === "string" && why !== facts.week)
          .map((why) => el("div", { class: "harness-said" }, capital(why))),
        // The test the night made of the owner's run (item 904): the one it kept, or why it kept none.
        typeof facts.task === "string" ? el("div", { class: "harness-said muted" }, t("views.harness.ownTestTask", { task: facts.task })) : null,
      ),
    ),
  );
}

function experienceRow(experience) {
  return el(
    "div",
    { class: "set-row", dataset: { experience: experience.id } },
    el(
      "div",
      { class: "harness-line top" },
      el("span", { class: "harness-mark" }, el("span", { class: "chip", title: t("views.harness.howSureDotWas") }, experience.confidence)),
      el(
        "div",
        { class: "harness-main" },
        el("div", { class: "harness-title" }, experience.cue),
        el("div", { class: "harness-said" }, experience.lesson),
        el("div", { class: "harness-meta" }, experience.evidence),
        experience.tainted
          ? el("div", { class: "harness-meta" }, t("views.harness.learnedFromATaskThat"))
          : null,
      ),
    ),
  );
}

/** A tile of the overview: what it counts, its number or word (with `extra` beside it) and a line under it, whole on a
 * hover past two lines. */
function statTile({ label, value, extra = null, sub, hover = sub }) {
  return el(
    "div",
    { class: "stat-tile" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-figure" }, el("span", { class: "stat-value" }, value), extra),
    sub === "" ? null : el("div", { class: "stat-sub", title: hover }, sub),
  );
}

/** The newest nights of learning as a strip of cells, oldest at the left, each named on a hover. */
function nightStrip(steps) {
  const shown = steps.slice(0, STRIP_STEPS).reverse();
  const cells = shown.map((step) =>
    el("span", {
      class: `night-cell ${STRIP_KIND[step.state] ?? "quiet"}`,
      title: `${dateTime(step.started_at)}: ${STEP_WORDS[step.state] ?? step.state}`,
      role: "img",
      "aria-label": `${dateTime(step.started_at)}: ${STEP_WORDS[step.state] ?? step.state}`,
    }),
  );
  return el("span", { class: "night-strip", role: "group", "aria-label": t("views.harness.theNewestPartOfLearning", { part: tCount(shown.length, "views.harness.countNight", "views.harness.countNights") }) }, cells);
}

/** What the cells of the strip mean: it stands over the list of the nights, which names each night's outcome in words. */
function nightLegend() {
  const key = (kind, words) => el("span", { class: "night-key" }, el("span", { class: `night-cell ${kind}`, "aria-hidden": "true" }), words);
  return el(
    "div",
    { class: "night-legend" },
    key("done", "a change"),
    key("quiet", t("views.harness.noChange")),
    key("unusable", t("views.harness.anAnswerUnusable")),
    key("failed", "failed"),
  );
}

/** The overview above the sections: the version in use, the versions, the nights of learning and the lessons, counted in
 * all by the daemon, as the lists of nights and lessons stop at the newest. */
function overview(view, steps) {
  const counts = view.version_counts;
  const breakdown = ["adopted", "candidate", "probed", "retired"]
    .filter((state) => counts[state] > 0)
    .map((state) => `${counts[state]} ${STATE_WORDS[state]}`)
    .join(" · ");
  const lastNight = steps[0];
  const nights =
    lastNight === undefined
      ? view.step_time === "off"
        ? t("views.harness.theNightlyLearningIsOff")
        : t("views.harness.nextAtStepTime", { step_time: view.step_time })
      : t("views.harness.lastDatetimeValue", { dateTime: dateTime(lastNight.started_at), value: STEP_WORDS[lastNight.state] ?? lastNight.state });
  const tiles = [
    statTile({
      label: t("views.harness.inUse2"),
      value: view.adopted === null ? "Built-in" : "A version",
      sub: view.adopted === null ? t("views.harness.dotSOwnInstructionsAlone") : t("views.harness.notTheBuiltInInstructions"),
      hover: view.adopted === null ? t("views.harness.dotSOwnInstructionsAlone") : view.adopted.summary,
    }),
    statTile({ label: t("views.harness.versions"), value: String(view.versions_total), sub: breakdown === "" ? t("views.harness.noneYet") : breakdown }),
    statTile({ label: t("views.harness.learningNights"), value: String(view.steps_total), extra: steps.length === 0 ? null : nightStrip(steps), sub: nights }),
    statTile({
      label: t("views.harness.lessonsKept"),
      value: String(view.experiences_total),
      sub: view.experiences_total === 0 ? t("views.harness.noneUntilATestIs") : t("views.harness.fromTheNightsTests"),
    }),
  ];
  const card = setCard(...tiles);
  card.classList.add("stat-card");
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", t("views.harness.overview"));
  return card;
}

/**
 * What the version in use says, in two columns: habits and tool guidance as lists, folded to how many they are until
 * opened (`textsOpen`, kept by the page through its repaints: `onTexts(open)`); with none in use, a line that says so.
 */
function followBody(view, handlers, textsOpen, onTexts) {
  const adopted = view.adopted;
  if (adopted === null) {
    return setCard(
      setRow({
        title: t("views.harness.dotSBuiltInInstructions"),
        lead: t("views.harness.versionsAppearBelowWhenYou"),
      }),
    );
  }
  const column = (label, text) => el("div", { class: "follow-column" }, el("div", { class: "follow-label" }, label), bulletList(text));
  const reasons = handlers.reasons(adopted);
  const refusal = handlers.refusal(adopted);
  return setCard(
    el(
      "div",
      { class: "follow-head" },
      pill(STATE_TONE, STATE_WORDS, "adopted"),
      reasons.length > 0 ? outsidePill(reasons) : null,
      refusal === "" ? null : refusedPill(refusal),
      el("span", { class: "follow-summary" }, adopted.summary),
      el("button", { class: "btn sm", type: "button", onclick: () => handlers.onRetire(adopted) }, t("views.harness.setAside2")),
    ),
    refusal === ""
      ? null
      : el(
          "p",
          { class: "hint follow-refused" },
          t("views.harness.capitalThePartThatFails", { capital: capital(refusal) }),
        ),
    // Folded to how many habits and tips it holds (item 938): their words took the middle of the page on a phone.
    el(
      "details",
      { class: "follow-texts", open: textsOpen, ontoggle: (event) => onTexts(event.currentTarget.open) },
      el(
        "summary",
        null,
        `${tCount(pointsOf(adopted.habits).length, "views.harness.countHabit", "views.harness.countHabits")} · ${tCount(pointsOf(adopted.tool_guidance).length, "views.harness.countTip", "views.harness.countTips")}`,
      ),
      el(
        "div",
        { class: "follow-columns" },
        column(t("views.harness.habitsHowToWork"), adopted.habits),
        column(t("views.harness.tipsWhatThisMacAnd"), adopted.tool_guidance),
      ),
    ),
  );
}

/**
 * Where a new version is tried (item 719): on the tasks the version in use still fails sometimes, the only ones where
 * another can do better, or none yet while Dot tries the pool of test tasks a few a night to find them.
 */
function testTasks(view) {
  const each = t("views.harness.partEach", { part: tCount(view.step_repeats, "views.harness.countTime", "views.harness.countTimes") });
  const tried = t("views.harness.valueOfPoolSizeTried", { value: view.pool_size - view.untried, pool_size: view.pool_size });
  if (view.headroom.length >= view.headroom_least) {
    return t("views.harness.theOnesTheVersionIn", { join: view.headroom.join(", "), each, tried });
  }
  const found = view.headroom.length === 0 ? t("views.harness.noneSoFar") : t("views.harness.soFarJoin", { join: view.headroom.join(", ") });
  return t("views.harness.noneYetAStepRun", { each, headroom_least: view.headroom_least, found, tried });
}

/** The time the nightly learning is offered at when it is switched on, a quiet hour of most nights. */
const DEFAULT_NIGHT = "04:00";

/**
 * The switch of the nightly learning and its time (item 721): `onSchedule(stepTime, switchingOn)` keeps a time of day
 * or "off". The page's choice wins over the settings file's.
 */
function scheduleControl(view, onSchedule) {
  const on = view.step_time !== "off";
  const time = el("input", { class: "input night-time", type: "time", "aria-label": t("views.harness.atWhatTime"), value: on ? view.step_time : DEFAULT_NIGHT, hidden: !on });
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(on), "aria-label": t("views.harness.nightlyLearningOn"), title: on ? t("views.harness.switchTheNightlyLearningOff") : t("views.harness.switchTheNightlyLearningOn") });
  toggle.addEventListener("click", () => attempt(() => onSchedule(on ? "off" : time.value || DEFAULT_NIGHT, !on)));
  time.addEventListener("change", () => {
    if (time.value !== "") {
      attempt(() => onSchedule(time.value, false));
    }
  });
  return el("span", { class: "night-controls" }, time, toggle);
}

/**
 * What a night costs, in the words the page says it before the learning is switched on (item 721): since item 987 a
 * night reads Dot's own record of the day's real runs, and calls no model; once a week, the proposer (item 988).
 */
function nightCost() {
  return t("views.harness.aNightReadsDotS");
}

// The nights the page shows before "Show all" (item 938).
const NIGHTS_SHOWN = 3;

/**
 * The nightly learning: when it runs and what it does, and under that the nights it ran, the newest first: the newest
 * few, and all of them while `showAll` holds (`onShowAll()` flips it).
 */
function nightlyBody(view, steps, onSchedule, showAll, onShowAll) {
  const on = view.step_time !== "off";
  const when = setRow({
    title: on ? t("views.harness.everyNightAtStepTime", { step_time: view.step_time }) : t("views.harness.theNightlyLearningIsOff"),
    lead: on
      ? el(
          "span",
          null,
          t("views.harness.dotReadsHowTheDay", { nightCost: nightCost() }),
        )
      : el("span", null, t("views.harness.switchItOnAndDot", { nightCost: nightCost() })),
    control: el(
      "span",
      { class: "night-when" },
      on ? el("span", { class: "night-next", title: t("views.harness.byThisWindowSClock") }, icon("clock"), nextNight(view.step_time)) : null,
      scheduleControl(view, onSchedule),
    ),
  });
  if (steps.length === 0) {
    return setCard(
      when,
      emptyPanel("bolt", t("views.harness.nothingLearnedYet"), el("span", null, on ? t("views.harness.theFirstNightComesAt") : t("views.harness.switchTheNightlyLearningOn2"), el("details", { class: "error-details" }, el("summary", null, t("views.harness.details")), el("p", { class: "small muted" }, t("views.harness.inTerminal"), el("code", { class: "mono" }, "dotx harness step"), t("views.harness.runsItOnceNow"))))),
    );
  }
  const shown = showAll ? steps : steps.slice(0, NIGHTS_SHOWN);
  const more =
    steps.length > NIGHTS_SHOWN
      ? el(
          "div",
          { class: "panel-foot" },
          el("button", { class: "btn ghost sm", type: "button", "aria-expanded": String(showAll), onclick: onShowAll }, showAll ? t("views.harness.showFewer") : t("views.harness.showAllLengthNights", { length: steps.length })),
        )
      : null;
  return setCard(when, el("div", { class: "night-head" }, el("span", { class: "follow-label" }, t("views.harness.recentNights")), nightLegend()), ...shown.map(stepRow), more);
}

/** A section folded to its title and a count (item 938), open once the owner opens it; `actions` sit beside the title. */
function foldedSection(id, title, count, actions, body) {
  const card = foldCard({ id, title, actions, body, openByDefault: false });
  card.setSummary(el("span", { class: "hint" }, count));
  return card.element;
}

function fact(term, words) {
  return setRow({ title: term, lead: words });
}

/** How the learning is set (the folded section): the test tasks, the final check, who chooses and the limits. */
function howItLearns(view) {
  // A step run by hand puts a version in use only with the test tasks played at least twice, as docs/harness.md says.
  const once = view.step_repeats >= 2 ? "" : t("views.harness.theyArePlayedOnceNow");
  const adopts =
    view.adopt === "auto"
      ? t("views.harness.dotItselfInAStep", { once })
      : t("views.harness.youFromTheVersionsAbove");
  const kept =
    view.keep_days === 0
      ? t("views.harness.theNightsTheirTestsAnd")
      : t("views.harness.theNightsTheirTestsAnd2", { part: tCount(view.keep_days, "views.harness.countDay", "views.harness.countDays") });
  return foldCard({
    id: "harness-how",
    title: t("views.harness.howItLearns"),
    actions: null,
    openByDefault: false,
    body: [
      fact(t("views.harness.guards"), t("views.harness.joinPlayedEveryNightA", { join: view.scoring_tasks.join(", ") })),
      fact(t("views.harness.testTasks"), testTasks(view)),
      fact(t("views.harness.finalCheck"), t("views.harness.joinPlayedOnceAfterA", { join: view.final_tasks.join(", ") })),
      fact(
        t("views.harness.better"),
        t("views.harness.atLeastPartPassedMore", { part: tCount(view.step_margin, "views.harness.countAttempt", "views.harness.countAttempts") }),
      ),
      fact(t("views.harness.whoChooses"), adopts),
      fact(t("views.harness.limits"), t("views.harness.habitsHabitsCharsCharactersTips", { habits_chars: view.habits_chars, tool_guidance_chars: view.tool_guidance_chars, component_lines: view.component_lines })),
      fact(t("views.harness.kept"), kept),
    ],
  }).element;
}

// What a version can say (item 412), shown in the empty fields and put in them by Start from an example: lines a run
// can follow, with no secret, link, address or home path, which a version refuses.
const EXAMPLE_HABITS = [
  "- Read a file whole before you change it.",
  "- When a command fails twice the same way, stop and say why instead of trying a third time.",
  "- Keep answers short; when the details run long, put them in a file and say where.",
].join("\n");
const EXAMPLE_GUIDANCE = [
  "- Save documents in the Documents folder, not on the Desktop.",
  "- Open links in the same browser window, not a new one.",
  "- Read an Excel file with the excel skill, not as text.",
].join("\n");
const EXAMPLE_SUMMARY = t("views.harness.readBeforeEditingStopAfter");

/**
 * The fields that write a version of the owner's, for the dialog that holds them (item 615): habits and tool guidance,
 * which start from the version in use, and a summary, optional as its first lines say. `view` is the daemon's view of
 * the harness, for the words about the nightly learning and the limits a version keeps; Start from an example, which puts
 * one in the fields, is in it too. Returns the element and `submit(adoptNow)`, which saves the version, in use or not,
 * and throws when the summary is empty or the daemon refuses it.
 */
function buildForm(view) {
  const habits = el("textarea", { class: "textarea", rows: "5", "aria-label": t("views.harness.habits"), spellcheck: "false", placeholder: t("views.harness.forExampleExampleHabits", { EXAMPLE_HABITS }) });
  const guidance = el("textarea", { class: "textarea", rows: "5", "aria-label": t("views.harness.tips"), spellcheck: "false", placeholder: t("views.harness.forExampleExampleGuidance", { EXAMPLE_GUIDANCE }) });
  const summary = el("input", { class: "input", type: "text", maxlength: "200", "aria-label": t("views.harness.summary"), placeholder: t("views.harness.forExampleExampleSummary", { EXAMPLE_SUMMARY }) });
  if (view.adopted !== null) {
    habits.value = view.adopted.habits;
    guidance.value = view.adopted.tool_guidance;
  }
  const example = el("button", { class: "btn ghost", type: "button" }, icon("pencil"), t("views.harness.startFromAnExample"));
  example.addEventListener("click", () => {
    habits.value = EXAMPLE_HABITS;
    guidance.value = EXAMPLE_GUIDANCE;
    summary.value = EXAMPLE_SUMMARY;
    habits.focus();
  });
  // What the form is for, in plain words, and what the nights do (items 412 and 987).
  const nightly =
    view.step_time === "off"
      ? t("views.harness.onceATimeOfDay")
      : t("views.harness.eachNightAtStepTime", { step_time: view.step_time });
  const intro = el(
    "p",
    { class: "hint harness-intro" },
    t("views.harness.optionalYouNeedNotWrite", { nightly }),
  );
  const bounds = el(
    "p",
    { class: "hint" },
    t("views.harness.atMostHabitsCharsCharacters", { habits_chars: view.habits_chars, tool_guidance_chars: view.tool_guidance_chars, component_lines: view.component_lines }),
  );
  async function submit(adoptNow) {
    if (summary.value.trim() === "") {
      summary.focus();
      throw new Error("Say in a line what the version changes.");
    }
    await api.post("/harness", {
      habits: habits.value,
      tool_guidance: guidance.value,
      summary: summary.value.trim(),
      adopt: adoptNow,
    });
    toast("ok", adoptNow ? t("views.harness.inUseTheTasksThat") : t("views.harness.savedUnderVersionsNotIn"));
  }
  const element = el(
    "div",
    { class: "harness-form" },
    intro,
    el("div", { class: "harness-example" }, example),
    field(t("views.harness.habits"), habits, t("views.harness.howDotShouldWorkAs")),
    field(t("views.harness.tips"), guidance, t("views.harness.whatTheCommandsAppsAnd")),
    field(t("views.harness.summary"), summary, t("views.harness.oneLineForTheList")),
    bounds,
    el("p", { class: "hint" }, t("views.harness.saveForLaterKeepsIt")),
  );
  return { element, submit };
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  let steps = [];
  // Whether every night is listed, or the newest few, and whether the texts in use are open (item 938).
  let allNights = false;
  let textsOpen = false;
  let experiences = [];
  const holder = el("div", null, loadingPanel());
  // The button at the head of Versions opens the form of the owner's own version in a dialog (item 615). It is made once:
  // the page is drawn again with every read.
  const opener = el("button", { class: "harness-write", type: "button", "aria-haspopup": "dialog" }, icon("pencil"), t("views.harness.writeYourOwn"));
  opener.addEventListener("click", () => writeOwn());

  /** The versions, the one in use first (also when it is older than the page the daemon gave) and the others as the
   * daemon lists them, the newest first; under them, how many older ones there are to show. */
  function versionsBody(handlers) {
    const inPage = view.adopted !== null && view.versions.some((version) => version.id === view.adopted.id);
    const listed = [...(view.adopted === null || inPage ? [] : [view.adopted]), ...view.versions].sort(
      (one, other) => Number(other.state === "adopted") - Number(one.state === "adopted"),
    );
    if (listed.length === 0) {
      return setCard(
        emptyPanel("squarePen", t("views.harness.noVersionYet"), t("views.harness.everyTaskFollowsDotS")),
      );
    }
    // The version in use, when it is older than the page, is listed already and is not one of the older ones left.
    const older = view.versions_total - listed.length;
    const more =
      older > 0
        ? el(
            "div",
            { class: "panel-foot" },
            el("button", { class: "btn ghost sm", type: "button", onclick: () => attempt(showOlder) }, t("views.harness.showPart", { part: tCount(Math.min(older, VERSIONS_PAGE), "views.harness.countOlderVersion", "views.harness.countOlderVersions") })),
          )
        : null;
    return setCard(...listed.map((version) => versionRow(version, handlers)), more);
  }

  /** The next page of versions, under the ones shown. */
  async function showOlder() {
    const page = await api.get(`/harness?limit=${VERSIONS_PAGE}&offset=${view.versions.length}`);
    if (disposed) {
      return;
    }
    const shown = new Set(view.versions.map((version) => version.id));
    view = {
      ...view,
      versions: [...view.versions, ...page.versions.filter((version) => !shown.has(version.id))],
      from_outside: { ...view.from_outside, ...page.from_outside },
      refused: { ...view.refused, ...page.refused },
    };
    paint();
  }

  function paint() {
    if (view === null) {
      return;
    }
    const handlers = {
      onAdopt: (version) => attempt(() => adoptVersion(version)),
      onRetire: (version) => attempt(() => retireVersion(version)),
      reasons: (version) => outsideReasons(view, version),
      refusal: (version) => view.refused?.[version.id] ?? "",
    };
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections" },
        overview(view, steps),
        // What happens each night first, then what Dot follows, folded; the versions and the lessons fold to their
        // counts (item 938).
        setSection({
          title: t("views.harness.nightlyLearning"),
          body: nightlyBody(
            view,
            steps,
            (stepTime, switchingOn) => schedule(stepTime, switchingOn),
            allNights,
            () => {
              allNights = !allNights;
              paint();
            },
          ),
        }),
        setSection({
          title: t("views.harness.whatDotFollowsNow"),
          body: followBody(view, handlers, textsOpen, (open) => {
            textsOpen = open;
          }),
        }),
        foldedSection("harness-versions", t("views.harness.versions"), tCount(view.versions_total, "views.harness.countVersion", "views.harness.countVersions"), opener, versionsBody(handlers)),
        foldedSection(
          "harness-lessons",
          t("views.harness.lessonsLearned"),
          tCount(view.experiences_total, "views.harness.countLesson", "views.harness.countLessons"),
          null,
          experiences.length === 0
            ? setCard(emptyPanel("sparkle", t("views.harness.noLessonYet"), t("views.harness.aNightOfLearningKeeps")))
            : setCard(...experiences.map(experienceRow)),
        ),
        howItLearns(view),
      ),
    );
  }

  /** The form that writes a version of the owner's, in a dialog: it starts from the version in use as the daemon last
   * said it, and Save for later and Save and use are its two ways to save beside Cancel. */
  function writeOwn() {
    if (view === null) {
      return;
    }
    const form = buildForm(view);
    const saved = (adoptNow) => async () => {
      await form.submit(adoptNow);
      await reload();
      return true;
    };
    formDialog({
      title: t("views.harness.writeYourOwnVersion"),
      description: t("views.harness.habitsAndTipsForEvery"),
      body: form.element,
      submitLabel: t("views.harness.saveAndUse"),
      alternative: { label: t("views.harness.saveForLater"), onSubmit: saved(false) },
      size: "wide",
      returnFocus: opener,
      onSubmit: saved(true),
    });
  }

  /** Keep when the nightly learning runs; switching it on says first what a night costs. */
  async function schedule(stepTime, switchingOn) {
    if (switchingOn) {
      const confirmed = await confirmDialog({
        title: t("views.harness.switchTheNightlyLearningOn3"),
        message: t("views.harness.nightcostItRunsAtSteptime", { nightCost: nightCost(), stepTime }),
        confirmLabel: t("views.harness.switchOn"),
        danger: false,
        focusCancel: false,
      });
      if (!confirmed) {
        paint();
        return;
      }
    }
    const fresh = await api.put("/harness/schedule", { step_time: stepTime });
    if (disposed) {
      return;
    }
    view = { ...fresh, versions: view.versions, from_outside: view.from_outside, refused: view.refused };
    paint();
    toast("ok", stepTime === "off" ? t("views.harness.theNightlyLearningIsOff2") : t("views.harness.dotLearnsEveryNightAt", { stepTime }));
  }

  async function reload() {
    try {
      // The versions already shown stay shown (up to what the daemon gives at once), so that Use or Set aside on one of
      // the older ones does not send the list back to its first page.
      const asked = Math.min(Math.max(VERSIONS_PAGE, view === null ? 0 : view.versions.length), MOST_VERSIONS);
      const [fresh, freshSteps, freshExperiences] = await Promise.all([
        api.get(`/harness?limit=${asked}`),
        api.get("/harness/steps"),
        api.get("/harness/experiences"),
      ]);
      if (disposed) {
        return;
      }
      view = fresh;
      steps = freshSteps;
      experiences = freshExperiences;
      paint();
    } catch (error) {
      if (disposed) {
        return;
      }
      if (view === null) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      } else {
        readFailed("readFailed.learning", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  async function adoptVersion(version) {
    // A version that may come from content others can write is the owner's to weigh, and a click is not a read.
    const reasons = outsideReasons(view, version);
    if (reasons.length > 0) {
      const confirmed = await confirmDialog({
        title: t("views.harness.useAVersionWrittenFrom"),
        message: t("views.harness.aLineInThisVersion2", { join: reasons.join("; ") }),
        confirmLabel: t("views.harness.useIt"),
        danger: false,
        focusCancel: true,
      });
      if (!confirmed) {
        return;
      }
    }
    await api.post(`/harness/${version.id}/adopt`, null);
    toast("ok", t("views.harness.inUseTheTasksThat"));
    await reload();
  }

  async function retireVersion(version) {
    const confirmed = await confirmDialog({
      title: version.state === "adopted" ? t("views.harness.stopUsingThisVersion") : t("views.harness.setThisVersionAside"),
      message:
        version.state === "adopted"
          ? t("views.harness.theTasksThatStartFrom")
          : t("views.harness.itStaysHereForThe"),
      confirmLabel: t("views.harness.setAside2"),
      danger: false,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.post(`/harness/${version.id}/retire`, null);
    toast("ok", t("views.harness.setAside3"));
    await reload();
  }

  ctx.head.setLead(
    t("views.harness.dotLearnsFromItsOwn"),
  );
  root.append(holder);
  attempt(reload);
  return () => {
    disposed = true;
  };
}
