// What the tasks used in the last week, as a section of Settings › Models (item 1403): the windows of the Claude plan the
// tasks last saw, the whole week in a line and a row for each day with a bar of its tokens, from `GET /api/usage`. Dot
// holds no price per model, so it shows time, the words the models read and wrote, and how full the plan is: no money.
// Words in usagewords.js.

import { api, query } from "./api.js";
import { el } from "./dom.js";
import { inlineError, inlineLoading, setCard, setRow, setSection } from "./panels.js";
import { barShare, dayLabel, dayLine, localDay, totalLine, windowLine, windowName, windowShare } from "./usagewords.js";

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

/**
 * The Usage section: `{ element, load() }`. `load()` reads the usage and draws it (the card says "Loading" until then,
 * and a failure with a way to try again); it never throws, so a page that fails to read it keeps its other sections.
 */
export function buildUsage() {
  const card = setCard(inlineLoading());
  const element = setSection({ title: "Usage", lead: LEAD, body: card });
  element.dataset.section = "usage";

  function paint(usage) {
    const now = Date.now() / 1000;
    const today = localDay(now);
    // The newest day first: what happened today is what the owner looks for.
    const days = [...usage.days].reverse();
    const total = setRow({ title: `Last ${DAYS} days`, lead: totalLine(usage.days) });
    total.dataset.usageTotal = "";
    card.replaceChildren(...usage.windows.map((window) => windowRow(window, now)), total, ...days.map((day) => dayRow(day, usage.days, today)));
  }

  async function load() {
    try {
      paint(await api.get(`/usage${query({ days: DAYS })}`));
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
