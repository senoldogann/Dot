// Ported from bloub: src/bot/states.ts. Every animation is a state with a pose(t): the body's silhouette, the gaze,
// the eyes, the decor. The numbers were read frame by frame from the reference video; `swirl` alone is chosen.
import { COMET_DOT, COMET_RIBBONS, DOT_PEAK, DOT_R, DOT_X, NOTIF_ANGLE, NOTIF_DIST, NOTIF_MARGIN, NOTIF_POP, NOTIF_R, RINGS, SWOOSH, particles } from "./decor.js";
import { EYE_H, EYE_SPLIT, EYE_W, REST_GAZE } from "./face.js";
import { TAU, clamp, easings } from "./math.js";
import { circle, hullOfCircles, polyPath, profileFromPolygon, silhouette } from "./shape.js";

// An eye: {w (short axis, ball radii), h (long axis), open (1 open, 0 closed), tilt (degrees, positive = the top
// leans right, applied after the sphere's tangent frame)}.
const pair = (w, h) => [{ w, h, open: 1 }, { w, h, open: 1 }];

// A pose: {sil, offX, offY, gaze, split, eyes, eyeAlpha, bodyAlpha, dots, arcs, notif, dotsBehind}.
function base(over = {}) {
  return {
    sil: circle(1),
    offX: 0,
    offY: 0,
    gaze: { ...REST_GAZE },
    split: EYE_SPLIT,
    eyes: pair(EYE_W, EYE_H),
    eyeAlpha: 1,
    bodyAlpha: 1,
    dots: [],
    arcs: [],
    notif: null,
    dotsBehind: false,
    ...over,
  };
}

// The bar of the upright "!": the hull of two circles, top (0, -0.505) r 0.132, bottom (0, +0.130) r 0.075.
const BAR_UPRIGHT_CY = -0.1875;
const BAR_UPRIGHT = profileFromPolygon(hullOfCircles(0, -0.505, 0.132, 0, 0.13, 0.075), 0, BAR_UPRIGHT_CY);

// The bar of the leaning "!": a pure capsule (width 0.269, length 0.776).
const BAR_ITALIC = profileFromPolygon(hullOfCircles(0, -0.2535, 0.1345, 0, 0.2535, 0.1345), 0, 0);

const barUpright = (pose = {}) => ({ radii: [...BAR_UPRIGHT], rot: 0, cx: 0, cy: BAR_UPRIGHT_CY, sx: 1, sy: 1, ...pose });
const barItalic = (pose = {}) => ({ radii: [...BAR_ITALIC], rot: 0, cx: 0, cy: 0, sx: 1, sy: 1, ...pose });

// The dot of the leaning "!" is a teardrop: round end (r 0.118) toward the bar, fine point away, length 0.300.
const TEAR = polyPath(hullOfCircles(0, 0, 0.118, 0, 0.172, 0.012));

// The triangle does not spin in place: its centre describes a circle of radius 0.213 about the origin.
const TRI_ORBIT = 0.213;

function spinningTriangle(rot) {
  return silhouette("triangle", { rot, cx: -TRI_ORBIT * Math.sin(rot), cy: TRI_ORBIT * Math.cos(rot) });
}

// A pulse that runs through the three dots from left to right.
function dotPulse(t, index) {
  const p = ((((t - index * 0.5) / 1.5) % 1) + 1) % 1;
  const k = p < 0.5 ? 0.5 - 0.5 * Math.cos(p * TAU) : 0;
  return clamp(k * 2);
}

// Each state: {id, duration (held when the full sequence plays), minDuration (below it the animation is cut before
// it lands), morph (the entry morph's length), blinkIn (the entry hidden by a blink), baseBody (the body is the rest
// silhouette, so the chosen shape may replace it), baseFace (the face is the rest face, so the chosen expression may
// replace it), pose(t)}.
export const STATES = [
  { id: "idle", duration: 2.4, morph: 0.45, blinkIn: false, baseFace: true, baseBody: true, pose: () => base() },

  {
    id: "thinking",
    duration: 2.6,
    morph: 0.4,
    baseFace: false,
    baseBody: false,
    blinkIn: true,
    pose: (t) => {
      const mid = dotPulse(t, 1);
      // The side dots come out of the ball's flanks: fused with it for a frame or two before they part.
      const emerge = 0.3 + 0.7 * easings.easeOutCubic(clamp(t / 0.3));
      return base({
        // the ball becomes the middle dot, so the morph stays continuous
        sil: circle(DOT_R * (1 + (DOT_PEAK - 1) * mid), { cx: DOT_X[1] }),
        eyeAlpha: 0,
        dots: [0, 2].map((i) => {
          const k = dotPulse(t, i);
          return { x: DOT_X[i] * emerge, y: 0, r: DOT_R * (1 + (DOT_PEAK - 1) * k), opacity: 0.55 + 0.45 * k };
        }),
      });
    },
  },

  {
    id: "wink",
    duration: 1.6,
    morph: 0.3,
    blinkIn: true,
    baseFace: false,
    baseBody: true,
    pose: () =>
      base({
        gaze: { yaw: -5.37, pitch: 4.55, roll: 6.7 },
        split: 16.25,
        // The closed eye is not the open eye squashed: a horizontal dash wider than the open eye (0.447 vs 0.236).
        eyes: [
          { w: 0.236, h: 0.464, open: 1 },
          { w: 0.447, h: 0.089, open: 1 },
        ],
      }),
  },

  {
    id: "wide",
    duration: 1.8,
    morph: 0.55,
    blinkIn: true,
    baseFace: false,
    baseBody: true,
    pose: () => base({ gaze: { yaw: 6.92, pitch: -21.96, roll: 11.6 }, split: 18.43, eyes: pair(0.356, 0.875) }),
  },

  {
    id: "alert",
    duration: 2.4,
    // the "!" comes back in place at 1.6 + 0.4
    minDuration: 2,
    morph: 0.45,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    pose: (t) => {
      // Measured travel: -0.087 to +0.732 in 1.5 s, ease-in-out, a micro overshoot.
      const p = clamp(t / 1.5);
      const travel = easings.easeInOutCubic(p) * 0.82 - 0.087;
      const back = t > 1.6 ? clamp((t - 1.6) / 0.4) : 0;
      const x = travel * (1 - back) + 0.1 * back;
      // A secondary buzz at 2.5 Hz, bar and dot in opposite phase.
      const buzz = Math.sin(t * 2.5 * TAU) * 0.005;
      const tilt = (17.7 * Math.PI) / 180;
      return base({
        sil: barItalic({ rot: tilt, cx: x, cy: -0.325 - buzz }),
        eyeAlpha: 0,
        dots: [
          {
            // the dot follows the glyph's axis, 0.580 from the bar's centre
            x: x - Math.sin(tilt) * 0.58,
            y: -0.325 + Math.cos(tilt) * 0.58 + buzz * 2.8,
            r: 0.118,
            d: TEAR,
            rot: (tilt * 180) / Math.PI,
            opacity: 1,
          },
        ],
      });
    },
  },

  {
    id: "notify",
    duration: 2.2,
    morph: 0.5,
    blinkIn: true,
    baseFace: false,
    baseBody: true,
    pose: (t) => {
      // The blue dot's pop: a peak at +14 % near 0.3 s, then still.
      const p = clamp(t / 0.45);
      const pop = 1 + (NOTIF_POP - 1) * Math.sin(p * Math.PI) * (1 - p * 0.35);
      const r = NOTIF_R * (p < 1 ? pop : 1);
      const a = (NOTIF_ANGLE * Math.PI) / 180;
      return base({
        // the gaze goes away from the pastille
        gaze: { yaw: -21.94, pitch: -5.82, roll: -12.2 },
        split: 18.89,
        eyes: pair(0.505, 0.498),
        notif: { x: Math.cos(a) * NOTIF_DIST, y: Math.sin(a) * NOTIF_DIST, r, notch: r + NOTIF_MARGIN },
      });
    },
  },

  {
    id: "exclaim",
    duration: 2,
    morph: 0.45,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    pose: () => base({ sil: barUpright(), eyeAlpha: 0, dots: [{ x: -0.012, y: 0.526, r: 0.113, opacity: 1 }] }),
  },

  {
    id: "sleep",
    duration: 2.4,
    morph: 0.5,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    // A measured vertical bounce: ±0.19 about +0.11, period 0.6 s.
    pose: (t) => base({ sil: circle(0.1585, { cy: 0.11 + Math.sin(t * (TAU / 0.6)) * 0.19 }), eyeAlpha: 0 }),
  },

  {
    id: "egg",
    duration: 1.8,
    morph: 0.4,
    baseFace: false,
    baseBody: false,
    blinkIn: true,
    // the eyes draw together like the body
    pose: () => base({ sil: silhouette("egg"), gaze: { yaw: 19.97, pitch: 26.01, roll: -17.1 }, split: 11.07, eyes: pair(0.164, 0.385) }),
  },

  {
    id: "hexagon",
    duration: 1.6,
    morph: 0.4,
    baseFace: false,
    baseBody: false,
    blinkIn: true,
    pose: () => base({ sil: silhouette("hexagon"), gaze: { yaw: 23.11, pitch: 24.42, roll: -13.3 }, split: 13.37, eyes: pair(0.177, 0.411) }),
  },

  {
    id: "play",
    duration: 2,
    morph: 0.5,
    baseFace: false,
    baseBody: false,
    blinkIn: true,
    pose: (t) => {
      // The triangle stays nearly still while the bouquet crosses it, right to left.
      const fade = clamp(t / 0.35) * clamp((2.2 - t) / 0.5);
      return base({
        sil: spinningTriangle(0),
        gaze: { yaw: 12, pitch: -8, roll: -6 },
        split: 15,
        eyes: pair(0.18, 0.34),
        arcs: SWOOSH.map((s, i) => ({ id: `sw${i}`, seed: { ...s, cx: 0.45 - t * 0.42 }, t, opacity: fade })),
      });
    },
  },

  {
    id: "orbit",
    duration: 3.4,
    // the body has relaxed from the triangle to the ball at 1.6 + 0.9
    minDuration: 2.5,
    morph: 0.6,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    pose: (t) => {
      // Measured spin: a 0.35 s ramp, then 1.25 turns/s anticlockwise.
      const ramp = easings.easeInOutCubic(clamp(t / 0.35));
      const rot = -TAU * 1.25 * t * ramp;
      const back = easings.easeInOutCubic(clamp((t - 1.6) / 0.9));
      const tri = spinningTriangle(rot);
      const ball = circle(1, { rot });
      const sil = {
        radii: tri.radii.map((r, i) => r + (ball.radii[i] - r) * back),
        rot,
        cx: tri.cx * (1 - back),
        cy: tri.cy * (1 - back),
        sx: 1,
        sy: 1,
      };
      const fade = clamp(t / 0.8) * clamp((3.6 - t) / 0.9);
      return base({
        sil,
        // the eyes race round the sphere about three times faster than the silhouette
        gaze: { yaw: REST_GAZE.yaw + Math.sin(t * 6.5) * 65 * (1 - back), pitch: -4 + back * 32, roll: -13 },
        eyes: pair(0.18, 0.34 + back * 0.07),
        // the rings enter one by one over 0.8 s
        arcs: RINGS.map((s, i) => ({ id: `rg${i}`, seed: s, t, opacity: fade * clamp((t - i * 0.13) / 0.3) })),
      });
    },
  },

  {
    // The one state not read from the video: a chosen interface transition that borrows orbit's rings, cut short
    // (1.3 s, half the rings, no triangle). Both flags true: the chosen shape morphs in and the rest face carries
    // the gaze from the start. Not in SEQUENCE.
    id: "swirl",
    duration: 1.3,
    minDuration: 1.3,
    morph: 0.3,
    baseFace: true,
    baseBody: true,
    blinkIn: true,
    pose: (t) =>
      base({
        arcs: RINGS.slice(0, 3).map((s, i) => ({
          id: `sw${i}`,
          seed: s,
          t,
          opacity: clamp((t - i * 0.06) / 0.14) * clamp((1.22 - t) / 0.34),
        })),
      }),
  },

  {
    id: "burst",
    duration: 2.6,
    // the body is whole again at 1.7 + 0.7
    minDuration: 2.4,
    morph: 0.4,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    pose: (t) => {
      // Measured collapse: 1.0 to 0.166 in 0.7 s, ease-out, no bounce.
      const collapse = 1 - 0.834 * easings.easeOutQuint(clamp(t / 0.7));
      const regrow = easings.easeOutQuint(clamp((t - 1.7) / 0.7));
      return base({
        sil: circle(collapse + (1 - collapse) * regrow),
        eyeAlpha: clamp((t - 1.85) / 0.4),
        dots: particles(t, 1),
        dotsBehind: true,
      });
    },
  },

  {
    id: "comet",
    duration: 2.4,
    // the dot is whole again at 1.85 + 0.6 = 2.45, 0.05 s after the video's cut
    minDuration: 2.4,
    morph: 0.45,
    baseFace: false,
    baseBody: false,
    blinkIn: false,
    pose: (t) => {
      const collapse = 1 - (1 - COMET_DOT) * easings.easeOutQuint(clamp(t / 0.55));
      const regrow = easings.easeOutQuint(clamp((t - 1.85) / 0.6));
      const fade = clamp((t - 0.15) / 0.25) * clamp((1.95 - t) / 0.3);
      return base({
        // the dot drifts 0.035 down and back up (a measured wobble)
        sil: circle(collapse + (1 - collapse) * regrow, { cy: Math.sin(clamp(t / 1.7) * Math.PI) * 0.035 }),
        eyeAlpha: clamp((t - 2) / 0.35),
        arcs: COMET_RIBBONS.map((s, i) => ({ id: `cm${i}`, seed: s, t, opacity: fade })),
      });
    },
  },
];

export const STATE_BY_ID = new Map(STATES.map((s) => [s.id, s]));

// The local time at which each state reads best: the pose of the thumbnails. Deterministic.
export const POSES = {
  idle: 1,
  thinking: 1.1,
  wink: 0.8,
  wide: 0.8,
  alert: 0.75,
  notify: 0.9,
  exclaim: 0.8,
  sleep: 0.45,
  egg: 0.8,
  hexagon: 0.8,
  play: 0.9,
  orbit: 1.2,
  swirl: 0.5,
  burst: 0.45,
  comet: 1.15,
};

// The reading order of the full sequence, after the reference video.
export const SEQUENCE = ["idle", "thinking", "wink", "wide", "alert", "notify", "exclaim", "sleep", "egg", "hexagon", "play", "orbit", "burst", "comet"];
