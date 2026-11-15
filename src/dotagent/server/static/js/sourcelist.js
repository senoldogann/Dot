// The pages a turn read on the web (item 1184): what its answer stands on, listed under it as an answer from a search
// assistant lists its sources. Pure, so that node's tests run it: the run's events in, the pages out.
//
// A page is counted from what the run asked for, never from what the page said: the address `web_fetch` was given (or
// the one it ended at after redirects, as the fence around the page names it) and the addresses `web_extract` read
// (those its result does not list as not extracted).
// The title is the page's own `Title:` line, as the fetch writes it at the head of the page's text. It is written by
// strangers, so the page shows it as text and nothing else, and a title longer than a line is cut.

/** @typedef {{ url: string, host: string, title: string }} Source */
/** @typedef {{ kind: string, data: Record<string, unknown> }} SourceEvent */

// The tools whose successful result is a page the run read.
const FETCH_TOOL = "web_fetch";
const EXTRACT_TOOL = "web_extract";
// At most this many pages a turn lists: past it the list says how many more it read.
export const MAX_SOURCES = 12;
// A title longer than this is cut at a word.
const TITLE_CHARS = 120;
// The first fence of the result, the one the fetch put the page in (the page's own text comes after it, so a fence the
// page wrote is never the first): its `url`, where the fetch ended after any redirect,
// quoted as `fence_untrusted` quotes it (a backslash and a double quote each escaped with a backslash), and the
// page's title when the fetch wrote one on the fence's first line.
const FENCE = /<web_content nonce="[^"]*" url="((?:[^"\\]|\\.)+)">\n(?:Title: ([^\n]+))?/;

/** Whether the tool `tool` reads pages on the web: its calls and results are what `sourcesOf` reads. */
export function readsPages(tool) {
  return tool === FETCH_TOOL || tool === EXTRACT_TOOL;
}

/** The host of `address` without a leading "www.", or "" for an address that is not http(s). */
function hostOf(address) {
  let parsed;
  try {
    parsed = new URL(address);
  } catch {
    return "";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "";
  }
  return parsed.hostname.toLowerCase().replace(/^www\./, "");
}

/** `text` on one line and at most TITLE_CHARS long, cut at a word with an ellipsis. */
function clipped(text) {
  const line = text.replace(/\s+/g, " ").trim();
  if (line.length <= TITLE_CHARS) {
    return line;
  }
  const cut = line.slice(0, TITLE_CHARS);
  const space = cut.lastIndexOf(" ");
  return `${(space > TITLE_CHARS / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** The page `web_fetch` read: the address it ended at when the fence names an http(s) one, else the one it was given,
 * and the page's title when the fetch found one. Null for an address that is not http(s). */
function fetchedPage(asked, text) {
  const fence = FENCE.exec(text);
  const fenced = (fence?.[1] ?? "").replace(/\\(.)/g, "$1");
  const url = hostOf(fenced) !== "" ? fenced : asked;
  const host = hostOf(url);
  if (host === "") {
    return null;
  }
  const title = fence?.[2] ?? "";
  return { url, host, title: clipped(title) };
}

// Where an extraction's result lists the sources it could not read, one `- <address>: <why>` line each, after its fence
// (`render_extractions`, tools/web.py; the fence's JSON writes no line of its own there).
const NOT_EXTRACTED = "\nNot extracted:\n";

/** The addresses among `asked` that the extraction whose result is `text` read: those its result does not list as not
 * extracted. */
function extracted(asked, text) {
  const at = text.lastIndexOf(NOT_EXTRACTED);
  const missed = at === -1 ? "" : text.slice(at + NOT_EXTRACTED.length - 1);
  return asked.filter((url) => !missed.includes(`\n- ${url}: `));
}

/** The strings among `value`, an argument a model wrote: anything else is no address. */
function strings(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
}

/**
 * The pages the run read, in the order its calls started (the results of calls made together come back in any order),
 * each once (by address), from its `tool_call` and `tool_result` events: a fetch or an extraction that failed read nothing. The first MAX_SOURCES are listed, and `more` counts the
 * rest.
 * @param {SourceEvent[]} events
 * @returns {{ sources: Source[], more: number }}
 */
export function sourcesOf(events) {
  /** @type {Map<string, { tool: string, args: Record<string, unknown>, order: number }>} */
  const calls = new Map();
  /** @type {Map<string, { page: Source, order: number }>} */
  const pages = new Map();
  let seen = 0;
  for (const event of events) {
    const callId = typeof event.data.call_id === "string" ? event.data.call_id : "";
    if (event.kind === "tool_call") {
      const tool = typeof event.data.tool === "string" ? event.data.tool : "";
      const args = event.data.args;
      if (readsPages(tool) && typeof args === "object" && args !== null) {
        calls.set(callId, { tool, args: /** @type {Record<string, unknown>} */ (args), order: seen });
        seen += 1;
      }
      continue;
    }
    const call = event.kind === "tool_result" ? calls.get(callId) : undefined;
    if (call === undefined || event.data.ok !== true) {
      continue;
    }
    calls.delete(callId);
    const text = typeof event.data.text === "string" ? event.data.text : "";
    const read =
      call.tool === FETCH_TOOL
        ? [typeof call.args.url === "string" ? fetchedPage(call.args.url, text) : null]
        : extracted(strings(call.args.sources), text).map((url) => (hostOf(url) === "" ? null : { url, host: hostOf(url), title: "" }));
    for (const page of read) {
      const known = page === null ? undefined : pages.get(page.url);
      if (page !== null && (known === undefined || known.order > call.order)) {
        pages.set(page.url, { page, order: call.order });
      }
    }
  }
  const all = [...pages.values()].sort((one, other) => one.order - other.order).map((entry) => entry.page);
  return { sources: all.slice(0, MAX_SOURCES), more: Math.max(0, all.length - MAX_SOURCES) };
}

/** The hosts of `sources`, each once, in their order: the faces the row shows before it is opened. */
export function hostsOf(sources) {
  return [...new Set(sources.map((source) => source.host))];
}
