// Rules: your own answers to "may Dot do this?", checked in order before the safety preset decides. A rule reads as a
// sentence: "When Dot would use {tool}, matching {pattern}: {answer}", and the answer is one of four. A rule can be
// said in your own words (item 110): the review model drafts its parts, the daemon writes the pattern from them, and
// the draft fills the form, its description the sentence the rule reads back as, to be checked and added;
// `#/settings/rules?sentence=…` (an approval card's "Make a rule of this") puts a sentence in the box. Above the rules,
// the switch of the macOS sandbox of a run's commands (item 262).

import { api } from "../api.js";
import { clip, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, paneHead, segmented, switchSetting } from "../panels.js";
import { onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const QUOTE_CHARS = 100;
// The live event of a rule added, switched or deleted on any page (`rules_changed`, `dotagent.server.routes_data`).
const RULES_CHANGED = "rules_changed";
// A pattern longer than this is cut in the sentence a rule reads as; the whole of it is its tooltip and in the form.
const PATTERN_CHARS = 40;
// Sentences to start from: a click puts one in the box.
const EXAMPLES = [
  "Let Dot run git status and git diff without asking",
  "Ask me before Dot deletes anything with rm",
  "Hand me every step on mybank.com",
];
// The four answers, in the order of the control. `label` is the answer as it reads in the sentence; `text` says what
// Dot does then.
const ANSWERS = [
  {
    id: "allow",
    label: "Do it",
    tone: "b-ok",
    text: "Dot does it without asking. When untrusted content is in context and the action reaches outside this Mac, it still asks you.",
  },
  {
    id: "preapproved",
    label: "Do it, whatever it has read",
    tone: "b-ok",
    text: "Dot does it without asking, even after reading untrusted content.",
  },
  { id: "ask", label: "Ask me first", tone: "b-warn", text: "Dot stops and asks you every time; the run waits for your answer." },
  {
    id: "handoff",
    label: "Hand it to me",
    tone: "b-info",
    text: "Dot stops, tells you what it needs done and waits. In the browser you can take over, do the step and give the browser back; when you say you did it, the run goes on from there.",
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

function ruleRow(record, index, handlers) {
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
      ),
      record.description === "" ? null : el("div", { class: "row-text muted" }, record.description),
    ),
    el("div", { class: "row-actions" }, remove),
  );
}

function buildForm(onCreated) {
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
    () => paintPreview(),
  );
  answers.element.classList.add("answers");
  answers.element.setAttribute("aria-label", "Then");

  function paintPreview() {
    const answer = answerOf(answers.value());
    answerHint.textContent = answer.text;
    preview.replaceChildren(...sentence(glob.value.trim(), regex.value.trim()), " ", el("em", null, answer.label.toLowerCase()), ".");
  }

  glob.addEventListener("input", paintPreview);
  regex.addEventListener("input", paintPreview);
  const create = el("button", { class: "btn primary", type: "button" }, icon("plus"), "Add rule");

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
  }

  create.addEventListener("click", () => attempt(submit));
  paintPreview();
  const element = el(
    "section",
    { class: "card card-pad stack" },
    el("div", { class: "rule-sentence rule-sentence-preview" }, icon("shield"), preview),
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
    el("div", { class: "row" }, el("span", { class: "spacer" }), create),
  );
  return { element, fill };
}

/**
 * The rule in your own words: a sentence, the examples to start from and the Draft button. `onDrafted(rule)` gets the
 * daemon's draft (`behavior`, `tool_glob`, `match_regex`, and `description`, the sentence it reads back as). Returns
 * the element, `start(text)`, which puts a sentence in the box for a change before it is drafted, and `reset()`, which
 * empties the box and what the last draft said, once the rule is added.
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
  const element = el(
    "section",
    { class: "card card-pad stack" },
    field("Say the rule in your words", words, "Dot drafts the rule from it into the form below; nothing applies until you add it."),
    examples,
    el("div", { class: "rule-words-foot" }, outcome, draft),
  );
  return {
    element,
    start: (text) => {
      words.value = text.slice(0, 500);
      words.focus();
      words.setSelectionRange(words.value.length, words.value.length);
    },
    reset: () => {
      words.value = "";
      outcome.hidden = true;
      outcome.replaceChildren();
    },
  };
}

/** The macOS sandbox of a run's commands (item 262), switched here while the daemon runs; the config file keeps it. */
function buildSandbox(isDisposed) {
  let view = null;
  const setting = switchSetting({
    label: "Sandbox for a run's commands",
    hint:
      "On, the shell commands, terminal sessions, AppleScript and searches of a run that is not yolo start in a macOS sandbox: they can neither read nor change Dot's own files (its keys, token, settings, database and browser profiles) nor change what Dot loads and runs, and macOS starts no program that runs as root there (ps, top, sudo). Off, they run with your account's rights, and the shell guard still reads every line. A yolo run is never sandboxed. Takes effect from the next command, without a restart; kept in the config file as [shell] sandbox.",
    read: () => view !== null && view.sandbox,
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

  return { element: el("section", { class: "card card-pad", "aria-label": "Sandbox" }, setting.element), load };
}

export function mount(root, ctx) {
  let disposed = false;
  let rules = null;
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "hint" });

  function paint() {
    if (rules === null) {
      return;
    }
    if (rules.length === 0) {
      holder.replaceChildren(emptyPanel("No rules yet", "Without rules the safety preset decides every action."));
      return;
    }
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
    };
    holder.replaceChildren(el("div", { class: "rule-list" }, rules.map((record, index) => ruleRow(record, index, handlers))));
    count.textContent = plural(rules.length, "rule", "rules");
  }

  async function reload() {
    try {
      const fresh = await api.get("/rules");
      if (disposed) {
        return;
      }
      rules = fresh;
      paint();
    } catch (error) {
      if (disposed) {
        return;
      }
      if (rules === null) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      } else {
        readFailed("The rules", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  async function toggle(record) {
    await api.patch(`/rules/${record.id}`, { enabled: !record.enabled });
    await reload();
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

  const form = buildForm(async () => {
    words.reset();
    await reload();
  });
  const words = buildWords((drafted) => form.fill(drafted), () => disposed);
  const sandbox = buildSandbox(() => disposed);
  const addButton = el("button", { class: "btn primary sm", type: "button" }, icon("plus"), "New rule");
  // The two ways to write a rule stay away until Add rule asks for them: the list is what this page is for (item 373).
  const forms = el("div", { class: "rule-forms", hidden: true }, words.element, form.element);
  const closeForms = () => {
    forms.hidden = true;
    addButton.lastChild.textContent = "New rule";
  };
  forms.append(
    el(
      "div",
      { class: "panel-foot rule-forms-foot" },
      el("span", { class: "spacer" }),
      el("button", { class: "btn sm", type: "button", onclick: closeForms }, "Close"),
    ),
  );
  addButton.addEventListener("click", () => {
    forms.hidden = !forms.hidden;
    addButton.lastChild.textContent = forms.hidden ? "New rule" : "Close";
    if (!forms.hidden) {
      forms.scrollIntoView({ block: "nearest" });
      words.start("");
    }
  });
  root.append(
    paneHead("Your own answers to “may Dot do this?”, applied before the safety preset.", null),
    el(
      "div",
      { class: "note" },
      icon("shield"),
      el(
        "span",
        null,
        "Rules cannot lift the protections that are always on: reading secrets and sending them out is refused, research runs stay read-only, and money or credentials are handed to you. A yolo run goes past all of these but the read-only research runs, and a hand-off rule still stops it there too.",
      ),
    ),
    sandbox.element,
    forms,
    el(
      "section",
      { class: "memory-panel notes-panel rule-panel" },
      el(
        "div",
        { class: "panel-head" },
        el("h2", { class: "panel-title" }, "Checked in this order"),
        count,
        el("span", { class: "spacer" }),
        addButton,
      ),
      el("div", { class: "panel-note" }, "The first enabled rule that fits an action decides it; a new rule goes to the end."),
      holder,
    ),
  );
  // The sentence an approval card sends ("Make a rule of this"), in the address after the tab's name.
  const asked = new URLSearchParams(location.hash.split("?")[1] ?? "").get("sentence");
  if (asked !== null && asked.trim() !== "") {
    // A card's "Make a rule of this" opens the ways to write one with its sentence already in the box (item 373).
    forms.hidden = false;
    addButton.lastChild.textContent = "Close";
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
