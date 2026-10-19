// What the owner picks in the composer for one message (item 318, after Codex's composer): MCP servers whose tools the
// run starts with and skills whose instructions it starts with, as chips under the box, each in a colour of its own and
// taken away by its ×, and Plan first, by which the run writes its plan and asks before it changes anything. They go
// with the message that starts a run and are put away once it went (`sent()`); a steer leaves them for the next one.

import { el, switchRow } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

// The daemon's limits (server/views.py).
const MAX_PLUGINS = 8;
const MAX_SKILLS = 4;
const PLAN_FIRST = "Plan first";
const PLAN_FIRST_HINT = "Dot writes its plan and asks you before it changes anything.";

const KINDS = {
  plugin: { icon: "plug", noun: "MCP server", many: "MCP servers", most: MAX_PLUGINS },
  skill: { icon: "sparkles", noun: "skill", many: "skills", most: MAX_SKILLS },
};

// The colours a server's or a skill's chip takes, by its name: the characters' (themes.css), without amber, Dot's own.
const PICK_COLOURS = ["blue", "green", "violet", "orange", "teal", "pink", "red", "brown"];

/** The colour of the pick `name`, the same wherever its chip shows: one of `PICK_COLOURS`. */
export function pickColour(name) {
  let hash = 0;
  for (const char of name) {
    hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  }
  return PICK_COLOURS[hash % PICK_COLOURS.length];
}

// A chip or a mark of the server or the skill `name` takes its colour (app.css reads --pick-tint; Plan first keeps the
// accent).
function tint(node, name) {
  node.style.setProperty("--pick-tint", `var(--character-${pickColour(name)})`);
  return node;
}

/** The mark of a pick of `kind` ("plugin", "skill" or "plan") named `name`: its icon in its colour. */
function pickMark(kind, name) {
  const mark = el("span", { class: "pick-mark", dataset: { kind } }, icon(kind === "plan" ? "listOrdered" : KINDS[kind].icon));
  return kind === "plan" ? mark : tint(mark, name);
}

/** The chip of a pick of `kind` named `name`, which says what it is; `onRemove` takes it away, or null for the chip of
 * a sent message. */
function pickChip(kind, name, onRemove) {
  const what = kind === "plan" ? PLAN_FIRST_HINT : `The ${KINDS[kind].noun} ${name}`;
  const chip = el("span", { class: "pick-chip", role: "listitem", title: what, dataset: { kind, name } }, pickMark(kind, name), el("span", { class: "pick-name" }, name));
  if (kind !== "plan") {
    tint(chip, name);
  }
  if (onRemove !== null) {
    chip.append(el("button", { class: "pick-remove", type: "button", title: `Remove ${name}`, "aria-label": `Remove ${name}`, onclick: onRemove }, icon("x")));
  }
  return chip;
}

/** The picks a message went with (`data`, a `picks` event's: `{ plugins, skills, plan_first }`), as chips under it. */
export function sentPicks(data) {
  return el(
    "div",
    { class: "pick-list sent", role: "list", "aria-label": "Picked for this message" },
    data.plan_first ? pickChip("plan", PLAN_FIRST, null) : null,
    data.plugins.map((name) => pickChip("plugin", name, null)),
    data.skills.map((name) => pickChip("skill", name, null)),
  );
}

/** Whether `value`, read from a draft, holds picks as `saved()` gives them. */
export function arePicks(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.plugins) &&
    value.plugins.every((name) => typeof name === "string") &&
    Array.isArray(value.skills) &&
    value.skills.every((name) => typeof name === "string") &&
    typeof value.plan_first === "boolean"
  );
}

/**
 * The picks of one composer: `list` is the row of their chips, to place under the box, and `onChange()` runs after
 * every change. Returns `list`, `has(kind, name)`, `add(kind, name)` and `toggle(kind, name)` for a "plugin" or a
 * "skill", `planFirst()`, `planRow()` (the switch of Plan first, for the composer's menu), `setPlanAvailable(on)` (a chat
 * has no Plan first: off, the switch hides and the pick goes), `count()`, `body()` (the fields of a run request:
 * `{ plugins, skills, plan_first }`), `saved()` and `restore(saved)` for a draft, and `sent()` once a message that
 * starts a run went with them.
 */
export function createPicks({ onChange }) {
  const chosen = { plugin: [], skill: [] };
  let planFirst = false;
  let planAvailable = true;
  const list = el("div", { class: "pick-list", role: "list", "aria-label": "Picked for this message", hidden: true });
  const planSwitch = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": PLAN_FIRST });
  const planRow = switchRow(
    el(
      "div",
      { class: "composer-pick", role: "group", "aria-label": PLAN_FIRST, title: PLAN_FIRST_HINT },
      el(
        "span",
        { class: "composer-pick-content" },
        el("span", { class: "composer-pick-label" }, PLAN_FIRST),
        el("span", { class: "composer-pick-hint" }, "Draft a plan and ask before taking action"),
      ),
      planSwitch,
    ),
    planSwitch,
  );

  function paint() {
    planSwitch.setAttribute("aria-checked", String(planFirst));
    planRow.hidden = !planAvailable;
    list.replaceChildren(
      ...(planFirst ? [pickChip("plan", PLAN_FIRST, () => setPlanFirst(false))] : []),
      ...chosen.plugin.map((name) => pickChip("plugin", name, () => remove("plugin", name))),
      ...chosen.skill.map((name) => pickChip("skill", name, () => remove("skill", name))),
    );
    list.hidden = list.childElementCount === 0;
  }

  function changed() {
    paint();
    onChange();
  }

  function remove(kind, name) {
    chosen[kind] = chosen[kind].filter((other) => other !== name);
    changed();
  }

  function add(kind, name) {
    if (chosen[kind].includes(name)) {
      return;
    }
    if (chosen[kind].length >= KINDS[kind].most) {
      toast("error", `At most ${KINDS[kind].most} ${KINDS[kind].many} go with one message.`);
      return;
    }
    chosen[kind] = [...chosen[kind], name];
    changed();
  }

  function setPlanFirst(on) {
    planFirst = on && planAvailable;
    changed();
  }

  planSwitch.addEventListener("click", () => setPlanFirst(!planFirst));
  paint();

  return {
    list,
    has: (kind, name) => chosen[kind].includes(name),
    add,
    toggle: (kind, name) => (chosen[kind].includes(name) ? remove(kind, name) : add(kind, name)),
    planFirst: () => planFirst,
    planRow: () => planRow,
    setPlanAvailable: (on) => {
      planAvailable = on;
      planFirst = planFirst && on;
      changed();
    },
    count: () => chosen.plugin.length + chosen.skill.length + (planFirst ? 1 : 0),
    body: () => ({ plugins: [...chosen.plugin], skills: [...chosen.skill], plan_first: planFirst }),
    saved: () => ({ plugins: [...chosen.plugin], skills: [...chosen.skill], plan_first: planFirst }),
    restore: (saved) => {
      chosen.plugin = saved.plugins.slice(0, MAX_PLUGINS);
      chosen.skill = saved.skills.slice(0, MAX_SKILLS);
      planFirst = saved.plan_first && planAvailable;
      changed();
    },
    sent: () => {
      chosen.plugin = [];
      chosen.skill = [];
      planFirst = false;
      changed();
    },
  };
}
