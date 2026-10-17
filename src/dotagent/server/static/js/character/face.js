// Ported from bloub: src/bot/face.ts. The eyes are painted on a sphere, not laid flat: the eye nearest the edge is
// 0.69 times the width of the other and 0.663 times its area, exactly the depth factor (z = 0.669) of a sphere point
// at that distance from the centre. So a real head orientation: each eye gets the sphere's tangent frame, projected
// orthographically; the compression and the tilt follow. The constants come from a fit of the model to the
// positions and sizes read frame by frame (residual about 1 px on a 190 px radius).
import { clamp, createRng, loopNoise } from "./math.js";

// Half the eyes' spread on the sphere, in degrees (about 31° in all).
export const EYE_SPLIT = 15.46;
// The eye at rest, in ball radii.
export const EYE_W = 0.186;
export const EYE_H = 0.412;

// The head at rest, fitted on the reference frames. yaw: positive looks right; pitch: positive looks up; roll: tilt.
export const REST_GAZE = { yaw: 28.49, pitch: 28.62, roll: -13 };

const deg = (d) => (d * Math.PI) / 180;

// Spins two vectors of an orthonormal frame in their common plane.
function spin(u, v, angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [
    [u[0] * c + v[0] * s, u[1] * c + v[1] * s, u[2] * c + v[2] * s],
    [v[0] * c - u[0] * s, v[1] * c - u[1] * s, v[2] * c - u[2] * s],
  ];
}

// The head's frame, then the two eyes'. Screen frame: x right, y down, z toward the viewer. Index 0 is the inner
// eye, 1 the outer. Each pose: {x, y, a, b, c, d (the 2x2 tangent matrix in SVG order), depth (z of the normal)}.
export function eyePoses(gaze, scale, split = EYE_SPLIT) {
  let f = [0, 0, 1];
  let right = [1, 0, 0];
  let down = [0, 1, 0];
  [f, right] = spin(f, right, deg(gaze.yaw));
  [down, f] = spin(down, f, deg(gaze.pitch));
  [right, down] = spin(right, down, deg(gaze.roll));
  const build = (side) => {
    const [ef, er] = spin(f, right, deg(split * side));
    return { x: ef[0] * scale, y: ef[1] * scale, a: er[0], b: er[1], c: down[0], d: down[1], depth: ef[2] };
  };
  return [build(-1), build(1)];
}

// Life at rest: a slow drift of the gaze, saccades, blinks. A pure function of time, so pause, resume and a jump to
// any date give the same picture. The values are offsets added to the current state's pose.
const BLINK_RNG = createRng(0x5eed);
// A blink calendar drawn once: deterministic and stateless. It covers about 15 minutes and then starts again, so a
// face shown for longer goes on blinking; its last blink ends before the time the calendar covers does.
const { BLINKS, BLINK_CYCLE } = (() => {
  const out = [];
  let t = 1.4;
  while (t < 900) {
    out.push(t);
    // 1.9 to 4.6 s between two blinks, a double blink now and then
    t += 1.9 + BLINK_RNG() * 2.7;
    if (BLINK_RNG() < 0.18) {
      out.push(t);
      t += 0.24;
    }
  }
  return { BLINKS: out, BLINK_CYCLE: t };
})();

// Measured: 1 to 2 frames at 10 fps.
const BLINK_DUR = 0.18;

function blinkLid(time) {
  const t = time % BLINK_CYCLE;
  for (let i = 0; i < BLINKS.length; i++) {
    const start = BLINKS[i];
    if (t < start) break;
    const k = (t - start) / BLINK_DUR;
    if (k >= 0 && k <= 1) return k < 0.45 ? 1 - k / 0.45 : (k - 0.45) / 0.55;
  }
  return 1;
}

export function liveliness(t, opt = {}) {
  const { wander = 1, blink = true, float = true } = opt;
  // Periods prime to each other: the drift never repeats to the eye.
  return {
    dYaw: (loopNoise(t, 11.3, 0.4) * 5.5 + loopNoise(t, 3.7, 2.1) * 1.6) * wander,
    dPitch: (loopNoise(t, 9.1, 1.3) * 4.2 + loopNoise(t, 4.3, 0.7) * 1.3) * wander,
    dRoll: loopNoise(t, 13.7, 3.2) * 2.2 * wander,
    lid: blink ? blinkLid(t) : 1,
    // At rest the video is nearly still: all the life is in the gaze and the blinks; just enough not to freeze.
    driftX: float ? loopNoise(t, 7.9, 1.9) * 0.006 : 0,
    driftY: float ? loopNoise(t, 5.3, 0.3) * 0.007 : 0,
    // The width is constant; only the height breathes, very lightly.
    breath: float ? 1 + Math.sin((t / 3.4) * Math.PI * 2) * 0.005 : 1,
  };
}

// A blink is a vertical squash in screen space about the eye's centre (the bbox width is kept, the height falls to
// about 0.35), not a shrink along the capsule's tilted axis: composed after the tangent matrix, on y only.
export function blinkScale(lid) {
  return 0.06 + 0.94 * clamp(lid);
}
