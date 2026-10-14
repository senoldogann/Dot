// Turns the text an agent wrote into DOM nodes. Everything goes in as text nodes; only http(s) links become anchors, and
// the path of a file in code a button that shows the file beside the conversation (item 195).

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
    case "link":
      return el("a", { href: part.href, target: "_blank", rel: "noopener noreferrer nofollow" }, part.value);
    default:
      return part.value;
  }
}

function inlineNodes(parts) {
  return parts.map(inlineNode);
}

function cellRow(tag, cells) {
  return el("tr", null, cells.map((cell) => el(tag, null, inlineNodes(cell))));
}

function blockNode(block) {
  switch (block.type) {
    case "code":
      return el("pre", null, el("code", null, block.text));
    case "heading":
      return el(`h${Math.min(block.level + 1, 4)}`, null, inlineNodes(block.inline));
    case "list":
      return el(
        block.ordered ? "ol" : "ul",
        null,
        block.items.map((item) => el("li", null, inlineNodes(item))),
      );
    case "table":
      return el(
        "table",
        null,
        el("thead", null, cellRow("th", block.header)),
        el("tbody", null, block.rows.map((row) => cellRow("td", row))),
      );
    case "quote":
      return el("blockquote", null, inlineNodes(block.inline));
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
