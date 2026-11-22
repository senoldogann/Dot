// The head of Plugins, Skills and Connectors (items 542, 545, 946 and 1336): a line that says what the page holds, then
// its search with its filters and buttons beside it on one row, on the page's own ground. The page's name is in the
// bar's breadcrumb (views/settings.js); the three open with this head, so that they read as one page.

import { el } from "./dom.js";
import { icon } from "./icons.js";

/**
 * The head of one of the three pages: `line`, the `search` input (made by the tab, which keeps it while it paints
 * again) and `controls` (the tab's filters and buttons, beside the search; empty for none). Returns `element`.
 */
export function customizeHead({ line, search, controls }) {
  const element = el(
    "section",
    { class: "customize-hero" },
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
