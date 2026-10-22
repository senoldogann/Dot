// The head of each tab of Customize (items 542 and 545): a card lit by the theme's accent, its title over a line that
// says what the tab holds, the tab's search and its controls under them, and a few marks scattered beside them, which a
// phone leaves out. Plugins, Skills and Connectors all open with it, so that the three read as one page.

import { el } from "./dom.js";
import { icon } from "./icons.js";

// The head shows this many marks at most.
const MARKS = 5;

/**
 * The head of a Customize tab: `title`, `line`, the `search` input (made by the tab, which keeps it while it paints
 * again) and `controls` (nodes under the search: the tab's filters and buttons; empty for none). Returns `element` and
 * `setMarks(nodes)`, which scatters the first marks beside the words once the tab has read what it shows.
 */
export function customizeHead({ title, line, search, controls }) {
  const marks = el("div", { class: "customize-hero-marks", "aria-hidden": "true" });
  const element = el(
    "section",
    { class: "customize-hero" },
    el(
      "div",
      { class: "customize-hero-main" },
      el("h2", { class: "customize-hero-title" }, title),
      el("p", { class: "customize-hero-line" }, line),
      el("label", { class: "customize-find" }, icon("search"), search),
      controls.length === 0 ? null : el("div", { class: "customize-hero-controls" }, controls),
    ),
    marks,
  );
  return {
    element,
    setMarks(nodes) {
      if (marks.childElementCount === 0) {
        marks.replaceChildren(...nodes.slice(0, MARKS).map((node) => el("span", { class: "customize-hero-mark" }, node)));
      }
    },
  };
}
