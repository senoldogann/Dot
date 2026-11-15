// What the owner picks in the composer for one message (item 318, after Codex's composer): MCP servers whose tools the
// run starts with, accounts signed in whose mail and calendar it starts with (item 496) and skills whose instructions
// it starts with, under the box, each its mark and its name in a colour of its own with no border (item 858) and taken
// away by its × (a server's wears its service's mark, as the at menu does, item 530), and Plan first, by which the run writes its plan and asks before
// it changes anything. They go
// with the message that starts a run and are put away once it went (`sent()`); a steer leaves them for the next one.

import { el, switchRow } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { markContent, wearsDot } from "./marks.js";
import { onServerMarks, serverMark, signedAccount, wantServerMarks } from "./palette.js";
import { toast } from "./ui.js";

// The daemon's limits (server/views.py).
const MAX_PLUGINS = 8;
const MAX_SKILLS = 4;
const MAX_ACCOUNTS = 4;
const PLAN_FIRST = t("options.planFirst");
const PLAN_FIRST_HINT = t("options.planFirstHint");

const KINDS = {
  plugin: { icon: "plug", chip: "picks.chipPlugin", most: MAX_PLUGINS, tooMany: "picks.mostPlugins" },
  account: { icon: "mail", chip: "picks.chipAccount", most: MAX_ACCOUNTS, tooMany: "picks.mostAccounts" },
  skill: { icon: "puzzle", chip: "picks.chipSkill", most: MAX_SKILLS, tooMany: "picks.mostSkills" },
};

// The colours a server's or a skill's chip takes, by its name: the characters' (themes.css), without amber, Dot's own.
const PICK_COLOURS = ["blue", "green", "violet", "orange", "teal", "pink", "red", "brown"];

/** The colour of the pick `name`, the same wherever its chip shows: one of `PICK_COLOURS`. */
function pickColour(name) {
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

/** Draw the mark of the chip of the MCP server `name` into `holder` (its `.pick-mark`): its service's mark as the at menu
 * draws it, in the chip's colour (items 530 and 946); Dot's own icon for a server of no service, and for one the palette
 * has not named yet (item 581). */
function paintServerMark(holder, name) {
  const mark = serverMark(name);
  holder.replaceChildren(markContent(mark));
  holder.dataset.mark = wearsDot(mark) ? "dot" : "service";
}

// A chip drawn before the palette arrived wears Dot's icon: each chip that names a server is drawn again as the palette
// gives the marks, the chips under a sent message too.
onServerMarks(() => {
  for (const chip of document.querySelectorAll(".pick-chip[data-kind='plugin']")) {
    const holder = chip.querySelector(".pick-mark");
    const name = chip.getAttribute("data-name");
    if (holder !== null && name !== null) {
      paintServerMark(holder, name);
    }
  }
  for (const chip of document.querySelectorAll(".pick-chip[data-kind='account']")) {
    paintAccount(chip);
  }
});

/** Draw the chip of an account (`chip`, its address in `data-name`) as its provider: the provider's mark and name
 * (item 946: "Google", not the address, which its title keeps), once the palette names the account; until then, and for
 * an account no longer signed in, the mail icon and the address. */
function paintAccount(chip) {
  const address = chip.getAttribute("data-name");
  const holder = chip.querySelector(".pick-mark");
  const label = chip.querySelector(".pick-name");
  if (address === null || holder === null || label === null) {
    return;
  }
  const account = signedAccount(address);
  if (account === null) {
    return;
  }
  holder.replaceChildren(markContent(account.mark));
  holder.dataset.mark = "service";
  label.textContent = account.provider_name;
  chip.title = t("picks.accountTitle", { provider: account.provider_name, address });
  chip.dataset.provider = account.provider;
}

/** The mark of a pick of `kind` ("plugin", "skill" or "plan") named `name`: its icon in its colour; a server wears its
 * service's mark. */
function pickMark(kind, name) {
  if (kind === "plugin") {
    const holder = el("span", { class: "pick-mark", dataset: { kind } });
    paintServerMark(holder, name);
    wantServerMarks();
    return holder;
  }
  const mark = el("span", { class: "pick-mark", dataset: { kind } }, icon(kind === "plan" ? "listOrdered" : KINDS[kind].icon));
  return kind === "plan" ? mark : tint(mark, name);
}

/** The chip of a pick of `kind` named `name`, which says what it is; `onRemove` takes it away, or null for the chip of
 * a sent message. */
function pickChip(kind, name, onRemove) {
  const what = kind === "plan" ? PLAN_FIRST_HINT : t(KINDS[kind].chip, { name });
  const chip = el("span", { class: "pick-chip", role: "listitem", title: what, dataset: { kind, name } }, pickMark(kind, name), el("span", { class: "pick-name" }, name));
  if (kind !== "plan") {
    tint(chip, name);
  }
  if (onRemove !== null) {
    chip.append(el("button", { class: "pick-remove", type: "button", title: t("options.remove", { name }), "aria-label": t("options.remove", { name }), onclick: onRemove }, icon("x")));
  }
  if (kind === "account") {
    paintAccount(chip);
    wantServerMarks();
  }
  return chip;
}

/** The picks a message went with (`data`, a `picks` event's: `{ plugins, skills, plan_first, accounts }`), as a row
 * under it, or, with `inline`, as words of its text (item 858: its mark and its name in its colour, with no chip
 * round them, as a skill stands in a line of text). An event recorded before item 496 names no accounts. */
export function sentPicks(data, inline = false) {
  return el(
    inline ? "span" : "div",
    { class: inline ? "pick-list sent inline" : "pick-list sent", role: "list", "aria-label": t("options.picked") },
    spaced(
      [
        data.plan_first ? pickChip("plan", PLAN_FIRST, null) : null,
        ...data.plugins.map((name) => pickChip("plugin", name, null)),
        ...(data.accounts ?? []).map((name) => pickChip("account", name, null)),
        ...data.skills.map((name) => pickChip("skill", name, null)),
      ].filter((chip) => chip !== null),
      inline,
    ),
  );
}

/** `chips` as they stand: in a line of text with a space between them, so that a selection or a reader reads them as
 * words; in a row as they are. */
function spaced(chips, inline) {
  return inline ? chips.flatMap((chip, at) => (at === 0 ? [chip] : [" ", chip])) : chips;
}

/** Whether `value`, read from a draft, holds picks as `saved()` gives them; a draft kept before item 496 names no
 * accounts. */
export function arePicks(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.plugins) &&
    value.plugins.every((name) => typeof name === "string") &&
    Array.isArray(value.skills) &&
    value.skills.every((name) => typeof name === "string") &&
    (value.accounts === undefined || (Array.isArray(value.accounts) && value.accounts.every((name) => typeof name === "string"))) &&
    typeof value.plan_first === "boolean"
  );
}

/**
 * The picks of one composer: `list` is the row of their chips, to place under the box, and `onChange()` runs after
 * every change. Returns `list`, `has(kind, name)`, `add(kind, name)` and `toggle(kind, name)` for a "plugin", an
 * "account" or a "skill", `planFirst()`, `planRow()` (the switch of Plan first, for the composer's menu), `setPlanAvailable(on)` (a chat
 * has no Plan first: off, the switch hides and the pick goes), `count()`, `body()` (the fields of a run request:
 * `{ plugins, skills, accounts, plan_first }`), `saved()` and `restore(saved)` for a draft, and `sent()` once a message that
 * starts a run went with them.
 */
export function createPicks({ onChange }) {
  const chosen = { plugin: [], account: [], skill: [] };
  let planFirst = false;
  let planAvailable = true;
  const list = el("div", { class: "pick-list", role: "list", "aria-label": t("options.picked"), hidden: true });
  const planSwitch = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": PLAN_FIRST });
  const planRow = switchRow(
    el(
      "div",
      { class: "composer-pick", role: "group", "aria-label": PLAN_FIRST, title: PLAN_FIRST_HINT },
      el(
        "span",
        { class: "composer-pick-content" },
        el("span", { class: "composer-pick-label" }, PLAN_FIRST),
        el("span", { class: "composer-pick-hint" }, t("options.planFirstShort")),
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
      ...chosen.account.map((name) => pickChip("account", name, () => remove("account", name))),
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
      toast("error", t(KINDS[kind].tooMany, { most: KINDS[kind].most }));
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
    count: () => chosen.plugin.length + chosen.account.length + chosen.skill.length + (planFirst ? 1 : 0),
    body: () => ({ plugins: [...chosen.plugin], skills: [...chosen.skill], accounts: [...chosen.account], plan_first: planFirst }),
    saved: () => ({ plugins: [...chosen.plugin], skills: [...chosen.skill], accounts: [...chosen.account], plan_first: planFirst }),
    restore: (saved) => {
      chosen.plugin = saved.plugins.slice(0, MAX_PLUGINS);
      chosen.account = (saved.accounts ?? []).slice(0, MAX_ACCOUNTS);
      chosen.skill = saved.skills.slice(0, MAX_SKILLS);
      planFirst = saved.plan_first && planAvailable;
      changed();
    },
    sent: () => {
      chosen.plugin = [];
      chosen.account = [];
      chosen.skill = [];
      planFirst = false;
      changed();
    },
  };
}
