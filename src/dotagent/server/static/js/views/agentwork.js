// An agent's work, page by page, as DotNext's bot profile lists it (item 2035): its work orders (open, then
// finished), its objectives (active, then finished) and today's hand-offs (received, then sent to others, each newest
// first). Dot's pages hold everything: it hands every order out and keeps every objective. Read only; each page reads
// again when a task starts or ends and when the stream comes back.

import { api } from "../api.js";
import { agentHandoffs, agentObjectives, agentOrders, DOT_ID, orderOpen } from "../botwork.js";
import { el } from "../dom.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead } from "../panels.js";
import { onLifecycle, onStreamBack } from "../state.js";
import { attempt } from "../ui.js";
import { objectiveRow, orderRow } from "../workorders.js";
import { messageRow } from "./team.js";

/** A group of rows under its heading, or nothing when it has none. */
function group(title, rows) {
  return rows.length === 0 ? null : el("section", { class: "agent-work-group" }, el("h2", { class: "section-label" }, title), el("div", { class: "card" }, ...rows));
}

/** The page of `kind` ("orders", "objectives" or "handoffs") of the agent `agentId`, from what the daemon sends. */
async function body(kind, agentId) {
  if (kind === "handoffs") {
    const team = await api.get("/team");
    const { received, sent } = agentHandoffs(agentId, team.messages);
    if (received.length + sent.length === 0) {
      return [emptyPanel("branch", t("views.agentwork.noHandoffs"), t("views.agentwork.noHandoffsHow"))];
    }
    return [
      group(t("views.agentwork.received"), received.map(messageRow)),
      group(t("views.agentwork.sent"), sent.map(messageRow)),
      el("p", { class: "muted agent-work-foot" }, t("views.agentwork.handoffsFoot")),
    ];
  }
  const orders = await api.get("/work-orders");
  if (kind === "orders") {
    const own = agentOrders(agentId, orders);
    if (own.length === 0) {
      return [emptyPanel("list", t("views.agentwork.noOrders"), t("views.agentwork.noOrdersHow"))];
    }
    return [group(t("views.agentwork.open"), own.filter(orderOpen).map(orderRow)), group(t("views.agentwork.finished"), own.filter((view) => !orderOpen(view)).map(orderRow))];
  }
  const objectives = agentObjectives(agentId, orders, await api.get("/objectives"));
  if (objectives.length === 0) {
    return [emptyPanel("target", t("views.agentwork.noObjectives"), agentId === DOT_ID ? t("views.agentwork.noObjectivesDot") : t("views.agentwork.noObjectivesAgent"))];
  }
  return [
    group(t("views.agentwork.active"), objectives.filter((objective) => objective.status === "active").map(objectiveRow)),
    group(t("views.agentwork.finished"), objectives.filter((objective) => objective.status !== "active").map(objectiveRow)),
  ];
}

const TITLES = { orders: "workorders.workOrders", objectives: "views.agentwork.objectives", handoffs: "views.team.handoffs" };

export function mount(root, ctx) {
  const agentId = ctx.params[0];
  const kind = ctx.params[1];
  let disposed = false;
  let ticket = 0;
  const holder = el("div", { class: "agent-work" }, loadingPanel());

  async function reload() {
    const mine = ++ticket;
    try {
      const parts = await body(kind, agentId);
      if (!disposed && mine === ticket) {
        holder.replaceChildren(...parts.filter((part) => part !== null));
      }
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(el("a", { class: "back", href: `#/agents/${agentId}` }, icon("arrowLeft"), t("views.agentwork.work")), pageHead(t(TITLES[kind]), null, null), holder);
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
