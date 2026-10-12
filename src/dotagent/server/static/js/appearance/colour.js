// The colour arithmetic of the themes (item 126), after T3 Code's palette engine (apps/web/src/themePalette.ts; see
// LICENSE): colours are read from the CSS forms the themes are written in, stepped and solved in OKLCH, mapped back
// into sRGB along their own lightness and hue, and measured by the WCAG contrast ratio that
// tests/unit/test_css_tokens.py measures too. An sRGB colour is `{ r, g, b }` with channels from 0 to 255; an OKLCH
// colour is `{ L, C, h }`.

const HEX = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const FUNCTION = /^(rgba?|oklch)\(\s*([^()]*)\)$/i;

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function number(text, percentOf) {
  const trimmed = text.trim();
  if (trimmed === "none") {
    return 0;
  }
  const value = Number.parseFloat(trimmed);
  if (!Number.isFinite(value) || !/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?(%|deg)?$/i.test(trimmed)) {
    return Number.NaN;
  }
  return trimmed.endsWith("%") ? (value / 100) * percentOf : value;
}

// The parts of a colour function: space- or comma-separated, the alpha after a slash or as a fourth part.
function parts(body) {
  const [main, slashed] = body.split("/");
  const values = main.includes(",") ? main.split(",") : main.trim().split(/\s+/);
  if (slashed !== undefined) {
    values.push(slashed);
  }
  return values.map((value) => value.trim()).filter((value) => value !== "");
}

/**
 * Read a colour written as hex (#rgb, #rgba, #rrggbb, #rrggbbaa), `rgb()`/`rgba()`, `oklch()` or `transparent`.
 * Returns `{ rgb, alpha }` with the colour mapped into sRGB, or null for anything else.
 */
export function parseColour(value) {
  if (typeof value !== "string") {
    return null;
  }
  const input = value.trim().toLowerCase();
  if (input === "transparent") {
    return { rgb: { r: 0, g: 0, b: 0 }, alpha: 0 };
  }
  const hex = HEX.exec(input);
  if (hex !== null) {
    const digits = hex[1].length <= 4 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
    const channel = (index) => Number.parseInt(digits.slice(index, index + 2), 16);
    return { rgb: { r: channel(0), g: channel(2), b: channel(4) }, alpha: digits.length === 8 ? channel(6) / 255 : 1 };
  }
  const call = FUNCTION.exec(input);
  if (call === null) {
    return null;
  }
  const values = parts(call[2]);
  if (values.length < 3 || values.length > 4) {
    return null;
  }
  const alpha = values.length === 4 ? number(values[3], 1) : 1;
  if (call[1] === "oklch") {
    const colour = { L: number(values[0], 1), C: number(values[1], 0.4), h: number(values[2], 1) };
    if (![colour.L, colour.C, colour.h, alpha].every(Number.isFinite)) {
      return null;
    }
    const rgb = oklchToRgb({ L: clamp(colour.L, 0, 1), C: Math.max(0, colour.C), h: colour.h });
    return { rgb, alpha: clamp(alpha, 0, 1) };
  }
  const [r, g, b] = values.slice(0, 3).map((part) => number(part, 255));
  if (![r, g, b, alpha].every(Number.isFinite)) {
    return null;
  }
  return { rgb: { r: clamp(r, 0, 255), g: clamp(g, 0, 255), b: clamp(b, 0, 255) }, alpha: clamp(alpha, 0, 1) };
}

/** The colour as `#rrggbb`. */
export function toHex({ r, g, b }) {
  return `#${[r, g, b].map((channel) => Math.round(clamp(channel, 0, 255)).toString(16).padStart(2, "0")).join("")}`;
}

function toLinear(channel) {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function fromLinear(channel) {
  const c = channel <= 0.0031308 ? channel * 12.92 : 1.055 * channel ** (1 / 2.4) - 0.055;
  return Math.round(clamp(c, 0, 1) * 255);
}

/** The colour in OKLCH (Björn Ottosson's matrices, as T3 Code has them). */
export function rgbToOklch(colour) {
  const r = toLinear(colour.r);
  const g = toLinear(colour.g);
  const b = toLinear(colour.b);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { L, C: Math.hypot(a, bb), h: (Math.atan2(bb, a) * 180) / Math.PI };
}

function oklchToLinear({ L, C, h }) {
  const hr = (h * Math.PI) / 180;
  const a = C * Math.cos(hr);
  const bb = C * Math.sin(hr);
  const l = (L + 0.3963377774 * a + 0.2158037573 * bb) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * bb) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * bb) ** 3;
  return {
    r: 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    g: -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    b: -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  };
}

// The greatest chroma at the colour's lightness and hue that sRGB can show.
function intoGamut(colour) {
  const fits = (C) => {
    const linear = oklchToLinear({ ...colour, C });
    return [linear.r, linear.g, linear.b].every((channel) => channel >= -0.0001 && channel <= 1.0001);
  };
  if (fits(colour.C)) {
    return colour;
  }
  let low = 0;
  let high = colour.C;
  for (let step = 0; step < 24; step += 1) {
    const middle = (low + high) / 2;
    if (fits(middle)) {
      low = middle;
    } else {
      high = middle;
    }
  }
  return { ...colour, C: low };
}

/** The OKLCH colour in sRGB, its chroma brought into the gamut first. */
export function oklchToRgb(colour) {
  const linear = oklchToLinear(intoGamut(colour));
  return { r: fromLinear(linear.r), g: fromLinear(linear.g), b: fromLinear(linear.b) };
}

/** `base` moved toward `overlay` by `amount` (0 to 1), channel by channel. */
export function mix(base, overlay, amount) {
  return {
    r: base.r + (overlay.r - base.r) * amount,
    g: base.g + (overlay.g - base.g) * amount,
    b: base.b + (overlay.b - base.b) * amount,
  };
}

/** The relative luminance of WCAG 2. */
export function luminance(colour) {
  const channel = (value) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b);
}

/** The WCAG contrast ratio of two colours, 1 to 21. */
export function contrast(one, other) {
  const lighter = Math.max(luminance(one), luminance(other));
  const darker = Math.min(luminance(one), luminance(other));
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * `base` with the lightness that reaches `least` contrast against `against`, moved `direction` ("lighter" or
 * "darker") as little as the search can tell; `base` itself when it already does.
 */
export function solveLightness(base, against, least, direction) {
  if (contrast(oklchToRgb(base), against) >= least) {
    return base;
  }
  let low = direction === "lighter" ? base.L : 0;
  let high = direction === "lighter" ? 1 : base.L;
  for (let step = 0; step < 18; step += 1) {
    const middle = (low + high) / 2;
    const reaches = contrast(oklchToRgb({ ...base, L: middle }), against) >= least;
    if (reaches === (direction === "lighter")) {
      high = middle;
    } else {
      low = middle;
    }
  }
  return { ...base, L: direction === "lighter" ? high : low };
}

/**
 * `colour` moved toward black or white, whichever lies away from `ground`, until it reads at `least` on it; itself
 * when it already does.
 */
export function readableOn(colour, ground, least) {
  const direction = luminance(colour) >= luminance(ground) ? "lighter" : "darker";
  const solved = oklchToRgb(solveLightness(rgbToOklch(colour), ground, least, direction));
  if (contrast(solved, ground) >= least) {
    return solved;
  }
  // The search stops a hair short of the ratio; the end of the line it searched always reaches it when anything does.
  return direction === "lighter" ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 };
}

const LIGHT_INK = { r: 255, g: 250, b: 255 };
const DARK_INK = { r: 36, g: 21, b: 35 };

/** The near-white or near-black (else white or black) that reads best on `ground`, as T3 Code picks it. */
export function inkFor(ground) {
  const light = contrast(ground, LIGHT_INK);
  const dark = contrast(ground, DARK_INK);
  if (Math.max(light, dark) >= 4.5) {
    return light >= dark ? LIGHT_INK : DARK_INK;
  }
  const white = { r: 255, g: 255, b: 255 };
  const black = { r: 0, g: 0, b: 0 };
  return contrast(ground, white) >= contrast(ground, black) ? white : black;
}
