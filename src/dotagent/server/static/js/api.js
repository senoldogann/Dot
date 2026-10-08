// The client of the Dot daemon API: bearer token, JSON requests, server-sent events and images.

import { sleep } from "./dom.js";

const TOKEN_KEY = "dot.token";
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 8000;

let token = null;
let authLostHandler = null;

export class ApiError extends Error {
  constructor(status, message, context) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.context = context;
  }
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

export function setToken(value) {
  token = value;
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

function messageOf(status, data, text) {
  if (data !== null && typeof data === "object" && typeof data.message === "string") {
    const errors = data.context && Array.isArray(data.context.errors) ? data.context.errors : null;
    return errors !== null && errors.length > 0 ? `${data.message} (${describeValidation(errors)})` : data.message;
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

export async function request(method, path, body) {
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
    throw new ApiError(0, "Cannot reach the Dot daemon. Is it running?", { path, cause: String(cause) });
  }
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  const text = await response.text();
  return text === "" ? null : JSON.parse(text);
}

export const api = {
  get: (path) => request("GET", path, null),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
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

/** Read a server-sent event stream with fetch, so the bearer token travels in a header and not in the URL. */
export async function* sse(path, signal) {
  const response = await fetch(`/api${path}`, {
    headers: { ...authHeaders(), Accept: "text/event-stream" },
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    throw await errorOf(response, path);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
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
    await reader.cancel().catch(() => undefined);
  }
}

function withAfter(path, after) {
  return `${path}${path.includes("?") ? "&" : "?"}after=${after}`;
}

/**
 * Follow a stream until `isFinished(message)` says so or `signal` aborts, reconnecting from the last event seen.
 * A rejected token or a missing resource ends the loop with the error; anything else is retried with a growing pause.
 */
export async function follow({ path, after, signal, onMessage, onState, isFinished }) {
  let lastId = after;
  let pause = RECONNECT_MIN_MS;
  while (!signal.aborted) {
    try {
      onState("live");
      for await (const message of sse(withAfter(path, lastId), signal)) {
        pause = RECONNECT_MIN_MS;
        lastId = Math.max(lastId, message.id);
        onMessage(message);
        if (isFinished(message)) {
          return;
        }
      }
    } catch (error) {
      if (signal.aborted) {
        return;
      }
      if (error instanceof ApiError && (error.status === 401 || error.status === 404)) {
        throw error;
      }
      console.warn("stream interrupted; reconnecting", { path, after: lastId, error });
    }
    if (signal.aborted) {
      return;
    }
    onState("reconnecting");
    await sleep(pause, signal);
    pause = Math.min(pause * 2, RECONNECT_MAX_MS);
  }
}

const images = new Map();

/** A `blob:` URL for a stored screenshot; images need the token, so they cannot be plain `<img src>` links. */
export function imageUrl(name) {
  let pending = images.get(name);
  if (pending === undefined) {
    pending = fetch(`/api/images/${encodeURIComponent(name)}`, { headers: authHeaders(), cache: "force-cache" }).then(
      async (response) => {
        if (!response.ok) {
          throw await errorOf(response, `/images/${name}`);
        }
        return URL.createObjectURL(await response.blob());
      },
    );
    images.set(name, pending);
    pending.catch(() => images.delete(name));
  }
  return pending;
}
