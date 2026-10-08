// The home page: one composer in the middle of the window. The recent runs live in the sidebar; a run that waits for an
// answer is the only other thing this page ever shows, above the composer.

import { approvalCard, fromRecord } from "../approvals.js";
import { api } from "../api.js";
import { createAttachments, readyToSend } from "../attachments.js";
import { el, modelName } from "../dom.js";
import { allModels, offersChoice, savedLevel } from "../efforts.js";
import { icon } from "../icons.js";
import { segmented } from "../panels.js";
import { reasoningSelect } from "../reasoning.js";
import { approvalsPicker, goalModePicker } from "../runmodes.js";
import { onCompose } from "../shortcuts.js";
import { getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt } from "../ui.js";

const MODES = [
  { id: "task", label: "Task", hint: "Carry the goal out end to end on this Mac" },
  { id: "chat", label: "Chat", hint: "Converse; use tools whenever they help" },
  { id: "research", label: "Research", hint: "Read-only: gather information and report, change nothing" },
];
const MAX_GOAL_PX = 320;
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;

function defaultRefOf(snapshot) {
  return snapshot.status === null ? null : snapshot.status.models.main;
}

function defaultOptionText(ref) {
  return ref === null ? "Default model" : `Default (${modelName(ref)})`;
}

/** The models that can call tools, grouped by provider: runs need tools, so the others would only fail. */
function toolGroups(providers) {
  return providers
    .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools) }))
    .filter((group) => group.models.length > 0);
}

function pick(caption, control) {
  return el("label", { class: "composer-pick" }, el("span", { class: "composer-pick-label" }, caption), control);
}

function buildComposer(ctx) {
  const goal = el("textarea", { class: "textarea", rows: "3", placeholder: "What should Dot do?", "aria-label": "Goal" });
  const approvals = approvalsPicker();
  const goalMode = goalModePicker();
  // A chat has no goal to check, so the goal switch only belongs to the other two kinds of run.
  const mode = segmented(MODES, "task", (id) => {
    goalMode.setAvailable(id !== "chat");
    goal.focus();
  });
  const defaultOption = el("option", { value: "" });
  const model = el("select", { class: "select sm", "aria-label": "Model" }, defaultOption);
  const reasoning = el("span", { class: "composer-reasoning", hidden: true });
  const attachments = createAttachments({ dropTarget: goal, input: goal });
  const start = el(
    "button",
    { class: "btn primary send", type: "button", title: "Start (Enter)", "aria-label": "Start" },
    icon("arrowUp"),
    el("span", { class: "label" }, "Start"),
  );
  let providers = [];
  let shownDefault = NOTHING_SHOWN;

  // The model a run would use now: the one picked, else the daemon's default.
  function selectedInfo() {
    const ref = model.value === "" ? defaultRefOf(getSnapshot()) : model.value;
    return allModels(providers).find((candidate) => candidate.ref === ref) ?? null;
  }

  // The reasoning menu only exists for a model that offers more than one level.
  function paintReasoning() {
    const info = selectedInfo();
    if (info === null || !offersChoice(info)) {
      reasoning.hidden = true;
      reasoning.replaceChildren();
      return;
    }
    reasoning.replaceChildren(pick("Reasoning", reasoningSelect(info, "Reasoning level")));
    reasoning.hidden = false;
  }

  // The closed menu shows the model name only; its tooltip carries the whole reference.
  function paintModel() {
    const info = selectedInfo();
    model.title = info === null ? "" : info.ref;
    paintReasoning();
  }

  // Called on every status update; it only does work when the daemon's default model changed.
  function syncDefault() {
    const ref = defaultRefOf(getSnapshot());
    if (ref === shownDefault) {
      return;
    }
    shownDefault = ref;
    defaultOption.textContent = defaultOptionText(ref);
    paintModel();
  }

  function growGoal() {
    goal.style.height = "auto";
    goal.style.height = `${Math.min(goal.scrollHeight, MAX_GOAL_PX)}px`;
  }

  async function submit() {
    const text = goal.value.trim();
    if (text === "") {
      goal.focus();
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    const info = selectedInfo();
    start.disabled = true;
    await attempt(async () => {
      const created = await api.post("/runs", {
        goal: text,
        mode: mode.value(),
        model: model.value === "" ? null : model.value,
        effort: info !== null && offersChoice(info) ? savedLevel(info) : null,
        approvals: approvals.value(),
        goal_mode: mode.value() === "chat" ? null : goalMode.value(),
        thread_id: null,
        attachments: attachments.ids(),
      });
      attachments.clear();
      ctx.navigate(`/runs/${created.id}`);
    });
    start.disabled = false;
  }

  start.addEventListener("click", submit);
  goal.addEventListener("input", growGoal);
  // Enter starts the run like it sends a message in a chat; Shift+Enter keeps a new line.
  goal.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  model.addEventListener("change", paintModel);
  loadModels().then(
    (loaded) => {
      providers = loaded;
      for (const group of toolGroups(loaded)) {
        model.append(
          el(
            "optgroup",
            { label: group.provider },
            group.models.map((info) => el("option", { value: info.ref, title: info.ref }, modelName(info.ref))),
          ),
        );
      }
      paintModel();
    },
    (error) => {
      console.warn("the model list is unavailable", error);
      model.title = `The model list could not be loaded: ${error.message}`;
    },
  );
  syncDefault();

  // The status carries the daemon's defaults; the pickers show them until the person chose something else.
  function syncStatus() {
    syncDefault();
    approvals.sync();
    goalMode.sync();
  }

  const element = el(
    "section",
    { class: "card composer", "aria-label": "New chat" },
    goal,
    attachments.list,
    attachments.picker,
    el(
      "div",
      { class: "composer-bar" },
      el("div", { class: "composer-options" }, attachments.button, mode.element, pick("Model", model), reasoning, approvals.element, goalMode.element),
      start,
    ),
    approvals.warning,
  );
  return { element, focus: () => goal.focus(), dispose: onChange(syncStatus) };
}

function buildApprovals() {
  const list = el("div", { class: "stack" });
  const section = el("section", { class: "home-approvals", hidden: true }, el("div", { class: "section-label" }, "Waiting for you"), list);
  const cards = new Map();

  function sync(snapshot) {
    const wanted = new Set(snapshot.approvals.map((record) => record.id));
    for (const [id, card] of cards) {
      if (!wanted.has(id)) {
        card.node.remove();
        cards.delete(id);
      }
    }
    for (const record of snapshot.approvals) {
      if (!cards.has(record.id)) {
        const card = approvalCard({
          approval: fromRecord(record),
          heading: el("a", { class: "small", href: `#/runs/${record.run_id}` }, "Open run"),
          onSettled: () => {
            refresh().catch((error) => console.warn("status refresh failed", error));
          },
        });
        cards.set(record.id, card);
        list.append(card.node);
      }
    }
    section.hidden = cards.size === 0;
  }

  return { element: section, sync };
}

export function mount(root, ctx) {
  const composer = buildComposer(ctx);
  const approvals = buildApprovals();
  root.append(el("div", { class: "home" }, el("h1", { class: "visually-hidden" }, "New chat"), approvals.element, composer.element));
  approvals.sync(getSnapshot());
  const stopApprovals = onChange(approvals.sync);
  const stopCompose = onCompose(composer.focus);
  composer.focus();
  return () => {
    stopApprovals();
    stopCompose();
    composer.dispose();
  };
}
