// What the run's context says of a tool call and of a memory change (items 539 and 546): the skills and MCP servers it
// used, the pages and searches it read and the notes it saved, each once. The DOM sections that show them are
// contextpanelview.js's; this module keeps that state plain, so it is read without a page.

// The most readings and notes a run's context keeps, newest first.
const READING_LIMIT = 8;
const MEMORY_LIMIT = 6;
const PAGE_TOOLS = new Set(["web_fetch", "web_crawl", "browser_open", "browser_new_tab"]);
const SEARCH_TOOLS = new Set(["web_search"]);
const SKILL_TOOL = "skill";
const MCP_PREFIX = "mcp__";

/** The tool a call names as it is known by its own name: `mcp__server__web_fetch` reads `web_fetch`. */
function toolName(tool) {
  const cut = tool.lastIndexOf("__");
  return cut === -1 ? tool : tool.slice(cut + 2);
}

/** What a tool call says the run used (item 546): the skill the `skill` tool named (`{ kind: "skill", name }`), the
 * MCP server a tool of it belongs to (`{ kind: "server", name }`), or nothing for the run's own tools. */
export function usageOf(tool, args) {
  if (tool === SKILL_TOOL) {
    // A skill read in parts is one use: only the call that begins it counts (tools/skill.py's `start`).
    if (typeof args.start === "number" && args.start > 0) {
      return null;
    }
    const name = typeof args.name === "string" ? args.name.trim() : "";
    return name === "" ? null : { kind: "skill", name };
  }
  if (!tool.startsWith(MCP_PREFIX)) {
    return null;
  }
  const rest = tool.slice(MCP_PREFIX.length);
  const cut = rest.indexOf("__");
  const name = cut === -1 ? rest : rest.slice(0, cut);
  return name === "" ? null : { kind: "server", name };
}

/** `list` with `usage` counted, each skill or server once in the order it was first used; `count` says how many calls
 * of it the run made. */
export function addUsage(list, usage) {
  const at = list.findIndex((item) => item.kind === usage.kind && item.name === usage.name);
  if (at === -1) {
    return [...list, { kind: usage.kind, name: usage.name, count: 1 }];
  }
  return list.map((item, index) => (index === at ? { ...item, count: item.count + 1 } : item));
}

// The mark a file's kind wears (item 547), named as the file under `static/icons/languages` is: Simple Icons' slug.
const LANGUAGE_MARKS = new Map([
  ["py", "python"],
  ["pyi", "python"],
  ["pyw", "python"],
  ["js", "javascript"],
  ["mjs", "javascript"],
  ["cjs", "javascript"],
  ["jsx", "javascript"],
  ["ts", "typescript"],
  ["mts", "typescript"],
  ["cts", "typescript"],
  ["tsx", "typescript"],
  ["json", "json"],
  ["jsonc", "json"],
  ["css", "css"],
  ["scss", "sass"],
  ["sass", "sass"],
  ["html", "html5"],
  ["htm", "html5"],
  ["xml", "xml"],
  ["svg", "svg"],
  ["sh", "gnubash"],
  ["bash", "gnubash"],
  ["zsh", "gnubash"],
  ["fish", "gnubash"],
  ["rs", "rust"],
  ["go", "go"],
  ["java", "openjdk"],
  ["kt", "kotlin"],
  ["kts", "kotlin"],
  ["c", "c"],
  ["h", "c"],
  ["cc", "cplusplus"],
  ["cpp", "cplusplus"],
  ["cxx", "cplusplus"],
  ["hpp", "cplusplus"],
  ["hh", "cplusplus"],
  ["cs", "dotnet"],
  ["php", "php"],
  ["rb", "ruby"],
  ["sql", "sqlite"],
  ["toml", "toml"],
  ["yaml", "yaml"],
  ["yml", "yaml"],
  ["md", "markdown"],
  ["markdown", "markdown"],
  ["mdx", "markdown"],
  ["swift", "swift"],
  ["dart", "dart"],
  ["vue", "vuedotjs"],
  ["svelte", "svelte"],
  ["lua", "lua"],
  ["r", "r"],
  ["scala", "scala"],
  ["ex", "elixir"],
  ["exs", "elixir"],
  ["hs", "haskell"],
  ["pl", "perl"],
  ["pm", "perl"],
  ["clj", "clojure"],
  ["cljs", "clojure"],
  ["cljc", "clojure"],
  ["ipynb", "jupyter"],
  ["dockerfile", "docker"],
  ["gradle", "gradle"],
  ["cmake", "cmake"],
]);

// The files whose own name is their language's: they have no extension to read.
const WHOLE_NAME_MARKS = new Map([
  ["dockerfile", "docker"],
  ["cmakelists.txt", "cmake"],
]);

/** The mark of a file's language (item 547), by its own name or extension: a slug of `static/icons/languages`, or null
 * for a kind with no mark of its own, which wears its letters instead. */
export function languageOf(path) {
  const name = path.slice(path.lastIndexOf("/") + 1).toLowerCase();
  const whole = WHOLE_NAME_MARKS.get(name);
  if (whole !== undefined) {
    return whole;
  }
  const dot = name.lastIndexOf(".");
  return dot === -1 ? null : (LANGUAGE_MARKS.get(name.slice(dot + 1)) ?? null);
}

const SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const HOST_AND_PORT = /^[\w-]+(\.[\w-]+)*:\d+/;
const HOST_LIKE = /^(localhost|[\w-]+(\.[\w-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$/;

/** `address` as the browser's tools read it (browser/safety.py's `normalize_url`): a bare host, or a host and port,
 * gets `https://`, and a loopback one `http://`. */
function absoluteAddress(address) {
  const text = address.trim();
  if (SCHEME.test(text) && !HOST_AND_PORT.test(text)) {
    return text;
  }
  if (HOST_LIKE.test(text)) {
    const scheme = text.startsWith("localhost") || text.startsWith("127.0.0.1") ? "http" : "https";
    return `${scheme}://${text}`;
  }
  return text;
}

/** A page a run read, as a line (the host, without its `www.`, and the path, without a query or a fragment) and the
 * address it points at; null for anything a browser does not open: another protocol, a local file, a malformed
 * address. `bareHost` reads an address with no scheme as the browser's tools do. */
function pageReading(address, bareHost = false) {
  if (typeof address !== "string") {
    return null;
  }
  const text = bareHost ? absoluteAddress(address) : address;
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.host === "") {
    return null;
  }
  return { kind: "page", text: `${url.host.replace(/^www\./, "")}${url.pathname}`, url: text };
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
    return (Array.isArray(args.sources) ? args.sources : [])
      .map((source) => pageReading(source))
      .filter((reading) => reading !== null);
  }
  if (PAGE_TOOLS.has(name)) {
    const reading = pageReading(args.url, true);
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
