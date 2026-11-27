// The words of the work orders on the Agents page (item 907) and of the objectives above them (item 908): where an
// order stands, when it is due, and how long it has been going, said as the page says them. Pure, so a test can read them.

import { t } from "./i18n.js";

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
  assigned: { label: t("workwords.handedOut"), tone: "b-muted" },
  working: { label: t("workwords.working"), tone: "b-info" },
  in_review: { label: t("workwords.waitingForDotSCheck"), tone: "b-warn" },
  returned: { label: t("workwords.sentBack"), tone: "b-muted" },
  accepted: { label: t("workwords.accepted"), tone: "b-ok" },
  escalated: { label: t("workwords.waitingForYou"), tone: "b-bad" },
  cancelled: { label: t("workwords.cancelled"), tone: "b-muted" },
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
    return t("workwords.floorMin", { floor: Math.floor(wanted / 60) });
  }
  if (wanted < 86_400) {
    return `${Math.floor(wanted / 3600)} h`;
  }
  const days = Math.floor(wanted / 86_400);
  return days === 1 ? "1 day" : t("workwords.daysDays", { days });
}

/** When an open order is due: "due in 3 h", "2 h late"; nothing for one with no time or one that is over. */
export function dueWords(order, now) {
  if (order.due_at === null || !isOpen(order.status)) {
    return "";
  }
  return order.due_at > now ? t("workwords.dueInSpan", { span: span(order.due_at - now) }) : t("workwords.spanLate", { span: span(now - order.due_at) });
}

/** The count of attempts, as the page says it: nothing before the first. */
export function attemptWords(attempts) {
  if (attempts <= 0) {
    return "";
  }
  return attempts === 1 ? "1 try" : t("workwords.attemptsTries", { attempts });
}
