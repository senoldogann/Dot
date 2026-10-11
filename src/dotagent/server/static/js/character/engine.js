// Ported from bloub: src/bot/engine.ts. An engine without a clock: `sample(t)` is a pure function of time, so pause,
// resume, slow motion and a jump to any date give the same picture, and the rendering is testable without a DOM.
// Every external change enters through a dated setter, never through a variable read during `sample`.
import { arcRender } from "./decor.js";
import { blendExpression } from "./expressions.js";
import { eyeOffset } from "./eyefit.js";
import { blinkScale, eyePoses, liveliness } from "./face.js";
import { clamp, easings, lerp, r2 } from "./math.js";
import { blend, capsulePath, closedPath, radiusAtAngle, toPoints } from "./shape.js";
import { STATE_BY_ID } from "./states.js";

// Where the character looks when something outside drives it (the pointer, the composer). `yaw` and `pitch` are
// absolute directions that replace the pose's as `mix` rises; `wander` is what remains of the automatic drift;
// `spin` is a turn to make on the way, in degrees, melted toward 0 on arrival.
const NO_LOOK = { yaw: 0, pitch: 0, mix: 0, spin: 0, wander: 1 };

const lerpLook = (a, b, t) => ({
  yaw: lerp(a.yaw, b.yaw, t),
  pitch: lerp(a.pitch, b.pitch, t),
  mix: lerp(a.mix, b.mix, t),
  spin: lerp(a.spin, b.spin, t),
  wander: lerp(a.wander, b.wander, t),
});

const lerpEye = (a, b, t) => ({
  w: lerp(a.w, b.w, t),
  h: lerp(a.h, b.h, t),
  open: lerp(a.open, b.open, t),
  tilt: lerp(a.tilt ?? 0, b.tilt ?? 0, t),
});

// Interpolation of two poses. The decor crosses in opacity, not in geometry.
function blendPose(a, b, t) {
  const out = 1 - t;
  return {
    sil: blend(a.sil, b.sil, t),
    offX: lerp(a.offX, b.offX, t),
    offY: lerp(a.offY, b.offY, t),
    gaze: {
      yaw: lerp(a.gaze.yaw, b.gaze.yaw, t),
      pitch: lerp(a.gaze.pitch, b.gaze.pitch, t),
      roll: lerp(a.gaze.roll, b.gaze.roll, t),
    },
    split: lerp(a.split, b.split, t),
    eyes: [lerpEye(a.eyes[0], b.eyes[0], t), lerpEye(a.eyes[1], b.eyes[1], t)],
    eyeAlpha: lerp(a.eyeAlpha, b.eyeAlpha, t),
    bodyAlpha: lerp(a.bodyAlpha, b.bodyAlpha, t),
    dots: [...a.dots.map((d) => ({ ...d, opacity: d.opacity * out })), ...b.dots.map((d) => ({ ...d, opacity: d.opacity * t }))],
    arcs: [
      ...a.arcs.map((r) => ({ ...r, id: `a${r.id}`, opacity: r.opacity * out })),
      ...b.arcs.map((r) => ({ ...r, id: `b${r.id}`, opacity: r.opacity * t })),
    ],
    // the pastille belongs to one of the two states; it does not blend
    notif: t < 0.5 ? a.notif : b.notif,
    dotsBehind: t < 0.5 ? a.dotsBehind : b.dotsBehind,
  };
}

// The morph's length when the body's shape or the rest expression changes.
export const SHAPE_MORPH = 0.45;
// The gaze's catch-up toward its target: shorter than SHAPE_MORPH, a following gaze looks attentive, not viscous.
export const LOOK_MORPH = 0.24;

export class CharacterEngine {
  // `scale` is the ball's radius at rest in viewBox units; `shape` a radii array of skins.js or null; `expression`
  // an entry of expressions.js or null.
  constructor(scale = 100, initial = "idle", shape = null, expression = null) {
    this.scale = scale;
    this.cur = initial;
    this.prev = null;
    // The frozen departure pose, set only when a state change arrives while a fade is under way (see setState).
    this.frozenStart = null;
    this.tCur = 0;
    this.tPrev = 0;
    this.blinkAt = -10;
    this.pts = [];
    this.shape = shape;
    this.shapePrev = null;
    this.shapeAt = -10;
    this.expr = expression;
    this.exprPrev = null;
    this.exprAt = -10;
    this.look = NO_LOOK;
    this.lookPrev = NO_LOOK;
    this.lookAt = -10;
    this.lookMorph = LOOK_MORPH;
  }

  get state() {
    return this.cur;
  }

  // The rest expression; like the shape, it glides to the new value instead of jumping.
  setExpression(expression, now = 0) {
    if (expression === this.expr) return;
    this.exprPrev = this.expr;
    this.expr = expression;
    this.exprAt = now;
  }

  exprAtTime(now) {
    const to = this.expr;
    const from = this.exprPrev;
    if (!to || !from) return to;
    const k = (now - this.exprAt) / SHAPE_MORPH;
    if (k >= 1) return to;
    return blendExpression(from, to, easings.easeOutQuint(clamp(k)));
  }

  // The chosen shape replaces the body on the rest states only (`baseBody`): elsewhere the silhouette is the
  // animation. The change is a morph of radii, since every shape is sampled at the same angles.
  setShape(radii, now = 0) {
    if (radii === this.shape) return;
    this.shapePrev = this.shape;
    this.shape = radii;
    this.shapeAt = now;
  }

  // `shapePrev` is never cleared at the morph's end: `sample` stays a pure function of time, so a past date gives
  // the intermediate picture again.
  shapeAtTime(now) {
    const to = this.shape;
    const from = this.shapePrev;
    if (!to || !from) return to;
    const k = (now - this.shapeAt) / SHAPE_MORPH;
    if (k >= 1) return to;
    const t = easings.easeOutQuint(clamp(k));
    return to.map((r, i) => lerp(from[i] ?? r, r, t));
  }

  // A new gaze target, `null` to return to the state's. It restarts from the current value, not the previous
  // target: called at every pointer move, restarting from the old target would make the gaze step back first.
  setLook(look, now, morph = LOOK_MORPH) {
    // A non-finite target is refused and the last one kept: a NaN set once would spread to every frame.
    if (look && !Number.isFinite(look.yaw + look.pitch + look.mix + look.spin + look.wander)) return;
    this.lookPrev = this.lookAtTime(now);
    this.look = look ?? NO_LOOK;
    this.lookAt = now;
    this.lookMorph = morph;
  }

  lookAtTime(now) {
    const k = (now - this.lookAt) / this.lookMorph;
    if (k >= 1) return this.look;
    return lerpLook(this.lookPrev, this.look, easings.easeOutQuint(clamp(k)));
  }

  posed(def, t, shape, expr) {
    let pose = def.pose(t);
    if (def.baseBody && shape) pose = { ...pose, sil: { ...pose.sil, radii: shape } };
    if (def.baseFace && expr) pose = { ...pose, gaze: expr.gaze, split: expr.split, eyes: expr.eyes };
    return pose;
  }

  // The eyes' offset at `now` for a state, in ball radii: read from the eyefit table on the bounds of each morph
  // (shape and expression) and interpolated with the morph's own curve, never recomputed per frame.
  offsetAtTime(now, state) {
    const onAxis = (start, length, a, b) => {
      if (a === b) return b;
      const k = (now - start) / length;
      if (k >= 1) return b;
      const t = easings.easeOutQuint(clamp(k));
      return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t) };
    };
    const byShape = (radii) =>
      onAxis(
        this.exprAt,
        SHAPE_MORPH,
        eyeOffset(radii, state, this.exprPrev?.id ?? null),
        eyeOffset(radii, state, this.expr?.id ?? null),
      );
    return onAxis(this.shapeAt, SHAPE_MORPH, byShape(this.shapePrev), byShape(this.shape));
  }

  // Starts over on `id` with no previous state, as a new engine set on it: the rewind of a sequence.
  reset(id, now) {
    this.cur = id;
    this.prev = null;
    this.frozenStart = null;
    this.tCur = now;
    this.tPrev = now;
    this.blinkAt = -10;
  }

  // The origin of the fade under way: the frozen pose if any, else the state left, evaluated at its own elapsed
  // time, so still animating.
  origin(now, shape, expr) {
    if (this.frozenStart) return this.frozenStart;
    if (!this.prev) return null;
    const prevDef = STATE_BY_ID.get(this.prev);
    return this.posed(prevDef, Math.max(0, now - this.tPrev), shape, expr);
  }

  // The composite pose at `now`, fade included: what `sample` blends before the life and gaze layer.
  composedPose(now) {
    const def = STATE_BY_ID.get(this.cur);
    const shape = this.shapeAtTime(now);
    const expr = this.exprAtTime(now);
    const pose = this.posed(def, Math.max(0, now - this.tCur), shape, expr);
    const since = now - this.tCur;
    if (since >= def.morph) return pose;
    const origin = this.origin(now, shape, expr);
    if (!origin) return pose;
    return blendPose(origin, pose, easings.easeOutQuint(clamp(since / def.morph)));
  }

  // A dated state change. The engine keeps one slot of history, so a change during a fade would replace the blend's
  // origin by the full pose of the state left instead of the half-blended picture on screen (measured: a 35.9 px
  // jump against 8.0 px of normal motion); the current composite pose is frozen then, and only then.
  setState(id, now) {
    if (id === this.cur) return;
    const morph = STATE_BY_ID.get(this.cur).morph;
    const midFade = this.prev !== null && now - this.tCur < morph;
    this.frozenStart = midFade ? this.composedPose(now) : null;
    this.prev = this.cur;
    this.tPrev = this.tCur;
    this.cur = id;
    this.tCur = now;
    // In the video every change of shape is hidden by a blink.
    if (STATE_BY_ID.get(id)?.blinkIn) this.blinkAt = now;
  }

  // The frame at `now`: {bodyPath, bodyAlpha, eyes: [{d, matrix, alpha}], dots, dotsBehind, arcs, notif, notch}.
  sample(now) {
    const R = this.scale;
    const def = STATE_BY_ID.get(this.cur);
    const shape = this.shapeAtTime(now);
    const expr = this.exprAtTime(now);
    let pose = this.posed(def, Math.max(0, now - this.tCur), shape, expr);
    let offset = this.offsetAtTime(now, this.cur);

    // The previous state is never purged: `since < def.morph` ignores it once the fade has passed, and forgetting
    // it would make the engine unreplayable.
    const since = now - this.tCur;
    const origin = since < def.morph ? this.origin(now, shape, expr) : null;
    if (origin) {
      // The ratio is clamped: a date before the state change would give a negative ratio the ease-out extrapolates.
      const ratio = easings.easeOutQuint(clamp(since / def.morph));
      pose = blendPose(origin, pose, ratio);
      if (this.prev) {
        const before = this.offsetAtTime(now, this.prev);
        offset = { x: lerp(before.x, offset.x, ratio), y: lerp(before.y, offset.y, ratio) };
      }
    }

    // Life at rest.
    const alive = pose.eyeAlpha > 0.01;
    const look = this.lookAtTime(now);
    const life = liveliness(now, { wander: alive ? look.wander : 0, blink: alive });
    const gaze = {
      // The target replaces the pose's direction rather than adding to it; the drift is added after the mix so it
      // survives a turned head; the roll follows nothing (the head's -13° is the signature).
      yaw: lerp(pose.gaze.yaw, look.yaw, look.mix) + life.dYaw - look.spin,
      pitch: lerp(pose.gaze.pitch, look.pitch, look.mix) + life.dPitch,
      roll: pose.gaze.roll + life.dRoll,
    };

    // A blink forced by the state change, on top of the calendar.
    const forced = clamp((now - this.blinkAt) / 0.2);
    const forcedLid = forced < 1 ? Math.abs(forced * 2 - 1) : 1;
    const lid = Math.min(life.lid, forcedLid);

    const offX = pose.offX + life.driftX;
    const offY = pose.offY + life.driftY;

    // The body.
    const sil = { ...pose.sil, cx: pose.sil.cx + offX, cy: pose.sil.cy + offY, sy: pose.sil.sy * life.breath };
    const bodyPath = closedPath(toPoints(sil, R, this.pts));

    // The eyes live on a sphere of radius 1; once the silhouette is not a circle they are drawn in to the real
    // radius in their direction, or the mask would cut them.
    const bodyRadius = (x, y) => radiusAtAngle(pose.sil.radii, Math.atan2(y, x) - pose.sil.rot);
    const eyes = [];
    if (pose.eyeAlpha > 0.01) {
      const poses = eyePoses(gaze, R, pose.split);
      for (let i = 0; i < 2; i++) {
        const e = poses[i];
        if (e.depth <= 0.02) continue;
        const cfg = pose.eyes[i];
        const fit = bodyRadius(e.x, e.y);
        // The eye's own tilt: the tangent frame composed with a rotation in the eye's plane.
        const phi = ((cfg.tilt ?? 0) * Math.PI) / 180;
        const cp = Math.cos(phi);
        const sp = Math.sin(phi);
        const ax = e.a * cp + e.c * sp;
        const ay = e.b * cp + e.d * sp;
        const cx2 = -e.a * sp + e.c * cp;
        const cy2 = -e.b * sp + e.d * cp;
        // The blink comes after all that: a vertical squash on screen, not along the capsule's axis.
        const k = blinkScale(Math.min(lid, cfg.open));
        eyes.push({
          d: capsulePath(cfg.w * R, cfg.h * R),
          matrix: `matrix(${r2(ax)},${r2(ay * k)},${r2(cx2)},${r2(cy2 * k)},${r2(e.x * fit + (offX + offset.x) * R)},${r2(e.y * fit + (offY + offset.y) * R)})`,
          alpha: pose.eyeAlpha * clamp(e.depth / 0.12),
        });
      }
    }

    // The decor.
    const dots = pose.dots
      .filter((p) => p.opacity > 0.01 && p.r > 0.0005)
      .map((p) => ({ ...p, x: (p.x + offX) * R, y: (p.y + offY) * R, r: p.r * R }));
    // the pastille sits on the contour, so it follows the shape too
    const nFit = pose.notif ? bodyRadius(pose.notif.x, pose.notif.y) : 1;
    const nx = pose.notif ? (pose.notif.x * nFit + offX) * R : 0;
    const ny = pose.notif ? (pose.notif.y * nFit + offY) * R : 0;
    const notif = pose.notif ? { x: nx, y: ny, r: pose.notif.r * R } : null;
    const notch = pose.notif ? { x: nx, y: ny, r: pose.notif.notch * R } : null;

    return {
      bodyPath,
      bodyAlpha: pose.bodyAlpha,
      eyes,
      dots,
      dotsBehind: pose.dotsBehind,
      // the states declare arcs in ball radii; the engine alone knows the viewBox's scale, so it traces them
      arcs: pose.arcs.filter((a) => a.opacity > 0.01).map((a) => arcRender(a.seed, a.t, R, a.id, a.opacity)),
      notif,
      notch,
    };
  }
}
