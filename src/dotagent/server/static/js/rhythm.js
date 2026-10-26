// How often a schedule of a chat asks again, in words (items 281, 580, 590 and 592): the rhythms "/loop" makes (every
// 30 minutes, every 2 hours) and the days and times a schedule a run made keeps (every day at 10:00, on Mon, Wed and
// Fri at 18:30), as the loop line over the composer and the mark of a chat in the sidebar say them. Pure, so that it
// runs under Node without a page.

// A day alone by its name ("on Mondays at 09:00"), days together by their short names, so that the time after them
// stays in sight on a phone's line (item 592: "on Mondays, Wednesdays and Frid…" lost it).
const DAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];
const SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const HOUR_MINUTE = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
const WEEKDAY = new Intl.DateTimeFormat("en-GB", { weekday: "short" });
const DAY_MONTH = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const DAY_KEY = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" });
// A next firing this far away or more is named by its date: a day's name alone would read as the coming one.
const WEEK_S = 6 * 24 * 3600;
// A firing this little past its time is happening now (the scheduler moves the next one on at its tick); one past it
// longer was missed, while the daemon was off, and is named by its date.
const DUE_GRACE_S = 5 * 60;

/** "a", "a and b", "a, b and c". */
function listed(words) {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

/** The times of a cron's minute and hour fields ("09:00 and 19:00"), or null for a form that names no plain times. */
function cronTimes(minute, hour) {
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}(,\d{1,2})*$/.test(hour)) {
    return null;
  }
  const hours = [...new Set(hour.split(",").map(Number))].sort((a, b) => a - b);
  return listed(hours.map((each) => `${String(each).padStart(2, "0")}:${minute.padStart(2, "0")}`));
}

/** The days of a cron's day-of-week field ("every day", "on weekdays", "on Mondays", "on Mon and Fri"), or null. */
function cronDays(day) {
  if (day === "*") {
    return "every day";
  }
  if (day === "1-5") {
    return "on weekdays";
  }
  if (/^[0-7](,[0-7])*$/.test(day)) {
    // In the order of a week that starts on Monday, as en-GB's does; 0 and 7 are both Sunday.
    const numbers = [...new Set(day.split(",").map((each) => Number(each) % 7))].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7));
    if (numbers.length === 2 && numbers.includes(0) && numbers.includes(6)) {
      return "at weekends";
    }
    return numbers.length === 1 ? `on ${DAYS[numbers[0]]}` : `on ${listed(numbers.map((each) => SHORT_DAYS[each]))}`;
  }
  return null;
}

/** How often a schedule of the rhythm `cron` asks again, in words: "every 30 minutes", "every hour", "every 2 hours",
 * "every day at 10:00", "on weekdays at 09:00", "on Mondays at 09:00", "on Mon, Wed and Fri at 18:30"; null for another
 * form (a day of the month, a month, a range of minutes). */
export function loopRhythm(cron) {
  const minutes = /^\*\/(\d+) \* \* \* \*$/.exec(cron);
  if (minutes !== null) {
    return `every ${minutes[1]} minutes`;
  }
  if (cron === "0 * * * *") {
    return "every hour";
  }
  const hours = /^0 \*\/(\d+) \* \* \*$/.exec(cron);
  if (hours !== null) {
    return `every ${hours[1]} hours`;
  }
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5 || fields[2] !== "*" || fields[3] !== "*") {
    return null;
  }
  const times = cronTimes(fields[0], fields[1]);
  const days = cronDays(fields[4]);
  return times === null || days === null ? null : `${days} at ${times}`;
}

/** When a schedule fires next (`at`, seconds), as the sidebar says it: the time alone later today ("15:30"), the day's
 * name and the time within the coming week ("Fri 18:30"), else the date and the time ("1 Nov 09:00"), so that a firing
 * weeks away, or one missed while the daemon was off, does not read as the coming one; one a few minutes past its time
 * is the one happening now, said as today's. `now` is in seconds too. */
export function nextFiring(at, now) {
  const when = new Date(at * 1000);
  const time = HOUR_MINUTE.format(when);
  if (at < now - DUE_GRACE_S || at - now >= WEEK_S) {
    return `${DAY_MONTH.format(when)} ${time}`;
  }
  // One happening now is said by its time alone, also when it fell a moment before midnight.
  if (at <= now) {
    return time;
  }
  return DAY_KEY.format(when) === DAY_KEY.format(new Date(now * 1000)) ? time : `${WEEKDAY.format(when)} ${time}`;
}

// How often a taught task can repeat, as the cron's day-of-week field says it (a week's day is chosen beside it).
export const REPEAT_RHYTHMS = [
  { id: "day", label: "Every day", days: "*" },
  { id: "weekdays", label: "On weekdays", days: "1-5" },
  { id: "week", label: "Every week", days: null },
];
// The days of a week, Monday first, by the cron's numbers.
export const WEEK_DAYS = [
  ["1", "Monday"],
  ["2", "Tuesday"],
  ["3", "Wednesday"],
  ["4", "Thursday"],
  ["5", "Friday"],
  ["6", "Saturday"],
  ["0", "Sunday"],
];

/** The cron of a taught task's repeat (item 666): a rhythm (`REPEAT_RHYTHMS`' id), a day of the week ("1" for Monday) and a time ("09:30"). */
export function repeatCron(rhythm, day, time) {
  const [hour, minute] = time.split(":").map(Number);
  const days = REPEAT_RHYTHMS.find((each) => each.id === rhythm)?.days ?? day;
  return `${minute} ${hour} * * ${days}`;
}
