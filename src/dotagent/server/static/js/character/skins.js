// Ported from bloub: src/bot/skins.ts. The shapes and colours of the customiser. Unlike the animation silhouettes
// (profiles.js), these are not read from the video: they are built analytically after the original customiser's grid.
// The ids are English here (bloub's are French); the order and the maths are bloub's.
import { PROFILE_SAMPLES } from "./profiles.js";
import { hullOfCircles, profileFromPolygon, regularPolygonProfile, superellipseProfile, unionOfCirclesProfile } from "./shape.js";

// Brings the largest radius to `max` so every shape weighs the same to the eye.
function normalize(radii, max = 1) {
  const peak = Math.max(...radii);
  if (peak <= 0) return radii;
  const k = max / peak;
  return radii.map((r) => r * k);
}

const ANGLES = Array.from({ length: PROFILE_SAMPLES }, (_, i) => (i / PROFILE_SAMPLES) * Math.PI * 2);

// Pebble: a circle bent by two low harmonics, irregular but smooth.
const pebble = normalize(ANGLES.map((a) => 1 + 0.075 * Math.cos(2 * a + 0.5) + 0.035 * Math.cos(3 * a + 2.1)), 1.02);

// Cloud: a union of bumps, wide below, two lobes above.
const cloud = normalize(
  unionOfCirclesProfile([
    { x: -0.44, y: 0.2, r: 0.54 },
    { x: 0.46, y: 0.2, r: 0.5 },
    { x: 0.02, y: 0.3, r: 0.6 },
    { x: -0.24, y: -0.3, r: 0.48 },
    { x: 0.3, y: -0.24, r: 0.44 },
  ]),
  1.02,
);

// Droplet: a big disc below, a fine point above.
const droplet = normalize(profileFromPolygon(hullOfCircles(0, 0.28, 0.66, 0, -0.96, 0.05), 0, 0), 1.04);

// Capsule lying down: the hull of two discs side by side.
const capsule = profileFromPolygon(hullOfCircles(-0.42, 0, 0.62, 0.42, 0, 0.62), 0, 0);

export const SHAPES = [
  { id: "circle", radii: new Array(PROFILE_SAMPLES).fill(1) },
  { id: "pebble", radii: pebble },
  // 1.15, not 1.02: on a superellipse the largest radius is the diagonal, so normalising on it shrinks the shape.
  { id: "squircle", radii: normalize(superellipseProfile(4.2), 1.15) },
  { id: "capsule", radii: capsule },
  // -90°: a vertex toward the top of the screen (y points down)
  { id: "triangle", radii: regularPolygonProfile(3, 1.12, 0.34, -90) },
  // 0°: vertices left and right, so the top and bottom edges are flat
  { id: "hexagon", radii: regularPolygonProfile(6, 1.04, 0.26, 0) },
  { id: "cloud", radii: cloud },
  { id: "droplet", radii: droplet },
];

export const SHAPE_IDS = SHAPES.map((s) => s.id);
export const SHAPE_BY_ID = new Map(SHAPES.map((s) => [s.id, s]));
export const DEFAULT_SHAPE = "circle";

// The original customiser's palette; the hex is the reference, the UI paints the body from the theme's
// `--character-<id>` token so each scheme may tune it.
export const COLOURS = [
  { id: "ink", hex: "#0a0a0c" },
  { id: "brown", hex: "#8b5e3c" },
  { id: "red", hex: "#e8483f" },
  { id: "orange", hex: "#f08a24" },
  { id: "amber", hex: "#f0b429" },
  { id: "green", hex: "#3ecf8e" },
  { id: "teal", hex: "#2fbfa0" },
  { id: "blue", hex: "#3b93f0" },
  { id: "violet", hex: "#8b5cf6" },
  { id: "pink", hex: "#e152b0" },
  { id: "grey", hex: "#a3a3a3" },
  { id: "cream", hex: "#f1efe9" },
];

export const COLOUR_IDS = COLOURS.map((c) => c.id);
export const COLOUR_BY_ID = new Map(COLOURS.map((c) => [c.id, c]));
export const DEFAULT_COLOUR = "ink";

