// The context meter of a run's composer (item 222), after T3 Code's: a ring of how much of the run's context budget its
// last step filled, and on a click what that is: the tokens the step sent the model against the budget past which Dot
// summarizes the older part of the run, the model's whole window, and why the budget is smaller and where it is set
// (item 239), and the context size chosen for the model in Settings › Models (item 250). The run's scaffold event gives
// the budget, the window and the size, each step's assistant event what its prompt held; nothing is asked of the model.

import { compact, el, svg } from "./dom.js";
import { anchoredPopover } from "./popover.js";

// Past this share of the budget the ring says the run is near its next summary.
const NEAR_SHARE = 0.8;

/**
 * The meter for the composer whose box is `frame` (where its panel opens). Returns `element` (a button, hidden until the
 * meter knows the budget and a step's prompt), `setBudget({ contextTokens, window, chosen, summariseAt })` (the scaffold
 * event's; `window` is null when the provider does not say, `chosen` when no size was chosen for the model, and
 * `summariseAt`, the prompt past which the run summarizes, for a run of before item 250), `setUsed(tokens)` (what a
 * step sent) and `dispose()`.
 */
export function createContextMeter({ frame }) {
  let budget = null;
  let modelWindow = null;
  let chosen = null;
  let summariseAt = null;
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
        summariseAt === null
          ? `The run's last step sent the model ${used.toLocaleString("en-GB")} tokens. Past ${budget.toLocaleString("en-GB")}, the run's budget, Dot summarizes its older part.`
          : `The run's last step sent the model ${used.toLocaleString("en-GB")} tokens. Past ${summariseAt.toLocaleString("en-GB")}, Dot summarizes its older part, so the run stays within its budget of ${budget.toLocaleString("en-GB")}.`,
      ),
      modelWindow === null ? null : el("p", { class: "context-meter-text" }, `The model holds ${modelWindow.toLocaleString("en-GB")} tokens.`),
      // Why the budget is smaller than the window, and where it is set (items 239 and 250).
      chosen === null
        ? el(
            "p",
            { class: "context-meter-text" },
            "Each step sends the model the whole run again, so a run kept within its budget is answered sooner and costs less. ",
            "Dot's default size sets the budget, and Settings › Models one model's own, up to three quarters of the model's window. ",
            el("span", { class: "faint" }, "In the config file: [agent] context_tokens."),
          )
        : el(
            "p",
            { class: "context-meter-text" },
            "Each step sends the model the whole run again, so a run kept within its budget is answered sooner and costs less. ",
            `The context size chosen for this model in Settings › Models, ${chosen.toLocaleString("en-GB")} tokens, sets the budget, up to three quarters of its window.`,
          ),
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
    setBudget({ contextTokens, window: windowTokens, chosen: chosenTokens, summariseAt: summariseTokens }) {
      budget = contextTokens;
      modelWindow = windowTokens;
      chosen = chosenTokens;
      summariseAt = summariseTokens;
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
