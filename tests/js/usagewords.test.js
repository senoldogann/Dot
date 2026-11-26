// What Dot says of what its tasks used (usagewords.js, item 1403): pure, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  barShare,
  budgetLine,
  cacheWords,
  capMillions,
  capTokens,
  costWords,
  dayLabel,
  dayLine,
  isWorking,
  localDay,
  sinceWords,
  sourceName,
  sumCosts,
  todayLine,
  topRunLine,
  totalLine,
  usedSoFar,
  windowLine,
  windowName,
  windowShare,
} from "../../src/dotagent/server/static/js/usagewords.js";

const NOW = 1_800_000_000;

/** A day of `GET /api/usage`. */
function day(name, runs, input, output, minutes) {
  return { day: name, runs, steps: runs * 10, input_tokens: input, output_tokens: output, minutes };
}

test("a day is today, yesterday, or its weekday and date", () => {
  assert.equal(dayLabel("2026-11-24", "2026-11-24"), "Today");
  assert.equal(dayLabel("2026-11-23", "2026-11-24"), "Yesterday");
  assert.equal(dayLabel("2026-11-22", "2026-11-24"), "Sun 22 Nov");
  // Across a month and a year: told apart by the numbers of the days, whatever the time zone.
  assert.equal(dayLabel("2026-12-31", "2027-01-01"), "Yesterday");
  assert.equal(dayLabel("2026-12-30", "2027-01-01"), "Wed 30 Dec");
  assert.equal(dayLabel("2024-02-29", "2024-03-01"), "Yesterday");
});

test("the local day of a moment is the form the usage's days come in", () => {
  assert.match(localDay(NOW), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(localDay(new Date(2026, 10, 5, 12, 0).getTime() / 1000), "2026-11-05");
});

test("a day says its tasks, the tokens in and out and its time in one line, and a day with none says so", () => {
  assert.equal(dayLine(day("2026-11-24", 12, 1_234_567, 30_400, 200)), "12 tasks · 1.2M in · 30k out · 3h 20m");
  assert.equal(dayLine(day("2026-11-24", 1, 950, 12, 1.5)), "1 task · 950 in · 12 out · 1m 30s");
  assert.equal(dayLine(day("2026-11-23", 0, 0, 0, 0)), "No tasks");
});

test("the week is one line of the days together", () => {
  const days = [day("2026-11-23", 2, 1000, 100, 30), day("2026-11-24", 3, 2_000_000, 50_000, 90)];
  assert.equal(totalLine(days), "5 tasks · 2.0M in · 50k out · 2h 00m");
  assert.equal(totalLine([day("2026-11-23", 0, 0, 0, 0), day("2026-11-24", 0, 0, 0, 0)]), "No tasks in the last 2 days");
});

test("a day's bar is its tokens against the busiest day, a sliver for a day with some, nothing for a day with none", () => {
  const days = [day("a", 1, 1000, 0, 1), day("b", 1, 90_000, 10_000, 1), day("c", 0, 0, 0, 0), day("d", 1, 100, 0, 1)];
  assert.equal(barShare(days[1], days), 100);
  assert.equal(barShare(days[0], days), 2);
  assert.equal(barShare(days[2], days), 0);
  assert.equal(barShare(days[3], days), 2);
  assert.equal(barShare(days[2], [days[2]]), 0);
});

test("a window of the plan has a name by its kind, and a kind not known its own words", () => {
  assert.equal(windowName("five_hour"), "5-hour window");
  assert.equal(windowName("seven_day_opus"), "7-day Opus window");
  assert.equal(windowName("seven_day_haiku"), "seven day haiku");
});

test("the share of a window used is a whole percent, kept between none and all, or null when the plan did not say", () => {
  assert.equal(windowShare({ utilization: 0.624 }), 62);
  assert.equal(windowShare({ utilization: 1.4 }), 100);
  assert.equal(windowShare({ utilization: -0.2 }), 0);
  assert.equal(windowShare({ utilization: null }), null);
  assert.equal(windowShare({}), null);
});

test("how long ago a moment was is in the biggest unit that fits", () => {
  assert.equal(sinceWords(NOW - 10, NOW), "just now");
  assert.equal(sinceWords(NOW - 12 * 60, NOW), "12m ago");
  assert.equal(sinceWords(NOW - 3 * 3600, NOW), "3h ago");
  assert.equal(sinceWords(NOW - 2 * 86_400 - 100, NOW), "2d ago");
  assert.equal(sinceWords(NOW + 50, NOW), "just now");
});

test("a window says how full it is, when it resets and when it was seen, and what is not known", () => {
  const seen = NOW - 12 * 60;
  const window = { kind: "five_hour", status: "allowed", utilization: 0.62, resets_at: NOW + 4800, seen_at: seen };
  assert.equal(windowLine(window, NOW), "62% used · resets in 1h 20m · seen 12m ago");
  // The reset has passed: the figure is from before it.
  assert.equal(windowLine({ ...window, resets_at: NOW - 60 }, NOW), "62% used, before it reset · has reset since · seen 12m ago");
  // The plan did not say how much, nor when.
  assert.equal(windowLine({ ...window, utilization: null, resets_at: null }, NOW), "How much is used was not said · seen 12m ago");
  // A window that allows no call says so first, until it resets.
  assert.equal(windowLine({ ...window, status: "rejected", utilization: 1 }, NOW), "No call is allowed until it resets · 100% used · resets in 1h 20m · seen 12m ago");
  assert.equal(windowLine({ ...window, status: "rejected", utilization: 1, resets_at: NOW - 5 }, NOW), "100% used, before it reset · has reset since · seen 12m ago");
});

test("only a task at work says what it has used so far", () => {
  assert.equal(isWorking("running"), true);
  assert.equal(isWorking("queued"), true);
  assert.equal(isWorking("blocked"), true);
  assert.equal(isWorking("ending"), true);
  assert.equal(isWorking("done"), false);
  assert.equal(isWorking("cancelled"), false);
  const costs = {
    status: "running",
    minutes: 130,
    stats: { input_tokens: 1_200_000, output_tokens: 30_000, steps: 41 },
    plan_window: { kind: "five_hour", status: "allowed", utilization: 0.62, resets_at: NOW + 4800, seen_at: NOW - 5 },
  };
  assert.deepEqual(usedSoFar(costs, NOW), {
    line: "2h 10m · 1.2M in · 30k out · 41 steps",
    window: "5-hour window: 62% used · resets in 1h 20m",
  });
  // A task that did not run on the plan has no window; one that ended says nothing of "so far".
  assert.deepEqual(usedSoFar({ ...costs, plan_window: null }, NOW), { line: "2h 10m · 1.2M in · 30k out · 41 steps", window: null });
  assert.deepEqual(usedSoFar({ ...costs, plan_window: undefined, stats: { ...costs.stats, steps: 1 } }, NOW), {
    line: "2h 10m · 1.2M in · 30k out · 1 step",
    window: null,
  });
  assert.equal(usedSoFar({ ...costs, status: "done" }, NOW), null);
});

test("a task against its cap says what it and its helpers used and what tasks like it usually use (item 1731)", () => {
  const budget = { estimate: { runs: 12, median: 2_300_000, p90: 26_000_000 }, spent: 12_000_000, cap: 500_000_000, source: "ui", day: 0, day_cap: 0, source_day: 0, source_day_cap: 0 };
  assert.equal(budgetLine(budget), "With helpers 12.0M of its 500.0M cap · usually 2.3M, 9 in 10 under 26.0M");
  assert.equal(budgetLine({ ...budget, cap: 0, estimate: null }), "With helpers 12.0M, no cap · too few tasks like it to say what it usually uses");
  assert.equal(budgetLine(undefined), null);
});

test("a cap's box holds whole millions, and anything else is no cap value", () => {
  assert.equal(capMillions(500_000_000), "500");
  assert.equal(capTokens(" 750 "), 750_000_000);
  assert.equal(capTokens("0"), 0);
  assert.equal(capTokens("-1"), null);
  assert.equal(capTokens("1.5"), null);
  assert.equal(capTokens(""), null);
});

test("today against the day's cap, and one of the costliest tasks, in a line", () => {
  assert.equal(todayLine({ caps: { day_tokens: 4_000_000_000 }, today: 1_200_000 }), "Today 1.2M of 4000.0M");
  assert.equal(todayLine({ caps: { day_tokens: 0 }, today: 1_200_000 }), "Today 1.2M, no cap");
  assert.equal(topRunLine({ source: "delegate", model: "claude_code:sonnet", steps: 371, tokens: 209_800_000 }), "Helpers · claude_code:sonnet · 371 steps · 209.8M");
  assert.equal(sourceName("work_order"), "work order");
});

test("the cache says why a task's first step was cold and what its cold later steps came after, and nothing when it found its prompt", () => {
  const report = { first_cause: "warm", changed: [], cold_steps: [] };
  assert.equal(cacheWords(null), "None");
  assert.equal(cacheWords(report), "None");
  assert.equal(cacheWords({ ...report, first_cause: "new_chat" }), "its first step was cold: a new chat");
  assert.equal(
    cacheWords({ ...report, first_cause: "prefix_changed", changed: ["system:Skills", "tools:browser", "system:Team", "system:Environment"] }),
    "its first step was cold: the instructions changed since the chat's last task · changed: system:Skills, tools:browser, system:Team and 1 more",
  );
  const steps = [
    { step: 3, uncached_tokens: 90_000, cause: "tool_group" },
    { step: 9, uncached_tokens: 50_000, cause: "tool_group" },
    { step: 12, uncached_tokens: 40_000, cause: "compaction" },
  ];
  assert.equal(
    cacheWords({ ...report, cold_steps: steps }),
    "3 later steps read at full price after a tool group turned on, a summary of the chat",
  );
});

test("an estimate in dollars is said with what ran on a plan, and a model with no price makes it unknown (item 1856)", () => {
  const cost = (usd, plan, unknown) => ({ usd, priced_usd: usd ?? 0.5, unknown_models: unknown, plan_models: plan, prices_at: 100 });
  assert.equal(costWords(null), null);
  assert.equal(costWords(cost(0.4236, [], [])), "~$0.42");
  assert.equal(costWords(cost(0.004, [], [])), "~<$0.01");
  assert.equal(costWords(cost(0, [], [])), "~$0");
  assert.equal(costWords(cost(12.5, ["claude_code:opus"], [])), "~$12.50 + plan");
  assert.equal(costWords(cost(0, ["claude_code:opus"], [])), "on a plan");
  assert.equal(costWords(cost(null, [], ["openai:new"])), "cost unknown");
  assert.equal(costWords({ ...cost(null, [], ["openai:new"]), prices_at: null }), "cost unknown (no prices yet)");
  assert.equal(sumCosts([cost(1, [], []), undefined, cost(2, [], [])]).usd, 3);
  assert.equal(sumCosts([cost(1, [], []), cost(null, [], ["x"])]).usd, null);
  assert.equal(sumCosts([null, undefined]), null);
  const day = { day: "2026-11-25", runs: 2, steps: 3, input_tokens: 1_200_000, output_tokens: 30_000, minutes: 200, cost: cost(0.42, [], []) };
  assert.equal(dayLine(day), "2 tasks · 1.2M in · 30k out · ~$0.42 · 3h 20m");
  assert.match(totalLine([day, day]), / · ~\$0\.84 · /);
  const budget = { cap: 0, spent: 2_000_000, spent_cost: cost(1.5, [], []), estimate: null };
  assert.match(budgetLine(budget), /^With helpers 2\.0M \(~\$1\.50\), no cap/);
});
