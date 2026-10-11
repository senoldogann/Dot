// Ported from bloub: src/bot/eyefit.ts. Where to set the face on a shape of the customiser. The eyes live on a
// sphere and `radiusAtAngle` draws their centre in to the real contour, but an eye has a size: on a silhouette narrow
// in its direction it is pushed against the edge until the mask opens it outward (a notch in the body on the capsule,
// the triangle, the cloud and the droplet). This module solves that once per shape and returns a table of offsets:
// solved in the render loop, the correction reacted to everything that moves at 60 fps and every variant trembled;
// tabulated, it only goes from one entry to another on the morph's own curve. bloub builds the whole table at import;
// here a shape's table is built on its first use, so a page with one character pays for one shape.
import { EXPRESSIONS } from "./expressions.js";
import { eyePoses } from "./face.js";
import { radiusAtAngle, toPoints } from "./shape.js";
import { SHAPES } from "./skins.js";
import { STATES } from "./states.js";

// The solver's reference radius; the offset is returned in units of it.
const R = 100;

// The largest amplitudes of life at rest, read from `liveliness` (loopNoise is bounded by 1, so these are exact
// bounds): 7° of yaw move an eye a dozen units on a ball of radius 100.
const DRIFT_YAW = 5.5 + 1.6;
const DRIFT_PITCH = 4.2 + 1.3;
const DRIFT_X = 0.006;
const DRIFT_Y = 0.007;

// The footprints of a face's two eyes on a profile: {x, y, ax, ay (the axis half-vector), r (the local disc's
// radius), m (the tangent matrix's columns, for the support function)}. A capsule is a segment thickened by a disc;
// its image by the tangent matrix is a segment thickened by an ellipse, and the radius to clear in a direction is
// that ellipse's support function. The blink is not here: a closed eye needs no room.
function footprints(face, sil, radii) {
  const out = [];
  const poses = eyePoses(face.gaze, R, face.split);
  for (let i = 0; i < 2; i++) {
    const e = poses[i];
    if (e.depth <= 0.02) continue;
    const cfg = face.eyes[i];
    const phi = ((cfg.tilt ?? 0) * Math.PI) / 180;
    const cp = Math.cos(phi);
    const sp = Math.sin(phi);
    const ax = e.a * cp + e.c * sp;
    const ay = e.b * cp + e.d * sp;
    const cx = -e.a * sp + e.c * cp;
    const cy = -e.b * sp + e.d * cp;
    const hw = Math.max(cfg.w * R, 0.01) / 2;
    const hh = Math.max(cfg.h * R, 0.01) / 2;
    const r = Math.min(hw, hh);
    const long = hh > hw;
    const half = long ? hh - r : hw - r;
    const fit = radiusAtAngle(radii, Math.atan2(e.y, e.x) - sil.rot);
    out.push({ x: e.x * fit, y: e.y * fit, ax: (long ? cx : ax) * half, ay: (long ? cy : ay) * half, r, m: [ax, ay, cx, cy] });
  }
  return out;
}

// The closest approach between a contour and a segment: the distance and the unit vector from the contour toward
// the segment, the direction that clears. Both from the same pass, the module's one real cost.
function approach(pts, x0, y0, x1, y1) {
  const sx = x1 - x0;
  const sy = y1 - y0;
  const len2 = sx * sx + sy * sy;
  let best = Infinity;
  let vx = 0;
  let vy = 0;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    let t = len2 > 0 ? ((p.x - x0) * sx + (p.y - y0) * sy) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const ex = x0 + t * sx - p.x;
    const ey = y0 + t * sy - p.y;
    const d2 = ex * ex + ey * ey;
    if (d2 < best) {
      best = d2;
      vx = ex;
      vy = ey;
    }
  }
  const d = Math.sqrt(best);
  return { d, ux: d > 1e-9 ? vx / d : 0, uy: d > 1e-9 ? vy / d : 0 };
}

// The centre's float at rest in viewBox units, added to the capsule's radius: under one unit.
const FLOAT = Math.hypot(DRIFT_X, DRIFT_Y) * R;

// The margin of the tightest capsule, and the direction that clears it.
function worst(pts, prints, tx, ty) {
  let margin = Infinity;
  let ux = 0;
  let uy = 0;
  for (const e of prints) {
    const x = e.x + tx;
    const y = e.y + ty;
    const a = approach(pts, x - e.ax, y - e.ay, x + e.ax, y + e.ay);
    const [m0, m1, m2, m3] = e.m;
    const radius = e.r * Math.hypot(m0 * a.ux + m1 * a.uy, m2 * a.ux + m3 * a.uy) + FLOAT;
    if (a.d - radius < margin) {
      margin = a.d - radius;
      ux = a.ux;
      uy = a.uy;
    }
  }
  return { margin, ux, uy };
}

// The directions probed and the bisection's steps: their product is the table's cost.
const DIRECTIONS = 12;
const BISECTION = 8;

// The offset for both eyes on this shape, state and expression: one translation common to both eyes (an isometry,
// so spread, sizes and tilts are kept to the pixel). The margin aimed at is the original profile's, capped by what
// the shape offers at its centre. A directional search, not a descent: the shortest translation that fits, found by
// probing a ring of directions and bisecting the distance along each.
function solve(trials) {
  if (!trials.length) return { x: 0, y: 0 };
  const margin = (tx, ty) => {
    let m = Infinity;
    for (const tr of trials) m = Math.min(m, worst(tr.contour, tr.prints, tx, ty).margin);
    return m;
  };
  let required = Infinity;
  for (const tr of trials) required = Math.min(required, worst(tr.calContour, tr.reference, 0, 0).margin);
  // The travel must reach the body's centre: `wide` has capsules 87 units long that fit a triangle only near it.
  let mx = 0;
  let my = 0;
  const prints = trials[0].prints;
  for (const e of prints) {
    mx -= e.x / prints.length;
    my -= e.y / prints.length;
  }
  const travel = Math.max(0.35 * R, Math.hypot(mx, my) * 1.25);
  required = Math.min(required, margin(mx, my));
  // Already good: the circle, and any shape wide enough. The capsule must fit as well as be no tighter than on the
  // original profile; where nothing fits, aim at the least bad rather than give up.
  const start = margin(0, 0);
  if (start >= required && start >= 0) return { x: 0, y: 0 };
  const target = Math.max(required, 0);
  let bestX = 0;
  let bestY = 0;
  let bestNorm = Infinity;
  let rescueX = 0;
  let rescueY = 0;
  let rescue = start;
  for (let d = 0; d < DIRECTIONS; d++) {
    const a = (d / DIRECTIONS) * Math.PI * 2;
    const ux = Math.cos(a);
    const uy = Math.sin(a);
    if (margin(ux * travel, uy * travel) < target) {
      for (const k of [0.3, 0.6, 1]) {
        const m = margin(ux * travel * k, uy * travel * k);
        if (m > rescue) {
          rescue = m;
          rescueX = ux * travel * k;
          rescueY = uy * travel * k;
        }
      }
      continue;
    }
    let low = 0;
    let high = travel;
    for (let i = 0; i < BISECTION; i++) {
      const mid = (low + high) / 2;
      if (margin(ux * mid, uy * mid) >= target) high = mid;
      else low = mid;
    }
    if (high < bestNorm) {
      bestNorm = high;
      bestX = ux * high;
      bestY = uy * high;
    }
  }
  const x = bestNorm === Infinity ? rescueX : bestX;
  const y = bestNorm === Infinity ? rescueY : bestY;
  // in ball radii: the engine puts it back to its scale
  return { x: +(x / R).toFixed(6), y: +(y / R).toFixed(6) };
}

// The face to cover: the expression's when the state accepts it, the state's own otherwise. One entry per
// expression, not a common worst case: on a capsule, neutral wants to go down and scared wants to go up.
function faceOf(def, pose, expr) {
  if (def.baseFace && expr) return { gaze: expr.gaze, split: expr.split, eyes: expr.eyes };
  return { gaze: pose.gaze, split: pose.split, eyes: pose.eyes };
}

// The dates to sample in a state: one if its pose does not move.
function dates(def) {
  const signature = (p) => JSON.stringify([p.gaze, p.split, p.eyes, p.sil.rot, p.sil.cx, p.sil.cy, p.sil.sx, p.sil.sy]);
  if (signature(def.pose(0)) === signature(def.pose(def.duration))) return [0];
  const n = 3;
  return Array.from({ length: n }, (_, i) => (i / (n - 1)) * def.duration);
}

// A shape's offset on a state and an expression, the drift's four corners covered.
function offsetFor(def, radii, expr) {
  const trials = [];
  for (const t of dates(def)) {
    const pose = def.pose(t);
    const contour = toPoints({ ...pose.sil, radii }, R);
    const calContour = toPoints(pose.sil, R);
    const v = faceOf(def, pose, expr);
    for (const dy of [-DRIFT_YAW, DRIFT_YAW]) {
      for (const dp of [-DRIFT_PITCH, DRIFT_PITCH]) {
        const c = { ...v, gaze: { yaw: v.gaze.yaw + dy, pitch: v.gaze.pitch + dp, roll: v.gaze.roll } };
        trials.push({ prints: footprints(c, pose.sil, radii), reference: footprints(c, pose.sil, pose.sil.radii), contour, calContour });
      }
    }
  }
  return solve(trials);
}

const NONE = { x: 0, y: 0 };

const key = (state, expr) => `${state}|${expr ?? ""}`;

// One shape's table: an entry per (rest-body state, expression). Only `idle` and `swirl` wear the rest face, so
// only they vary by expression.
function tableFor(radii) {
  const table = new Map();
  for (const def of STATES) {
    if (!def.baseBody) continue;
    const expressions = def.baseFace ? [null, ...EXPRESSIONS] : [null];
    for (const expr of expressions) table.set(key(def.id, expr?.id ?? null), offsetFor(def, radii, expr));
  }
  return table;
}

// Keyed by the radii array's reference, the engine's own convention (`radii === this.shape`); built on first use.
const TABLES = new Map();
const KNOWN = new Set(SHAPES.map((s) => s.radii));

// The offset to apply to both eyes for this shape on this state, in ball radii. Zero for a shape not in the
// catalogue (`null` included) and for the circle, whose two profiles are the same.
export function eyeOffset(radii, state, expr) {
  if (!radii || !KNOWN.has(radii)) return NONE;
  let table = TABLES.get(radii);
  if (!table) {
    table = tableFor(radii);
    TABLES.set(radii, table);
  }
  return table.get(key(state, expr)) ?? table.get(key(state, null)) ?? NONE;
}
