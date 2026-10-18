// The palette generator (item 128), derived code (see LICENSE in this folder): every colour role of a palette from two
// colours, a canvas and an accent, worked out in OKLCH. The surfaces step away from the canvas in lightness, tinted
// with the accent's hue at a low chroma; the text is solved to 7:1 on the canvas and the muted text to the strength of
// the stock palettes; a companion action colour is turned 50° off the accent; every foreground is solved on its own
// surface, and the error and warning colours are the standard ones laid over the canvas. A family of roles moves
// together when the colour that stands for it is changed (`withFamily`), for the families Dot draws with. Colours come
// out as `#rrggbb`, as a theme file holds them.

import { contrast, inkFor, luminance, mix, oklchToRgb, parseColour, rgbToOklch, solveLightness, toHex } from "./colour.js";

/** The colour roles of a palette; dotagent/themes.py names the same. */
export const THEME_ROLES = [
  "canvas",
  "chrome",
  "toolbar",
  "toolbarForeground",
  "toolbarBorder",
  "toolbarControl",
  "toolbarControlForeground",
  "toolbarControlHover",
  "surface",
  "surfaceRaised",
  "surfaceOverlay",
  "text",
  "textMuted",
  "border",
  "input",
  "focus",
  "accent",
  "accentForeground",
  "secondary",
  "secondaryForeground",
  "muted",
  "mutedForeground",
  "placeholder",
  "secondaryLabel",
  "iconMuted",
  "error",
  "errorForeground",
  "errorSurface",
  "warning",
  "warningForeground",
  "warningSurface",
  "update",
  "updateForeground",
  "updateSurface",
  "accentSurface",
  "accentSurfaceForeground",
  "messageSurface",
  "messageForeground",
  "messageAction",
  "messageActionForeground",
  "messageActionHover",
  "codeBackground",
  "codeForeground",
  "searchMatchBackground",
  "searchMatchForeground",
  "searchMatchActiveBackground",
  "searchMatchActiveForeground",
  "sidebar",
  "sidebarForeground",
  "sidebarMutedForeground",
  "sidebarControlSurface",
  "sidebarRowHover",
  "sidebarRowActive",
  "sidebarRowSelected",
  "sidebarBorder",
  "terminalBackground",
  "terminalForeground",
  "terminalCursor",
  "terminalSelection",
  "terminalScrollbar",
  "terminalScrollbarHover",
];

// The luminance under which a canvas is dark: where white and black text have the same headroom.
const DARK_LUMINANCE = 0.179;
// A foreground on its surface: 4.5:1 with a little headroom for the browser's rounding.
const ON_SURFACE = 4.6;
// The muted text of the stock palettes: zinc-500 on a light canvas, #818181 on a dark one.
const MUTED_LIGHT = 4.705;
const MUTED_DARK = 5.082;
// The status colours of Clean (red-500 and amber-500), which a generated palette keeps.
const STANDARD_STATUS = {
  light: { error: "#fb2c36", errorForeground: "#c10007", warning: "#fe9a00", warningForeground: "#bb4d00" },
  dark: { error: "#fb414a", errorForeground: "#ff6467", warning: "#fe9a00", warningForeground: "#ffb900" },
};
const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };

function rgbOf(value) {
  const parsed = parseColour(value);
  if (parsed === null) {
    throw new Error(`not a colour: ${value}`);
  }
  return parsed.rgb;
}

function isDark(canvas) {
  return luminance(canvas) < DARK_LUMINANCE;
}

// `colour` with the lightness that reads at `least` on `ground`: lighter for a dark ground, darker for a light one.
function toneOn(colour, ground, least, dark) {
  return oklchToRgb(solveLightness(rgbToOklch(colour), ground, least, dark ? "lighter" : "darker"));
}

// The text of `foreground` on `background`: mixed toward `background` by `amount`, or, where that reads under `least`,
// the quietest mix between the two that still reads at it.
function readableText(background, foreground, amount, least) {
  const softened = mix(foreground, background, amount);
  if (contrast(softened, background) >= least) {
    return softened;
  }
  let readable = foreground;
  let lower = 0;
  let upper = amount;
  for (let step = 0; step < 12; step += 1) {
    const middle = (lower + upper) / 2;
    const candidate = mix(foreground, background, middle);
    if (contrast(candidate, background) >= least) {
      readable = candidate;
      lower = middle;
    } else {
      upper = middle;
    }
  }
  return readable;
}

function mutedText(background, foreground) {
  return readableText(background, foreground, 1, isDark(background) ? MUTED_DARK : MUTED_LIGHT);
}

// The standard error and warning over a canvas: the surface the standard colour laid over it (8% in light, 16% in
// dark), the foreground the standard one solved on that surface.
function statusColours(canvas) {
  const dark = isDark(canvas);
  const standard = STANDARD_STATUS[dark ? "dark" : "light"];
  const surfaceOf = (value) => mix(canvas, rgbOf(value), dark ? 0.16 : 0.08);
  const errorSurface = surfaceOf(standard.error);
  const warningSurface = surfaceOf(standard.warning);
  return {
    error: rgbOf(standard.error),
    errorForeground: toneOn(rgbOf(standard.errorForeground), errorSurface, ON_SURFACE, dark),
    errorSurface,
    warning: rgbOf(standard.warning),
    warningForeground: toneOn(rgbOf(standard.warningForeground), warningSurface, ON_SURFACE, dark),
    warningSurface,
  };
}

function hexes(colours) {
  return Object.fromEntries(Object.entries(colours).map(([role, colour]) => [role, toHex(colour)]));
}

/** Every role of a palette from its canvas and its accent (CSS colours). */
export function vividPalette(canvasValue, accentValue) {
  const canvasRgb = rgbOf(canvasValue);
  const accentRgb = rgbOf(accentValue);
  const canvas = rgbToOklch(canvasRgb);
  const accent = rgbToOklch(accentRgb);
  // The ramp follows the canvas picked, not the scheme the palette is for.
  const dark = isDark(canvasRgb);
  const hue = accent.C < 0.02 ? canvas.h : accent.h;
  const tint = Math.min(0.045, Math.max(0.008, accent.C * 0.22));
  const away = dark ? 1 : -1;
  const at = (lightness, chroma) =>
    oklchToRgb({ L: Math.min(0.98, Math.max(0.05, canvas.L + away * lightness)), C: chroma, h: hue });
  // The text carries a whisper of the accent's hue and reads at 7:1 on the canvas.
  const textBase = { L: dark ? 0.95 : 0.2, C: Math.min(0.035, accent.C * 0.25), h: hue };
  const text = oklchToRgb(solveLightness(textBase, canvasRgb, 7, dark ? "lighter" : "darker"));
  const textMuted = mutedText(canvasRgb, text);
  const foregroundOn = (surface) =>
    oklchToRgb(solveLightness(textBase, surface, ON_SURFACE, dark ? "lighter" : "darker"));
  const action = {
    L: Math.min(0.85, Math.max(0.35, accent.L + (dark ? 0.06 : -0.02))),
    C: Math.max(accent.C * 0.9, 0.06),
    h: (hue + 50) % 360,
  };
  const actionRgb = oklchToRgb(action);
  const sidebar = at(0.045, tint * 1.4);
  const surfaceRaised = at(0.05, tint);
  const secondary = at(dark ? 0.1 : 0.06, Math.min(0.09, accent.C * 0.5));
  const muted = at(dark ? 0.06 : 0.04, Math.min(0.06, accent.C * 0.35));
  const accentSurface = at(dark ? 0.13 : 0.08, Math.min(0.11, accent.C * 0.55));
  const messageSurface = at(dark ? 0.16 : 0.1, Math.min(0.13, accent.C * 0.6));
  const updateSurface = at(dark ? 0.14 : 0.09, Math.min(0.12, accent.C * 0.55));
  const searchMatch = at(0.22, Math.min(0.12, accent.C));
  const searchMatchActive = at(0.4, Math.min(0.18, accent.C));
  return hexes({
    ...statusColours(canvasRgb),
    canvas: canvasRgb,
    chrome: canvasRgb,
    toolbar: canvasRgb,
    toolbarForeground: text,
    toolbarBorder: at(dark ? 0.14 : 0.1, Math.min(0.08, accent.C * 0.4)),
    toolbarControl: at(dark ? 0.09 : 0.05, tint * 1.3),
    toolbarControlForeground: text,
    toolbarControlHover: at(dark ? 0.14 : 0.09, tint * 1.6),
    surface: at(0.015, tint),
    surfaceRaised,
    surfaceOverlay: at(0.075, tint),
    text,
    textMuted,
    border: at(dark ? 0.16 : 0.12, Math.min(0.07, accent.C * 0.35)),
    input: at(dark ? 0.21 : 0.16, Math.min(0.08, accent.C * 0.4)),
    focus: accentRgb,
    accent: accentRgb,
    accentForeground: inkFor(accentRgb),
    secondary,
    secondaryForeground: foregroundOn(secondary),
    muted,
    mutedForeground: readableText(muted, text, 1, ON_SURFACE),
    placeholder: readableText(surfaceRaised, text, 1, ON_SURFACE),
    secondaryLabel: textMuted,
    iconMuted: textMuted,
    update: accentRgb,
    updateForeground: foregroundOn(updateSurface),
    updateSurface,
    accentSurface,
    accentSurfaceForeground: foregroundOn(accentSurface),
    messageSurface,
    messageForeground: foregroundOn(messageSurface),
    messageAction: actionRgb,
    messageActionForeground: inkFor(actionRgb),
    messageActionHover: oklchToRgb({ ...action, L: action.L + (dark ? 0.06 : -0.06) }),
    codeBackground: at(0.035, tint * 0.8),
    codeForeground: text,
    searchMatchBackground: searchMatch,
    searchMatchForeground: inkFor(searchMatch),
    searchMatchActiveBackground: searchMatchActive,
    searchMatchActiveForeground: inkFor(searchMatchActive),
    sidebar,
    sidebarForeground: foregroundOn(sidebar),
    sidebarMutedForeground: mutedText(sidebar, text),
    sidebarControlSurface: at(dark ? 0.1 : 0.07, tint * 1.5),
    sidebarRowHover: at(dark ? 0.08 : 0.06, Math.min(0.08, accent.C * 0.45)),
    sidebarRowActive: at(dark ? 0.12 : 0.09, Math.min(0.1, accent.C * 0.55)),
    sidebarRowSelected: at(dark ? 0.14 : 0.1, Math.min(0.11, accent.C * 0.6)),
    sidebarBorder: at(dark ? 0.17 : 0.12, Math.min(0.08, accent.C * 0.4)),
    terminalBackground: canvasRgb,
    terminalForeground: text,
    terminalCursor: accentRgb,
    terminalSelection: at(dark ? 0.18 : 0.12, Math.min(0.12, accent.C * 0.55)),
    terminalScrollbar: at(dark ? 0.22 : 0.16, tint),
    terminalScrollbarHover: at(dark ? 0.3 : 0.22, tint),
  });
}

/**
 * The families of colours the editor shows, each by the role that stands for it, for the roles Dot draws with: the
 * page, the sidebar and the surfaces, the lines, the text, the accent and the action, the owner's message, and the
 * error and the warning.
 */
export const FAMILIES = [
  { role: "canvas", label: "Background" },
  { role: "sidebar", label: "Sidebar" },
  { role: "surface", label: "Surface" },
  { role: "accentSurface", label: "Highlight surface" },
  { role: "border", label: "Border" },
  { role: "input", label: "Input border" },
  { role: "text", label: "Text" },
  { role: "textMuted", label: "Muted text" },
  { role: "accent", label: "Accent" },
  { role: "messageAction", label: "Action" },
  { role: "messageSurface", label: "Message surface" },
  { role: "error", label: "Error" },
  { role: "warning", label: "Warning" },
];

/**
 * A copy of `palette` with the family `role` stands for set to `value` (an opaque colour), the roles that go with it
 * worked out again on the palette's canvas and every other role as it was.
 */
export function withFamily(palette, role, value) {
  const selected = rgbOf(value);
  const colour = toHex(selected);
  const canvas = rgbOf(palette.canvas);
  const dark = isDark(canvas);
  const status = () => {
    const surface = mix(canvas, selected, dark ? 0.16 : 0.08);
    return { foreground: toHex(toneOn(selected, surface, ON_SURFACE, isDark(surface))), surface: toHex(surface) };
  };
  switch (role) {
    case "canvas":
      return { ...palette, canvas: colour, chrome: colour, toolbar: colour };
    case "surface":
    case "input":
      return { ...palette, [role]: colour };
    case "text":
      return { ...palette, text: colour, toolbarForeground: colour, toolbarControlForeground: colour };
    case "textMuted":
      return {
        ...palette,
        textMuted: colour,
        mutedForeground: colour,
        placeholder: colour,
        secondaryLabel: colour,
        iconMuted: colour,
        sidebarMutedForeground: colour,
      };
    case "border":
      return { ...palette, border: colour, toolbarBorder: colour, sidebarBorder: colour };
    case "accentSurface":
      return {
        ...palette,
        accentSurface: colour,
        accentSurfaceForeground: toHex(inkFor(selected)),
        toolbarControlHover: colour,
      };
    case "accent": {
      const updateSurface = mix(canvas, selected, dark ? 0.32 : 0.16);
      return {
        ...palette,
        accent: colour,
        accentForeground: toHex(inkFor(selected)),
        focus: colour,
        update: colour,
        updateForeground: toHex(toneOn(selected, updateSurface, ON_SURFACE, isDark(updateSurface))),
        updateSurface: toHex(updateSurface),
        terminalCursor: colour,
      };
    }
    case "messageAction": {
      const ink = inkFor(selected);
      const opposite = luminance(ink) > luminance(selected) ? BLACK : WHITE;
      return {
        ...palette,
        messageAction: colour,
        messageActionForeground: toHex(ink),
        messageActionHover: toHex(mix(selected, opposite, 0.12)),
      };
    }
    case "messageSurface":
      return { ...palette, messageSurface: colour, messageForeground: toHex(inkFor(selected)) };
    case "sidebar":
      return { ...palette, sidebar: colour, sidebarForeground: toHex(inkFor(selected)) };
    case "error": {
      const { foreground, surface } = status();
      return { ...palette, error: colour, errorForeground: foreground, errorSurface: surface };
    }
    case "warning": {
      const { foreground, surface } = status();
      return { ...palette, warning: colour, warningForeground: foreground, warningSurface: surface };
    }
    default:
      throw new Error(`no family of colours stands on the role ${role}`);
  }
}
