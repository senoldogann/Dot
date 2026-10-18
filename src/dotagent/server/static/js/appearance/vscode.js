// A VS Code colour theme as a theme of one's own (item 128), derived code (see LICENSE in this folder). A VS Code theme
// describes an editor's chrome, not a palette: a few hundred workbench keys, most unset, overlays in hex with alpha.
// The palette the generator derives from the editor's background and a visible accent is the floor, and the theme's own
// workbench colours replace the roles they name where they stay readable and stand apart from the surfaces beside them;
// overlays are laid over the surface they sit on.

import { contrast, luminance, mix, parseColour, toHex } from "./colour.js";
import { vividPalette } from "./generator.js";
import { PALETTES } from "./palettes.js";

// The luminance under which a canvas is dark.
const DARK_LUMINANCE = 0.179;
const HEX = /^(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const NAME_UNITS = 48;
// The keys whose colour stands for the theme's accent, the first visible one taken.
const ACCENT_KEYS = [
  "focusBorder",
  "button.background",
  "textLink.foreground",
  "activityBarBadge.background",
  "progressBar.background",
  "badge.background",
];

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function decodeGamma(value) {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function encodeGamma(value) {
  const clamped = Math.max(0, Math.min(1, value));
  return clamped <= 0.0031308 ? clamped * 12.92 : 1.055 * clamped ** (1 / 2.4) - 0.055;
}

// `color(display-p3 r g b / a)` or `color(srgb …)`, clipped to sRGB as an sRGB screen shows it; null for anything else.
function colorFunction(value) {
  const match = /^color\(\s*(display-p3|srgb)\s+([^)]+)\)$/i.exec(value);
  if (match === null) {
    return null;
  }
  const [channelPart, alphaPart] = match[2].split("/");
  const read = (part) => (part.endsWith("%") ? Number.parseFloat(part) / 100 : Number.parseFloat(part));
  const channels = channelPart.trim().split(/\s+/).map(read);
  const alpha = alphaPart === undefined ? 1 : read(alphaPart.trim());
  if (channels.length !== 3 || !channels.every(Number.isFinite) || !Number.isFinite(alpha)) {
    return null;
  }
  const a = Math.max(0, Math.min(1, alpha));
  if (match[1].toLowerCase() === "srgb") {
    return { r: channels[0] * 255, g: channels[1] * 255, b: channels[2] * 255, a };
  }
  const [red, green, blue] = channels.map(decodeGamma);
  // Display P3 linear to sRGB linear.
  return {
    r: encodeGamma(1.2249401762805 * red - 0.2249401762805 * green) * 255,
    g: encodeGamma(-0.042056961239 * red + 1.042056961239 * green) * 255,
    b: encodeGamma(-0.0196375547643 * red - 0.0786360655012 * green + 1.0982736202656 * blue) * 255,
    a,
  };
}

// A colour as VS Code writes one (#rgb, #rgba, #rrggbb, #rrggbbaa or color()), as `{ r, g, b, a }`; null otherwise.
function vsColour(value) {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (trimmed.startsWith("color(")) {
    return colorFunction(trimmed);
  }
  const hex = trimmed.replace(/^#/, "");
  if (!HEX.test(hex)) {
    return null;
  }
  const parsed = parseColour(`#${hex}`);
  return { ...parsed.rgb, a: parsed.alpha };
}

// An overlay laid over the surface it sits on, as `#rrggbb`.
function flattenOver(colour, base) {
  return toHex(colour.a >= 1 ? colour : mix(base, colour, colour.a));
}

function rgb(hex) {
  return parseColour(hex).rgb;
}

/** Whether `value` is a VS Code colour theme: workbench colours under dotted keys, or token colours, and no version 1. */
export function isVsCodeTheme(value) {
  if (!isRecord(value) || value.version === 1) {
    return false;
  }
  return (isRecord(value.colors) && Object.keys(value.colors).some((key) => key.includes("."))) ||
    Array.isArray(value.tokenColors);
}

// An extension's `name` is often a slug; read as words.
function humanized(raw) {
  const trimmed = raw.trim();
  if (/\s/.test(trimmed) || !/[-_.]/.test(trimmed)) {
    return trimmed;
  }
  return trimmed
    .split(/[-_.]+/)
    .filter((word) => word !== "")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function nameOf(value) {
  for (const candidate of [value.displayName, value.name]) {
    if (typeof candidate === "string" && humanized(candidate) !== "") {
      return humanized(candidate).slice(0, NAME_UNITS);
    }
  }
  return "VS Code theme";
}

/**
 * The theme file (version 1, without an id) of a VS Code colour theme; null when the theme has no editor background to
 * build on.
 */
export function vsCodeThemeFile(value) {
  const colours = isRecord(value.colors) ? value.colors : {};
  const pick = (...keys) => {
    for (const key of keys) {
      const parsed = vsColour(colours[key]);
      if (parsed !== null) {
        return parsed;
      }
    }
    return null;
  };
  const solidOver = (base, ...keys) => {
    const parsed = pick(...keys);
    return parsed === null ? null : flattenOver(parsed, base);
  };
  const canvasColour = pick("editor.background", "editorPane.background");
  if (canvasColour === null) {
    return null;
  }
  const canvas = { r: canvasColour.r, g: canvasColour.g, b: canvasColour.b };
  const type = typeof value.type === "string" ? value.type.toLowerCase() : null;
  const appearance =
    type === "light" || type === "hc-light"
      ? "light"
      : type === "dark" || type === "hc-black"
        ? "dark"
        : luminance(canvas) < DARK_LUMINANCE
          ? "dark"
          : "light";
  const canvasHex = toHex(canvas);
  const raisedCandidate = solidOver(canvas, "editorWidget.background", "dropdown.background");
  // An accent or a control has to stand apart from every surface beside it.
  const apart = (first, second) => contrast(rgb(first), rgb(second)) >= 1.1;
  const clearOfSurfaces = (hex) => apart(hex, canvasHex) && (raisedCandidate === null || apart(hex, raisedCandidate));
  let accentHex = null;
  let accentColour = null;
  for (const key of ACCENT_KEYS) {
    const candidate = vsColour(colours[key]);
    if (candidate !== null && clearOfSurfaces(flattenOver(candidate, canvas))) {
      accentColour = candidate;
      accentHex = flattenOver(candidate, canvas);
      break;
    }
  }
  if (accentHex === null) {
    const standard = PALETTES.clean[appearance].accent;
    accentHex = [standard, "#ffffff", "#000000"].find(clearOfSurfaces) ?? standard;
    accentColour = { ...rgb(accentHex), a: 1 };
  }
  // The floor is derived from the accent at a fifth of its strength, so that a grey theme with a blue focus line does
  // not come out blue all over.
  const derived = vividPalette(canvasHex, flattenOver({ ...accentColour, a: 0.2 }, canvas));
  const sidebarHex = solidOver(canvas, "sideBar.background", "activityBar.background") ?? derived.sidebar;
  const sidebar = rgb(sidebarHex);
  const terminalHex = solidOver(canvas, "terminal.background", "panel.background") ?? derived.terminalBackground;
  const terminal = rgb(terminalHex);
  // A foreground of the theme's wins where it reads on the surface it lands on; else the derived one where that does,
  // else the readable end of the greys.
  const readableOn = (surface, fallback, ...keys) => {
    const ground = rgb(surface);
    const reads = (candidate) => contrast(rgb(candidate), ground) >= 4.5;
    const specified = solidOver(ground, ...keys);
    if (specified !== null && reads(specified)) {
      return specified;
    }
    if (reads(fallback)) {
      return fallback;
    }
    return luminance(ground) < DARK_LUMINANCE ? "#ffffff" : "#000000";
  };
  const raisedHex = raisedCandidate ?? derived.surfaceRaised;
  const buttonHex = solidOver(canvas, "button.background");
  const actionHex = buttonHex !== null && apart(buttonHex, canvasHex) && apart(buttonHex, raisedHex) ? buttonHex : accentHex;
  const inputCandidates = [derived.input, derived.surfaceRaised, PALETTES.clean[appearance].input, "#000000", "#ffffff"];
  let inputHex = inputCandidates.find((candidate) => apart(candidate, canvasHex) && apart(candidate, actionHex)) ?? "#808080";
  for (const key of ["input.background", "input.border"]) {
    const candidate = solidOver(canvas, key);
    if (candidate !== null && apart(candidate, canvasHex) && apart(candidate, actionHex)) {
      inputHex = candidate;
      break;
    }
  }
  const searchMatch = solidOver(canvas, "editor.findMatchHighlightBackground") ?? derived.searchMatchBackground;
  const searchMatchActive = solidOver(canvas, "editor.findMatchBackground") ?? derived.searchMatchActiveBackground;
  const overrides = {
    canvas: canvasHex,
    text: readableOn(canvasHex, derived.text, "editor.foreground", "foreground"),
    textMuted: readableOn(canvasHex, derived.textMuted, "descriptionForeground", "disabledForeground"),
    surface: solidOver(canvas, "editorWidget.background") ?? derived.surface,
    surfaceRaised: raisedHex,
    surfaceOverlay:
      solidOver(canvas, "menu.background", "quickInput.background", "dropdown.background") ?? derived.surfaceOverlay,
    border: solidOver(canvas, "panel.border", "editorGroup.border", "contrastBorder") ?? derived.border,
    input: inputHex,
    placeholder: readableOn(raisedHex, derived.placeholder, "input.placeholderForeground"),
    error: readableOn(canvasHex, derived.error, "editorError.foreground", "errorForeground"),
    warning: readableOn(canvasHex, derived.warning, "editorWarning.foreground"),
    accentSurface:
      solidOver(canvas, "list.activeSelectionBackground", "list.hoverBackground") ?? derived.accentSurface,
    codeBackground: solidOver(canvas, "textCodeBlock.background") ?? derived.codeBackground,
    searchMatchBackground: searchMatch,
    searchMatchForeground: readableOn(
      searchMatch,
      derived.searchMatchForeground,
      "editor.findMatchHighlightForeground",
      "editor.foreground",
      "foreground",
    ),
    searchMatchActiveBackground: searchMatchActive,
    searchMatchActiveForeground: readableOn(
      searchMatchActive,
      derived.searchMatchActiveForeground,
      "editor.findMatchForeground",
      "editor.foreground",
      "foreground",
    ),
    sidebar: sidebarHex,
    sidebarForeground: readableOn(sidebarHex, derived.sidebarForeground, "sideBar.foreground"),
    sidebarBorder: solidOver(sidebar, "sideBar.border") ?? derived.sidebarBorder,
    sidebarRowHover: solidOver(sidebar, "list.hoverBackground") ?? derived.sidebarRowHover,
    sidebarRowActive:
      solidOver(sidebar, "list.inactiveSelectionBackground", "list.hoverBackground") ?? derived.sidebarRowActive,
    sidebarRowSelected: solidOver(sidebar, "list.activeSelectionBackground") ?? derived.sidebarRowSelected,
    terminalBackground: terminalHex,
    terminalForeground: readableOn(terminalHex, derived.terminalForeground, "terminal.foreground"),
    terminalCursor:
      solidOver(terminal, "terminalCursor.foreground", "editorCursor.foreground") ?? derived.terminalCursor,
    terminalSelection:
      solidOver(terminal, "terminal.selectionBackground", "editor.selectionBackground") ?? derived.terminalSelection,
    terminalScrollbar: solidOver(terminal, "scrollbarSlider.background") ?? derived.terminalScrollbar,
    accent: accentHex,
    focus: accentHex,
    messageAction: actionHex,
    messageActionForeground: readableOn(actionHex, derived.messageActionForeground, "button.foreground"),
    accentForeground: readableOn(accentHex, derived.accentForeground, "button.foreground"),
  };
  return { version: 1, name: nameOf(value), appearance, colors: { ...derived, ...overrides } };
}
