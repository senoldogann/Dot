// Ported from bloub: src/bot/expressions.ts. The face is two capsules, so everything plays on four levers: the head's
// orientation, the eyes' spread, their proportions and each eye's own tilt. The tilt is what allows anger and
// sadness, which want mirrored tilts (tops that converge or diverge), impossible with the head's roll alone. Only
// the rest state wears the expression; the video's expressive states (wink, wide eyes, notification) keep theirs.
// The ids are English here (bloub's are French); the numbers are bloub's.
import { EYE_H, EYE_SPLIT, EYE_W, REST_GAZE } from "./face.js";
import { lerp } from "./math.js";

// `tilt` in degrees, positive = the capsule's top leans right.
const eye = (w, h, tilt = 0, open = 1) => ({ w, h, tilt, open });

// Two identical eyes, mirrored tilts when `tilt` is given.
const pair = (w, h, tilt = 0, open = 1) => [eye(w, h, tilt, open), eye(w, h, -tilt, open)];

export const EXPRESSIONS = [
  // the pose read frame by frame on the reference video
  { id: "neutral", gaze: { ...REST_GAZE }, split: EYE_SPLIT, eyes: [eye(EYE_W, EYE_H), eye(EYE_W, EYE_H)] },
  { id: "attentive", gaze: { yaw: 4, pitch: 5, roll: -4 }, split: 16, eyes: pair(0.21, 0.44) },
  { id: "surprised", gaze: { yaw: 3, pitch: -3, roll: 0 }, split: 19, eyes: pair(0.45, 0.47) },
  { id: "excited", gaze: { yaw: 6, pitch: -14, roll: 0 }, split: 19.5, eyes: pair(0.4, 0.56, -10) },
  // eyes narrowed in an arc: the tops converge a little
  { id: "happy", gaze: { yaw: 5, pitch: 9, roll: 0 }, split: 17, eyes: pair(0.27, 0.17, 14) },
  { id: "laughing", gaze: { yaw: 4, pitch: 14, roll: 0 }, split: 18, eyes: pair(0.34, 0.13, 20) },
  // the tops converge hard toward the centre, the eyes narrowed
  { id: "angry", gaze: { yaw: 3, pitch: 7, roll: 0 }, split: 17, eyes: pair(0.34, 0.15, 30) },
  // the reverse: the tops diverge and the gaze falls
  { id: "sad", gaze: { yaw: 3, pitch: -13, roll: 0 }, split: 16, eyes: pair(0.22, 0.4, -28) },
  { id: "scared", gaze: { yaw: 2, pitch: -20, roll: 0 }, split: 20.5, eyes: pair(0.4, 0.6) },
  // one eye plainly more closed than the other
  { id: "suspicious", gaze: { yaw: 12, pitch: 6, roll: -6 }, split: 16, eyes: [eye(0.21, 0.4), eye(0.22, 0.15)] },
  // asymmetric on both axes: the narrowed eye is flat on purpose (ratio 1.6), or its tilt would not show
  { id: "confused", gaze: { yaw: -14, pitch: 3, roll: 8 }, split: 16.5, eyes: [eye(0.2, 0.44, -18), eye(0.28, 0.17, 14)] },
  // the head leans: the roll carries the curiosity
  { id: "curious", gaze: { yaw: 16, pitch: -9, roll: -15 }, split: 16.5, eyes: [eye(0.24, 0.46, -8), eye(0.2, 0.38, -8)] },
  { id: "proud", gaze: { yaw: 5, pitch: 17, roll: 0 }, split: 17, eyes: pair(0.3, 0.15, 18) },
  { id: "shy", gaze: { yaw: -19, pitch: -14, roll: -7 }, split: 14, eyes: pair(0.17, 0.3) },
  // horizontal slits and a gaze that drifts aside
  { id: "unimpressed", gaze: { yaw: -22, pitch: 2, roll: 0 }, split: 16, eyes: pair(0.3, 0.12) },
  // lids half down through `open`: the vertical squash on screen, the blink's mechanism
  { id: "sleepy", gaze: { yaw: 6, pitch: -9, roll: -3 }, split: 16, eyes: pair(0.2, 0.42, 0, 0.42) },
];

export const EXPRESSION_IDS = EXPRESSIONS.map((e) => e.id);
export const EXPRESSION_BY_ID = new Map(EXPRESSIONS.map((e) => [e.id, e]));
export const DEFAULT_EXPRESSION = "neutral";

const lerpEyeCfg = (a, b, t) => ({
  w: lerp(a.w, b.w, t),
  h: lerp(a.h, b.h, t),
  tilt: lerp(a.tilt ?? 0, b.tilt ?? 0, t),
  open: lerp(a.open, b.open, t),
});

// Interpolation of two expressions: the change glides.
export function blendExpression(a, b, t) {
  return {
    id: b.id,
    gaze: {
      yaw: lerp(a.gaze.yaw, b.gaze.yaw, t),
      pitch: lerp(a.gaze.pitch, b.gaze.pitch, t),
      roll: lerp(a.gaze.roll, b.gaze.roll, t),
    },
    split: lerp(a.split, b.split, t),
    eyes: [lerpEyeCfg(a.eyes[0], b.eyes[0], t), lerpEyeCfg(a.eyes[1], b.eyes[1], t)],
  };
}
