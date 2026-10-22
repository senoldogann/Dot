// What the run's context says of a tool call and of a memory change (item 539): the pages and searches it read, and the
// notes it saved, each once and newest first. The DOM sections that show them are contextpanelview.js's; this module
// keeps that state plain, so it is read without a page.

// The most readings and notes a run's context keeps, newest first.
const READING_LIMIT = 8;
const MEMORY_LIMIT = 6;
const PAGE_TOOLS = new Set(["web_fetch", "web_crawl", "browser_open", "browser_new_tab"]);
const SEARCH_TOOLS = new Set(["web_search"]);

/** The tool a call names as it is known by its own name: `mcp__server__web_fetch` reads `web_fetch`. */
function toolName(tool) {
  const cut = tool.lastIndexOf("__");
  return cut === -1 ? tool : tool.slice(cut + 2);
}

/** A page a run read, as a line (the host, without its `www.`, and the path, without a query or a fragment) and the
 * address it points at; null for anything a browser does not open: another protocol, a local file, a malformed
 * address. */
function pageReading(address) {
  if (typeof address !== "string") {
    return null;
  }
  let url;
  try {
    url = new URL(address);
  } catch {
    return null;
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.host === "") {
    return null;
  }
  return { kind: "page", text: `${url.host.replace(/^www\./, "")}${url.pathname}`, url: address };
}

/**
 * What a tool call read, as `{ kind: "page", text, url }` and `{ kind: "search", text }`: the query of a search, the
 * page of a read or a crawl (a crawl reads its root), each source of an extract that is a page; nothing for a tool that
 * read nothing, and nothing for a search with no words.
 */
export function readingsOf(tool, args) {
  const name = toolName(tool);
  if (SEARCH_TOOLS.has(name)) {
    const query = typeof args.query === "string" ? args.query.trim() : "";
    return query === "" ? [] : [{ kind: "search", text: query }];
  }
  if (name === "web_extract") {
    return (Array.isArray(args.sources) ? args.sources : []).map(pageReading).filter((reading) => reading !== null);
  }
  if (PAGE_TOOLS.has(name)) {
    const reading = pageReading(args.url);
    return reading === null ? [] : [reading];
  }
  return [];
}

/** The key by which a reading is told once: its address, or its words. */
function readingKey(reading) {
  return reading.kind === "page" ? `page:${reading.url}` : `search:${reading.text}`;
}

/** `list` with `readings` added, the newest first, each reading once (a page read again moves to the front), at most
 * `limit` of them. */
export function addReadings(list, readings, limit = READING_LIMIT) {
  const seen = new Set();
  const kept = [];
  for (const reading of [...readings, ...list]) {
    const key = readingKey(reading);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    kept.push(reading);
    if (kept.length === limit) {
      break;
    }
  }
  return kept;
}

/** `list` with `change` added, the newest first, a note told once as it stands now, at most `limit` of them. */
export function addMemory(list, change, limit = MEMORY_LIMIT) {
  const seen = new Set();
  const kept = [];
  for (const note of [change, ...list]) {
    if (seen.has(note.id)) {
      continue;
    }
    seen.add(note.id);
    kept.push(note);
    if (kept.length === limit) {
      break;
    }
  }
  return kept;
}
