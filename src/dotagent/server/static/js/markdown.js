// A small Markdown reader for what the agent writes: code, lists (nested, numbered from any number, with tasks),
// headings, quotes, tables (with their alignment), bold, italics, strikethrough and links. It only produces plain data
// (no HTML); prose.js turns that into DOM nodes, and mirror.js the composer's text as it reads (`sourceLines`).

const HEADING = /^(#{1,6})\s+(.*)$/;
// The first line of a list item: its indent, its mark (a bullet, or a number and its dot or bracket), the space after
// it and the rest of the line.
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$/;
const TASK = /^\[([ xX])\]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
// The line under a table's head: a dash in each cell at least, with a colon at an end of it to align the column. No two
// stretches of white space stand side by side with nothing between them that must match, so that a line of spaces
// that fails at its end is read once and not once from each of its spaces (item 692).
const TABLE_SEPARATOR = /^\s*(?:\|\s*)?:?-+:?(?:\s*\|\s*:?-+:?)*(?:\s*\|)?\s*$/;
// A bare address, or one between angle brackets (its brackets are no part of it).
const BARE_URL = /<(https?:\/\/[^\s<>]+)>|https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"\]]/g;
const SAFE_URL = /^https?:\/\/[^\s]+$/;
// How deep lists and quotes nest in what is read: a text of nothing but nested marks cannot take the page down.
const MOST_NESTING = 6;
const WHITE_SPACE = /\s/;

/**
 * What a line that opens a block of code says (``` or ~~~, then what the writer called the code): the width of its
 * indent, its mark, the language (the first word of what follows the mark read as letters, digits and `+#.-`:
 * `python title="x.py"`, `js {1,3}` and `python:x.py` all say theirs) and the title (`title="x.py"`, or what follows a
 * colon after the language), "" when it has none; null when the line opens no block: a line that holds backticks after
 * a backtick mark is code in a line.
 */
export function openFence(line) {
  const match = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(line);
  if (match === null || (match[2][0] === "`" && match[3].includes("`"))) {
    return null;
  }
  const info = match[3].trim();
  const language = /^[\w+#.-]*/.exec(info)[0];
  const named = /title\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))/.exec(info);
  const colon = info.startsWith(`${language}:`) ? /^[^\s{]*/.exec(info.slice(language.length + 1))[0] : "";
  return { indent: match[1].length, marker: match[2], language, title: named === null ? colon : (named[1] ?? named[2] ?? named[3]) };
}

/** Whether `line` closes the block of code that `marker` opened: marks of the same kind, at least as many, alone. */
function closesFence(line, marker) {
  const trimmed = line.trim();
  return trimmed.length >= marker.length && [...trimmed].every((char) => char === marker[0]);
}

function splitCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function linkParts(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(BARE_URL)) {
    if (match.index > last) {
      parts.push({ type: "text", value: text.slice(last, match.index) });
    }
    const address = match[1] ?? match[0];
    parts.push({ type: "link", value: address, href: address });
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ type: "text", value: text.slice(last) });
  }
  return parts;
}

// Bold (`**` or `__`), italics (`*` or `_`, an underscore only between words' edges, so `snake_case` stays whole) and
// strikethrough (`~~`), in this order of groups.
const EMPHASIS =
  /\*\*(?=\S)([^*]+?)(?<=\S)\*\*|(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])|~~(?=\S)([^~\n]+?)(?<=\S)~~|(?<![\w_])__(?=\S)([^_\n]+?)(?<=\S)__(?![\w_])|(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])/g;

const LINK_HEAD = /\]\(https?:\/\//y;
const ADDRESS_RUN = /[^\s)]*/y;

/**
 * The links `[words](https://address)` of `text`, in order, each `{ index, text, words, href }` (`index` and `text`
 * with the `!` of a picture written as a link when `withPicture`): what `/!?\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g`
 * finds, in one pass. A pattern that reads the words of each `[` to the next `]` and the address after it again from
 * every `[` of a line made of `[a` took a time that grew with the square of its length (item 692): here the closing
 * bracket and the end of the line are looked up once for all the `[` before them, and an address whose run of
 * characters ended at a space or at the end is known to fail for every `](` inside that run.
 */
function linkMatches(text, withPicture) {
  const found = [];
  let open = text.indexOf("[");
  let close = -1;
  let lineEnd = -1;
  let deadUntil = 0;
  let last = 0;
  while (open !== -1) {
    if (close <= open) {
      close = text.indexOf("]", open + 1);
      if (close === -1) {
        break;
      }
    }
    if (lineEnd <= open) {
      const at = text.indexOf("\n", open + 1);
      lineEnd = at === -1 ? text.length : at;
    }
    if (lineEnd < close) {
      // The line ends before this bracket closes: only a `[` after the line's end can be closed by it.
      open = text.indexOf("[", lineEnd + 1);
      continue;
    }
    if (close > open + 1 && close >= deadUntil) {
      LINK_HEAD.lastIndex = close;
      const head = LINK_HEAD.exec(text);
      if (head !== null) {
        ADDRESS_RUN.lastIndex = close + head[0].length;
        const end = close + head[0].length + ADDRESS_RUN.exec(text)[0].length;
        if (end > close + head[0].length && text[end] === ")") {
          const href = text.slice(close + 2, end);
          const start = withPicture && open > last && text[open - 1] === "!" ? open - 1 : open;
          found.push({ index: start, text: text.slice(start, end + 1), words: text.slice(open + 1, close), href });
          last = end + 1;
          open = text.indexOf("[", last);
          continue;
        }
        deadUntil = end;
      }
    }
    // Every `[` of the line before this `]` fails the same way: the next one that can start a link is after it.
    open = text.indexOf("[", close + 1);
  }
  return found;
}

function emphasisParts(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(EMPHASIS)) {
    if (match.index > last) {
      parts.push(...linkParts(text.slice(last, match.index)));
    }
    if (match[1] !== undefined || match[4] !== undefined) {
      parts.push({ type: "bold", value: match[1] ?? match[4] });
    } else if (match[3] !== undefined) {
      parts.push({ type: "strike", value: match[3] });
    } else {
      parts.push({ type: "italic", value: match[2] ?? match[5] });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push(...linkParts(text.slice(last)));
  }
  return parts;
}

// A link, or a picture written as one (`![words](address)`): the picture is not drawn (nothing is fetched for a
// message), its words are the link (`linkMatches`).

function markdownLinkParts(text) {
  const parts = [];
  let last = 0;
  for (const match of linkMatches(text, true)) {
    if (match.index > last) {
      parts.push(...emphasisParts(text.slice(last, match.index)));
    }
    if (SAFE_URL.test(match.href)) {
      parts.push({ type: "link", value: match.words, href: match.href });
    } else {
      parts.push({ type: "text", value: match.text });
    }
    last = match.index + match.text.length;
  }
  if (last < text.length) {
    parts.push(...emphasisParts(text.slice(last)));
  }
  return parts;
}

// A backslash before a mark makes the mark a letter (item 537): while a line is read its marks stand as private
// characters, so that no reader takes them for marks, and each is given back as itself (in code with its backslash).
// The characters are a block of the private use area that the line does not use itself (icon fonts keep their glyphs
// there, in the first plane and in the last: Nerd Fonts' Material Design icons are U+F0001 on), so that a glyph of the
// writer's is never taken for a hidden mark; a line that uses every block (none does) keeps its backslashes.
const ESCAPABLE = /\\([\\`*_{}[\]()#+\-.!|~<>])/g;
const FIRST_BLOCK = 0xf0000;
const BLOCK = 0x80;
const BLOCKS = 0x1ff;
const ENTITIES = { "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&#39;": "'", "&nbsp;": "\u00a0" };
const BREAK = /<br\s*\/?>/i;

/** The first character of a block of 128 private characters that `line` does not use, or null when it uses them all. */
function freeBlock(line) {
  const used = new Set();
  for (const char of line) {
    const point = char.codePointAt(0);
    if (point >= FIRST_BLOCK && point < FIRST_BLOCK + BLOCKS * BLOCK) {
      used.add(Math.floor((point - FIRST_BLOCK) / BLOCK));
    }
  }
  for (let block = 0; block < BLOCKS; block += 1) {
    if (!used.has(block)) {
      return FIRST_BLOCK + block * BLOCK;
    }
  }
  return null;
}

/** `line` with its escaped marks hidden in a block it does not use, and the function that gives them back (`show(text,
 * keepBackslash)`; in code with their backslash); a line that leaves no block is returned as it is. */
function escapes(line) {
  const base = freeBlock(line);
  if (base === null) {
    return { hidden: line, show: (text) => text };
  }
  const hidden = line.replace(ESCAPABLE, (_, mark) => String.fromCodePoint(base + mark.charCodeAt(0)));
  const show = (text, keepBackslash) => {
    let out = "";
    for (const char of text) {
      const point = char.codePointAt(0);
      out += point >= base && point < base + BLOCK ? `${keepBackslash ? "\\" : ""}${String.fromCodePoint(point - base)}` : char;
    }
    return out;
  };
  return { hidden, show };
}

// The words of a line as they read: the escapes given back and the few entities a writer uses spelled out; a `<br>` is a
// break of its own.
function wordsOf(part, show) {
  const value = show(part.value, false).replace(/&(?:lt|gt|amp|quot|#39|nbsp);/g, (entity) => ENTITIES[entity]);
  if (part.type !== "text" || !BREAK.test(value)) {
    return [{ ...part, value }];
  }
  return value.split(/<br\s*\/?>/i).flatMap((piece, index) => [...(index === 0 ? [] : [{ type: "break", value: "" }]), ...(piece === "" ? [] : [{ type: "text", value: piece }])]);
}

// A bold whose words hold code ("**to `a@b.c` now**", item 778): the code cuts the line before bold is looked for, so
// its two marks fell in different pieces and stood on the page. They are found on the line with its escapes hidden, by
// the rules of EMPHASIS's bold (no space inside a mark, no star between the marks, so "2 ** 3" and an escaped "\*\*" are
// no marks), around at least one code span, and stand for two characters out of the escapes' blocks, which the line
// does not hold; the pieces between them are marked `strong` and the characters taken off (`strongAcross`).
const BOLD_AROUND_CODE = /\*\*(?=[^\s*])((?:[^*`\n]|`[^`\n]+`)*?`[^`\n]+`(?:[^*`\n]|`[^`\n]+`)*?)(?<=[^\s*])\*\*/g;
const STRONG_OPEN = "\u{100000}";
const STRONG_CLOSE = "\u{100001}";

/** The pieces of a line between the characters that stand for a bold's marks (BOLD_AROUND_CODE), marked `strong`. */
function strongAcross(parts) {
  const out = [];
  let strong = false;
  for (const part of parts) {
    if (part.type !== "text" || !(part.value.includes(STRONG_OPEN) || part.value.includes(STRONG_CLOSE))) {
      out.push(strong ? { ...part, strong: true } : part);
      continue;
    }
    let piece = "";
    for (const char of part.value) {
      if (char !== STRONG_OPEN && char !== STRONG_CLOSE) {
        piece += char;
        continue;
      }
      if (piece !== "") {
        out.push(strong ? { ...part, value: piece, strong: true } : { ...part, value: piece });
      }
      piece = "";
      strong = char === STRONG_OPEN;
    }
    if (piece !== "") {
      out.push(strong ? { ...part, value: piece, strong: true } : { ...part, value: piece });
    }
  }
  return out;
}

/** What `text` says with its markdown taken off, for a line that shows a part of it as words (a preview, item 778): the
 * words of links and code, bold and italic without their marks, and no heading, quote, list or fence marks. */
export function plainText(text) {
  return text
    .replace(/^\s*(`{3,}|~{3,}).*$/gm, "")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|~~)(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replace(/(?<![\w_])__(?=\S)([\s\S]*?\S)__(?![\w_])/g, "$1")
    .replace(/(^|[^\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])/g, "$1$2")
    .replace(/(`+)([^`]*?)\1/g, "$2")
    .replace(/^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** The inline pieces of one line of text: `{type: "text" | "code" | "bold" | "italic" | "strike" | "link" | "break", value, href?}`. */
export function parseInline(line) {
  const parts = [];
  const { hidden: escaped, show } = escapes(line);
  const hidden =
    escaped.includes(STRONG_OPEN) || escaped.includes(STRONG_CLOSE)
      ? escaped
      : escaped.replace(BOLD_AROUND_CODE, (_, words) => `${STRONG_OPEN}${words}${STRONG_CLOSE}`);
  const segments = hidden.split(/(`+)/);
  // An odd split index holds the backtick run that opens or closes a code span.
  for (let index = 0; index < segments.length; index += 1) {
    if (index % 2 === 0) {
      if (segments[index] !== "") {
        parts.push(...markdownLinkParts(segments[index]).flatMap((piece) => wordsOf(piece, show)));
      }
      continue;
    }
    const fence = segments[index];
    const closing = segments.indexOf(fence, index + 1);
    if (closing === -1 || closing % 2 === 0) {
      parts.push({ type: "text", value: show(fence, false) });
      continue;
    }
    parts.push({ type: "code", value: show(segments.slice(index + 1, closing).join(""), true).trim() });
    index = closing;
  }
  return strongAcross(parts).map((part) => (part.href === undefined ? part : { ...part, href: show(part.href, false) }));
}

// Whether the line under `line` makes it the head of a table.
function startsTable(line, next) {
  return line.includes("|") && next !== undefined && TABLE_SEPARATOR.test(next) && (next.includes("|") || /-{3,}/.test(next));
}

function startsBlock(line, next) {
  return (
    openFence(line) !== null ||
    HEADING.test(line) ||
    ITEM.test(line) ||
    QUOTE.test(line) ||
    RULE.test(line) ||
    startsTable(line, next)
  );
}

function readFence(lines, start) {
  const open = openFence(lines[start]);
  const body = [];
  let index = start + 1;
  while (index < lines.length && !closesFence(lines[index], open.marker)) {
    // The lines of code stand as far in from the fence as the fence stands in from the margin (a fence under a list item).
    body.push(lines[index].replace(new RegExp(`^ {0,${open.indent}}`), ""));
    index += 1;
  }
  return { block: { type: "code", language: open.language, title: open.title, text: body.join("\n") }, next: index + 1 };
}

function indentOf(line) {
  return /^ */.exec(line)[0].length;
}

// The lines of the list item that begins at `lines[start]` (marked as `match` reads it), as the text of the item with
// the lines under it set out to its margin: those indented under it, and blank lines between them. `next` is the line
// after them.
function readItemLines(lines, start, match, base) {
  const offset = match[1].length + match[2].length + match[3].length;
  const own = [match[4]];
  let index = start + 1;
  let end = index;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    if (indentOf(line) < base + 2) {
      break;
    }
    // Blank lines inside the item are part of it only when more of it follows them.
    while (end < index) {
      own.push("");
      end += 1;
    }
    own.push(line.slice(Math.min(indentOf(line), offset)));
    index += 1;
    end = index;
  }
  return { text: own.join("\n"), next: end };
}

function readList(lines, start, depth) {
  const first = ITEM.exec(lines[start]);
  const base = first[1].length;
  const ordered = /^\d/.test(first[2]);
  const items = [];
  let index = start;
  while (index < lines.length) {
    // A list goes on past blank lines when its next item follows them.
    let probe = index;
    while (probe < lines.length && lines[probe].trim() === "") {
      probe += 1;
    }
    const match = probe < lines.length ? ITEM.exec(lines[probe]) : null;
    if (match === null || match[1].length < base || match[1].length >= base + 2 || /^\d/.test(match[2]) !== ordered) {
      break;
    }
    const read = readItemLines(lines, probe, match, base);
    let own = read.text;
    let checked = null;
    const task = TASK.exec(own.split("\n")[0]);
    if (task !== null) {
      checked = task[1] !== " ";
      own = [task[2], ...own.split("\n").slice(1)].join("\n");
    }
    const blocks = parseSourcedBlocks(own, depth + 1).map((sourced) => sourced.block);
    const head = blocks[0]?.type === "paragraph" ? blocks[0].inline : [];
    items.push({ inline: head, blocks: head === blocks[0]?.inline ? blocks.slice(1) : blocks, checked });
    index = read.next;
  }
  return { block: { type: "list", ordered, start: ordered ? Number.parseInt(first[2], 10) : 1, items }, next: index };
}

// The alignment each cell of a table's rule line asks for: "left", "center", "right" or null.
function tableAlignments(line) {
  return splitCells(line).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    return left && right ? "center" : right ? "right" : left ? "left" : null;
  });
}

function readTable(lines, start) {
  const header = splitCells(lines[start]).map(parseInline);
  const aligns = tableAlignments(lines[start + 1]);
  const rows = [];
  let index = start + 2;
  while (index < lines.length && lines[index].includes("|") && lines[index].trim() !== "") {
    rows.push(splitCells(lines[index]).map(parseInline));
    index += 1;
  }
  return { block: { type: "table", header, aligns, rows }, next: index };
}

function readQuote(lines, start, depth) {
  const body = [];
  let index = start;
  while (index < lines.length && QUOTE.test(lines[index])) {
    body.push(QUOTE.exec(lines[index])[1]);
    index += 1;
  }
  return { block: { type: "quote", blocks: parseSourcedBlocks(body.join("\n"), depth + 1).map((sourced) => sourced.block) }, next: index };
}

function readParagraph(lines, start) {
  const body = [];
  let index = start;
  while (index < lines.length && lines[index].trim() !== "" && (index === start || !startsBlock(lines[index], lines[index + 1]))) {
    body.push(lines[index]);
    index += 1;
  }
  return { block: { type: "paragraph", inline: parseInline(body.join("\n")) }, next: index };
}

/** The blocks of a text: `code`, `heading`, `list`, `table`, `quote`, `rule` and `paragraph`. */
export function parseBlocks(text) {
  return parseSourcedBlocks(text).map((sourced) => sourced.block);
}

/** A heading's words without the closing marks (`## Title ##`): white space and hashes at its end, when white space stands before the hashes. */
function withoutClosingMarks(text) {
  let end = text.length;
  while (end > 0 && WHITE_SPACE.test(text[end - 1])) {
    end -= 1;
  }
  let marks = end;
  while (marks > 0 && text[marks - 1] === "#") {
    marks -= 1;
  }
  let start = marks;
  while (start > 0 && WHITE_SPACE.test(text[start - 1])) {
    start -= 1;
  }
  return marks === end || start === marks ? text : text.slice(0, start);
}

/**
 * The blocks of a text, each with the lines it was read from as its `source` (item 274). A block is read from its own
 * lines alone, so two texts whose first blocks were read from the same lines have the same first blocks. A `list` has
 * `ordered`, `start` (the number its first item bears) and `items`, each `{ inline, blocks, checked }`: the words of
 * the item, the blocks under them (a list inside it, code, more paragraphs) and whether a task is done (null: no task).
 * A `table` has `aligns`, one for each column; a `quote` has the `blocks` it holds.
 */
export function parseSourcedBlocks(text, depth = 0) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    let read;
    if (openFence(line) !== null) {
      read = readFence(lines, index);
    } else if (HEADING.test(line)) {
      const match = HEADING.exec(line);
      read = {
        block: { type: "heading", level: match[1].length, inline: parseInline(withoutClosingMarks(match[2])) },
        next: index + 1,
      };
    } else if (RULE.test(line)) {
      read = { block: { type: "rule" }, next: index + 1 };
    } else if (ITEM.test(line) && depth < MOST_NESTING) {
      read = readList(lines, index, depth);
    } else if (QUOTE.test(line) && depth < MOST_NESTING) {
      read = readQuote(lines, index, depth);
    } else if (startsTable(line, lines[index + 1])) {
      read = readTable(lines, index);
    } else {
      read = readParagraph(lines, index);
    }
    blocks.push({ block: read.block, source: lines.slice(index, read.next).join("\n") });
    index = read.next;
  }
  return blocks;
}

/**
 * Whether `text` reads as Markdown (item 269): one of its blocks is more than a paragraph, or a paragraph holds bold,
 * italics, code or a link with words of its own, as `parseBlocks` reads them. A bare address does not count.
 */
export function readsAsMarkdown(text) {
  return parseBlocks(text).some(
    (block) => block.type !== "paragraph" || block.inline.some((part) => part.type !== "text" && !(part.type === "link" && part.value === part.href)),
  );
}

function piece(kind, text) {
  return { kind, text };
}

// The pieces of `text` outside code and links: bare addresses as links, the rest as it is.
function sourceAddresses(text) {
  return linkParts(text).map((part) => piece(part.type === "link" ? "link" : "text", part.value));
}

// The pieces of `text` outside code and links, as `emphasisParts` reads it: bold and italics between their marks.
function sourceEmphasis(text) {
  const pieces = [];
  let last = 0;
  for (const match of text.matchAll(EMPHASIS)) {
    pieces.push(...sourceAddresses(text.slice(last, match.index)));
    if (match[1] !== undefined || match[4] !== undefined) {
      const mark = match[1] !== undefined ? "**" : "__";
      pieces.push(piece("mark", mark), piece("strong", match[1] ?? match[4]), piece("mark", mark));
    } else if (match[3] !== undefined) {
      pieces.push(piece("mark", "~~"), piece("text", match[3]), piece("mark", "~~"));
    } else {
      const mark = match[2] !== undefined ? "*" : "_";
      pieces.push(piece("mark", mark), piece("em", match[2] ?? match[5]), piece("mark", mark));
    }
    last = match.index + match[0].length;
  }
  pieces.push(...sourceAddresses(text.slice(last)));
  return pieces;
}

// The pieces of `text` outside code, as `markdownLinkParts` reads it: a link to the web as its words and its address
// between their brackets.
function sourceLinks(text) {
  const pieces = [];
  let last = 0;
  for (const match of linkMatches(text, false)) {
    pieces.push(...sourceEmphasis(text.slice(last, match.index)));
    if (SAFE_URL.test(match.href)) {
      pieces.push(piece("mark", "["), piece("link", match.words), piece("mark", "]("), piece("address", match.href), piece("mark", ")"));
    } else {
      pieces.push(piece("text", match.text));
    }
    last = match.index + match.text.length;
  }
  pieces.push(...sourceEmphasis(text.slice(last)));
  return pieces;
}

// The pieces of one line as `parseInline` reads it, with the marks it reads kept in place.
function sourceInline(text) {
  const pieces = [];
  const segments = text.split(/(`+)/);
  for (let index = 0; index < segments.length; index += 1) {
    if (index % 2 === 0) {
      pieces.push(...sourceLinks(segments[index]));
      continue;
    }
    const fence = segments[index];
    const closing = segments.indexOf(fence, index + 1);
    if (closing === -1 || closing % 2 === 0) {
      pieces.push(piece("text", fence));
      continue;
    }
    pieces.push(piece("mark", fence), piece("code", segments.slice(index + 1, closing).join("")), piece("mark", fence));
    index = closing;
  }
  return pieces.filter((part) => part.text !== "");
}

// A line of a table: its pipes as marks between its cells.
function sourceRow(line) {
  return line.split(/((?<!\\)\|)/).flatMap((part, index) => (index % 2 === 1 ? [piece("mark", part)] : sourceInline(part)));
}

// A line that begins with a mark (`pattern`'s groups: the indent, the mark, the space after it and the rest).
function sourceMarked(match) {
  return [piece("text", match[1]), piece("mark", match[2]), piece("text", match[3]), ...sourceInline(match[4])].filter((part) => part.text !== "");
}

const SOURCE_HEADING = /^()(#{1,6})(\s+)(.*)$/;
const SOURCE_BULLET = /^(\s*)([-*+])(\s+)(.*)$/;
const SOURCE_NUMBERED = /^(\s*)(\d+[.)])(\s+)(.*)$/;
const SOURCE_QUOTE = /^(\s*)(>)(\s?)(.*)$/;

/**
 * The text of the composer as it reads (item 207): one entry for each line, `{ kind, pieces }`, read as `parseBlocks`
 * and `parseInline` read it. A line's `kind` is "text", "heading", "list", "quote", "fence" (one that opens or closes
 * code), "block" (a line of that code), "table-head" (a table's first line), "table-rule" (the dashes under it),
 * "table" (a row after them) or "rule". Its `pieces` are `{ kind, text }` in order, whose texts together give the line
 * back exactly: "text", "mark" (what the reader takes as a mark: a heading's hashes, a list's mark, a quote's >, the
 * stars of bold and italics, the backticks of code, a link's brackets, a table's pipes), "strong", "em", "code", "link"
 * (a link's words or a bare address) and "address" (a link's address).
 */
export function sourceLines(text) {
  const lines = text.split("\n");
  const shown = [];
  let fence = null;
  let table = false;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const next = lines[index + 1];
    if (fence !== null) {
      const closes = closesFence(line, fence);
      shown.push(closes ? { kind: "fence", pieces: [piece("mark", line)] } : { kind: "block", pieces: [piece("text", line)] });
      fence = closes ? null : fence;
      continue;
    }
    table = table && line.includes("|") && line.trim() !== "";
    if (table) {
      shown.push({ kind: "table", pieces: sourceRow(line) });
    } else if (openFence(line) !== null) {
      fence = openFence(line).marker;
      shown.push({ kind: "fence", pieces: [piece("mark", line)] });
    } else if (SOURCE_HEADING.test(line)) {
      shown.push({ kind: "heading", pieces: sourceMarked(SOURCE_HEADING.exec(line)) });
    } else if (RULE.test(line)) {
      shown.push({ kind: "rule", pieces: [piece("mark", line)] });
    } else if (SOURCE_BULLET.test(line)) {
      shown.push({ kind: "list", pieces: sourceMarked(SOURCE_BULLET.exec(line)) });
    } else if (SOURCE_NUMBERED.test(line)) {
      shown.push({ kind: "list", pieces: sourceMarked(SOURCE_NUMBERED.exec(line)) });
    } else if (SOURCE_QUOTE.test(line)) {
      shown.push({ kind: "quote", pieces: sourceMarked(SOURCE_QUOTE.exec(line)) });
    } else if (startsTable(line, next)) {
      shown.push({ kind: "table-head", pieces: sourceRow(line) }, { kind: "table-rule", pieces: [piece("mark", next)] });
      index += 1;
      table = true;
    } else {
      shown.push({ kind: "text", pieces: sourceInline(line) });
    }
  }
  return shown;
}
