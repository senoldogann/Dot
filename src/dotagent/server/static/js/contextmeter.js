// The context meter of a run's composer (item 222), after T3 Code's: a ring of how much of the run's context budget its
// last step filled, and on a click what that is: the tokens the step sent the model against the budget past which Dot
// summarizes the older part of the run, and the model's whole window. The run's scaffold event gives the budget and the
// window, each step's assistant event what its prompt held; nothing is asked of the model.

import { compact, el, svg } from "./dom.js";
import { anchoredPopover } from "./popover.js";

// Past this share of the budget the ring says the run is near its next summary.
const NEAR_SHARE = 0.8;

/**
 * The meter for the composer whose box is `frame` (where its panel opens). Returns `element` (a button, hidden until the
 * meter knows the budget and a step's prompt), `setBudget(contextTokens, window)` (the scaffold event's; `window` is
 * null when the provider does not say), `setUsed(tokens)` (what a step sent) and `dispose()`.
 */
export function createContextMeter({ frame }) {
  let budget = null;
  let modelWindow = null;
  let used = null;
  const fill = svg("circle", { class: "context-ring-fill", cx: "9", cy: "9", r: "7", pathLength: "100", "stroke-dasharray": "0 100" });
  const ring = svg(
    "svg",
    { class: "context-ring", viewBox: "0 0 18 18", "aria-hidden": "true" },
    svg("circle", { class: "context-ring-track", cx: "9", cy: "9", r: "7", pathLength: "100" }),
    fill,
  );
  const element = el("button", { class: "context-meter", type: "button", hidden: true }, ring);
  // The popover opens and closes on the button's click itself.
  const popover = anchoredPopover({ anchor: element, frame, label: "Context", build });

  function percent() {
    return Math.round((used / budget) * 100);
  }

  function build() {
    return [
      el(
        "div",
        { class: "context-meter-head" },
        el("span", { class: "context-meter-title" }, "Context window"),
        el("span", { class: "context-meter-figure" }, `${percent()}% · ${compact(used)} / ${compact(budget)}`),
      ),
      el(
        "p",
        { class: "context-meter-text" },
        `The run's last step sent the model ${used.toLocaleString("en-GB")} tokens. Past ${budget.toLocaleString("en-GB")}, the run's budget, Dot summarizes its older part.`,
      ),
      modelWindow === null ? null : el("p", { class: "context-meter-text" }, `The model holds ${modelWindow.toLocaleString("en-GB")} tokens.`),
    ];
  }

  function paint() {
    if (budget === null || used === null) {
      element.hidden = true;
      return;
    }
    const share = used / budget;
    element.hidden = false;
    fill.setAttribute("stroke-dasharray", `${Math.min(100, Math.round(share * 100))} 100`);
    element.dataset.level = share >= 1 ? "full" : share >= NEAR_SHARE ? "near" : "room";
    const words = `Context: ${percent()}% of the run's budget, ${compact(used)} of ${compact(budget)} tokens`;
    element.setAttribute("aria-label", words);
    element.title = words;
    if (popover.isOpen()) {
      popover.repaint();
    }
  }

  return {
    element,
    setBudget(contextTokens, windowTokens) {
      budget = contextTokens;
      modelWindow = windowTokens;
      paint();
    },
    setUsed(tokens) {
      used = tokens;
      paint();
    },
    dispose() {
      popover.dispose();
    },
  };
}
