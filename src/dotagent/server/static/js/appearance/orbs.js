// A theme's orb (item 126), drawn as T3 Code draws it (apps/web/src/components/settings/ThemePreviewCircles.tsx and
// packages/shared/src/themePreview.ts; see LICENSE): a ball of the theme's page colour, kept near true for its scheme,
// lit by the accent from one corner and tinted by the action colour from the other, the light blurred and spread a
// little past the ball's edge. The colours come in as CSS colours and are mixed by the browser.

import { el } from "../dom.js";

const SPECS = {
  light: {
    base: "#ffffff",
    accent: { at: "72% 22%", middle: 72, middleAt: 28, end: 58 },
    action: { at: "18% 82%", start: 45, end: 55 },
  },
  dark: {
    base: "#09090b",
    accent: { at: "28% 78%", middle: 62, middleAt: 28, end: 58 },
    action: { at: "82% 18%", start: 45, end: 55 },
  },
};

/** The orb of `colours` (`{ canvas, accent, action }`) in `scheme` ("light" or "dark"). */
export function orb(colours, scheme) {
  const spec = SPECS[scheme];
  const light = el("span", { class: "orb-light" });
  light.style.backgroundColor = `color-mix(in oklab, ${colours.canvas} 80%, ${spec.base})`;
  light.style.backgroundImage = [
    `radial-gradient(circle at ${spec.accent.at} in oklab, ${colours.accent} 0%, ` +
      `color-mix(in oklab, ${colours.accent} ${spec.accent.middle}%, transparent) ${spec.accent.middleAt}%, ` +
      `transparent ${spec.accent.end}%)`,
    `radial-gradient(circle at ${spec.action.at} in oklab, ` +
      `color-mix(in oklab, ${colours.action} ${spec.action.start}%, transparent) 0%, transparent ${spec.action.end}%)`,
  ].join(", ");
  return el("span", { class: "orb", "data-scheme": scheme, "aria-hidden": "true" }, light);
}
