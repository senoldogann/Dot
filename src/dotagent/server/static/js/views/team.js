// The Team page (item 87): today's messages between agents, newest first, as DotNext lists them (item 2035). A
// message is a line from the agent that sent it to the agent that answered, with the reply under it once the answering
// run ended (or that the agent asked is working on it), the time, and the runs behind it (the sender's and the
// answer's). Dot is a member like any agent, with its own face. No badge: the line under the message says how it stands,
// as the phone's row does.

import { actorFace, dotFace } from "../actors.js";
import { api } from "../api.js";
import { ago, clip, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead } from "../panels.js";
import { onLifecycle, onStreamBack } from "../state.js";
import { attempt } from "../ui.js";
import { plainText } from "../markdown.js";
import { WORKING_STATES } from "../runstates.js";
import { t } from "../i18n.js";

const DOT_ID = "dot";
const MESSAGE_CHARS = 240;
const REPLY_CHARS = 300;
const WORKING = WORKING_STATES;

function faceOf(id, working) {
  const state = working ? "working" : "idle";
  return id === DOT_ID ? dotFace(state) : actorFace(id, state, null);
}

function memberLink(id, name) {
  return el("a", { class: "team-member", href: id === DOT_ID ? "#/runs" : `#/agents/${id}` }, name);
}

/** One message: who to whom, the line, the reply, the time and the runs. */
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
        // The word is text, which a screen reader reads; a label on a plain span it does not (item 559).
        el("span", { class: "team-arrow" }, icon("arrowRight"), el("span", { class: "visually-hidden" }, t("views.team.to"))),
        memberLink(message.to_id, message.to_name),
      ),
      // Words with their markdown taken off (item 778): a preview printed "**Files:**" and backticks as they were written.
      el("div", { class: "row-text" }, clip(plainText(message.message), MESSAGE_CHARS)),
      message.reply === null
        ? el("div", { class: "row-text muted" }, working ? t("views.team.toNameIsWorkingOn", { to_name: message.to_name }) : t("views.team.noReply"))
        : el("div", { class: "row-text muted team-reply" }, clip(plainText(message.reply), REPLY_CHARS)),
      el(
        "div",
        { class: "row-meta" },
        el("span", null, ago(message.ts)),
        el("a", { href: `#/runs/${message.parent_run_id}` }, t("views.team.theSenderSTask")),
        el("a", { href: `#/runs/${message.run_id}` }, t("views.team.theReplySTask")),
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
          ? emptyPanel("message", t("views.team.noMessagesToday"), t("views.team.askDotToBringOne"))
          : el("div", { class: "card" }, [...view.messages].sort((a, b) => b.ts - a.ts).map(messageRow)),
      );
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(
    el("a", { class: "back", href: "#/settings/agents" }, icon("arrowLeft"), t("views.team.allAgents")),
    pageHead(t("views.team.handoffs"), t("views.team.todaySMessagesBetweenDot"), null),
    holder,
  );
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
    }
  });
  const stopBack = onStreamBack(() => attempt(reload));
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
  };
}
