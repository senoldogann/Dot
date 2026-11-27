// What Dot says of what its tasks used (item 1403): the days of Settings › Models › Usage (`GET /api/usage`) and the
// "so far" of a task that works (`GET /runs/{id}/stats`), in words. Dot records time, tokens and the windows of the Claude
// plan; the dollars beside the tokens are an estimate from a public price list (item 1856), "unknown" where a model has
// no price there, never a guess. Pure, so that it runs under Node without a page.

import { compact, duration, } from "./dom.js";
import { t, tCount } from "./i18n.js";

// The statuses of a task that is still at work (store/models.py WORKING_STATUSES).
/** What a task with nothing to tell reads as. */
const NONE = t("usagewords.none");

const WORKING = new Set(["queued", "running", "blocked", "ending"]);
const WEEKDAYS = [t("usagewords.sun"), t("usagewords.mon"), t("usagewords.tue"), t("usagewords.wed"), t("usagewords.thu"), t("usagewords.fri"), t("usagewords.sat")];
const MONTHS = [t("usagewords.jan"), t("usagewords.feb"), t("usagewords.mar"), t("usagewords.apr"), t("usagewords.may"), t("usagewords.jun"), t("usagewords.jul"), t("usagewords.aug"), t("usagewords.sep"), t("usagewords.oct"), t("usagewords.nov"), t("usagewords.dec")];
const DAY_MS = 86_400_000;
// The windows of the plan by the kind its steps record, as the plan's own pages name them.
const WINDOWS = {
  five_hour: t("usagewords.5HourWindow"),
  seven_day: t("usagewords.7DayWindow"),
  seven_day_opus: t("usagewords.7DayOpusWindow"),
  seven_day_sonnet: t("usagewords.7DaySonnetWindow"),
  overage: t("usagewords.extraUsage"),
};

/** Whether a task with this `status` is still at work. */
export function isWorking(status) {
  return WORKING.has(status);
}

/** The local day of `now` (seconds) as `YYYY-MM-DD`, the form of the days `GET /api/usage` gives. */
export function localDay(now) {
  const date = new Date(now * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** A `YYYY-MM-DD` day at midnight UTC, in milliseconds: the days are told apart by their numbers, not by a time zone. */
function stamp(day) {
  const [year, month, date] = day.split("-").map(Number);
  return Date.UTC(year, month - 1, date);
}

/** "Today", "Yesterday", or "Sun 22 Nov", for `day` when `today` is the local day now. */
export function dayLabel(day, today) {
  const apart = Math.round((stamp(today) - stamp(day)) / DAY_MS);
  if (apart === 0) {
    return t("usagewords.today");
  }
  if (apart === 1) {
    return t("usagewords.yesterday");
  }
  const date = new Date(stamp(day));
  return `${WEEKDAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** Dollars as a line says them: "$12.34", "$0.42", "<$0.01" for less than a cent, "$0" for none. */
export function dollars(usd) {
  if (usd === 0) {
    return "$0";
  }
  return usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

/**
 * What an estimate of `GET /api/usage` or `GET /runs/{id}/stats` (`CostEstimate`, item 1856) says, or null without one:
 * "~$0.42", "~$0.42 + plan" when part ran on a subscription, "on a plan" when all of it did, and "cost unknown" when
 * part of it was of a model with no known price (or before any prices were fetched).
 */
export function costWords(cost) {
  if (cost === null || cost === undefined) {
    return null;
  }
  if (cost.usd === null) {
    return cost.prices_at === null ? t("usagewords.costUnknownNoPricesYet") : t("usagewords.costUnknown");
  }
  const plan = cost.plan_models.length > 0;
  if (plan && cost.usd === 0) {
    return t("usagewords.onAPlan");
  }
  return `~${dollars(cost.usd)}${plan ? " + plan" : ""}`;
}

/** The estimates `costs` summed as one (`usd` null when any is), or null when none is there. */
export function sumCosts(costs) {
  const present = costs.filter((cost) => cost !== null && cost !== undefined);
  if (present.length === 0) {
    return null;
  }
  return {
    usd: present.some((cost) => cost.usd === null) ? null : present.reduce((sum, cost) => sum + cost.usd, 0),
    priced_usd: present.reduce((sum, cost) => sum + cost.priced_usd, 0),
    unknown_models: [...new Set(present.flatMap((cost) => cost.unknown_models))],
    plan_models: [...new Set(present.flatMap((cost) => cost.plan_models))],
    prices_at: present[0].prices_at,
  };
}

/** One day's figures in a line: "12 tasks · 1.2M in · 30k out · ~$0.42 · 3h 20m", or "No tasks". */
export function dayLine(day) {
  if (day.runs === 0) {
    return t("usagewords.noTasks");
  }
  const cost = costWords(day.cost);
  const tail = cost === null ? "" : ` · ${cost}`;
  return t("usagewords.dayLine", { tasks: tCount(day.runs, "usagewords.countTask", "usagewords.countTasks"), input: compact(day.input_tokens), output: compact(day.output_tokens), tail, time: duration(day.minutes * 60) });
}

/** The figures of all `days` in one line, as a day's, or "No tasks in the last 7 days". */
export function totalLine(days) {
  const total = days.reduce(
    (sum, day) => ({
      runs: sum.runs + day.runs,
      input_tokens: sum.input_tokens + day.input_tokens,
      output_tokens: sum.output_tokens + day.output_tokens,
      minutes: sum.minutes + day.minutes,
    }),
    { runs: 0, input_tokens: 0, output_tokens: 0, minutes: 0, cost: null },
  );
  total.cost = sumCosts(days.filter((day) => day.runs > 0).map((day) => day.cost));
  return total.runs === 0 ? t("usagewords.noTasksInTheLast", { part: tCount(days.length, "usagewords.countDay", "usagewords.countDays") }) : dayLine(total);
}

/** How much of the bar a day fills, 0 to 100: its tokens against the day of the most; a day with some use shows at
 * least a sliver, and a day with none nothing. */
export function barShare(day, days) {
  const tokens = (one) => one.input_tokens + one.output_tokens;
  const most = Math.max(0, ...days.map(tokens));
  if (tokens(day) === 0 || most === 0) {
    return 0;
  }
  return Math.max(2, Math.round((tokens(day) / most) * 100));
}

/** The name of a window of the plan by its `kind`: "5-hour window"; a kind not known is its own words. */
export function windowName(kind) {
  return WINDOWS[kind] ?? kind.replaceAll("_", " ");
}

/** The share of a window used, as a whole percent from 0 to 100, or null when the plan did not say. */
export function windowShare(window) {
  return window.utilization === null || window.utilization === undefined ? null : Math.round(Math.min(1, Math.max(0, window.utilization)) * 100);
}

/** How long before `now` a moment was, "just now", "12m ago", "3h ago", "2d ago" (both in seconds). */
export function sinceWords(at, now) {
  const seconds = Math.max(0, now - at);
  if (seconds < 45) {
    return t("usagewords.justNow");
  }
  if (seconds < 3600) {
    return t("usagewords.roundMAgo", { round: Math.round(seconds / 60) });
  }
  if (seconds < 86_400) {
    return t("usagewords.roundHAgo", { round: Math.round(seconds / 3600) });
  }
  return t("usagewords.roundDAgo", { round: Math.round(seconds / 86_400) });
}

/**
 * What a window of the plan says in a line, from `window` (`{ kind, status, utilization, resets_at, seen_at }`) and `now`:
 * "62% used · resets in 1h 20m · seen 12m ago". A window that was said not to allow a call says so first; one whose reset
 * has passed says the figure is from before it; one that did not say how much is used says that. `seen_at` is left out
 * where the window is of a task at work (it has none).
 */
export function windowLine(window, now) {
  const share = windowShare(window);
  const reset = window.resets_at === null || window.resets_at === undefined ? null : window.resets_at;
  const parts = [];
  if (window.status === "rejected" && (reset === null || reset > now)) {
    parts.push(t("usagewords.noCallIsAllowedUntil"));
  }
  parts.push(share === null ? t("usagewords.howMuchIsUsedWas") : t("usagewords.shareUsedValue", { share, value: reset !== null && reset <= now ? t("usagewords.beforeItReset") : "" }));
  if (reset !== null) {
    parts.push(reset > now ? t("usagewords.resetsInDuration", { duration: duration(reset - now) }) : t("usagewords.hasResetSince"));
  }
  if (window.seen_at !== null && window.seen_at !== undefined) {
    parts.push(t("usagewords.seenSincewords", { sinceWords: sinceWords(window.seen_at, now) }));
  }
  return parts.join(" · ");
}

const FIRST_STEP_CAUSES = {
  new_chat: t("usagewords.itsFirstStepWasCold"),
  prefix_changed: t("usagewords.itsFirstStepWasCold2"),
  after_pause: t("usagewords.itsFirstStepWasCold3"),
  unexplained: t("usagewords.itsFirstStepWasCold4"),
};
const COLD_STEP_CAUSES = { tool_group: t("usagewords.aToolGroupTurnedOn"), compaction: t("usagewords.aSummaryOfTheChat"), other: t("usagewords.noKnownCause") };

/**
 * What the service's cache did for a task (item 1742), from `cache` of `GET /runs/{id}/stats`, as a line: why its first
 * step was cold, if it was, and how many later steps were read at full price and after what. "None" when the task
 * found its prompt in the cache from the first step to the last, or measured no step.
 */
export function cacheWords(cache) {
  if (cache === null || cache === undefined) {
    return NONE;
  }
  const parts = [];
  const first = FIRST_STEP_CAUSES[cache.first_cause];
  if (first !== undefined) {
    parts.push(first);
  }
  if (cache.changed.length > 0) {
    parts.push(t("usagewords.changedList", { names: cache.changed.slice(0, 3).join(", "), more: cache.changed.length > 3 ? t("usagewords.andValueMore", { value: cache.changed.length - 3 }) : "" }));
  }
  if (cache.cold_steps.length > 0) {
    const causes = [...new Set(cache.cold_steps.map((step) => COLD_STEP_CAUSES[step.cause]))].join(", ");
    parts.push(t("usagewords.partReadAtFullPrice", { part: tCount(cache.cold_steps.length, "usagewords.countLaterStep", "usagewords.countLaterSteps"), causes }));
  }
  return parts.length === 0 ? NONE : parts.join(" · ");
}

/**
 * What a task has used so far, from the answer of `GET /runs/{id}/stats` (`costs`): `{ line, window }`, or null when the
 * task is not at work (the card then shows what it cost, as it did). `line` is its time, the tokens in and out and its
 * steps ("2h 10m · 1.2M in · 30k out · 41 steps"); `window` the window of the plan its newest step recorded, in a line
 * ("5-hour window: 62% used · resets in 1h 20m"), or null for a task that is not on the plan.
 */
export function usedSoFar(costs, now) {
  if (!isWorking(costs.status)) {
    return null;
  }
  const stats = costs.stats;
  const line = t("usagewords.durationCompactInCompact2Out", { duration: duration(costs.minutes * 60), compact: compact(stats.input_tokens), compact2: compact(stats.output_tokens), part: tCount(stats.steps, "usagewords.countStep", "usagewords.countSteps") });
  const window = costs.plan_window ?? null;
  return { line, window: window === null ? null : `${windowName(window.kind)}: ${windowLine({ ...window, seen_at: null }, now)}` };
}

// The sources of a task as the owner reads them, for the caps of each and the costliest tasks (item 1731).
const SOURCES = {
  ui: t("usagewords.yourChatsAndTasks"),
  schedule: t("usagewords.schedules"),
  initiative: t("usagewords.dotSOwnWork"),
  review: t("usagewords.codeReviews"),
  delegate: t("usagewords.helpers"),
};

/** The name of a task's `source`: "Schedules"; a source not known is its own word. */
export function sourceName(source) {
  return SOURCES[source] ?? source.replaceAll("_", " ");
}

/** The caps of `[budget]` Settings shows, each with its name, in the order they are set (item 1731). */
export const CAPS = [
  ["run_tokens", t("usagewords.oneTaskAndItsHelpers")],
  ["day_tokens", t("usagewords.allTasksOfADay")],
  ["ui_day_tokens", t("usagewords.uiADay", { ui: SOURCES.ui })],
  ["schedule_day_tokens", t("usagewords.scheduleADay", { schedule: SOURCES.schedule })],
  ["initiative_day_tokens", t("usagewords.initiativeADay", { initiative: SOURCES.initiative })],
  ["review_day_tokens", t("usagewords.reviewADay", { review: SOURCES.review })],
  ["delegate_day_tokens", t("usagewords.delegateADay", { delegate: SOURCES.delegate })],
];

/** Millions of tokens as the cap's box shows them: 500_000_000 is "500", 0 is "0" (no cap). */
export function capMillions(tokens) {
  return String(Math.round(tokens / 1_000_000));
}

/** The tokens of what the cap's box holds in millions, or null for what is not a whole number of 0 or more. */
export function capTokens(text) {
  const trimmed = text.trim();
  return /^\d+$/.test(trimmed) ? Number(trimmed) * 1_000_000 : null;
}

/**
 * A task against its caps, from `budget` of `GET /runs/{id}/stats` (`RunBudget`), or null without one: what it and its
 * helpers used against its cap, and what tasks like it usually use ("With helpers 12.0M of its 500.0M cap · usually
 * 2.3M, 9 in 10 under 26.0M").
 */
export function budgetLine(budget) {
  if (budget === null || budget === undefined) {
    return null;
  }
  const cost = costWords(budget.spent_cost);
  const spent = `${compact(budget.spent)}${cost === null ? "" : ` (${cost})`}`;
  const used = budget.cap > 0 ? t("usagewords.withHelpersSpentOfIts", { spent, compact: compact(budget.cap) }) : t("usagewords.withHelpersSpentNoCap", { spent });
  const usual = budget.estimate === null ? t("usagewords.tooFewTasksLikeIt") : t("usagewords.usuallyCompact9In10", { compact: compact(budget.estimate.median), compact2: compact(budget.estimate.p90) });
  return `${used} · ${usual}`;
}

/** Today's tokens against the day's cap, from `GET /api/budget`: "Today 1.2M of 4000.0M", or "Today 1.2M, no cap". */
export function todayLine(view) {
  return view.caps.day_tokens > 0 ? t("usagewords.todayCompactOfCompact2", { compact: compact(view.today), compact2: compact(view.caps.day_tokens) }) : t("usagewords.todayCompactNoCap", { compact: compact(view.today) });
}

/** One of the tasks that used the most: "Helpers · claude_code:sonnet · 371 steps · 209.8M · on a plan". */
export function topRunLine(run) {
  const cost = costWords(run.cost);
  return `${sourceName(run.source)} · ${run.model} · ${tCount(run.steps, "usagewords.countStep", "usagewords.countSteps")} · ${compact(run.tokens)}${cost === null ? "" : ` · ${cost}`}`;
}
