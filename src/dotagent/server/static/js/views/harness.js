// Settings › Self-improvement (items 393 and 566, docs/harness.md: the harness): what Dot improves around its model, in plain
// words (item 503). An overview of four tiles, the version in use with its two texts, the versions with Use and Set aside and
// a form for one of the owner's, which opens in a dialog (item 615), the nightly learning with the nights it ran, the
// lessons they kept, and how it is set.

import { api } from "../api.js";
import { clip, dateTime, el, joined, nextNight, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, foldCard, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { readFailed } from "../state.js";
import { attempt, confirmDialog, formDialog, settingFold, toast } from "../ui.js";
import { codeText } from "./memory.js";

const STATE_TONE = { adopted: "b-ok", probed: "b-warn", candidate: "b-info", retired: "b-muted" };
// A version's state and a night's outcome in the words of the page; the API's own words stay in the docs.
const STATE_WORDS = { adopted: "in use", probed: "tried", candidate: "new", retired: "set aside" };
const STEP_TONE = { done: "b-ok", keep: "b-muted", skipped: "b-muted", failed: "b-bad" };
const STEP_WORDS = { done: "done", keep: "no change", skipped: "skipped", failed: "failed" };
// The nights the strip of the overview shows, the newest at its right end.
const STRIP_STEPS = 14;
// A night's cell in the strip, by its outcome: a change made, none, or a failure; each is named in the legend too.
const STRIP_KIND = { done: "done", keep: "quiet", skipped: "quiet", failed: "failed" };
const MADE_BY = { owner: "written by you", proposer: "proposed by Dot", composer: "combined by Dot" };
const SUMMARY_CHARS = 200;
// How many versions the list shows at first and each time **Show older versions** is pressed (the daemon pages them).
const VERSIONS_PAGE = 50;
// The most versions the daemon gives in one answer.
const MOST_VERSIONS = 500;
// The keys of a version's notes the page reads out, in this order, each with its words.
const NOTE_WORDS = {
  scope: "about",
  moments: "what in the task it answers",
  contrast: "in the test, against the version before",
  confirmation: "in the test played again, before Dot used it itself",
  decisions: "decisions",
  retired: "set aside",
  from_run: "learned from task",
  composed_of: "combined from",
  tainted: "read content others wrote",
  tainted_by: "what it read",
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
      title: `A line in this version may come from content others can write: ${reasons.join("; ")}.`,
    },
    "From outside content",
  );
}

/** The pill of a version whose text the daemon's checks refuse now: it is not put in use, and a run never takes it. */
function refusedPill(refusal) {
  return el("span", { class: "badge b-bad", title: `${capital(refusal)}. A version like this is not put in use.` }, "Refused");
}

/** A pill with the name of a state or an outcome: green for what is in use or done, red for a failure, grey for the rest. */
function pill(tones, words, state) {
  return el("span", { class: `badge ${tones[state] ?? "b-muted"}` }, capital(words[state] ?? state));
}

function textBlock(text) {
  return text === "" ? el("pre", { class: "harness-text empty" }, "nothing") : el("pre", { class: "harness-text" }, text);
}

/** A text of a version as a list, a line a point: the dash a line starts with is the list's own mark, and the words
 * between backticks are code. */
function bulletList(text) {
  const lines = text
    .split("\n")
    .map((line) => line.replace(/^\s*[-*•]\s+/, "").trim())
    .filter((line) => line !== "");
  if (lines.length === 0) {
    return el("p", { class: "follow-none" }, "nothing");
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
    return [el("div", { class: "row-text muted" }, `These notes could not be read: ${notes}`)];
  }
  if (parsed === null || typeof parsed !== "object") {
    return [el("div", { class: "row-text muted" }, `These notes are not in the form Dot expects: ${notes}`)];
  }
  return Object.entries(NOTE_WORDS)
    .filter(([key]) => key in parsed)
    .map(([key, words]) => {
      const value = parsed[key];
      let text;
      if (key === "tainted") {
        // A candidate written from a run that read what others can author is said so, for the owner to weigh.
        text = value ? "yes: the task read a page or a message others can write; weigh the change" : "no";
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
    buttons.push(el("button", { class: "btn sm", type: "button", onclick: () => handlers.onAdopt(version) }, "Use"));
  }
  // The version in use is set aside from What Dot follows now, above; a set-aside one has nothing left to set aside.
  if (version.state === "candidate" || version.state === "probed") {
    buttons.push(el("button", { class: "btn ghost sm", type: "button", onclick: () => handlers.onRetire(version) }, "Set aside"));
  }
  const details = el(
    "div",
    { class: "version-details", id: `version-${version.id}`, hidden: true },
    el(
      "div",
      { class: "version-facts" },
      ...joined([
        el("span", { class: "mono", title: "The version's ID" }, version.id),
        MADE_BY[version.made_by] ?? `by ${version.made_by}`,
        dateTime(version.created_at),
      ]),
    ),
    refusal === "" ? null : el("div", { class: "row-text" }, el("span", { class: "muted" }, "Refused: "), `${refusal}. Set it aside and write another.`),
    el("div", { class: "version-label" }, "Habits"),
    textBlock(version.habits),
    el("div", { class: "version-label" }, "Tips"),
    textBlock(version.tool_guidance),
    ...noteLines(version.notes),
  );
  const toggle = el("button", { class: "version-toggle", type: "button", "aria-expanded": "false", "aria-controls": details.id }, "Full text and notes");
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
    return { unreadable: `This step's record could not be read: ${error.message}` };
  }
}

/** A night of learning as a row: how it ended, when, the run it learned from and what it decided. */
function stepRow(step) {
  const facts = stepFacts(step);
  const said = step.error ?? facts.unreadable ?? facts.decision ?? facts.reason ?? "";
  const candidates = Array.isArray(facts.candidates) ? facts.candidates.length : 0;
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
          ...joined([
            step.from_run === null ? "no task to learn from" : el("a", { href: `#/runs/${step.from_run}` }, "the task it learned from"),
            `${plural(candidates, "version", "versions")} proposed`,
          ]),
        ),
        said === "" ? null : el("div", { class: "harness-said" }, said),
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
      el("span", { class: "harness-mark" }, el("span", { class: "chip", title: "How sure Dot was" }, experience.confidence)),
      el(
        "div",
        { class: "harness-main" },
        el("div", { class: "harness-title" }, experience.cue),
        el("div", { class: "harness-said" }, experience.lesson),
        el("div", { class: "harness-meta" }, experience.evidence),
        experience.tainted
          ? el("div", { class: "harness-meta" }, "Learned from a task that read content others wrote, so Dot never reads it when it suggests a change.")
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
  return el("span", { class: "night-strip", role: "group", "aria-label": `The newest ${plural(shown.length, "night", "nights")} of learning` }, cells);
}

/** What the cells of the strip mean: it stands over the list of the nights, which names each night's outcome in words. */
function nightLegend() {
  const key = (kind, words) => el("span", { class: "night-key" }, el("span", { class: `night-cell ${kind}`, "aria-hidden": "true" }), words);
  return el("div", { class: "night-legend" }, key("done", "a change"), key("quiet", "no change"), key("failed", "failed"));
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
        ? "The nightly learning is off"
        : `Next at ${view.step_time}`
      : `Last: ${dateTime(lastNight.started_at)} · ${STEP_WORDS[lastNight.state] ?? lastNight.state}`;
  const tiles = [
    statTile({
      label: "In use",
      value: view.adopted === null ? "Built-in" : "A version",
      sub: view.adopted === null ? "Dot's own instructions alone" : "Not the built-in instructions",
      hover: view.adopted === null ? "Dot's own instructions alone" : view.adopted.summary,
    }),
    statTile({ label: "Versions", value: String(view.versions_total), sub: breakdown === "" ? "None yet" : breakdown }),
    statTile({ label: "Learning nights", value: String(view.steps_total), extra: steps.length === 0 ? null : nightStrip(steps), sub: nights }),
    statTile({
      label: "Lessons kept",
      value: String(view.experiences_total),
      sub: view.experiences_total === 0 ? "None until a test is clear" : "From the nights' tests",
    }),
  ];
  const card = setCard(...tiles);
  card.classList.add("stat-card");
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", "Overview");
  return card;
}

/** What the version in use says, in two columns: habits and tool guidance as lists; with none in use, a line that says so. */
function followBody(view, handlers) {
  const adopted = view.adopted;
  if (adopted === null) {
    return setCard(
      setRow({
        title: "Dot's built-in instructions alone: no version is in use.",
        lead: "Versions appear below when the nightly learning proposes one; you can also write one.",
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
      el("button", { class: "btn sm", type: "button", onclick: () => handlers.onRetire(adopted) }, "Set aside"),
    ),
    refusal === ""
      ? null
      : el(
          "p",
          { class: "hint follow-refused" },
          `${capital(refusal)}: the part that fails is left out of every task's instructions. Set this version aside and write another.`,
        ),
    el(
      "div",
      { class: "follow-columns" },
      column("Habits · how to work", adopted.habits),
      column("Tips · what this Mac and its apps want", adopted.tool_guidance),
    ),
  );
}

/** The nightly learning: when it runs and what it does, and under that the nights it ran, the newest first. */
function nightlyBody(view, steps) {
  const on = view.step_time !== "off";
  const when = setRow({
    title: on ? `Every night at ${view.step_time}` : "The nightly learning is off",
    lead: on
      ? el(
          "span",
          null,
          "Dot reads what its tasks did and proposes versions by itself; you never have to write one. The time can be changed in the settings file.",
          settingFold("[harness] step_time", "The key is"),
        )
      : el(
          "span",
          null,
          "Set a time of day in the settings file, and Dot learns once a night.",
          settingFold("[harness] step_time", "The key is"),
        ),
    control: on
      ? el("span", { class: "night-next", title: "By this window's clock; Dot reads the time in the time zone of its schedules" }, icon("clock"), nextNight(view.step_time))
      : null,
  });
  if (steps.length === 0) {
    return setCard(
      when,
      emptyPanel("bolt", "Nothing learned yet", el("span", null, "Once a time of day is set in the settings file, Dot learns once a night.", el("details", { class: "error-details" }, el("summary", null, "Details"), el("p", { class: "small muted" }, "The key is ", el("code", { class: "mono" }, "[harness] step_time"), ". In Terminal, ", el("code", { class: "mono" }, "dotx harness step"), " runs it once now.")))),
    );
  }
  return setCard(when, el("div", { class: "night-head" }, el("span", { class: "follow-label" }, "Recent nights"), nightLegend()), ...steps.map(stepRow));
}

function fact(term, words) {
  return setRow({ title: term, lead: words });
}

/** How the learning is set (the folded section): the test tasks, the final check, who chooses and the limits. */
function howItLearns(view) {
  // The night puts a version in use only with the test tasks played at least twice, as docs/harness.md says.
  const once = view.step_repeats >= 2 ? "" : " (they are played once now, so it puts nothing in use until they are played twice)";
  const adopts =
    view.adopt === "auto"
      ? `Dot itself, when the test showed a version better and worse on no task with the test tasks played twice${once}, and the test played again on new attempts showed it better again; never over a version you chose since the night began`
      : "you, from the versions above";
  const kept =
    view.keep_days === 0
      ? "The nights, their tests and the versions you set aside are kept for ever"
      : `The nights, their tests and the versions you set aside are deleted after ${plural(view.keep_days, "day", "days")}, but for the version in use and the ones it came from`;
  return foldCard({
    id: "harness-how",
    title: "How it learns",
    actions: null,
    openByDefault: false,
    body: [
      fact("Test tasks", `${view.scoring_tasks.join(", ")} (${plural(view.step_repeats, "time", "times")} each), on which each new version is tried`),
      fact("Final check", `${view.final_tasks.join(", ")}, played once after a version is in use (in Terminal: dotx harness final), never to choose one`),
      fact(
        "Better",
        `at least ${plural(view.step_margin, "attempt", "attempts")} passed more than the version in use, over all the tasks together, and fewer on none; with less the test says it is unclear, and why`,
      ),
      fact("Who chooses", adopts),
      fact("Limits", `habits ${view.habits_chars} characters, tips ${view.tool_guidance_chars}, ${view.component_lines} lines each`),
      fact("Kept", kept),
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
const EXAMPLE_SUMMARY = "Read before editing, stop after two failures, short answers";

/**
 * The fields that write a version of the owner's, for the dialog that holds them (item 615): habits and tool guidance,
 * which start from the version in use, and a summary, optional as its first lines say. `view` is the daemon's view of
 * the harness, for the words about the nightly learning and the limits a version keeps; Start from an example, which puts
 * one in the fields, is in it too. Returns the element and `submit(adoptNow)`, which saves the version, in use or not,
 * and throws when the summary is empty or the daemon refuses it.
 */
function buildForm(view) {
  const habits = el("textarea", { class: "textarea", rows: "5", "aria-label": "Habits", spellcheck: "false", placeholder: `For example:\n${EXAMPLE_HABITS}` });
  const guidance = el("textarea", { class: "textarea", rows: "5", "aria-label": "Tips", spellcheck: "false", placeholder: `For example:\n${EXAMPLE_GUIDANCE}` });
  const summary = el("input", { class: "input", type: "text", maxlength: "200", "aria-label": "Summary", placeholder: `For example: ${EXAMPLE_SUMMARY}` });
  if (view.adopted !== null) {
    habits.value = view.adopted.habits;
    guidance.value = view.adopted.tool_guidance;
  }
  const example = el("button", { class: "btn ghost", type: "button" }, icon("pencil"), "Start from an example");
  example.addEventListener("click", () => {
    habits.value = EXAMPLE_HABITS;
    guidance.value = EXAMPLE_GUIDANCE;
    summary.value = EXAMPLE_SUMMARY;
    habits.focus();
  });
  // What the form is for, in plain words: writing a version is optional, the nightly learning writes them (item 412).
  const nightly =
    view.step_time === "off"
      ? "Once a time of day is set for the nightly learning in the settings file, Dot reads what its tasks did each night and proposes versions by itself."
      : `Each night at ${view.step_time} Dot reads what its tasks did and proposes versions by itself.`;
  const intro = el(
    "p",
    { class: "hint harness-intro" },
    `Optional: you need not write anything here. ${nightly} They appear under Versions for you to use. Write one yourself only when you want every task to keep a way of working you choose.`,
  );
  const bounds = el(
    "p",
    { class: "hint" },
    `At most ${view.habits_chars} characters of habits and ${view.tool_guidance_chars} of tips, ${view.component_lines} lines each, no line past 300 characters. A version with a secret, a link, an e-mail address, a file location or an account name, with a character that prints nothing, or with a line that starts like a heading or a note of the system, is refused.`,
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
    toast("ok", adoptNow ? "In use: the tasks that start from now on follow it." : "Saved under Versions, not in use.");
  }
  const element = el(
    "div",
    { class: "harness-form" },
    intro,
    el("div", { class: "harness-example" }, example),
    field("Habits", habits, "How Dot should work, as you would brief a new colleague: one rule a line. Every task reads them after Dot's own way of working."),
    field("Tips", guidance, "What the commands, apps and sites of this Mac want: one line each. Every task reads them after the list of what it can do."),
    field("Summary", summary, "One line for the list of versions: what this version changes."),
    bounds,
    el("p", { class: "hint" }, "Save for later keeps it under Versions without using it; Save and use puts it into every task that starts from now on. Set aside, on a version, stops using it, and Dot follows its built-in instructions alone again."),
  );
  return { element, submit };
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  let steps = [];
  let experiences = [];
  const holder = el("div", null, loadingPanel());
  // The button at the head of Versions opens the form of the owner's own version in a dialog (item 615). It is made once:
  // the page is drawn again with every read.
  const opener = el("button", { class: "harness-write", type: "button", "aria-haspopup": "dialog" }, icon("pencil"), "Write your own");
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
        emptyPanel("squarePen", "No version yet", "Every task follows Dot's built-in instructions. A version appears here when the nightly learning proposes one, or when you write one."),
      );
    }
    // The version in use, when it is older than the page, is listed already and is not one of the older ones left.
    const older = view.versions_total - listed.length;
    const more =
      older > 0
        ? el(
            "div",
            { class: "panel-foot" },
            el("button", { class: "btn ghost sm", type: "button", onclick: () => attempt(showOlder) }, `Show ${plural(Math.min(older, VERSIONS_PAGE), "older version", "older versions")}`),
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
        setSection({ title: "What Dot follows now", body: followBody(view, handlers) }),
        setSection({
          title: ["Versions", el("span", { class: "harness-count" }, String(view.versions_total))],
          actions: opener,
          body: versionsBody(handlers),
        }),
        setSection({ title: "Nightly learning", body: nightlyBody(view, steps) }),
        setSection({
          title: "Lessons learned",
          body:
            experiences.length === 0
              ? setCard(emptyPanel("sparkle", "No lesson yet", "A night of learning keeps one when a change clearly helped or hurt in its test."))
              : setCard(...experiences.map(experienceRow)),
        }),
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
      title: "Write your own version",
      description: "Habits and tips for every task, written by you.",
      body: form.element,
      submitLabel: "Save and use",
      alternative: { label: "Save for later", onSubmit: saved(false) },
      size: "wide",
      returnFocus: opener,
      onSubmit: saved(true),
    });
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
        readFailed("The learning", error);
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
        title: "Use a version written from outside content?",
        message: `A line in this version may come from content others can write: ${reasons.join("; ")}. Read its full text under Full text and notes first: the tasks that start from now on follow it.`,
        confirmLabel: "Use it",
        danger: false,
        focusCancel: true,
      });
      if (!confirmed) {
        return;
      }
    }
    await api.post(`/harness/${version.id}/adopt`, null);
    toast("ok", "In use: the tasks that start from now on follow it.");
    await reload();
  }

  async function retireVersion(version) {
    const confirmed = await confirmDialog({
      title: version.state === "adopted" ? "Stop using this version?" : "Set this version aside?",
      message:
        version.state === "adopted"
          ? "The tasks that start from now on follow Dot's built-in instructions alone. The version stays here to come back to."
          : "It stays here, for the record and to come back to.",
      confirmLabel: "Set aside",
      danger: false,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.post(`/harness/${version.id}/retire`, null);
    toast("ok", "Set aside.");
    await reload();
  }

  ctx.head.setLead(
    "Dot learns from its own tasks how to work better on this Mac. Each night it can suggest new habits and tips, test them on practice tasks first, and you choose the version it follows.",
  );
  root.append(holder);
  attempt(reload);
  return () => {
    disposed = true;
  };
}
