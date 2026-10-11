// One still frame of a character as a standalone SVG document, for the tab's icon and the notifications, where no
// stylesheet reaches: the colours are bloub's reference hex values on white paper. The string is built from numbers
// and ids of this folder's catalogues alone; nothing of it comes from the outside.
import { NOTIF_BLUE } from "./decor.js";
import { CharacterEngine } from "./engine.js";
import { EXPRESSION_BY_ID } from "./expressions.js";
import { RADIUS } from "./repere.js";
import { COLOUR_BY_ID, SHAPE_BY_ID } from "./skins.js";
import { POSES } from "./states.js";

const HALF = RADIUS * 1.2;
const PAPER = "#ffffff";

function known(map, id, what) {
  const found = map.get(id);
  if (!found) throw new RangeError(`unknown character ${what}: ${id}`);
  return found;
}

// `look` is {shape, expression, colour}; `animation` the state to pose; `expression` replaces the look's at rest when
// given; `label` goes on the root as `data-state`, for the page that reads the icon back.
export function stillSvg(look, animation, expression, label) {
  const shape = known(SHAPE_BY_ID, look.shape, "shape");
  const rest = known(EXPRESSION_BY_ID, expression ?? look.expression, "expression");
  const colour = known(COLOUR_BY_ID, look.colour, "colour");
  const engine = new CharacterEngine(RADIUS, animation, shape.radii, rest);
  const frame = engine.sample(POSES[animation] ?? 1);
  const box = `x="${-HALF}" y="${-HALF}" width="${HALF * 2}" height="${HALF * 2}"`;
  const eyes = frame.eyes
    .map((eye) => `<path d="${eye.d}" transform="${eye.matrix}" opacity="${eye.alpha}" fill="#000"/>`)
    .join("");
  const notch = frame.notch ? `<circle cx="${frame.notch.x}" cy="${frame.notch.y}" r="${frame.notch.r}" fill="#000"/>` : "";
  const notif = frame.notif ? `<circle cx="${frame.notif.x}" cy="${frame.notif.y}" r="${frame.notif.r}" fill="${NOTIF_BLUE}"/>` : "";
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${-HALF} ${-HALF} ${HALF * 2} ${HALF * 2}" data-state="${label}">` +
    `<mask id="m" maskUnits="userSpaceOnUse" ${box}><path d="${frame.bodyPath}" fill="#fff"/>${eyes}${notch}</mask>` +
    `<path d="${frame.bodyPath}" fill="${PAPER}"/><rect ${box} fill="${colour.hex}" mask="url(#m)"/>${notif}</svg>`
  );
}
