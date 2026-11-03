// The head of each tab of Customize (items 542, 545 and 946): its title over a line that says what the tab holds, then
// the tab's search with its filters and buttons beside it on one row, on the page's own ground. Plugins, Skills and
// Connectors all open with it, so that the three read as one page.

import { el } from "./dom.js";
import { icon } from "./icons.js";

/**
 * The head of a Customize tab: `title`, `line`, the `search` input (made by the tab, which keeps it while it paints
 * again) and `controls` (the tab's filters and buttons, beside the search; empty for none). Returns `element`.
 */
export function customizeHead({ title, line, search, controls }) {
  const element = el(
    "section",
    { class: "customize-hero" },
    el("h2", { class: "customize-hero-title" }, title),
    el("p", { class: "customize-hero-line" }, line),
    el(
      "div",
      { class: "customize-hero-bar" },
      el("label", { class: "customize-find" }, icon("search"), search),
      controls.length === 0 ? null : el("div", { class: "customize-hero-controls" }, controls),
    ),
  );
  return { element };
}
