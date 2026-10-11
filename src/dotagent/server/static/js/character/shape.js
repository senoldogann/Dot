// Ported from bloub: src/bot/shape.ts. A silhouette is a radial profile r(theta) plus a pose. Every profile is
// sampled at the same angles, so any two shapes have points that correspond one to one and a morph is a linear
// interpolation of radii: clean transitions without a path-morphing library.
import { TAU, lerp, r2 } from "./math.js";
import { PROFILES, PROFILE_SAMPLES } from "./profiles.js";

const ANGLES = Array.from({ length: PROFILE_SAMPLES }, (_, i) => (i / PROFILE_SAMPLES) * TAU);
const COS = ANGLES.map(Math.cos);
const SIN = ANGLES.map(Math.sin);

// {radii, rot (radians), cx, cy (in ball radii), sx, sy (squash and stretch in screen space, after the rotation)}
export function silhouette(name, pose = {}) {
  return { radii: [...PROFILES[name]], rot: 0, cx: 0, cy: 0, sx: 1, sy: 1, ...pose };
}

// A perfect circle: the neutral base (dot, bubble, fade target).
export function circle(radius, pose = {}) {
  return { radii: new Array(PROFILE_SAMPLES).fill(radius), rot: 0, cx: 0, cy: 0, sx: 1, sy: 1, ...pose };
}

// Interpolation of two silhouettes; `out` is reused so nothing is allocated at 60 fps.
export function blend(a, b, t, out) {
  const dst = out ?? { radii: new Array(PROFILE_SAMPLES), rot: 0, cx: 0, cy: 0, sx: 1, sy: 1 };
  for (let i = 0; i < PROFILE_SAMPLES; i++) dst.radii[i] = lerp(a.radii[i] ?? 1, b.radii[i] ?? 1, t);
  // The rotation takes the short way round, so +170° to -170° is not a full turn.
  let dRot = b.rot - a.rot;
  while (dRot > Math.PI) dRot -= TAU;
  while (dRot < -Math.PI) dRot += TAU;
  dst.rot = a.rot + dRot * t;
  dst.cx = lerp(a.cx, b.cx, t);
  dst.cy = lerp(a.cy, b.cy, t);
  dst.sx = lerp(a.sx, b.sx, t);
  dst.sy = lerp(a.sy, b.sy, t);
  return dst;
}

// The silhouette as screen points; `scale` is the ball's radius in viewBox units.
export function toPoints(s, scale, out = []) {
  const cr = Math.cos(s.rot);
  const sr = Math.sin(s.rot);
  for (let i = 0; i < PROFILE_SAMPLES; i++) {
    const r = s.radii[i] ?? 1;
    const x = r * (COS[i] ?? 0);
    const y = r * (SIN[i] ?? 0);
    const rx = x * cr - y * sr;
    const ry = x * sr + y * cr;
    const p = out[i] ?? { x: 0, y: 0 };
    p.x = (rx * s.sx + s.cx) * scale;
    p.y = (ry * s.sy + s.cy) * scale;
    out[i] = p;
  }
  out.length = PROFILE_SAMPLES;
  return out;
}

// Closed polyline to Catmull-Rom cubics: with 64 points the centred tangents give a contour smooth to the pixel
// even at 600 px, and the string stays short.
export function closedPath(pts, tension = 1 / 6) {
  const n = pts.length;
  if (n < 3) return "";
  const first = pts[0];
  let d = `M${r2(first.x)} ${r2(first.y)}`;
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n];
    const p1 = pts[i];
    const p2 = pts[(i + 1) % n];
    const p3 = pts[(i + 2) % n];
    const c1x = p1.x + (p2.x - p0.x) * tension;
    const c1y = p1.y + (p2.y - p0.y) * tension;
    const c2x = p2.x - (p3.x - p1.x) * tension;
    const c2y = p2.y - (p3.y - p1.y) * tension;
    d += `C${r2(c1x)} ${r2(c1y)} ${r2(c2x)} ${r2(c2y)} ${r2(p2.x)} ${r2(p2.y)}`;
  }
  return `${d}Z`;
}

// Any polygon to a radial profile, by casting rays from (cx, cy). For the shapes that are not natural in r(theta),
// such as the tapered bar of the "!". Computed once at load, never in the render loop.
export function profileFromPolygon(poly, cx, cy) {
  const radii = new Array(PROFILE_SAMPLES).fill(0);
  const n = poly.length;
  for (let k = 0; k < PROFILE_SAMPLES; k++) {
    const dx = COS[k] ?? 0;
    const dy = SIN[k] ?? 0;
    let best = 0;
    for (let i = 0; i < n; i++) {
      const a = poly[i];
      const b = poly[(i + 1) % n];
      const ex = b.x - a.x;
      const ey = b.y - a.y;
      const den = dx * ey - dy * ex;
      if (Math.abs(den) < 1e-9) continue;
      const px = a.x - cx;
      const py = a.y - cy;
      const t = (px * ey - py * ex) / den; // distance along the ray
      const u = (px * dy - py * dx) / den; // position on the segment
      if (t > best && u >= 0 && u <= 1) best = t;
    }
    radii[k] = best;
  }
  return radii;
}

// The convex hull of two circles: the tapered bar of the vertical "!".
export function hullOfCircles(x1, y1, r1, x2, y2, r2v, steps = 96) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const dist = Math.hypot(dx, dy) || 1e-6;
  const base = Math.atan2(dy, dx);
  const spread = Math.acos(Math.max(-1, Math.min(1, (r1 - r2v) / dist)));
  const pts = [];
  for (let i = 0; i <= steps / 2; i++) {
    const a = base + spread + ((TAU - 2 * spread) * i) / (steps / 2);
    pts.push({ x: x1 + Math.cos(a) * r1, y: y1 + Math.sin(a) * r1 });
  }
  for (let i = 0; i <= steps / 2; i++) {
    const a = base - spread + (2 * spread * i) / (steps / 2);
    pts.push({ x: x2 + Math.cos(a) * r2v, y: y2 + Math.sin(a) * r2v });
  }
  return pts;
}

// The profile's radius in any direction, between its two neighbouring samples. What sits "on" the body (the eyes,
// the pastille) is placed with it when the silhouette is no longer a circle, or the mask would crop it.
export function radiusAtAngle(radii, angle) {
  const n = radii.length;
  const t = ((((angle / TAU) % 1) + 1) % 1) * n;
  const i = Math.floor(t);
  return lerp(radii[i % n] ?? 1, radii[(i + 1) % n] ?? 1, t - i);
}

// Superellipse |x/sx|^n + |y/sy|^n = 1: n = 2 an ellipse, n ≈ 4 the squircle.
export function superellipseProfile(n, sx = 1, sy = 1) {
  return ANGLES.map((_, i) => {
    const c = Math.abs((COS[i] ?? 0) / sx) ** n;
    const s = Math.abs((SIN[i] ?? 0) / sy) ** n;
    return (c + s) ** (-1 / n);
  });
}

// The radial profile of a union of discs: the farthest ray/circle intersection. Exact while the origin is inside
// the union; the cloud's bumps without path booleans.
export function unionOfCirclesProfile(circles) {
  const out = new Array(PROFILE_SAMPLES).fill(0);
  for (let i = 0; i < PROFILE_SAMPLES; i++) {
    const dx = COS[i] ?? 0;
    const dy = SIN[i] ?? 0;
    let best = 0;
    for (const c of circles) {
      const b = dx * c.x + dy * c.y;
      const disc = b * b - (c.x * c.x + c.y * c.y - c.r * c.r);
      if (disc < 0) continue;
      const t = b + Math.sqrt(disc);
      if (t > best) best = t;
    }
    out[i] = best;
  }
  return out;
}

// A polygon with round corners (Minkowski sum with a disc): each edge pushed out by `rc`, each vertex an arc.
// Expects a clockwise polygon in screen space.
function roundedPolygon(verts, rc, arcSteps = 10) {
  const n = verts.length;
  const out = [];
  const normal = (a, b) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    return Math.atan2(-dx / len, dy / len);
  };
  for (let i = 0; i < n; i++) {
    const prev = verts[(i - 1 + n) % n];
    const cur = verts[i];
    const next = verts[(i + 1) % n];
    const a0 = normal(prev, cur);
    const a1 = normal(cur, next);
    let d = a1 - a0;
    while (d > Math.PI) d -= TAU;
    while (d < -Math.PI) d += TAU;
    for (let k = 0; k <= arcSteps; k++) {
      const a = a0 + (d * k) / arcSteps;
      out.push({ x: cur.x + Math.cos(a) * rc, y: cur.y + Math.sin(a) * rc });
    }
  }
  return out;
}

// A regular polygon with round corners, inscribed in `radius`.
export function regularPolygonProfile(sides, radius, rc, rotationDeg = 0) {
  const rot = (rotationDeg * Math.PI) / 180;
  const verts = Array.from({ length: sides }, (_, i) => {
    const a = rot + (i / sides) * TAU;
    return { x: Math.cos(a) * (radius - rc), y: Math.sin(a) * (radius - rc) };
  });
  return profileFromPolygon(roundedPolygon(verts, rc), 0, 0);
}

// An exact closed polyline: keeps the straight segments, unlike closedPath.
export function polyPath(pts, scale = 1) {
  if (pts.length < 3) return "";
  let d = "";
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    d += `${i === 0 ? "M" : "L"}${r2(p.x * scale)} ${r2(p.y * scale)}`;
  }
  return `${d}Z`;
}

// A capsule (stadium) centred on the origin: the exact shape of the eyes.
export function capsulePath(w, h) {
  const hw = Math.max(w, 0.01) / 2;
  const hh = Math.max(h, 0.01) / 2;
  const r = Math.min(hw, hh);
  return (
    `M${r2(-hw)} ${r2(-hh + r)}` +
    `A${r2(r)} ${r2(r)} 0 0 1 ${r2(-hw + r)} ${r2(-hh)}` +
    `L${r2(hw - r)} ${r2(-hh)}` +
    `A${r2(r)} ${r2(r)} 0 0 1 ${r2(hw)} ${r2(-hh + r)}` +
    `L${r2(hw)} ${r2(hh - r)}` +
    `A${r2(r)} ${r2(r)} 0 0 1 ${r2(hw - r)} ${r2(hh)}` +
    `L${r2(-hw + r)} ${r2(hh)}` +
    `A${r2(r)} ${r2(r)} 0 0 1 ${r2(-hw)} ${r2(hh - r)}Z`
  );
}
