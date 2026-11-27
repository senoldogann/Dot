// The context meter of a run's composer (item 222): a ring of how much of the run's context budget its
// last step filled, and on a click what that is: the tokens the step sent the model against the budget past which Dot
// summarizes the older part of the run, the model's whole window, and why the budget is smaller and where it is set
// (item 239), the context size chosen for the model in Settings › Models (item 250), and a link to that setting of the
// model (item 396). The run's scaffold event gives the model, the budget, the window and the size, each step's assistant
// event what its prompt held; nothing is asked of the model.

import { compact, el, svg } from "./dom.js";
import { icon } from "./icons.js";
import { anchoredPopover } from "./popover.js";
import { WHOLE_RUN, providerOf } from "./providers.js";
import { t, uiLocale } from "./i18n.js";

// Past this share of the budget the ring says the run is near its next summary.
const NEAR_SHARE = 0.8;

/** A count written the way the interface's language writes numbers. */
function number(value) {
  return value.toLocaleString(uiLocale());
}

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
  const popover = anchoredPopover({ anchor: element, frame, label: t("contextmeter.chatSize"), build });

  function percent() {
    return Math.round((used / budget) * 100);
  }

  function build() {
    return [
      el(
        "div",
        { class: "context-meter-head" },
        el("span", { class: "context-meter-title" }, t("contextmeter.chatSize")),
        el("span", { class: "context-meter-figure" }, `${percent()}% · ${compact(used)} / ${compact(budget)}`),
      ),
      el(
        "p",
        { class: "context-meter-text" },
        summariseAt === null
          ? t("contextmeter.lastStepUsedPastBudget", { used: number(used), budget: number(budget) })
          : t("contextmeter.lastStepUsedPastSummarise", { used: number(used), summariseAt: number(summariseAt), budget: number(budget) }),
      ),
      modelWindow === null ? null : el("p", { class: "context-meter-text" }, t("contextmeter.modelCanTakeIn", { window: number(modelWindow) })),
      // Why the budget is smaller than the window, and where it is set (items 239, 250 and 396).
      chosen === null
        ? el(
            "p",
            { class: "context-meter-text" },
            t("contextmeter.eachStepMakesTheModel"),
            modelWindow !== null && budget < Math.round(modelWindow * 0.75)
              ? t("contextmeter.limitBelowWindow", { budget: number(budget) })
              : t("contextmeter.theLimitIsThreeQuarters"),
          )
        : el(
            "p",
            { class: "context-meter-text" },
            t("contextmeter.eachStepMakesTheModel"),
            t("contextmeter.chosenSizeSetsLimit", { chosen: number(chosen) }),
          ),
      chosen === null
        ? el(
            "details",
            { class: "error-details" },
            el("summary", null, t("contextmeter.details")),
            el("p", { class: "small muted" }, t("contextmeter.inTheSettingsFile"), el("code", { class: "mono" }, "[agent] context_tokens"), t("contextmeter.setsOneNumberForEvery")),
          )
        : null,
      // OpenCode's agent and Claude Code keep their own windows (items 283 and 1013), and a window its provider does not
      // say takes no size.
      model === null || modelWindow === null || WHOLE_RUN.has(providerOf(model))
        ? null
        : el("a", { class: "context-meter-link", href: sizeSetting(model) }, icon("sliders"), t("contextmeter.changeTheChatSize")),
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
    const words = t("contextmeter.chatSizePercentUsedCompact", { percent: percent(), compact: compact(used), compact2: compact(budget) });
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
