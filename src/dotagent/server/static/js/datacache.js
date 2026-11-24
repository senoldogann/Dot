// The page's data cache (item 1520): what the phone read of Dot's chats, kept in IndexedDB (`dot-cache`) so that Dot opens
// with them drawn when the link is bad or gone. The rules (what a snapshot holds, which chat goes first, whether a record
// reads) are cacherules.js; this module only keeps the records they shape. The service worker stays shell-only: the page
// owns the data.
//
// A chat's snapshot is one record written in one transaction with the index of what is kept and what the budget lets go:
// whole or not at all. A record that does not read is dropped and read from Dot again. The whole cache is dropped when the
// phone's key is forgotten or when it is another address or key than the one it was kept for. Nothing here ever throws: a
// browser without IndexedDB (a private window), a store that is full or blocked and a record that is broken all leave the
// page as it was before the cache, reading from Dot.

import {
  BUDGET_BYTES,
  CACHE_NAME,
  LAYOUT,
  listFor,
  listKey,
  listReadable,
  ownerChanged,
  ownerOf,
  readable,
  snapshotOf,
  touch,
} from "./cacherules.js";

// The version of the database's layout of stores (not the records': LAYOUT is that). The outbox (item 1523) adds a store.
const DB_VERSION = 1;
const STORES = ["meta", "lists", "chats"];
// A list read again and again (the sidebar's, with every event) is written at most this often.
const LIST_WRITE_EVERY_MS = 2000;
const FINGERPRINT_CHARS = 32;

/** @type {Promise<IDBDatabase> | null} */
let opening = null;
// Settles with whether the cache is usable for the key it was claimed for; false until `claim`, and after `forget`.
/** @type {Promise<boolean>} */
let ready = Promise.resolve(false);
let told = false;
// Counts the times the cache was forgotten: a write that began before one never lands after it, so that a key forgotten
// leaves no chat behind, whatever was on its way.
let generation = 0;

/** Say once that the cache is off, and why: it is a convenience, so the page goes on without it. */
function unavailable(why, error) {
  if (!told) {
    told = true;
    console.warn(`the data cache is not used: ${why}`, error);
  }
}

function supported() {
  return typeof indexedDB === "object" && indexedDB !== null && globalThis.crypto?.subtle !== undefined;
}

/** @template T @param {IDBRequest<T>} request @returns {Promise<T>} */
function result(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/** Settles when the transaction has committed: the writes of it are all there, or (an error, an abort) none. */
function committed(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(undefined);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error ?? new Error("the transaction was aborted"));
  });
}

/** @returns {Promise<IDBDatabase>} */
function database() {
  if (opening === null) {
    opening = new Promise((resolve, reject) => {
      const request = indexedDB.open(CACHE_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const made = request.result;
        for (const name of STORES) {
          if (!made.objectStoreNames.contains(name)) {
            made.createObjectStore(name, { keyPath: "id" });
          }
        }
      };
      request.onsuccess = () => {
        const found = request.result;
        // Another tab upgrading the database waits for this one to let go of it.
        found.onversionchange = () => {
          found.close();
          opening = null;
        };
        resolve(found);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("the database is blocked by another page"));
    });
    opening.catch(() => {
      opening = null;
    });
  }
  return opening;
}

/** The first characters of the SHA-256 of `token`, in hex: whose cache this is, the key itself never kept. */
async function fingerprintOf(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, FINGERPRINT_CHARS);
}

/** Clear what the cache keeps of chats and lists, in the transaction given. The outbox is the owner's own words and is
 * not the cache's to drop. */
function empty(transaction) {
  for (const name of STORES) {
    transaction.objectStore(name).clear();
  }
}

/**
 * Claim the cache for the phone's key `token` at this page's address: what was kept for another key or address is dropped
 * first, so that nothing of it is ever drawn. Settles with whether the cache can be used.
 * @param {string} token
 * @returns {Promise<boolean>}
 */
export function claim(token) {
  const mine = (async () => {
    if (!supported() || token === "") {
      return false;
    }
    const owner = ownerOf(location.origin, await fingerprintOf(token));
    const db = await database();
    const transaction = db.transaction(STORES, "readwrite");
    const kept = await result(transaction.objectStore("meta").get("owner"));
    if (kept !== undefined && (typeof kept.owner !== "string" || kept.layout !== LAYOUT || ownerChanged(kept.owner, owner))) {
      empty(transaction);
    }
    transaction.objectStore("meta").put({ id: "owner", owner, layout: LAYOUT });
    await committed(transaction);
    return true;
  })().catch((error) => {
    unavailable("it could not be opened", error);
    return false;
  });
  ready = mine;
  return mine;
}

/**
 * Drop everything the cache keeps of chats and lists, as a phone's key forgotten does: nothing is drawn from it again
 * until a key claims it anew.
 */
export function forget() {
  const before = ready;
  generation += 1;
  ready = Promise.resolve(false);
  pendingLists.clear();
  written.clear();
  lastWrite.clear();
  return (async () => {
    // A claim that was still being made is settled first, so that its write does not come after the drop.
    await before.catch(() => false);
    if (!supported()) {
      return;
    }
    try {
      const transaction = (await database()).transaction(STORES, "readwrite");
      empty(transaction);
      await committed(transaction);
    } catch (error) {
      unavailable("it could not be emptied", error);
    }
  })();
}

/** The lists waiting to be written, by key: the newest value of each, so that a burst of reads writes once. */
const pendingLists = new Map();
const lastWrite = new Map();
const written = new Map();

async function flushList(key) {
  const waiting = pendingLists.get(key);
  pendingLists.delete(key);
  const mine = generation;
  if (waiting === undefined || !(await ready)) {
    return;
  }
  if (written.get(key) === waiting.text) {
    return;
  }
  try {
    const db = await database();
    if (mine !== generation) {
      return;
    }
    const transaction = db.transaction("lists", "readwrite");
    transaction.objectStore("lists").put({ layout: LAYOUT, id: key, savedAt: waiting.savedAt, value: JSON.parse(waiting.text) });
    await committed(transaction);
    written.set(key, waiting.text);
    lastWrite.set(key, Date.now());
  } catch (error) {
    unavailable("a list could not be kept", error);
  }
}

/**
 * Keep `value`, what `GET path` answered, when it is a list the page keeps (cacherules.js `listKey`). It is kept as it was
 * answered (the caller may change its own copy), written at most every couple of seconds, and not at all when it is what
 * is kept.
 * @param {string} path
 * @param {unknown} value
 */
export function keepList(path, value) {
  const key = listKey(path);
  if (key === null || value === undefined) {
    return;
  }
  const text = JSON.stringify(value);
  const quiet = pendingLists.has(key);
  if (!quiet && written.get(key) === text) {
    return;
  }
  pendingLists.set(key, { text, savedAt: Date.now() });
  if (quiet) {
    return;
  }
  const wait = Math.max(0, (lastWrite.get(key) ?? 0) + LIST_WRITE_EVERY_MS - Date.now());
  setTimeout(() => {
    flushList(key);
  }, wait);
}

/**
 * The copy kept of the list `GET path` answers, `{ value, savedAt }`, or null when none is kept or it does not read (a
 * record that does not is dropped).
 * @param {string} path
 * @returns {Promise<{ value: any, savedAt: number } | null>}
 */
export async function keptList(path) {
  const key = listKey(path);
  if (key === null || !(await ready)) {
    return null;
  }
  try {
    const transaction = (await database()).transaction("lists", "readwrite");
    const store = transaction.objectStore("lists");
    const record = await result(store.get(key));
    if (record !== undefined && !listReadable(record)) {
      store.delete(key);
    }
    await committed(transaction);
    return record !== undefined && listReadable(record) ? { value: listFor(path, record.value), savedAt: record.savedAt } : null;
  } catch (error) {
    unavailable("a list could not be read", error);
    return null;
  }
}

/**
 * Keep a chat: its snapshot is written with the index of what is kept and what the budget lets go in one transaction,
 * so that it is whole or not there.
 * @param {import("./cacherules.js").ChatSource} source
 * @returns {Promise<boolean>} whether it was kept
 */
export async function keepChat(source) {
  const mine = generation;
  if (!(await ready)) {
    return false;
  }
  try {
    const now = Date.now();
    const record = snapshotOf(source, now);
    const db = await database();
    if (mine !== generation) {
      return false;
    }
    const transaction = db.transaction(["chats", "meta"], "readwrite");
    const meta = transaction.objectStore("meta");
    const index = await result(meta.get("chats"));
    const entries = index !== undefined && Array.isArray(index.entries) ? index.entries : [];
    const next = touch(entries, { id: record.id, bytes: record.bytes, now }, BUDGET_BYTES);
    const chats = transaction.objectStore("chats");
    chats.put(record);
    for (const id of next.dropped) {
      chats.delete(id);
    }
    meta.put({ id: "chats", entries: next.entries });
    await committed(transaction);
    return true;
  } catch (error) {
    unavailable("a chat could not be kept", error);
    return false;
  }
}

/** Let go of the chat kept for run `id` (one that does not read, or whose run Dot no longer has), and its entry of the
 * index. */
export async function dropChat(id) {
  if (!(await ready)) {
    return;
  }
  try {
    const transaction = (await database()).transaction(["chats", "meta"], "readwrite");
    transaction.objectStore("chats").delete(id);
    const meta = transaction.objectStore("meta");
    const index = await result(meta.get("chats"));
    if (index !== undefined && Array.isArray(index.entries)) {
      meta.put({ id: "chats", entries: index.entries.filter((entry) => entry.id !== id) });
    }
    await committed(transaction);
  } catch (error) {
    unavailable("a chat that does not read could not be dropped", error);
  }
}

/**
 * The snapshot kept of the chat opened at run `id` (see `cacherules.js` `snapshotOf` for what it holds), or null when none
 * is kept or it does not read: the page then reads the chat from Dot, as it did before the cache. A read counts as the
 * chat's newest use, so that the chats read longest ago are the first to go.
 * @param {string} id
 */
export async function keptChat(id) {
  if (!(await ready)) {
    return null;
  }
  let record;
  try {
    const transaction = (await database()).transaction(["chats", "meta"], "readwrite");
    record = await result(transaction.objectStore("chats").get(id));
    if (readable(record)) {
      const meta = transaction.objectStore("meta");
      const index = await result(meta.get("chats"));
      const entries = index !== undefined && Array.isArray(index.entries) ? index.entries : [];
      if (entries.some((entry) => entry.id === id)) {
        meta.put({ id: "chats", entries: touch(entries, { id, now: Date.now() }, BUDGET_BYTES).entries });
      }
    }
    await committed(transaction);
  } catch (error) {
    // A record the store cannot hand over (a clone that fails) is as good as one that does not read.
    unavailable("a chat could not be read", error);
    await dropChat(id);
    return null;
  }
  if (record === undefined) {
    return null;
  }
  if (!readable(record)) {
    await dropChat(id);
    return null;
  }
  return record;
}
