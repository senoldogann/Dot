// Settings › Appearance (item 126), after T3 Code's page: the colour scheme as three miniatures of Dot's window, each
// drawn on the tokens of the scheme it names (System split down the middle), and the themes as a gallery of cards with
// a light and a dark orb each, the card taking the theme for both schemes and an orb for its own; then the layout and
// the motion of this browser, which General held before.

import { facesMove, setFacesMove } from "../actors.js";
import { orb } from "../appearance/orbs.js";
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
import { paneHead, segmented, switchSetting } from "../panels.js";
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

/** The layout this browser starts with, which follows the theme picked for light until a switch is set. */
function layoutCard() {
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
  const density = segmented(
    DENSITIES.map((option) => ({ id: option.id, label: option.label, hint: option.hint })),
    currentDensity(),
    chooseDensity,
  );
  density.element.setAttribute("aria-label", "Density");
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
    "Layout",
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "Density"), density.element),
        el("div", { class: "hint" }, "How close the rows, cards and lists sit. Kept in this browser."),
      ),
      fold.element,
      inspector.element,
      el("div", { class: "setting" }, el("div", { class: "row" }, layoutNote, reset)),
    ),
  );
  return { element, dispose: onThemeChange(paint) };
}

// The faces that move (item 103): the home's, the run page's and the previews; off leaves every face still. While the
// system asks for less motion every face holds still whatever the switch says (item 107); the note says so, and where
// the system's own switch is.
function motionCard() {
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
    "Your system reduces motion, so every face holds still whatever this says. On a Mac: System Settings › Accessibility › Display › Reduce motion.",
  );
  faces.element.append(motionNote);
  const paintMotion = () => {
    motionNote.hidden = !REDUCED_MOTION.matches;
  };
  REDUCED_MOTION.addEventListener("change", paintMotion);
  paintMotion();
  faces.paint();
  const element = section("Motion", el("div", { class: "card-pad stack" }, faces.element));
  return { element, dispose: () => REDUCED_MOTION.removeEventListener("change", paintMotion) };
}

/** Draw the Appearance tab into `root`. Returns the function that stops its listeners. */
export function mount(root) {
  const schemes = schemeCards();
  const gallery = themeGallery();
  const layout = layoutCard();
  const motion = motionCard();
  root.append(
    paneHead("How Dot looks in this browser: the colour scheme, a theme for light and one for dark, the layout and the motion. Kept in this browser.", null),
    section("Colour scheme", el("div", { class: "card-pad" }, schemes.element)),
    section("Themes", gallery.element),
    layout.element,
    motion.element,
  );
  return () => {
    schemes.dispose();
    gallery.dispose();
    layout.dispose();
    motion.dispose();
  };
}
