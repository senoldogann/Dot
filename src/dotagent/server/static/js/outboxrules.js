// The rules of the outbox (item 1523): what the phone did with no link is kept and sent once, in order, when the link comes
// back. Pure, so that they run under Node without a page: outbox.js keeps the entries in IndexedDB and does the asking. They
// are the iPhone app's own (`ios/README.md`, "The outbox"): an entry goes with the key the daemon starts a message once by
// (`server/sending.py`: a key is kept a day, a repeat is answered with what the first made, 409 while the first is under
// way), so an answer that was lost after the daemon took it, a page killed while it sent and a link that came back twice
// send it once.

/** How long the daemon keeps a message's key: a message that waited as long is the owner's to send again. */
export const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * What the outbox holds.
 * @typedef {object} Entry
 * @property {string} id the entry's own key; for a message, the key the daemon dedupes it by
 * @property {"message" | "answer" | "stop" | "queue"} kind what was done: a message sent (a new run, a steer, a place in
 *   the queue), an answer to an approval or a question, a Stop, a change of the chat's queue
 * @property {string} chat the chat it belongs to (its thread); the order is kept for each
 * @property {string} method
 * @property {string} path the API path it is sent to
 * @property {unknown} body what it is sent with, the key among it for a message
 * @property {string} text a message's words, as the owner wrote them, for the row that shows it; "" for the others
 * @property {number} seq the order it was made in, over every chat
 * @property {number} madeAt when it was made (ms)
 * @property {number} tries how many times it was sent
 * @property {"waiting" | "sending" | "left" | "refused"} state `left`: it waited more than a day, so the daemon has
 *   forgotten its key and the owner decides; `refused`: the daemon would not take it, `why` says why
 * @property {string} [why] the daemon's words, or the page's, for a `refused` or `left` entry
 */

/**
 * A new entry.
 * @param {{ id: string, kind: Entry["kind"], chat: string, method: string, path: string, body: unknown, text?: string, now: number, seq: number }} made
 * @returns {Entry}
 */
export function entryOf(made) {
  return {
    id: made.id,
    kind: made.kind,
    chat: made.chat,
    method: made.method,
    path: made.path,
    body: made.body,
    text: made.text ?? "",
    seq: made.seq,
    madeAt: made.now,
    tries: 0,
    state: "waiting",
  };
}

/**
 * The entries after `entry` was added: one send for one key, so an entry whose key is there already (the same message
 * made twice) is not added again.
 * @param {Entry[]} entries
 * @param {Entry} entry
 * @returns {Entry[]}
 */
export function added(entries, entry) {
  return entries.some((held) => held.id === entry.id) ? entries : [...entries, entry];
}

/**
 * Whether an entry has waited so long that the daemon has forgotten its key: sending it again could make a second message.
 * @param {Entry} entry
 * @param {number} now
 */
export function tooOld(entry, now) {
  return entry.state === "waiting" && now - entry.madeAt > DAY_MS;
}

/**
 * The entries that have waited more than a day become `left` for the owner, with the words that say so; the others are as
 * they were.
 * @param {Entry[]} entries
 * @param {number} now
 * @param {string} words
 * @returns {Entry[]}
 */
export function leaveOld(entries, now, words) {
  return entries.map((entry) => (tooOld(entry, now) ? { ...entry, state: /** @type {const} */ ("left"), why: words } : entry));
}

/**
 * The entry to send next, or null: none while the phone has no link or the page is out of view; each chat's entries go in
 * the order they were made, the one that waits at the head of its chat holding the ones behind it (an entry that is
 * `left`, `refused` or on its way does not hold, it is not waiting any more or is being sent); of the chats' heads the
 * oldest goes first.
 * @param {Entry[]} entries
 * @param {{ linkUp: boolean, visible: boolean }} facts
 * @returns {Entry | null}
 */
export function nextDue(entries, facts) {
  if (!facts.linkUp || !facts.visible || entries.some((entry) => entry.state === "sending")) {
    return null;
  }
  /** @type {Map<string, Entry>} */
  const heads = new Map();
  for (const entry of [...entries].sort((a, b) => a.seq - b.seq)) {
    if (entry.state === "waiting" && !heads.has(entry.chat)) {
      heads.set(entry.chat, entry);
    }
  }
  let first = null;
  for (const head of heads.values()) {
    if (first === null || head.seq < first.seq) {
      first = head;
    }
  }
  return first;
}

/**
 * What came of sending an entry, and what is done about it.
 * @typedef {{ done: true } | { done: true, told: "expired" | "answered" | "gone" } | { again: true } | { refused: true, why: string }} Verdict
 */

/**
 * What the daemon's answer to an entry means. `status` is 0 for no answer at all (the link, the daemon down, a read that
 * ran out of time).
 * - It took it (`ok`): done.
 * - No answer, 408, 429, a 5xx: the link or the daemon is the trouble and may pass: the entry waits, and holds the ones behind it.
 * - 409 on a message: the daemon is still sending the first of it: it waits it out.
 * - 409 or 404 on an answer: the card is closed already, expired or answered on the Mac: told, and done.
 * - 409 or 404 on a Stop or a change of the queue: there was nothing left to stop or change: done.
 * - Any other refusal of a message or of a change: the daemon will not take it, whatever is tried: it stays, with its words,
 *   and does not hold the next.
 * @param {Entry} entry
 * @param {{ ok: true } | { ok?: false, status: number, message?: string }} outcome
 * @returns {Verdict}
 */
export function verdictOf(entry, outcome) {
  if (outcome.ok === true) {
    return { done: true };
  }
  const status = outcome.status;
  if (status === 0 || status === 408 || status === 429 || status >= 500) {
    return { again: true };
  }
  if (status === 409 && entry.kind === "message") {
    return { again: true };
  }
  if (status === 409 || status === 404) {
    if (entry.kind === "answer") {
      return { done: true, told: status === 404 ? "expired" : "answered" };
    }
    if (entry.kind === "message") {
      return { refused: true, why: outcome.message ?? "" };
    }
    return { done: true, told: "gone" };
  }
  return { refused: true, why: outcome.message ?? "" };
}

/**
 * The entries after an entry was sent and `verdict` came of it.
 * @param {Entry[]} entries
 * @param {string} id
 * @param {Verdict} verdict
 * @returns {Entry[]}
 */
export function settled(entries, id, verdict) {
  if ("done" in verdict) {
    return entries.filter((entry) => entry.id !== id);
  }
  return entries.map((entry) => {
    if (entry.id !== id) {
      return entry;
    }
    return "refused" in verdict
      ? { ...entry, state: /** @type {const} */ ("refused"), why: verdict.why }
      : { ...entry, state: /** @type {const} */ ("waiting") };
  });
}

/**
 * The entries a message becomes when the owner presses Send again on one that was `left` or `refused`: a new key, since the
 * daemon may have forgotten the old one; the others are as they were. `fresh` is the new key.
 * @param {Entry[]} entries
 * @param {string} id
 * @param {string} fresh
 * @param {number} now
 * @param {number} seq
 * @returns {Entry[]}
 */
export function sendAgain(entries, id, fresh, now, seq) {
  return entries.map((entry) => {
    if (entry.id !== id || (entry.state !== "left" && entry.state !== "refused")) {
      return entry;
    }
    const body =
      entry.body !== null && typeof entry.body === "object" && "key" in entry.body ? { ...entry.body, key: fresh } : entry.body;
    return { ...entry, id: fresh, body, state: /** @type {const} */ ("waiting"), madeAt: now, seq, tries: 0, why: undefined };
  });
}

/**
 * How many entries wait to be sent: what the line under the bar counts (an entry on its way counts too; one that is `left`
 * or `refused` is the owner's and is not a thing that waits for the link).
 * @param {Entry[]} entries
 */
export function waitingCount(entries) {
  return entries.filter((entry) => entry.state === "waiting" || entry.state === "sending").length;
}

/**
 * The entries of one chat, in the order they were made.
 * @param {Entry[]} entries
 * @param {string} chat
 */
export function ofChat(entries, chat) {
  return entries.filter((entry) => entry.chat === chat).sort((a, b) => a.seq - b.seq);
}

/**
 * An entry that was `sending` when the page was killed is waiting again: its key makes a second send harmless if the daemon
 * took the first.
 * @param {Entry[]} entries
 * @returns {Entry[]}
 */
export function afterKill(entries) {
  return entries.map((entry) => (entry.state === "sending" ? { ...entry, state: /** @type {const} */ ("waiting") } : entry));
}
