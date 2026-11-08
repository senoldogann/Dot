// The arithmetic of the full-screen viewer's zoom (item 1056, viewer.js), kept apart from the page so that it is tested
// alone: the scales it allows, the view kept within the content, and a zoom about a point that stays where it is.

export const MIN_SCALE = 1;
export const MAX_SCALE = 5;

/**
 * The view moved back within the content: zoomed, its edges never leave the stage's (`width` by `height`); at the
 * fitted size it stands still.
 * @param {{ scale: number, x: number, y: number }} view
 * @param {number} width
 * @param {number} height
 */
export function clamped(view, width, height) {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale));
  return {
    scale,
    x: Math.min(0, Math.max(width * (1 - scale), view.x)),
    y: Math.min(0, Math.max(height * (1 - scale), view.y)),
  };
}

/**
 * The view zoomed to `scale` about the point (`px`, `py`) of the stage, which stays where it is on the screen.
 * @param {{ scale: number, x: number, y: number }} view
 * @param {number} scale
 * @param {number} px
 * @param {number} py
 */
export function zoomedAbout(view, scale, px, py) {
  const ratio = scale / view.scale;
  return { scale, x: px - (px - view.x) * ratio, y: py - (py - view.y) * ratio };
}


/**
 * The scale a pinch on a page full screen comes to (item 1056): the page measures its fingers in its own frame, which
 * the viewer scales, so the ratio it reports (now to the start) shrinks as the view grows; the fingers' own ratio is the
 * reported one times the scale now over the scale at the start, which is the scale the pinch started at times it.
 * @param {number} reported the ratio of the fingers' distance now to at the start, in the frame's coordinates
 * @param {number} current the view's scale now
 */
export function pinchedScale(reported, current) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, reported * current));
}
