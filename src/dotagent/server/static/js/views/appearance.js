// Settings › Appearance (item 126), after T3 Code's page: the colour scheme as three miniatures of Dot's window, each
// drawn on the tokens of the scheme it names (System split down the middle), and the themes as a gallery of cards with
// a light and a dark orb each, the card taking the theme for both schemes and an orb for its own; then the interface,
// the motion and the type (item 127), the density, the layout and the faces among them, which General held before.

import { facesMove, setFacesMove } from "../actors.js";
import { orb } from "../appearance/orbs.js";
import {
  ANSWER_FONT,
  CHAT_WIDTH,
  CODE_FONT,
  CODE_SIZE,
  COMPOSER_FONT,
  COMPOSER_SIZE,
  CONTRAST,
  INTERFACE_FONT,
  INTERFACE_SIZE,
  PANEL_MOTION,
  SMOOTHING,
  WRAP_CODE,
  accepts,
  setValue,
  valueOf,
} from "../appearance/settings.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import {
  chooseDensity,
  currentDensity,
  DENSITIES,
  followTheme,
  foldSteps,
  hasOwnLayout,
  setFoldSteps,
  setShowInspector,
  showInspector,
} from "../layout.js";
import { paneHead, rangeSetting, segmented, switchSetting } from "../panels.js";
import {
  SCHEMES,
  THEMES,
  chooseScheme,
  chooseTheme,
  chooseThemeFor,
  currentScheme,
  onSchemeChange,
  onThemeChange,
  themeInUse,
  themePicks,
} from "../theme.js";

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

function section(title, body) {
  const head = el("div", { class: "card-head" }, el("span", { class: "grow" }, title));
  return el("section", { class: "card" }, head, body);
}

// Dot's window in one scheme, drawn small: the sidebar with its search and three rows, the owner's message and two
// lines of an answer, the composer and its send button, each on the token the window draws it with.
function pane(scheme) {
  return el(
    "span",
    { class: "wire-pane", "data-pane": scheme },
    el(
      "span",
      { class: "wire-side" },
      el("span", { class: "wire-search" }),
      ...[1, 2, 3].map(() => el("span", { class: "wire-row" })),
    ),
    el("span", { class: "wire-bubble" }),
    el("span", { class: "wire-line" }),
    el("span", { class: "wire-line" }),
    el("span", { class: "wire-composer" }, el("span", { class: "wire-send" })),
  );
}

/** The three cards of the colour scheme; the one chosen is pressed. */
function schemeCards() {
  const buttons = new Map();
  for (const scheme of SCHEMES) {
    const panes = scheme.id === "system" ? [pane("light"), pane("dark")] : [pane(scheme.id)];
    const button = el(
      "button",
      { class: "scheme-card", type: "button", title: scheme.hint, onclick: () => chooseScheme(scheme.id) },
      el("span", { class: "wire", "data-wire": scheme.id, "aria-hidden": "true" }, ...panes),
      el("span", { class: "scheme-card-label" }, scheme.label),
    );
    buttons.set(scheme.id, button);
  }
  function paint() {
    for (const [id, button] of buttons) {
      button.setAttribute("aria-pressed", String(id === currentScheme()));
    }
  }
  paint();
  const group = { class: "scheme-cards", role: "group", "aria-label": "Colour scheme" };
  const element = el("div", group, ...buttons.values());
  return { element, dispose: onSchemeChange(paint) };
}

/** A theme's card: a button over the whole card for both schemes, and an orb for each scheme on its own. */
function themeCard(theme) {
  const orbButton = (scheme) =>
    el(
      "button",
      {
        class: "theme-orb",
        type: "button",
        "data-orb": scheme,
        "aria-label": `Use ${theme.label} for ${scheme}`,
        title: `Use for ${scheme} only`,
        onclick: () => chooseThemeFor(scheme, theme.id),
      },
      orb(theme.orb(scheme), scheme),
      el("span", { class: "theme-orb-badge", "aria-hidden": "true" }, icon(scheme === "light" ? "sun" : "moon")),
    );
  const light = orbButton("light");
  const dark = orbButton("dark");
  const both = el("button", {
    class: "theme-card-pick",
    type: "button",
    "aria-label": `Use ${theme.label} for light and dark`,
    title: theme.hint,
    onclick: () => chooseTheme(theme.id),
  });
  const element = el(
    "div",
    { class: "theme-card", "data-theme-card": theme.id },
    both,
    el("div", { class: "theme-orbs" }, light, dark),
    el("div", { class: "theme-card-name" }, theme.label),
  );
  function paint(picks) {
    light.setAttribute("aria-pressed", String(picks.light === theme.id));
    dark.setAttribute("aria-pressed", String(picks.dark === theme.id));
    both.setAttribute("aria-pressed", String(picks.light === theme.id && picks.dark === theme.id));
    element.toggleAttribute("data-in-use", picks.light === theme.id || picks.dark === theme.id);
  }
  return { element, paint };
}

function themeGallery() {
  const cards = THEMES.map(themeCard);
  const paint = (picks) => {
    for (const card of cards) {
      card.paint(picks);
    }
  };
  paint(themePicks());
  const element = el(
    "div",
    { class: "card-pad stack" },
    el(
      "div",
      { class: "hint" },
      "A card takes its theme for light and dark; an orb, for its own scheme only. Clean is T3 Code's own look and Rose T3 Chat's; Grove, Ocean, Ember and Iris are T3 Code's palettes.",
    ),
    el("div", { class: "theme-gallery" }, ...cards.map((card) => card.element)),
  );
  return { element, dispose: onThemeChange(paint) };
}

// The value of "Another family…" in a family's menu, which no stack has.
const ANOTHER = "another";
const TYPED_HINT = "Letters, digits, spaces, quotes, dots, dashes and underscores, with a comma between two families.";

// A choice of a few, as Density is drawn: the label and the buttons on a line, the hint under them.
function choiceSetting({ label, hint, options, selected, onSelect }) {
  const choice = segmented(options, selected, onSelect);
  choice.element.setAttribute("aria-label", label);
  const element = el(
    "div",
    { class: "setting" },
    el("div", { class: "row" }, el("span", { class: "grow" }, label), choice.element),
    el("div", { class: "hint" }, hint),
  );
  return { element, choice };
}

// An on/off setting of appearance/settings.js, whose values are "on" and "off".
function onOffSetting({ label, hint, setting }) {
  const toggle = switchSetting({
    label,
    hint,
    read: () => valueOf(setting) === "on",
    write: (on) => setValue(setting, on ? "on" : "off"),
    onToggled: () => toggle.paint(),
  });
  toggle.paint();
  return toggle;
}

function rangeOf({ label, hint, setting, min, max, step, unit }) {
  return rangeSetting({
    label,
    hint,
    min,
    max,
    step,
    format: (value) => `${value}${unit}`,
    read: () => valueOf(setting),
    write: (value) => setValue(setting, value),
  });
}

// A family of the type (T3 Code's, with Dot's stacks to start from): the stack Dot draws with, the others it offers, or
// "Another family…", which opens a field for families typed, a comma between two; a family typed goes before the stack
// it replaces. What the field cannot take is said under it and not kept.
function familySetting({ label, hint, setting, stacks }) {
  const select = el(
    "select",
    { class: "select sm", "aria-label": label },
    ...stacks.map((stack) => el("option", { value: stack.id }, stack.label)),
    el("option", { value: ANOTHER }, "Another family…"),
  );
  const field = el("input", {
    class: "input",
    type: "text",
    maxlength: "200",
    placeholder: "A family, or several with commas",
    "aria-label": `${label}: another family`,
  });
  const note = el("div", { class: "hint family-note" }, hint);
  const offered = (value) => stacks.some((stack) => stack.id === value);
  function paint() {
    const value = valueOf(setting);
    select.value = offered(value) ? value : ANOTHER;
    field.hidden = offered(value);
    field.value = offered(value) ? "" : value;
    field.removeAttribute("aria-invalid");
    note.removeAttribute("data-invalid");
    note.textContent = hint;
  }
  select.addEventListener("change", () => {
    if (select.value === ANOTHER) {
      field.hidden = false;
      field.focus();
      return;
    }
    setValue(setting, select.value);
    paint();
  });
  field.addEventListener("change", () => {
    const typed = field.value.trim();
    if (typed !== "" && !accepts(setting, typed)) {
      field.setAttribute("aria-invalid", "true");
      note.setAttribute("data-invalid", "");
      note.textContent = TYPED_HINT;
      return;
    }
    setValue(setting, typed);
    paint();
  });
  // Left empty, "Another family…" goes back to the family in use.
  field.addEventListener("blur", () => {
    if (field.value.trim() === "") {
      paint();
    }
  });
  paint();
  const element = el(
    "div",
    { class: "setting" },
    el("div", { class: "row" }, el("span", { class: "grow" }, label), select),
    field,
    note,
  );
  return { element };
}

// A line of type drawn on the tokens a setting writes, so that it follows the setting as it moves.
function preview(kind, words) {
  return el("div", { class: "type-preview", "data-preview": kind }, words);
}

/**
 * The interface: the contrast and the chat's width (item 127), the density, and the layout this browser starts with,
 * which follows the theme picked for light until a switch is set.
 */
function interfaceCard() {
  const contrast = rangeOf({
    label: "Contrast",
    hint: "How far the text and the lines stand from the page: under 100% they fade toward it, over 100% they move toward black in light and white in dark.",
    setting: CONTRAST,
    min: 50,
    max: 200,
    step: 5,
    unit: "%",
  });
  const width = choiceSetting({
    label: "Chat width",
    hint: "How wide the home and a conversation read.",
    options: [
      { id: "comfortable", label: "Comfortable", hint: "768 px, Dot's own." },
      { id: "wide", label: "Wide", hint: "1152 px." },
      { id: "full", label: "Full", hint: "As wide as the window." },
    ],
    selected: valueOf(CHAT_WIDTH),
    onSelect: (id) => setValue(CHAT_WIDTH, id),
  });
  const density = choiceSetting({
    label: "Density",
    hint: "How close the rows, cards and lists sit.",
    options: DENSITIES.map((option) => ({ id: option.id, label: option.label, hint: option.hint })),
    selected: currentDensity(),
    onSelect: chooseDensity,
  });
  const layoutNote = el("div", { class: "hint grow" });
  const fold = switchSetting({
    label: "Fold tool steps",
    hint: "The tool calls of a turn collapse into one “Worked for…” line; open it to see every step.",
    read: foldSteps,
    write: setFoldSteps,
    onToggled: paint,
  });
  const inspector = switchSetting({
    label: "Details panel",
    hint: "On wide windows the run page shows its details, plan and screenshots in a panel beside the conversation.",
    read: showInspector,
    write: setShowInspector,
    onToggled: paint,
  });
  const reset = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      onclick: () => {
        followTheme();
        paint();
      },
    },
    "Follow the theme",
  );
  function paint() {
    fold.paint();
    inspector.paint();
    reset.disabled = !hasOwnLayout();
    layoutNote.textContent = hasOwnLayout()
      ? "You set these two yourself; they stay as they are when you change the theme."
      : `These two follow ${themeInUse().label}, the theme picked for light, until you change one of them.`;
  }
  paint();
  const element = section(
    "Interface",
    el(
      "div",
      { class: "card-pad stack" },
      contrast.element,
      width.element,
      density.element,
      fold.element,
      inspector.element,
      el("div", { class: "setting" }, el("div", { class: "row" }, layoutNote, reset)),
    ),
  );
  return { element, dispose: onThemeChange(paint) };
}

// The slide of the panels (item 127) and the faces that move (item 103): the home's, the run page's and the previews;
// off leaves every face still. While the system asks for less motion every face holds still and no panel slides
// whatever the settings say (item 107); the note says so, and where the system's own switch is.
function motionCard() {
  const panels = rangeOf({
    label: "Panel animation",
    hint: "How long the sidebar and the details panel take to slide in and out; at 0 they are there at once.",
    setting: PANEL_MOTION,
    min: 0,
    max: 400,
    step: 25,
    unit: " ms",
  });
  const faces = switchSetting({
    label: "Faces that move",
    hint: "Dot's face on the home and the face beside a chat move while they work and rest; off, every face is a still frame.",
    read: facesMove,
    write: setFacesMove,
    onToggled: () => faces.paint(),
  });
  const motionNote = el(
    "div",
    { class: "hint", "data-reduced-motion": "" },
    "Your system reduces motion, so every face holds still and the panels never slide, whatever this says. On a Mac: System Settings › Accessibility › Display › Reduce motion.",
  );
  faces.element.append(motionNote);
  const paintMotion = () => {
    motionNote.hidden = !REDUCED_MOTION.matches;
  };
  REDUCED_MOTION.addEventListener("change", paintMotion);
  paintMotion();
  faces.paint();
  const element = section("Motion", el("div", { class: "card-pad stack" }, panels.element, faces.element));
  return { element, dispose: () => REDUCED_MOTION.removeEventListener("change", paintMotion) };
}

/** The type (item 127): the families and sizes of the interface, the answers, the composer and the code. */
function typeCard() {
  const interfaceFont = familySetting({
    label: "Interface font",
    hint: "The menus, the lists and the pages.",
    setting: INTERFACE_FONT,
    stacks: [
      { id: "", label: "Inter" },
      { id: "system", label: "The system's" },
    ],
  });
  const interfaceSize = rangeOf({
    label: "Interface size",
    hint: "Every text of the interface grows or shrinks with it; 14 px is Dot's own.",
    setting: INTERFACE_SIZE,
    min: 13,
    max: 17,
    step: 1,
    unit: " px",
  });
  const answers = choiceSetting({
    label: "Answers",
    hint: "The font of Dot's answers.",
    options: [
      { id: "theme", label: "Theme", hint: "As the theme says: the interface's font, or a serif in Paper." },
      { id: "interface", label: "Interface", hint: "The interface's font in every theme." },
      { id: "serif", label: "Serif", hint: "A serif in every theme." },
    ],
    selected: valueOf(ANSWER_FONT),
    onSelect: (id) => setValue(ANSWER_FONT, id),
  });
  const composerFont = familySetting({
    label: "Composer font",
    hint: "What you type for Dot.",
    setting: COMPOSER_FONT,
    stacks: [
      { id: "", label: "The interface's" },
      { id: "serif", label: "Serif" },
      { id: "mono", label: "The code's" },
    ],
  });
  const composerSize = rangeOf({
    label: "Composer size",
    hint: "15 px is Dot's own.",
    setting: COMPOSER_SIZE,
    min: 12,
    max: 20,
    step: 1,
    unit: " px",
  });
  const codeFont = familySetting({
    label: "Code font",
    hint: "Code, commands and file names.",
    setting: CODE_FONT,
    stacks: [
      { id: "", label: "Geist Mono" },
      { id: "sf-mono", label: "SF Mono" },
      { id: "menlo", label: "Menlo" },
    ],
  });
  const codeSize = rangeOf({
    label: "Code size",
    hint: "The code blocks of an answer; 13 px is Dot's own.",
    setting: CODE_SIZE,
    min: 10,
    max: 18,
    step: 1,
    unit: " px",
  });
  const smoothing = onOffSetting({
    label: "Font smoothing",
    hint: "Thinner strokes on a Mac; off, the system's heavier ones.",
    setting: SMOOTHING,
  });
  const wrap = onOffSetting({
    label: "Wrap long lines of code",
    hint: "A long line of code in an answer wraps instead of scrolling sideways.",
    setting: WRAP_CODE,
  });
  const element = section(
    "Type",
    el(
      "div",
      { class: "card-pad stack" },
      interfaceFont.element,
      interfaceSize.element,
      preview("interface", "Dot reads the page, fills in the form and asks before it sends."),
      answers.element,
      preview("answers", "The report is ready: three files changed, and the tests pass."),
      composerFont.element,
      composerSize.element,
      preview("composer", "Summarise the three newest files on my Desktop"),
      codeFont.element,
      codeSize.element,
      preview("code", 'const files = await list("~/Desktop"); // the three newest, by the time they changed'),
      smoothing.element,
      wrap.element,
    ),
  );
  return { element };
}

/** Draw the Appearance tab into `root`. Returns the function that stops its listeners. */
export function mount(root) {
  const schemes = schemeCards();
  const gallery = themeGallery();
  const look = interfaceCard();
  const motion = motionCard();
  root.append(
    paneHead("How Dot looks in this browser: the colour scheme, a theme for light and one for dark, the interface, the motion and the type. Kept in this browser.", null),
    section("Colour scheme", el("div", { class: "card-pad" }, schemes.element)),
    section("Themes", gallery.element),
    look.element,
    motion.element,
    typeCard().element,
  );
  return () => {
    schemes.dispose();
    gallery.dispose();
    look.dispose();
    motion.dispose();
  };
}
