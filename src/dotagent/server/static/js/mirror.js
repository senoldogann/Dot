// The markdown the composer's box holds, shown as it reads (item 207): a copy of the box's
// text lies under the box, its marks quiet, bold words, headings and a table's head heavier, code and the lines of a
// block of code tinted, quotes softer and links in the colour of links, while the box itself, its letters clear, keeps
// the caret, the selection, the spelling, the keys and the undo of a text field. The copy has to set its letters where
// the box sets its own, so nothing in it changes the size or the width of a letter (no other face, size, weight or
// slant; only colours, tints, and a stroke or a shadow for weight), and each of its lines is one text, as in the box:
// the marks within a line are highlights of that text (the CSS Custom Highlight API), since a line cut into elements
// may set its letters a fraction of a pixel apart from the box's, enough over a long line to wrap a word elsewhere.

import { el } from "./dom.js";
import { sourceLines } from "./markdown.js";

// The pieces of a line painted as highlights, each kind under the name `md-<kind>` (app.css); words and italics stay as
// the line's own text.
const HIGHLIGHTED = new Set(["mark", "strong", "code", "link", "address"]);
// A browser without highlights (before Chrome 105 or Safari 17.2) shows the lines' kinds without the marks' colours.
const CAN_HIGHLIGHT = "highlights" in CSS;

// The highlight of one kind of piece, shared by the copies of the page: each copy adds and takes away its own ranges.
function highlightOf(kind) {
  const name = `md-${kind}`;
  let highlight = CSS.highlights.get(name);
  if (highlight === undefined) {
    highlight = new Highlight();
    CSS.highlights.set(name, highlight);
  }
  return highlight;
}

function lineNode(line) {
  const text = line.pieces.map((part) => part.text).join("");
  // An empty line still takes a line, as it does in the box.
  return el("div", { class: `md-line md-${line.kind}` }, text === "" ? el("br") : text);
}

/**
 * The box `textarea` with its markdown shown under it. Returns `field` (the box and the copy under it, to place where
 * the box goes), `paint()`, which shows the text the box holds now and lines the copy up with the box, to call after
 * every change of the text (the composer's `autosize` does), and `dispose()`.
 */
export function markdownMirror(textarea) {
  const copy = el("div", { class: "composer-mirror", "aria-hidden": "true" });
  const field = el("div", { class: "composer-field" }, copy, textarea);
  // The ranges this copy painted, `[kind, range]`.
  let painted = [];
  let shown = null;
  // A paint asked for before the copy was disposed of (in a frame to come) paints nothing.
  let live = true;

  function unpaint() {
    for (const [kind, range] of painted) {
      highlightOf(kind).delete(range);
    }
    painted = [];
  }

  function highlight(node, line) {
    if (!CAN_HIGHLIGHT) {
      return;
    }
    let at = 0;
    for (const part of line.pieces) {
      if (HIGHLIGHTED.has(part.kind)) {
        const range = document.createRange();
        range.setStart(node.firstChild, at);
        range.setEnd(node.firstChild, at + part.text.length);
        highlightOf(part.kind).add(range);
        painted.push([part.kind, range]);
      }
      at += part.text.length;
    }
  }

  function follow() {
    copy.scrollTop = textarea.scrollTop;
  }

  function paint() {
    if (!live) {
      return;
    }
    if (textarea.value !== shown) {
      shown = textarea.value;
      unpaint();
      const lines = sourceLines(shown);
      const nodes = lines.map(lineNode);
      copy.replaceChildren(...nodes);
      lines.forEach((line, index) => highlight(nodes[index], line));
    }
    // A box that scrolls may give room to its scroll bar: the copy leaves the same room, and scrolls with the box.
    copy.style.setProperty("--mirror-bar", `${textarea.offsetWidth - textarea.clientWidth}px`);
    follow();
  }

  textarea.addEventListener("scroll", follow);
  // A box that changes size may wrap its lines elsewhere and gain or lose its scroll bar.
  const resized = new ResizeObserver(paint);
  resized.observe(textarea);
  // The box's letters go clear only once the copy shows them.
  paint();
  field.classList.add("mirrored");
  return {
    field,
    paint,
    dispose: () => {
      live = false;
      textarea.removeEventListener("scroll", follow);
      resized.disconnect();
      unpaint();
    },
  };
}
