// A small Markdown reader for what the agent writes: code, lists, headings, quotes, tables, bold, italics and links.
// It only produces plain data (no HTML); prose.js turns that into DOM nodes.

const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const BARE_URL = /https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"\]]/g;
const SAFE_URL = /^https?:\/\/[^\s]+$/;

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
    parts.push({ type: "link", value: match[0], href: match[0] });
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ type: "text", value: text.slice(last) });
  }
  return parts;
}

const EMPHASIS = /\*\*(?=\S)([^*]+?)(?<=\S)\*\*|(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])/g;
const LINK = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;

function emphasisParts(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(EMPHASIS)) {
    if (match.index > last) {
      parts.push(...linkParts(text.slice(last, match.index)));
    }
    parts.push(
      match[1] === undefined
        ? { type: "italic", value: match[2] }
        : { type: "bold", value: match[1] },
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push(...linkParts(text.slice(last)));
  }
  return parts;
}

function markdownLinkParts(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(LINK)) {
    if (match.index > last) {
      parts.push(...emphasisParts(text.slice(last, match.index)));
    }
    if (SAFE_URL.test(match[2])) {
      parts.push({ type: "link", value: match[1], href: match[2] });
    } else {
      parts.push({ type: "text", value: match[0] });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push(...emphasisParts(text.slice(last)));
  }
  return parts;
}

/** The inline pieces of one line of text: `{type: "text" | "code" | "bold" | "italic" | "link", value, href?}`. */
export function parseInline(text) {
  const parts = [];
  const segments = text.split(/(`+)/);
  // An odd split index holds the backtick run that opens or closes a code span.
  for (let index = 0; index < segments.length; index += 1) {
    if (index % 2 === 0) {
      if (segments[index] !== "") {
        parts.push(...markdownLinkParts(segments[index]));
      }
      continue;
    }
    const fence = segments[index];
    const closing = segments.indexOf(fence, index + 1);
    if (closing === -1 || closing % 2 === 0) {
      parts.push({ type: "text", value: fence });
      continue;
    }
    parts.push({ type: "code", value: segments.slice(index + 1, closing).join("").trim() });
    index = closing;
  }
  return parts;
}

function startsBlock(line, next) {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    BULLET.test(line) ||
    NUMBERED.test(line) ||
    QUOTE.test(line) ||
    RULE.test(line) ||
    (line.includes("|") && next !== undefined && TABLE_SEPARATOR.test(next) && next.includes("-"))
  );
}

function readFence(lines, start) {
  const open = FENCE.exec(lines[start]);
  const marker = open[1];
  const body = [];
  let index = start + 1;
  while (index < lines.length && !(lines[index].trim().startsWith(marker) && lines[index].trim().replace(/[`~]/g, "") === "")) {
    body.push(lines[index]);
    index += 1;
  }
  return { block: { type: "code", language: open[2], text: body.join("\n") }, next: index + 1 };
}

function readList(lines, start, pattern, ordered) {
  const items = [];
  let index = start;
  while (index < lines.length && pattern.test(lines[index])) {
    items.push(parseInline(pattern.exec(lines[index])[1]));
    index += 1;
  }
  return { block: { type: "list", ordered, items }, next: index };
}

function readTable(lines, start) {
  const header = splitCells(lines[start]).map(parseInline);
  const rows = [];
  let index = start + 2;
  while (index < lines.length && lines[index].includes("|") && lines[index].trim() !== "") {
    rows.push(splitCells(lines[index]).map(parseInline));
    index += 1;
  }
  return { block: { type: "table", header, rows }, next: index };
}

function readQuote(lines, start) {
  const body = [];
  let index = start;
  while (index < lines.length && QUOTE.test(lines[index])) {
    body.push(QUOTE.exec(lines[index])[1]);
    index += 1;
  }
  return { block: { type: "quote", inline: parseInline(body.join("\n")) }, next: index };
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
    if (FENCE.test(line)) {
      read = readFence(lines, index);
    } else if (HEADING.test(line)) {
      const match = HEADING.exec(line);
      read = { block: { type: "heading", level: match[1].length, inline: parseInline(match[2]) }, next: index + 1 };
    } else if (RULE.test(line)) {
      read = { block: { type: "rule" }, next: index + 1 };
    } else if (BULLET.test(line)) {
      read = readList(lines, index, BULLET, false);
    } else if (NUMBERED.test(line)) {
      read = readList(lines, index, NUMBERED, true);
    } else if (QUOTE.test(line)) {
      read = readQuote(lines, index);
    } else if (line.includes("|") && lines[index + 1] !== undefined && TABLE_SEPARATOR.test(lines[index + 1]) && lines[index + 1].includes("-")) {
      read = readTable(lines, index);
    } else {
      read = readParagraph(lines, index);
    }
    blocks.push(read.block);
    index = read.next;
  }
  return blocks;
}
