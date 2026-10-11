// Ported from bloub: src/bot/decor.ts. The rings, the swoosh, the three dots, the burst's particles, the comet's
// ribbons and the notification pastille. Geometry in ball radii; the engine, which alone knows the viewBox's scale,
// rasterises it.
import { TAU, clamp, createRng, r2 } from "./math.js";

// The rings are not flat colours: the video shows a full hue wheel at constant lightness, with a gradient along each
// trace (measured S 45–62 %, L 50–67 %).
function wheel(hue, s = 0.55, l = 0.62) {
  const h = ((hue % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  const hex = (v) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

// A tilted 3D circle in orthographic projection. The circle lives in the plane of u (in the screen) and v (into the
// depth); z splits the arc in two: the back half is drawn before the body, so the body hides it. That real depth
// sort is what makes the rings read as orbits rather than a flat drawing.
// Returns {id, front, back, width, opacity, grad: {x1, y1, x2, y2, stops}}.
export function arcRender(seed, t, scale, id, opacity = 1) {
  const spin = seed.phase + t * seed.speed * TAU;
  const cu = Math.cos(seed.tilt);
  const su = Math.sin(seed.tilt);
  const kz = Math.sqrt(Math.max(0, 1 - seed.k * seed.k));
  const N = 64;
  const span = seed.sweep * TAU;
  let front = "";
  let back = "";
  let prev = null;
  for (let i = 0; i <= N; i++) {
    const th = spin + (i / N) * span;
    const ct = Math.cos(th);
    const st = Math.sin(th);
    const x = seed.a * (ct * cu + st * -su * seed.k) + seed.cx;
    const y = seed.a * (ct * su + st * cu * seed.k) + seed.cy;
    const z = seed.a * st * kz;
    const behind = z < 0;
    const sx = r2(x * scale);
    const sy = r2(y * scale);
    const cmd = behind !== prev ? "M" : "L";
    if (behind) back += `${cmd}${sx} ${sy}`;
    else front += `${cmd}${sx} ${sy}`;
    prev = behind;
  }
  const gx = Math.cos(seed.tilt) * seed.a * scale;
  const gy = Math.sin(seed.tilt) * seed.a * scale;
  return {
    id,
    front,
    back,
    width: seed.width * scale,
    opacity,
    grad: {
      x1: r2(seed.cx * scale - gx),
      y1: r2(seed.cy * scale - gy),
      x2: r2(seed.cx * scale + gx),
      y2: r2(seed.cy * scale + gy),
      stops: [wheel(seed.hue), wheel(seed.hue + seed.hueSpan * 0.5), wheel(seed.hue + seed.hueSpan)],
    },
  };
}

const RING_RNG = createRng(0xa11ce);

// 6 rings, semi-major axis 1.30–1.40 (well beyond the ball), flattening at most 0.45, width 0.055, about 3.3 turns/s.
export const RINGS = Array.from({ length: 6 }, (_, i) => ({
  a: 1.3 + RING_RNG() * 0.1,
  k: 0.05 + RING_RNG() * 0.4,
  tilt: (i / 6) * Math.PI + RING_RNG() * 0.5,
  speed: 3 + RING_RNG() * 0.7,
  phase: RING_RNG() * TAU,
  sweep: 0.6 + RING_RNG() * 0.25,
  hue: (i * 360) / 6 + RING_RNG() * 30,
  hueSpan: 60 + RING_RNG() * 60,
  width: 0.05 + RING_RNG() * 0.012,
  cx: 0,
  cy: 0.1,
}));

// A bouquet of nested arcs that sweeps the triangle just before the orbits; seen nearly edge-on (the hairpin shape).
export const SWOOSH = Array.from({ length: 4 }, (_, i) => ({
  a: 0.78 + i * 0.2,
  k: 0.05 + i * 0.02,
  tilt: -0.62 + i * 0.05,
  speed: 0.3,
  phase: 0.06 * i,
  sweep: 0.4,
  hue: 95 + i * 62,
  hueSpan: 100,
  width: 0.05,
  cx: 0,
  cy: -0.12,
}));

// The three dots: x measured at -0.557 / -0.013 / +0.532, y = 0.
export const DOT_X = [-0.557, -0.013, 0.532];
export const DOT_R = 0.165;
export const DOT_PEAK = 1.25;

const P_RNG = createRng(0xbeef);

// 5 particles, a new one every 0.2 s, alive 0.55 s.
const PARTICLES = Array.from({ length: 5 }, (_, i) => ({ birth: i * 0.2, angle: P_RNG() * TAU, rho: 0.58 + P_RNG() * 0.18 }));

// The particles spiral toward the centre (radius × 0.75 per frame, angle +100°/s) while growing, and pass behind
// the core where they are swallowed. Each: {x, y, r, depth, opacity}.
export function particles(t, scale) {
  const out = [];
  for (const p of PARTICLES) {
    const u = t - p.birth;
    if (u < 0 || u > 0.62) continue;
    const rho = p.rho * Math.pow(0.75, u * 10);
    const a = p.angle + (u * 100 * Math.PI) / 180;
    out.push({
      x: Math.cos(a) * rho * scale,
      y: Math.sin(a) * rho * scale,
      r: (0.04 + 0.028 * clamp(u / 0.55)) * scale,
      depth: clamp(1 - rho / 0.8),
      opacity: clamp(u / 0.06) * clamp((0.62 - u) / 0.08),
    });
  }
  return out;
}

// The comet's dot stays at the centre; the trail orbits it. Ellipse a = 0.85, b = 0.15, major axis at +34°, 4
// ribbons, about 210°/s.
const COMET_RNG = createRng(0xc0e7);
export const COMET_RIBBONS = Array.from({ length: 4 }, (_, i) => {
  const d = i - 1.5;
  return {
    a: 0.85 * (1 + d * 0.03),
    k: (0.15 / 0.85) * (1 + d * 0.16),
    tilt: (34 * Math.PI) / 180 + d * 0.035,
    speed: 210 / 360,
    phase: -i * 0.045 + COMET_RNG() * 0.012,
    sweep: 0.34,
    hue: i * 85 + COMET_RNG() * 20,
    hueSpan: 80,
    width: 0.095,
    cx: 0,
    cy: 0,
  };
});

// The comet's dot, measured at 0.129.
export const COMET_DOT = 0.129;

// The notification pastille: the blue read at the pixel, on the circumference at -42°, a pop 14 % above its rest
// radius, and a notch (a concentric disc subtracted from the body) with a constant margin.
export const NOTIF_BLUE = "#2496e8";
export const NOTIF_ANGLE = -42;
export const NOTIF_DIST = 1.003;
export const NOTIF_R = 0.15;
export const NOTIF_POP = 1.14;
export const NOTIF_MARGIN = 0.054;
