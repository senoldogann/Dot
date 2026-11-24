// The rules of the page's data cache (item 1520): what a phone keeps of the chats it has read so that Dot opens with them
// drawn when the link is bad or gone. Pure, so that they run under Node without a page: datacache.js only keeps the
// records these rules shape, in IndexedDB. The native iPhone app keeps the same things in files (`ios/README.md`, "On a
// bad link"); the numbers are the same, the layout is this page's own.

/** The IndexedDB database the page keeps its data in. */
export const CACHE_NAME = "dot-cache";
/** The layout of what the cache holds. A record of another layout is not read: it is dropped, and read from Dot again. */
export const LAYOUT = 1;
/** The most the chats of the cache take, in bytes of their JSON; the chats read longest ago go first. */
export const BUDGET_BYTES = 24 * 1024 * 1024;
/** How many runs of a chat a snapshot keeps: the run the page was opened at and the ones before it, the newest. */
export const RUNS_KEPT = 12;
/** How many events of each run a snapshot keeps: the last ones. */
export const EVENTS_KEPT = 400;
/** The most one chat takes: past it the oldest events go until it fits, so that one long chat never fills the budget. */
export const RECORD_MAX_BYTES = 2 * 1024 * 1024;

// The reads of lists the page keeps a copy of, by the key they are kept under. `/threads` is kept once whatever its limit.
const LISTS = [
  { key: "threads", pattern: /^\/threads\?limit=(\d+)$/ },
  { key: "agents", pattern: /^\/agents$/ },
  { key: "status", pattern: /^\/status$/ },
  { key: "activity", pattern: /^\/activity$/ },
  { key: "approvals", pattern: /^\/approvals\?status=pending&limit=100$/ },
  { key: "assistant", pattern: /^\/assistant$/ },
  { key: "themes", pattern: /^\/themes$/ },
  // The models each provider offers: a message is made with the level of one of them, so the composer waits for the list
  // before it sends, which a phone with no link must not wait on (item 1523).
  { key: "models", pattern: /^\/models$/ },
];

/**
 * The key a read of `path` is kept under, or null for a read that is not kept (everything but the lists of the shell).
 * @param {string} path the API path with its query, without `/api`
 * @returns {string | null}
 */
export function listKey(path) {
  for (const list of LISTS) {
    if (list.pattern.test(path)) {
      return list.key;
    }
  }
  return null;
}

/**
 * What a kept list answers for a read of `path`: the list as it was kept, cut to the `limit` the read asked for (a list
 * of chats is kept once, whatever limit it was read with).
 * @param {string} path
 * @param {any} value
 */
export function listFor(path, value) {
  const found = /^\/threads\?limit=(\d+)$/.exec(path);
  if (found !== null && Array.isArray(value)) {
    return value.slice(0, Number(found[1]));
  }
  return value;
}

/**
 * Whose cache this is: the address Dot is served from and the phone's key, as a fingerprint (the key itself is never
 * kept). Everything is dropped when it is another's, so that a key forgotten, or another Dot answering at the same
 * address, leaves nothing of the earlier one's chats behind.
 * @param {string} origin
 * @param {string} fingerprint
 */
export function ownerOf(origin, fingerprint) {
  return `${origin}|${fingerprint}`;
}

/**
 * Whether a cache kept for `kept` is not the one `now` may read.
 * @param {string | null | undefined} kept
 * @param {string} now
 */
export function ownerChanged(kept, now) {
  return typeof kept === "string" && kept !== now;
}

/**
 * The newest `count` events of `events`, oldest first.
 * @template T
 * @param {T[]} events
 * @param {number} count
 * @returns {T[]}
 */
function lastOf(events, count) {
  return count <= 0 ? [] : events.length > count ? events.slice(events.length - count) : events;
}

/** The size of `record` in bytes of its JSON, as far as it is worth knowing: the budget counts it. */
function sizeOf(record) {
  return JSON.stringify(record).length;
}

/**
 * What a chat's snapshot is made of, from the pieces its page holds.
 * @typedef {object} ChatSource
 * @property {string} id the run the page was opened at
 * @property {any} detail `GET /runs/{id}` as the page read it
 * @property {{ earlier: any[], read: Map<string, { events?: any[], error?: unknown }>, cut: boolean }} history the runs before
 *   it, oldest first, and the events of the last of them (history.js)
 * @property {{ id: number, event: string, data: any }[]} messages the run's stream as the page read it, the live pieces
 *   (no id) among them
 * @property {any | null} queue the chat's queue (`GET /threads/{id}/queue`), null when it was not read
 * @property {number} [verifiedAt] when Dot last said that this is how the chat stands (ms); a chat drawn from a copy and
 *   not heard of since keeps the time of the copy, so that "last updated" never says a copy is fresher than it is
 */

/**
 * The record that keeps a chat: its newest `RUNS_KEPT` runs, the last `EVENTS_KEPT` events of each, and its queue,
 * within `RECORD_MAX_BYTES` (the oldest events go first, then the oldest runs). Only what the daemon stored is kept: a
 * live piece of a thought or of a message being written has no id and is not read again from Dot.
 * @param {ChatSource} source
 * @param {number} now
 */
export function snapshotOf(source, now) {
  const earlier = lastOf(source.history.earlier, RUNS_KEPT - 1);
  /** @type {Record<string, any[]>} */
  const read = {};
  for (const past of earlier) {
    const found = source.history.read.get(past.id);
    if (found !== undefined && Array.isArray(found.events)) {
      read[past.id] = found.events;
    }
  }
  const stored = source.messages.filter((message) => message.id > 0);
  let keep = EVENTS_KEPT;
  let runs = earlier;
  for (;;) {
    const byRun = {};
    for (const past of runs) {
      if (read[past.id] !== undefined) {
        byRun[past.id] = lastOf(read[past.id], keep);
      }
    }
    const record = {
      layout: LAYOUT,
      id: source.id,
      threadId: source.detail.run.thread_id,
      savedAt: source.verifiedAt ?? now,
      detail: source.detail,
      earlier: runs,
      read: byRun,
      cut: source.history.cut,
      messages: lastOf(stored, keep),
      queue: source.queue,
      bytes: 0,
    };
    const bytes = sizeOf(record);
    if (bytes <= RECORD_MAX_BYTES || (keep === 0 && runs.length === 0)) {
      record.bytes = bytes;
      return record;
    }
    if (keep > 0) {
      keep = keep > 25 ? Math.floor(keep / 2) : 0;
    } else {
      runs = runs.slice(1);
    }
  }
}

/**
 * The `history` a chat's page draws from a record (`loadHistory`'s shape: a map of what the runs did, none of it an error).
 * @param {{ earlier: any[], read: Record<string, any[]>, cut: boolean }} record
 */
export function historyOf(record) {
  return {
    earlier: record.earlier,
    read: new Map(Object.entries(record.read).map(([id, events]) => [id, { events }])),
    cut: record.cut,
  };
}

/**
 * Whether a record read from the store is one of this layout that holds what a chat's page needs. One that is not is
 * dropped and read from Dot again, whatever is wrong with it (a layout of another build, a write that was cut short, a
 * record of another kind).
 * @param {unknown} record
 */
export function readable(record) {
  if (typeof record !== "object" || record === null) {
    return false;
  }
  const chat = /** @type {Record<string, any>} */ (record);
  return (
    chat.layout === LAYOUT &&
    typeof chat.id === "string" &&
    typeof chat.threadId === "string" &&
    Number.isFinite(chat.savedAt) &&
    typeof chat.detail === "object" &&
    chat.detail !== null &&
    typeof chat.detail.run === "object" &&
    chat.detail.run !== null &&
    chat.detail.run.id === chat.id &&
    Array.isArray(chat.earlier) &&
    typeof chat.read === "object" &&
    chat.read !== null &&
    Array.isArray(chat.messages) &&
    chat.messages.every((message) => typeof message === "object" && message !== null && Number.isFinite(message.id))
  );
}

/**
 * Whether a kept list is of this layout and has what a list is made of.
 * @param {unknown} record
 */
export function listReadable(record) {
  if (typeof record !== "object" || record === null) {
    return false;
  }
  const list = /** @type {Record<string, any>} */ (record);
  return list.layout === LAYOUT && typeof list.id === "string" && Number.isFinite(list.savedAt) && "value" in list;
}

/**
 * What a chat of the index says of itself.
 * @typedef {object} IndexEntry
 * @property {string} id
 * @property {number} bytes
 * @property {number} readAt when the page last read it or wrote it: the one read longest ago goes first
 */

/**
 * The chats to let go so that the rest fit in `budget` bytes: the one read longest ago first, and never `keep`, the chat
 * the page is on. Nothing goes while they fit.
 * @param {IndexEntry[]} entries
 * @param {number} budget
 * @param {string | null} keep
 * @returns {string[]}
 */
export function evictions(entries, budget, keep) {
  let total = entries.reduce((sum, entry) => sum + entry.bytes, 0);
  const dropped = [];
  const oldestFirst = [...entries].sort((a, b) => a.readAt - b.readAt || a.id.localeCompare(b.id));
  for (const entry of oldestFirst) {
    if (total <= budget) {
      break;
    }
    if (entry.id !== keep) {
      dropped.push(entry.id);
      total -= entry.bytes;
    }
  }
  return dropped;
}

/**
 * The index after `id` was written (or read) at `now`, with `bytes` if it was written, and what the budget lets go.
 * @param {IndexEntry[]} entries
 * @param {{ id: string, bytes?: number, now: number }} touched
 * @param {number} budget
 * @returns {{ entries: IndexEntry[], dropped: string[] }}
 */
export function touch(entries, touched, budget) {
  const before = entries.find((entry) => entry.id === touched.id);
  const bytes = touched.bytes ?? before?.bytes ?? 0;
  const next = [...entries.filter((entry) => entry.id !== touched.id), { id: touched.id, bytes, readAt: touched.now }];
  const dropped = evictions(next, budget, touched.id);
  return { entries: next.filter((entry) => !dropped.includes(entry.id)), dropped };
}

/**
 * The highest id among stored messages, 0 for none: what the stream goes on after. A live piece (no id) is not one.
 * @param {{ id: number }[]} messages
 */
export function lastIdOf(messages) {
  return messages.reduce((highest, message) => Math.max(highest, message.id), 0);
}
