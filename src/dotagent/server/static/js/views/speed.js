// Settings › Maintenance › Speed (item 2003): where the runs' seconds went in the last 24 hours or 7 days, stage by
// stage, as `dotx perf` prints it: the start of a run, the time between two steps (and Dot's own part of it), the model
// calls by model and purpose, the first word of a step's reply and the tools. Each row: how many, the 50th, 95th and
// 99th percentiles, the longest and how many took over 3 s (dotagent/store/perf.py). Read when the page is opened.

import { api } from "../api.js";
import { el } from "../dom.js";
import { t, tCount } from "../i18n.js";
import { icon } from "../icons.js";
import { inlineError, inlineLoading, pageHead, segmented } from "../panels.js";
import { scrollCue } from "../scrollcue.js";
import { attempt } from "../ui.js";

/** @typedef {"start" | "start_read" | "gap" | "gap_daemon" | "model" | "first_token" | "tool"} PerfStage */
/** @typedef {{ stage: PerfStage, name: string, n: number, p50: number, p95: number, p99: number, max: number, over_3s: number }} PerfRow */
/** @typedef {{ window: "24h" | "7d", since: number, until: number, runs: number, rows: PerfRow[] }} PerfTable */

/** The heading of each stage, in the order the daemon lists them. @type {Record<PerfStage, import("../i18n.js").WordKey>} */
const STAGE_TITLES = {
  start: "speed.stageStart",
  start_read: "speed.stageStartRead",
  gap: "speed.stageGap",
  gap_daemon: "speed.stageGapDaemon",
  model: "speed.stageModel",
  first_token: "speed.stageFirstToken",
  tool: "speed.stageTool",
};

/** Seconds as the table shows them: one decimal under 100. */
function seconds(value) {
  return value < 100 ? value.toFixed(1) : value.toFixed(0);
}

/** @param {PerfRow} row */
function rowOf(row) {
  return el(
    "tr",
    null,
    el("td", null, row.name),
    el("td", { class: "num" }, String(row.n)),
    el("td", { class: "num" }, seconds(row.p50)),
    el("td", { class: "num" }, seconds(row.p95)),
    el("td", { class: "num" }, seconds(row.p99)),
    el("td", { class: "num" }, seconds(row.max)),
    el("td", { class: row.over_3s > 0 ? "num speed-slow" : "num" }, String(row.over_3s)),
  );
}

/** The rows of one stage as a card with its table. @param {PerfStage} stage @param {PerfRow[]} rows */
function stageCard(stage, rows) {
  const heads = [t("speed.colWhat"), "n", "p50", "p95", "p99", t("speed.colMax"), t("speed.colOver3")];
  const table = el(
    "div",
    { class: "table-scroll" },
    el("table", { class: "table wide speed-table" }, el("thead", null, el("tr", null, heads.map((head) => el("th", null, head)))), el("tbody", null, rows.map(rowOf))),
  );
  return { card: el("section", { class: "card" }, el("div", { class: "card-head" }, el("span", { class: "grow" }, t(STAGE_TITLES[stage]))), table), table };
}

export function mount(root) {
  let disposed = false;
  /** @type {Array<() => void>} */
  let cues = [];
  const summary = el("p", { class: "small muted" });
  const body = el("div", { class: "stack" }, inlineLoading());

  function stopCues() {
    for (const stop of cues) {
      stop();
    }
    cues = [];
  }

  /** @param {"24h" | "7d"} window */
  async function load(window) {
    body.replaceChildren(inlineLoading());
    try {
      /** @type {PerfTable} */
      const table = await api.get(`/diagnostics/speed?window=${window}`);
      if (disposed) {
        return;
      }
      stopCues();
      summary.textContent =
        window === "24h" ? tCount(table.runs, "speed.summaryDayOne", "speed.summaryDay") : tCount(table.runs, "speed.summaryWeekOne", "speed.summaryWeek");
      if (table.rows.length === 0) {
        body.replaceChildren(el("div", { class: "card-pad hint" }, t("speed.empty")));
        return;
      }
      /** @type {Map<PerfStage, PerfRow[]>} */
      const stages = new Map();
      for (const row of table.rows) {
        stages.set(row.stage, [...(stages.get(row.stage) ?? []), row]);
      }
      const cards = [...stages].map(([stage, rows]) => stageCard(stage, rows));
      body.replaceChildren(...cards.map((made) => made.card));
      cues = cards.map((made) => scrollCue(made.table));
    } catch (error) {
      if (!disposed) {
        body.replaceChildren(inlineError(error, () => attempt(() => load(windows.value()))));
      }
    }
  }

  const windows = segmented(
    [
      { id: "24h", label: t("speed.window24h"), hint: "" },
      { id: "7d", label: t("speed.window7d"), hint: "" },
    ],
    "24h",
    (id) => attempt(() => load(id === "7d" ? "7d" : "24h")),
  );
  const refresh = el("button", { class: "btn ghost sm", type: "button", "aria-label": t("speed.refresh"), title: t("speed.refresh"), onclick: () => attempt(() => load(windows.value() === "7d" ? "7d" : "24h")) }, icon("refresh"), t("speed.refresh"));

  root.append(pageHead(t("speed.title"), t("speed.about"), null), el("div", { class: "speed-controls" }, windows.element, refresh), summary, body);
  attempt(() => load("24h"));
  return () => {
    disposed = true;
    stopCues();
  };
}
