// How often a chat's schedule asks again, in words (rhythm.js, items 580 and 590): pure, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { loopRhythm, nextFiring, repeatCron } from "../../src/dotagent/server/static/js/rhythm.js";

test("the rhythms /loop makes read as they are written", () => {
  assert.equal(loopRhythm("*/30 * * * *"), "every 30 minutes");
  assert.equal(loopRhythm("0 * * * *"), "every hour");
  assert.equal(loopRhythm("0 */2 * * *"), "every 2 hours");
});

test("a schedule a run made reads by its days and times, as the owner's own three do", () => {
  assert.equal(loopRhythm("0 10 * * *"), "every day at 10:00");
  assert.equal(loopRhythm("0 9,19 * * *"), "every day at 09:00 and 19:00");
  // Days together by their short names, so that the time stays in sight on a phone (item 592); a day alone by its name.
  assert.equal(loopRhythm("30 18 * * 1,3,5"), "on Mon, Wed and Fri at 18:30");
  assert.equal(loopRhythm("30 9 * * 2,4"), "on Tue and Thu at 09:30");
  assert.equal(loopRhythm("0 9 * * 1"), "on Mondays at 09:00");
  assert.equal(loopRhythm("0 9 * * 1-5"), "on weekdays at 09:00");
  assert.equal(loopRhythm("15 8 * * 0,6"), "at weekends at 08:15");
  assert.equal(loopRhythm("5 7 * * 7"), "on Sundays at 07:05");
  // In order, whatever order the cron gives them: the hours of a day, the days of a week from Monday.
  assert.equal(loopRhythm("0 19,9 * * *"), "every day at 09:00 and 19:00");
  assert.equal(loopRhythm("30 18 * * 0,5,1"), "on Mon, Fri and Sun at 18:30");
  assert.equal(loopRhythm("0 8 * * 0,7"), "on Sundays at 08:00");
});

test("another form is no rhythm in words, and the caller says it otherwise", () => {
  for (const cron of ["0 9 1 * *", "0 9 * 6 *", "*/5 9-17 * * 1-5", "0 9 * * MON", "nonsense"]) {
    assert.equal(loopRhythm(cron), null, cron);
  }
});

test("the next firing is its time today, else its day's name and time", () => {
  // Friday 23 October 2026, nine in the morning, in the zone the test runs in.
  const now = new Date(2026, 9, 23, 9, 0).getTime() / 1000;
  assert.equal(nextFiring(new Date(2026, 9, 23, 18, 30).getTime() / 1000, now), "18:30");
  assert.equal(nextFiring(new Date(2026, 9, 24, 10, 0).getTime() / 1000, now), "Sat 10:00");
  // A week away or more, or past due, by its date: "Sun" alone would read as the coming Sunday.
  assert.equal(nextFiring(new Date(2026, 10, 1, 9, 0).getTime() / 1000, now), "1 Nov 09:00");
  assert.equal(nextFiring(new Date(2026, 9, 23, 8, 0).getTime() / 1000, now), "23 Oct 08:00");
  // A firing a minute past its time is the one happening now, before the scheduler moves the next one on.
  assert.equal(nextFiring(new Date(2026, 9, 23, 8, 59).getTime() / 1000, now), "08:59");
  // Also when it fell a moment before midnight: not "Fri 23:58", which reads as next Friday.
  const pastMidnight = new Date(2026, 9, 24, 0, 1).getTime() / 1000;
  assert.equal(nextFiring(new Date(2026, 9, 23, 23, 58).getTime() / 1000, pastMidnight), "23:58");
});

test("a taught task repeats on the cron of its rhythm, day and time, which the page says in words (item 666)", () => {
  assert.equal(repeatCron("day", "1", "09:00"), "0 9 * * *");
  assert.equal(repeatCron("weekdays", "1", "18:30"), "30 18 * * 1-5");
  assert.equal(repeatCron("week", "0", "07:05"), "5 7 * * 0");
  assert.equal(loopRhythm(repeatCron("week", "1", "09:00")), "on Mondays at 09:00");
  assert.equal(loopRhythm(repeatCron("weekdays", "1", "18:30")), "on weekdays at 18:30");
});
