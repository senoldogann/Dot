// The editor of a theme of one's own (item 128): a name, and for each scheme the theme has a palette
// for, the background and the accent the generator makes the palette from (appearance/generator.js) and the action
// colour Dot paints its buttons, switches and send button with, which starts as the generator's companion of the
// accent; under them the families of colours, to change one at a time. The page is drawn in the palette being changed
// while the editor is open (theme.js previewTokens), and in the picks again once it closes.

import { parseColour, toHex } from "../appearance/colour.js";
import { FAMILIES, vividPalette, withFamily } from "../appearance/generator.js";
import { paletteTokens } from "../appearance/tokens.js";
import { el } from "../dom.js";
import { foldGroup, segmented } from "../panels.js";
import { endPreview, previewTokens, shownScheme } from "../theme.js";

const NAME_UNITS = 48;
// The colours the guided fields hold, which the families leave out.
const GUIDED = ["canvas", "accent", "messageAction"];

// A colour of the editor: a swatch to pick it and the same as hex to type or paste one (hex, rgb() or oklch(), opaque).
function colourField(label, hint, onChange) {
  const swatch = el("input", { class: "swatch", type: "color", "aria-label": label });
  const text = el("input", {
    class: "input hex",
    type: "text",
    maxlength: "64",
    spellcheck: "false",
    autocomplete: "off",
    "aria-label": `${label}, as hex`,
  });
  swatch.addEventListener("input", () => {
    text.value = swatch.value;
    text.removeAttribute("aria-invalid");
    onChange(swatch.value);
  });
  text.addEventListener("change", () => {
    const parsed = parseColour(text.value.trim());
    if (parsed === null || parsed.alpha < 1) {
      text.setAttribute("aria-invalid", "true");
      return;
    }
    text.removeAttribute("aria-invalid");
    const colour = toHex(parsed.rgb);
    swatch.value = colour;
    text.value = colour;
    onChange(colour);
  });
  const element = el(
    "div",
    { class: "colour-field" },
    el("div", { class: "grow" }, el("div", null, label), hint === "" ? null : el("div", { class: "hint" }, hint)),
    swatch,
    text,
  );
  return {
    element,
    set(colour) {
      swatch.value = colour;
      text.value = colour;
      text.removeAttribute("aria-invalid");
    },
  };
}

// A few of the things Dot draws, on the tokens of the palette being changed: the owner's message, an answer, a muted
// line, the states of a run and the two kinds of button.
function sample() {
  return el(
    "div",
    { class: "theme-sample", "aria-hidden": "true" },
    el("div", { class: "bubble user" }, el("div", { class: "prose" }, el("p", { class: "para" }, "Plan a weekend by the coast"))),
    el(
      "div",
      { class: "prose" },
      el("p", { class: "para" }, "Two days by train: the harbour and the old town on Saturday, the cliff path on Sunday."),
    ),
    el("div", { class: "muted" }, "Worked for 12s"),
    el(
      "div",
      { class: "row wrap" },
      el("span", { class: "badge s-done" }, "done"),
      el("span", { class: "badge s-blocked" }, "waiting"),
      el("span", { class: "badge s-failed" }, "failed"),
      el("span", { class: "grow" }),
      el("button", { class: "btn sm", type: "button", tabindex: "-1" }, "Cancel"),
      el("button", { class: "btn primary sm", type: "button", tabindex: "-1" }, "Send"),
    ),
  );
}

/**
 * The editor. `palettes` are the roles of each scheme the theme has (`{ light, dark }`, null for a scheme it has none
 * for), `actions` the action colour of each set by hand or null for the generator's, `tuned` whether each palette is
 * more than the generator's alone. `onSave({ name, palettes, managed })` keeps the theme and may throw; the editor
 * says why under its buttons. Returns the element, `focus()` and `close()`, which puts the picks back on the page.
 */
export function themeEditor({ name, palettes, actions, tuned, onSave, onCancel }) {
  const schemes = ["light", "dark"].filter((scheme) => palettes[scheme] !== null);
  const state = {
    palettes: { ...palettes },
    seeds: Object.fromEntries(
      schemes.map((scheme) => [scheme, { canvas: palettes[scheme].canvas, accent: palettes[scheme].accent }]),
    ),
    actions: { ...actions },
    tuned: { ...tuned },
    editing: schemes.includes(shownScheme()) ? shownScheme() : schemes[0],
  };
  let frame = null;
  let closed = false;

  function preview() {
    if (frame !== null) {
      return;
    }
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!closed) {
        previewTokens(paletteTokens(state.palettes[state.editing]), state.editing);
      }
    });
  }

  // The palette of the scheme being changed made again from its background and accent, the action set by hand kept.
  function regenerate() {
    const seeds = state.seeds[state.editing];
    const made = vividPalette(seeds.canvas, seeds.accent);
    const action = state.actions[state.editing];
    state.palettes[state.editing] = action === null ? made : withFamily(made, "messageAction", action);
    state.tuned[state.editing] = false;
  }

  const nameField = el("input", {
    class: "input",
    type: "text",
    maxlength: String(NAME_UNITS),
    "aria-label": "Theme name",
    value: name,
  });
  const background = colourField("Background", "The page; the surfaces, lines and text are made from it.", (colour) => {
    state.seeds[state.editing].canvas = colour;
    regenerate();
    paint();
  });
  const accent = colourField("Accent", "Tints the surfaces and marks what is picked.", (colour) => {
    state.seeds[state.editing].accent = colour;
    regenerate();
    paint();
  });
  const action = colourField("Action", "Buttons, switches and the send button.", (colour) => {
    state.actions[state.editing] = colour;
    state.palettes[state.editing] = withFamily(state.palettes[state.editing], "messageAction", colour);
    paint();
  });
  const families = FAMILIES.filter((family) => !GUIDED.includes(family.role)).map((family) => ({
    role: family.role,
    field: colourField(family.label, "", (colour) => {
      state.palettes[state.editing] = withFamily(state.palettes[state.editing], family.role, colour);
      state.tuned[state.editing] = true;
      paint();
    }),
  }));
  const which = segmented(
    [
      { id: "light", label: "Light", hint: "The palette of the light scheme." },
      { id: "dark", label: "Dark", hint: "The palette of the dark scheme." },
    ],
    state.editing,
    (scheme) => {
      state.editing = scheme;
      paint();
    },
  );
  which.element.setAttribute("aria-label", "Palette");
  const alert = el("div", { class: "alert-line", role: "alert", hidden: true });
  const save = el("button", { class: "btn primary", type: "button" }, "Save theme");
  const cancel = el("button", { class: "btn", type: "button", onclick: onCancel }, "Cancel");

  function paint() {
    const palette = state.palettes[state.editing];
    background.set(state.seeds[state.editing].canvas);
    accent.set(state.seeds[state.editing].accent);
    action.set(palette.messageAction);
    for (const family of families) {
      family.field.set(palette[family.role]);
    }
    preview();
  }

  save.addEventListener("click", async () => {
    const named = nameField.value.trim();
    if (named === "") {
      alert.textContent = "Name the theme first.";
      alert.hidden = false;
      nameField.focus();
      return;
    }
    // Cancel waits as Save does: the theme is being kept, and the editor closes once it is.
    save.disabled = true;
    cancel.disabled = true;
    try {
      const managed = schemes.every((scheme) => !state.tuned[scheme] && state.actions[scheme] === null);
      await onSave({ name: named, palettes: state.palettes, managed });
    } catch (error) {
      console.error("the theme was not saved", error);
      alert.textContent = error.message;
      alert.hidden = false;
    } finally {
      save.disabled = false;
      cancel.disabled = false;
    }
  });

  const element = el(
    "div",
    { class: "theme-editor" },
    el(
      "div",
      { class: "stack" },
      el("label", { class: "field" }, el("span", { class: "field-label" }, "Name"), nameField),
      schemes.length === 2 ? el("div", { class: "row" }, el("span", { class: "grow" }, "Palette"), which.element) : null,
      background.element,
      accent.element,
      action.element,
      foldGroup({
        id: "theme-families",
        head: el("span", null, "Colour families"),
        body: el(
          "div",
          { class: "stack" },
          el(
            "div",
            { class: "hint" },
            "Each changes its colour and the ones that go with it. A new background or accent makes the palette again and undoes these.",
          ),
          ...families.map((family) => family.field.element),
        ),
        openByDefault: false,
      }),
      alert,
      el("div", { class: "row" }, el("span", { class: "grow" }), cancel, save),
    ),
    sample(),
  );
  paint();
  return {
    element,
    focus: () => nameField.focus(),
    close() {
      closed = true;
      if (frame !== null) {
        cancelAnimationFrame(frame);
        frame = null;
      }
      endPreview();
    },
  };
}
