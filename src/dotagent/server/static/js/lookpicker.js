// The picker of a look (item 102): a character moving in the preview, and the shapes, the expressions and the colours
// as rows of radio buttons, each a still face. `lookPicker({ look, uid })` returns `{ element, value() }`; `value()`
// is the look picked, `{shape, expression, colour}`.
import { mountCharacter } from "./character/character.js";
import { EXPRESSION_IDS } from "./character/expressions.js";
import { COLOUR_IDS, SHAPE_IDS } from "./character/skins.js";
import { el } from "./dom.js";

/** A group of radio buttons: `name` joins them, and `label` is the word a screen reader names the group by (item 559),
 * not the machine name. */
function optionRow({ name, label, ids, picked, face, onPick }) {
  const inputs = ids.map((id) => {
    const input = el("input", { class: "visually-hidden look-radio", type: "radio", name, value: id, checked: id === picked });
    input.addEventListener("change", () => onPick(id));
    const cell = el("span", { class: "look-face" });
    face(cell, id);
    return el("label", { class: "look-option", title: id }, input, cell, el("span", { class: "look-name" }, id));
  });
  return el("div", { class: "look-options", role: "radiogroup", "aria-label": label }, ...inputs);
}

export function lookPicker({ look, uid }) {
  const chosen = { ...look };
  const stage = el("div", { class: "look-preview" });
  const preview = mountCharacter(stage, { ...chosen, size: 120, label: "Preview of the look" });
  const shapes = optionRow({
    name: `${uid}-shape`,
    label: "Shape",
    ids: SHAPE_IDS,
    picked: chosen.shape,
    face: (cell, id) => mountCharacter(cell, { shape: id, expression: "neutral", colour: chosen.colour, size: 40, moving: false, tight: true, phase: 0 }),
    onPick: (id) => {
      chosen.shape = id;
      preview.setShape(id);
    },
  });
  const expressions = optionRow({
    name: `${uid}-expression`,
    label: "Expression",
    ids: EXPRESSION_IDS,
    picked: chosen.expression,
    face: (cell, id) => mountCharacter(cell, { shape: "circle", expression: id, colour: chosen.colour, size: 40, moving: false, tight: true, phase: 0 }),
    onPick: (id) => {
      chosen.expression = id;
      preview.setExpression(id);
    },
  });
  const colours = optionRow({
    name: `${uid}-colour`,
    label: "Colour",
    ids: COLOUR_IDS,
    picked: chosen.colour,
    face: (cell, id) => mountCharacter(cell, { shape: "circle", expression: "neutral", colour: id, size: 28, moving: false, tight: true, phase: 0 }),
    onPick: (id) => {
      chosen.colour = id;
      preview.setColour(id);
      for (const face of element.querySelectorAll(".look-options .character")) {
        face.dataset.colour = id;
      }
      for (const swatch of colours.querySelectorAll(".character")) {
        swatch.dataset.colour = swatch.closest(".look-option").querySelector("input").value;
      }
    },
  });
  const element = el(
    "div",
    { class: "look-picker" },
    stage,
    el("div", { class: "stack" }, el("span", { class: "field-label" }, "Shape"), shapes, el("span", { class: "field-label" }, "Expression"), expressions, el("span", { class: "field-label" }, "Colour"), colours),
  );
  return { element, value: () => ({ ...chosen }), dispose: () => preview.destroy() };
}
