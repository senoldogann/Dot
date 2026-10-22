// Turns the text an agent or the owner wrote into DOM nodes. Everything goes in as text nodes; only http(s) links
// become anchors, and the path of a file in code a button that shows the file beside the conversation (item 195). A
// heading is drawn one level down and keeps the level it was written at in `data-level`, for a copy (item 206).

import { table as tableMarkdown } from "./clipboard.js";
import { codeBlock } from "./codeblock.js";
import { copyButton } from "./copybutton.js";
import { el, requestFile } from "./dom.js";
import { parseBlocks } from "./markdown.js";

// A file in the home folder as an agent names one it made or read: under ~ or /Users/<name>, to a name with a suffix,
// and no pattern for many files.
const FILE_PATH = /^(~|\/Users\/[^/\s]+)\/[^\n*?[\]{}$<>|]*[^/\s*?[\]{}$<>|]\.[A-Za-z0-9]{1,8}$/;
const FILE_PATH_CHARS = 400;

function codeNode(value) {
  if (value.length > FILE_PATH_CHARS || !FILE_PATH.test(value)) {
    return el("code", null, value);
  }
  return el(
    "button",
    { class: "code-path", type: "button", title: "Show the file", dataset: { path: value }, onclick: (event) => requestFile(event.currentTarget) },
    el("code", null, value),
  );
}

function inlineNode(part) {
  switch (part.type) {
    case "code":
      return codeNode(part.value);
    case "bold":
      return el("strong", null, part.value);
    case "italic":
      return el("em", null, part.value);
    case "strike":
      return el("del", null, part.value);
    case "break":
      return el("br");
    case "link":
      return el("a", { href: part.href, target: "_blank", rel: "noopener noreferrer nofollow" }, part.value);
    default:
      return part.value;
  }
}

function inlineNodes(parts) {
  return parts.map(inlineNode);
}

// A column that is aligned says so by a class, as the page's content security policy allows no style of its own.
function cellRow(tag, cells, aligns) {
  return el(
    "tr",
    null,
    cells.map((cell, column) => el(tag, { class: aligns[column] === null || aligns[column] === undefined ? null : `align-${aligns[column]}` }, inlineNodes(cell))),
  );
}

// One item of a list: its words, a box when it is a task, and the blocks under it (a list inside it, code, more words).
function itemNode(item) {
  const task =
    item.checked === null
      ? null
      : el("input", { class: "task-box", type: "checkbox", checked: item.checked, disabled: true, "aria-label": item.checked ? "Done" : "Not done" });
  return el("li", { class: task === null ? null : "task" }, task, inlineNodes(item.inline), item.blocks.map(blockNode));
}

/** The node of one block that `parseBlocks` read, as `prose` draws it in its `div.prose`. */
export function blockNode(block) {
  switch (block.type) {
    case "code":
      // A block of code is a box with its head (item 537): the language it was written for, a switch that wraps its long
      // lines and a copy of it, as it was written, not as drawn (item 439).
      return codeBlock({ text: block.text, label: block.title || block.language || "text", language: block.language, className: "prose-code" });
    case "heading":
      return el(`h${Math.min(block.level + 1, 4)}`, { dataset: { level: String(block.level) } }, inlineNodes(block.inline));
    case "list":
      return el(block.ordered ? "ol" : "ul", { start: block.ordered && block.start !== 1 ? String(block.start) : null }, block.items.map(itemNode));
    case "table": {
      const table = el(
        "table",
        null,
        el("thead", null, cellRow("th", block.header, block.aligns)),
        el("tbody", null, block.rows.map((row) => cellRow("td", row, block.aligns))),
      );
      // A table is a box with a head, as a block of code is (item 537), and has a copy of itself as markdown a press away
      // (item 403); the table scrolls sideways under its head.
      return el(
        "div",
        { class: "prose-table" },
        el("div", { class: "code-head" }, el("span", { class: "code-label" }, "table"), el("span", { class: "spacer" }), copyButton(() => tableMarkdown(table), "Copy the table")),
        el("div", { class: "prose-table-scroll" }, table),
      );
    }
    case "quote":
      return el("blockquote", null, block.blocks.map(blockNode));
    case "rule":
      return el("hr");
    default:
      return el("p", { class: "para" }, inlineNodes(block.inline));
  }
}

/** A `div.prose` holding the rendered text. */
export function prose(text) {
  return el("div", { class: "prose" }, parseBlocks(text).map(blockNode));
}
