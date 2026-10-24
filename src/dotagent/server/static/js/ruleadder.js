// The line that adds a rule (item 622): "When Dot wants to [ … ]  It should [ Ask me first ]  Add rule". The owner's words
// and the choice are made one sentence for the review model, which drafts the rule (item 110); the draft is read back as
// a sentence ("Dot understood: …") and nothing applies until the owner says Add it. Under Details of that sentence
// are the parts the daemon keeps (the tool, the pattern, the answer, the why), to be changed, and what the rule would
// have done on the steps of the last days (item 469). When the drafter fails, the words stay in the box, and the parts
// can be filled in by hand. A rule drafted from words is never added with another answer than the owner chose: a draft
// that came back with another one is not offered, and the answer of a draft is not changed alone, since the drafter
// writes its pattern for that answer (item 561).

import { api } from "./api.js";
import { ago, el } from "./dom.js";
import { icon } from "./icons.js";
import { field } from "./panels.js";
import { outcomeLabel, previewLines } from "./rulepreview.js";
import { ruleSentence } from "./rulesentence.js";
import { ANSWERS, answerOf, CHOICES, DEFAULT_CHOICE, draftSentence, LATELY_DAYS, latelyLine, WANTS_CHARS } from "./rulewords.js";
import { attempt, toast } from "./ui.js";

/**
 * The rule as its parts, read back as a sentence: the line "Dot understood: …", the buttons that add it or take the
 * owner back to the words, and the parts under Details. `rule` is what the drafter gave (`behavior`, `tool_glob`,
 * `match_regex`, `description`); with `byHand` the parts are the owner's to fill in and Details is open.
 * `onAdd(rule)` adds the rule as the parts stand, `onBack()` goes back to the words.
 */
function buildPending({ rule, byHand, onAdd, onBack }) {
  // True while the parts are as the drafter wrote them: a part changed makes the rule the owner's own, and the sentence
  // the drafter wrote for it, which said what it fitted, no longer does.
  let understood = !byHand;
  const tool = el("input", {
    class: "input mono",
    type: "text",
    maxlength: "120",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "shell",
    "aria-label": "Tool",
  });
  const pattern = el("input", {
    class: "input mono",
    type: "text",
    maxlength: "500",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "\\bgit (status|diff|log)\\b",
    "aria-label": "Pattern",
  });
  const answer = el(
    "select",
    { class: "select", "aria-label": "Answer" },
    ANSWERS.map((each) => el("option", { value: each.id }, each.choice)),
  );
  const why = el("input", {
    class: "input",
    type: "text",
    maxlength: "300",
    autocomplete: "off",
    placeholder: "Reading the state of a repository is harmless",
    "aria-label": "Why",
  });
  tool.value = rule.tool_glob;
  pattern.value = rule.match_regex;
  answer.value = rule.behavior;
  why.value = rule.description;
  const answerHint = el("span", { class: "hint" });

  function current() {
    return {
      behavior: answer.value,
      tool_glob: tool.value.trim(),
      match_regex: pattern.value.trim(),
      description: why.value.trim(),
    };
  }

  const line = el("p", { class: "rule-understood" });
  const addIt = el("button", { class: "btn primary", type: "button" }, "Add it");
  const back = el("button", { class: "btn ghost rule-back", type: "button" }, "Change the words");
  // What the rule would have done lately (item 469), in a line the owner reads before adding it; the whole of it is in Details.
  const lately = el("p", { class: "rule-lately", role: "status" });
  const tried = el("div", { class: "rule-tryout stack tight", role: "status", hidden: true });
  const tryButton = el("button", { class: "btn", type: "button" }, `Test on the last ${LATELY_DAYS} days`);
  // Which try-out may still show: one that answers after the rule changed is left unshown.
  let tryTicket = 0;

  function paint() {
    // The answer of a draft is the choice the owner made: the drafter wrote its pattern for that answer, wide for a rule
    // that stops Dot and narrow for one that lets it act, so it is not changed alone. Once another part is the owner's
    // own, the answer is too.
    answer.disabled = understood;
    answerHint.textContent = understood ? "The answer you chose above. Change another part to set it here." : answerOf(answer.value).text;
    line.replaceChildren(
      icon("check"),
      el("span", { class: "rule-understood-label" }, understood ? "Dot understood:" : "Your rule:"),
      " ",
      ruleSentence(current()),
    );
    addIt.disabled = tool.value.trim() === "";
  }

  function putAwayTryOut() {
    tryTicket += 1;
    tryButton.disabled = false;
    lately.replaceChildren();
    tried.hidden = true;
    tried.replaceChildren();
  }

  // A part changed: the rule is no longer the one that was drafted, nor tried.
  function changed() {
    if (understood && why.value === rule.description) {
      why.value = "";
    }
    understood = false;
    putAwayTryOut();
    paint();
  }

  for (const part of [tool, pattern, answer]) {
    part.addEventListener("input", changed);
  }
  why.addEventListener("input", paint);

  async function tryOut() {
    const now = current();
    if (now.tool_glob === "") {
      tool.focus();
      return;
    }
    tryTicket += 1;
    const ticket = tryTicket;
    tryButton.disabled = true;
    lately.replaceChildren(el("span", { class: "spinner" }), "Trying it on what Dot did lately…");
    try {
      const view = await api.post("/rules/preview", {
        behavior: now.behavior,
        tool_glob: now.tool_glob,
        match_regex: now.match_regex,
        days: LATELY_DAYS,
      });
      if (ticket !== tryTicket) {
        return;
      }
      lately.textContent = latelyLine(view, LATELY_DAYS);
      // replaceChildren writes a null as the word: what is not there is left out first.
      const parts = [
        ...previewLines(view, LATELY_DAYS).map((each) => el("p", { class: "small" }, each)),
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
      tried.replaceChildren(...parts.filter((each) => each !== null));
      tried.hidden = false;
    } catch (error) {
      if (ticket === tryTicket) {
        lately.replaceChildren();
      }
      throw error;
    } finally {
      if (ticket === tryTicket) {
        tryButton.disabled = false;
      }
    }
  }

  // True while the rule is being added: a second press, or Enter, must not add it twice.
  let adding = false;
  async function add() {
    const now = current();
    if (now.tool_glob === "") {
      tool.focus();
      return;
    }
    if (adding) {
      return;
    }
    adding = true;
    addIt.disabled = true;
    try {
      await onAdd(now);
    } finally {
      adding = false;
      addIt.disabled = tool.value.trim() === "";
    }
  }

  tryButton.addEventListener("click", () => attempt(tryOut));
  addIt.addEventListener("click", () => attempt(add));
  back.addEventListener("click", onBack);
  const toolField = field("Tool", tool, "What Dot would use: shell, write_file, browser_*, desktop_*, mcp__*. A * stands for any run of letters.");
  const answerField = field("Answer", answer, "");
  answerField.classList.add("wide");
  answerField.append(answerHint);
  const whyField = field("Why", why, "Optional. Shown when the rule decides.");
  whyField.classList.add("wide");
  // The parts of the rule, for whoever wants them: shut, unless they are the owner's to fill in.
  const body = el(
    "div",
    { class: "stack rule-details-body", id: "rule-pending-details", hidden: !byHand },
    el(
      "div",
      { class: "form-grid" },
      toolField,
      field(
        "Pattern",
        pattern,
        "Optional. Searched, capitals or not, in what the action says it does (a command's starts with “Run:”) and in what it acts on. Empty means every time.",
      ),
      answerField,
      whyField,
    ),
    tried,
    el("div", { class: "rule-tryout-buttons" }, tryButton),
  );
  const more = el(
    "button",
    { class: "btn ghost sm rule-more", type: "button", "aria-expanded": String(byHand), "aria-controls": body.id },
    el("span", { class: "chev" }, icon("chevron")),
    "Details",
  );
  more.addEventListener("click", () => {
    body.hidden = !body.hidden;
    more.setAttribute("aria-expanded", String(!body.hidden));
  });
  paint();
  const element = el(
    "div",
    { class: "rule-pending", role: "group", "aria-label": "The rule to add" },
    line,
    lately,
    el("div", { class: "rule-pending-buttons" }, more, back, addIt),
    body,
  );
  return {
    element,
    focusAdd: () => addIt.focus({ preventScroll: true }),
    focusTool: () => tool.focus({ preventScroll: true }),
    tryOut,
  };
}

/**
 * The adder: the box, the choice and Add rule, the outcome of a draft that failed, and the rule to check once it is
 * drafted. `onAdded()` runs once a rule is added; `isDisposed()` says the page is gone. Returns the card and `start(wants,
 * choice)`, which puts the words and the choice in (an example, an approval's "Make a rule of this").
 */
export function buildAdder({ onAdded, isDisposed }) {
  const wants = el("input", {
    class: "input",
    type: "text",
    maxlength: String(WANTS_CHARS),
    autocomplete: "off",
    autocapitalize: "none",
    spellcheck: "false",
    enterkeyhint: "go",
    placeholder: "e.g. open a website",
    "aria-label": "When Dot wants to",
  });
  const should = el(
    "select",
    { class: "select", "aria-label": "It should" },
    CHOICES.map((id) => el("option", { value: id }, answerOf(id).choice)),
  );
  should.value = DEFAULT_CHOICE;
  const add = el("button", { class: "btn primary", type: "submit", disabled: true }, "Add rule");
  const form = el(
    "form",
    { class: "rule-adder", novalidate: true },
    el("span", { class: "rule-adder-label rule-adder-when", "aria-hidden": "true" }, "When Dot wants to"),
    wants,
    el("span", { class: "rule-adder-label rule-adder-it", "aria-hidden": "true" }, "It should"),
    should,
    add,
  );
  const hint = el("p", { class: "rule-hint" });
  const outcome = el("div", { class: "rule-outcome", role: "status", hidden: true });
  const slot = el("div", { class: "rule-pending-slot", hidden: true });
  const element = el("section", { class: "set-card rule-adder-card", "aria-label": "Add a rule" }, el("div", { class: "rule-adder-row" }, form, hint), outcome, slot);
  // True while the words are being drafted, and while the rule drafted waits to be added: the box and the choice are the
  // ones the draft was made of, so they stay as they are until the owner goes back to the words.
  let drafting = false;
  let locked = false;

  function paint() {
    hint.textContent = answerOf(should.value).text;
    wants.disabled = drafting || locked;
    should.disabled = drafting || locked;
    add.disabled = drafting || locked || wants.value.trim() === "";
  }

  function lock(on) {
    locked = on;
    paint();
  }

  function say(tone, ...parts) {
    outcome.className = `rule-outcome ${tone}`;
    outcome.replaceChildren(...parts);
    outcome.hidden = false;
  }

  function hideOutcome() {
    outcome.hidden = true;
    outcome.replaceChildren();
  }

  function focusWants() {
    wants.focus({ preventScroll: true });
    wants.setSelectionRange(wants.value.length, wants.value.length);
  }

  function closePending() {
    slot.replaceChildren();
    slot.hidden = true;
    lock(false);
  }

  function reset() {
    closePending();
    hideOutcome();
    wants.value = "";
    should.value = DEFAULT_CHOICE;
    paint();
  }

  async function addRule(rule) {
    await api.post("/rules", rule);
    toast("ok", "Rule added at the end of your list.");
    reset();
    // The box is ready for the next rule, where a keyboard is a key away; on a touch screen it would raise one.
    if (matchMedia("(pointer: fine)").matches) {
      focusWants();
    }
    await onAdded();
  }

  function openPending(rule, byHand) {
    closePending();
    hideOutcome();
    lock(true);
    const pending = buildPending({
      rule,
      byHand,
      onAdd: addRule,
      onBack: () => {
        closePending();
        focusWants();
      },
    });
    slot.replaceChildren(pending.element);
    slot.hidden = false;
    element.scrollIntoView({ block: "nearest" });
    if (byHand) {
      pending.focusTool();
    } else {
      pending.focusAdd();
      // A drafted rule was written without looking at what it would catch: it is tried at once.
      attempt(pending.tryOut);
    }
  }

  // The way out of a draft that did not come: fill in the parts of the rule by hand, under the choice made.
  const byHandButton = () =>
    el(
      "button",
      { class: "btn sm", type: "button", onclick: () => openPending({ behavior: should.value, tool_glob: "", match_regex: "", description: "" }, true) },
      "Fill in the details yourself",
    );

  function failed(error) {
    // 422: the words make no rule, and the model says why; anything else is a failure to draft.
    say(
      "bad",
      icon("alert"),
      el(
        "div",
        { class: "rule-outcome-text" },
        el("p", { class: "rule-outcome-title" }, error.status === 422 ? "Dot could not make a rule of these words." : "Dot could not read your words as a rule just now."),
        el("p", null, error.message),
        el("p", { class: "hint" }, "Your words are still in the box. Say what Dot wants to do, like “open a website”, and try again, or fill in the details yourself."),
        el("div", { class: "cluster" }, byHandButton()),
      ),
    );
  }

  function mismatched(drafted) {
    say(
      "bad",
      icon("alert"),
      el(
        "div",
        { class: "rule-outcome-text" },
        el("p", { class: "rule-outcome-title" }, "Dot read your words differently from your choice."),
        el("p", null, "It understood: ", ruleSentence(drafted)),
        el("p", { class: "hint" }, `You chose “${answerOf(should.value).choice}”, so nothing was added. Change the words, or the choice, and try again.`),
        el("div", { class: "cluster" }, byHandButton()),
      ),
    );
  }

  async function draft() {
    if (drafting || locked) {
      return;
    }
    const words = wants.value.trim();
    if (words === "") {
      wants.focus();
      return;
    }
    const chosen = should.value;
    drafting = true;
    paint();
    say("busy", el("span", { class: "spinner" }), el("span", null, "Dot is reading your words…"));
    let drafted;
    try {
      drafted = await api.post("/rules/draft", { sentence: draftSentence(words, chosen) });
    } catch (error) {
      drafting = false;
      paint();
      if (!isDisposed()) {
        failed(error);
        focusWants();
      }
      return;
    }
    drafting = false;
    paint();
    if (isDisposed()) {
      return;
    }
    if (drafted.behavior !== chosen) {
      mismatched(drafted);
      focusWants();
      return;
    }
    openPending(drafted, false);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    attempt(draft);
  });
  wants.addEventListener("input", () => {
    paint();
    hideOutcome();
  });
  should.addEventListener("change", () => {
    paint();
    hideOutcome();
  });
  paint();

  return {
    element,
    start: (text, choice) => {
      reset();
      wants.value = text.slice(0, WANTS_CHARS);
      if (CHOICES.includes(choice)) {
        should.value = choice;
      }
      paint();
      focusWants();
      element.scrollIntoView({ block: "nearest" });
    },
  };
}
