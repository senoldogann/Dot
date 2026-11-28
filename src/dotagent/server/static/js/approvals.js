// The card that asks the user: an approval of an action, a hand-off of a step, or a question from the agent, which the
// run waits for (`pending`) or left for the user and went on without (`left`).

import { api } from "./api.js";
import { cardWords, FIXED_REASONS, unsaid } from "./cardwords.js";
import { codeBlock } from "./codeblock.js";
import { plainReason, riskWords } from "./approvalwords.js";
import { clip, dateTime, el } from "./dom.js";
import { capital, flagWords, kindWords } from "./flagwords.js";
import { icon } from "./icons.js";
import { t } from "./i18n.js";
import { viaOutbox } from "./outbox.js";
import { toastError } from "./ui.js";

const STATUS_BADGE = { approved: "b-ok", denied: "b-bad", expired: "b-muted" };
// The shortest secret a card takes: one shorter would stand bare where a command prints it (held_secrets.py, item 857).
const SECRET_LEAST_CHARS = 8;
const STATUS_LABEL = { approved: t("approvals.approved"), denied: t("approvals.denied"), expired: t("approvals.expired"), pending: t("approvals.waitingForYou") };
// The note the daemon gives a step nobody answered in time (agent/approvals.py): the settled line says it in its words.
const EXPIRED_NOTES = new Set(["no decision before the deadline", "kept past its time"]);
// What becomes of the answer to a question a run left (`leftNote` of a card): it carries the work on in the run's
// conversation, or, for a session of a campaign, reaches the session while it works and the next session after that.
export const LEFT_IN_RUN = t("approvals.theTaskWentOnWithout");
export const LEFT_IN_CAMPAIGN = t("approvals.theSessionWentOnWithout");
// What a step kept for the owner (item 482) says: nobody answered it in time, so the run went on without it.
const KEPT_NOTE = t("approvals.nobodyAnsweredInTimeSo");
// What a question kept for the owner's yes (item 483) says: Allow once lets the run do this exact step once.
const KEPT_ASK_NOTE = t("approvals.nobodyAnsweredInTimeSo2");
// The same for a step a session of a campaign kept (item 484): the answer reaches the session while it works, and the
// campaign's next session after that.
const KEPT_IN_CAMPAIGN = t("approvals.nobodyAnsweredInTimeSo4");
const KEPT_ASK_IN_CAMPAIGN = t("approvals.nobodyAnsweredInTimeSo5");

/** The common shape of an approval, from a stored record. */
export function fromRecord(record) {
  return {
    id: record.id,
    runId: record.run_id,
    tool: record.tool,
    summary: record.summary,
    severity: record.severity,
    args: record.args,
    status: record.status,
    note: record.note,
    keptUntil: record.kept_until ?? null,
    // The campaign a kept step belongs to (item 484), a session's helper's too (item 485).
    scopeCampaignId: record.scope_campaign_id ?? null,
  };
}

/** The common shape of an approval, from an `approval_requested` event. */
export function fromEvent(runId, data) {
  return {
    id: data.approval_id,
    runId,
    tool: data.tool,
    summary: data.summary,
    severity: data.severity,
    args: data.args,
    status: "pending",
    note: "",
    keptUntil: null,
    scopeCampaignId: null,
  };
}

/**
 * The answer of a question card while it waits, and how to send it, by the card's node (item 2001): the asks panel
 * sends every question's answer with one Submit once each has one, as the phone's dock does.
 * @type {WeakMap<HTMLElement, { answer: () => string, send: () => void }>}
 */
export const questionControls = new WeakMap();

// What joins the labels of the options chosen in one answer (`CHOICES_JOIN` of dotagent.agent.questions).
const CHOICES_JOIN = "; ";

/**
 * The options a question offers (item 192): each with its label, what choosing it leads to (empty when the label says
 * it), and whether Dot recommends it. A question asked before then stored its options as words alone.
 */
function questionOptions(args) {
  if (!Array.isArray(args.options)) {
    return [];
  }
  return args.options.map((option) =>
    typeof option === "string"
      ? { label: option, description: "", recommended: false }
      : {
          label: String(option.label),
          description: typeof option.description === "string" ? option.description : "",
          recommended: option.recommended === true,
        },
  );
}

/**
 * The one text that answers a question when the user chose the options labelled `chosen` and wrote `written`: the
 * labels joined, then what was written on a line of its own (`chosen_answer` of dotagent.agent.questions).
 */
function chosenAnswer(chosen, written) {
  return [chosen.join(CHOICES_JOIN), written.trim()].filter((part) => part !== "").join("\n");
}

/** An option's words: its label, marked when Dot recommends it, over what choosing it leads to. */
function choiceText(option) {
  return el(
    "span",
    { class: "choice-text" },
    el("span", { class: "choice-label" }, option.label, option.recommended ? el("span", { class: "badge b-info" }, t("approvals.recommended")) : null),
    option.description === "" ? null : el("span", { class: "choice-description" }, option.description),
  );
}

// The card of a task past a cap of what it spends (item 1731, agent/budget.py): Continue lets as many tokens again
// go, Stop ends the task. It is never remembered, has no rule and no note.
const BUDGET = "budget";

function titleOf(severity) {
  if (severity === "question") {
    return t("approvals.dotIsAskingYou");
  }
  if (severity === "secret") {
    return t("approvals.dotAsksForASecret");
  }
  if (severity === "handoff") {
    return t("approvals.dotNeedsYouToTake");
  }
  if (severity === BUDGET) {
    return t("approvals.aTaskReachedItsBudget");
  }
  return t("approvals.approvalNeeded");
}

function iconOf(severity) {
  if (severity === "question") {
    return "help";
  }
  if (severity === "secret") {
    return "key";
  }
  return severity === "handoff" ? "hand" : "shield";
}

const TAKEOVER_IDLE = t("approvals.dotSWindowComesTo");
const TAKEOVER_HELD = t("approvals.youHaveTheBrowserDo");

/** On the hand-off of a browser step, the offer of the live view: take the browser over, do the step, give it back;
 * with `onUseHere`, also using the page from this screen, which opens it over the whole page (item 475). */
function takeoverOffer(runId, onUseHere) {
  let held = false;
  const button = el("button", { class: "btn sm", type: "button" }, icon("hand"), t("approvals.takeTheBrowserOver"));
  const text = el("span", { class: "small muted" }, TAKEOVER_IDLE);
  const here =
    onUseHere === null
      ? null
      : el("div", { class: "row takeover-offer" }, el("button", { class: "btn sm", type: "button", onclick: () => onUseHere() }, t("approvals.useItHere")), el("span", { class: "small muted" }, t("approvals.orUseThePageFrom")));
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api.post(`/runs/${runId}/${held ? "handback" : "takeover"}`, {});
      held = !held;
      button.replaceChildren(icon("hand"), held ? t("approvals.returnControl") : t("approvals.takeTheBrowserOver"));
      text.textContent = held ? TAKEOVER_HELD : TAKEOVER_IDLE;
    } catch (error) {
      toastError(error);
    } finally {
      button.disabled = false;
    }
  });
  const mac = el("div", { class: "row takeover-offer" }, button, text);
  return here === null ? mac : el("div", { class: "stack tight" }, mac, here);
}

// What the policy asks the owner about in every mode that asks, whatever their rules say (item 429): a rule offered for
// it would never apply. A yolo run asks nothing else (item 443) but a payment, in a chat the owner opened or one of Dot's
// own initiative (item 2069).
// A login filled from the password manager (item 493) is asked as a message is, in every mode but yolo, and a change of
// Dot's own files (`self_change`, item 398) before any rule is read (`own_files` in policy/engine.py, item 559).
const GUARDED_FLAGS = new Set([
  "financial",
  "credential",
  "message_send",
  "public_post",
  "vault_fill",
  "self_change",
]);
// The words of the question a chat Dot opened on its own initiative asks of a message, a post or a release (item 850,
// `_INITIATIVE_REASON` in policy/engine.py, which the card's summary ends with): the owner's in every mode but yolo,
// whatever their rules say, so no rule is offered for it; the same step elsewhere keeps its offer.
const INITIATIVE_WORDS = "On its own initiative, Dot asks before";
// The line under a card that no rule answers, which says why Dot asks.
const INITIATIVE_LINE = t("approvals.inAChatDotOpened");
const OWNERS_LINE = t("approvals.aPaymentAMessageOr");
const GUARDED_LINE = t("approvals.dotAlwaysAsksYouAbout");

/** Whether the owner answers `approval` in every mode that asks, whatever their rules say (item 429). */
function guarded(approval) {
  const flags = approval.args.flags;
  return Array.isArray(flags) && flags.some((flag) => typeof flag === "string" && GUARDED_FLAGS.has(flag));
}

// What a "don't ask again in this chat" never covers (item 866, `NEVER_REMEMBERED` in policy/chat_permits.py): money, a
// credential or a password manager's fill, Dot's own files. Such a card shows no box.
const NEVER_REMEMBERED = new Set(["financial", "credential", "vault_fill", "self_change"]);

// The words of the question a yolo run asks of a payment, in a chat the owner opened or one of Dot's own initiative
// (item 2069, `PAYMENT_REASON` in policy/engine.py).
const OWNERS_WORDS = "In yolo too, Dot asks you before";

/** Whether `approval` is the question of a chat Dot opened on its own initiative (item 850), asked in every
 * approval mode but yolo. */
function initiativeAsked(approval) {
  return typeof approval.summary === "string" && approval.summary.includes(INITIATIVE_WORDS);
}

/** Whether the owner's yes to `approval` may hold for its kind in its chat (item 866): not a question, a hand-off or a
 * card kept for later (`plain` false), not a step of a chat of Dot's own initiative, and none of its flags is one that
 * is never remembered. */
function rememberable(approval, plain) {
  // A step that reaches a shell other than a command of the shell tool (typed lines, an AppleScript) is never remembered.
  const typedShell = approval.args.kind === "shell" && approval.tool !== "shell";
  if (plain || typedShell || approval.severity === "secret" || initiativeAsked(approval)) {
    return false;
  }
  const flags = approval.args.flags;
  return !(Array.isArray(flags) && flags.some((flag) => typeof flag === "string" && NEVER_REMEMBERED.has(flag)));
}

/** Whether `approval` is the question a yolo run asks of a payment (item 2069). */
function ownersAsked(approval) {
  return typeof approval.summary === "string" && approval.summary.includes(OWNERS_WORDS);
}

/**
 * The way from an approval to a rule (item 110): the Rules page with what Dot wanted to do in the box "When Dot wants to"
 * (the tool and the action) and "Allow on its own" as the choice, for the owner to change before Dot drafts the rule
 * (item 622).
 */
function ruleOffer(approval) {
  const action = typeof approval.args.summary === "string" ? approval.args.summary : approval.summary;
  const wants = `use ${approval.tool}: ${clip(action, 300)}`;
  return el(
    "a",
    { class: "approval-rule", href: `#/settings/rules?wants=${encodeURIComponent(wants)}&should=allow` },
    icon("shield"),
    t("approvals.makeARuleOfThis"),
  );
}

/** The parts of a card that are there: `replaceChildren` would turn a part left out (null) into the text "null". */
function present(...parts) {
  return parts.filter((part) => part !== null);
}

/** The chips that say what the step counts as, left out where the card's text says it already (item 872). */
function chipsOf(args, said) {
  const named = [];
  if (typeof args.kind === "string") {
    named.push([args.kind, capital(kindWords(args.kind))]);
  }
  if (Array.isArray(args.flags)) {
    for (const flag of args.flags) {
      named.push([String(flag), capital(flagWords(String(flag)))]);
    }
  }
  const fresh = new Set(unsaid(named.map(([, words]) => words), said));
  const chips = named.filter(([, words]) => fresh.has(words)).map(([title, words]) => el("span", { class: "chip", title }, words));
  return chips.length === 0 ? null : el("div", { class: "cluster" }, chips);
}

/** What the step acts on or runs, whole, in the boxes of code the card keeps in view (the command is the card's one
 * exhibit; the chips and the policy's words are folded away, item 22 of the 2026-11-26 review). */
function targetBlocks(args) {
  const target = typeof args.target === "string" && args.target !== "" ? args.target : null;
  // What the step runs where its target says only where (a page's script, item 985): asked of the owner because the
  // review did not read it whole, so the card shows it whole.
  const body = typeof args.body === "string" && args.body !== "" ? args.body : null;
  if (target === null && body === null) {
    return null;
  }
  return el(
    "div",
    { class: "stack tight" },
    target === null ? null : targetBlock(target, args.kind === "shell" ? t("approvals.command") : t("approvals.target"), t("approvals.whatTheActionActsOn")),
    body === null ? null : targetBlock(body, t("approvals.script"), t("approvals.whatTheActionRuns")),
  );
}

/** The fold under a card: what the step counts as, its risk and the policy's own words for asking; null when none. */
function foldedDetails(chips, severity, policyWords) {
  const risk = severity === "high" || severity === "medium" || severity === "low" ? t("approvals.riskLabel", { level: riskWords(severity) }) : null;
  if (chips === null && risk === null && policyWords === null) {
    return null;
  }
  return el(
    "details",
    // A high-risk step's fold stands open: its risk and what it counts as are read before Approve, not behind a press.
    { class: "approval-fold", open: severity === "high" },
    el("summary", null, t("approvals.details")),
    el("div", { class: "stack tight" }, chips, risk === null ? null : el("div", { class: "small muted" }, risk), policyWords === null ? null : el("div", { class: "small muted" }, t("approvals.whyPolicy", { reason: policyWords }))),
  );
}

// The whole target, as the action would act on it (item 242): a long command wraps, and scrolls within the card past a
// dozen lines. It is the box of code a chat shows everywhere else (item 537), so the owner can copy what is asked.
function targetBlock(target, label, name) {
  const block = codeBlock({ text: target, label, wrapped: true, className: "approval-target" });
  const text = block.querySelector("pre");
  text.tabIndex = 0;
  text.setAttribute("aria-label", name);
  return block;
}

/** What a kept step's card says of it: what I did it does, and until when the step waits. */
function keptNote(handoff, keptUntil, inCampaign) {
  const said = inCampaign ? (handoff ? KEPT_IN_CAMPAIGN : KEPT_ASK_IN_CAMPAIGN) : handoff ? KEPT_NOTE : KEPT_ASK_NOTE;
  return t("approvals.saidItWaitsUntilDatetime", { said, dateTime: dateTime(keptUntil) });
}

/**
 * A card for one approval. `onSettled(decided)` runs once the daemon answered a decision: `decided` is the decided
 * record when it took the decision, null when it refused it (made already, or not possible now). `leftNote` says what
 * becomes of the answer to a question the run left (LEFT_IN_RUN or LEFT_IN_CAMPAIGN). `chat` is the chat the card stands in
 * (its thread), which an answer made with no link is kept under, in order with the rest of what was done in it (item 1523);
 * a card with none keeps it under its run. `onUseHere`, where the card
 * stands beside the run's view, is offered on the hand-off of a browser step: use the page from this screen (item 475).
 * Returns the node and `settle(status, note)`, which turns the card into its decided form.
 */
export function approvalCard({ approval, heading, onSettled, leftNote, onUseHere, chat = null }) {
  const node = el("div", { class: "approval" });
  const isQuestion = approval.severity === "question";
  const isHandoff = approval.severity === "handoff";
  const isBudget = approval.severity === BUDGET;
  // A card that asks for a key, a token or a password (item 857): its value goes to its own route alone, never as a note.
  const isSecret = approval.severity === "secret";
  // An answer to a question the run left goes on with the work in the run's conversation, so it needs words.
  const isLeft = approval.status === "left";
  // What a card's own controls paint again after the shared ones are given back (the secret's Save, by its length).
  let repaint = () => {};
  // A step handed to the owner that nobody answered in time, kept for them until it lapses (item 482).
  const isKept = isLeft && approval.keptUntil !== null;
  let busy = false;

  async function decide(approve, note, remember = false) {
    if (busy) {
      return;
    }
    busy = true;
    node.querySelectorAll("button, input").forEach((control) => {
      control.disabled = true;
    });
    let decided;
    try {
      // With no link the answer is kept and sent once the link is back, in order with what else was done in the chat
      // (item 1523); a card that was closed meanwhile is told then.
      const answer = remember ? { approve, note, remember } : { approve, note };
      decided = await viaOutbox({ kind: "answer", chat: chat ?? approval.runId, method: "POST", path: `/approvals/${approval.id}/decision`, body: answer }, () =>
        api.post(`/approvals/${approval.id}/decision`, answer),
      );
      if (decided !== null && typeof decided === "object" && "kept" in decided) {
        node.append(el("div", { class: "note outbox-note", role: "status" }, icon("clock"), el("span", null, t("outbox.answerWaiting"))));
        return;
      }
    } catch (error) {
      busy = false;
      node.querySelectorAll("button, input").forEach((control) => {
        control.disabled = false;
      });
      repaint();
      toastError(error);
      if (error.status === 409) {
        onSettled(null);
      }
      return;
    }
    onSettled(decided);
  }

  function renderPending() {
    node.className = `approval${approval.severity === "high" ? " high" : ""}${isQuestion ? " question" : ""}${isHandoff ? " handoff" : ""}`;
    // Each thing once (item 872): the command in its box, why Dot asks in the line under it that says so.
    const asks = isQuestion || isHandoff ? null : initiativeAsked(approval) ? INITIATIVE_LINE : ownersAsked(approval) ? OWNERS_LINE : guarded(approval) ? GUARDED_LINE : null;
    const target = typeof approval.args.target === "string" ? approval.args.target : "";
    // What the line says is left out of the words; any other reason, and the review model's warning, stays.
    const said = asks === INITIATIVE_LINE ? FIXED_REASONS.initiative : asks === OWNERS_LINE ? FIXED_REASONS.owners : "";
    const words = isQuestion ? null : cardWords(approval.summary, target, said);
    const text = isQuestion && typeof approval.args.question === "string" ? approval.args.question : words === null ? approval.summary : words.text;
    // Why Dot asks, in one plain sentence when the policy's reason names a preset; its own words are folded under Details
    // (item 22 of the 2026-11-26 review). Any other reason (a rule's, the review model's warning) is said as it is.
    const reason = words === null ? "" : words.reason;
    const plain = reason === "" ? null : plainReason(reason);
    const why = reason === "" ? null : plain ?? reason;
    const policyWords = plain === null ? null : reason;
    const choices = isQuestion ? questionOptions(approval.args) : [];
    // Several options may be chosen; one is otherwise. A pick is a card ringed and pressed, sent with the answer
    // (item 2001): a tap no longer sends it at once, so the panel's one Submit sends every question's answer together.
    const several = choices.length > 0 && approval.args.multiple === true;
    const picked = new Set();
    const note = el("input", {
      class: "input",
      type: "text",
      maxlength: "2000",
      placeholder: !isQuestion ? t("approvals.noteForDotOptional") : choices.length > 0 ? t("approvals.orWriteYourOwnAnswer") : t("approvals.yourAnswer"),
      "aria-label": isQuestion ? t("approvals.yourAnswer") : t("approvals.noteForDot"),
    });
    // "Don't ask again in this chat" (item 866): on a question of the policy open now, of a step that may be
    // remembered, in a chat that is not of Dot's own initiative.
    const remember = rememberable(approval, isQuestion || isHandoff || isLeft || isBudget) ? el("input", { type: "checkbox", class: "approval-remember-box" }) : null;
    // An answer to a question is the options picked and what was written; it is never empty.
    const answerNow = () => chosenAnswer(choices.filter((option) => picked.has(option.label)).map((option) => option.label), note.value);
    const submit = (approve) => {
      const answer = isQuestion && approve ? answerNow() : note.value.trim();
      if (isQuestion && approve && answer === "") {
        note.focus();
        return;
      }
      decide(approve, answer, approve && remember !== null && remember.checked);
    };
    // Enter sends an answer to a question; on any other card it does nothing, so a note that says "no" never
    // approves the action (item 116).
    note.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && isQuestion) {
        event.preventDefault();
        submit(true);
      }
    });
    // Each option a full-width card: a tap picks it (one of them, or several), a second tap lets it go.
    const optionButtons = choices.map((option) =>
      el("button", { class: "choice", type: "button", "aria-pressed": "false", "data-label": option.label }, choiceText(option), el("span", { class: "choice-mark", "aria-hidden": "true" }, icon("check"))),
    );
    const changed = () => {
      for (const button of optionButtons) {
        button.setAttribute("aria-pressed", String(picked.has(button.dataset.label ?? "")));
      }
      if (isQuestion) {
        yesButton.disabled = answerNow() === "";
        node.dispatchEvent(new Event("answerchange", { bubbles: true }));
      }
    };
    optionButtons.forEach((button, index) => {
      button.addEventListener("click", () => {
        const label = choices[index].label;
        if (picked.has(label)) {
          picked.delete(label);
        } else {
          if (!several) {
            picked.clear();
          }
          picked.add(label);
        }
        changed();
      });
    });
    note.addEventListener("input", () => changed());
    const options = choices.length === 0 ? null : el("div", { class: `choices${several ? " several" : ""}` }, optionButtons);
    const more = typeof approval.args.more_words === "string" ? ` (+${approval.args.more_words})` : "";
    const yes = isQuestion ? t("approvals.sendAnswer") : isHandoff ? t("approvals.iDidIt") : isBudget ? t("approvals.continueMore", { more }) : isKept ? t("approvals.allowOnce") : t("approvals.approve");
    const no = isQuestion ? t("approvals.skip") : isHandoff ? t("approvals.skipThisStep") : isBudget ? t("approvals.stop") : isKept ? t("approvals.skip") : t("approvals.deny");
    note.hidden = isBudget;
    const yesButton = el("button", { class: "btn primary approval-yes", type: "button", onclick: () => submit(true) }, yes);
    if (isQuestion) {
      yesButton.disabled = true;
      questionControls.set(node, { answer: answerNow, send: () => submit(true) });
    }
    // A yes with a note is the note first (item 1140): the daemon holds the step until the run read it, and the step
    // goes through once if the note still calls for it. The button says so while a note is written.
    if (!isQuestion && !isHandoff && !isLeft && !isBudget) {
      note.addEventListener("input", () => {
        const noted = note.value.trim() !== "";
        yesButton.textContent = noted ? t("approvals.sendNote") : yes;
        yesButton.title = noted ? t("approvals.dotReadsYourNoteFirst") : "";
      });
    }
    node.replaceChildren(
      ...present(
        el(
          "div",
          { class: "approval-head" },
          icon(iconOf(approval.severity)),
          el("span", null, isKept ? t("approvals.keptForYou") : isLeft ? t("approvals.dotAskedYou") : titleOf(approval.severity)),
          el("span", { class: "spacer" }),
          heading,
        ),
        // A question's topic, as small capitals over its words (item 2001, "APPROVAL"), when the run gave one.
        isQuestion && typeof approval.args.header === "string" && approval.args.header !== "" ? el("div", { class: "approval-topic" }, approval.args.header) : null,
        text === "" ? null : el("div", { class: "approval-text" }, text),
        why === null ? null : el("div", { class: "small muted approval-why" }, why),
        several ? el("div", { class: "small muted" }, t("approvals.chooseOneOrMoreThen")) : null,
        isLeft ? el("div", { class: "small muted" }, isKept ? keptNote(isHandoff, approval.keptUntil, approval.scopeCampaignId !== null || leftNote === LEFT_IN_CAMPAIGN) : leftNote) : null,
        // A chip is left out only when the words are the step's own, the command cut out of them, never a reason's.
        isQuestion ? options : isBudget ? null : targetBlocks(approval.args),
        isQuestion || isHandoff || isBudget ? null : foldedDetails(chipsOf(approval.args, words?.cut ? text : ""), approval.severity, policyWords),
        isQuestion || isHandoff || isBudget ? null : asks !== null ? el("div", { class: "small muted" }, asks) : ruleOffer(approval),
        isHandoff && approval.status === "pending" && approval.tool.startsWith("browser_") ? takeoverOffer(approval.runId, onUseHere) : null,
        el(
          "div",
          { class: "approval-actions" },
          note,
          remember === null ? null : el("label", { class: "approval-remember", title: t("approvals.stepsOfThisKindThe") }, remember, t("approvals.donTAskAgainIn")),
          el("button", { class: "btn", type: "button", onclick: () => submit(false) }, no),
          yesButton,
        ),
      ),
    );
  }

  /** The card of a secret (item 857): what it is and why, in the run's words, a box that shows no letter of it, Save
   * securely, which sends the value to the daemon's own route for it (held for this chat's commands, written nowhere
   * else), and Decline. The box is emptied once it is sent. */
  function renderSecret() {
    node.className = "approval secret";
    const label = typeof approval.args.label === "string" ? approval.args.label : approval.summary;
    const why = typeof approval.args.why === "string" ? approval.args.why : "";
    const value = el("input", {
      class: "input mono",
      type: "password",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      maxlength: "8192",
      placeholder: label,
      "aria-label": label,
    });
    const save = el("button", { class: "btn primary", type: "button", disabled: true }, t("approvals.saveSecurely"));
    const decline = el("button", { class: "btn ghost", type: "button", onclick: () => decide(false, "") }, t("approvals.decline"));
    // A value shorter than a card holds (item 1073: the owner typed a 4-digit code and Save did nothing, saying
    // nothing): the card says why, and a short code goes as a message instead, which Dot then sees.
    // A card left for the owner (no run waits on it): its answer reaches no run, so a short code is written in the chat,
    // whose next run reads it.
    const asMessage = isLeft
      ? null
      : el(
          "button",
          { class: "btn sm", type: "button", title: t("approvals.sendItInWordsDot"), onclick: () => decide(false, value.value.trim()) },
          t("approvals.sendAsAMessage"),
        );
    const instead = isLeft ? t("approvals.aShortCodeAnSms") : t("approvals.aShortCodeAnSms2");
    const short = el(
      "div",
      { class: "hint secret-short", role: "status", hidden: true },
      el("span", { class: "grow" }, t("approvals.aSecretIsAtLeast", { SECRET_LEAST_CHARS, instead })),
      asMessage,
    );
    function paintLength() {
      const typed = value.value.trim().length;
      save.disabled = typed < SECRET_LEAST_CHARS;
      short.hidden = typed === 0 || typed >= SECRET_LEAST_CHARS;
    }
    value.addEventListener("input", paintLength);
    repaint = paintLength;
    async function give() {
      if (busy) {
        return;
      }
      if (value.value.trim().length < SECRET_LEAST_CHARS) {
        paintLength();
        value.focus();
        return;
      }
      busy = true;
      node.querySelectorAll("button, input").forEach((control) => {
        control.disabled = true;
      });
      let decided;
      try {
        decided = await api.post(`/approvals/${approval.id}/secret`, { value: value.value });
      } catch (error) {
        busy = false;
        node.querySelectorAll("button, input").forEach((control) => {
          control.disabled = false;
        });
        paintLength();
        toastError(error);
        if (error.status === 409) {
          onSettled(null);
        }
        return;
      }
      value.value = "";
      onSettled(decided);
    }
    save.addEventListener("click", () => give());
    value.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        give();
      }
    });
    node.replaceChildren(
      ...present(
        el("div", { class: "approval-head" }, icon("key"), el("span", null, titleOf("secret")), el("span", { class: "spacer" }), heading),
        el("div", { class: "approval-text secret-label" }, label),
        why === "" ? null : el("div", { class: "secret-why" }, why),
        el("div", { class: "secret-entry" }, value, save),
        short,
        el(
          "div",
          { class: "secret-foot" },
          icon("shield"),
          el("span", { class: "grow" }, t("approvals.keptOutOfTheChat")),
          decline,
        ),
      ),
    );
  }

  function settle(status, note) {
    if (isSecret) {
      node.className = "approval decided";
      node.replaceChildren(
        el(
          "div",
          { class: "approval-head" },
          icon("key"),
          el("span", null, typeof approval.args.label === "string" ? approval.args.label : approval.summary),
          el("span", { class: `badge ${STATUS_BADGE[status] ?? "b-muted"}` }, status === "approved" ? t("approvals.savedSecurely") : (STATUS_LABEL[status] ?? status)),
          el("span", { class: "spacer" }),
          heading,
        ),
      );
      return;
    }
    // A step settled is one quiet line (item 891, the owner's word on a card that expired: "this looks very ugly"): what
    // it was, its command on one line, and how it ended; the whole command and why Dot asked a click away. It said
    // the command twice and the reason twice, at the size of a card that waits for an answer.
    node.className = "approval decided compact";
    const target = typeof approval.args.target === "string" ? approval.args.target : "";
    // The action's label ("Run"): a ": " before why Dot asks, with the command right after it (its first word, as a
    // summary folds a command's spaces). A ": " in the reasons is none (the pre-push review: "Write /a.txt — … The
    // review model passed this on to you: …").
    const colon = approval.summary.indexOf(": ");
    const mark = approval.summary.indexOf(" — ");
    const first = target.trim().split(/\s+/)[0] ?? "";
    const labelled = colon > 0 && (mark === -1 || colon < mark) && first !== "" && approval.summary.startsWith(first, colon + 2);
    const label = labelled ? approval.summary.slice(0, colon) : "";
    const timedOut = status === "expired" && (note === "" || EXPIRED_NOTES.has(note));
    const outcome = STATUS_LABEL[status] ?? status;
    const why = timedOut ? t("approvals.nobodyAnsweredInTimeSo3") : "";
    // What the step does (the policy's brackets), and a reason of its own; the fixed sentences of why Dot asks are no
    // news once it is settled. A summary whose command could not be cut out of it (a folder, several lines, a long
    // one) is kept whole but for that sentence: its folder and its reasons are in it (the pre-push review).
    const fixed = Object.values(FIXED_REASONS).find((sentence) => approval.summary.includes(` — ${sentence}`)) ?? "";
    const words = isQuestion ? null : cardWords(approval.summary, target, fixed);
    const plain = isQuestion || words?.cut ? null : cardWords(approval.summary, "", fixed);
    // The line: the question, the label beside its command, or the action of a summary with no label, without the
    // fixed sentence (the pre-push review: "Buy … (real money) — In yolo too, Dot asks you …").
    const what = isQuestion && typeof approval.args.question === "string" ? approval.args.question : label !== "" ? label : (plain?.text ?? approval.summary);
    const parts = words === null ? [] : words.cut ? [words.text, words.reason] : [plain?.text ?? "", plain?.reason ?? ""];
    // What the line says already is not said again under it (a summary with no label is the line itself).
    const reason = parts.filter((part) => part !== "" && part !== what).join(". ");
    const own = note !== "" && !timedOut;
    const whole = isQuestion || target === "" || label === "";
    // The whole command under Details, unless the line says it already, word for word.
    const shown = target !== "" && !(whole && what.includes(target)) ? target : "";
    node.replaceChildren(
      ...present(
        el(
          "div",
          { class: "approval-line" },
          icon(iconOf(approval.severity)),
          // A question, or a summary with no command beside it, is the line's one text: it wraps, whole, as nothing
          // under Details says it (the pre-push review); a label beside its command stays one short word.
          el("span", { class: whole ? "approval-what whole" : "approval-what" }, whole ? clip(what, 400) : clip(what, 160)),
          // A summary with no label says its target already ("Fetch https://…"): it is not said again beside it.
          target === "" || isQuestion || label === "" ? null : el("code", { class: "approval-command", title: target }, clip(target.replace(/\s+/g, " "), 160)),
          el("span", { class: `badge ${STATUS_BADGE[status] ?? "b-muted"}`, title: why === "" ? outcome : why }, outcome),
          heading,
        ),
        own ? el("div", { class: "small approval-note" }, el("span", { class: "muted" }, isQuestion ? t("approvals.answer") : t("approvals.note")), note) : null,
        shown === "" && reason === "" && why === ""
          ? null
          : el(
              "details",
              { class: "approval-more" },
              el("summary", null, t("approvals.details")),
              why === "" ? null : el("div", { class: "small muted" }, why),
              shown === "" ? null : el("pre", { class: "approval-command-whole" }, shown),
              reason === "" ? null : el("div", { class: "small muted" }, reason),
            ),
      ),
    );
  }

  if ((approval.status === "pending" || isLeft) && isSecret) {
    renderSecret();
  } else if (approval.status === "pending" || isLeft) {
    renderPending();
  } else {
    settle(approval.status, approval.note);
  }
  return { node, settle };
}
