// The context meter of a run's composer (item 222): a ring of how much of the run's context budget its
// last step filled, and on a click what that is: the tokens the step sent the model against the budget past which Dot
// summarizes the older part of the run, the model's whole window, and why the budget is smaller and where it is set
// (item 239), the context size chosen for the model in Settings › Models (item 250), and a link to that setting of the
// model (item 396). The run's scaffold event gives the model, the budget, the window and the size, each step's assistant
// event what its prompt held; nothing is asked of the model.

import { compact, el, svg } from "./dom.js";
import { icon } from "./icons.js";
import { anchoredPopover } from "./popover.js";
import { providerOf } from "./providers.js";

// Past this share of the budget the ring says the run is near its next summary.
const NEAR_SHARE = 0.8;

/** The address of Settings › Models that shows `model`'s context size (item 396). */
function sizeSetting(model) {
  return `#/settings/models?model=${encodeURIComponent(model)}`;
}

/**
 * The meter for the composer whose box is `frame` (where its panel opens). Returns `element` (a button, hidden until the
 * meter knows the budget and a step's prompt), `setBudget({ model, contextTokens, window, chosen, summariseAt })` (the
 * scaffold event's; `model` is the run's model, `window` is null when the provider does not say, `chosen` when no size
 * was chosen for the model, and `summariseAt`, the prompt past which the run summarizes, for a run of before item 250),
 * `setUsed(tokens)` (what a step sent) and `dispose()`.
 */
export function createContextMeter({ frame }) {
  let model = null;
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
  const popover = anchoredPopover({ anchor: element, frame, label: "Chat size", build });

  function percent() {
    return Math.round((used / budget) * 100);
  }

  function build() {
    return [
      el(
        "div",
        { class: "context-meter-head" },
        el("span", { class: "context-meter-title" }, "Chat size"),
        el("span", { class: "context-meter-figure" }, `${percent()}% · ${compact(used)} / ${compact(budget)}`),
      ),
      el(
        "p",
        { class: "context-meter-text" },
        summariseAt === null
          ? `The last step used ${used.toLocaleString("en-GB")}. Past ${budget.toLocaleString("en-GB")}, Dot sums up the older part of the chat to make room.`
          : `The last step used ${used.toLocaleString("en-GB")}. Past ${summariseAt.toLocaleString("en-GB")}, Dot sums up the older part of the chat, so the chat stays within its limit of ${budget.toLocaleString("en-GB")}.`,
      ),
      modelWindow === null ? null : el("p", { class: "context-meter-text" }, `The model can take in ${modelWindow.toLocaleString("en-GB")} at once.`),
      // Why the budget is smaller than the window, and where it is set (items 239, 250 and 396).
      chosen === null
        ? el(
            "p",
            { class: "context-meter-text" },
            "Each step makes the model read the whole chat again, so a shorter chat is answered sooner and costs less. ",
            modelWindow !== null && budget < Math.round(modelWindow * 0.75)
              ? `The limit, ${budget.toLocaleString("en-GB")}, is set below the most the model can take in: a longer chat makes every step slower to answer. Settings › Models lets you set a larger size for a model.`
              : "The limit is three quarters of the most the model can take in. Settings › Models lets you set a size for a model.",
          )
        : el(
            "p",
            { class: "context-meter-text" },
            "Each step makes the model read the whole chat again, so a shorter chat is answered sooner and costs less. ",
            `The size you chose for this model in Settings › Models, ${chosen.toLocaleString("en-GB")}, sets the limit, up to three quarters of what the model can take in.`,
          ),
      chosen === null
        ? el(
            "details",
            { class: "error-details" },
            el("summary", null, "Details"),
            el("p", { class: "small muted" }, "In the settings file, ", el("code", { class: "mono" }, "[agent] context_tokens"), " sets one number for every model."),
          )
        : null,
      // OpenCode's agent keeps its own window (item 283), and a window its provider does not say takes no size.
      model === null || modelWindow === null || providerOf(model) === "opencode"
        ? null
        : el("a", { class: "context-meter-link", href: sizeSetting(model) }, icon("sliders"), "Change the chat size"),
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
    const words = `Chat size: ${percent()}% used, ${compact(used)} of ${compact(budget)}`;
    element.setAttribute("aria-label", words);
    element.title = words;
    if (popover.isOpen()) {
      popover.repaint();
    }
  }

  return {
    element,
    setBudget({ model: runModel, contextTokens, window: windowTokens, chosen: chosenTokens, summariseAt: summariseTokens }) {
      model = runModel;
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
