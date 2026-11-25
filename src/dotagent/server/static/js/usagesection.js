// What the tasks used in the last week, as a section of Settings › Models (item 1403): the windows of the Claude plan the
// tasks last saw, the whole week in a line and a row for each day with a bar of its tokens, from `GET /api/usage`; the
// tasks that used the most, and the caps of what they spend with today's tokens against them (item 1731,
// `GET`/`PUT /api/budget`). Dot holds no price per model, so it shows time, the words the models read and wrote, and how
// full the plan is: no money. Words in usagewords.js.

import { api, query } from "./api.js";
import { compact, el } from "./dom.js";
import { inlineError, inlineLoading, setCard, setRow, setSection } from "./panels.js";
import { toast, toastError } from "./ui.js";
import {
  barShare,
  CAPS,
  capMillions,
  capTokens,
  dayLabel,
  dayLine,
  localDay,
  sourceName,
  todayLine,
  topRunLine,
  totalLine,
  windowLine,
  windowName,
  windowShare,
} from "./usagewords.js";

const DAYS = 7;
const LEAD = `What your tasks used in the last ${DAYS} days: their time, the words the models read (in) and wrote (out), and how full your Claude plan is. Dot keeps no price for a model, so there is no money here.`;

/** A thin bar `share` percent full. */
function bar(share) {
  const fill = el("span", { class: "usage-bar-fill" });
  fill.style.setProperty("--share", `${share}%`);
  return el("div", { class: "usage-bar", "aria-hidden": "true" }, fill);
}

/** A window of the plan the tasks last saw: its name, how full it was and when it resets, with a bar when the plan said. */
function windowRow(window, now) {
  const share = windowShare(window);
  const row = setRow({ title: `Claude plan · ${windowName(window.kind)}`, lead: windowLine(window, now), body: share === null ? null : bar(share) });
  row.dataset.usageWindow = window.kind;
  return row;
}

/** A day: its name, what the tasks started on it used, and a bar of its tokens against the busiest day. */
function dayRow(day, days, today) {
  const row = setRow({ title: dayLabel(day.day, today), lead: dayLine(day), body: bar(barShare(day, days)) });
  row.dataset.usageDay = day.day;
  return row;
}

/** One of the tasks that used the most: the start of its goal, and its source, model, steps and tokens; it opens the task. */
function topRow(run) {
  const link = el("a", { href: `#/runs/${encodeURIComponent(run.run_id)}`, class: "usage-top-goal" }, run.goal === "" ? "Untitled task" : run.goal);
  const row = setRow({ title: link, lead: topRunLine(run) });
  row.dataset.usageTop = run.run_id;
  return row;
}

const CAPS_LEAD =
  "Millions of tokens, 0 for no cap. A task past a cap waits and asks you: Continue (as many again) or Stop; no answer stops it. It asks in every mode, Pay or send and free mode too.";

/** The caps of `view` (`GET /api/budget`), a box of millions for each, and today's tokens against them; a box changed is saved. */
function capRows(view, repaint) {
  const spent = new Map(view.by_source.map((entry) => [entry.source, entry.tokens]));
  const today = setRow({ title: todayLine(view), lead: view.by_source.map((entry) => `${sourceName(entry.source)} ${compact(entry.tokens)}`).join(" · ") });
  today.dataset.usageToday = "";
  const rows = CAPS.map(([key, label]) => {
    const input = el("input", { class: "input", type: "number", min: "0", step: "1", inputmode: "numeric", value: capMillions(view.caps[key]), "aria-label": `${label}, millions of tokens` });
    input.addEventListener("change", async () => {
      const tokens = capTokens(input.value);
      if (tokens === null) {
        input.value = capMillions(view.caps[key]);
        return;
      }
      try {
        repaint(await api.put("/budget", { ...view.caps, [key]: tokens }));
        toast("ok", tokens === 0 ? `${label}: no cap.` : `${label}: ${capMillions(tokens)}M tokens.`);
      } catch (error) {
        input.value = capMillions(view.caps[key]);
        toastError(error);
      }
    });
    const source = key.endsWith("_day_tokens") ? key.slice(0, -"_day_tokens".length) : null;
    const used = source === null ? "" : `${compact(spent.get(source) ?? 0)} today`;
    const row = setRow({ title: label, lead: used, control: input });
    row.dataset.usageCap = key;
    return row;
  });
  return [today, ...rows];
}

/**
 * The Usage section: `{ element, load() }`. `load()` reads the usage and draws it (the card says "Loading" until then,
 * and a failure with a way to try again); it never throws, so a page that fails to read it keeps its other sections.
 */
export function buildUsage() {
  const card = setCard(inlineLoading());
  const top = setCard();
  const caps = setCard();
  const used = setSection({ title: "Usage", lead: LEAD, body: card });
  used.dataset.section = "usage";
  const costliest = setSection({ title: "Tasks that used the most", lead: `The ${DAYS} days' costliest tasks, each with its own tokens (a helper is a task of its own).`, body: top });
  costliest.dataset.section = "usage-top";
  const capped = setSection({ title: "Caps", lead: CAPS_LEAD, body: caps });
  capped.dataset.section = "usage-caps";
  const element = el("div", { class: "set-sections" }, used, costliest, capped);

  function paintCaps(view) {
    caps.replaceChildren(...capRows(view, paintCaps));
  }

  function paint(usage) {
    const now = Date.now() / 1000;
    const today = localDay(now);
    // The newest day first: what happened today is what the owner looks for.
    const days = [...usage.days].reverse();
    const total = setRow({ title: `Last ${DAYS} days`, lead: totalLine(usage.days) });
    total.dataset.usageTotal = "";
    card.replaceChildren(...usage.windows.map((window) => windowRow(window, now)), total, ...days.map((day) => dayRow(day, usage.days, today)));
    top.replaceChildren(...(usage.top_runs.length === 0 ? [setRow({ title: "No tasks yet" })] : usage.top_runs.map(topRow)));
  }

  async function load() {
    try {
      const [usage, budget] = await Promise.all([api.get(`/usage${query({ days: DAYS })}`), api.get("/budget")]);
      paint(usage);
      paintCaps(budget);
    } catch (error) {
      card.replaceChildren(
        inlineError(error, () => {
          card.replaceChildren(inlineLoading());
          load();
        }),
      );
    }
  }

  return { element, load };
}
