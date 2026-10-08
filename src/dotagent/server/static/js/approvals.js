// The card that asks the user: an approval of an action, a hand-off of a step, or a question from the agent.

import { api } from "./api.js";
import { clip, el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

const STATUS_BADGE = { approved: "b-ok", denied: "b-bad", expired: "b-muted" };
const STATUS_LABEL = { approved: "Approved", denied: "Denied", expired: "Expired", pending: "Waiting for you" };

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
  };
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
  return el(
    "div",
    { class: "stack tight" },
    chips.length > 0 ? el("div", { class: "cluster" }, chips) : null,
    target === null ? null : el("div", { class: "mono small muted truncate", title: target }, clip(target, 300)),
  );
}

/**
 * A card for one approval. `onSettled` runs after the daemon accepted a decision (or said it was made already).
 * Returns the node and `settle(status, note)`, which turns the card into its decided form.
 */
export function approvalCard({ approval, heading, onSettled }) {
  const node = el("div", { class: "approval" });
  const isQuestion = approval.severity === "question";
  const isHandoff = approval.severity === "handoff";
  let busy = false;

  async function decide(approve, note) {
    if (busy) {
      return;
    }
    busy = true;
    node.querySelectorAll("button, input").forEach((control) => {
      control.disabled = true;
    });
    try {
      await api.post(`/approvals/${approval.id}/decision`, { approve, note });
    } catch (error) {
      busy = false;
      node.querySelectorAll("button, input").forEach((control) => {
        control.disabled = false;
      });
      toast("error", error.message);
      if (error.status === 409) {
        onSettled();
      }
      return;
    }
    onSettled();
  }

  function renderPending() {
    node.className = `approval${approval.severity === "high" ? " high" : ""}${isQuestion ? " question" : ""}${isHandoff ? " handoff" : ""}`;
    const text = isQuestion && typeof approval.args.question === "string" ? approval.args.question : approval.summary;
    const note = el("input", {
      class: "input",
      type: "text",
      maxlength: "2000",
      placeholder: isQuestion ? "Your answer" : "Note for Dot (optional)",
      "aria-label": isQuestion ? "Your answer" : "Note for Dot",
    });
    const submit = (approve) => decide(approve, note.value.trim());
    note.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit(true);
      }
    });
    const options =
      isQuestion && Array.isArray(approval.args.options) && approval.args.options.length > 0
        ? el(
            "div",
            { class: "options" },
            approval.args.options.map((option) =>
              el(
                "button",
                {
                  class: "btn sm",
                  type: "button",
                  onclick: () => {
                    note.value = String(option);
                    submit(true);
                  },
                },
                String(option),
              ),
            ),
          )
        : null;
    const yes = isQuestion ? "Send answer" : isHandoff ? "I did it" : "Approve";
    const no = isQuestion ? "Skip" : isHandoff ? "Skip this step" : "Deny";
    node.replaceChildren(
      el(
        "div",
        { class: "approval-head" },
        icon(iconOf(approval.severity)),
        el("span", null, titleOf(approval.severity)),
        isQuestion || isHandoff ? null : el("span", { class: `badge ${approval.severity === "high" ? "b-bad" : "b-warn"}` }, approval.severity),
        el("span", { class: "spacer" }),
        heading,
      ),
      el("div", { class: "approval-text" }, text),
      isQuestion ? options : technicalDetails(approval.args),
      el(
        "div",
        { class: "approval-actions" },
        note,
        el("button", { class: "btn", type: "button", onclick: () => submit(false) }, no),
        el("button", { class: "btn primary", type: "button", onclick: () => submit(true) }, yes),
      ),
    );
  }

  function settle(status, note) {
    node.className = "approval decided";
    const text = isQuestion && typeof approval.args.question === "string" ? approval.args.question : approval.summary;
    node.replaceChildren(
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
      note === "" ? null : el("div", { class: "small" }, el("span", { class: "muted" }, isQuestion ? "Answer: " : "Note: "), note),
    );
  }

  if (approval.status === "pending") {
    renderPending();
  } else {
    settle(approval.status, approval.note);
  }
  return { node, settle };
}
