// Rules: your own answers to "may Dot do this?", checked in order before the safety preset decides. A rule reads as a
// sentence: "When Dot would use {tool}, matching {pattern}: {answer}", and the answer is one of four.

import { api } from "../api.js";
import { clip, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, paneHead, segmented } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const QUOTE_CHARS = 100;
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

/** The sentence a rule reads as, up to the answer: "When Dot would use shell, matching ^git push:". */
function sentence(toolGlob, matchRegex) {
  const parts = ["When Dot would use ", el("code", { class: "mono" }, toolGlob === "" ? "…" : toolGlob)];
  if (matchRegex !== "") {
    parts.push(", matching ", el("code", { class: "mono" }, matchRegex));
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
    placeholder: "^git (status|diff|log)",
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
        "Optional regular expression, case-insensitive, searched in a one-line summary of the action and its target. Empty means every time.",
      ),
      el("div", { class: "field wide" }, el("span", { class: "field-label" }, "Then"), answers.element, answerHint),
      field("Why", description, "Optional. Shown when the rule decides."),
    ),
    el("div", { class: "row" }, el("span", { class: "spacer" }), create),
  );
  return { element };
}

export function mount(root, ctx) {
  let disposed = false;
  let rules = null;
  const holder = el("div", null, loadingPanel());

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
    holder.replaceChildren(el("div", { class: "card" }, rules.map((record, index) => ruleRow(record, index, handlers))));
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
        console.warn("the rules could not be refreshed", error);
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

  const form = buildForm(reload);
  root.append(
    paneHead("Your own answers to “may Dot do this?”, applied before the safety preset.", null),
    el(
      "div",
      { class: "note" },
      icon("shield"),
      el(
        "span",
        null,
        "The first enabled rule that matches an action decides it. Rules cannot lift the protections that are always on: reading secrets and sending them out is refused, research runs stay read-only, and money or credentials are handed to you. A yolo run goes past all of these but the read-only research runs, and a hand-off rule still stops it there too.",
      ),
    ),
    form.element,
    el("div", { class: "section-label" }, "Checked in this order"),
    holder,
  );
  attempt(reload);
  return () => {
    disposed = true;
  };
}
