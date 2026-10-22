// What a copy out of the conversation puts on the clipboard (item 206): a selection that starts in a
// message or an answer goes on it as the markdown it was written in, for a paste into the composer or any other text
// field, and as a clean copy of its nodes in HTML, for a paste into a document. Both are read from copies of the
// selected nodes; nothing here builds markup from a string.

import { el } from "./dom.js";

// What is not part of what was said: controls, the faces of the actors, the feet under messages and answers (their time
// and buttons) and what only a screen reader hears. A file's path in code is a button too, but it is text (prose.js).
const LEFT_OUT = [
  "button:not(.code-path)",
  "input",
  "select",
  "textarea",
  "script",
  "style",
  "template",
  "svg",
  "canvas",
  "img",
  "[hidden]",
  "[aria-hidden='true']",
  ".visually-hidden",
  ".turn-face",
  ".message-foot",
  ".answer-foot",
  // The head of a block of code or of a table (its label and its buttons; item 537).
  ".code-head",
].join(", ");
// The elements that stand on lines of their own; any other is part of a line.
const BLOCKS = new Set([
  "ADDRESS",
  "ARTICLE",
  "ASIDE",
  "BLOCKQUOTE",
  "DD",
  "DETAILS",
  "DIV",
  "DL",
  "DT",
  "FIELDSET",
  "FIGCAPTION",
  "FIGURE",
  "FOOTER",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "HEADER",
  "HR",
  "LI",
  "MAIN",
  "NAV",
  "OL",
  "P",
  "PRE",
  "SECTION",
  "SUMMARY",
  "TABLE",
  "TBODY",
  "TD",
  "TFOOT",
  "TH",
  "THEAD",
  "TR",
  "UL",
]);
const WEB_ADDRESS = /^https?:\/\//i;
// The elements that mark up words within a line of a message (prose.js).
const MARKUP = new Set(["A", "B", "BR", "BUTTON", "CODE", "DEL", "EM", "I", "S", "STRONG"]);
// The items of one list or the cells of one table in a message, as a selection across several of them cuts them out.
const CUT_FROM = ".prose li, .prose td, .prose th, .prose ul, .prose ol, .prose table";

// The element `node` is, or the one it lies in; null for the document itself.
function elementOf(node) {
  return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
}

function longestRun(text, mark) {
  let longest = 0;
  for (const run of text.matchAll(mark)) {
    longest = Math.max(longest, run[0].length);
  }
  return longest;
}

// `**` or `*` around `text`, its outer spaces outside the marks, where markdown reads them.
function wrapped(mark, text) {
  const [, before, core, after] = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
  return core === "" ? text : `${before}${mark}${core}${mark}${after}`;
}

// Inline code between more backticks than any run of them in it; a space pads code that starts or ends with one.
function codeSpan(code) {
  const fence = "`".repeat(longestRun(code, /`+/g) + 1);
  const padded = code.startsWith("`") || code.endsWith("`") ? ` ${code} ` : code;
  return `${fence}${padded}${fence}`;
}

// A link to the web in markdown; an address that only repeats its words stays bare, and any other kind leaves only its
// words (prose.js links nothing else).
function link(anchor) {
  const words = inline(anchor.childNodes);
  const href = anchor.getAttribute("href") ?? "";
  if (!WEB_ADDRESS.test(href)) {
    return words;
  }
  return words.trim() === "" || words === href ? href : `[${words}](${href})`;
}

function inlineOf(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.data;
  }
  if (node.nodeType !== Node.ELEMENT_NODE || node.matches(LEFT_OUT)) {
    return "";
  }
  switch (node.tagName) {
    case "BR":
      return "\n";
    case "STRONG":
    case "B":
      return wrapped("**", inline(node.childNodes));
    case "EM":
    case "I":
      return wrapped("*", inline(node.childNodes));
    case "DEL":
    case "S":
      return wrapped("~~", inline(node.childNodes));
    case "CODE":
      return codeSpan(node.textContent);
    case "A":
      return link(node);
    default:
      return inline(node.childNodes);
  }
}

// Two elements side by side are words apart, as the page shows them (the spans of a step's line), unless both mark up
// words within a line.
function apart(before, after) {
  const elements = before.nodeType === Node.ELEMENT_NODE && after.nodeType === Node.ELEMENT_NODE;
  return elements && !(MARKUP.has(before.tagName) && MARKUP.has(after.tagName));
}

/** The markdown of the inline nodes `nodes`. */
function inline(nodes) {
  let text = "";
  let previous = null;
  for (const node of nodes) {
    const piece = inlineOf(node);
    if (piece === "") {
      continue;
    }
    if (previous !== null && apart(previous, node) && !/\s$/.test(text) && !/^\s/.test(piece)) {
      text += " ";
    }
    text += piece;
    previous = node;
  }
  return text;
}

// One line of a table: its cells' text on one line, their pipes escaped.
function cellText(cell) {
  return inline(cell.childNodes).replace(/\s*\n\s*/g, " ").replaceAll("|", "\\|").trim();
}

// What a table's rule line says of a column that is aligned (the class prose.js gives its cells).
const RULES = { "align-left": ":--", "align-center": ":-:", "align-right": "--:" };

/** A table as markdown: its rows in order, a row of dashes under the first (with the colons of the columns that are
 * aligned), and empty cells where a row is short. */
export function table(element) {
  const cells = (row) => [...row.children].filter((cell) => cell.tagName === "TD" || cell.tagName === "TH");
  const lines = element.tagName === "TR" ? [element] : [...element.querySelectorAll("tr")];
  const rows = lines.map((row) => cells(row).map(cellText));
  const width = Math.max(0, ...rows.map((one) => one.length));
  if (width === 0) {
    return "";
  }
  const line = (one) => `| ${[...one, ...Array(width - one.length).fill("")].join(" | ")} |`;
  const rule = Array.from({ length: width }, (_, column) => {
    const aligned = cells(lines[0])[column]?.className.split(" ").find((name) => name in RULES);
    return aligned === undefined ? "---" : RULES[aligned];
  });
  return [line(rows[0]), line(rule), ...rows.slice(1).map(line)].join("\n");
}

// What stands under the first words of an item and is no paragraph of its own: a list inside it, a block of code.
const UNDER_WORDS = /^(\s*([-*+]|\d+[.)])\s|`{3,}|~{3,})/;

// The items of a list, each under its own mark (a numbered one from the number the list starts at), a task with its box,
// the lines after an item's first set in under it: a list or code inside it on the next line, a paragraph after a
// blank one.
function list(element) {
  const ordered = element.tagName === "OL";
  const start = ordered ? Number.parseInt(element.getAttribute("start") ?? "1", 10) || 1 : 1;
  const items = [...element.children].filter((item) => item.tagName === "LI" && !item.matches(LEFT_OUT));
  return items
    .map((item, index) => {
      const mark = `${ordered ? `${start + index}.` : "-"} `;
      const box = item.querySelector(":scope > input[type='checkbox']");
      const task = box === null ? "" : `${box.checked ? "[x]" : "[ ]"} `;
      const parts = blocks(item.childNodes);
      const loose = parts.slice(1).some((part) => !UNDER_WORDS.test(part));
      return `${mark}${task}${parts.join(loose ? "\n\n" : "\n").replaceAll("\n", `\n${" ".repeat(mark.length)}`)}`;
    })
    .join("\n");
}

// Code as a fenced block, its fence longer than any run of backticks in the code, and the language it was written for.
function fenced(code, language = "") {
  const body = code.replace(/\n$/, "");
  const fence = "`".repeat(Math.max(2, longestRun(body, /`+/g)) + 1);
  return `${fence}${language}\n${body}\n${fence}`;
}

function quoted(text) {
  return text
    .split("\n")
    .map((line) => (line === "" ? ">" : `> ${line}`))
    .join("\n");
}

function block(element) {
  switch (element.tagName) {
    case "P":
    case "TD":
    case "TH":
      return inline(element.childNodes).trim();
    case "H1":
    case "H2":
    case "H3":
    case "H4":
    case "H5":
    case "H6": {
      // Dot draws a heading one level down and keeps the level it was written at (prose.js).
      const level = Number(element.dataset.level ?? element.tagName.slice(1));
      return `${"#".repeat(level)} ${inline(element.childNodes).replace(/\s*\n\s*/g, " ").trim()}`;
    }
    case "UL":
    case "OL":
      return list(element);
    case "BLOCKQUOTE":
      return quoted(blocks(element.childNodes).join("\n\n"));
    case "PRE":
      return fenced(element.textContent, element.closest(".code-block")?.dataset.language ?? "");
    case "TABLE":
    case "THEAD":
    case "TBODY":
    case "TFOOT":
    case "TR":
      return table(element);
    case "HR":
      return "---";
    default:
      return blocks(element.childNodes).join("\n\n");
  }
}

/** The markdown blocks of `nodes`: each block element, and each run of inline nodes between them as a paragraph. */
function blocks(nodes) {
  const found = [];
  let run = [];
  const endRun = () => {
    found.push(inline(run).trim());
    run = [];
  };
  for (const node of nodes) {
    if (node.nodeType === Node.ELEMENT_NODE && BLOCKS.has(node.tagName)) {
      endRun();
      if (!node.matches(LEFT_OUT)) {
        found.push(block(node));
      }
    } else {
      run.push(node);
    }
  }
  endRun();
  return found.filter((text) => text.trim() !== "");
}

// The nodes of `range`, copied. A selection across the items of one list or the cells of one table in a message keeps
// them in it: the copies go back into bare copies of the elements they were cut from, up to that list or table.
function copiedNodes(range) {
  const fragment = range.cloneContents();
  const start = elementOf(range.commonAncestorContainer);
  const outer = start.closest(CUT_FROM);
  if (outer === null || outer.matches("li, td, th")) {
    return fragment;
  }
  let copied = fragment;
  for (let node = start; ; node = node.parentElement) {
    const shell = node.cloneNode(false);
    shell.append(copied);
    copied = shell;
    if (node === outer) {
      break;
    }
  }
  const holder = document.createDocumentFragment();
  holder.append(copied);
  return holder;
}

// Markdown as it reads best: outside code, no spaces at the ends of lines and no more than one empty line in a row.
function tidy(markdown) {
  const kept = [];
  let fence = null;
  let empty = 0;
  for (const raw of markdown.split("\n")) {
    const mark = /^(`{3,})/.exec(raw);
    const inCode = fence !== null && !(mark !== null && mark[1].length >= fence.length);
    const line = inCode ? raw : raw.replace(/[ \t]+$/, "");
    if (mark !== null && !inCode) {
      fence = fence === null ? mark[1] : null;
    }
    empty = line === "" && !inCode ? empty + 1 : 0;
    if (empty < 2) {
      kept.push(line);
    }
  }
  return kept.join("\n").trim();
}

// The copied nodes as clean HTML: what is left out goes, a file's path is its code, and of the attributes only a web
// link's address stays.
function cleanHtml(nodes) {
  const holder = el("div");
  holder.append(nodes.cloneNode(true));
  for (const node of holder.querySelectorAll(LEFT_OUT)) {
    node.remove();
  }
  for (const button of holder.querySelectorAll("button.code-path")) {
    button.replaceWith(...button.childNodes);
  }
  for (const node of holder.querySelectorAll("*")) {
    for (const name of node.getAttributeNames()) {
      if (!(name === "href" && node.tagName === "A" && WEB_ADDRESS.test(node.getAttribute(name)))) {
        node.removeAttribute(name);
      }
    }
  }
  return new XMLSerializer().serializeToString(holder);
}

/**
 * What a copy of `selection` puts on the clipboard: `{ text, html }`, the markdown and the clean HTML of what it holds;
 * null when it lies in one block of code, whose text the browser copies as it is, or holds no words.
 */
function selectionCopy(selection) {
  const texts = [];
  const htmls = [];
  for (let index = 0; index < selection.rangeCount; index += 1) {
    const range = selection.getRangeAt(index);
    if (elementOf(range.commonAncestorContainer).closest("pre") !== null) {
      return null;
    }
    const nodes = copiedNodes(range);
    texts.push(blocks(nodes.childNodes).join("\n\n"));
    htmls.push(cleanHtml(nodes));
  }
  const text = tidy(texts.join("\n\n"));
  return text === "" ? null : { text, html: htmls.join("") };
}

/**
 * Copies out of the conversation in markdown: a selection that starts in a message or an answer (`.prose`), anywhere
 * under `target`, goes on the clipboard as `selectionCopy` reads it. Any other copy is the browser's own.
 */
export function wireConversationCopy(target) {
  target.addEventListener("copy", (event) => {
    const selection = document.getSelection();
    if (event.clipboardData === null || selection === null || selection.rangeCount === 0 || selection.isCollapsed) {
      return;
    }
    // A selection can start at the document itself, which is in no message.
    const start = elementOf(selection.getRangeAt(0).startContainer);
    if (start === null || start.closest(".prose") === null) {
      return;
    }
    const copy = selectionCopy(selection);
    if (copy === null) {
      return;
    }
    event.preventDefault();
    event.clipboardData.setData("text/plain", copy.text);
    event.clipboardData.setData("text/html", copy.html);
  });
}
