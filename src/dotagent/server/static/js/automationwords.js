// What a chat's side panel says of a schedule of the chat (item 1334): a title and a quiet line under it, as a developer
// tool's Automations list does ("Every 6 hours · paused", "Once, 17 Nov at 10:00", "Loops every day at 09:00 · next
// Mon 09:00"). Pure, so that it runs under Node without a page.

import { t, uiLocale } from "./i18n.js";
import { lastMoment, loopRhythm, nextFiring, onceAt } from "./rhythm.js";

const LOOP_PREFIX = /^Loop: /;

/** `text` with its first letter in capitals, in the interface's language ("every 6 hours" becomes "Every 6 hours"). */
function sentence(text) {
  return text === "" ? text : text[0].toLocaleUpperCase(uiLocale()) + text.slice(1);
}

/**
 * How the schedule's last run ended and when (item 1355), from its record: "" for one that never ran, one that works
 * now and a status the panel has no words for. A run that failed says how many in a row did when more than one.
 */
function lastRun(record, now) {
  if (record.last_run_at === null || record.last_run_at === undefined) {
    return "";
  }
  const when = lastMoment(record.last_run_at, now);
  const status = record.last_status ?? "";
  if (status.startsWith("done")) {
    return t("automations.lastDone", { when });
  }
  if (status.startsWith("failed")) {
    const row = record.runs_failed_in_row ?? 0;
    return row > 1 ? t("automations.lastFailedRow", { count: row, when }) : t("automations.lastFailed", { when });
  }
  if (status.startsWith("skipped")) {
    return t("automations.lastSkipped", { when });
  }
  return "";
}

/**
 * What the panel shows of one automation, from `view` (a `ScheduleView` of `GET /schedules`, its `record` and `ran_once`),
 * `loop` (whether it is the chat's own loop) and `now` (seconds): `title` (a loop's name without its "Loop: "), `line`
 * (how it repeats or when it runs, then how it stands), `active` (switched on and with a firing left), `done` (a time
 * that ran once and has none left), `failed` (the last run failed while it is on), `running` (a run of it works now) and
 * `last` (how its last run ended and when, "Last run done · 09:00"; "" while it works or when it never ran: item 1355).
 */
export function automationWords(view, loop, now) {
  const record = view.record;
  const done = view.ran_once === true;
  const rhythm = record.cron === null ? null : loopRhythm(record.cron);
  const main =
    record.cron === null
      ? t("automations.once", { when: record.run_at === null ? "" : onceAt(record.run_at, now) })
      : rhythm === null
        ? t("automations.cron")
        : loop
          ? t("automations.loops", { rhythm })
          : sentence(rhythm);
  const state = done
    ? t("automations.done")
    : !record.enabled
      ? t("automations.paused")
      : record.cron !== null && record.next_run_at !== null
        ? t("automations.next", { when: nextFiring(record.next_run_at, now) })
        : "";
  const running = record.last_status === "running" || record.last_status === "starting";
  return {
    last: running ? "" : lastRun(record, now),
    title: loop ? record.name.replace(LOOP_PREFIX, "") : record.name,
    line: [main, state].filter((part) => part !== "").join(" · "),
    active: record.enabled && !done,
    done,
    failed: record.enabled && !running && record.last_status.startsWith("failed"),
    running,
  };
}
