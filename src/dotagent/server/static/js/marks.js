// The marks things wear wherever they are listed (item 477): a service's mark on its Customize tile and on a server of
// it in the composer's menus, the plug on a server of no service, and a skill's on its Customize card and in the slash
// menu. The pages draw them from here, so that a mark is the same in both places.

import { el } from "./dom.js";
import { icon } from "./icons.js";

// A colour lighter than this (its relative luminance, a contrast of 3 to 1 with the tile's white) does not read on the
// tile: the icon or letters drawn in it take the ink.
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

/** A logo of `static/icons/plugins` as a picture, in its own colours. */
function logoPicture(file) {
  return el("img", { class: "plugin-logo", src: `/icons/plugins/${file}`, alt: "" });
}

/** The colour the icon or the letters of the mark of `colour` (six hex digits, or null) are drawn in on the light
 * tile: `#` and the colour where it reads there, else null, for the ink. */
export function markInk(colour) {
  return colour !== null && luminance(colour) <= LIGHTEST_INK ? `#${colour}` : null;
}

/** What a tile shows of a service's mark (`{ logo, glyph, monogram }`, a listing's or a server's `mark`): its logo,
 * else the interface's icon, else its letters; a server of no service (`null`) wears the plug. */
export function markContent(mark) {
  if (mark === null) {
    return icon("plug");
  }
  if (mark.logo !== null) {
    return logoPicture(mark.logo);
  }
  if (mark.glyph !== null) {
    return icon(mark.glyph);
  }
  return el("span", { class: "plugin-letters" }, mark.monogram);
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
