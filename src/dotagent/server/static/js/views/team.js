// The Team page (item 87): today's messages between agents as one thread, oldest first. A message is a line from the
// agent that sent it to the agent that answered, with the reply under it once the answering run ended, the time, and
// the runs behind it (the sender's and the answer's). Dot is a member like any agent, with its own face.

import { actorFace } from "../actors.js";
import { api } from "../api.js";
import { ago, clip, el } from "../dom.js";
import { avatar, icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead, statusBadge } from "../panels.js";
import { onLifecycle } from "../state.js";
import { attempt } from "../ui.js";

const DOT_ID = "dot";
const MESSAGE_CHARS = 240;
const REPLY_CHARS = 300;
const WORKING = new Set(["queued", "running", "blocked"]);

function faceOf(id, working) {
  const state = working ? "working" : "idle";
  return id === DOT_ID ? avatar(state) : actorFace(id, state);
}

function memberLink(id, name) {
  return el("a", { class: "team-member", href: id === DOT_ID ? "#/runs" : `#/agents/${id}` }, name);
}

/** One message of the thread: who to whom, the line, the reply, the time and the runs. */
export function messageRow(message) {
  const working = WORKING.has(message.status);
  return el(
    "div",
    { class: "list-row team-row", dataset: { run: message.run_id } },
    el("div", { class: "team-faces" }, faceOf(message.from_id, false), faceOf(message.to_id, working)),
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row team-heads" },
        memberLink(message.from_id, message.from_name),
        el("span", { class: "team-arrow", "aria-label": "to" }, icon("arrowRight")),
        memberLink(message.to_id, message.to_name),
        statusBadge(message.status),
      ),
      el("div", { class: "row-text" }, clip(message.message, MESSAGE_CHARS)),
      message.reply === null
        ? el("div", { class: "row-text muted" }, working ? `${message.to_name} is working on it` : "No reply")
        : el("div", { class: "row-text muted team-reply" }, clip(message.reply, REPLY_CHARS)),
      el(
        "div",
        { class: "row-meta" },
        el("span", null, ago(message.ts)),
        el("a", { href: `#/runs/${message.parent_run_id}` }, "The sender's run"),
        el("a", { href: `#/runs/${message.run_id}` }, "The reply's run"),
      ),
    ),
  );
}

export function mount(root) {
  let disposed = false;
  let ticket = 0;
  const holder = el("div", null, loadingPanel());

  async function reload() {
    const mine = ++ticket;
    try {
      const view = await api.get("/team");
      if (disposed || mine !== ticket) {
        return;
      }
      holder.replaceChildren(
        view.messages.length === 0
          ? emptyPanel("No messages today", "When a run sends an agent a message with message_agent, the exchange shows here.")
          : el("div", { class: "card" }, view.messages.map(messageRow)),
      );
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(
    el("a", { class: "back", href: "#/agents" }, icon("arrowLeft"), "All agents"),
    pageHead("Team", "Today's messages between Dot and your agents, oldest first.", null),
    holder,
  );
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
    }
  });
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
  };
}
