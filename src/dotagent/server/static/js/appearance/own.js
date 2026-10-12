// The themes of one's own (item 128): the files the daemon keeps in its home's `themes` folder (GET, PUT and DELETE
// /api/themes, dotagent/themes.py), each T3 Code's theme file (version 1) or the short form T3 Code publishes. A theme
// has a palette of T3 Code's roles for its own scheme and, under `variants`, for the other; a role a palette leaves out
// is the one of T3 Code's default palette for that scheme (Dot's Rose), a palette with a canvas and an accent is laid
// over the one the generator derives from them, and a translucent colour is drawn over the canvas. tokens.js maps the
// roles to Dot's tokens and holds them to Dot's rule, as it holds the built-in palettes. A file is checked here as the
// daemon checks it, whether it comes from the daemon or from a file the owner adds: T3 Code's theme file, its short
// form, or a VS Code colour theme (vscode.js).

import { api } from "../api.js";
import { THEMES, findTheme, forgetTheme, setOwnThemes } from "../theme.js";
import { mix, parseColour, toHex } from "./colour.js";
import { THEME_ROLES, vividPalette } from "./generator.js";
import { PALETTES } from "./palettes.js";
import { lookTokens, paletteTokens } from "./tokens.js";
import { isVsCodeTheme, vsCodeThemeFile } from "./vscode.js";

/** The schemes a theme has a palette for, in order. */
export const SCHEMES_OF_THEMES = ["light", "dark"];
const THEME_ID = /^[a-z0-9](?:[a-z0-9-]{0,47})$/;
const ID_UNITS = 48;
const NAME_UNITS = 48;
const COLOUR_CHARS = 64;
const SEED = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
const COLLECTION_ID = /^[a-z0-9][a-z0-9.:-]{0,127}$/i;
const FILE_KEYS = ["version", "id", "name", "appearance", "canvas", "accent", "colors", "variants", "collection", "managed"];
const ROLES = new Set(THEME_ROLES);
// The ids T3 Code keeps from any theme file, beside Dot's own themes (dotagent/themes.py RESERVED_THEME_IDS).
const T3_RESERVED = ["system", "light", "dark", "t3-chat", "t3-code", "t3-chat-dark", "t3-grove", "t3-ocean", "t3-ember", "t3-iris"];

/** What is wrong with a theme file, said for the owner. */
export class ThemeFileError extends Error {}

// The folder the daemon keeps the themes in, and the files of it it left out, as the last read found them.
let folder = { path: "", skipped: [] };

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkedName(value) {
  if (typeof value !== "string" || value.trim() === "" || value.trim().length > NAME_UNITS) {
    throw new ThemeFileError(`A theme needs a name of 1 to ${NAME_UNITS} characters.`);
  }
  return value.trim();
}

function checkedPalette(value, where) {
  if (!isRecord(value)) {
    throw new ThemeFileError(`The theme's ${where} are not a set of colour roles.`);
  }
  const palette = {};
  for (const [role, colour] of Object.entries(value)) {
    if (!ROLES.has(role)) {
      throw new ThemeFileError(`"${role}" is not a colour role of a theme.`);
    }
    if (typeof colour !== "string" || colour.trim().length > COLOUR_CHARS || parseColour(colour.trim()) === null) {
      throw new ThemeFileError(`The colour of "${role}" is not one Dot reads: hex, rgb() or oklch().`);
    }
    palette[role] = colour.trim();
  }
  return palette;
}

function checkedVariants(value, appearance) {
  if (!isRecord(value)) {
    throw new ThemeFileError("The theme's variants are not a set of palettes.");
  }
  const variants = {};
  for (const [scheme, palette] of Object.entries(value)) {
    if (!SCHEMES_OF_THEMES.includes(scheme)) {
      throw new ThemeFileError('A theme\'s variants are named "light" or "dark".');
    }
    if (scheme === appearance) {
      throw new ThemeFileError(`The variants repeat the theme's own appearance, ${scheme}.`);
    }
    variants[scheme] = checkedPalette(palette, `${scheme} colours`);
  }
  return variants;
}

/**
 * The theme of a file's JSON value: T3 Code's theme file (version 1) or its short form, checked as the daemon checks
 * it. Throws ThemeFileError with what is wrong.
 */
export function themeFileOf(value) {
  if (!isRecord(value)) {
    throw new ThemeFileError("A theme file holds one JSON object.");
  }
  const stranger = Object.keys(value).find((key) => !FILE_KEYS.includes(key));
  if (stranger !== undefined) {
    throw new ThemeFileError(`A theme file has no "${stranger}".`);
  }
  if (value.version !== undefined && value.version !== 1) {
    throw new ThemeFileError("The theme file is of a version Dot does not read; it reads version 1.");
  }
  const name = checkedName(value.name);
  if (value.appearance !== "light" && value.appearance !== "dark") {
    throw new ThemeFileError('A theme\'s appearance is "light" or "dark".');
  }
  if (value.id !== undefined && (typeof value.id !== "string" || !THEME_ID.test(value.id))) {
    throw new ThemeFileError("A theme's id is lowercase letters, digits and dashes.");
  }
  const seeded = value.canvas !== undefined || value.accent !== undefined;
  if (seeded && !(SEED.test(String(value.canvas)) && SEED.test(String(value.accent)))) {
    throw new ThemeFileError("A theme derived from two colours names both, the canvas and the accent, in hex.");
  }
  const colors = value.colors === undefined ? undefined : checkedPalette(value.colors, "colours");
  if (!seeded && (colors === undefined || Object.keys(colors).length === 0)) {
    throw new ThemeFileError("A theme names its colours, or a canvas and an accent to derive them from.");
  }
  const collection = value.collection;
  if (
    collection !== undefined &&
    !(isRecord(collection) && COLLECTION_ID.test(String(collection.id)) && typeof collection.label === "string")
  ) {
    throw new ThemeFileError("The theme's collection needs an id and a label.");
  }
  if (value.managed !== undefined && typeof value.managed !== "boolean") {
    throw new ThemeFileError("The theme's managed flag is true or false.");
  }
  const file = { name, appearance: value.appearance };
  for (const [key, kept] of Object.entries({
    version: value.version,
    id: value.id,
    canvas: value.canvas,
    accent: value.accent,
    colors,
    variants: value.variants === undefined ? undefined : checkedVariants(value.variants, value.appearance),
    collection: collection === undefined ? undefined : { id: collection.id, label: checkedName(collection.label) },
    managed: value.managed,
  })) {
    if (kept !== undefined) {
      file[key] = kept;
    }
  }
  return file;
}

/** The schemes `file` has a palette for: its own, and the other when its variants hold one. */
export function schemesOf(file) {
  return SCHEMES_OF_THEMES.filter((scheme) => scheme === file.appearance || file.variants?.[scheme] !== undefined);
}

// The roles with every colour as `#rrggbb`, a translucent one laid over the canvas, as the page shows it there.
function opaque(roles) {
  const canvas = parseColour(roles.canvas).rgb;
  return Object.fromEntries(
    Object.entries(roles).map(([role, value]) => {
      const { rgb, alpha } = parseColour(value);
      return [role, toHex(alpha < 1 ? mix(canvas, rgb, alpha) : rgb)];
    }),
  );
}

/** The roles of `file`'s palette for `scheme`, every role Dot reads among them, as `#rrggbb`; null when it has none. */
export function rolesOf(file, scheme) {
  if (scheme === file.appearance) {
    const derived = file.canvas === undefined ? {} : vividPalette(file.canvas, file.accent);
    return opaque({ ...PALETTES.rose[scheme], ...derived, ...file.colors });
  }
  const variant = file.variants?.[scheme];
  return variant === undefined ? null : opaque({ ...PALETTES.rose[scheme], ...variant });
}

// The roles of a look of themes.css read back from its tokens, so that a look can be copied into the editor.
function lookRoles(tokens) {
  return opaque({
    canvas: tokens["--bg"],
    sidebar: tokens["--sidebar"],
    surface: tokens["--surface"],
    accentSurface: tokens["--surface-2"],
    border: tokens["--border"],
    input: tokens["--border-strong"],
    text: tokens["--text"],
    textMuted: tokens["--muted"],
    placeholder: tokens["--muted"],
    iconMuted: tokens["--control"],
    accent: tokens["--accent"],
    messageAction: tokens["--accent"],
    messageActionHover: tokens["--accent-strong"],
    messageActionForeground: tokens["--on-accent"],
    updateSurface: tokens["--accent-weak"],
    messageSurface: tokens["--bubble"],
    messageForeground: tokens["--on-bubble"],
    error: tokens["--bad"],
    errorForeground: tokens["--bad"],
    errorSurface: tokens["--bad-weak"],
    warning: tokens["--warn"],
    warningForeground: tokens["--warn"],
    warningSurface: tokens["--warn-weak"],
  });
}

/** The roles of any theme for `scheme`, to copy into the editor: a look's read back, a palette's, an own theme's. */
export function copiedRoles(theme, scheme) {
  if (theme.own) {
    return rolesOf(theme.file, scheme);
  }
  return theme.look ? lookRoles(lookTokens(theme.id, scheme)) : opaque(PALETTES[theme.id][scheme]);
}

// A theme of one's own as the gallery and the page take a theme (theme.js).
function described(id, file) {
  const schemes = schemesOf(file);
  const roles = (scheme) => {
    const found = rolesOf(file, scheme);
    if (found === null) {
      throw new Error(`the theme ${id} has no palette for ${scheme}`);
    }
    return found;
  };
  return {
    id,
    label: file.name,
    hint: schemes.length === 2 ? "Your own, for light and dark." : `Your own, for ${schemes[0]} only.`,
    look: false,
    own: true,
    foldSteps: true,
    inspector: false,
    schemes,
    file,
    tokens: (scheme) => paletteTokens(roles(scheme)),
    orb: (scheme) => {
      const found = roles(scheme);
      return { canvas: found.canvas, accent: found.accent, action: found.messageAction };
    },
  };
}

function checkedFolder(value) {
  if (!isRecord(value) || typeof value.folder !== "string" || !Array.isArray(value.themes) || !Array.isArray(value.skipped)) {
    throw new Error("the daemon's list of themes is not one this page reads");
  }
  const themes = value.themes.map((entry) => {
    if (!isRecord(entry) || typeof entry.id !== "string" || !THEME_ID.test(entry.id)) {
      throw new Error("the daemon listed a theme without an id this page reads");
    }
    return described(entry.id, themeFileOf(entry.file));
  });
  const skipped = value.skipped.map((entry) => ({ file: String(entry.file), reason: String(entry.reason) }));
  return { path: value.folder, themes, skipped };
}

/** Read the themes of one's own from the daemon and hand them to the gallery and the page. */
export async function loadOwnThemes() {
  const read = checkedFolder(await api.get("/themes"));
  folder = { path: read.path, skipped: read.skipped };
  setOwnThemes(read.themes);
}

/** The folder the themes are kept in and the files of it the daemon left out, `{ path, skipped: [{ file, reason }] }`. */
export function themeFolder() {
  return folder;
}

/** Keep `file` as the theme `id`, new or over the one of that id, and read the themes again. */
export async function saveOwnTheme(id, file) {
  await api.put(`/themes/${id}`, file);
  await loadOwnThemes();
}

/** Delete the theme `id`, let go of the picks of it and read the themes again. */
export async function deleteOwnTheme(id) {
  await api.del(`/themes/${id}`);
  forgetTheme(id);
  await loadOwnThemes();
}

/** T3 Code's themeIdFromName: the name in lowercase, every run of other characters a dash. */
export function idFromName(name) {
  const id = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, ID_UNITS);
  return id === "" ? "custom-theme" : id;
}

/** `id`, or the first of `id-2`, `id-3` and on that no theme has and no theme may have, kept to 48 characters. */
export function freeId(id) {
  const taken = (candidate) =>
    findTheme(candidate) !== undefined ||
    T3_RESERVED.includes(candidate) ||
    THEMES.some((theme) => theme.id === candidate);
  let candidate = id;
  for (let count = 2; taken(candidate); count += 1) {
    const suffix = `-${count}`;
    candidate = `${id.slice(0, ID_UNITS - suffix.length)}${suffix}`;
  }
  return candidate;
}

/**
 * The file of a theme of one's own as T3 Code's theme file (version 1), JSON with two spaces of indent and a line end;
 * a palette derived from two colours is written out whole, so that T3 Code shows the theme as Dot draws it.
 */
export function exportedText(theme) {
  const file = theme.file;
  const colors = file.canvas === undefined ? file.colors : { ...vividPalette(file.canvas, file.accent), ...file.colors };
  const exported = { version: 1, id: theme.id, name: file.name, appearance: file.appearance, colors };
  for (const key of ["variants", "collection", "managed"]) {
    if (file[key] !== undefined) {
      exported[key] = file[key];
    }
  }
  return `${JSON.stringify(exported, null, 2)}\n`;
}

/**
 * The theme file of a file the owner adds, ready to keep: a VS Code colour theme made into one, a short form written
 * out whole, T3 Code's file as it is. Throws ThemeFileError with what is wrong.
 */
export function importedFile(text) {
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new ThemeFileError(`The file is not JSON: ${error.message}`);
  }
  if (isVsCodeTheme(value)) {
    const made = vsCodeThemeFile(value);
    if (made === null) {
      throw new ThemeFileError('The VS Code theme has no "editor.background" colour, so there is nothing to build on.');
    }
    return themeFileOf(made);
  }
  const file = themeFileOf(value);
  if (file.canvas === undefined) {
    return { ...file, version: 1 };
  }
  const { canvas, accent, ...rest } = file;
  return { ...rest, version: 1, colors: { ...vividPalette(canvas, accent), ...file.colors } };
}
