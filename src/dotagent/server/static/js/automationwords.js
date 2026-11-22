// What a chat's side panel says of a schedule of the chat (item 1334): a title and a quiet line under it, as a developer
// tool's Automations list does ("Every 6 hours · paused", "Once, 17 Nov at 10:00", "Loops every day at 09:00 · next
// Mon 09:00"). Pure, so that it runs under Node without a page.

import { t, uiLocale } from "./i18n.js";
import { loopRhythm, nextFiring, onceAt } from "./rhythm.js";

const LOOP_PREFIX = /^Loop: /;

/** `text` with its first letter in capitals, in the interface's language ("every 6 hours" becomes "Every 6 hours"). */
function sentence(text) {
  return text === "" ? text : text[0].toLocaleUpperCase(uiLocale()) + text.slice(1);
}

/**
 * What the panel shows of one automation, from `view` (a `ScheduleView` of `GET /schedules`, its `record` and `ran_once`),
 * `loop` (whether it is the chat's own loop) and `now` (seconds): `title` (a loop's name without its "Loop: "), `line`
 * (how it repeats or when it runs, then how it stands), `active` (switched on and with a firing left), `done` (a time
 * that ran once and has none left), `failed` (the last run failed while it is on) and `running` (a run of it works now).
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
    title: loop ? record.name.replace(LOOP_PREFIX, "") : record.name,
    line: [main, state].filter((part) => part !== "").join(" · "),
    active: record.enabled && !done,
    done,
    failed: record.enabled && !running && record.last_status.startsWith("failed"),
    running,
  };
}
