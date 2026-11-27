// The bill of a run: what it cost and where it went, from `GET /runs/{id}/stats` (store.stats.RunCosts). A section of
// the side panel's Run tab, under its More (item 452), the details saying the minutes and the tokens; its list says
// where the minutes went (the model, the summaries, the tools, the gate) and what the tokens, the compactions and the
// delegated runs came to. A task that is still at work says first what it has used so far, its time, its tokens and its
// steps, and the window of the Claude plan its newest step saw (item 1403): the owner sets no ceiling, so what is
// spent is what there is to read; the dollars are an estimate (item 1856). `paint(costs)` takes the route's answer as it is; the section says "Counting…" until
// the first one.

import { compact, duration, el } from "./dom.js";
import { panelSection } from "./panels.js";
import { budgetLine, cacheWords, costWords, usedSoFar } from "./usagewords.js";
import { t } from "./i18n.js";

const HINTS = {
  sofar: t("cost.whatThisTaskHasUsed"),
  window: t("cost.howFullTheWindowOf"),
  budget: t("cost.theTokensOfThisTask"),
  model: t("cost.theSecondsTheModelTook"),
  summaries: t("cost.theTimeSpentSummingUp"),
  tools: t("cost.theSecondsTheActionsTook"),
  gate: t("cost.thePartOfThatTime"),
  steps: t("cost.howManyTimesDotWent"),
  input: t("cost.whatWasSentToThe"),
  cache: t("cost.whyTheServiceDidNot"),
  output: t("cost.whatTheModelWrote"),
  dollars: t("cost.anEstimateOfThisTask"),
  compactions: t("cost.howOftenTheOlderPart"),
  searches: t("cost.theWebSearchesThatCame"),
  delegated: t("cost.theTasksThisTaskHanded"),
};

function cell(label, value, hint) {
  return [el("dt", null, label), el("dd", { title: hint }, value)];
}

function share(part, whole) {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : "–";
}

/** What a figure of nothing reads as; a line of it is left out (item 452). */
const NONE = t("cost.none");

/** Seconds spent on one thing and their share of the run: "2m 10s · 35%", or none. */
function spent(seconds, total) {
  return seconds <= 0 ? NONE : `${duration(seconds)} · ${share(seconds, total)}`;
}

export function createCostCard() {
  // Label and value side by side, as the details above them are.
  const grid = el("dl", { class: "facts" });
  const body = el("div", null, el("div", { class: "hint" }, t("cost.counting")));
  const card = panelSection({ id: "run-cost", title: t("cost.cost"), actions: null, body });
  card.element.classList.add("cost-card");
  card.element.setAttribute("aria-label", t("cost.costOfThisTask"));
  // The figures the grid was drawn from.
  let drawn = null;

  function paint(costs) {
    const stats = costs.stats;
    const time = stats.time;
    const total = costs.minutes * 60;
    const cached = time.cached_share === null ? "" : t("cost.roundCached", { round: Math.round(time.cached_share * 100) });
    const perHundred = stats.compaction.per_100_steps === null ? "" : t("cost.tofixedPer100Steps", { toFixed: stats.compaction.per_100_steps.toFixed(1) });
    const compactions = stats.compaction.compactions === 0 ? NONE : `${stats.compaction.compactions}${perHundred}`;
    const delegated = stats.delegation.runs === 0 ? NONE : t("cost.runsDurationWaited", { runs: stats.delegation.runs, duration: duration(stats.delegation.seconds) });
    const searches = stats.searches.length === 0 ? NONE : stats.searches.map((entry) => `${entry.searches} ${entry.engine}`).join(" · ");
    // A figure of none says nothing worth a line (item 452).
    const facts = [
      [t("cost.model"), spent(time.model_seconds, total), HINTS.model],
      [t("cost.summaries"), spent(time.compaction_seconds, total), HINTS.summaries],
      [t("cost.actions"), spent(costs.tool_seconds, total), HINTS.tools],
      [t("cost.waitingForYou"), spent(costs.waited_s, total), HINTS.gate],
      [t("cost.steps"), String(stats.steps), HINTS.steps],
      [t("cost.usageIn"), `${compact(stats.input_tokens)}${cached}`, HINTS.input],
      [t("cost.cacheMisses"), cacheWords(costs.cache), HINTS.cache],
      [t("cost.usageOut"), compact(stats.output_tokens), HINTS.output],
      [t("cost.cost2"), costWords(costs.cost) ?? NONE, HINTS.dollars],
      [t("cost.chatSummaries"), compactions, HINTS.compactions],
      [t("cost.tasksGivenToHelpers"), delegated, HINTS.delegated],
      [t("cost.searches"), searches, HINTS.searches],
    ].filter(([, value]) => value !== NONE);
    // What a task at work has used so far, above the details (item 1403); none once it has ended.
    const sofar = usedSoFar(costs, Date.now() / 1000);
    // The task against its cap and what tasks like it usually use (item 1731), from its first step on.
    const budget = budgetLine(costs.budget);
    // Read every ten seconds while the run works; drawn again only when a figure changed (item 310).
    const from = JSON.stringify([sofar, budget, facts]);
    if (from === drawn) {
      return;
    }
    drawn = from;
    grid.replaceChildren(...facts.flatMap(([label, value, hint]) => cell(label, value, hint)));
    const head =
      sofar === null
        ? null
        : el(
            "div",
            { class: "cost-so-far", "aria-live": "off" },
            el("div", { class: "cost-so-far-label" }, t("cost.usedSoFar")),
            el("div", { class: "cost-so-far-line", title: HINTS.sofar }, sofar.line),
            sofar.window === null ? null : el("div", { class: "cost-so-far-window", title: HINTS.window }, sofar.window),
          );
    const limit = budget === null ? null : el("div", { class: "cost-budget hint", title: HINTS.budget }, budget);
    // The native replaceChildren writes a null as the word: a part that is not there is left out first.
    body.replaceChildren(...[head, limit, grid].filter((part) => part !== null));
  }

  return { element: card.element, paint };
}
