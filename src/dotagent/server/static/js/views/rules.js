// Rules (items 110, 373, 449, 565 and 622): what Dot may do on its own, what it asks you first, and what it leaves to you,
// for someone who knows no software. The page is one line that adds a rule ("When Dot wants to [ … ], it should [ … ]",
// buildAdder in ruleadder.js), the rules as plain sentences in the order the policy tries them, and, under one fold,
// Advanced, the rest: the sandbox of a run's commands (item 262) and what is always on whatever a rule says. A rule's
// tool, pattern and how it was used (item 473) are under its Details. A rule's arrows move it up or down the order, and
// a rule that another one decides in place of wherever it fits says it is never used (item 449): an earlier one, or one
// that hands the step to you, which decides wherever it stands (item 481). `#/settings/rules?wants=…&should=…` (an
// approval card's "Make a rule of this") puts the words and the choice in the adder. Which switch Auto review would be
// is left out: no setting of Dot's turns a check off without making Dot ask less (roadmap/622.md).

import { api } from "../api.js";
import { clip, el, } from "../dom.js";
import { icon } from "../icons.js";
import { buildAdder } from "../ruleadder.js";
import { ruleSentence } from "../rulesentence.js";
import { usageLine } from "../rulepreview.js";
import { DEFAULT_CHOICE, LATELY_DAYS, neverUsedWhy, ruleText } from "../rulewords.js";
import { emptyPanel, inlineError, inlineLoading, setSection, switchRowSetting } from "../panels.js";
import { onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { t, tCount } from "../i18n.js";

const QUOTE_CHARS = 100;
// The live event of a rule added, switched or deleted on any page (`rules_changed`, `dotagent.server.routes_data`).
const RULES_CHANGED = "rules_changed";
// Sentences to start from: a click puts the words and the choice in the adder (the drafter turns each into a rule: a
// command or two of the shell, a command that stops Dot, every step on one site).
const EXAMPLES = [
  { label: t("views.rules.runGitStatusAndGit"), wants: t("views.rules.runGitStatusAndGit2"), choice: "allow" },
  { label: t("views.rules.askMeBeforeDeletingAnything"), wants: t("views.rules.deleteAnythingWithRm"), choice: "ask" },
  { label: t("views.rules.leaveEverythingOnMybankCom"), wants: t("views.rules.doAnythingOnMybankCom"), choice: "handoff" },
];

/** One of a rule's two arrows: up toward the first rule, down toward the last; at its end of the order it is off. */
function moveButton(record, index, total, direction, onMove) {
  const up = direction === "up";
  return el(
    "button",
    {
      class: "btn ghost sm icon-only rule-move",
      type: "button",
      title: up ? t("views.rules.moveThisRuleUpIt") : t("views.rules.moveThisRuleDownIt"),
      "aria-label": t("views.rules.moveRuleValueDirection", { value: index + 1, direction }),
      "data-rule": record.id,
      "data-move": direction,
      disabled: up ? index === 0 : index === total - 1,
      onclick: () => onMove(record, direction),
    },
    icon(up ? "arrowUp" : "arrowDown"),
  );
}

/** Under a rule that another rule decides in place of (`never_decided_by`, item 449): the badge in the sentence's line and
 * the words that say why and what helps, or null. `numberOf(id)` is a rule's place in the list and `behaviorOf(id)` its answer. */
function neverUsed(record, numberOf, behaviorOf) {
  if (record.never_decided_by === null) {
    return null;
  }
  const why = neverUsedWhy(numberOf(record.never_decided_by), behaviorOf(record.never_decided_by), record.behavior);
  return {
    badge: el("span", { class: "badge b-warn", title: why }, t("views.rules.neverUsed")),
    note: el("p", { class: "rule-never" }, icon("alert"), el("span", null, why)),
  };
}

/** What a rule is made of, for whoever wants it: the tool, the pattern, the why, and how the rule was used on the last days
 * (`used`, a line, or null until it is read and for a rule switched off). */
function ruleFacts(record, used) {
  const facts = [
    [t("views.rules.tool"), el("code", { class: "mono" }, record.tool_glob)],
    [t("views.rules.pattern"), record.match_regex === "" ? t("views.rules.noneItFitsEveryTime") : el("code", { class: "mono rule-pattern" }, record.match_regex)],
    record.description === "" ? null : [t("views.rules.why"), record.description],
    used !== null ? [t("views.rules.usedLately"), used] : record.enabled ? null : [t("views.rules.usedLately"), t("views.rules.itIsSwitchedOffSo")],
  ].filter((fact) => fact !== null);
  return el(
    "dl",
    { class: "rule-facts", id: `rule-facts-${record.id}` },
    facts.map(([name, value]) => el("div", { class: "rule-fact" }, el("dt", null, name), el("dd", null, value))),
  );
}

/** A rule's row: its switch, its place, the sentence it reads as with its answer as the badge that ends it, why it is
 * never used when it is not, and, in the row's actions, its Details (`open` says whether they are shown), its arrows
 * (while there is more than one rule) and its delete. */
function ruleRow(record, index, total, numberOf, behaviorOf, handlers, used, open) {
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": t("views.rules.valueRuleValue2", { value: record.enabled ? t("views.rules.switchOff") : t("views.rules.switchOn"), value2: index + 1 }),
    title: record.enabled ? t("views.rules.switchThisRuleOff") : t("views.rules.switchThisRuleOn"),
    onclick: () => handlers.onToggle(record),
  });
  const remove = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: t("views.rules.deleteThisRule"),
      "aria-label": t("views.rules.deleteRuleValue", { value: index + 1 }),
      onclick: () => handlers.onDelete(record),
    },
    icon("trash"),
  );
  const facts = ruleFacts(record, used);
  facts.hidden = !open;
  const more = el(
    "button",
    { class: "btn ghost sm rule-more", type: "button", "aria-expanded": String(open), "aria-controls": facts.id },
    el("span", { class: "chev" }, icon("chevron")),
    t("views.rules.details"),
  );
  more.addEventListener("click", () => {
    facts.hidden = !facts.hidden;
    more.setAttribute("aria-expanded", String(!facts.hidden));
    handlers.onOpen(record, !facts.hidden);
  });
  const never = neverUsed(record, numberOf, behaviorOf);
  return el(
    "li",
    { class: record.enabled ? "rule-row" : "rule-row faint", dataset: { rule: record.id } },
    toggle,
    el(
      "div",
      { class: "rule-main" },
      el(
        "div",
        { class: "rule-line" },
        el("span", { class: "rule-number", title: t("views.rules.rulesAreCheckedInThis"), "aria-hidden": "true" }, String(index + 1)),
        el("span", { class: "rule-text" }, ruleSentence(record), never === null ? null : never.badge),
      ),
      never === null ? null : never.note,
    ),
    el(
      "div",
      { class: "rule-actions" },
      total > 1 ? [moveButton(record, index, total, "up", handlers.onMove), moveButton(record, index, total, "down", handlers.onMove)] : null,
      more,
      remove,
    ),
    facts,
  );
}

/** Under the rules: how two rules that fit the same action are told apart, and that the built-in checks come first. In the
 * words of docs/safety.md. `onSeeAll()` opens Advanced, where they are listed. */
function foot(count, onSeeAll) {
  return el(
    "div",
    { class: "rule-foot" },
    count > 1
      ? el("p", null, t("views.rules.ifTwoRulesFitThe"))
      : null,
    el(
      "p",
      null,
      t("views.rules.dotSBuiltInSafety"),
    ),
    el("button", { class: "set-link rule-see-all", type: "button", onclick: onSeeAll }, t("views.rules.seeThemAll")),
  );
}

/** The card's place while there is no rule: the shield, what Dot does meanwhile and the sentences to start from;
 * `onPick(example)` puts one in the adder. */
function emptyState(onPick) {
  return el(
    "div",
    { class: "rule-empty" },
    emptyPanel("shield", t("views.rules.noRulesYet"), t("views.rules.forNowDotSBuilt")),
    el(
      "div",
      { class: "rule-examples", role: "group", "aria-label": t("views.rules.rulesToStartFrom") },
      EXAMPLES.map((example) => el("button", { class: "rule-example", type: "button", onclick: () => onPick(example) }, example.label)),
    ),
  );
}

/** What no rule lifts, in words: five checked lines and what a run in the least-asking mode does, one row of the Advanced card. */
function protections() {
  const lines = [
    t("views.rules.readingYourSecretsAndSending"),
    t("views.rules.moneyAndPasswordsAreHanded"),
    t("views.rules.aMessageOrAPost"),
    t("views.rules.aChangeToDotS"),
    [t("views.rules.aResearchRunOnlyReads"), el("span", { class: "muted" }, t("views.rules.askOnlyToPayOr"))],
  ];
  return el(
    "div",
    { class: "set-row rule-always", role: "group", "aria-labelledby": "rule-always-title" },
    el(
      "div",
      { class: "rule-always-head" },
      el("span", { class: "rule-tile", "aria-hidden": "true" }, icon("lock")),
      el("h3", { class: "rule-always-title", id: "rule-always-title" }, t("views.rules.alwaysOnWhateverYourRules")),
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
      t("views.rules.askOnlyToPayOr2"),
    ),
  );
}

/**
 * The owner's free mode (item 922), switched here while the daemon runs; the config file keeps it. On, no task asks
 * anything, in any chat and any mode; off, each asks as its mode says.
 */
function buildFreeMode(isDisposed) {
  let view = null;
  const setting = switchRowSetting({
    label: t("views.rules.freeModeDotAsksYou"),
    hint: [
      t("views.rules.onNoTaskAsksYou"),
      el(
        "span",
        { class: "rule-fine" },
        t("views.rules.itHoldsFromTheNext"),
        el("code", { class: "mono" }, "[policy] free"),
        ".",
      ),
    ],
    read: () => view?.free === true,
    write: async (on) => {
      view = await api.put("/policy/free", { free: on });
      toast("ok", on ? t("views.rules.freeModeIsOnFrom") : t("views.rules.freeModeIsOffEach"));
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
      const found = await api.get("/policy/free");
      if (isDisposed()) {
        return;
      }
      view = found;
      setting.toggle.disabled = false;
      setting.paint();
    } catch (error) {
      if (!isDisposed()) {
        readFailed("readFailed.freeMode", error);
      }
    }
  }

  return { element: setting.element, load };
}

/** The macOS sandbox of a run's commands (item 262), switched here while the daemon runs; the config file keeps it. */
function buildSandbox(isDisposed) {
  let view = null;
  const setting = switchRowSetting({
    label: t("views.rules.keepDotSCommandsIn"),
    hint: [
      t("views.rules.commandsTerminalsScriptsAndSearche"),
      el(
        "span",
        { class: "rule-fine" },
        t("views.rules.offTheyRunWithYour"),
        el("code", { class: "mono" }, "[shell] sandbox"),
        ".",
      ),
    ],
    read: () => view?.sandbox === true,
    write: async (on) => {
      view = await api.put("/shell/sandbox", { sandbox: on });
      toast("ok", on ? t("views.rules.fromTheNextCommandOn") : t("views.rules.fromTheNextCommandOn2"));
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
        readFailed("readFailed.sandbox", error);
      }
    }
  }

  return { element: setting.element, load };
}

export function mount(root, ctx) {
  let disposed = false;
  let rules = null;
  // How the rules were used on the last days (item 473), read after the rules; an answer older than the last read is
  // left unshown.
  let usage = null;
  let usageTicket = 0;
  // The rules whose Details are open, kept through every drawing of the list.
  const opened = new Set();
  const holder = el("div", { class: "rule-holder" }, inlineLoading());

  function paint() {
    if (rules === null) {
      return;
    }
    // What the page holds, at the head's right: how many rules there are.
    ctx.head.setMeta(...(rules.length === 0 ? [] : [tCount(rules.length, "views.rules.countRule", "views.rules.countRules")]));
    if (rules.length === 0) {
      holder.replaceChildren(emptyState((example) => adder.start(example.wants, example.choice)), foot(0, showAdvanced));
      return;
    }
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
      onMove: (record, direction) => attempt(() => move(record, direction)),
      onOpen: (record, isOpen) => (isOpen ? opened.add(record.id) : opened.delete(record.id)),
    };
    const numberOf = (id) => rules.findIndex((record) => record.id === id) + 1;
    const behaviorOf = (id) => rules.find((record) => record.id === id)?.behavior ?? null;
    const usedBy = new Map((usage?.rules ?? []).map((used) => [used.rule_id, used]));
    const lineOf = (record) => {
      const used = usedBy.get(record.id);
      return used === undefined ? null : usageLine(used, usage, LATELY_DAYS, numberOf);
    };
    const problem = usage === null || usage.problem === null ? null : el("p", { class: "small rule-tryout-problem rule-problem" }, usage.problem);
    // A rule's arrow keeps the focus through every drawing of the list, whichever of the reads after a move comes last.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && holder.contains(active) ? active.dataset : null;
    // replaceChildren writes a null as the word: what is not there is left out first.
    holder.replaceChildren(
      ...[
        problem,
        el(
          "ol",
          { class: "rule-list" },
          rules.map((record, index) => ruleRow(record, index, rules.length, numberOf, behaviorOf, handlers, lineOf(record), opened.has(record.id))),
        ),
        foot(rules.length, showAdvanced),
      ].filter((part) => part !== null),
    );
    if (focused?.rule !== undefined && focused.move !== undefined) {
      focusArrow(focused.rule, focused.move);
    }
  }

  async function loadUsage() {
    usageTicket += 1;
    const ticket = usageTicket;
    const read = await api.get(`/rules/usage?days=${LATELY_DAYS}`);
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
        readFailed("readFailed.rules", error);
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
    const arrows = [...holder.querySelectorAll(`[data-rule="${CSS.escape(id)}"][data-move]`)];
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
      title: t("views.rules.deleteThisRule2"),
      message: t("views.rules.clipStopsApplying", { clip: clip(ruleText(record), QUOTE_CHARS) }),
      confirmLabel: t("views.rules.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/rules/${record.id}`);
    opened.delete(record.id);
    toast("ok", t("views.rules.ruleDeleted"));
    await reload();
  }

  const adder = buildAdder({ onAdded: reload, isDisposed: () => disposed });
  const sandbox = buildSandbox(() => disposed);
  const freeMode = buildFreeMode(() => disposed);
  const advanced = el(
    "details",
    { class: "set-card rule-advanced" },
    el(
      "summary",
      { class: "rule-advanced-head" },
      el("span", { class: "chev" }, icon("chevron")),
      el("span", { class: "rule-advanced-title" }, t("views.rules.advanced")),
      el("span", { class: "rule-advanced-lead" }, t("views.rules.theSafeBoxForDot")),
    ),
    el("div", { class: "rule-advanced-body" }, sandbox.element, protections()),
  );

  /** Open Advanced and bring it into view, for the sentence under the rules that says the checks are listed there. */
  function showAdvanced() {
    advanced.open = true;
    advanced.scrollIntoView({ block: "nearest" });
    advanced.querySelector("summary")?.focus({ preventScroll: true });
  }

  ctx.head.setLead(t("views.rules.sayWhatDotMayDo"));
  root.append(
    el(
      "div",
      { class: "set-sections" },
      setSection({
        title: t("views.rules.howDotAsks"),
        lead: t("views.rules.oneSwitchForEveryChat"),
        body: el("section", { class: "set-card rule-free", "aria-label": t("views.rules.howDotAsks") }, freeMode.element),
      }),
      setSection({
        title: t("views.rules.addARule"),
        lead: t("views.rules.sayItInYourOwn"),
        body: adder.element,
      }),
      setSection({ title: t("views.rules.yourRules"), body: el("section", { class: "set-card rule-panel", "aria-label": t("views.rules.yourRules") }, holder) }),
      advanced,
    ),
  );
  // The words and the choice an approval card sends ("Make a rule of this"), in the address after the tab's name.
  const asked = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const wants = asked.get("wants");
  if (wants !== null && wants.trim() !== "") {
    adder.start(wants.trim(), asked.get("should") ?? DEFAULT_CHOICE);
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
  attempt(freeMode.load);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
  };
}
