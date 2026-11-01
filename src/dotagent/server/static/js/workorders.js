// The work orders of the Agents page (items 907 and 908): what Dot handed to the agents and where each piece stands,
// under the objectives it keeps. Read only: the lead (Dot) hands work out and judges it, in its chats. An order is a
// line with its state, its agent and the task of its newest try; the objectives that are active stand above them,
// each with why it matters, how progress is judged and its newest line of progress.

import { api } from "./api.js";
import { ago, clip, el, plural } from "./dom.js";
import { errorPanel, loadingPanel } from "./panels.js";
import { attempt } from "./ui.js";
import { attemptWords, dueWords, stateOf } from "./workwords.js";

const TITLE_CHARS = 140;
const NOTE_CHARS = 200;
// Words said of what a run wrote after it read a page or a mail: not the owner's words, whatever they look like.
const OUTSIDE = "Written after Dot read content from outside";

/**
 * @typedef {import("./workwords.js").WorkOrder} WorkOrder
 * @typedef {{ order: WorkOrder, agent_name: string, objective_title: string | null }} OrderView
 * @typedef {{ at: number, text: string, tainted: boolean }} ObjectiveNote
 * @typedef {{ id: string, title: string, why: string, measure: string, status: string, notes: ObjectiveNote[], tainted: boolean }} Objective
 */

/** @param {Objective} objective */
function objectiveRow(objective) {
  const newest = objective.notes.at(-1);
  return el(
    "div",
    { class: "list-row work-row", dataset: { objective: objective.id } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, clip(objective.title, TITLE_CHARS)),
      el("div", { class: "row-text muted" }, clip(objective.why, NOTE_CHARS)),
      el("div", { class: "row-text muted" }, `Judged by: ${clip(objective.measure, NOTE_CHARS)}`),
      newest === undefined
        ? null
        : el("div", { class: "row-text" }, `${new Date(newest.at * 1000).toLocaleDateString()}: ${clip(newest.text, NOTE_CHARS)}`),
      objective.tainted || objective.notes.some((note) => note.tainted) ? el("div", { class: "row-meta" }, el("span", null, OUTSIDE)) : null,
    ),
  );
}

/** @param {OrderView} view */
function orderRow(view) {
  const { order } = view;
  const state = stateOf(order.status);
  const due = dueWords(order, Date.now() / 1000);
  const last = order.run_ids.at(-1);
  return el(
    "div",
    { class: "list-row work-row", dataset: { order: order.id, status: order.status } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row work-heads" }, el("span", { class: `badge ${state.tone}` }, state.label), el("span", { class: "row-title" }, clip(order.title, TITLE_CHARS))),
      el(
        "div",
        { class: "row-meta" },
        view.agent_name === "" ? el("span", null, "An agent that is gone") : el("a", { href: `#/agents/${order.agent_id}` }, view.agent_name),
        attemptWords(order.attempts) === "" ? null : el("span", null, attemptWords(order.attempts)),
        el("span", null, `handed out ${ago(order.created_at)}`),
        due === "" ? null : el("span", null, due),
        view.objective_title === null ? null : el("span", null, `for: ${clip(view.objective_title, NOTE_CHARS)}`),
        last === undefined ? null : el("a", { href: `#/runs/${last}` }, "Its task"),
      ),
      order.note === "" ? null : el("div", { class: "row-text muted" }, clip(order.note, NOTE_CHARS)),
      order.tainted ? el("div", { class: "row-meta" }, el("span", null, OUTSIDE)) : null,
    ),
  );
}

/**
 * The part of the Agents page for the work orders. Returns its `element` and `reload()`, which the page calls when a task
 * starts or ends and when the connection comes back.
 */
export function workOrdersPart() {
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "agents-count" });
  let ticket = 0;
  let gone = false;

  async function reload() {
    const mine = ++ticket;
    try {
      const [orders, objectives] = await Promise.all([api.get("/work-orders"), api.get("/objectives")]);
      if (gone || mine !== ticket) {
        return;
      }
      const active = objectives.filter((objective) => objective.status === "active");
      const open = orders.filter((view) => view.order.status !== "accepted" && view.order.status !== "cancelled");
      count.textContent = open.length === 0 ? "" : String(open.length);
      if (active.length === 0 && orders.length === 0) {
        holder.replaceChildren(
          el("div", { class: "card empty-teams" }, el("p", { class: "muted" }, "Nothing handed out yet. When Dot gives your agents work as orders, they show here with where each stands.")),
        );
        return;
      }
      holder.replaceChildren(
        el(
          "div",
          { class: "card work-card" },
          active.length === 0 ? null : el("h3", { class: "work-sub" }, plural(active.length, "objective", "objectives")),
          ...active.map(objectiveRow),
          orders.length === 0 ? null : el("h3", { class: "work-sub" }, "Orders"),
          ...orders.map(orderRow),
        ),
      );
    } catch (error) {
      if (!gone && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  const element = el(
    "section",
    { class: "agents-part work-part", "aria-label": "Work orders" },
    el("div", { class: "agents-part-head" }, el("h2", { class: "section-label" }, "Work orders", count)),
    holder,
  );
  return {
    element,
    reload,
    dispose() {
      gone = true;
    },
  };
}
