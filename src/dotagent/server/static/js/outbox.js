// The outbox (item 1523): what the owner did with no link, kept in IndexedDB and sent once, in order, when the link is back.
// A message, an answer to an approval or a question, a Stop and a change of a chat's queue are the things it keeps. Each is
// kept with its key and its place in the order before anything is asked of the network, so it survives the page being
// killed; it is sent when the phone is online and the page in view (`online`, `visibilitychange`, the stream coming back,
// and a wait that grows while Dot cannot be reached), one chat's entries in the order they were made. A message goes with
// the key Dot starts a message once by (`server/sending.py`), so a send whose answer was lost, a page killed while it sent
// and a link that came back twice send it once. The rules are outboxrules.js; the daemon is not changed.
//
// Nothing here throws: a browser that cannot keep it in IndexedDB keeps it in memory, and the entries are sent as long as
// the page lives.

import { request, unreachable } from "./api.js";
import { canKeep, openDatabase } from "./datacache.js";
import { t } from "./i18n.js";
import { noteWaiting } from "./linkfacts.js";
import { added, afterKill, entryOf, leaveOld, nextDue, ofChat, sendAgain, settled, verdictOf, waitingCount } from "./outboxrules.js";
import { reconnectWait } from "./sseframes.js";
import { onStreamLive } from "./state.js";
import { toast } from "./ui.js";

const STORE = "outbox";

/** @type {import("./outboxrules.js").Entry[]} */
let entries = [];
let seq = 0;
/** @type {Promise<void> | null} */
let loading = null;
let flushing = false;
let flushAgain = false;
let breaks = 0;
let timer = null;
let toldAbout = false;
const listeners = new Set();

function paint() {
  noteWaiting(waitingCount(entries));
  for (const listener of listeners) {
    listener(entries);
  }
}

/** @param {unknown} error */
function warn(error) {
  if (!toldAbout) {
    toldAbout = true;
    console.warn("the outbox is kept in memory only: it could not be written", error);
  }
}

/** Write the entries, all of them or none, in one transaction. */
async function write() {
  if (!canKeep()) {
    return;
  }
  const mine = entries;
  try {
    const db = await openDatabase();
    const transaction = db.transaction(STORE, "readwrite");
    const store = transaction.objectStore(STORE);
    store.clear();
    for (const entry of mine) {
      store.put(entry);
    }
    await new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve(undefined);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error ?? new Error("the transaction was aborted"));
    });
  } catch (error) {
    warn(error);
  }
}

/** What was kept before the page was killed: an entry that was on its way waits again, and its key makes a second send
 * harmless. */
async function read() {
  if (!canKeep()) {
    return [];
  }
  try {
    const db = await openDatabase();
    const found = await new Promise((resolve, reject) => {
      const request = db.transaction(STORE).objectStore(STORE).getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return Array.isArray(found) ? found.filter((entry) => entry !== null && typeof entry === "object" && typeof entry.id === "string") : [];
  } catch (error) {
    warn(error);
    return [];
  }
}

/** Load what is kept, once, and start answering the link's return. Settles when the entries are known. */
export function loadOutbox() {
  if (loading === null) {
    loading = (async () => {
      const kept = afterKill(await read());
      // Entries made before the load (a message sent while it ran) stay, those kept before come first.
      const fresh = entries;
      entries = [];
      for (const entry of [...kept, ...fresh]) {
        entries = added(entries, entry);
      }
      seq = entries.reduce((highest, entry) => Math.max(highest, entry.seq), Date.now());
      await leaveWhatIsOld();
      paint();
      window.addEventListener("online", flush);
      document.addEventListener("visibilitychange", flush);
      onStreamLive(flush);
      flush();
    })();
  }
  return loading;
}

/**
 * Run `listener` with the entries (all chats' entries) now and whenever they change. Returns the function that stops it.
 * @param {(entries: import("./outboxrules.js").Entry[]) => void} listener
 */
export function onOutbox(listener) {
  listeners.add(listener);
  listener(entries);
  return () => listeners.delete(listener);
}

/** How many entries wait to be sent, in all chats. */
export function waiting() {
  return waitingCount(entries);
}

/** Whether `chat` has an entry that waits or is on its way: what is done in it now goes behind them, to keep the order. */
function holds(chat) {
  return ofChat(entries, chat).some((entry) => entry.state === "waiting" || entry.state === "sending");
}

/**
 * Keep something done with no link, to be sent when it is back; returns the entry.
 * @param {{ id?: string, kind: import("./outboxrules.js").Entry["kind"], chat: string, method: string, path: string, body: unknown, text?: string }} made
 */
export async function keep(made) {
  await loadOutbox();
  seq += 1;
  const entry = entryOf({ ...made, id: made.id ?? crypto.randomUUID(), now: Date.now(), seq });
  entries = added(entries, entry);
  paint();
  await write();
  flush();
  return entry;
}

/**
 * Do `send`, which asks Dot, and when there is no link, or Dot cannot be reached, keep what it was for and say that it was:
 * `{ kept }` with the entry. What Dot refuses is thrown as it is. While an earlier thing of the same chat waits, this one
 * goes behind it without being tried, so that what the owner did is sent in the order they did it.
 * @template T
 * @param {{ id?: string, kind: import("./outboxrules.js").Entry["kind"], chat: string, method: string, path: string, body: unknown, text?: string }} made
 * @param {() => Promise<T>} send
 * @returns {Promise<T | { kept: import("./outboxrules.js").Entry }>}
 */
export async function viaOutbox(made, send) {
  await loadOutbox();
  if (!navigator.onLine || holds(made.chat)) {
    return { kept: await keep(made) };
  }
  try {
    return await send();
  } catch (error) {
    if (unreachable(error)) {
      return { kept: await keep(made) };
    }
    throw error;
  }
}

/** Say what came of an entry that the daemon answered for in words the owner reads (an answer to a card that was closed). */
function say(what) {
  if (what === "expired") {
    toast("error", t("outbox.expired"));
  } else if (what === "answered") {
    toast("ok", t("outbox.answered"));
  }
}

/** What waited more than a day is the owner's now (the daemon has forgotten its key): its row says so. */
async function leaveWhatIsOld() {
  const left = leaveOld(entries, Date.now(), t("outbox.left"));
  if (left.some((entry, index) => entry !== entries[index])) {
    entries = left;
    paint();
    await write();
  }
}

/** Send what is due, one entry at a time, until nothing is: the order of a chat is kept, a failure that may pass holds it. */
export async function flush() {
  if (flushing) {
    flushAgain = true;
    return;
  }
  flushing = true;
  clearTimeout(timer);
  try {
    await loadOutbox();
    do {
      flushAgain = false;
      for (;;) {
        await leaveWhatIsOld();
        const next = nextDue(entries, { linkUp: navigator.onLine, visible: document.visibilityState === "visible" });
        if (next === null) {
          break;
        }
        entries = entries.map((entry) =>
          entry.id === next.id ? { ...entry, state: /** @type {const} */ ("sending"), tries: entry.tries + 1 } : entry,
        );
        paint();
        await write();
        /** @type {import("./outboxrules.js").Verdict} */
        let verdict;
        try {
          await request(next.method, next.path, next.body);
          verdict = verdictOf(next, { ok: true });
        } catch (error) {
          const status = error !== null && typeof error === "object" && "status" in error && typeof error.status === "number" ? error.status : 0;
          const message = error instanceof Error ? error.message : "";
          verdict = verdictOf(next, { status, message });
        }
        entries = settled(entries, next.id, verdict);
        if ("done" in verdict && "told" in verdict) {
          say(verdict.told);
        }
        paint();
        await write();
        if ("again" in verdict) {
          // The link or the daemon is the trouble: wait, longer each time, and try again; nothing behind it goes first.
          retryLater();
          return;
        }
        breaks = 0;
      }
    } while (flushAgain);
  } finally {
    flushing = false;
  }
}

function retryLater() {
  clearTimeout(timer);
  timer = setTimeout(flush, reconnectWait(breaks));
  breaks += 1;
}

/** Send what waits at once, without waiting for the next try (Retry now). */
export function retryNow() {
  breaks = 0;
  return flush();
}

/** Let go of an entry (Discard). */
export async function discard(id) {
  await loadOutbox();
  entries = entries.filter((entry) => entry.id !== id);
  paint();
  await write();
}

/**
 * Take an entry back, to be edited: it leaves the outbox, and what the owner wrote is returned.
 * @returns {Promise<import("./outboxrules.js").Entry | null>}
 */
export async function takeBack(id) {
  await loadOutbox();
  const entry = entries.find((held) => held.id === id) ?? null;
  if (entry === null || entry.state === "sending") {
    return null;
  }
  await discard(id);
  return entry;
}

/** A message that was left or refused is sent again, under a new key (Send again). */
export async function again(id) {
  await loadOutbox();
  seq += 1;
  entries = sendAgain(entries, id, crypto.randomUUID(), Date.now(), seq);
  paint();
  await write();
  flush();
}
