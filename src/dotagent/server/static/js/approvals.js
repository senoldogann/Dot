// The card that asks the user: an approval of an action, a hand-off of a step, or a question from the agent, which the
// run waits for (`pending`) or left for the user and went on without (`left`).

import { api } from "./api.js";
import { clip, dateTime, el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

const STATUS_BADGE = { approved: "b-ok", denied: "b-bad", expired: "b-muted" };
const STATUS_LABEL = { approved: "Approved", denied: "Denied", expired: "Expired", pending: "Waiting for you" };
// What becomes of the answer to a question a run left (`leftNote` of a card): it carries the work on in the run's
// conversation, or, for a session of a campaign, reaches the session while it works and the next session after that.
export const LEFT_IN_RUN = "The run went on without your answer. Your answer goes to this conversation and carries the work on.";
export const LEFT_IN_CAMPAIGN = "The session went on without your answer. While it works, your answer reaches it; after that, the campaign's next session reads it, and starts right away while the campaign is active.";
// What a step kept for the owner (item 482) says: nobody answered it in time, so the run went on without it.
const KEPT_NOTE = "Nobody answered in time, so the run went on without this step. Once you have done it yourself, I did it carries the work on in this conversation.";

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
  if (severity === "handoff") {
    return "Dot needs you to take over this step";
  }
  return "Approval needed";
}

function iconOf(severity) {
  if (severity === "question") {
    return "help";
  }
  return severity === "handoff" ? "hand" : "shield";
}

const TAKEOVER_IDLE = "Dot's window comes to the front and the run waits while you have it.";
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
      toast("error", error.message);
    } finally {
      button.disabled = false;
    }
  });
  const mac = el("div", { class: "row takeover-offer" }, button, text);
  return here === null ? mac : el("div", { class: "stack tight" }, mac, here);
}

// What the policy asks the owner about in every mode that asks, whatever their rules say (item 429): a rule offered for
// it would never apply. A yolo run asks nothing at all (item 443), so no card of these comes its way.
// A login filled from the password manager (item 493) is asked as a message is, in every mode but yolo.
const GUARDED_FLAGS = new Set(["financial", "credential", "message_send", "public_post", "vault_fill"]);

/** Whether the owner answers `approval` in every mode that asks, whatever their rules say (item 429). */
function guarded(approval) {
  const flags = approval.args.flags;
  return Array.isArray(flags) && flags.some((flag) => typeof flag === "string" && GUARDED_FLAGS.has(flag));
}

/**
 * The way from an approval to a rule (item 110): the Rules page with a sentence in its box that names the tool and the
 * action, for the owner to change before Dot drafts the rule.
 */
function ruleOffer(approval) {
  const action = typeof approval.args.summary === "string" ? approval.args.summary : approval.summary;
  const sentence = `Let Dot use ${approval.tool} for this without asking me: ${clip(action, 300)}`;
  return el(
    "a",
    { class: "approval-rule", href: `#/settings/rules?sentence=${encodeURIComponent(sentence)}` },
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
    chips.push(el("span", { class: "chip" }, args.kind));
  }
  if (Array.isArray(args.flags)) {
    for (const flag of args.flags) {
      chips.push(el("span", { class: "chip" }, String(flag)));
    }
  }
  const target = typeof args.target === "string" && args.target !== "" ? args.target : null;
  if (chips.length === 0 && target === null) {
    return null;
  }
  // The whole target, as the action would act on it (item 242): a long command wraps, and scrolls within the card.
  return el(
    "div",
    { class: "stack tight" },
    chips.length > 0 ? el("div", { class: "cluster" }, chips) : null,
    target === null ? null : el("pre", { class: "approval-target", tabindex: "0", "aria-label": "What the action acts on" }, target),
  );
}

/** What a kept step's card says of it: what I did it does, and until when the step waits. */
function keptNote(keptUntil) {
  return `${KEPT_NOTE} It waits until ${dateTime(keptUntil)}.`;
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
  // An answer to a question the run left goes on with the work in the run's conversation, so it needs words.
  const isLeft = approval.status === "left";
  // A step handed to the owner that nobody answered in time, kept for them until it lapses (item 482).
  const isKept = isLeft && approval.keptUntil !== null;
  let busy = false;

  async function decide(approve, note) {
    if (busy) {
      return;
    }
    busy = true;
    node.querySelectorAll("button, input").forEach((control) => {
      control.disabled = true;
    });
    let decided;
    try {
      decided = await api.post(`/approvals/${approval.id}/decision`, { approve, note });
    } catch (error) {
      busy = false;
      node.querySelectorAll("button, input").forEach((control) => {
        control.disabled = false;
      });
      toast("error", error.message);
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
    // An answer to a question is the options ticked and what was written; it is never empty.
    const submit = (approve) => {
      const answer = isQuestion && approve ? chosenAnswer(ticks.filter((tick) => tick.checked).map((tick) => tick.value), note.value) : note.value.trim();
      if (isQuestion && approve && answer === "") {
        note.focus();
        return;
      }
      decide(approve, answer);
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
    const yes = isQuestion ? "Send answer" : isHandoff ? "I did it" : "Approve";
    const no = isQuestion ? "Skip" : isHandoff ? "Skip this step" : "Deny";
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
        isLeft ? el("div", { class: "small muted" }, isKept ? keptNote(approval.keptUntil) : leftNote) : null,
        isQuestion ? options : technicalDetails(approval.args),
        isQuestion || isHandoff
          ? null
          : guarded(approval)
            ? el("div", { class: "small muted" }, "Dot asks you about this in every mode but yolo; no rule turns that off.")
            : ruleOffer(approval),
        isHandoff && approval.status === "pending" && approval.tool.startsWith("browser_") ? takeoverOffer(approval.runId, onUseHere) : null,
        el(
          "div",
          { class: "approval-actions" },
          note,
          el("button", { class: "btn", type: "button", onclick: () => submit(false) }, no),
          el("button", { class: "btn primary", type: "button", onclick: () => submit(true) }, yes),
        ),
      ),
    );
  }

  function settle(status, note) {
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

  if (approval.status === "pending" || isLeft) {
    renderPending();
  } else {
    settle(approval.status, approval.note);
  }
  return { node, settle };
}
