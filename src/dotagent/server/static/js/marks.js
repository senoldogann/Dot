// The marks things wear wherever they are listed (item 477): a service's mark on its Customize tile and on a server of
// it in the composer's menus, the plug on a service with no mark of its own (item 542), Dot's own icon on a server of
// no service (item 581), and a skill's on its Customize card and in the slash menu. The pages draw them from here, so
// that a mark is the same in both places.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { drawInked } from "./inked.js";

/** The mark of an imported skill, Dot's copy of another agent's (item 165), and of a skill Dot learned. */
export const IMPORTED_SKILL_MARK = "download";
export const LEARNED_SKILL_MARK = "brain";
// The source of a skill Dot learned, as the catalogue names it (skills/catalog.py).
const LEARNED_SOURCE = "learned";

// Dot's own icon (item 581), which a server of no service wears: web-research, which comes with Dot, and the servers
// added by hand. A service of the catalogue keeps its own mark, or the plug: its name is no one else's to draw.
const DOT_ICON = "/icons/icon-96.png";

/** A logo of `static/icons/plugins` in one colour, the ink of the tile it stands on (item 946): the file's shape drawn
 * inline (item 959, `inked.js`), so that a logo drawn in its brand's colours reads as one drawn in none, in every look
 * and scheme, and a repaint around it has no image to draw anew. The shape keeps its file in `data-logo`. */
function logoShape(file) {
  const shape = el("span", { class: "plugin-logo mark-logo", role: "presentation", dataset: { logo: file } });
  return drawInked(shape, `/icons/plugins/${encodeURIComponent(file)}`);
}

/** Whether the mark `mark` (`{ logo, glyph }`, a listing's or a server's) is a server's of no service (`null`), which
 * wears Dot's own icon (item 581). */
export function wearsDot(mark) {
  return mark === null;
}

/** What a tile shows of a service's mark (`{ logo, glyph }`, a listing's or a server's `mark`): its logo, else the
 * interface's icon, else the plug, which every connector without a mark of its own wears (item 542), each in the
 * tile's ink (item 946); a server of no service (`null`) wears Dot's own icon (item 581), which is its own tile
 * (`markTile`). */
export function markContent(mark) {
  if (wearsDot(mark)) {
    return el("img", { class: "plugin-logo dot-mark", src: DOT_ICON, alt: "" });
  }
  if (mark.logo !== null) {
    return logoShape(mark.logo);
  }
  return icon(mark.glyph ?? "plug");
}

/** Dress the tile `node` of the mark of a service or a server (`mark`, null for a server of no service): Dot's icon
 * is its own tile, as an application's icon is (item 581); every other mark stands on the quiet tile of its kind. */
export function markTile(node, mark) {
  if (wearsDot(mark)) {
    node.dataset.icon = "dot";
  }
}

/** The icon a skill of the palette wears, as its Customize card does: a learned one's, an imported one's, else its own
 * (item 408; a palette of an older daemon names none). */
export function skillMark(skill) {
  if (skill.source === LEARNED_SOURCE) {
    return LEARNED_SKILL_MARK;
  }
  if (skill.imported === true) {
    return IMPORTED_SKILL_MARK;
  }
  return skill.icon ?? "sparkles";
}
