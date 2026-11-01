// The newest of the words a run at work writes, as the office's panel shows them (item 905): rendered as Markdown, so
// cut between the blocks the chat page's own reader finds (markdown.js), never inside one. A code block, the one block
// that grows long as it is written, keeps its opening line and its newest lines.

import { parseSourcedBlocks } from "../markdown.js";

const LONGEST = 1600;

/** `text` itself when it is short; else "…" and its newest whole blocks, ~1,600 characters at most. When the newest
 * block alone is longer: a code block (open still, or closed) its opening line and its newest lines; another block
 * whole, as cutting it could leave a fence inside it without its opening.
 * @param {string} text
 * @returns {string} */
export function tailOf(text) {
  if (text.length <= LONGEST) return text;
  const blocks = parseSourcedBlocks(text);
  const kept = [];
  let size = 0;
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const source = blocks[index].source;
    if (kept.length > 0 && size + source.length + 2 > LONGEST) break;
    kept.unshift(blocks[index]);
    size += source.length + 2;
  }
  const last = kept[kept.length - 1];
  const longCode = kept.length === 1 && last.block.type === "code" && last.source.length > LONGEST;
  if (kept.length === blocks.length && !longCode) return text;
  if (longCode) {
    const lines = last.source.split("\n");
    const newest = [];
    let length = lines[0].length;
    for (let index = lines.length - 1; index > 0 && length + lines[index].length + 1 <= LONGEST; index -= 1) {
      newest.unshift(lines[index]);
      length += lines[index].length + 1;
    }
    return `…\n\n${[lines[0], ...newest].join("\n")}`;
  }
  return `…\n\n${kept.map((block) => block.source).join("\n\n")}`;
}
