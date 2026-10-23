// The marks things wear wherever they are listed (item 477): a service's mark on its Customize tile and on a server of
// it in the composer's menus, the plug on a service with no mark of its own (item 542), Dot's own icon on a server of
// no service (item 581), and a skill's on its Customize card and in the slash menu. The pages draw them from here, so
// that a mark is the same in both places.

import { el } from "./dom.js";
import { hashId } from "./hues.js";
import { icon } from "./icons.js";

// A colour lighter than this (its relative luminance, a contrast of 3 to 1 with the tile's white) does not read on the
// tile: an icon drawn in it takes the ink.
const LIGHTEST_INK = 0.3;

/** The mark of an imported skill, Dot's copy of another agent's (item 165), and of a skill Dot learned. */
export const IMPORTED_SKILL_MARK = "download";
export const LEARNED_SKILL_MARK = "brain";
// The source of a skill Dot learned, as the catalogue names it (skills/catalog.py).
const LEARNED_SOURCE = "learned";

/** The relative luminance of the colour `hex` (six digits, no "#"), as WCAG reckons it. */
function luminance(hex) {
  const [r, g, b] = [0, 2, 4].map((at) => {
    const value = parseInt(hex.slice(at, at + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// Dot's own icon (item 581), which a server of no service wears: web-research, which comes with Dot, and the servers
// added by hand. A service of the catalogue keeps its own mark, or the plug: its name is no one else's to draw.
const DOT_ICON = "/icons/icon-96.png";

/** A logo of `static/icons/plugins` as a picture, in its own colours. */
function logoPicture(file) {
  return el("img", { class: "plugin-logo", src: `/icons/plugins/${file}`, alt: "" });
}

/** The colour the icon of the mark of `colour` (six hex digits, or null) is drawn in on the light tile: `#` and the
 * colour where it reads there, else null, for the ink. */
export function markInk(colour) {
  return colour !== null && luminance(colour) <= LIGHTEST_INK ? `#${colour}` : null;
}

/** Whether the mark `mark` (`{ logo, glyph }`, a listing's or a server's) is a server's of no service (`null`), which
 * wears Dot's own icon (item 581). */
export function wearsDot(mark) {
  return mark === null;
}

/** What a tile shows of a service's mark (`{ logo, glyph }`, a listing's or a server's `mark`): its logo, else the
 * interface's icon, else the plug, which every connector without a mark of its own wears (item 542); a server of no
 * service (`null`) wears Dot's own icon (item 581), which is its own tile (`markTile`). */
export function markContent(mark) {
  if (wearsDot(mark)) {
    return el("img", { class: "plugin-logo dot-mark", src: DOT_ICON, alt: "" });
  }
  if (mark.logo !== null) {
    return logoPicture(mark.logo);
  }
  return icon(mark.glyph ?? "plug");
}

/** The colours of themes.css (`--mark-<name>`) a mark with no colour of its own takes one of, by its id (item 542). */
const MARK_HUES = ["blue", "pink", "violet", "teal", "orange", "green"];

/** Put the icon of a mark that is no logo (`{ logo, colour }`, or null for a server of no service) on a tile `node` of
 * its colour (item 542), as a logo stands on its own artwork: the icon is white, or the ink on a colour too light for
 * white (`data-light`). A mark with no colour takes one of `MARK_HUES` by `id`, the same wherever it shows. A logo's
 * tile stays light. */
export function tintMark(node, mark, id) {
  if (mark !== null && mark.logo !== null) {
    return;
  }
  node.dataset.tint = "";
  if (mark !== null && mark.colour !== null) {
    node.style.setProperty("--plugin-hue", `#${mark.colour}`);
    if (markInk(mark.colour) === null) {
      node.dataset.light = "";
    }
    return;
  }
  node.style.setProperty("--plugin-hue", `var(--mark-${MARK_HUES[hashId(id) % MARK_HUES.length]})`);
}

/** Dress the tile `node` of the mark of a service or a server (`mark`, null for a server of no service; `id` its
 * name): Dot's icon is its own tile, as an application's icon is (item 581); a mark that is no logo stands on a tile of
 * its colour (`tintMark`). */
export function markTile(node, mark, id) {
  if (wearsDot(mark)) {
    node.dataset.icon = "dot";
    return;
  }
  tintMark(node, mark, id);
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
