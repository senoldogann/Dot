// The rules of a phone's reads on a bad link (item 1521): how long a read waits for an answer, how often it is tried, how
// long it waits between tries, which failures are tried again, and when a chat that was away reads the end of a long run
// instead of all of it. Pure, so that they run under Node without a page; api.js does the asking. They are the iPhone
// app's own rules (`ios/README.md`, "On a bad link"): a read gives up after 10 s, is tried three times with a wait that
// grows and has a little chance in it, and what will not change is never asked again.

/** How long one try of a read waits for the answer to begin and end. */
export const READ_TIMEOUT_MS = 10_000;
/** How long a write waits: what was done is kept and sent again with its key (the iPhone app gives a write 30 s). */
export const WRITE_TIMEOUT_MS = 30_000;
// The writes the outbox keeps (item 1523): a message (a next run, a steer, a place in the queue), an answer to an approval
// or a question, a Stop, and the changes of a chat's queue. Only they are kept and sent again with their key.
const KEPT_WRITE = /^\/(runs|runs\/[^/]+\/(steer|cancel)|approvals\/[^/]+\/decision|threads\/[^/]+\/queue(\/resume)?|queue\/[^/]+)$/;

/**
 * Whether a write to `path` is one the outbox keeps, and so gives up after `WRITE_TIMEOUT_MS` on a phone: any other write
 * (a voice, a file, a side answer, a rewind) may take as long as it takes and is left to.
 * @param {string} path the API path, without `/api`
 */
export function isKeptWrite(path) {
  return KEPT_WRITE.test(path);
}
/** How many times a read is tried. */
export const TRIES = 3;
/** The wait before the second try; each wait after it is twice the one before, up to `MAX_WAIT_MS`. */
export const FIRST_WAIT_MS = 400;
export const MAX_WAIT_MS = 3000;
/** The most a read takes, its waits included: a try that would start past it does not. */
export const BUDGET_MS = 20_000;
/** A chat that was away through this many events or more reads the newest `TAIL_EVENTS` instead (item 1370). */
export const GAP_EVENTS = 500;
export const TAIL_EVENTS = 400;

/**
 * How long to wait after the `failed`-th try (1 for the first) before the next: the wait doubles from `FIRST_WAIT_MS` to
 * `MAX_WAIT_MS`, half of it fixed and half chance, so that phones that lost the link together do not ask together.
 * @param {number} failed how many tries have failed, 1 or more
 * @param {number} chance a number from 0 up to but not including 1
 */
export function waitAfter(failed, chance) {
  const grown = Math.min(FIRST_WAIT_MS * 2 ** (failed - 1), MAX_WAIT_MS);
  return Math.round(grown / 2 + (grown / 2) * chance);
}

/**
 * Whether a read that failed is asked again: a request that got no answer at all or ran out of time (status 0), a daemon
 * that is busy or starting (408, 429 and every 5xx). What will not change is not: any other refusal, an answer that cannot
 * be read, a read that was cancelled.
 * @param {{ status?: number, cancelled?: boolean, unreadable?: boolean }} failure
 */
export function worthTryingAgain(failure) {
  if (failure.cancelled === true || failure.unreadable === true) {
    return false;
  }
  const status = failure.status ?? 0;
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

/**
 * What to do after a try failed: ask again after a wait (`{ wait }`), or give the failure up (`{ giveUp: true }`).
 * `failed` is how many tries have failed, `elapsed` how long the read has taken so far, and `linkUp` whether the phone has a
 * link at all: with none, a failure spends no try (the read waits for the link instead, `{ waitForLink: true }`).
 * @param {{ failed: number, elapsed: number, chance: number, linkUp: boolean, failure: { status?: number, cancelled?: boolean, unreadable?: boolean } }} state
 * @returns {{ wait: number } | { waitForLink: true } | { giveUp: true }}
 */
export function nextStep(state) {
  if (!worthTryingAgain(state.failure)) {
    return { giveUp: true };
  }
  if (!state.linkUp) {
    return { waitForLink: true };
  }
  if (state.failed >= TRIES) {
    return { giveUp: true };
  }
  const wait = waitAfter(state.failed, state.chance);
  return state.elapsed + wait >= BUDGET_MS ? { giveUp: true } : { wait };
}

/**
 * Whether the events a chat missed are too many to read in full: `read` is how many came back when the first `GAP_EVENTS`
 * after the last one it holds were asked for. A page as long as was asked for says there are at least that many.
 * @param {number} read
 */
export function longGap(read) {
  return read >= GAP_EVENTS;
}

/**
 * The events to put after the ones a chat holds when it read the end of a long run: the tail, as the daemon answered it
 * (`X-Dot-Events-Tail`, item 1370), or none when the daemon ignored `tail` (an older one answers the first events of the
 * run, which are no end of it) and the stream's own replay reads what was missed, as before.
 * @template {{ id: number }} T
 * @param {T[]} events what `GET /runs/{id}/events?tail=` answered
 * @param {boolean} knownTail whether the answer carried the header
 * @param {number} lastId the id of the last event the chat holds
 * @returns {T[] | null}
 */
export function tailToAdd(events, knownTail, lastId) {
  if (!knownTail) {
    return null;
  }
  return events.filter((event) => event.id > lastId);
}
