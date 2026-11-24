// What the page knows of its link to Dot (items 1520 and 1524): whether what it shows is a copy kept on the phone, since
// when, and whether Dot can be reached. The parts of the page that learn one of these say it here; the line under the bar
// reads them (item 1524). No timers run here: a fact changes only when something is told.

/**
 * @typedef {object} LinkFacts
 * @property {number | null} copyFrom when the copy the page shows was kept (ms), null while it shows what Dot just said
 * @property {boolean} unreachable the last read or stream could not reach Dot
 * @property {number} waiting how many things done with no link wait to be sent
 */

/** @type {LinkFacts} */
const facts = { copyFrom: null, unreachable: false, waiting: 0 };

/** @param {Partial<LinkFacts>} change */
function set(change) {
  Object.assign(facts, change);
}

/** The page draws a copy kept on the phone, which was Dot's word at `savedAt`: until Dot answers, what it shows is not Dot's
 * word of now. The oldest copy shown is the one that counts. */
export function noteCopy(savedAt) {
  set({ copyFrom: facts.copyFrom === null ? savedAt : Math.min(facts.copyFrom, savedAt) });
}

/** Dot answered a read: what is shown is its word of now, and it is reachable. */
export function noteReached() {
  set({ copyFrom: null, unreachable: false });
}

/** A read or a stream could not reach Dot. */
export function noteUnreachable() {
  set({ unreachable: true });
}

/** How many things done with no link wait to be sent (the outbox, item 1523). */
export function noteWaiting(count) {
  set({ waiting: count });
}
