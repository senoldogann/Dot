// What an agent's page lists of its work, as DotNext's bot profile does (item 2035, DotEngine's `botOrders`,
// `botObjectives` and `botHandoffs`): the same rules on both, so the two read alike. Pure, so it runs under Node.

/** Dot's id: the lead, which hands every order out and keeps every objective. */
export const DOT_ID = "dot";

/** @typedef {{ order: { agent_id: string, objective_id?: string | null, status: string } }} OrderLike */
/** @typedef {{ id: string, status: string }} ObjectiveLike */

/** The statuses of an order that is over. */
const OVER = new Set(["accepted", "cancelled"]);

/**
 * The orders of an agent: every one for Dot, the ones given to it for another agent.
 * @template {OrderLike} T
 * @param {string} agentId
 * @param {T[]} orders
 * @returns {T[]}
 */
export function agentOrders(agentId, orders) {
  return agentId === DOT_ID ? orders : orders.filter((view) => view.order.agent_id === agentId);
}

/**
 * Whether an order is not over yet.
 * @param {OrderLike} view
 */
export function orderOpen(view) {
  return !OVER.has(view.order.status);
}

/**
 * The objectives of an agent: every one for Dot, the ones its orders serve for another agent.
 * @template {ObjectiveLike} O
 * @param {string} agentId
 * @param {OrderLike[]} orders
 * @param {O[]} objectives
 * @returns {O[]}
 */
export function agentObjectives(agentId, orders, objectives) {
  if (agentId === DOT_ID) {
    return objectives;
  }
  const served = new Set(agentOrders(agentId, orders).map((view) => view.order.objective_id ?? null).filter((id) => id !== null));
  return objectives.filter((objective) => served.has(objective.id));
}

/**
 * The messages of the day to an agent and from it, each newest first.
 * @template {{ ts: number, from_id: string, to_id: string }} M
 * @param {string} agentId
 * @param {M[]} messages
 * @returns {{ received: M[], sent: M[] }}
 */
export function agentHandoffs(agentId, messages) {
  const newest = [...messages].sort((a, b) => b.ts - a.ts);
  return { received: newest.filter((message) => message.to_id === agentId), sent: newest.filter((message) => message.from_id === agentId) };
}

/** @typedef {{ kind: string, count: number, limit: number }} OverloadReason */
/** @typedef {{ reasons: OverloadReason[] }} LoadLike */

/** The catalogue key of each reason a bot is overloaded (item 2067, the daemon's `OverloadReason`). */
const OVERLOAD_KEYS = /** @type {Record<string, import("./i18n.js").WordKey>} */ ({
  orders: "views.agents.overloadOrders",
  routines: "views.agents.overloadRoutines",
  done_share: "views.agents.overloadDoneShare",
});

/**
 * Why a bot is overloaded, as catalogue keys with their values, in the daemon's order; empty when it is not, when the
 * daemon sent no load (one from before item 2067), and for a reason this page does not know yet.
 * @param {LoadLike | null | undefined} load
 * @returns {{ key: import("./i18n.js").WordKey, values: { count: number, limit: number } }[]}
 */
export function overloadLines(load) {
  return (load?.reasons ?? [])
    .filter((reason) => reason.kind in OVERLOAD_KEYS)
    .map((reason) => ({ key: OVERLOAD_KEYS[reason.kind], values: { count: reason.count, limit: reason.limit } }));
}
