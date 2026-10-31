// The card that asks the user: an approval of an action, a hand-off of a step, or a question from the agent, which the
// run waits for (`pending`) or left for the user and went on without (`left`).

import { api } from "./api.js";
import { codeBlock } from "./codeblock.js";
import { clip, dateTime, el } from "./dom.js";
import { capital, flagWords, kindWords } from "./flagwords.js";
import { icon } from "./icons.js";
import { toastError } from "./ui.js";

const STATUS_BADGE = { approved: "b-ok", denied: "b-bad", expired: "b-muted" };
// The shortest secret a card takes: one shorter would stand bare where a command prints it (held_secrets.py, item 857).
const SECRET_LEAST_CHARS = 8;
const STATUS_LABEL = { approved: "Approved", denied: "Denied", expired: "Expired", pending: "Waiting for you" };
// What becomes of the answer to a question a run left (`leftNote` of a card): it carries the work on in the run's
// conversation, or, for a session of a campaign, reaches the session while it works and the next session after that.
export const LEFT_IN_RUN = "The task went on without your answer. Your answer goes to this conversation and carries the work on.";
export const LEFT_IN_CAMPAIGN = "The session went on without your answer. While it works, your answer reaches it; after that, the campaign's next session reads it, and starts right away while the campaign is active.";
// What a step kept for the owner (item 482) says: nobody answered it in time, so the run went on without it.
const KEPT_NOTE = "Nobody answered in time, so the task went on without this step. Once you have done it yourself, choose I did it to carry the work on in this conversation.";
// What a question kept for the owner's yes (item 483) says: Allow once lets the run do this exact step once.
const KEPT_ASK_NOTE = "Nobody answered in time, so the task went on without this step. Choose Allow once to carry the work on in this conversation, where Dot may do this exact step once.";
// The same for a step a session of a campaign kept (item 484): the answer reaches the session while it works, and the
// campaign's next session after that.
const KEPT_IN_CAMPAIGN = "Nobody answered in time, so the session went on without this step. Once you have done it yourself, choose I did it: it reaches the session while it works; after that, the campaign's next session reads it and starts right away while the campaign is active.";
const KEPT_ASK_IN_CAMPAIGN = "Nobody answered in time, so the session went on without this step. Choose Allow once: it reaches the session while it works; after that, the campaign's next session reads it, may do this exact step once, and starts right away while the campaign is active.";

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
    el("span", { class: "choice-label" }, option.label, option.recommended ? el("span", { class: "badge b-info" }, "Recommended") : null),
    option.description === "" ? null : el("span", { class: "choice-description" }, option.description),
  );
}

function titleOf(severity) {
  if (severity === "question") {
    return "Dot is asking you";
  }
  if (severity === "secret") {
    return "Dot asks for a secret";
  }
  if (severity === "handoff") {
    return "Dot needs you to take over this step";
  }
  return "Approval needed";
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

const TAKEOVER_IDLE = "Dot's window comes to the front and Dot waits while you have it.";
const TAKEOVER_HELD = "You have the browser. Do the step, return control, then say you did it.";

/** On the hand-off of a browser step, the offer of the live view: take the browser over, do the step, give it back;
 * with `onUseHere`, also using the page from this screen, which opens it over the whole page (item 475). */
function takeoverOffer(runId, onUseHere) {
  let held = false;
  const button = el("button", { class: "btn sm", type: "button" }, icon("hand"), "Take the browser over");
  const text = el("span", { class: "small muted" }, TAKEOVER_IDLE);
  const here =
    onUseHere === null
      ? null
      : el("div", { class: "row takeover-offer" }, el("button", { class: "btn sm", type: "button", onclick: () => onUseHere() }, "Use it here"), el("span", { class: "small muted" }, "Or use the page from this screen: it opens over the whole page and your taps go to it."));
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api.post(`/runs/${runId}/${held ? "handback" : "takeover"}`, {});
      held = !held;
      button.replaceChildren(icon("hand"), held ? "Return control" : "Take the browser over");
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
// it would never apply. A yolo run asks nothing else (item 443) but a payment, a message and a post in a chat the owner
// opened (item 860) and those of item 850.
// A login filled from the password manager (item 493) is asked as a message is, in every mode but yolo, and a change of
// Dot's own files (`self_change`, item 398) before any rule is read (`own_files` in policy/engine.py, item 559). So is
// an order with real money on the owner's trading account (`trade`, item 763).
const GUARDED_FLAGS = new Set([
  "financial",
  "credential",
  "message_send",
  "public_post",
  "vault_fill",
  "trade",
  "self_change",
]);
// The words of the question a chat Dot opened on its own initiative asks of a message, a post or a release (item 850,
// `_INITIATIVE_REASON` in policy/engine.py, which the card's summary ends with): the owner's in every mode, whatever
// their rules say, so no rule is offered for it; the same step elsewhere keeps its offer.
const INITIATIVE_WORDS = "On its own initiative, Dot asks before";

/** Whether the owner answers `approval` in every mode that asks, whatever their rules say (item 429). */
function guarded(approval) {
  const flags = approval.args.flags;
  return Array.isArray(flags) && flags.some((flag) => typeof flag === "string" && GUARDED_FLAGS.has(flag));
}

// What a "don't ask again in this chat" never covers (item 866, `NEVER_REMEMBERED` in policy/chat_permits.py): money, a
// trade, a credential or a password manager's fill, Dot's own files. Such a card shows no box.
const NEVER_REMEMBERED = new Set(["financial", "trade", "credential", "vault_fill", "self_change"]);

// The words of the question a yolo run of a chat the owner opened asks of a payment, a message or a post (item 860,
// `OWNERS_REASON` in policy/engine.py).
const OWNERS_WORDS = "In yolo too, Dot asks you before";

/** Whether `approval` is the question of a chat Dot opened on its own initiative (item 850), asked in YOLO too. */
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

/** Whether `approval` is the question a yolo run of a chat the owner opened asks (item 860). */
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
    "Make a rule of this",
  );
}

/** The parts of a card that are there: `replaceChildren` would turn a part left out (null) into the text "null". */
function present(...parts) {
  return parts.filter((part) => part !== null);
}

function technicalDetails(args) {
  const chips = [];
  if (typeof args.kind === "string") {
    chips.push(el("span", { class: "chip", title: args.kind }, capital(kindWords(args.kind))));
  }
  if (Array.isArray(args.flags)) {
    for (const flag of args.flags) {
      chips.push(el("span", { class: "chip", title: String(flag) }, capital(flagWords(String(flag)))));
    }
  }
  const target = typeof args.target === "string" && args.target !== "" ? args.target : null;
  if (chips.length === 0 && target === null) {
    return null;
  }
  return el(
    "div",
    { class: "stack tight" },
    chips.length > 0 ? el("div", { class: "cluster" }, chips) : null,
    target === null ? null : targetBlock(target, args.kind === "shell" ? "Command" : "Target"),
  );
}

// The whole target, as the action would act on it (item 242): a long command wraps, and scrolls within the card past a
// dozen lines. It is the box of code a chat shows everywhere else (item 537), so the owner can copy what is asked.
function targetBlock(target, label) {
  const block = codeBlock({ text: target, label, wrapped: true, className: "approval-target" });
  const text = block.querySelector("pre");
  text.tabIndex = 0;
  text.setAttribute("aria-label", "What the action acts on");
  return block;
}

/** What a kept step's card says of it: what I did it does, and until when the step waits. */
function keptNote(handoff, keptUntil, inCampaign) {
  const said = inCampaign ? (handoff ? KEPT_IN_CAMPAIGN : KEPT_ASK_IN_CAMPAIGN) : handoff ? KEPT_NOTE : KEPT_ASK_NOTE;
  return `${said} It waits until ${dateTime(keptUntil)}.`;
}

/**
 * A card for one approval. `onSettled(decided)` runs once the daemon answered a decision: `decided` is the decided
 * record when it took the decision, null when it refused it (made already, or not possible now). `leftNote` says what
 * becomes of the answer to a question the run left (LEFT_IN_RUN or LEFT_IN_CAMPAIGN). `onUseHere`, where the card
 * stands beside the run's view, is offered on the hand-off of a browser step: use the page from this screen (item 475).
 * Returns the node and `settle(status, note)`, which turns the card into its decided form.
 */
export function approvalCard({ approval, heading, onSettled, leftNote, onUseHere }) {
  const node = el("div", { class: "approval" });
  const isQuestion = approval.severity === "question";
  const isHandoff = approval.severity === "handoff";
  // A card that asks for a key, a token or a password (item 857): its value goes to its own route alone, never as a note.
  const isSecret = approval.severity === "secret";
  // An answer to a question the run left goes on with the work in the run's conversation, so it needs words.
  const isLeft = approval.status === "left";
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
      decided = await api.post(`/approvals/${approval.id}/decision`, remember ? { approve, note, remember } : { approve, note });
    } catch (error) {
      busy = false;
      node.querySelectorAll("button, input").forEach((control) => {
        control.disabled = false;
      });
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
    const text = isQuestion && typeof approval.args.question === "string" ? approval.args.question : approval.summary;
    const choices = isQuestion ? questionOptions(approval.args) : [];
    // Several options may be chosen: each is ticked, and Send answer sends them with what was written.
    const several = choices.length > 0 && approval.args.multiple === true;
    const note = el("input", {
      class: "input",
      type: "text",
      maxlength: "2000",
      placeholder: !isQuestion ? "Note for Dot (optional)" : choices.length > 0 ? "Or write your own answer" : "Your answer",
      "aria-label": isQuestion ? "Your answer" : "Note for Dot",
    });
    const ticks = several ? choices.map((option) => el("input", { type: "checkbox", value: option.label })) : [];
    // "Don't ask again in this chat" (item 866): on a question of the policy open now, of a step that may be
    // remembered, in a chat that is not of Dot's own initiative.
    const remember = rememberable(approval, isQuestion || isHandoff || isLeft) ? el("input", { type: "checkbox", class: "approval-remember-box" }) : null;
    // An answer to a question is the options ticked and what was written; it is never empty.
    const submit = (approve) => {
      const answer = isQuestion && approve ? chosenAnswer(ticks.filter((tick) => tick.checked).map((tick) => tick.value), note.value) : note.value.trim();
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
    // One option chosen alone is the answer: its row sends it.
    const options =
      choices.length === 0
        ? null
        : el(
            "div",
            { class: "choices" },
            several
              ? choices.map((option, index) => el("label", { class: "choice" }, ticks[index], choiceText(option)))
              : choices.map((option) =>
                  el(
                    "button",
                    {
                      class: "choice",
                      type: "button",
                      onclick: () => {
                        note.value = option.label;
                        submit(true);
                      },
                    },
                    choiceText(option),
                  ),
                ),
          );
    const yes = isQuestion ? "Send answer" : isHandoff ? "I did it" : isKept ? "Allow once" : "Approve";
    const no = isQuestion ? "Skip" : isHandoff ? "Skip this step" : isKept ? "Skip" : "Deny";
    node.replaceChildren(
      ...present(
        el(
          "div",
          { class: "approval-head" },
          icon(iconOf(approval.severity)),
          el("span", null, isKept ? "Kept for you" : isLeft ? "Dot asked you" : titleOf(approval.severity)),
          isQuestion || isHandoff ? null : el("span", { class: `badge ${approval.severity === "high" ? "b-bad" : "b-warn"}` }, approval.severity),
          el("span", { class: "spacer" }),
          heading,
        ),
        el("div", { class: "approval-text" }, text),
        several ? el("div", { class: "small muted" }, "Choose one or more, then send them.") : null,
        isLeft ? el("div", { class: "small muted" }, isKept ? keptNote(isHandoff, approval.keptUntil, approval.scopeCampaignId !== null || leftNote === LEFT_IN_CAMPAIGN) : leftNote) : null,
        isQuestion ? options : technicalDetails(approval.args),
        isQuestion || isHandoff
          ? null
          : initiativeAsked(approval)
            ? el("div", { class: "small muted" }, "In a chat Dot opened on its own initiative, Dot always asks you about this, YOLO too. No rule can turn that off.")
            : ownersAsked(approval)
              ? el("div", { class: "small muted" }, "A payment, a message or mail sent for you and a public post wait for you in YOLO too. No rule can turn that off.")
              : guarded(approval)
                ? el("div", { class: "small muted" }, "Dot always asks you about this in every mode that asks. No rule can turn that off.")
                : ruleOffer(approval),
        isHandoff && approval.status === "pending" && approval.tool.startsWith("browser_") ? takeoverOffer(approval.runId, onUseHere) : null,
        el(
          "div",
          { class: "approval-actions" },
          note,
          remember === null ? null : el("label", { class: "approval-remember", title: "Steps of this kind (the same tool and place) go without asking in this chat" }, remember, "Don't ask again in this chat"),
          el("button", { class: "btn", type: "button", onclick: () => submit(false) }, no),
          el("button", { class: "btn primary", type: "button", onclick: () => submit(true) }, yes),
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
    const save = el("button", { class: "btn primary", type: "button" }, "Save securely");
    const decline = el("button", { class: "btn ghost", type: "button", onclick: () => decide(false, "") }, "Decline");
    async function give() {
      if (busy) {
        return;
      }
      if (value.value.trim().length < SECRET_LEAST_CHARS) {
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
        el(
          "div",
          { class: "secret-foot" },
          icon("shield"),
          el("span", { class: "grow" }, "Kept out of the chat and out of Dot's sight: this chat's commands read it for 24 hours."),
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
          el("span", { class: `badge ${STATUS_BADGE[status] ?? "b-muted"}` }, status === "approved" ? "Saved securely" : (STATUS_LABEL[status] ?? status)),
          el("span", { class: "spacer" }),
          heading,
        ),
      );
      return;
    }
    node.className = "approval decided";
    const text = isQuestion && typeof approval.args.question === "string" ? approval.args.question : approval.summary;
    node.replaceChildren(
      ...present(
        el(
          "div",
          { class: "approval-head" },
          icon(iconOf(approval.severity)),
          el("span", null, isQuestion ? "Question" : isHandoff ? "Hand-off" : "Approval"),
          el("span", { class: `badge ${STATUS_BADGE[status] ?? "b-muted"}` }, STATUS_LABEL[status] ?? status),
          el("span", { class: "spacer" }),
          heading,
        ),
        el("div", { class: "approval-text muted" }, clip(text, 400)),
        note === "" ? null : el("div", { class: "small approval-note" }, el("span", { class: "muted" }, isQuestion ? "Answer: " : "Note: "), note),
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
