// Rules: your own answers to "may Dot do this?", checked in order before the safety preset decides. A rule reads as a
// sentence: "When Dot would use {tool}, matching {pattern}: {answer}", and the answer is one of four. A rule can be
// said in your own words (item 110): the review model drafts its parts, the daemon writes the pattern from them, and
// the draft fills the form, its description the sentence the rule reads back as, to be checked and added;
// `#/settings/rules?sentence=…` (an approval card's "Make a rule of this") puts a sentence in the box. A rule's arrows
// move it up or down the order, and a rule that another one decides in place of wherever it fits says it never decides
// (item 449): an earlier one, or one that hands the step to you, which decides wherever it stands (item 481).
// Above the rules, the switch of the macOS sandbox of a run's commands (item 262). The page follows the design (item
// 565): the protections that are always on in a card of their own, the sandbox under Commands, the rules as the rows of
// one card, and, under them, what a rule can answer; with no rule, the card offers the sentences to start from.

import { api } from "../api.js";
import { ago, clip, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, field, inlineError, inlineLoading, paintOpener, segmented, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { outcomeLabel, previewLines, usageLine } from "../rulepreview.js";
import { onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

// How far back a rule is tried before it is added (item 469).
const TRY_OUT_DAYS = 30;
const QUOTE_CHARS = 100;
// The live event of a rule added, switched or deleted on any page (`rules_changed`, `dotagent.server.routes_data`).
const RULES_CHANGED = "rules_changed";
// A pattern longer than this is cut in the sentence a rule reads as; the whole of it is its tooltip and in the form.
const PATTERN_CHARS = 40;
// Sentences to start from: a click puts one in the box of the words (the drafter turns each into a rule: a command or
// two of the shell, a command that stops Dot, every step on one site).
const EXAMPLES = [
  "Let Dot run git status and git diff without asking",
  "Ask me before Dot deletes anything with rm",
  "Hand me every step on mybank.com",
];
// The four answers, in the order of the control. `label` is the answer as it reads in the sentence; `text` says what
// Dot does then (in the form, in a rule's tooltip and in the legend under the rules); `tone` colours its badge.
const ANSWERS = [
  {
    id: "allow",
    label: "Do it",
    tone: "b-ok",
    text: "Without asking. After reading untrusted content, it still asks before reaching outside this Mac.",
  },
  { id: "preapproved", label: "Do it, whatever it has read", tone: "b-ok", text: "Without asking, even after reading untrusted content." },
  { id: "ask", label: "Ask me first", tone: "b-warn", text: "Dot stops and asks every time; the run waits for your answer." },
  {
    id: "handoff",
    label: "Hand it to me",
    tone: "b-hand",
    text: "Dot says what it needs done and waits. In the browser you take over, do the step and give it back; when you say you did it, the run goes on from there.",
  },
];

function answerOf(id) {
  return ANSWERS.find((answer) => answer.id === id) ?? { id, label: id, tone: "b-muted", text: "" };
}

/** The sentence a rule reads as, up to the answer: "When Dot would use shell, matching \bgit push\b:". */
function sentence(toolGlob, matchRegex) {
  const parts = ["When Dot would use ", el("code", { class: "mono" }, toolGlob === "" ? "…" : toolGlob)];
  if (matchRegex !== "") {
    parts.push(", matching ", el("code", { class: "mono", title: matchRegex }, clip(matchRegex, PATTERN_CHARS)));
  }
  parts.push(":");
  return parts;
}

/** One of a rule's two arrows: up toward the first rule, down toward the last; at its end of the order it is off. */
function moveButton(record, index, total, direction, onMove) {
  const up = direction === "up";
  return el(
    "button",
    {
      class: `btn ghost sm icon-only rule-move ${up ? "up" : "down"}`,
      type: "button",
      title: up ? "Move this rule up: it is checked earlier" : "Move this rule down: it is checked later",
      "aria-label": `Move rule ${index + 1} ${direction}`,
      "data-rule": record.id,
      "data-move": direction,
      disabled: up ? index === 0 : index === total - 1,
      onclick: () => onMove(record, direction),
    },
    icon("chevron"),
  );
}

/** The note on a rule that another rule decides in place of wherever it fits (`never_decided_by`, item 449), or null:
 * an earlier rule, which moving this one above it undoes, or a rule that hands the step to you, which decides wherever
 * it stands (item 481), so that only narrowing or deleting one of them does. `behaviorOf(id)` is a rule's answer. */
function neverDecides(record, numberOf, behaviorOf) {
  if (record.never_decided_by === null) {
    return null;
  }
  const first = numberOf(record.never_decided_by);
  const title =
    behaviorOf(record.never_decided_by) === "handoff" && record.behavior !== "handoff"
      ? `Rule #${first} hands you every action this rule fits, and a rule that hands the step to you decides wherever it stands, so this rule never decides. Narrow one of them, or delete one.`
      : `Rule #${first} fits every action this rule fits and is checked first, so this rule never decides. Move it above rule #${first}, or delete one of them.`;
  return el("span", { class: "badge b-warn", title }, "Never decides");
}

/** A rule's row; `used` is the line of how it was used on the last days (item 473), or null until that is read and for a
 * rule switched off. */
function ruleRow(record, index, total, numberOf, behaviorOf, handlers, used) {
  const answer = answerOf(record.behavior);
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? "Switch off" : "Switch on"} rule ${index + 1}`,
    title: record.enabled ? "Switch this rule off" : "Switch this rule on",
    onclick: () => handlers.onToggle(record),
  });
  const remove = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Delete this rule",
      "aria-label": `Delete rule ${index + 1}`,
      onclick: () => handlers.onDelete(record),
    },
    icon("trash"),
  );
  return el(
    "div",
    { class: record.enabled ? "list-row" : "list-row faint" },
    toggle,
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row rule-sentence" },
        el("span", { class: "chip", title: "Rules are checked in this order" }, `#${index + 1}`),
        el("span", null, ...sentence(record.tool_glob, record.match_regex)),
        el("span", { class: `badge ${answer.tone}`, title: answer.text }, answer.label),
        neverDecides(record, numberOf, behaviorOf),
      ),
      record.description === "" ? null : el("div", { class: "row-text muted" }, record.description),
      used === null ? null : el("div", { class: "rule-usage faint small" }, used),
    ),
    el(
      "div",
      { class: "row-actions" },
      moveButton(record, index, total, "up", handlers.onMove),
      moveButton(record, index, total, "down", handlers.onMove),
      remove,
    ),
  );
}

/** What no rule lifts, in a card of its own at the head of the page: four checked lines and what a yolo run does. */
function alwaysOn() {
  const lines = [
    "Reading secrets and sending them out is refused.",
    ["Research runs stay read-only. ", el("span", { class: "muted" }, "Yolo keeps this one too.")],
    "Money and credentials are handed to you.",
    "A message or post in your name waits for your yes.",
  ];
  // One child in the card: a card's children are parted by hairlines, and these are one block.
  return el(
    "section",
    { class: "set-card rule-always", "aria-labelledby": "rule-always-title" },
    el(
      "div",
      null,
      el(
        "div",
        { class: "rule-always-head" },
        el("span", { class: "rule-tile", "aria-hidden": "true" }, icon("lock")),
        el("h2", { class: "rule-always-title", id: "rule-always-title" }, "Always on, whatever your rules say"),
      ),
      el(
        "ul",
        { class: "rule-always-list" },
        lines.map((line) => el("li", null, icon("check"), el("span", null, line))),
      ),
      el(
        "p",
        { class: "rule-always-note" },
        // What a yolo run keeps besides (item 559): what an untrusted read leaves it is said, not only what it waives.
        "A yolo run asks about none of the others: it does the step and records why it would have asked. One that has read untrusted content still has a secret refused on the way out, and a step that content may be after judged by the review model.",
      ),
    ),
  );
}

/** What a rule can answer: each of the four answers as its badge with what Dot does then; it stands under the rules, with
 * or without any. */
function legend() {
  return el(
    "div",
    { class: "rule-legend" },
    el("h3", { class: "rule-legend-title" }, "What a rule can answer"),
    el(
      "dl",
      { class: "rule-legend-list" },
      ANSWERS.map((answer) => el("div", { class: "rule-legend-item" }, el("dt", null, el("span", { class: `badge ${answer.tone}` }, answer.label)), el("dd", null, answer.text))),
    ),
  );
}

/** The card's place while there is no rule: the shield, what the preset does meanwhile and the sentences to start from;
 * `onPick(text)` opens the ways to write a rule with one in the box. */
function emptyState(onPick) {
  return el(
    "div",
    { class: "rule-empty" },
    emptyPanel("shield", "No rules yet", "The safety preset decides every action. Write a rule in your own words, or start from one of these:"),
    el(
      "div",
      { class: "rule-examples", role: "group", "aria-label": "Rules to start from" },
      EXAMPLES.map((text) => el("button", { class: "rule-example", type: "button", onclick: () => onPick(text) }, text)),
    ),
  );
}

/** The rule as its parts: a sentence that reads as the rule will, the fields, what the rule would have done on the steps
 * of the last days, and the buttons. Returns the row and `fill(rule)`, which puts a drafted rule in the fields.
 * `onCreated()` runs once the rule is added, `onClose()` shuts the ways to write a rule. */
function buildForm(onCreated, onClose) {
  const glob = el("input", {
    class: "input mono",
    type: "text",
    maxlength: "120",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "shell",
    "aria-label": "Tool",
  });
  const regex = el("input", {
    class: "input mono",
    type: "text",
    maxlength: "500",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "\\bgit (status|diff|log)\\b",
    "aria-label": "Pattern",
  });
  const description = el("input", {
    class: "input",
    type: "text",
    maxlength: "300",
    autocomplete: "off",
    placeholder: "Reading the state of a repository is harmless",
    "aria-label": "Why",
  });
  const preview = el("span", { class: "rule-preview" });
  const answerHint = el("span", { class: "hint" }, ANSWERS[0].text);
  const answers = segmented(
    ANSWERS.map(({ id, label, text }) => ({ id, label, hint: text })),
    ANSWERS[0].id,
    () => {
      paintPreview();
      putAwayTryOut();
    },
  );
  answers.element.classList.add("answers");
  answers.element.setAttribute("aria-label", "Then");

  function paintPreview() {
    const answer = answerOf(answers.value());
    answerHint.textContent = answer.text;
    preview.replaceChildren(...sentence(glob.value.trim(), regex.value.trim()), " ", el("em", null, answer.label.toLowerCase()), ".");
  }

  // What the rule would have done on the steps of the last days (item 469); a change of the rule puts it away.
  const tryOutButton = el("button", { class: "btn", type: "button" }, `Test on the last ${TRY_OUT_DAYS} days`);
  const tried = el("div", { class: "rule-tryout stack tight", role: "status", hidden: true });
  // Which try-out may still show: one that answers after the rule changed is left unshown.
  let tryOutTicket = 0;

  function putAwayTryOut() {
    tryOutTicket += 1;
    tried.hidden = true;
    tried.replaceChildren();
  }

  glob.addEventListener("input", () => {
    paintPreview();
    putAwayTryOut();
  });
  regex.addEventListener("input", () => {
    paintPreview();
    putAwayTryOut();
  });
  const create = el("button", { class: "btn primary", type: "button" }, icon("plus"), "Add rule");

  async function tryOut() {
    const globText = glob.value.trim();
    if (globText === "") {
      glob.focus();
      return;
    }
    tryOutTicket += 1;
    const ticket = tryOutTicket;
    tryOutButton.disabled = true;
    try {
      const view = await api.post("/rules/preview", {
        behavior: answers.value(),
        tool_glob: globText,
        match_regex: regex.value.trim(),
        days: TRY_OUT_DAYS,
      });
      if (ticket !== tryOutTicket) {
        return;
      }
      // replaceChildren writes a null as the word: what is not there is left out first.
      const parts = [
        ...previewLines(view, TRY_OUT_DAYS).map((line) => el("p", { class: "small" }, line)),
        view.problem === null ? null : el("p", { class: "small rule-tryout-problem" }, view.problem),
        view.examples.length === 0
          ? null
          : el(
              "ul",
              { class: "firings-list" },
              view.examples.map((example) =>
                el(
                  "li",
                  { class: "firing" },
                  el(
                    "div",
                    { class: "row-meta" },
                    el("span", { class: "faint" }, ago(example.at)),
                    el("a", { class: "firing-title", href: `#/runs/${example.run_id}` }, `${example.tool}: ${example.summary}`),
                    el("span", { class: "faint" }, outcomeLabel(example.was, example.ok, example.recorded)),
                  ),
                ),
              ),
            ),
      ];
      tried.replaceChildren(...parts.filter((part) => part !== null));
      tried.hidden = false;
    } finally {
      tryOutButton.disabled = false;
    }
  }

  tryOutButton.addEventListener("click", () => attempt(tryOut));

  async function submit() {
    const globText = glob.value.trim();
    if (globText === "") {
      glob.focus();
      return;
    }
    create.disabled = true;
    try {
      await api.post("/rules", {
        behavior: answers.value(),
        tool_glob: globText,
        match_regex: regex.value.trim(),
        description: description.value.trim(),
      });
      glob.value = "";
      regex.value = "";
      description.value = "";
      paintPreview();
      putAwayTryOut();
      toast("ok", "Rule added at the end of the list.");
      await onCreated();
    } finally {
      create.disabled = false;
    }
  }

  // A drafted rule fills the fields, to be checked and added.
  function fill(rule) {
    glob.value = rule.tool_glob;
    regex.value = rule.match_regex;
    description.value = rule.description;
    answers.set(rule.behavior);
    paintPreview();
    element.scrollIntoView({ block: "nearest" });
    create.focus({ preventScroll: true });
    // A drafted rule was written without looking at what it would catch: it is tried at once.
    attempt(tryOut);
  }

  create.addEventListener("click", () => attempt(submit));
  paintPreview();
  // The row's title is the sentence the rule reads as, written as the fields are filled.
  const element = setRow({
    title: el("div", { class: "rule-sentence rule-sentence-preview" }, icon("shield"), preview),
    body: el(
      "div",
      { class: "stack rule-body" },
      el(
        "div",
        { class: "form-grid" },
        field("When Dot would use", glob, "A tool name or a pattern: shell, write_file, browser_*, desktop_*, mcp__*"),
        field(
          "matching",
          regex,
          "Optional regular expression, case-insensitive, searched in the action's summary (a shell command's starts with “Run:”) and its target (the command, path or address). Empty means every time.",
        ),
        el("div", { class: "field wide" }, el("span", { class: "field-label" }, "Then"), answers.element, answerHint),
        field("Why", description, "Optional. Shown when the rule decides."),
      ),
      tried,
      el(
        "div",
        { class: "rule-form-buttons" },
        el("button", { class: "btn ghost", type: "button", onclick: onClose }, "Close"),
        el("div", { class: "rule-form-main" }, tryOutButton, create),
      ),
    ),
  });
  return { element, fill };
}

/**
 * The rule in your own words: a sentence, the examples to start from and the Draft button. `onDrafted(rule)` gets the
 * daemon's draft (`behavior`, `tool_glob`, `match_regex`, and `description`, the sentence it reads back as). Returns
 * the row, `start(text)`, which puts a sentence in the box for a change before it is drafted, `focus()`, `reset()`, which
 * empties the box and what the last draft said, once the rule is added, and `showExamples(shown)`: the examples are
 * the card of the rules' own while there is no rule, and the form's own once there is one.
 */
function buildWords(onDrafted, isDisposed) {
  const words = el("textarea", {
    class: "textarea rule-words",
    rows: "2",
    maxlength: "500",
    placeholder: "For example: let Dot run npm test without asking me",
    "aria-label": "The rule in your words",
  });
  const draft = el("button", { class: "btn primary", type: "button" }, icon("sparkle"), "Draft the rule");
  const outcome = el("div", { class: "rule-outcome", role: "status", hidden: true });
  const examples = el(
    "div",
    { class: "rule-examples", role: "group", "aria-label": "Examples" },
    EXAMPLES.map((text) =>
      el(
        "button",
        {
          class: "rule-example",
          type: "button",
          onclick: () => {
            words.value = text;
            words.focus();
          },
        },
        text,
      ),
    ),
  );

  function show(tone, ...parts) {
    outcome.className = `rule-outcome ${tone}`;
    outcome.replaceChildren(...parts);
    outcome.hidden = false;
  }

  // True while a rule is being drafted: ⌘Enter does not look at the disabled button, so a second press is refused
  // here, or one sentence would cost two model calls.
  let drafting = false;

  async function run() {
    if (drafting) {
      return;
    }
    const text = words.value.trim();
    if (text === "") {
      words.focus();
      return;
    }
    drafting = true;
    draft.disabled = true;
    show("busy", el("span", { class: "spinner" }), el("span", null, "Drafting the rule…"));
    let drafted;
    try {
      drafted = await api.post("/rules/draft", { sentence: text });
    } catch (error) {
      if (!isDisposed()) {
        // 422: the words make no rule, and the model says why; anything else is a failure to draft.
        show("bad", icon("alert"), el("span", null, error.status === 422 ? error.message : `The rule could not be drafted: ${error.message}`));
      }
      return;
    } finally {
      drafting = false;
      draft.disabled = false;
    }
    if (isDisposed()) {
      return;
    }
    show("ok", icon("check"), el("span", null, el("span", { class: "rule-readback" }, drafted.description), " Check it below, then add it."));
    onDrafted(drafted);
  }

  draft.addEventListener("click", () => attempt(run));
  words.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      attempt(run);
    }
  });
  const element = setRow({
    title: "Say the rule in your words",
    lead: "Dot drafts the rule from it into the form below; nothing applies until you add it.",
    body: el("div", { class: "stack rule-body" }, words, examples, el("div", { class: "rule-words-foot" }, outcome, draft)),
  });
  // The box takes the focus with the caret after the words it has.
  function focus() {
    words.focus();
    words.setSelectionRange(words.value.length, words.value.length);
  }

  return {
    element,
    start: (text) => {
      words.value = text.slice(0, 500);
      focus();
    },
    focus,
    reset: () => {
      words.value = "";
      outcome.hidden = true;
      outcome.replaceChildren();
    },
    showExamples: (shown) => {
      examples.hidden = !shown;
    },
  };
}

/** The macOS sandbox of a run's commands (item 262), switched here while the daemon runs; the config file keeps it. */
function buildSandbox(isDisposed) {
  let view = null;
  const setting = switchRowSetting({
    label: "Sandbox for a run's commands",
    hint: [
      "On, the shell commands, terminal sessions, AppleScript and searches of a run that is not yolo start in a macOS sandbox: they can neither read nor change Dot's own files (its keys, token, settings, database and browser profiles) nor change what Dot loads and runs, and macOS starts no program that runs as root there (ps, top, sudo). A yolo run is never sandboxed.",
      el(
        "span",
        { class: "rule-fine" },
        "Off, they run with your account's rights, and the shell guard still reads every line. Takes effect from the next command, without a restart; kept in the config file as ",
        el("code", { class: "mono" }, "[shell] sandbox"),
        ".",
      ),
    ],
    read: () => view?.sandbox === true,
    write: async (on) => {
      view = await api.put("/shell/sandbox", { sandbox: on });
      toast("ok", on ? "From the next command on, a run that is not yolo works in the sandbox." : "From the next command on, every run works without the sandbox.");
    },
    onToggled: () => {
      if (!isDisposed()) {
        setting.paint();
      }
    },
  });
  setting.toggle.disabled = true;

  async function load() {
    try {
      const found = await api.get("/shell/sandbox");
      if (isDisposed()) {
        return;
      }
      view = found;
      setting.toggle.disabled = false;
      setting.paint();
    } catch (error) {
      if (!isDisposed()) {
        readFailed("The sandbox", error);
      }
    }
  }

  return { element: el("section", { class: "set-card", "aria-label": "Sandbox" }, setting.element), load };
}

export function mount(root, ctx) {
  let disposed = false;
  let rules = null;
  // How the rules were used on the last days (item 473), read after the rules; an answer older than the last read is
  // left unshown.
  let usage = null;
  let usageTicket = 0;
  const holder = el("div", { class: "rule-holder" }, inlineLoading());

  function paint() {
    if (rules === null) {
      return;
    }
    words.showExamples(rules.length > 0);
    // What the page holds, at the head's right: how many rules there are.
    ctx.head.setMeta(...(rules.length === 0 ? [] : [plural(rules.length, "rule", "rules")]));
    if (rules.length === 0) {
      holder.replaceChildren(emptyState(startFrom));
      return;
    }
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
      onMove: (record, direction) => attempt(() => move(record, direction)),
    };
    const numberOf = (id) => rules.findIndex((record) => record.id === id) + 1;
    const behaviorOf = (id) => rules.find((record) => record.id === id)?.behavior ?? null;
    const usedBy = new Map((usage?.rules ?? []).map((used) => [used.rule_id, used]));
    const lineOf = (record) => {
      const used = usedBy.get(record.id);
      return used === undefined ? null : usageLine(used, usage, TRY_OUT_DAYS, numberOf);
    };
    const problem = usage === null || usage.problem === null ? null : el("p", { class: "small rule-tryout-problem rule-problem" }, usage.problem);
    // A rule's arrow keeps the focus through every drawing of the list, whichever of the reads after a move comes last.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && holder.contains(active) ? active.dataset : null;
    // replaceChildren writes a null as the word: what is not there is left out first.
    holder.replaceChildren(
      ...[problem, el("div", { class: "rule-list" }, rules.map((record, index) => ruleRow(record, index, rules.length, numberOf, behaviorOf, handlers, lineOf(record))))].filter(
        (part) => part !== null,
      ),
    );
    if (focused?.rule !== undefined) {
      focusArrow(focused.rule, focused.move);
    }
  }

  async function loadUsage() {
    usageTicket += 1;
    const ticket = usageTicket;
    const read = await api.get(`/rules/usage?days=${TRY_OUT_DAYS}`);
    if (disposed || ticket !== usageTicket) {
      return;
    }
    usage = read;
    paint();
  }

  async function reload() {
    try {
      const fresh = await api.get("/rules");
      if (disposed) {
        return;
      }
      rules = fresh;
      // What the rules did was counted with the rules as they were: it is read again for these.
      usage = null;
      paint();
      attempt(loadUsage);
    } catch (error) {
      if (disposed) {
        return;
      }
      if (rules === null) {
        holder.replaceChildren(inlineError(error, () => attempt(retry)));
      } else {
        readFailed("The rules", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(inlineLoading());
    await reload();
  }

  async function toggle(record) {
    await api.patch(`/rules/${record.id}`, { enabled: !record.enabled });
    await reload();
  }

  /** The arrow pressed keeps the focus as its rule moves; once the rule has reached an end, the other arrow takes it. */
  function focusArrow(id, direction) {
    const arrows = [...holder.querySelectorAll(`[data-rule="${CSS.escape(id)}"]`)];
    const pressed = arrows.find((arrow) => arrow.dataset.move === direction);
    (pressed !== undefined && !pressed.disabled ? pressed : arrows.find((arrow) => !arrow.disabled))?.focus();
  }

  async function move(record, direction) {
    await api.post(`/rules/${record.id}/move`, { direction });
    await reload();
    // A click does not focus a button in WebKit (Safari, the desktop shell), so the arrow is focused here too.
    focusArrow(record.id, direction);
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: "Delete this rule?",
      message: `“${answerOf(record.behavior).label}” for “${clip(record.tool_glob, QUOTE_CHARS)}” stops applying.`,
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/rules/${record.id}`);
    toast("ok", "Rule deleted.");
    await reload();
  }

  const addButton = el("button", { class: "btn primary", type: "button" });
  paintOpener(addButton, "New rule", false);
  // The two ways to write a rule stay away until New rule asks for them: the list is what this page is for (item 373).
  // Shut by the form's own Close, they give the focus back to the button that opened them.
  const closeForms = () => {
    setOpen(false);
    addButton.focus({ preventScroll: true });
  };
  const form = buildForm(async () => {
    words.reset();
    await reload();
  }, closeForms);
  const words = buildWords((drafted) => form.fill(drafted), () => disposed);
  const sandbox = buildSandbox(() => disposed);
  const forms = setSection({ title: "New rule", body: setCard(words.element, form.element) });
  forms.classList.add("rule-forms");
  forms.hidden = true;

  function setOpen(open) {
    forms.hidden = !open;
    paintOpener(addButton, "New rule", open);
  }

  addButton.addEventListener("click", () => {
    setOpen(forms.hidden);
    if (!forms.hidden) {
      forms.scrollIntoView({ block: "nearest" });
      // What was written before Close is still there.
      words.focus();
    }
  });

  /** A sentence to start from, chosen in the empty card: the ways to write a rule open with it in the box. */
  function startFrom(text) {
    setOpen(true);
    forms.scrollIntoView({ block: "nearest" });
    words.start(text);
  }

  ctx.head.setLead("Your own answers to “may Dot do this?”, checked before the safety preset.");
  ctx.head.setActions(addButton);
  root.append(
    el(
      "div",
      { class: "set-sections" },
      forms,
      alwaysOn(),
      setSection({ title: "Commands", body: sandbox.element }),
      setSection({
        title: "Your rules",
        lead: "Checked top to bottom: the first enabled rule that fits decides. A rule that hands the step to you decides wherever it stands. A new rule goes to the end, and a rule's arrows move it up or down.",
        body: el("section", { class: "set-card rule-panel", "aria-label": "Your rules" }, holder, legend()),
      }),
    ),
  );
  // The sentence an approval card sends ("Make a rule of this"), in the address after the tab's name.
  const asked = new URLSearchParams(location.hash.split("?")[1] ?? "").get("sentence");
  if (asked !== null && asked.trim() !== "") {
    // A card's "Make a rule of this" opens the ways to write one with its sentence already in the box (item 373).
    setOpen(true);
    words.start(asked.trim());
  }
  // A rule added, switched or deleted on another page is read again here, as are the rules once the stream is back.
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === RULES_CHANGED) {
      reload();
    }
  });
  const stopBack = onStreamBack(reload);
  attempt(reload);
  attempt(sandbox.load);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
  };
}
