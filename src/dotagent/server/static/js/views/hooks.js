// Settings › Hooks (docs/hooks.md): addresses a service calls, signed with the hook's own secret, to start a scheduled
// task or an agent. Each hook is a card: its address, what it starts, a switch, Rotate secret and Delete, and its last
// calls (when, the status, why, and the run it started). New hook makes one in a dialog; the address and the secret are
// shown once, in a card at the top of the page, after a hook is made or its secret is rotated: no answer of the daemon
// carries the secret again.

import { api } from "../api.js";
import { copyButton } from "../copybutton.js";
import { dateTime, el } from "../dom.js";
import { t } from "../i18n.js";
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
  markOpener(opener, t("views.hooks.newHook"));
  const holder = el("div", { class: "stack" }, loadingPanel());

  function targetName(hook) {
    if (hook.schedule_id !== null) {
      const found = schedules.find((view) => view.record.id === hook.schedule_id);
      return t("views.hooks.scheduledTask", { name: found === undefined ? hook.schedule_id : found.record.name });
    }
    const found = agents.find((view) => view.record.id === hook.agent_id);
    return t("views.hooks.agent", { name: found === undefined ? hook.agent_id : found.record.name });
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
      t("views.hooks.iCopiedIt"),
    );
    return setSection({
      title: t("views.hooks.secretOf", { name: fresh.hook.name }),
      lead: t("views.hooks.copyItNow"),
      actions: done,
      body: setCard(
        setRow({ title: t("views.hooks.address"), lead: el("code", { class: "hook-value" }, address), control: copyButton(address, t("views.hooks.copyAddress")) }),
        setRow({ title: t("views.hooks.secret"), lead: el("code", { class: "hook-value hook-secret" }, fresh.secret), control: copyButton(fresh.secret, t("views.hooks.copySecret")) }),
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
          call.run_id === null ? null : el("a", { class: "hook-call-run", href: `#/runs/${call.run_id}` }, t("views.hooks.openRun")),
        ),
      ),
    );
  }

  /** The signed calls, then the calls refused before their signature matched (kept until Dot restarts). */
  function callRows(hook) {
    return el(
      "div",
      { class: "hook-call-lists" },
      hook.calls.length === 0 ? el("p", { class: "hint hook-calls-empty" }, t("views.hooks.noCallYet")) : callList(hook.calls, t("views.hooks.lastCallsOf", { name: hook.name })),
      hook.refused.length === 0
        ? null
        : el(
            "div",
            { class: "hook-refused" },
            el("p", { class: "hint" }, t("views.hooks.refusedLately")),
            callList(hook.refused, t("views.hooks.refusedCallsOf", { name: hook.name })),
          ),
    );
  }

  function hookSection(hook) {
    const toggle = switchToggle({
      label: t("views.hooks.nameOn", { name: hook.name }),
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
    const rotate = el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => rotateSecret(hook)) }, icon("refresh"), t("views.hooks.rotateSecret"));
    const remove = el("button", { class: "btn sm danger", type: "button", onclick: () => attempt(() => removeHook(hook)) }, icon("trash"), t("views.hooks.delete"));
    const address = addressOf(hook);
    return setSection({
      title: hook.name,
      lead: targetName(hook),
      actions: el("div", { class: "row hook-actions" }, rotate, remove),
      body: setCard(
        setRow({ title: hook.enabled ? t("views.hooks.on") : t("views.hooks.off"), lead: hook.enabled ? t("views.hooks.callsStartWork") : t("views.hooks.callsRefused"), control: toggle }),
        setRow({ title: t("views.hooks.address"), lead: el("code", { class: "hook-value" }, address), control: copyButton(address, t("views.hooks.copyAddress")) }),
        setRow({
          title: t("views.hooks.whatRunIsGiven"),
          lead: [hook.template === "" ? t("views.hooks.tasksOwnGoal") : hook.template, hook.fields.length === 0 ? "" : t("views.hooks.fieldsList", { fields: hook.fields.join(", ") })].join(""),
        }),
        setRow({ title: t("views.hooks.lastCalls"), lead: t("views.hooks.atMost", { count: hook.per_minute }), body: callRows(hook) }),
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
        { class: "set-sections", role: "region", "aria-label": t("views.hooks.hooks") },
        freshCard(),
        sections.length === 0 ? emptyPanel("plug", t("views.hooks.noHooksYet"), t("views.hooks.noHooksLead")) : sections,
      ),
    );
  }

  async function rotateSecret(hook) {
    const sure = await confirmDialog({
      title: t("views.hooks.rotateTitle", { name: hook.name }),
      message: t("views.hooks.rotateMessage"),
      confirmLabel: t("views.hooks.rotate"),
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    fresh = await api.post(`/hooks/${hook.id}/rotate`, {});
    toast("ok", t("views.hooks.secretRotated"));
    paint();
  }

  async function removeHook(hook) {
    const sure = await confirmDialog({
      title: t("views.hooks.deleteTitle", { name: hook.name }),
      message: t("views.hooks.deleteMessage"),
      confirmLabel: t("views.hooks.delete"),
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
    const name = el("input", { type: "text", maxlength: "80", autocomplete: "off", "aria-label": t("views.hooks.name") });
    const target = el(
      "select",
      { "aria-label": t("views.hooks.whatItStarts") },
      el("option", { value: "" }, t("views.hooks.choose")),
      schedules.length === 0 ? null : el("optgroup", { label: t("views.hooks.scheduledTasks") }, schedules.map((view) => el("option", { value: `schedule:${view.record.id}` }, view.record.name))),
      agents.filter((view) => view.record.id !== "dot").length === 0
        ? null
        : el(
            "optgroup",
            { label: t("views.hooks.agents") },
            agents.filter((view) => view.record.id !== "dot").map((view) => el("option", { value: `agent:${view.record.id}` }, view.record.name)),
          ),
    );
    const template = el("textarea", { rows: "3", maxlength: "4000", "aria-label": t("views.hooks.whatRunDoes") });
    const fields = el("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: "title, pull_request.title", "aria-label": t("views.hooks.fields") });
    const rate = el("input", { type: "number", min: "1", max: "120", value: String(DEFAULT_PER_MINUTE), "aria-label": t("views.hooks.callsAMinute") });
    formDialog({
      title: t("views.hooks.newHook"),
      description: t("views.hooks.newHookLead"),
      body: [
        field(t("views.hooks.name"), name, ""),
        field(t("views.hooks.whatItStarts"), target, t("views.hooks.runApprovals")),
        field(t("views.hooks.whatRunDoes"), template, t("views.hooks.yourWords", { name: "{name}" })),
        field(t("views.hooks.fields"), fields, t("views.hooks.fieldsHelp")),
        field(t("views.hooks.callsAMinute"), rate, ""),
      ],
      submitLabel: t("views.hooks.makeHook"),
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

  ctx.head.setLead(t("views.hooks.pageLead"));
  ctx.head.setActions(opener);
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}
