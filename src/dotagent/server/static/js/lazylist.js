// Long lists drawn a page at a time (item 2017's code view, the file panel's Markdown and grids): the first page at
// once, each next one as the reader scrolls near the end of what is drawn, so a file of thousands of lines or rows
// opens at once and its later rows cost nothing until they are read.

import { el } from "./dom.js";

/**
 * Append `count` nodes to `holder`, `make(index)` each, `size` at a time: the first page now, the next when a mark after
 * the last drawn node comes within 800 px of the view of `root`, the element that scrolls them.
 * @param {Element} holder
 * @param {number} count
 * @param {number} size
 * @param {(index: number) => Node} make
 * @param {Element} root
 */
export function appendInPages(holder, count, size, make, root) {
  let drawn = 0;
  // In a table's body the mark is a row, so the table lays it out as one.
  const sentinel = el(holder.tagName === "TBODY" ? "tr" : "div", { class: "lazy-more", "aria-hidden": "true" });
  const more = () => {
    const next = Math.min(count, drawn + size);
    const nodes = [];
    for (let index = drawn; index < next; index += 1) {
      nodes.push(make(index));
    }
    drawn = next;
    sentinel.remove();
    holder.append(...nodes);
    if (drawn < count) {
      holder.append(sentinel);
    }
  };
  more();
  if (drawn >= count) {
    return;
  }
  const watch = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        more();
        if (drawn >= count) {
          watch.disconnect();
        }
      }
    },
    { root, rootMargin: "800px" },
  );
  watch.observe(sentinel);
}
