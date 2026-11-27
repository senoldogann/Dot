// A file a chat's runs changed, read for its view (item 2017): the unified diff read into numbered rows, a new file
// read as the file, a format chosen from its name (code by language, Markdown, JSON, an image or a PDF) and code coloured
// by a small scanner of its language. The phone reads the same way (DotEngine's ChangeView.swift); the two keep one set
// of rules. Pure: no DOM here, changes.js draws.

/**
 * @typedef {{ kind: "hunk" | "added" | "removed" | "context", old: number | null, new: number | null, text: string }} DiffRow
 * @typedef {{ kind: "plain" | "keyword" | "string" | "comment" | "number" | "key" | "heading", text: string }} CodeToken
 * @typedef {{ kind: "code", language: string | null } | { kind: "markdown" } | { kind: "json" } | { kind: "image" } | { kind: "pdf" }} ChangeFormat
 * @typedef {{ kind: "diff", rows: DiffRow[] } | { kind: "newFile", text: string, format: ChangeFormat } | { kind: "media", format: ChangeFormat } | { kind: "note", note: string }} ChangeLayout
 * @typedef {{ path: string, created: boolean, gone: boolean, diff: string, note: string | null }} ChangedFile
 */

/** `@@ -a,b +c,d @@ heading`: the first old and new line numbers and the heading; null for no such head.
 * @param {string} line
 * @returns {{ old: number, new: number, heading: string } | null} */
function hunkHead(line) {
  const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/.exec(line);
  return match === null ? null : { old: Number(match[1]), new: Number(match[2]), heading: match[3].trim() };
}

/** The rows of the unified diff `diff`: the `---`/`+++` heads before the first hunk and `\ No newline` marks are left out.
 * @param {string} diff
 * @returns {DiffRow[]} */
export function diffRows(diff) {
  /** @type {DiffRow[]} */
  const rows = [];
  let old = 0;
  let now = 0;
  let inHunk = false;
  for (const line of diff.split("\n")) {
    const head = line.startsWith("@@") ? hunkHead(line) : null;
    if (head !== null) {
      old = head.old;
      now = head.new;
      inHunk = true;
      rows.push({ kind: "hunk", old: head.old, new: head.new, text: head.heading });
      continue;
    }
    if (!inHunk || line === "") {
      continue;
    }
    const text = line.slice(1);
    if (line[0] === "+") {
      rows.push({ kind: "added", old: null, new: now, text });
      now += 1;
    } else if (line[0] === "-") {
      rows.push({ kind: "removed", old, new: null, text });
      old += 1;
    } else if (line[0] === " ") {
      rows.push({ kind: "context", old, new: now, text });
      old += 1;
      now += 1;
    }
  }
  return rows;
}

/** Whether the rows are a whole file made new: the runs created it, or its one diff adds every line to nothing.
 * @param {DiffRow[]} rows
 * @param {boolean} created */
export function isNewFile(rows, created) {
  if (created) {
    return true;
  }
  const first = rows[0];
  if (first === undefined || first.kind !== "hunk" || first.old !== 0) {
    return false;
  }
  return rows.every((row) => row.kind === "hunk" || row.kind === "added");
}

/** @type {Record<string, string>} */
const LANGUAGE_BY_EXTENSION = {
  py: "python",
  pyi: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  cts: "typescript",
  swift: "swift",
  json: "json",
  jsonl: "json",
  webmanifest: "json",
  toml: "toml",
  yaml: "yaml",
  yml: "yaml",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  md: "markdown",
  markdown: "markdown",
  html: "html",
  htm: "html",
  xml: "html",
  svg: "html",
  plist: "html",
  css: "css",
  scss: "css",
  rs: "rust",
  go: "go",
};

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff"]);

/** The file's extension in lower case, "" for none.
 * @param {string} path */
function fileExtension(path) {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/** The language of the file at `path`, by its extension; null for one the scanner does not know.
 * @param {string} path
 * @returns {string | null} */
export function codeLanguage(path) {
  return LANGUAGE_BY_EXTENSION[fileExtension(path)] ?? null;
}

/** How the file at `path` is shown, by its extension.
 * @param {string} path
 * @returns {ChangeFormat} */
export function changeFormat(path) {
  const ext = fileExtension(path);
  if (IMAGE_EXTENSIONS.has(ext)) {
    return { kind: "image" };
  }
  if (ext === "pdf") {
    return { kind: "pdf" };
  }
  if (ext === "md" || ext === "markdown") {
    return { kind: "markdown" };
  }
  if (ext === "json") {
    return { kind: "json" };
  }
  return { kind: "code", language: LANGUAGE_BY_EXTENSION[ext] ?? null };
}

/** The layout of `file`'s change: its diff, the new file it is, an image or PDF shown from the Mac, or why it has none.
 * @param {ChangedFile} file
 * @returns {ChangeLayout} */
export function changeLayout(file) {
  const format = changeFormat(file.path);
  if ((format.kind === "image" || format.kind === "pdf") && !file.gone) {
    return { kind: "media", format };
  }
  const rows = diffRows(file.diff);
  if (!rows.some((row) => row.kind !== "hunk")) {
    return { kind: "note", note: file.note ?? "" };
  }
  if (isNewFile(rows, file.created)) {
    const text = rows
      .filter((row) => row.kind === "added")
      .map((row) => row.text)
      .join("\n");
    return { kind: "newFile", text, format };
  }
  return { kind: "diff", rows };
}

/** `text` as JSON laid out two spaces to a level, its keys in their own order; null when it is no one JSON value (JSON
 * Lines, a file cut short).
 * @param {string} text
 * @returns {string | null} */
export function prettyJSON(text) {
  try {
    JSON.parse(text);
  } catch {
    return null;
  }
  let out = "";
  let depth = 0;
  let inString = false;
  let escaped = false;
  const newline = () => {
    out += `\n${"  ".repeat(depth)}`;
  };
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      out += char;
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }
    if (char === '"') {
      inString = true;
      out += char;
    } else if (char === "{" || char === "[") {
      let next = index + 1;
      while (next < text.length && /\s/.test(text[next])) {
        next += 1;
      }
      if (text[next] === "}" || text[next] === "]") {
        out += char + text[next];
        index = next;
      } else {
        out += char;
        depth += 1;
        newline();
      }
    } else if (char === "}" || char === "]") {
      depth -= 1;
      newline();
      out += char;
    } else if (char === ",") {
      out += char;
      newline();
    } else if (char === ":") {
      out += ": ";
    } else if (!/\s/.test(char)) {
      out += char;
    }
  }
  return out;
}

// Colouring.

/** @param {string} text */
const words = (text) => new Set(text.split(" "));

const JS_KEYWORDS = words(
  "async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while yield",
);

/** @typedef {{ lineComments: string[], blockComment: [string, string] | null, quotes: string[], longStrings: string[], keywords: Set<string> }} Grammar */

/** @type {Record<string, Grammar>} */
const GRAMMARS = {
  python: {
    lineComments: ["#"],
    blockComment: null,
    quotes: ['"', "'"],
    longStrings: ['"""', "'''"],
    keywords: words(
      "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda match case nonlocal not or pass raise return self try while with yield",
    ),
  },
  javascript: { lineComments: ["//"], blockComment: ["/*", "*/"], quotes: ['"', "'"], longStrings: ["`"], keywords: JS_KEYWORDS },
  typescript: {
    lineComments: ["//"],
    blockComment: ["/*", "*/"],
    quotes: ['"', "'"],
    longStrings: ["`"],
    keywords: new Set([
      ...JS_KEYWORDS,
      ...words("abstract any as boolean declare enum implements interface keyof never number private protected public readonly string type unknown"),
    ]),
  },
  swift: {
    lineComments: ["//"],
    blockComment: ["/*", "*/"],
    quotes: ['"'],
    longStrings: ['"""'],
    keywords: words(
      "actor any as associatedtype async await break case catch class continue default defer do else enum extension fallthrough false fileprivate final for func guard if import in init inout internal is let mutating nil nonisolated open override private protocol public repeat rethrows return self Self some static struct subscript super switch throw throws true try typealias var weak where while",
    ),
  },
  json: { lineComments: [], blockComment: null, quotes: ['"'], longStrings: [], keywords: words("true false null") },
  toml: { lineComments: ["#"], blockComment: null, quotes: ['"', "'"], longStrings: ['"""', "'''"], keywords: words("true false") },
  yaml: { lineComments: ["#"], blockComment: null, quotes: ['"', "'"], longStrings: [], keywords: words("true false null yes no on off") },
  shell: {
    lineComments: ["#"],
    blockComment: null,
    quotes: ['"', "'"],
    longStrings: [],
    keywords: words("if then else elif fi for while until do done case esac function in return export local readonly set unset source exit trap shift"),
  },
  css: { lineComments: [], blockComment: ["/*", "*/"], quotes: ['"', "'"], longStrings: [], keywords: words("important") },
  rust: {
    lineComments: ["//"],
    blockComment: ["/*", "*/"],
    quotes: ['"'],
    longStrings: [],
    keywords: words(
      "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
    ),
  },
  go: {
    lineComments: ["//"],
    blockComment: ["/*", "*/"],
    quotes: ['"', "'"],
    longStrings: ["`"],
    keywords: words(
      "break case chan const continue default defer else fallthrough for func go goto if import interface map nil package range return select struct switch type var true false",
    ),
  },
};

/** What a line leaves open for the next: nothing, a comment or string closed by `close`, a Markdown fence, an HTML tag.
 * @typedef {{ kind: "none" } | { kind: "comment", close: string } | { kind: "string", close: string } | { kind: "fence" } | { kind: "tag" }} Carry */

/** @type {Carry} */
const NONE = { kind: "none" };

/** Tokens with neighbours of one kind joined. */
class Pieces {
  constructor() {
    /** @type {CodeToken[]} */
    this.tokens = [];
  }

  /** @param {CodeToken["kind"]} kind @param {string} text */
  add(kind, text) {
    if (text === "") {
      return;
    }
    const last = this.tokens.at(-1);
    if (last !== undefined && last.kind === kind) {
      last.text += text;
    } else {
      this.tokens.push({ kind, text });
    }
  }
}

/** Where `close` ends from `index` on (the index after it), -1 when it does not in this line; `escapes` skips the
 * character after a backslash.
 * @param {string} close @param {string} line @param {number} index @param {boolean} escapes */
function endOf(close, line, index, escapes) {
  for (let at = index; at < line.length; at += 1) {
    if (escapes && line[at] === "\\") {
      at += 1;
      continue;
    }
    if (line.startsWith(close, at)) {
      return at + close.length;
    }
  }
  return -1;
}

/** @param {string} char */
const isWordStart = (char) => /[\p{L}_$]/u.test(char);
/** @param {string | undefined} char */
const isWordPart = (char) => char !== undefined && /[\p{L}\p{N}_$]/u.test(char);

/** Where the YAML or TOML key that opens `line` stands (after its indent, and a YAML list's `- `); null for none.
 * @param {string} line @param {string} language
 * @returns {[number, number] | null} */
function keyPrefix(line, language) {
  let start = 0;
  while (line[start] === " " || line[start] === "\t") {
    start += 1;
  }
  if (language === "yaml" && line.startsWith("- ", start)) {
    start += 2;
  }
  if (start >= line.length || line[start] === "#") {
    return null;
  }
  let at = start;
  if (language === "yaml") {
    while (at < line.length && !(line[at] === ":" && (at + 1 === line.length || line[at + 1] === " "))) {
      at += 1;
    }
  } else {
    while (at < line.length && line[at] !== "=") {
      at += 1;
    }
    if (![...line.slice(start, at)].every((char) => isWordPart(char) || "-. \"'".includes(char))) {
      return null;
    }
  }
  if (at >= line.length || at <= start) {
    return null;
  }
  let keyEnd = at;
  while (keyEnd > start && line[keyEnd - 1] === " ") {
    keyEnd -= 1;
  }
  return keyEnd > start ? [start, keyEnd] : null;
}

/** @param {string} line @param {Grammar} rules @param {string} language @param {Carry} carry @param {Pieces} pieces
 * @returns {Carry} */
function codeLine(line, rules, language, carry, pieces) {
  let at = 0;
  if (carry.kind === "comment" || carry.kind === "string") {
    const stop = endOf(carry.close, line, 0, carry.kind === "string");
    const kind = carry.kind;
    if (stop < 0) {
      pieces.add(kind, line);
      return carry;
    }
    pieces.add(kind, line.slice(0, stop));
    at = Math.min(stop, line.length);
  } else {
    const first = line.search(/\S/);
    if (language === "toml" && first >= 0 && line[first] === "[") {
      pieces.add("plain", line.slice(0, first));
      pieces.add("keyword", line.slice(first));
      return NONE;
    }
    const key = language === "yaml" || language === "toml" ? keyPrefix(line, language) : null;
    if (key !== null) {
      pieces.add("plain", line.slice(0, key[0]));
      pieces.add("key", line.slice(key[0], key[1]));
      at = key[1];
    }
  }
  while (at < line.length) {
    const char = line[at];
    // A `#` opens a comment after a space or at the line's start (`$#`, a URL's anchor are not), always in Python.
    if (rules.lineComments.some((open) => line.startsWith(open, at)) && (char !== "#" || language === "python" || at === 0 || /\s/.test(line[at - 1]))) {
      pieces.add("comment", line.slice(at));
      return NONE;
    }
    const block = rules.blockComment;
    if (block !== null && line.startsWith(block[0], at)) {
      const stop = endOf(block[1], line, at + block[0].length, false);
      if (stop < 0) {
        pieces.add("comment", line.slice(at));
        return { kind: "comment", close: block[1] };
      }
      pieces.add("comment", line.slice(at, stop));
      at = stop;
      continue;
    }
    const long = rules.longStrings.find((open) => line.startsWith(open, at));
    if (long !== undefined) {
      const stop = endOf(long, line, at + long.length, true);
      if (stop < 0) {
        pieces.add("string", line.slice(at));
        return { kind: "string", close: long };
      }
      pieces.add("string", line.slice(at, stop));
      at = Math.min(stop, line.length);
      continue;
    }
    if (rules.quotes.includes(char)) {
      const found = endOf(char, line, at + 1, true);
      const stop = found < 0 ? line.length : Math.min(found, line.length);
      let after = stop;
      while (line[after] === " ") {
        after += 1;
      }
      pieces.add(language === "json" && line[after] === ":" ? "key" : "string", line.slice(at, stop));
      at = stop;
      continue;
    }
    if (/\p{N}/u.test(char) && !isWordPart(line[at - 1])) {
      let stop = at + 1;
      while (stop < line.length && /[0-9a-fA-F._xX]/.test(line[stop])) {
        stop += 1;
      }
      pieces.add("number", line.slice(at, stop));
      at = stop;
      continue;
    }
    if (language === "css" && (char === "@" || char === "#" || char === "!") && isWordPart(line[at + 1])) {
      let stop = at + 1;
      while (stop < line.length && (isWordPart(line[stop]) || line[stop] === "-")) {
        stop += 1;
      }
      pieces.add(char === "#" ? "number" : "keyword", line.slice(at, stop));
      at = stop;
      continue;
    }
    if (isWordStart(char)) {
      let stop = at + 1;
      while (stop < line.length && isWordPart(line[stop])) {
        stop += 1;
      }
      const word = line.slice(at, stop);
      pieces.add(rules.keywords.has(word) ? "keyword" : "plain", word);
      at = stop;
      continue;
    }
    pieces.add("plain", char);
    at += 1;
  }
  return NONE;
}

/** @param {string} line @param {Carry} carry @param {Pieces} pieces @returns {Carry} */
function markdownLine(line, carry, pieces) {
  const trimmed = line.replace(/^ +/, "");
  if (trimmed.startsWith("```") || trimmed.startsWith("~~~")) {
    pieces.add("comment", line);
    return carry.kind === "fence" ? NONE : { kind: "fence" };
  }
  if (carry.kind === "fence") {
    pieces.add("string", line);
    return carry;
  }
  if (trimmed.startsWith("#")) {
    pieces.add("heading", line);
    return NONE;
  }
  if (trimmed.startsWith(">")) {
    pieces.add("comment", line);
    return NONE;
  }
  let at = line.length - trimmed.length;
  pieces.add("plain", line.slice(0, at));
  const mark = /^([-*+] |\d+[.)])/.exec(trimmed);
  if (mark !== null) {
    const width = mark[1].endsWith(" ") ? 1 : mark[1].length;
    pieces.add("key", line.slice(at, at + width));
    at += width;
  }
  while (at < line.length) {
    const stop = line[at] === "`" ? endOf("`", line, at + 1, false) : -1;
    if (stop > 0) {
      pieces.add("string", line.slice(at, stop));
      at = stop;
      continue;
    }
    pieces.add("plain", line[at]);
    at += 1;
  }
  return NONE;
}

/** @param {string} line @param {Carry} start @param {Pieces} pieces @returns {Carry} */
function htmlLine(line, start, pieces) {
  let at = 0;
  let carry = start;
  while (at < line.length) {
    if (carry.kind === "comment") {
      const stop = endOf("-->", line, at, false);
      if (stop < 0) {
        pieces.add("comment", line.slice(at));
        return carry;
      }
      pieces.add("comment", line.slice(at, stop));
      at = stop;
      carry = NONE;
      continue;
    }
    if (carry.kind === "tag") {
      const char = line[at];
      if (char === ">" || line.startsWith("/>", at)) {
        const width = char === ">" ? 1 : 2;
        pieces.add("plain", line.slice(at, at + width));
        at += width;
        carry = NONE;
      } else if (char === '"' || char === "'") {
        const found = endOf(char, line, at + 1, false);
        const stop = found < 0 ? line.length : found;
        pieces.add("string", line.slice(at, stop));
        at = stop;
      } else {
        pieces.add("plain", char);
        at += 1;
      }
      continue;
    }
    if (line.startsWith("<!--", at)) {
      carry = { kind: "comment", close: "-->" };
      pieces.add("comment", "<!--");
      at += 4;
      continue;
    }
    if (line[at] === "<" && /[\p{L}/!?]/u.test(line[at + 1] ?? "")) {
      let stop = at + 1;
      if ("/!?".includes(line[stop])) {
        stop += 1;
      }
      pieces.add("plain", line.slice(at, stop));
      let nameEnd = stop;
      while (nameEnd < line.length && (isWordPart(line[nameEnd]) || line[nameEnd] === "-" || line[nameEnd] === ":")) {
        nameEnd += 1;
      }
      pieces.add("key", line.slice(stop, nameEnd));
      at = nameEnd;
      carry = { kind: "tag" };
      continue;
    }
    pieces.add("plain", line[at]);
    at += 1;
  }
  return carry;
}

/** The pieces of each of `lines`, coloured as `language` reads them; a comment or a long string left open runs on to the
 * next line.
 * @param {string[]} lines
 * @param {string} language
 * @returns {CodeToken[][]} */
export function highlightLines(lines, language) {
  const rules = GRAMMARS[language] ?? { lineComments: [], blockComment: null, quotes: [], longStrings: [], keywords: new Set() };
  /** @type {Carry} */
  let carry = NONE;
  return lines.map((line) => {
    const pieces = new Pieces();
    if (language === "markdown") {
      carry = markdownLine(line, carry, pieces);
    } else if (language === "html") {
      carry = htmlLine(line, carry, pieces);
    } else {
      carry = codeLine(line, rules, language, carry, pieces);
    }
    return pieces.tokens;
  });
}
