// What the one capsule over a phone's composer says (item 1383): which segments it has, in which order, and the words of
// each, from what the page knows of the run. A segment is there only when it has something to say, and with none the
// capsule is not drawn. Pure, so that it runs under Node without a page.

import { elapsed } from "./dom.js";
import { t, tCount } from "./i18n.js";

// A loop's rhythm of more words than this ("on Mon, Wed and Fri at 18:30") is left to its sheet: the segment says how
// many loops there are.
const RHYTHM_CHARS = 18;

/**
 * What the page knows of the run and of the chat around it.
 * @typedef {object} DockState
 * @property {string | null} status the run's status while it works ("running", "queued", "blocked"); null once it no longer works
 * @property {number} took how long the run has worked, in seconds
 * @property {boolean} stopping Stop was pressed and the run has not ended yet
 * @property {boolean} reconnecting the page lost the run's stream and has not got it back
 * @property {{ done: number, total: number } | null} plan how far the run's plan has come
 * @property {{ live: number, waitedFor: number, letGo: number }} helpers the helpers that work (waitcounts.js)
 * @property {{ count: number, rhythm: string | null, problem: boolean }} loops the loops of the chat; `rhythm` is how often
 *   the one loop repeats, in few words
 * @property {{ count: number, held: boolean, problem: boolean, sending: boolean }} queue the messages queued for after the run
 * @property {{ count: number, problem: boolean }} running the commands at work in the chat
 */

/**
 * One part of the capsule.
 * @typedef {object} DockSegment
 * @property {"status" | "stop" | "plan" | "helpers" | "loops" | "queue" | "running"} id
 * @property {string} label the words at the roomiest width
 * @property {string} short what stays of them when the capsule is tight: a count, a time
 * @property {string} name what a screen reader says
 * @property {"work" | "waiting" | "warn" | "quiet"} tone how it is drawn
 * @property {boolean} sheet whether a tap opens a sheet of its own
 */

/**
 * The status segment: how long the run has worked, or that it waits for the owner, waits to start, stops or has lost
 * its stream.
 * @param {DockState} state
 * @returns {DockSegment}
 */
function statusSegment(state) {
  if (state.stopping) {
    const label = t("capsule.stopping");
    return { id: "status", label, short: label, name: label, tone: "quiet", sheet: false };
  }
  if (state.reconnecting) {
    const label = t("dock.reconnecting");
    return { id: "status", label, short: label, name: label, tone: "warn", sheet: false };
  }
  if (state.status === "queued") {
    const label = t("work.queued");
    return { id: "status", label, short: label, name: label, tone: "quiet", sheet: false };
  }
  if (state.status === "blocked") {
    const label = t("work.waitingForYou");
    return { id: "status", label, short: label, name: label, tone: "waiting", sheet: false };
  }
  const took = elapsed(state.took);
  const label = t("dock.working", { took });
  return { id: "status", label, short: took, name: label, tone: "work", sheet: false };
}

/**
 * The segments of the capsule, in the order it draws them: the run's status and its Stop, its plan, its helpers, the
 * chat's loops, its queue and the commands it has at work. Empty when none has anything to say.
 * @param {DockState} state
 * @returns {DockSegment[]}
 */
export function dockSegments(state) {
  /** @type {DockSegment[]} */
  const segments = [];
  const working = state.status !== null;
  if (working || state.reconnecting) {
    segments.push(statusSegment(state));
  }
  if (working && !state.stopping) {
    segments.push({ id: "stop", label: t("capsule.stop"), short: "", name: t("dock.stopLabel"), tone: "quiet", sheet: false });
  }
  if (working && state.plan !== null && state.plan.total > 0) {
    const { done, total } = state.plan;
    segments.push({
      id: "plan",
      label: t("capsule.plan", { done, total }),
      short: `${done}/${total}`,
      name: t("capsule.planDone", { done, total }),
      tone: "quiet",
      sheet: true,
    });
  }
  const { live, waitedFor, letGo } = state.helpers;
  if (working && live > 0) {
    const label = tCount(live, "dock.helperOne", "dock.helperMany");
    const detail = waitedFor > 0 ? tCount(waitedFor, "capsule.waitOne", "capsule.waitMany") : tCount(letGo, "capsule.backgroundOne", "capsule.backgroundMany");
    segments.push({ id: "helpers", label, short: String(live), name: `${label}. ${detail}`, tone: "quiet", sheet: true });
  }
  const { loops } = state;
  if (loops.count > 0 || loops.problem) {
    if (loops.count === 0) {
      segments.push({ id: "loops", label: t("dock.unread"), short: "!", name: t("dock.unread"), tone: "warn", sheet: true });
    } else if (loops.count === 1) {
      const said = loops.rhythm !== null && loops.rhythm.length <= RHYTHM_CHARS;
      const label = said ? /** @type {string} */ (loops.rhythm) : t("dock.loopOne");
      const name = said ? t("dock.loopNamed", { rhythm: label }) : label;
      // A read that failed after one that worked keeps the count it knew, and says that the list could not be read.
      segments.push({ id: "loops", label, short: "1", name: loops.problem ? `${name}. ${t("dock.unread")}` : name, tone: loops.problem ? "warn" : "quiet", sheet: true });
    } else {
      const label = t("dock.loopMany", { count: loops.count });
      segments.push({ id: "loops", label, short: String(loops.count), name: loops.problem ? `${label}. ${t("dock.unread")}` : label, tone: loops.problem ? "warn" : "quiet", sheet: true });
    }
  }
  const { queue } = state;
  if (queue.count > 0 || queue.problem || queue.sending) {
    if (queue.count === 0) {
      // A message on its way, or a queue that could not be read: there is nothing to count yet.
      const label = queue.sending ? t("dock.sending") : t("dock.unread");
      segments.push({ id: "queue", label, short: queue.sending ? "…" : "!", name: label, tone: queue.sending ? "quiet" : "warn", sheet: true });
    } else {
      const label = queue.held ? t("dock.queuedHeld", { count: queue.count }) : t("capsule.queued", { count: queue.count });
      const name = queue.problem ? `${label}. ${t("dock.unread")}` : label;
      segments.push({ id: "queue", label, short: String(queue.count), name, tone: queue.held || queue.problem ? "warn" : "quiet", sheet: true });
    }
  }
  const { running } = state;
  if (running.count > 0 || running.problem) {
    const label = running.count > 0 ? t("dock.running", { count: running.count }) : t("dock.unread");
    // As with the loops, a failed read that knew a count keeps it, and says so.
    const name = running.count > 0 && running.problem ? `${label}. ${t("dock.unread")}` : label;
    segments.push({ id: "running", label, short: running.count > 0 ? String(running.count) : "!", name, tone: running.count > 0 && !running.problem ? "quiet" : "warn", sheet: true });
  }
  return segments;
}
