// The client of the Dot daemon API: bearer token, JSON requests, server-sent events and images.

import { UNREACHABLE } from "./errors.js";
import { toast } from "./ui.js";

const TOKEN_KEY = "dot.token";
// The daemon proves that it holds the token before a request carries it there (`GET /api/proof`, `daemon_proof` in
// server/guard.py): a program that took the daemon's port while it was down cannot, and is sent nothing.
const PROOF_LABEL = "dot daemon proof\n";
const NONCE_BYTES = 32;
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 8000;
// The daemon writes a keepalive into every stream at least every 15 s (`_KEEPALIVE_S` in server/stream.py): a stream
// silent for three of them has stopped without closing (a network that changed, a Mac that slept) and is cut, to be
// followed again (item 200).
const STALL_MS = 45000;
// A page back in view whose stream has been silent for longer than a keepalive takes was put away (a phone that locked,
// a Mac that slept): its stream is cut at once and followed again, rather than after the rest of `STALL_MS` (item 286).
const BACK_SILENCE_MS = 20000;
// An event the page fails to show, or a read of a page that fails, is logged every time and told at most this often,
// so a fault that hits every event of a busy run does not bury the page in toasts.
const NOT_SHOWN_TOLD_EVERY_MS = 10000;
// How long a stream may be down before the page says so: one that is followed again sooner, as a page back in view
// does, says nothing (item 286).
export const QUIET_BREAK_MS = 2000;
let token = null;
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
  return token;
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
}

export function clearToken() {
  token = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored, nothing to remove.
  }
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

/** What the daemon answers to `nonce` when it holds this page's token. */
async function proofOf(nonce) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
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
  try {
    response = await fetch(`/api/proof?nonce=${nonce}`, { cache: "no-store" });
  } catch (cause) {
    throw new ApiError(0, UNREACHABLE, { path: "/proof", cause: String(cause) });
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

export async function request(method, path, body) {
  await proven();
  const headers = authHeaders();
  let payload;
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body: payload, cache: "no-store" });
  } catch (cause) {
    throw new ApiError(0, UNREACHABLE, { path, cause: String(cause) });
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  if (method !== "GET" && SCHEDULE_CHANGE.test(path)) {
    window.dispatchEvent(new CustomEvent(SCHEDULES_CHANGED_EVENT));
  }
  if (method !== "GET" && TEAM_CHANGE.test(path)) {
    window.dispatchEvent(new CustomEvent(TEAMS_CHANGED_EVENT));
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
  get: (path) => request("GET", path, null),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path, null),
};

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

function parseBlock(block) {
  let id = 0;
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "id") {
      id = Number(value) || 0;
    } else if (field === "event") {
      event = value;
    } else if (field === "data") {
      data.push(value);
    }
  }
  return data.length === 0 ? null : { id, event, data: JSON.parse(data.join("\n")) };
}

/**
 * Read a server-sent event stream with fetch, so the bearer token travels in a header and not in the URL. `onOpen` is
 * called once the server has taken the stream, before its first event. A stream from which nothing came for
 * `STALL_MS`, not even a keepalive, is cut: the read fails, as for a stream that broke; so is one silent for
 * `BACK_SILENCE_MS` when the page comes back into view.
 */
export async function* sse(path, signal, onOpen) {
  await proven();
  const cut = new AbortController();
  const stop = () => cut.abort();
  signal.addEventListener("abort", stop, { once: true });
  if (signal.aborted) {
    stop();
  }
  let silence = null;
  let heardAt = Date.now();
  const watch = () => {
    heardAt = Date.now();
    clearTimeout(silence);
    silence = setTimeout(() => cut.abort(new ApiError(0, "Dot stopped sending updates.", { path })), STALL_MS);
  };
  const back = () => {
    if (document.visibilityState === "visible" && Date.now() - heardAt >= BACK_SILENCE_MS) {
      cut.abort(new ApiError(0, "Dot stopped sending updates while this page was away.", { path }));
    }
  };
  document.addEventListener("visibilitychange", back);
  let reader = null;
  try {
    watch();
    const response = await fetch(`/api${path}`, {
      headers: { ...authHeaders(), Accept: "text/event-stream" },
      signal: cut.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      throw await errorOf(response, path);
    }
    onOpen();
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      watch();
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      for (let cut = buffer.indexOf("\n\n"); cut !== -1; cut = buffer.indexOf("\n\n")) {
        const message = parseBlock(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (message !== null) {
          yield message;
        }
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

/**
 * Wait `ms` before a stream is tried again, or less: once the browser is back on the network, or the page back in
 * view (a Mac that woke), it is tried at once. Ends at once when `signal` aborts.
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

/** Settles once the page is in view, or at once when `signal` aborts. */
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
  let pause = RECONNECT_MIN_MS;
  while (!signal.aborted) {
    try {
      const opened = () => {
        pause = RECONNECT_MIN_MS;
        onState("live");
      };
      for await (const message of sse(withAfter(path, lastId), signal, opened)) {
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
    await untilRetry(pause, signal);
    pause = Math.min(pause * 2, RECONNECT_MAX_MS);
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
  let pause = RECONNECT_MIN_MS;
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
        pause = RECONNECT_MIN_MS;
      };
      for await (const message of sse(path, seen.signal, opened)) {
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
    await untilRetry(pause, signal);
    pause = Math.min(pause * 2, RECONNECT_MAX_MS);
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
