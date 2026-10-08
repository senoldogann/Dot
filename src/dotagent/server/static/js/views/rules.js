// Rules: your own answers to "may Dot do this?", checked in order before the safety preset decides.

import { api } from "../api.js";
import { clip, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, pageHead } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const QUOTE_CHARS = 100;
const BEHAVIORS = [
  {
    id: "allow",
    label: "Allow",
    tone: "b-ok",
    text: "Run without asking. It still asks when untrusted content is in context and the action reaches outside this Mac.",
  },
  { id: "preapproved", label: "Pre-approved", tone: "b-ok", text: "Run without asking, whatever is in context." },
  { id: "ask", label: "Ask", tone: "b-warn", text: "Always ask you first." },
  { id: "handoff", label: "Hand off", tone: "b-info", text: "Stop and hand the action over to you instead of doing it." },
];

function behaviorOf(id) {
  return BEHAVIORS.find((behavior) => behavior.id === id) ?? { id, label: id, tone: "b-muted", text: "" };
}

function ruleRow(record, index, handlers) {
  const behavior = behaviorOf(record.behavior);
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
  const pattern =
    record.match_regex === ""
      ? el("span", null, "every time")
      : el("span", null, "when the action matches ", el("code", { class: "mono" }, record.match_regex));
  return el(
    "div",
    { class: record.enabled ? "list-row" : "list-row faint" },
    toggle,
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row" },
        el("span", { class: "chip", title: "Rules are checked in this order" }, `#${index + 1}`),
        el("span", { class: `badge ${behavior.tone}` }, behavior.label),
        el("code", { class: "mono" }, record.tool_glob),
      ),
      el("div", { class: "row-meta" }, pattern),
      record.description === "" ? null : el("div", { class: "row-text muted" }, record.description),
    ),
    el("div", { class: "row-actions" }, remove),
  );
}

function buildForm(onCreated) {
  const behavior = el(
    "select",
    { class: "select", "aria-label": "Then" },
    BEHAVIORS.map((option) => el("option", { value: option.id }, option.label)),
  );
  const behaviorHint = el("span", { class: "hint" }, BEHAVIORS[0].text);
  behavior.addEventListener("change", () => {
    behaviorHint.textContent = behaviorOf(behavior.value).text;
  });
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
        behavior: behavior.value,
        tool_glob: globText,
        match_regex: regex.value.trim(),
        description: description.value.trim(),
      });
      glob.value = "";
      regex.value = "";
      description.value = "";
      toast("ok", "Rule added at the end of the list.");
      await onCreated();
    } finally {
      create.disabled = false;
    }
  }

  create.addEventListener("click", () => attempt(submit));
  const element = el(
    "section",
    { class: "card card-pad stack" },
    el(
      "div",
      { class: "form-grid" },
      el("label", { class: "field" }, el("span", { class: "field-label" }, "Then"), behavior, behaviorHint),
      field("For the tool", glob, "A name or a pattern: shell, write_file, browser_*, desktop_*, mcp__*"),
      field(
        "When the action matches",
        regex,
        "Optional regular expression, case-insensitive, searched in a one-line summary of the action and its target. Empty means every time.",
      ),
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
      holder.replaceChildren(emptyPanel("shield", "No rules yet", "Without rules the safety preset decides every action."));
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
      message: `The ${record.behavior} rule for “${clip(record.tool_glob, QUOTE_CHARS)}” stops applying.`,
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
    pageHead("Rules", "Your own answers to “may Dot do this?”, applied before the safety preset.", null),
    el(
      "div",
      { class: "note" },
      icon("shield"),
      el(
        "span",
        null,
        "The first enabled rule that matches an action decides it. Rules cannot lift the protections that are always on: reading secrets and sending them out is refused, research runs stay read-only, and money or credentials are always handed to you.",
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
