// Settings › Hooks (docs/hooks.md): addresses a service calls, signed with the hook's own secret, to start a scheduled
// task or an agent. Each hook is a card: its address, what it starts, a switch, Rotate secret and Delete, and its last
// calls (when, the status, why, and the run it started). New hook makes one in a dialog; the address and the secret are
// shown once, in a card at the top of the page, after a hook is made or its secret is rotated: no answer of the daemon
// carries the secret again.

import { api } from "../api.js";
import { copyButton } from "../copybutton.js";
import { dateTime, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, markOpener, setCard, setRow, setSection, switchToggle } from "../panels.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";

const DEFAULT_PER_MINUTE = 10;

/** The address a service calls, as this page is reached. */
function addressOf(hook) {
  return `${location.origin}${hook.path}`;
}

/** The fields typed in one box, split at commas and spaces. */
function fieldsOf(text) {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

export function mount(root, ctx) {
  let disposed = false;
  let hooks = [];
  let schedules = [];
  let agents = [];
  // The hook whose secret was just made or rotated, with it, until the owner closes its card.
  let fresh = null;
  const opener = el("button", { class: "btn primary sm", type: "button", onclick: () => openNew() });
  markOpener(opener, "New hook");
  const holder = el("div", { class: "stack" }, loadingPanel());

  function targetName(hook) {
    if (hook.schedule_id !== null) {
      const found = schedules.find((view) => view.record.id === hook.schedule_id);
      return `Scheduled task: ${found === undefined ? hook.schedule_id : found.record.name}`;
    }
    const found = agents.find((view) => view.record.id === hook.agent_id);
    return `Agent: ${found === undefined ? hook.agent_id : found.record.name}`;
  }

  function freshCard() {
    if (fresh === null) {
      return null;
    }
    const address = addressOf(fresh.hook);
    const done = el(
      "button",
      {
        class: "btn sm",
        type: "button",
        onclick: () => {
          fresh = null;
          paint();
        },
      },
      "I copied it",
    );
    return setSection({
      title: `The secret of ${fresh.hook.name}`,
      lead: "Copy it now: it is shown this once. The caller signs each call with it (see the docs: Hooks).",
      actions: done,
      body: setCard(
        setRow({ title: "Address", lead: el("code", { class: "hook-value" }, address), control: copyButton(address, "Copy the address") }),
        setRow({ title: "Secret", lead: el("code", { class: "hook-value hook-secret" }, fresh.secret), control: copyButton(fresh.secret, "Copy the secret") }),
      ),
    });
  }

  function callList(calls, label) {
    return el(
      "ul",
      { class: "hook-calls", "aria-label": label },
      calls.map((call) =>
        el(
          "li",
          { class: "hook-call" },
          el("span", { class: "hook-call-time" }, dateTime(call.at)),
          el("span", { class: call.status === 202 ? "hook-call-status ok" : "hook-call-status bad" }, String(call.status)),
          el("span", { class: "hook-call-reason" }, call.reason),
          call.run_id === null ? null : el("a", { class: "hook-call-run", href: `#/runs/${call.run_id}` }, "Open run"),
        ),
      ),
    );
  }

  /** The signed calls, then the calls refused before their signature matched (kept until Dot restarts). */
  function callRows(hook) {
    return el(
      "div",
      { class: "hook-call-lists" },
      hook.calls.length === 0 ? el("p", { class: "hint hook-calls-empty" }, "No signed call yet.") : callList(hook.calls, `Last calls of ${hook.name}`),
      hook.refused.length === 0
        ? null
        : el(
            "div",
            { class: "hook-refused" },
            el("p", { class: "hint" }, "Refused lately: not signed with its secret, outside the 5 minutes, or made before. Kept until Dot restarts."),
            callList(hook.refused, `Refused calls of ${hook.name}`),
          ),
    );
  }

  function hookSection(hook) {
    const toggle = switchToggle({
      label: `${hook.name} on`,
      read: () => hook.enabled,
      write: async (on) => {
        const changed = await api.post(`/hooks/${hook.id}/switch`, { enabled: on });
        hooks = hooks.map((kept) => (kept.id === hook.id ? changed : kept));
      },
      onToggled: () => {
        if (!disposed) {
          paint();
        }
      },
    });
    toggle.setAttribute("aria-checked", String(hook.enabled));
    const rotate = el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => rotateSecret(hook)) }, icon("refresh"), "Rotate secret");
    const remove = el("button", { class: "btn sm danger", type: "button", onclick: () => attempt(() => removeHook(hook)) }, icon("trash"), "Delete");
    const address = addressOf(hook);
    return setSection({
      title: hook.name,
      lead: targetName(hook),
      actions: el("div", { class: "row hook-actions" }, rotate, remove),
      body: setCard(
        setRow({ title: hook.enabled ? "On" : "Off", lead: hook.enabled ? "Signed calls start its work." : "Its calls are refused.", control: toggle }),
        setRow({ title: "Address", lead: el("code", { class: "hook-value" }, address), control: copyButton(address, "Copy the address") }),
        setRow({
          title: "What the run is given",
          lead: [hook.template === "" ? "The scheduled task's own goal." : hook.template, hook.fields.length === 0 ? "" : ` Fields: ${hook.fields.join(", ")}.`].join(""),
        }),
        setRow({ title: "Last calls", lead: `At most ${hook.per_minute} a minute.`, body: callRows(hook) }),
      ),
    });
  }

  function paint() {
    if (disposed) {
      return;
    }
    const sections = hooks.map(hookSection);
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections", role: "region", "aria-label": "Hooks" },
        freshCard(),
        sections.length === 0 ? emptyPanel("plug", "No hooks yet", "A hook is an address a service calls, signed, to start a scheduled task or an agent.") : sections,
      ),
    );
  }

  async function rotateSecret(hook) {
    const sure = await confirmDialog({
      title: `Rotate the secret of ${hook.name}?`,
      message: "Calls signed with the old secret are refused from now on. The new one is shown once.",
      confirmLabel: "Rotate",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    fresh = await api.post(`/hooks/${hook.id}/rotate`, {});
    toast("ok", "The secret was rotated.");
    paint();
  }

  async function removeHook(hook) {
    const sure = await confirmDialog({
      title: `Delete ${hook.name}?`,
      message: "Its address answers no call again.",
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await api.post(`/hooks/${hook.id}/delete`, {});
    hooks = hooks.filter((kept) => kept.id !== hook.id);
    if (fresh !== null && fresh.hook.id === hook.id) {
      fresh = null;
    }
    paint();
  }

  function openNew() {
    const name = el("input", { type: "text", maxlength: "80", autocomplete: "off", "aria-label": "Name" });
    const target = el(
      "select",
      { "aria-label": "What it starts" },
      el("option", { value: "" }, "Choose…"),
      schedules.length === 0 ? null : el("optgroup", { label: "Scheduled tasks" }, schedules.map((view) => el("option", { value: `schedule:${view.record.id}` }, view.record.name))),
      agents.filter((view) => view.record.id !== "dot").length === 0
        ? null
        : el(
            "optgroup",
            { label: "Agents" },
            agents.filter((view) => view.record.id !== "dot").map((view) => el("option", { value: `agent:${view.record.id}` }, view.record.name)),
          ),
    );
    const template = el("textarea", { rows: "3", maxlength: "4000", "aria-label": "What the run does" });
    const fields = el("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: "title, pull_request.title", "aria-label": "Fields" });
    const rate = el("input", { type: "number", min: "1", max: "120", value: String(DEFAULT_PER_MINUTE), "aria-label": "Calls a minute" });
    formDialog({
      title: "New hook",
      description: "An address a service calls, signed with the hook's own secret, to start a scheduled task or an agent. What the call carries reaches the run as data, never as instructions.",
      body: [
        field("Name", name, ""),
        field("What it starts", target, "The run has this task's or agent's own approvals, never more."),
        field("What the run does", template, "Your words. {name} stands for a field below; it is never filled with what the call says. A scheduled task may leave it empty."),
        field("Fields", fields, "The fields of the call's JSON the run is given, quoted as data: a dotted path each, apart by commas."),
        field("Calls a minute", rate, ""),
      ],
      submitLabel: "Make hook",
      valid: () => name.value.trim() !== "" && target.value !== "",
      onSubmit: async () => {
        const [binding, targetId] = target.value.split(/:(.*)/s);
        fresh = await api.post("/hooks", {
          name: name.value.trim(),
          binding,
          target_id: targetId,
          template: template.value,
          fields: fieldsOf(fields.value),
          per_minute: Number(rate.value),
        });
        hooks = [...hooks, fresh.hook];
        paint();
      },
      returnFocus: opener,
    });
  }

  async function load() {
    try {
      const [listed, scheduled, made] = await Promise.all([api.get("/hooks"), api.get("/schedules"), api.get("/agents")]);
      hooks = listed;
      schedules = scheduled;
      agents = made;
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    paint();
  }

  ctx.head.setLead("Addresses a service calls, signed with each hook's secret, to start a scheduled task or an agent at once.");
  ctx.head.setActions(opener);
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}
