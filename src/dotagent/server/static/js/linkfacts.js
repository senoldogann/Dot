// What the page knows of its link to Dot (items 1520 and 1524): whether what it shows is a copy kept on the phone, since
// when, and whether Dot can be reached. The parts of the page that learn one of these say it here; the line under the bar
// reads them (item 1524). No timers run here: a fact changes only when something is told.

/**
 * Whether this page is a phone's touch screen (no pointer that hovers). What the page does on a bad link (the copies of the
 * chats, the reads that try again, the outbox, the line under the bar: items 1520 to 1524) is the phone's alone. A Mac's
 * page talks to a Dot on the same Mac, which a network that is down does not take away: `navigator.onLine` is false there
 * while the daemon answers, and a page that took it for a lost link would hold what the owner sends.
 */
export function phoneScreen() {
  return typeof matchMedia === "function" && matchMedia("(hover: none) and (pointer: coarse)").matches;
}

/**
 * @typedef {object} LinkFacts
 * @property {number | null} copyFrom when the copy the page shows was kept (ms), null while it shows what Dot just said
 * @property {boolean} unreachable the last read or stream could not reach Dot
 * @property {number} waiting how many things done with no link wait to be sent
 * @property {boolean} refused Dot refused the phone's key
 */

/** @type {LinkFacts} */
const facts = { copyFrom: null, unreachable: false, waiting: 0, refused: false };

const listeners = new Set();

/** The facts as they stand: a copy, that must not be changed. */
export function linkFacts() {
  return { ...facts };
}

/** Run `listener` with the facts whenever one changes. Returns the function that stops it. */
export function onLinkFacts(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** @param {Partial<LinkFacts>} change */
function set(change) {
  let changed = false;
  for (const [name, value] of Object.entries(change)) {
    if (facts[name] !== value) {
      facts[name] = value;
      changed = true;
    }
  }
  if (changed) {
    for (const listener of listeners) {
      listener(linkFacts());
    }
  }
}

/** The page draws a copy kept on the phone, which was Dot's word at `savedAt`: until Dot answers, what it shows is not Dot's
 * word of now. The oldest copy shown is the one that counts. */
export function noteCopy(savedAt) {
  set({ copyFrom: facts.copyFrom === null ? savedAt : Math.min(facts.copyFrom, savedAt) });
}

/** Dot answered a read: what is shown is its word of now, and it is reachable. */
export function noteReached() {
  set({ copyFrom: null, unreachable: false, refused: false });
}

/** A read or a stream could not reach Dot. */
export function noteUnreachable() {
  set({ unreachable: true });
}

/** Dot refused the phone's key (or, `false`, the key is not refused any more). */
export function noteRefused(refused = true) {
  set({ refused });
}

/** How many things done with no link wait to be sent (the outbox, item 1523). */
export function noteWaiting(count) {
  set({ waiting: count });
}
