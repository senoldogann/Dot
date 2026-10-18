// Dot's colour tokens from a theme (item 126). A look (Neutral, Paper) is a block of themes.css and is read from the
// stylesheet itself, so that file stays the one place its values live; a palette (palettes.js) is a set of roles,
// mapped here onto Dot's tokens and held to the rule tests/unit/test_css_tokens.py holds the looks to: every text tier
// reads on every surface (4.5:1 for the text and its second tier, 3:1 for the muted and the faint ones) and every ink
// on its ground at 4.5:1, solved where the palette falls short by moving the lightness away from the ground. The status
// colours keep their standard hues and are solved on each canvas.

import { contrast, inkFor, luminance, mix, oklchToRgb, parseColour, readableOn, rgbToOklch, toHex } from "./colour.js";

/** The colour tokens a theme sets, as themes.css declares them in each look. */
export const THEME_TOKENS = [
  "--bg",
  "--sidebar",
  "--surface",
  "--surface-2",
  "--surface-3",
  "--border",
  "--border-strong",
  "--control",
  "--text",
  "--text-2",
  "--muted",
  "--faint",
  "--accent",
  "--accent-strong",
  "--accent-weak",
  "--on-accent",
  "--ok",
  "--ok-weak",
  "--on-ok",
  "--warn",
  "--warn-weak",
  "--warn-border",
  "--bad",
  "--bad-weak",
  "--bad-border",
  "--on-bad",
  "--bubble",
  "--on-bubble",
];

const LOOK_SELECTORS = { neutral: ":root", paper: ':root[data-theme="paper"]' };
// A little over the rule, so that the browser's own rounding never lands a pair under it.
const HEADROOM = 0.1;
const LEAST_TEXT = 4.5 + HEADROOM;
const LEAST_QUIET = 3 + HEADROOM;
// The luminance under which a canvas is dark: where white and black text have the same headroom.
const DARK_LUMINANCE = 0.179;
// One step between two of Dot's surfaces in OKLCH lightness, about the steps of its own looks in dark.
const SURFACE_STEP = 0.035;
// Success has no role in the palettes; its standard is emerald.
const SUCCESS = {
  light: { signal: "#10b981", ink: "#047857" },
  dark: { signal: "#10b981", ink: "#34d399" },
};

// The stylesheet's rule of each look, found once: the rule whose selector names the look and which declares --bg.
let lookStyles = null;

function stylesOfLooks() {
  if (lookStyles !== null) {
    return lookStyles;
  }
  lookStyles = new Map();
  for (const sheet of document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (!(rule instanceof CSSStyleRule) || rule.style.getPropertyValue("--bg") === "") {
        continue;
      }
      for (const [look, selector] of Object.entries(LOOK_SELECTORS)) {
        if (rule.selectorText === selector) {
          lookStyles.set(look, rule.style);
        }
      }
    }
  }
  return lookStyles;
}

// The two arguments of `light-dark(a, b)`, split at its one comma outside parentheses; null for any other value.
function lightDark(value) {
  const match = /^light-dark\((.*)\)$/s.exec(value);
  if (match === null) {
    return null;
  }
  let depth = 0;
  for (let index = 0; index < match[1].length; index += 1) {
    const char = match[1][index];
    depth += char === "(" ? 1 : char === ")" ? -1 : 0;
    if (char === "," && depth === 0) {
      return [match[1].slice(0, index).trim(), match[1].slice(index + 1).trim()];
    }
  }
  throw new Error(`light-dark() without two arguments: ${value}`);
}

function lookValue(style, token, scheme) {
  const value = style.getPropertyValue(token).trim();
  if (value === "") {
    throw new Error(`the look declares no ${token}`);
  }
  const reference = /^var\((--[a-z0-9-]+)\)$/.exec(value);
  if (reference !== null) {
    return lookValue(style, reference[1], scheme);
  }
  const sides = lightDark(value);
  return sides === null ? value : sides[scheme === "light" ? 0 : 1];
}

/** The tokens of a look of themes.css for one scheme, as the stylesheet writes them. */
export function lookTokens(look, scheme) {
  const style = stylesOfLooks().get(look);
  if (style === undefined) {
    throw new Error(`no rule in the stylesheets for the look ${look}`);
  }
  return Object.fromEntries(THEME_TOKENS.map((token) => [token, lookValue(style, token, scheme)]));
}

function rgbOf(roles, role) {
  const parsed = parseColour(roles[role]);
  if (parsed === null) {
    throw new Error(`the palette's ${role} is not a colour: ${roles[role]}`);
  }
  return parsed.rgb;
}

// The surface of `grounds` a colour reads worst on: the nearest in luminance.
function worstGround(colour, grounds) {
  return grounds.reduce((worst, ground) => (contrast(colour, ground) < contrast(colour, worst) ? ground : worst));
}

// A text tier that reads at `least` on every one of `grounds`: solved against the one it reads worst on, away from it.
function tier(colour, grounds, least) {
  const solved = readableOn(colour, worstGround(colour, grounds), least);
  return grounds.every((ground) => contrast(solved, ground) >= least - HEADROOM)
    ? solved
    : readableOn(solved, worstGround(solved, grounds), least);
}

// `colour` moved in OKLCH lightness by `step` (negative is darker).
function stepped(colour, step) {
  const oklch = rgbToOklch(colour);
  return oklchToRgb({ ...oklch, L: Math.min(1, Math.max(0, oklch.L + step)) });
}

// The surfaces Dot steps through (the card, the hover, the pressed), each a step further from the page than the last:
// down in light, up in dark. The accent surface is the hover when it lies that way, else a step from the card.
function surfaceSteps(surface, hover, border, dark) {
  const further = (one, other) => (dark ? luminance(other) > luminance(one) : luminance(other) < luminance(one));
  const step = dark ? SURFACE_STEP : -SURFACE_STEP;
  const second = further(surface, hover) ? hover : stepped(surface, step);
  const third = mix(second, border, 0.5);
  return [second, further(second, third) ? third : stepped(second, step)];
}

// A status colour on its tinted ground: the ground is the canvas mixed toward the signal, the ink the standard ink
// solved on that ground.
function status(canvas, signal, ink, dark) {
  const weak = mix(canvas, signal, dark ? 0.16 : 0.08);
  return { ink: readableOn(ink, weak, LEAST_TEXT), weak, border: mix(weak, signal, 0.35) };
}

/** The tokens of a palette's roles for one scheme, as `#rrggbb`, held to Dot's contrast rule. */
export function paletteTokens(roles) {
  const canvas = rgbOf(roles, "canvas");
  const dark = luminance(canvas) < DARK_LUMINANCE;
  const sidebar = rgbOf(roles, "sidebar");
  // In dark a card lies a step above the page, as in Dot's looks; a palette may draw its cards on the page.
  const card = rgbOf(roles, "surface");
  const surface = dark && luminance(card) <= luminance(canvas) ? stepped(canvas, SURFACE_STEP) : card;
  const border = rgbOf(roles, "border");
  const [surface2, surface3] = surfaceSteps(surface, rgbOf(roles, "accentSurface"), border, dark);
  const grounds = [canvas, sidebar, surface, surface2, surface3];
  const text = tier(rgbOf(roles, "text"), grounds, LEAST_TEXT);
  const muted = tier(rgbOf(roles, "textMuted"), grounds, LEAST_QUIET);
  // Dot's accent is the palette's action colour, the one it paints every solid control with (buttons, switches, send).
  const accent = rgbOf(roles, "messageAction");
  const bubble = rgbOf(roles, "messageSurface");
  const bad = { ink: rgbOf(roles, "errorForeground"), weak: rgbOf(roles, "errorSurface") };
  const warn = { ink: rgbOf(roles, "warningForeground"), weak: rgbOf(roles, "warningSurface") };
  const success = SUCCESS[dark ? "dark" : "light"];
  const ok = status(canvas, parseColour(success.signal).rgb, parseColour(success.ink).rgb, dark);
  const badInk = readableOn(bad.ink, bad.weak, LEAST_TEXT);
  const tokens = {
    "--bg": canvas,
    "--sidebar": sidebar,
    "--surface": surface,
    "--surface-2": surface2,
    "--surface-3": surface3,
    "--border": border,
    "--border-strong": rgbOf(roles, "input"),
    "--control": tier(rgbOf(roles, "iconMuted"), grounds, LEAST_QUIET),
    "--text": text,
    "--text-2": tier(mix(text, muted, 0.45), grounds, LEAST_TEXT),
    "--muted": muted,
    "--faint": tier(mix(muted, canvas, 0.15), grounds, LEAST_QUIET),
    "--accent": accent,
    "--accent-strong": rgbOf(roles, "messageActionHover"),
    "--accent-weak": rgbOf(roles, "updateSurface"),
    "--on-accent": readableOn(rgbOf(roles, "messageActionForeground"), accent, LEAST_TEXT),
    "--ok": ok.ink,
    "--ok-weak": ok.weak,
    "--on-ok": inkFor(ok.ink),
    "--warn": readableOn(warn.ink, warn.weak, LEAST_TEXT),
    "--warn-weak": warn.weak,
    "--warn-border": mix(warn.weak, rgbOf(roles, "warning"), 0.35),
    "--bad": badInk,
    "--bad-weak": bad.weak,
    "--bad-border": mix(bad.weak, rgbOf(roles, "error"), 0.35),
    "--on-bad": readableOn(inkFor(badInk), badInk, LEAST_TEXT),
    "--bubble": bubble,
    "--on-bubble": readableOn(rgbOf(roles, "messageForeground"), bubble, LEAST_TEXT),
  };
  return Object.fromEntries(Object.entries(tokens).map(([token, colour]) => [token, toHex(colour)]));
}

/**
 * The tokens to write on the page element for `light` picked for the light scheme and `dark` for the dark one (each
 * `{ tokens(scheme) }`), as `light-dark()` pairs; none when one look is picked for both, which its stylesheet block
 * already draws.
 */
export function pairedTokens(light, dark) {
  if (light === dark && light.look) {
    return {};
  }
  const lightTokens = light.tokens("light");
  const darkTokens = dark.tokens("dark");
  return Object.fromEntries(
    THEME_TOKENS.map((token) => [token, `light-dark(${lightTokens[token]}, ${darkTokens[token]})`]),
  );
}
