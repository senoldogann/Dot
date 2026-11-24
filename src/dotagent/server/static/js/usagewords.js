// What Dot says of what its tasks used (item 1403): the days of Settings › Models › Usage (`GET /api/usage`) and the
// "so far" of a task that works (`GET /runs/{id}/stats`), in words. Dot records time, tokens and the windows of the Claude
// plan, no price per model, so there is no money here. Pure, so that it runs under Node without a page.

import { compact, duration, plural } from "./dom.js";

// The statuses of a task that is still at work (store/models.py WORKING_STATUSES).
const WORKING = new Set(["queued", "running", "blocked", "ending"]);
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_MS = 86_400_000;
// The windows of the plan by the kind its steps record, as the plan's own pages name them.
const WINDOWS = {
  five_hour: "5-hour window",
  seven_day: "7-day window",
  seven_day_opus: "7-day Opus window",
  seven_day_sonnet: "7-day Sonnet window",
  overage: "Extra usage",
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
    return "Today";
  }
  if (apart === 1) {
    return "Yesterday";
  }
  const date = new Date(stamp(day));
  return `${WEEKDAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** One day's figures in a line: "12 tasks · 1.2M in · 30k out · 3h 20m", or "No tasks". */
export function dayLine(day) {
  if (day.runs === 0) {
    return "No tasks";
  }
  return `${plural(day.runs, "task", "tasks")} · ${compact(day.input_tokens)} in · ${compact(day.output_tokens)} out · ${duration(day.minutes * 60)}`;
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
    { runs: 0, input_tokens: 0, output_tokens: 0, minutes: 0 },
  );
  return total.runs === 0 ? `No tasks in the last ${plural(days.length, "day", "days")}` : dayLine(total);
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
    return "just now";
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m ago`;
  }
  if (seconds < 86_400) {
    return `${Math.round(seconds / 3600)}h ago`;
  }
  return `${Math.round(seconds / 86_400)}d ago`;
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
    parts.push("No call is allowed until it resets");
  }
  parts.push(share === null ? "How much is used was not said" : `${share}% used${reset !== null && reset <= now ? ", before it reset" : ""}`);
  if (reset !== null) {
    parts.push(reset > now ? `resets in ${duration(reset - now)}` : "has reset since");
  }
  if (window.seen_at !== null && window.seen_at !== undefined) {
    parts.push(`seen ${sinceWords(window.seen_at, now)}`);
  }
  return parts.join(" · ");
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
  const line = `${duration(costs.minutes * 60)} · ${compact(stats.input_tokens)} in · ${compact(stats.output_tokens)} out · ${plural(stats.steps, "step", "steps")}`;
  const window = costs.plan_window ?? null;
  return { line, window: window === null ? null : `${windowName(window.kind)}: ${windowLine({ ...window, seen_at: null }, now)}` };
}
