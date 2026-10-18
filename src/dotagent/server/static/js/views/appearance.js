// Settings › Appearance (item 126): the colour scheme as three miniatures of Dot's window, each
// drawn on the tokens of the scheme it names (System split down the middle), and the themes as a gallery of cards with
// a light and a dark orb each, the card taking the theme for both schemes and an orb for its own; then the interface,
// the motion and the type (item 127), the density, the layout and the faces among them, which General held before.
// Themes of one's own (item 128) are made, copied, changed, exported, deleted and added from a file here; while one is
// being made the Themes card holds its editor and the other cards step aside.

import { facesMove, setFacesMove } from "../actors.js";
import { orb } from "../appearance/orbs.js";
import {
  copiedRoles,
  deleteOwnTheme,
  exportedText,
  freeId,
  idFromName,
  importedFile,
  saveOwnTheme,
  themeFolder,
} from "../appearance/own.js";
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
import { choiceSetting, paneHead, rangeSetting, switchSetting } from "../panels.js";
import {
  SCHEMES,
  allThemes,
  chooseScheme,
  chooseTheme,
  chooseThemeFor,
  currentScheme,
  findTheme,
  onSchemeChange,
  onThemeChange,
  onThemesChange,
  themeInUse,
  themePicks,
} from "../theme.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { themeEditor } from "./themeEditor.js";

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const NAME_UNITS = 48;
// A file larger than this is no theme, whatever it holds (a VS Code theme with its token colours runs to tens of KB).
const MOST_FILE_BYTES = 2 * 1024 * 1024;

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

// Take a theme for every scheme it has: both, or the one it was made for.
function pick(theme) {
  if (theme.schemes.length === 2) {
    chooseTheme(theme.id);
  } else {
    chooseThemeFor(theme.schemes[0], theme.id);
  }
}

/**
 * A theme's card: a button over the whole card for every scheme the theme has, an orb for each scheme on its own, and
 * the card's own buttons (`handlers`: duplicate for every theme, edit, export and remove for one's own).
 */
function themeCard(theme, handlers) {
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
  const orbs = new Map(theme.schemes.map((scheme) => [scheme, orbButton(scheme)]));
  const whole = theme.schemes.length === 2 ? "light and dark" : theme.schemes[0];
  const both = el("button", {
    class: "theme-card-pick",
    type: "button",
    "aria-label": `Use ${theme.label} for ${whole}`,
    title: theme.hint,
    onclick: () => pick(theme),
  });
  const action = (name, label, run) =>
    el(
      "button",
      {
        class: "btn ghost sm icon-only",
        type: "button",
        title: label,
        "aria-label": `${label} ${theme.label}`,
        "data-action": name,
        onclick: () => attempt(run),
      },
      icon(name),
    );
  const element = el(
    "div",
    { class: "theme-card", "data-theme-card": theme.id, "data-own": theme.own ? "" : null },
    both,
    el("div", { class: "theme-orbs" }, ...orbs.values()),
    el("div", { class: "theme-card-name" }, theme.label),
    el(
      "div",
      { class: "theme-card-actions" },
      action("copy", "Duplicate", () => handlers.duplicate(theme)),
      theme.own ? action("pencil", "Edit", () => handlers.edit(theme)) : null,
      theme.own ? action("download", "Export", () => handlers.exportTheme(theme)) : null,
      theme.own ? action("trash", "Delete", () => handlers.remove(theme)) : null,
    ),
  );
  function paint(picks) {
    for (const [scheme, button] of orbs) {
      button.setAttribute("aria-pressed", String(picks[scheme] === theme.id));
    }
    both.setAttribute("aria-pressed", String(theme.schemes.every((scheme) => picks[scheme] === theme.id)));
    element.toggleAttribute("data-in-use", picks.light === theme.id || picks.dark === theme.id);
  }
  return { element, paint };
}

// Hand a theme's file to the browser to download as `<id>.json`.
function download(theme) {
  const url = URL.createObjectURL(new Blob([exportedText(theme)], { type: "application/json" }));
  const link = el("a", { href: url, download: `${theme.id}.json`, hidden: true });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// The editor's start from the theme of each scheme (`sources`, a theme or null): its palette copied, and the action
// colour set by hand, none for a palette the generator made alone, else the palette's own, so that a new background
// or accent keeps it.
function startingPoint(sources) {
  const palettes = { light: null, dark: null };
  const actions = { light: null, dark: null };
  const tuned = { light: false, dark: false };
  for (const scheme of ["light", "dark"]) {
    const theme = sources[scheme];
    if (theme !== null) {
      const generated = theme.own && theme.file.managed === true;
      palettes[scheme] = copiedRoles(theme, scheme);
      actions[scheme] = generated ? null : palettes[scheme].messageAction;
      tuned[scheme] = !generated;
    }
  }
  return { palettes, actions, tuned };
}

// The start of a copy of `theme`: its palettes, for the schemes it has.
function copyOf(theme) {
  const has = (scheme) => (theme.schemes.includes(scheme) ? theme : null);
  return startingPoint({ light: has("light"), dark: has("dark") });
}

/**
 * The themes: the gallery of Dot's and one's own, and the making of one's own. `onEditing(editing)` is told when the
 * editor opens and closes, so that the other cards step aside while a theme is drawn on the page.
 */
function themesCard(onEditing) {
  const title = el("span", { class: "grow" }, "Themes");
  const create = el(
    "button",
    { class: "btn sm", type: "button", "data-action": "create", title: "Create theme", "aria-label": "Create theme" },
    icon("plus"),
    el("span", { class: "label" }, "Create theme"),
  );
  const add = el(
    "button",
    { class: "btn sm", type: "button", "data-action": "add", title: "Add theme", "aria-label": "Add theme" },
    icon("upload"),
    el("span", { class: "label" }, "Add theme"),
  );
  const chooser = el("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Theme file" });
  const body = el("div", { class: "card-pad stack" });
  const element = el(
    "section",
    { class: "card", "data-card": "themes" },
    el("div", { class: "card-head" }, title, create, add, chooser),
    body,
  );
  let cards = [];
  let editor = null;

  function paintPicks(picks) {
    for (const card of cards) {
      card.paint(picks);
    }
  }

  function showGallery() {
    cards = allThemes().map((theme) => themeCard(theme, { duplicate, edit, exportTheme: download, remove }));
    const { path, skipped } = themeFolder();
    // replaceChildren() writes a null as the word; el() leaves it out.
    body.replaceChildren(
      el(
        "div",
        { class: "hint" },
        "A card takes its theme for light and dark; an orb, for its own scheme only. A sun or a moon on an orb marks the theme in use for light or for dark.",
      ),
      el("div", { class: "theme-gallery" }, ...cards.map((card) => card.element)),
      el(
        "div",
        { class: "hint", "data-theme-folder": "" },
        "Themes of your own are kept by Dot in ",
        el("code", { class: "mono theme-folder-path" }, path),
        ", for every browser and the app; a theme file put there shows here.",
      ),
      ...(skipped.length === 0
        ? []
        : [
            el(
              "ul",
              { class: "theme-skipped hint", "aria-label": "Files left out of the themes folder" },
              ...skipped.map((entry) => el("li", null, el("code", null, entry.file), `: ${entry.reason}`)),
            ),
          ]),
    );
    paintPicks(themePicks());
  }

  function closeEditor() {
    editor.close();
    editor = null;
    title.textContent = "Themes";
    create.hidden = false;
    add.hidden = false;
    onEditing(false);
    showGallery();
  }

  // `id` is the theme changed, or null for a new one, which is picked once it is saved.
  function openEditor({ heading, id, name, start, keep }) {
    editor = themeEditor({
      name,
      palettes: start.palettes,
      actions: start.actions,
      tuned: start.tuned,
      async onSave({ name: named, palettes, managed }) {
        const target = id ?? freeId(idFromName(named));
        const base = palettes.light !== null ? "light" : "dark";
        const other = base === "light" ? "dark" : "light";
        const file = { version: 1, id: target, name: named, appearance: base, colors: palettes[base] };
        if (palettes[other] !== null) {
          file.variants = { [other]: palettes[other] };
        }
        await saveOwnTheme(target, { ...file, ...keep, ...(managed ? { managed: true } : {}) });
        closeEditor();
        if (id === null) {
          pick(findTheme(target));
        }
        toast("ok", `${named} saved.`);
      },
      onCancel: closeEditor,
    });
    title.textContent = heading;
    create.hidden = true;
    add.hidden = true;
    body.replaceChildren(editor.element);
    onEditing(true);
    element.scrollIntoView({ block: "nearest" });
    editor.focus();
  }

  // A new theme starts as the page is drawn now, from the theme picked for each scheme, so that
  // tuning what is in use is a change away; a new background or accent makes it the generator's.
  function createTheme() {
    const picks = themePicks();
    const start = startingPoint({ light: findTheme(picks.light), dark: findTheme(picks.dark) });
    openEditor({ heading: "New theme", id: null, name: "", start, keep: {} });
  }

  function duplicate(theme) {
    const name = `${theme.label} copy`.slice(0, NAME_UNITS);
    openEditor({ heading: `Copy of ${theme.label}`, id: null, name, start: copyOf(theme), keep: {} });
  }

  function edit(theme) {
    const keep = theme.file.collection === undefined ? {} : { collection: theme.file.collection };
    openEditor({ heading: `Edit ${theme.label}`, id: theme.id, name: theme.label, start: copyOf(theme), keep });
  }

  async function remove(theme) {
    const confirmed = await confirmDialog({
      title: `Delete ${theme.label}?`,
      message: "Its file goes from Dot's themes folder, for every browser and the app; where it is in use, Neutral takes its place.",
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await deleteOwnTheme(theme.id);
      toast("ok", `${theme.label} deleted.`);
    }
  }

  // A file the owner adds: a theme file or its short form, or a VS Code colour theme, kept as a theme.
  async function addFrom(file) {
    if (file.size > MOST_FILE_BYTES) {
      throw new Error(`${file.name} is larger than a theme file can be.`);
    }
    const theme = importedFile(await file.text());
    const id = freeId(theme.id ?? idFromName(theme.name));
    await saveOwnTheme(id, { ...theme, id });
    toast("ok", `${theme.name} added.`);
  }

  create.addEventListener("click", createTheme);
  add.addEventListener("click", () => chooser.click());
  chooser.addEventListener("change", () => {
    const [file] = chooser.files;
    chooser.value = "";
    if (file !== undefined) {
      attempt(() => addFrom(file));
    }
  });
  showGallery();
  const stopPicks = onThemeChange(paintPicks);
  const stopList = onThemesChange(() => {
    if (editor === null) {
      showGallery();
    }
  });
  return {
    element,
    dispose() {
      stopPicks();
      stopList();
      if (editor !== null) {
        editor.close();
      }
    },
  };
}

// The value of "Another family…" in a family's menu, which no stack has.
const ANOTHER = "another";
const TYPED_HINT = "Letters, digits, spaces, quotes, dots, dashes and underscores, with a comma between two families.";

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

// A family of the type (with Dot's stacks to start from): the stack Dot draws with, the others it offers, or
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
    hint: "The tool calls of a stretch of work fold into one line that says what they did, “Ran 2 commands and read 1 file”; open it to see every step.",
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

/** The text (item 331): a line of an answer over the size of every text, the body's on this device. */
function textCard() {
  const size = rangeOf({
    label: "Text size",
    hint: "Messages, labels and headings grow and shrink with it. 16 px is a phone's own and 14 px a Mac's; each device keeps its own.",
    setting: INTERFACE_SIZE,
    min: INTERFACE_SIZE.least,
    max: INTERFACE_SIZE.most,
    step: 1,
    unit: " px",
  });
  return section(
    "Text",
    el("div", { class: "card-pad stack" }, preview("answers", "Good morning. The weekly report is ready, and two things wait for you."), size.element),
  );
}

/** The type (item 127): the families of the interface, the answers, the composer and the code, and the sizes of the last
 * two. */
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
  const look = interfaceCard();
  const motion = motionCard();
  const others = [
    section("Colour scheme", el("div", { class: "card-pad" }, schemes.element)),
    textCard(),
    look.element,
    motion.element,
    typeCard().element,
  ];
  const themes = themesCard((editing) => {
    for (const card of others) {
      card.hidden = editing;
    }
  });
  root.append(
    paneHead("How Dot looks in this browser: the colour scheme, a theme for light and one for dark, the size of the text, the interface, the motion and the type. Themes of your own are kept by Dot, for every browser.", null),
    others[0],
    themes.element,
    ...others.slice(1),
  );
  return () => {
    schemes.dispose();
    themes.dispose();
    look.dispose();
    motion.dispose();
  };
}
