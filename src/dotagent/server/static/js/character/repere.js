// Ported from bloub: src/bot/repere.ts. The frame of everything the engine renders.

// Radius of the ball at rest, in viewBox units: the `scale` passed to the engine. Everything else in this folder is
// a fraction of it, so the measurements taken from the reference video do not depend on the displayed size.
export const RADIUS = 100;

// Half side of the displayed viewBox. The margin beyond the radius houses the rings: the orbit's rings and the
// comet's swoosh rise to 1.4 radii (140); the `RINGS` and `SWOOSH` tables of decor.js keep them under 158.
export const HALF_VIEWBOX = 158;
