// Settings › Self-improvement (item 393, docs/harness.md: the harness): what Dot improves around its model, in plain
// words (item 503). The version in use and its texts, a form for a version of the owner's, every version with Use and
// Set aside, the nights of learning and the lessons they kept, and how the nightly learning is set.

import { api } from "../api.js";
import { clip, dateTime, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel } from "../panels.js";
import { readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const STATE_TONE = { adopted: "b-ok", probed: "b-warn", candidate: "b-info", retired: "b-muted" };
// A version's state and a night's outcome in the words of the page; the API's own words stay in the docs.
const STATE_WORDS = { adopted: "in use", probed: "tried", candidate: "new", retired: "set aside" };
const STEP_TONE = { done: "b-ok", keep: "b-muted", skipped: "b-muted", failed: "b-bad" };
const STEP_WORDS = { done: "done", keep: "no change", skipped: "skipped", failed: "failed" };
// The steps of learning the strip of the overview shows, the newest at its right end.
const STRIP_STEPS = 14;
// A night's cell in the strip, by its outcome: a change made, none, or a failure; each is named in the legend too.
const STRIP_KIND = { done: "done", keep: "quiet", skipped: "quiet", failed: "failed" };
const MADE_BY = { owner: "written by you", proposer: "proposed by Dot", composer: "combined by Dot" };
const SUMMARY_CHARS = 200;
// The keys of a version's notes the page reads out, in this order, each with its words.
const NOTE_WORDS = {
  scope: "about",
  moments: "what in the run it answers",
  contrast: "in the trial, against the version before",
  decisions: "decisions",
  retired: "set aside",
  from_run: "learned from run",
  composed_of: "combined from",
  tainted: "read content others wrote",
};

function badge(tones, words, state) {
  return el("span", { class: `badge ${tones[state] ?? "b-muted"}` }, words[state] ?? state);
}

function textBlock(text) {
  return text === "" ? el("pre", { class: "harness-text empty" }, "nothing") : el("pre", { class: "harness-text" }, text);
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
    return [el("div", { class: "row-text muted" }, `Notes that could not be read as JSON: ${notes}`)];
  }
  if (parsed === null || typeof parsed !== "object") {
    return [el("div", { class: "row-text muted" }, `Notes that are not a JSON object: ${notes}`)];
  }
  return Object.entries(NOTE_WORDS)
    .filter(([key]) => key in parsed)
    .map(([key, words]) => {
      const value = parsed[key];
      let text;
      if (key === "tainted") {
        // A candidate written from a run that read what others can author is said so, for the owner to weigh.
        text = value ? "yes: the run read a page or a message others can write; weigh the change" : "no";
      } else if (Array.isArray(value)) {
        text = value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("; ");
      } else {
        text = String(value);
      }
      return el("div", { class: "row-text" }, el("span", { class: "muted" }, `${words}: `), text);
    });
}

function versionRow(version, handlers) {
  const actions = [];
  if (version.state !== "adopted") {
    actions.push(el("button", { class: "btn sm", type: "button", onclick: () => handlers.onAdopt(version) }, "Use"));
  }
  if (version.state !== "retired") {
    actions.push(el("button", { class: "btn ghost sm", type: "button", onclick: () => handlers.onRetire(version) }, "Set aside"));
  }
  const details = el(
    "details",
    { class: "harness-details" },
    el("summary", { class: "muted" }, "Texts and notes"),
    el("div", { class: "row-text" }, "Habits"),
    textBlock(version.habits),
    el("div", { class: "row-text" }, "Tool guidance"),
    textBlock(version.tool_guidance),
    ...noteLines(version.notes),
  );
  return el(
    "div",
    { class: version.state === "retired" ? "list-row faint" : "list-row", "data-version": version.id },
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row" },
        badge(STATE_TONE, STATE_WORDS, version.state),
        el("span", { class: "row-title" }, clip(version.summary, SUMMARY_CHARS)),
      ),
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: "mono faint", title: "The version's id" }, version.id),
        el("span", { class: "faint" }, MADE_BY[version.made_by] ?? `by ${version.made_by}`),
        el("span", { class: "faint" }, dateTime(version.created_at)),
      ),
      details,
    ),
    el("div", { class: "row-actions" }, ...actions),
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

function stepRow(step) {
  const facts = stepFacts(step);
  const said = step.error ?? facts.unreadable ?? facts.decision ?? facts.reason ?? "";
  const candidates = Array.isArray(facts.candidates) ? facts.candidates.length : 0;
  return el(
    "div",
    { class: "list-row", "data-step": step.id },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, badge(STEP_TONE, STEP_WORDS, step.state), el("span", { class: "row-title" }, dateTime(step.started_at))),
      el(
        "div",
        { class: "row-meta" },
        step.from_run === null
          ? el("span", { class: "faint" }, "no run to learn from")
          : el("a", { class: "faint", href: `#/runs/${step.from_run}` }, "the run it learned from"),
        el("span", { class: "faint" }, `${plural(candidates, "version", "versions")} proposed`),
      ),
      said === "" ? null : el("div", { class: "row-text" }, said),
    ),
  );
}

function experienceRow(experience) {
  return el(
    "div",
    { class: "list-row", "data-experience": experience.id },
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row" },
        el("span", { class: "chip", title: "How sure the writer was" }, experience.confidence),
        el("span", { class: "row-title" }, experience.cue),
      ),
      el("div", { class: "row-text" }, experience.lesson),
      el("div", { class: "row-text muted" }, experience.evidence),
      experience.tainted
        ? el("div", { class: "row-text muted" }, "Learned from a run that read content others wrote, so Dot never reads it when it proposes a change.")
        : null,
    ),
  );
}

/** A tile of the overview: what it counts, the number or word, and a line under it, whole on a hover past two lines. */
function statTile(label, value, sub, ...extra) {
  return el(
    "div",
    { class: "stat-tile" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-value" }, value),
    sub === "" ? null : el("div", { class: "stat-sub", title: sub }, sub),
    ...extra,
  );
}

/** The newest steps of learning as a strip of cells, oldest at the left, each named on a hover and in the legend. */
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
  const key = (kind, words) => el("span", { class: "night-key" }, el("span", { class: `night-cell ${kind}`, "aria-hidden": "true" }), words);
  return el(
    "div",
    { class: "night-strip-box" },
    el("div", { class: "night-strip", role: "group", "aria-label": `The newest ${plural(shown.length, "step", "steps")} of learning` }, cells),
    el("div", { class: "night-legend" }, key("done", "a change"), key("quiet", "no change"), key("failed", "failed")),
  );
}

/** The overview above the sections: the version in use, the versions, the steps of learning and the lessons, counted
 * in all by the daemon, as the lists of steps and lessons stop at the newest. */
function overview(view, steps) {
  const counts = {};
  for (const version of view.versions) {
    counts[version.state] = (counts[version.state] ?? 0) + 1;
  }
  const breakdown = ["adopted", "candidate", "probed", "retired"]
    .filter((state) => counts[state] > 0)
    .map((state) => `${counts[state]} ${STATE_WORDS[state]}`)
    .join(" · ");
  const lastNight = steps[0];
  return el(
    "div",
    { class: "stat-row", role: "group", "aria-label": "Overview" },
    statTile(
      "In use",
      view.adopted === null ? "Built-in" : "A version",
      view.adopted === null ? "Dot's own instructions alone" : view.adopted.summary,
    ),
    statTile("Versions", String(view.versions.length), breakdown === "" ? "none yet" : breakdown),
    statTile(
      "Learning steps",
      String(view.steps_total),
      lastNight === undefined ? (view.step_time === "off" ? "the nightly learning is off" : `next at ${view.step_time}`) : `last: ${STEP_WORDS[lastNight.state] ?? lastNight.state}, ${dateTime(lastNight.started_at)}`,
      steps.length === 0 ? null : nightStrip(steps),
    ),
    statTile("Lessons kept", String(view.experiences_total), view.experiences_total === 0 ? "none until a trial is clear" : "from the trials of the steps"),
  );
}

function fact(term, words) {
  return [el("dt", null, term), el("dd", null, words)];
}

function loopFacts(view) {
  // A setting of the config file is said in words, its key after it in faint type (docs/phone.md).
  const when =
    view.step_time === "off"
      ? el(
          "span",
          null,
          "off: set a time of day in the config file, and Dot learns once a night. ",
          el("span", { class: "faint" }, "In the config file: [harness] step_time."),
        )
      : `every night at ${view.step_time}`;
  // The night puts a version in use only with the test tasks played at least twice, as docs/harness.md says.
  const once = view.step_repeats >= 2 ? "" : " (they are played once now, so it puts nothing in use until they are played twice)";
  const adopts =
    view.adopt === "auto"
      ? `Dot itself, when the trial showed a version better on a task and worse on none with the test tasks played twice${once}`
      : "you, from the versions below";
  return [
    ...fact("When", when),
    ...fact("Test tasks", `${view.scoring_tasks.join(", ")} (${plural(view.step_repeats, "time", "times")} each), on which each new version is tried`),
    ...fact("Final check", `${view.final_tasks.join(", ")}, played once by dotx harness final after a version is in use, never to choose one`),
    ...fact("Who chooses", adopts),
    ...fact("Limits", `habits ${view.habits_chars} characters, tool guidance ${view.tool_guidance_chars}, ${view.component_lines} lines each`),
  ];
}

// What a version can say (item 412), shown in the empty fields and put in them by Start from an example: lines a run
// can follow, with no secret, link, address or home path, which a version refuses.
const EXAMPLE_HABITS = [
  "- Read a file whole before you change it.",
  "- When a command fails twice the same way, stop and say why instead of trying a third time.",
  "- Keep answers short; when the details run long, put them in a file and say where.",
].join("\n");
const EXAMPLE_GUIDANCE = [
  "- Search code with rg, not grep -r.",
  "- Run a project's tests with the command its README gives, not one you guess.",
  "- Read an Excel file with the excel skill, not as text.",
].join("\n");
const EXAMPLE_SUMMARY = "Read before editing, stop after two failures, short answers";

function buildForm(onWritten) {
  const habits = el("textarea", { class: "textarea", rows: "5", "aria-label": "Habits", spellcheck: "false", placeholder: `For example:\n${EXAMPLE_HABITS}` });
  const guidance = el("textarea", { class: "textarea", rows: "5", "aria-label": "Tool guidance", spellcheck: "false", placeholder: `For example:\n${EXAMPLE_GUIDANCE}` });
  const summary = el("input", { class: "input", type: "text", maxlength: "200", "aria-label": "Summary", placeholder: `For example: ${EXAMPLE_SUMMARY}` });
  const save = el("button", { class: "btn sm", type: "button" }, "Save for later");
  const adopt = el("button", { class: "btn primary sm", type: "button" }, icon("check"), "Save and use");
  const example = el("button", { class: "btn ghost sm", type: "button" }, icon("pencil"), "Start from an example");
  example.addEventListener("click", () => {
    habits.value = EXAMPLE_HABITS;
    guidance.value = EXAMPLE_GUIDANCE;
    summary.value = EXAMPLE_SUMMARY;
    habits.focus();
  });
  // What the section is for, in plain words: writing a version is optional, the nightly learning writes them (item 412).
  const intro = el("p", { class: "hint harness-intro" });
  const bounds = el("p", { class: "hint" });
  async function submit(adoptNow) {
    if (summary.value.trim() === "") {
      summary.focus();
      throw new Error("Say in a line what the version changes.");
    }
    save.disabled = true;
    adopt.disabled = true;
    try {
      await api.post("/harness", {
        habits: habits.value,
        tool_guidance: guidance.value,
        summary: summary.value.trim(),
        adopt: adoptNow,
      });
      toast("ok", adoptNow ? "In use: the runs that start from now on follow it." : "Saved under Versions, not in use.");
      summary.value = "";
      await onWritten();
    } finally {
      save.disabled = false;
      adopt.disabled = false;
    }
  }
  save.addEventListener("click", () => attempt(() => submit(false)));
  adopt.addEventListener("click", () => attempt(() => submit(true)));
  // The fields stay folded until the owner asks for them: writing a version is optional (item 412).
  const fields = el(
    "div",
    { class: "harness-form-fields", hidden: true },
    field("Habits", habits, "How Dot should work, as you would brief a new colleague: one rule a line. Every run reads them after Dot's own way of working."),
    field("Tool guidance", guidance, "What the commands, applications and sites of this Mac want: one line each. Every run reads it after the list of its tools."),
    field("Summary", summary, "One line for the list of versions: what this version changes."),
    bounds,
    el("p", { class: "hint" }, "Save for later keeps it under Versions without using it; Save and use puts it into every run that starts from now on. Set aside, on a version, stops using it, and Dot follows its built-in instructions alone again."),
    el("div", { class: "panel-foot" }, example, el("span", { class: "spacer" }), save, adopt),
  );
  const openWords = el("span", null, "Write a version");
  const open = el("button", { class: "btn sm", type: "button", "aria-expanded": "false" }, icon("pencil"), openWords);
  open.addEventListener("click", () => {
    fields.hidden = !fields.hidden;
    open.setAttribute("aria-expanded", String(!fields.hidden));
    openWords.textContent = fields.hidden ? "Write a version" : "Fold the form";
    if (!fields.hidden) {
      habits.focus();
    }
  });
  const element = el("div", { class: "harness-form" }, intro, el("div", { class: "row" }, open), fields);
  return {
    element,
    fill: (text) => {
      habits.value = text.habits;
      guidance.value = text.tool_guidance;
    },
    /** Say what the nightly learning does and the limits a version keeps, from the daemon's view of the harness. */
    describe: (view) => {
      const nightly =
        view.step_time === "off"
          ? "Once a time of day is set for the nightly learning ([harness] step_time in the config file), Dot reads what its runs did each night and proposes versions by itself."
          : `Each night at ${view.step_time} Dot reads what its runs did and proposes versions by itself.`;
      intro.textContent = `Optional: you need not write anything here. ${nightly} They appear under Versions for you to use. Write one yourself only when you want every run to keep a way of working you choose.`;
      bounds.textContent = `At most ${view.habits_chars} characters of habits and ${view.tool_guidance_chars} of tool guidance, ${view.component_lines} lines each, no line past 300 characters; no secret, link, e-mail address, home path or account name: a version with one is refused.`;
    },
  };
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  let steps = [];
  let experiences = [];
  const inUse = el("div", null, loadingPanel());
  const overviewHolder = el("div");
  const versionsHolder = el("div");
  const versionsCount = el("span", { class: "hint" });
  const stepsHolder = el("div");
  const experiencesHolder = el("div");
  const facts = el("dl", { class: "harness-facts" });
  // The form is filled from the version in use once, and again after the owner's own save, never over what the owner
  // is typing when a row's Use or Set aside reloads the page.
  let fillForm = true;
  const form = buildForm(() => {
    fillForm = true;
    return reload();
  });

  function paint() {
    if (view === null) {
      return;
    }
    if (view.adopted === null) {
      inUse.replaceChildren(
        el("div", { class: "row-text" }, "Dot's built-in instructions alone: no version is in use."),
        el("div", { class: "row-text muted" }, "Versions appear below when the nightly learning proposes one; you can also write one."),
      );
      if (fillForm) {
        form.fill({ habits: "", tool_guidance: "" });
      }
    } else {
      inUse.replaceChildren(
        el("div", { class: "row" }, badge(STATE_TONE, STATE_WORDS, "adopted"), el("span", { class: "row-title" }, view.adopted.summary)),
        el(
          "div",
          { class: "harness-columns" },
          el("div", { class: "stack tight" }, el("div", { class: "row-text muted" }, "Habits"), textBlock(view.adopted.habits)),
          el("div", { class: "stack tight" }, el("div", { class: "row-text muted" }, "Tool guidance"), textBlock(view.adopted.tool_guidance)),
        ),
      );
      if (fillForm) {
        form.fill(view.adopted);
      }
    }
    fillForm = false;
    const handlers = {
      onAdopt: (version) => attempt(() => adoptVersion(version)),
      onRetire: (version) => attempt(() => retireVersion(version)),
    };
    versionsHolder.replaceChildren(
      view.versions.length === 0
        ? emptyPanel("squarePen", "No version yet", "Every run follows Dot's built-in instructions. A version appears here when the nightly learning proposes one, or when you write one above.")
        : el("div", { class: "rule-list" }, view.versions.map((version) => versionRow(version, handlers))),
    );
    versionsCount.textContent = plural(view.versions.length, "version", "versions");
    stepsHolder.replaceChildren(
      steps.length === 0
        ? emptyPanel("bolt", "Nothing learned yet", "A time of day in the config file (its key is [harness] step_time) has Dot learn once a night; dotx harness step runs it once now.")
        : el("div", { class: "rule-list" }, steps.map(stepRow)),
    );
    experiencesHolder.replaceChildren(
      experiences.length === 0
        ? emptyPanel("sparkle", "No lesson yet", "A night of learning keeps one when a change clearly helped or hurt in its trial.")
        : el("div", { class: "rule-list lesson-grid" }, experiences.map(experienceRow)),
    );
    overviewHolder.replaceChildren(overview(view, steps));
    facts.replaceChildren(...loopFacts(view));
    form.describe(view);
  }

  async function reload() {
    try {
      const [fresh, freshSteps, freshExperiences] = await Promise.all([
        api.get("/harness"),
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
        inUse.replaceChildren(errorPanel(error, () => attempt(retry)));
      } else {
        readFailed("The harness", error);
      }
    }
  }

  async function retry() {
    inUse.replaceChildren(loadingPanel());
    await reload();
  }

  async function adoptVersion(version) {
    await api.post(`/harness/${version.id}/adopt`, null);
    toast("ok", "In use: the runs that start from now on follow it.");
    await reload();
  }

  async function retireVersion(version) {
    const confirmed = await confirmDialog({
      title: version.state === "adopted" ? "Stop using this version?" : "Set this version aside?",
      message:
        version.state === "adopted"
          ? "The runs that start from now on follow Dot's built-in instructions alone. The version stays here to come back to."
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

  function section(title, head, body) {
    return el(
      "section",
      { class: "memory-panel" },
      el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, title), ...head),
      body,
    );
  }

  ctx.head.setLead("Dot learns from its own runs how to work better on this Mac. Besides its built-in instructions, every run follows two short texts: habits (how to work) and tool guidance (what the commands, apps and sites of this Mac want). Each night Dot can propose a new version of them and try it on test tasks first; you choose the version it follows. You never have to write one yourself.");
  root.append(
    overviewHolder,
    section("What Dot follows now", [], inUse),
    section("Write your own (optional)", [], form.element),
    // The versions and the nights side by side where the page is wide enough, one under the other on a phone.
    el("div", { class: "harness-grid" }, section("Versions", [versionsCount], versionsHolder), section("Nightly learning", [], stepsHolder)),
    section("Lessons learned", [], experiencesHolder),
    section("How it learns", [], facts),
  );
  attempt(reload);
  return () => {
    disposed = true;
  };
}
