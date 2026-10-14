// The bill of a run: what it cost and where it went, from `GET /runs/{id}/stats` (store.stats.RunCosts). A section of
// the side panel's Run tab, under the details, which already say the minutes, the steps and the tokens; its list says
// where the minutes went (the model, the summaries, the tools, the gate) and what the tokens, the compactions and the
// delegated runs came to. `paint(costs)` takes the route's answer as it is; the section says "Counting…" until the
// first one.

import { compact, duration, el } from "./dom.js";
import { panelSection } from "./panels.js";

const HINTS = {
  model: "The seconds the model took to answer: its steps and its side calls together",
  summaries: "The model calls that compacted the context, and their share of the run",
  tools: "The seconds the tool calls took, the gate included",
  gate: "The part of the tool seconds spent waiting for your approval or your answer",
  steps: "The model turns of the run",
  input: "The tokens sent to the model in all its calls, and the share the provider served from its cache",
  output: "The tokens the model wrote",
  compactions: "How often the context was summarised to fit the budget",
  searches: "The web searches that came back, by the engine that answered (the anthropic rung costs one cent a search)",
  delegated: "The runs this run delegated and how long it waited for them",
};

function cell(label, value, hint) {
  return [el("dt", null, label), el("dd", { title: hint }, value)];
}

function share(part, whole) {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : "–";
}

/** Seconds spent on one thing and their share of the run: "2m 10s · 35%", or "None". */
function spent(seconds, total) {
  return seconds <= 0 ? "None" : `${duration(seconds)} · ${share(seconds, total)}`;
}

export function createCostCard() {
  // Label and value side by side, as the details above them are.
  const grid = el("dl", { class: "facts" });
  const body = el("div", null, el("div", { class: "hint" }, "Counting…"));
  const card = panelSection({ id: "run-cost", title: "Cost", actions: null, body });
  card.element.classList.add("cost-card");
  card.element.setAttribute("aria-label", "Cost of this run");

  function paint(costs) {
    const stats = costs.stats;
    const time = stats.time;
    const total = costs.minutes * 60;
    const cached = time.cached_share === null ? "" : ` · ${Math.round(time.cached_share * 100)}% cached`;
    const perHundred = stats.compaction.per_100_steps === null ? "" : ` · ${stats.compaction.per_100_steps.toFixed(1)} per 100 steps`;
    const compactions = stats.compaction.compactions === 0 ? "None" : `${stats.compaction.compactions}${perHundred}`;
    const delegated = stats.delegation.runs === 0 ? "None" : `${stats.delegation.runs} · ${duration(stats.delegation.seconds)} waited`;
    const searches = stats.searches.length === 0 ? "None" : stats.searches.map((entry) => `${entry.searches} ${entry.engine}`).join(" · ");
    grid.replaceChildren(...[
      cell("Model", spent(time.model_seconds, total), HINTS.model),
      cell("Summaries", spent(time.compaction_seconds, total), HINTS.summaries),
      cell("Tools", spent(costs.tool_seconds, total), HINTS.tools),
      cell("Waiting for you", spent(costs.waited_s, total), HINTS.gate),
      cell("Steps", String(stats.steps), HINTS.steps),
      cell("Input tokens", `${compact(stats.input_tokens)}${cached}`, HINTS.input),
      cell("Output tokens", compact(stats.output_tokens), HINTS.output),
      cell("Compactions", compactions, HINTS.compactions),
      cell("Delegated runs", delegated, HINTS.delegated),
      cell("Searches", searches, HINTS.searches),
    ].flat());
    body.replaceChildren(grid);
  }

  return { element: card.element, paint };
}
