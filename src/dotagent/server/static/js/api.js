// The client of the Dot daemon API: bearer token, JSON requests, server-sent events and images.

import { listKey } from "./cacherules.js";
import { claim, forget, keepList, keptList } from "./datacache.js";
import { UNREACHABLE } from "./errors.js";
import { noteCopy, noteReached, noteUnreachable } from "./linkfacts.js";
import { isKeptWrite, nextStep, READ_TIMEOUT_MS, WRITE_TIMEOUT_MS } from "./retryrules.js";
import { CONNECT_TIMEOUT_MS, createParser, IDLE_TIMEOUT_MS, reconnectWait } from "./sseframes.js";
import { toast } from "./ui.js";

const TOKEN_KEY = "dot.token";
// The daemon proves that it holds the token before a request carries it there (`GET /api/proof`, `daemon_proof` in
// server/guard.py): a program that took the daemon's port while it was down cannot, and is sent nothing.
const PROOF_LABEL = "dot daemon proof\n";
const NONCE_BYTES = 32;
// The daemon writes a keepalive into every stream at least every 15 s (`_KEEPALIVE_S` in server/stream.py): a stream
// silent for two of them and a little more (sseframes.js `IDLE_TIMEOUT_MS`, 35 s; 45 s before item 1522) has stopped
// without closing (a network that changed, a Mac that slept) and is cut, to be followed again (item 200), and a
// connection that answered nothing in `CONNECT_TIMEOUT_MS` (8 s) is dropped the same way. A read that is made once (a
// chat's stored steps) is given the old 45 s: it has no stream to go on after.
const ONE_SHOT_PATIENCE_MS = 45000;
// A page back in view whose stream has been silent for longer than a keepalive takes was put away (a phone that locked,
// a Mac that slept): its stream is cut at once and followed again, rather than after the rest of the idle time (item 286).
const BACK_SILENCE_MS = 20000;
// A page back in view after being put away this long is followed again too, however recently its stream spoke: a phone
// put away for a few seconds (a glance at a notification) can come back to a connection that stays open and passes
// nothing, as a VPN that woke anew leaves it, which only the idle time would cut (item 1152). The cost is one request that
// starts after the last event seen, so only a stream that is followed again from there (`follow`) is cut so; a bounded
// read with no retry (a chat's stored steps) would end in an error. It is the phone's: iOS puts a page away with its
// network, while a Mac keeps both for a tab or an app the owner switched from (⌘-Tab), whose return would make every
// stream and read the page again each time; there the silence decides, as before.
const BACK_AWAY_MS =
  typeof matchMedia === "function" && matchMedia("(hover: none) and (pointer: coarse)").matches ? 2000 : BACK_SILENCE_MS;
// An event the page fails to show, or a read of a page that fails, is logged every time and told at most this often,
// so a fault that hits every event of a busy run does not bury the page in toasts.
const NOT_SHOWN_TOLD_EVERY_MS = 10000;
// How long a stream may be down before the page says so: one that is followed again sooner, as a page back in view
// does, says nothing (item 286).
export const QUIET_BREAK_MS = 2000;
let token = null;
// The key the data cache was claimed for (item 1520): the cache answers only while the page holds that key, so a key that
// is only being tried (an address that brought one, main.js) is never answered with the chats of the key before it.
let cacheToken = null;
// How a phone's own key starts (server/phones.py, item 670).
const PHONE_KEY_PREFIX = "phone.";
let authLostHandler = null;
let proving = null;
let notShownToldAt = 0;

export class ApiError extends Error {
  constructor(status, message, context) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.context = context;
  }
}

/** Whether `error` is a request that got no answer at all (`UNREACHABLE`), as while a phone's network wakes. */
export function unreachable(error) {
  return error instanceof ApiError && error.status === 0 && error.message === UNREACHABLE;
}

/** The token kept in this browser, or null. Storage can be unavailable (private windows), so it is only a convenience. */
export function loadToken() {
  try {
    token = localStorage.getItem(TOKEN_KEY);
  } catch {
    // Storage is unavailable here: the token then lives only in memory for this page.
  }
  keepCacheFor(token);
  return token;
}

/** The data cache (item 1520) is the key's: claim it for `value`, which drops what it kept for another key or address. */
function keepCacheFor(value) {
  if (value === null || value === "") {
    return;
  }
  cacheToken = value;
  claim(value);
}

/** Whether the data cache may answer for the key the page holds. */
function cacheOn() {
  return token !== null && token === cacheToken;
}

/** Use `value` as the page's token; `keep` writes it in this browser's storage too, for its next pages. A token that
 * came in the address is kept only once the daemon has proven that it holds it (main.js, item 559). */
export function setToken(value, keep = true) {
  token = value;
  if (!keep) {
    return;
  }
  try {
    localStorage.setItem(TOKEN_KEY, value);
  } catch {
    // Same as above: the page keeps working with the in-memory token.
  }
  keepCacheFor(value);
}

/**
 * Tell Dot's iPhone app the phone's own key once the daemon has proven it (item 997): the app keeps it in its Keychain
 * and registers with it for the lock screen's pushes. Only the app's web view has the `dot` handler, and the app takes
 * the message from the daemon's own page alone; the installation's token is never passed on.
 */
export function tellPhoneApp() {
  const handler = phoneApp();
  if (handler === undefined || token === null || !token.startsWith(PHONE_KEY_PREFIX)) {
    return;
  }
  handler.postMessage({ type: "paired", key: token });
}

/** The `dot` message handler of Dot's iPhone app, or undefined in a browser (item 997). */
function phoneApp() {
  return /** @type {{webkit?: {messageHandlers?: {dot?: {postMessage(message: object): void}}}}} */ (window).webkit?.messageHandlers?.dot;
}

/** Whether the page is Dot's iPhone app's (item 1005): its web view names it in the user agent, and its notifications
 * are the app's own (APNs), not the browser's. */
export function inPhoneApp() {
  return navigator.userAgent.includes("DotPhone/") && phoneApp() !== undefined;
}

/** Open the app's page of the iPhone's Settings, where its notifications are switched (item 1005). */
export function openPhoneAppSettings() {
  phoneApp()?.postMessage({ type: "openSettings" });
}

export function clearToken() {
  token = null;
  // The key is forgotten, so are the chats kept on this phone under it (item 1520).
  cacheToken = null;
  forget();
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored, nothing to remove.
  }
}

/** Whether this page signed in with a key of a phone's own (item 670), which cannot pair or remove phones. */
export function holdsPhoneKey() {
  return phoneIdOf(token) !== null;
}

export function hasToken() {
  return token !== null && token !== "";
}

/** Register the function that runs when the daemon rejects the token. */
export function onAuthLost(handler) {
  authLostHandler = handler;
}

function describeValidation(errors) {
  return errors
    .map((entry) => {
      const where = Array.isArray(entry.loc) ? entry.loc.filter((part) => part !== "body" && part !== "query").join(".") : "";
      return where === "" ? entry.msg : `${where}: ${entry.msg}`;
    })
    .join("; ");
}

/** The reason a rule of a form gave for refusing a value, as its author wrote it for a person, without the "Value error, "
 * the validator puts before it; null for any other kind of complaint. */
function ownReason(entry) {
  return entry.type === "value_error" && typeof entry.msg === "string" ? entry.msg.replace(/^Value error, /, "") : null;
}

function messageOf(status, data, text) {
  if (data !== null && typeof data === "object" && typeof data.message === "string") {
    const errors = data.context && Array.isArray(data.context.errors) ? data.context.errors : null;
    if (errors === null || errors.length === 0) {
      return data.message;
    }
    // A value Dot's own rule refused says why in its own words; any other complaint is the validator's.
    const reasons = errors.map(ownReason);
    return reasons.every((reason) => reason !== null) ? reasons.join("; ") : `What you entered is not valid (${describeValidation(errors)})`;
  }
  return text !== "" ? `HTTP ${status}: ${text.slice(0, 200)}` : `HTTP ${status}`;
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function errorOf(response, path) {
  const text = await response.text();
  const data = text === "" ? null : parseJson(text);
  if (response.status === 401 && authLostHandler !== null) {
    authLostHandler();
  }
  const context = data !== null && typeof data === "object" && data.context ? data.context : { path };
  return new ApiError(response.status, messageOf(response.status, data, text), context);
}

function authHeaders() {
  return { Authorization: `Bearer ${token}` };
}

function hexOf(buffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// A key of a phone's own (item 670): `phone.<id>.<secret>`. The daemon holds only its SHA-256, so its proof for such a key
// is keyed with that hash, and the page asks for it with the phone's id.
const PHONE_KEY = /^phone\.([0-9a-f]{12})\.[A-Za-z0-9_-]{43}$/;

/** The id of the phone whose key this page holds, or null when it holds the installation's token. */
function phoneIdOf(value) {
  const found = typeof value === "string" ? PHONE_KEY.exec(value) : null;
  return found === null ? null : found[1];
}

/** What the daemon answers to `nonce` when it holds this page's token: keyed with the token, or for a phone's key with
 * the hex SHA-256 of it (item 670). */
async function proofOf(nonce) {
  const encoder = new TextEncoder();
  const secret = phoneIdOf(token) === null ? token : hexOf(await crypto.subtle.digest("SHA-256", encoder.encode(token)));
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hexOf(await crypto.subtle.sign("HMAC", key, encoder.encode(`${PROOF_LABEL}${nonce}`)));
}

/**
 * Ask what answers at this address to prove that it holds the page's token. An answer with another proof is a token the
 * daemon does not hold, told as a refused token is; any other answer is not the daemon's.
 */
async function proveDaemon() {
  if (globalThis.crypto?.subtle === undefined) {
    throw new ApiError(0, "This browser cannot check that this address is really your Dot, so it sent nothing. Use the address Settings › Phone gives.", {
      path: "/proof",
    });
  }
  const nonce = hexOf(crypto.getRandomValues(new Uint8Array(NONCE_BYTES)));
  let response;
  // A daemon that does not answer the proof makes every read wait for it: on a phone it is given the time a read has.
  const limit = new AbortController();
  const timer = phoneScreen() ? setTimeout(() => limit.abort(), READ_TIMEOUT_MS) : null;
  try {
    const phone = phoneIdOf(token);
    response = await fetch(`/api/proof?nonce=${nonce}${phone === null ? "" : `&phone=${phone}`}`, { cache: "no-store", signal: limit.signal });
  } catch (cause) {
    noteUnreachable();
    throw new ApiError(0, UNREACHABLE, { path: "/proof", cause: String(cause) });
  } finally {
    clearTimeout(timer);
  }
  const text = await response.text();
  const data = response.ok ? parseJson(text) : null;
  if (data === null || typeof data !== "object" || typeof data.proof !== "string") {
    throw new ApiError(0, "Something other than Dot answers at this address, so this browser sent it nothing. Check the address.", {
      path: "/proof",
      status: response.status,
      start: text.slice(0, 80),
    });
  }
  if (data.proof !== (await proofOf(nonce))) {
    if (authLostHandler !== null) {
      authLostHandler();
    }
    throw new ApiError(401, "This Dot does not use that access key, so this browser sent it nothing. Sign in again.", {
      path: "/proof",
    });
  }
}

/**
 * Settles once the daemon has proven itself for a request about to be sent. Requests made while a proof is on its way
 * wait for that one; a proof that has settled serves no later request.
 */
function proven() {
  if (proving === null) {
    proving = proveDaemon().finally(() => {
      proving = null;
    });
  }
  return proving;
}

/** The event the page sends once a request of it changed a schedule (item 580): the daemon tells of no change of one,
 * and the sidebar marks the chats a loop goes on in. */
export const SCHEDULES_CHANGED_EVENT = "dot:schedules-changed";
// A request that changes a schedule: one of /schedules, or a chat's new loop (/runs/{id}/loop).
const SCHEDULE_CHANGE = /^\/(schedules(\/|$|\?)|runs\/[^/]+\/loop$)/;

/** The event the page sends once a request of it changed a team (item 610), made one by Use team or took an agent from
 * its teams (an agent changed or deleted): the daemon tells of no change of one, and the sidebar names the team of each
 * chat it lists and wears the faces of its agents. */
export const TEAMS_CHANGED_EVENT = "dot:teams-changed";
// A request that changes a team: one of /teams, a ready team's Use, or an agent that is changed or deleted.
const TEAM_CHANGE = /^\/(teams(\/|$|\?)|agent-teams\/[^/]+\/use$|agents\/[^/]+$)/;

/** The event the page sends once a request of it made, changed or deleted an agent (item 835): the daemon tells of no
 * change of one, and the sidebar shows the owner's agents as faces at its top. */
export const AGENTS_CHANGED_EVENT = "dot:agents-changed";
// A request that changes an agent: one of /agents (a new one, an import, a change, its look, a pause, a delete), or an
// agent made from a template or a ready team.
const AGENT_CHANGE = /^\/(agents(\/|$|\?)|agent-templates\/[^/]+\/use$|agent-teams\/[^/]+\/use$)/;

/** A request that starts a run of the owner's, or queues a message or resumes a queue that may start one at once (item
 * 1096); one that started none after all opens a card the daemon ends a minute later. */
const RUN_START = /^\/(runs|runs\/[^/]+\/(resume|loop)|threads\/[^/]+\/queue(\/resume)?)$/;

// How long a read of a list waits for the daemon when the phone holds a copy of it: past this the copy answers and the
// daemon's own answer, when it comes, replaces it (`FRESH_EVENT`), so that a slow link never keeps the first frame waiting.
const COPY_PATIENCE_MS = 3000;
/** The event the page sends when the daemon answered a read late, after the copy kept on the phone had answered it
 * (item 1521): `detail.path` is what was read. A page that drew the copy reads again. */
export const FRESH_EVENT = "dot:fresh";
// Whether the daemon has answered this page once: before it has, a read is tried once, so that a Mac that is out of reach
// shows its screen (main.js) instead of a page that waits for a link that is there.
let reachedOnce = false;
// The reads on their way, by path (item 1521): the same read asked again while it is under way waits for that one.
const flights = new Map();

/** Whether this is a phone's touch screen (no pointer that hovers). */
function phoneScreen() {
  return typeof matchMedia === "function" && matchMedia("(hover: none) and (pointer: coarse)").matches;
}

/** Whether this page reads as a phone's does on a bad link (item 1521): a touch screen, and after the daemon has answered
 * it once. A computer's page keeps its single try and its own timeouts. */
function resilient() {
  return reachedOnce && phoneScreen();
}

/** The error a cancelled read ends with, the page's own (a chat left): nothing is told of it. */
function cancelled(signal) {
  return signal?.reason instanceof Error ? signal.reason : new DOMException("The read was cancelled.", "AbortError");
}

/** Settles after `ms`; at once, rejected, when `signal` aborts. */
function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", stop);
      resolve(undefined);
    }
    function stop() {
      clearTimeout(timer);
      reject(cancelled(signal));
    }
    signal?.addEventListener("abort", stop, { once: true });
    if (signal?.aborted) {
      stop();
    }
  });
}

/** Settles once the phone has a link and the page is in view: no try is spent while there is none (item 1521). Rejected
 * when `signal` aborts. */
function untilLinkUp(signal) {
  const ready = () => navigator.onLine && document.visibilityState === "visible";
  return new Promise((resolve, reject) => {
    if (ready()) {
      resolve(undefined);
      return;
    }
    function check() {
      if (ready()) {
        cleanup();
        resolve(undefined);
      }
    }
    function stop() {
      cleanup();
      reject(cancelled(signal));
    }
    function cleanup() {
      window.removeEventListener("online", check);
      document.removeEventListener("visibilitychange", check);
      signal?.removeEventListener("abort", stop);
    }
    window.addEventListener("online", check);
    document.addEventListener("visibilitychange", check);
    signal?.addEventListener("abort", stop, { once: true });
    if (signal?.aborted) {
      stop();
    }
  });
}

/** One try of a read: `ask` with the time a read has. A try that outruns it ends as a request with no answer does. */
async function tryRead(path, signal, outcome) {
  const limit = new AbortController();
  const timer = setTimeout(() => limit.abort(new ApiError(0, UNREACHABLE, { path, cause: "no answer in time" })), READ_TIMEOUT_MS);
  const cancel = () => limit.abort(cancelled(signal));
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) {
    cancel();
  }
  try {
    return await ask("GET", path, null, limit.signal, outcome);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

/**
 * A read as a phone makes it on a bad link (item 1521, retryrules.js): each try gives up after 10 s, it is tried three
 * times with a wait that grows and has chance in it, and while the phone has no link at all, or the page is out of view,
 * it waits for the link and the view instead of spending its tries. What will not change (a refusal) is never asked again.
 */
async function readWithTries(path, signal, outcome) {
  const began = Date.now();
  let failed = 0;
  for (;;) {
    await untilLinkUp(signal);
    try {
      return await tryRead(path, signal, outcome);
    } catch (error) {
      if (signal?.aborted) {
        throw error;
      }
      const step = nextStep({
        failed: failed + 1,
        elapsed: Date.now() - began,
        chance: Math.random(),
        linkUp: navigator.onLine,
        failure: { status: error instanceof ApiError ? error.status : 0 },
      });
      if ("giveUp" in step) {
        throw error;
      }
      if ("wait" in step) {
        failed += 1;
        await pause(step.wait, signal);
      }
    }
  }
}

/**
 * The newest `count` events of the run `runId`, oldest first, and whether the daemon knows `tail` (item 1370: it says so in
 * the header `X-Dot-Events-Tail`; an older one answers the first events of the run, which are no end of it). Made as a
 * phone's reads are (item 1521); `signal` cancels it.
 * @returns {Promise<{ events: { id: number, run_id: string, kind: string, data: any }[], tailed: boolean }>}
 */
export async function readEventsTail(runId, count, signal) {
  const path = `/runs/${runId}/events${query({ tail: count })}`;
  const outcome = {};
  const events = resilient() ? await readWithTries(path, signal, outcome) : await ask("GET", path, null, signal, outcome);
  return { events, tailed: outcome.headers?.has("X-Dot-Events-Tail") === true };
}

/** The read of `path` that is on its way, or a new one; each caller has its own copy of the answer, and leaves it alone
 * when its `signal` aborts (the read is cancelled once every caller has left). */
function sharedRead(path, signal) {
  let flight = flights.get(path);
  if (flight === undefined) {
    const controller = new AbortController();
    const made = { promise: readWithTries(path, controller.signal), controller, callers: 0 };
    flight = made;
    flights.set(path, made);
    const over = () => {
      if (flights.get(path) === made) {
        flights.delete(path);
      }
    };
    made.promise.then(over, over);
  }
  const mine = flight;
  mine.callers += 1;
  return new Promise((resolve, reject) => {
    const leave = () => {
      mine.callers -= 1;
      if (mine.callers === 0) {
        mine.controller.abort(cancelled(signal));
      }
      reject(cancelled(signal));
    };
    if (signal?.aborted) {
      leave();
      return;
    }
    signal?.addEventListener("abort", leave, { once: true });
    mine.promise.then(
      (value) => {
        signal?.removeEventListener("abort", leave);
        resolve(mine.callers > 1 ? structuredClone(value) : value);
      },
      (error) => {
        signal?.removeEventListener("abort", leave);
        reject(error);
      },
    );
  });
}

/**
 * A request to the daemon. On a phone a read is made as `readWithTries` makes it and shared with the same read on its way
 * (item 1521); `signal` cancels it. A read of one of the lists the shell draws (cacherules.js `listKey`) is kept on the
 * phone as it is answered (item 1520), and while Dot cannot be reached, or does not answer within a few seconds, it is
 * answered from that copy instead, so that the page opens with its chats drawn on a link that is bad or gone;
 * `linkfacts.js` says the page shows a copy, and since when. A refusal of the daemon's, a rejected key among them, is
 * never answered from a copy.
 */
export async function request(method, path, body, signal) {
  if (method !== "GET") {
    if (!resilient() || !isKeptWrite(path)) {
      return ask(method, path, body);
    }
    // A write the outbox keeps (item 1523) gives up after 30 s on a phone, as a request with no answer does: what was done
    // is kept and sent again with its key, which the daemon answers with what the first made. Any other write may take
    // as long as it takes (a side answer, a voice, a file), and is left to.
    const limit = new AbortController();
    const timer = setTimeout(() => limit.abort(new ApiError(0, UNREACHABLE, { path, cause: "no answer in time" })), WRITE_TIMEOUT_MS);
    try {
      return await ask(method, path, body, limit.signal);
    } finally {
      clearTimeout(timer);
    }
  }
  // Before the daemon has answered it, a phone's page asks once and gives up after the time a read has, so that a link that
  // died while Dot opened leaves the page with its copies instead of waiting for it (item 1521).
  const read = () => (resilient() ? sharedRead(path, signal) : phoneScreen() ? tryRead(path, signal) : ask(method, path, body, signal));
  if (listKey(path) === null) {
    return read();
  }
  // The read of Dot goes out at once and the copy kept on the phone is looked for beside it: with a link, the cache is never
  // on the way of an answer (item 1520). With no link at all there is nothing to ask, and the copy answers.
  const asked = navigator.onLine ? read() : null;
  asked?.catch(() => undefined);
  const copy = cacheOn() ? await keptList(path) : null;
  if (asked === null) {
    if (copy === null) {
      return read();
    }
    noteCopy(copy.savedAt);
    return copy.value;
  }
  let value;
  try {
    value = copy === null ? await asked : await answeredInTime(asked, copy, path);
  } catch (error) {
    if (copy === null || !unreachable(error)) {
      throw error;
    }
    noteCopy(copy.savedAt);
    return copy.value;
  }
  if (value === LATE) {
    noteCopy(copy.savedAt);
    return copy.value;
  }
  if (cacheOn()) {
    keepList(path, value);
  }
  return value;
}

const LATE = Symbol("late");

/** The daemon's answer, or `LATE` once `COPY_PATIENCE_MS` has passed with the copy in hand: the answer that comes after is
 * kept and, when it is not what the copy said, told to the page (`FRESH_EVENT`), which reads again what it drew from the
 * copy (an answer that says the same would only make the page ask again, and a slow link ask for ever). */
async function answeredInTime(asked, copy, path) {
  let patient = null;
  const slow = new Promise((resolve) => {
    patient = setTimeout(() => resolve(LATE), COPY_PATIENCE_MS);
  });
  try {
    const first = await Promise.race([asked, slow]);
    if (first === LATE) {
      asked.then(
        (value) => {
          if (cacheOn()) {
            keepList(path, value);
          }
          if (JSON.stringify(value) !== JSON.stringify(copy.value)) {
            window.dispatchEvent(new CustomEvent(FRESH_EVENT, { detail: { path } }));
          }
        },
        () => undefined,
      );
    }
    return first;
  } finally {
    clearTimeout(patient);
  }
}

async function ask(method, path, body, signal, outcome) {
  await proven();
  const headers = authHeaders();
  let payload;
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body: payload, cache: "no-store", signal });
  } catch (cause) {
    // A read the page cancelled (a chat left) or that ran out of its time ends as the page decided, not as a lost link.
    if (signal?.aborted) {
      const why = cancelled(signal);
      if (why instanceof ApiError) {
        noteUnreachable();
      }
      throw why;
    }
    noteUnreachable();
    throw new ApiError(0, UNREACHABLE, { path, cause: String(cause) });
  }
  if (method !== "GET") {
    // A read on its way may return what was there before this change: the next one goes to the daemon (item 1169).
    sharedReads.clear();
    flights.clear();
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  if (outcome !== undefined) {
    outcome.headers = response.headers;
  }
  // Dot answered: what the page shows next is its word of now, not a copy kept on the phone (linkfacts.js).
  reachedOnce = true;
  noteReached();
  if (method !== "GET" && SCHEDULE_CHANGE.test(path)) {
    window.dispatchEvent(new CustomEvent(SCHEDULES_CHANGED_EVENT));
  }
  if (method !== "GET" && TEAM_CHANGE.test(path)) {
    window.dispatchEvent(new CustomEvent(TEAMS_CHANGED_EVENT));
  }
  // Chats deleted may take an agent's conversation with them (item 1233): its face and its page name the one now.
  if (method !== "GET" && (AGENT_CHANGE.test(path) || (method === "DELETE" && path === "/threads"))) {
    window.dispatchEvent(new CustomEvent(AGENTS_CHANGED_EVENT));
  }
  if (method === "POST" && RUN_START.test(path)) {
    // Dot's iPhone app opens the lock screen's card itself, while it is in front: one the daemon started would come
    // with a notification (item 1096). The daemon fills it, and ends it when no run is left.
    phoneApp()?.postMessage({ type: "working" });
  }
  const text = await response.text();
  if (text === "") {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (cause) {
    throw new ApiError(response.status, "Dot answered in a way this page cannot read. Try again in a moment.", {
      path,
      start: text.slice(0, 80),
      cause: String(cause),
    });
  }
}

// A pairing code as the daemon reads it (server/pairing.py): eight characters of Crockford's base32, the spaces and
// dashes left out, O read as 0 and I and L as 1.
const CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const CODE_CHARS = 8;
const CODE_READ_AS = { O: "0", I: "1", L: "1" };

/** Whether `text` can be a pairing code (item 267). An access token never can. */
function canBePairingCode(text) {
  const chars = [...text.toUpperCase()].filter((char) => char !== " " && char !== "-").map((char) => CODE_READ_AS[char] ?? char);
  return chars.length === CODE_CHARS && chars.every((char) => CODE_ALPHABET.includes(char));
}

/**
 * Trade a pairing code from Settings › Phone for the access token (item 267), for an app on a phone's home screen,
 * whose storage is its own. Only the code is sent; the token brought back is proven as any other before it is used.
 * The daemon is not proven before the code goes, so what cannot be a code is not sent at all: a token pasted into the
 * code's field would go to whatever answers on the port.
 */
export async function pairWithCode(code) {
  const path = "/phone/pair";
  if (!canBePairingCode(code)) {
    throw new Error("That does not look like a sign-in code. A code has 8 letters and digits, as Settings › Phone shows it.");
  }
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(0, UNREACHABLE, { path, cause: String(cause) });
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  const data = parseJson(await response.text());
  if (data === null || typeof data !== "object" || typeof data.token !== "string") {
    throw new ApiError(response.status, "Something other than Dot answered, so the code was not used. Check the address.", { path });
  }
  return data.token;
}

export const api = {
  /** A read; `options.signal` cancels it (a chat left, item 1521). */
  get: (path, options) => request("GET", path, null, options?.signal),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path, null),
};

// The reads of a path that are on their way (item 1169): parts of the page that want the same thing at once (the
// agents, as Dot opens) share one request. Nothing is kept once it answered.
const sharedReads = new Map();

/** `GET path`, shared with the same read already on its way; each caller gets a copy of its own, so that none sees
 * what another changes in it. Once a change (`request`) is answered, the next read goes to the daemon again; a read
 * asked while a change is on its way may share one from before it, and the page reads again on the change's own event
 * (`AGENTS_CHANGED_EVENT` and the others). */
export function sharedGet(path, signal) {
  if (resilient()) {
    // On a phone a read is shared with the same read on its way by `request` itself (item 1521), whose callers each leave
    // it by their own `signal`.
    return request("GET", path, null, signal);
  }
  let pending = sharedReads.get(path);
  if (pending === undefined) {
    pending = request("GET", path, null);
    sharedReads.set(path, pending);
    const done = () => {
      if (sharedReads.get(path) === pending) {
        sharedReads.delete(path);
      }
    };
    pending.then(done, done);
  }
  return pending.then((value) => structuredClone(value));
}

/** `?a=1&b=2` from an object; undefined, null and empty values are left out. */
export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

/**
 * What a read made once (a chat's stored steps) is given: the 45 seconds a stream was given before item 1522, since it has
 * no stream to go on after.
 */
export const ONE_SHOT = { connectMs: ONE_SHOT_PATIENCE_MS, idleMs: ONE_SHOT_PATIENCE_MS };

/**
 * Read a server-sent event stream with fetch, so the bearer token travels in a header and not in the URL, and so that the
 * keepalive, a comment line an `EventSource` cannot see, tells a quiet stream from a dead one (item 1522, sseframes.js).
 * `onOpen` is called once the server has taken the stream, before its first event. A connection that has answered
 * nothing in `options.connectMs` (8 s), and a stream from which nothing came for `options.idleMs` (35 s), not even a
 * keepalive, is cut: the read fails, as for a stream that broke; so is one silent for `BACK_SILENCE_MS`, or whose page
 * was put away for `awayMs` (`Infinity`: however long), when the page comes back into view. `options.lastId` is the id of
 * the last event the reader has, sent as `Last-Event-ID` beside the `after` of the path, so that a stream made again reads
 * no history again. A frame that is cut by a drop is not an event.
 * @param {string} path
 * @param {AbortSignal} signal
 * @param {() => void} onOpen
 * @param {number} awayMs
 * @param {{ connectMs?: number, idleMs?: number, lastId?: number }} [options]
 */
export async function* sse(path, signal, onOpen, awayMs, options = {}) {
  const connectMs = options.connectMs ?? CONNECT_TIMEOUT_MS;
  const idleMs = options.idleMs ?? IDLE_TIMEOUT_MS;
  await proven();
  const cut = new AbortController();
  const stop = () => cut.abort();
  signal.addEventListener("abort", stop, { once: true });
  if (signal.aborted) {
    stop();
  }
  let silence = null;
  let heardAt = Date.now();
  let awayAt = document.visibilityState === "visible" ? null : Date.now();
  /** Cut the stream after `ms` of silence, with `why`; the clock starts again each time it is called. */
  const watch = (ms, why) => {
    heardAt = Date.now();
    clearTimeout(silence);
    silence = setTimeout(() => cut.abort(new ApiError(0, why, { path })), ms);
  };
  const back = () => {
    if (document.visibilityState !== "visible") {
      awayAt = awayAt ?? Date.now();
      return;
    }
    const away = awayAt !== null && Date.now() - awayAt >= awayMs;
    awayAt = null;
    if (away || Date.now() - heardAt >= BACK_SILENCE_MS) {
      cut.abort(new ApiError(0, "Dot stopped sending updates while this page was away.", { path }));
    }
  };
  document.addEventListener("visibilitychange", back);
  let reader = null;
  try {
    watch(connectMs, "Dot did not answer in time.");
    const headers = { ...authHeaders(), Accept: "text/event-stream" };
    if (options.lastId !== undefined && options.lastId > 0) {
      headers["Last-Event-ID"] = String(options.lastId);
    }
    const response = await fetch(`/api${path}`, { headers, signal: cut.signal, cache: "no-store" });
    if (!response.ok) {
      throw await errorOf(response, path);
    }
    onOpen();
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = createParser();
    for (;;) {
      watch(idleMs, "Dot stopped sending updates.");
      const { done, value } = await reader.read();
      clearTimeout(silence);
      // A frame cut by the end of the stream is no event: the stream is made again after the last whole one.
      for (const frame of done ? parser.end() : parser.push(decoder.decode(value, { stream: true }))) {
        if ("bad" in frame) {
          console.warn("a frame of the stream was not JSON, and is skipped", { path, id: frame.id, event: frame.event });
        } else {
          yield { id: frame.id, event: frame.event, data: frame.data };
        }
      }
      if (done) {
        return;
      }
    }
  } finally {
    clearTimeout(silence);
    document.removeEventListener("visibilitychange", back);
    signal.removeEventListener("abort", stop);
    if (reader !== null) {
      await reader.cancel().catch(() => undefined);
    }
  }
}

/** The body of a binary stream of the API (Watch's screen), once its answer began; it ends when `signal` aborts. */
export async function binaryStream(path, signal) {
  await proven();
  let response;
  try {
    response = await fetch(`/api${path}`, { headers: authHeaders(), signal, cache: "no-store" });
  } catch (cause) {
    if (signal.aborted) {
      throw cause;
    }
    throw new ApiError(0, UNREACHABLE, { path, cause: String(cause) });
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  return response.body;
}

/**
 * Wait `ms` before a stream is tried again, or less: once the browser is back on the network, or the page back in
 * view (a Mac that woke), it is tried at once. Ends at once when `signal` aborts.
 * @param {number} ms
 * @param {AbortSignal} signal
 * @returns {Promise<void>}
 */
function untilRetry(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function inView() {
      if (document.visibilityState === "visible") {
        done();
      }
    }
    function done() {
      clearTimeout(timer);
      window.removeEventListener("online", done);
      document.removeEventListener("visibilitychange", inView);
      signal.removeEventListener("abort", done);
      resolve();
    }
    window.addEventListener("online", done);
    document.addEventListener("visibilitychange", inView);
    signal.addEventListener("abort", done);
  });
}

/**
 * Settles once the page is in view, or at once when `signal` aborts.
 * @param {AbortSignal} signal
 * @returns {Promise<void>}
 */
function untilInView(signal) {
  return new Promise((resolve) => {
    function check() {
      if (document.visibilityState === "visible" || signal.aborted) {
        document.removeEventListener("visibilitychange", check);
        signal.removeEventListener("abort", check);
        resolve();
      }
    }
    document.addEventListener("visibilitychange", check);
    signal.addEventListener("abort", check);
    check();
  });
}

/** Whether the daemon refused a stream: a rejected token or a missing resource, which no retry mends. */
function refused(error) {
  return error instanceof ApiError && (error.status === 401 || error.status === 404);
}

function withAfter(path, after) {
  return `${path}${path.includes("?") ? "&" : "?"}after=${after}`;
}

/**
 * Tell that the page failed to show an event of a stream: logged with the stream and the event, and shown as a toast
 * at most every few seconds. The stream goes on with the next event.
 */
export function eventNotShown(path, message, error) {
  console.error("an event could not be shown", { path, id: message.id, event: message.event, error });
  const why = error instanceof Error ? error.message : String(error);
  tellOutOfDate("Part of this page could not be updated. Reload the page to see it.", `${message.event}: ${why}`);
}

/** Tell in a toast that part of the page is out of date, at most every few seconds. */
export function tellOutOfDate(text, details = "") {
  const now = Date.now();
  if (now - notShownToldAt >= NOT_SHOWN_TOLD_EVERY_MS) {
    notShownToldAt = now;
    toast("error", text, null, details);
  }
}

/**
 * Follow a stream until `isFinished(message)` says so or `signal` aborts, reconnecting from the last event seen.
 * A rejected token or a missing resource ends the loop with the error; anything else is retried with a pause that
 * grows while the stream cannot be made and starts again from the shortest once it is (`untilRetry`).
 * The stream is said to be live once the server has taken it, and reconnecting when it broke or could not be made.
 * A fault of the page in showing one event is not a break of the stream: it is told, and the next event comes.
 */
export async function follow({ path, after, signal, onMessage, onState, isFinished }) {
  let lastId = after;
  let breaks = 0;
  while (!signal.aborted) {
    try {
      const opened = () => {
        breaks = 0;
        onState("live");
      };
      for await (const message of sse(withAfter(path, lastId), signal, opened, BACK_AWAY_MS, { lastId })) {
        lastId = Math.max(lastId, message.id);
        try {
          onMessage(message);
        } catch (error) {
          eventNotShown(path, message, error);
        }
        if (isFinished(message)) {
          return;
        }
      }
    } catch (error) {
      if (signal.aborted) {
        return;
      }
      if (refused(error)) {
        throw error;
      }
      console.warn("stream interrupted; reconnecting", { path, after: lastId, error });
    }
    if (signal.aborted) {
      return;
    }
    onState("reconnecting");
    await untilRetry(reconnectWait(breaks), signal);
    breaks += 1;
  }
}

/**
 * Read a stream whose events stand alone (the frames of a screencast: nothing to resume from) while the page is in
 * view, until the server ends it or `signal` aborts. Out of sight the stream is closed and made again once the page is
 * back, so a phone that put the page away finds it going on; a stream that breaks or goes silent is made again after
 * the pause `follow` takes (item 276). A refused stream ends the read with the error, and a fault of the page in showing
 * one event is told as in `follow`.
 */
export async function watchInView({ path, signal, onMessage }) {
  let breaks = 0;
  while (!signal.aborted) {
    await untilInView(signal);
    if (signal.aborted) {
      return;
    }
    // Closed by the read's end or by the page going out of sight; `sse` cuts it itself when it goes silent.
    const seen = new AbortController();
    const close = () => seen.abort();
    const hide = () => {
      if (document.visibilityState === "hidden") {
        seen.abort();
      }
    };
    signal.addEventListener("abort", close, { once: true });
    document.addEventListener("visibilitychange", hide);
    try {
      const opened = () => {
        breaks = 0;
      };
      // Closed while the page is away, so its return never finds it open.
      for await (const message of sse(path, seen.signal, opened, Infinity)) {
        try {
          onMessage(message);
        } catch (error) {
          eventNotShown(path, message, error);
        }
      }
      return;
    } catch (error) {
      if (signal.aborted) {
        return;
      }
      if (refused(error)) {
        throw error;
      }
      if (seen.signal.aborted) {
        continue;
      }
      console.warn("stream interrupted; reconnecting", { path, error });
    } finally {
      signal.removeEventListener("abort", close);
      document.removeEventListener("visibilitychange", hide);
    }
    await untilRetry(reconnectWait(breaks), signal);
    breaks += 1;
  }
}

// The screenshots of the page on screen, by name: a page change lets them go (`forgetImages`), so a window kept open
// for days does not keep every screenshot it ever showed.
const images = new Map();

/** Let go of the screenshots' `blob:` URLs: the page that showed them has closed. */
export function forgetImages() {
  for (const pending of images.values()) {
    // A screenshot that failed was reported where it was shown; there is no URL to let go of.
    pending.then((url) => URL.revokeObjectURL(url), () => undefined);
  }
  images.clear();
}

/**
 * A `blob:` URL for the picture the daemon answers at `path` (a page of a PDF, an image of a file), fetched with the token
 * and never kept in the browser's cache: the file may change. Whoever asked lets it go with `URL.revokeObjectURL`.
 */
export async function blobUrl(path) {
  await proven();
  let response;
  try {
    response = await fetch(`/api${path}`, { headers: authHeaders(), cache: "no-store" });
  } catch (cause) {
    throw new ApiError(0, UNREACHABLE, { path, cause: String(cause) });
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  return URL.createObjectURL(await response.blob());
}

/** A `blob:` URL for a frame of a screencast, a JPEG sent in base64, which the caller revokes. A plain loop decodes it
 * (item 674): `Uint8Array.from` with a mapping function calls it once a byte, twenty times as slow, and a watched
 * browser sends several frames a second. */
export function frameUrl(base64) {
  const text = atob(base64);
  const bytes = new Uint8Array(text.length);
  for (let index = 0; index < text.length; index += 1) {
    bytes[index] = text.charCodeAt(index);
  }
  return URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
}

/** A `blob:` URL for a stored screenshot; images need the token, so they cannot be plain `<img src>` links. */
export function imageUrl(name) {
  let pending = images.get(name);
  if (pending === undefined) {
    pending = proven()
      .then(() => fetch(`/api/images/${encodeURIComponent(name)}`, { headers: authHeaders(), cache: "force-cache" }))
      .then(async (response) => {
        if (!response.ok) {
          throw await errorOf(response, `/images/${name}`);
        }
        return URL.createObjectURL(await response.blob());
      });
    images.set(name, pending);
    pending.catch(() => images.delete(name));
  }
  return pending;
}
