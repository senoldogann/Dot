// The words of the work orders on the Agents page (item 907) and of the objectives above them (item 908): where an
// order stands, when it is due, and how long it has been going, said as the page says them. Pure, so a test can read them.

/**
 * @typedef {object} WorkOrder
 * @property {string} id
 * @property {string} agent_id
 * @property {string} title
 * @property {string} brief
 * @property {string} acceptance
 * @property {string | null} objective_id
 * @property {"assigned" | "working" | "in_review" | "returned" | "accepted" | "escalated" | "cancelled"} status
 * @property {number} attempts
 * @property {string[]} run_ids
 * @property {number | null} due_at
 * @property {boolean} tainted
 * @property {string} note
 * @property {number} created_at
 * @property {number} updated_at
 */

/** What the page says of each status, and the colour of its badge (`b-` classes of the badges). */
const STATES = {
  assigned: { label: "Handed out", tone: "b-muted" },
  working: { label: "Working", tone: "b-info" },
  in_review: { label: "Waiting for Dot's check", tone: "b-warn" },
  returned: { label: "Sent back", tone: "b-muted" },
  accepted: { label: "Accepted", tone: "b-ok" },
  escalated: { label: "Waiting for you", tone: "b-bad" },
  cancelled: { label: "Cancelled", tone: "b-muted" },
};

/** The words and the colour of a status; an unknown one is shown as it is. */
export function stateOf(status) {
  return STATES[status] ?? { label: status, tone: "b-muted" };
}

/** Whether an order is still open: somebody has something to do or to decide about it. */
export function isOpen(status) {
  return status !== "accepted" && status !== "cancelled";
}

/** A span of seconds in its biggest unit: "5 min", "3 h", "2 days". */
export function span(seconds) {
  const wanted = Math.max(seconds, 0);
  if (wanted < 3600) {
    return `${Math.floor(wanted / 60)} min`;
  }
  if (wanted < 86_400) {
    return `${Math.floor(wanted / 3600)} h`;
  }
  const days = Math.floor(wanted / 86_400);
  return days === 1 ? "1 day" : `${days} days`;
}

/** When an open order is due: "due in 3 h", "2 h late"; nothing for one with no time or one that is over. */
export function dueWords(order, now) {
  if (order.due_at === null || !isOpen(order.status)) {
    return "";
  }
  return order.due_at > now ? `due in ${span(order.due_at - now)}` : `${span(now - order.due_at)} late`;
}

/** The count of attempts, as the page says it: nothing before the first. */
export function attemptWords(attempts) {
  if (attempts <= 0) {
    return "";
  }
  return attempts === 1 ? "1 try" : `${attempts} tries`;
}
