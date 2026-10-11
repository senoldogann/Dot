// Dot's presence on the home (item 72): the face in its state, a greeting by the hour with your name, one line of now
// and the Today strip (what waits, what works, what is due, what was learned, what ended), read from `GET /activity`
// and kept fresh by the lifecycle stream. The words are pure functions of the activity, so they can be read alone.

import { activityRow, LIFECYCLE_KINDS } from "./activityrow.js";
import { api } from "./api.js";
import { el } from "./dom.js";
import { dotName, overallState, setFaceState } from "./face.js";
import { avatar } from "./icons.js";
import { nestRows } from "./nesting.js";
import { readPref, writePref } from "./prefs.js";
import { getSnapshot, onChange, onLifecycle } from "./state.js";

const OWNER_KEY = "dot.owner";
export const OWNER_CHARS = 40;
// How many rows a section of the strip shows before "and N more" points at the Activity page.
const ROWS_SHOWN = 5;
const SECTIONS = [
  { key: "waiting", title: "Waits for you" },
  { key: "working", title: "Working" },
  { key: "due", title: "Due today" },
  { key: "learned_today", title: "Learned today" },
  { key: "recent", title: "Ended today" },
];
const VERB_OF_STATE = {
  idle: "What should I work on?",
  working: "I'm on it. Anything else?",
  waiting: "Something waits for you.",
};

/** Your name, as the greeting says it; empty when you have not given one. */
export function ownerName() {
  const saved = readPref(OWNER_KEY);
  return saved === null ? "" : saved.trim();
}

/** Set your name; blank forgets it. Returns the name in use. */
export function setOwnerName(name) {
  writePref(OWNER_KEY, name.trim().slice(0, OWNER_CHARS));
  return ownerName();
}

/** The greeting for `hour` (0 to 23), with `owner` when there is one, and the verb of the face's `state`. */
export function greeting(hour, owner, state) {
  const time = hour < 5 ? "Hello" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const salute = owner === "" ? `${time}.` : `${time}, ${owner}.`;
  return `${salute} ${VERB_OF_STATE[state] ?? VERB_OF_STATE.idle}`;
}

function count(n, one, many) {
  return n === 1 ? `1 ${one}` : `${n} ${many}`;
}

/** One line of now from the activity: what works, what waits, what ended, what was learned, what is due; a quiet day says so. */
export function nowLine(activity) {
  const parts = [];
  // The working section also lists a schedule due within the hour; only the runs are "working on".
  const runs = activity.working.filter((row) => row.run_id !== null).length;
  if (runs > 0) {
    parts.push(`Working on ${count(runs, "run", "runs")}`);
  }
  if (activity.waiting.length > 0) {
    parts.push(activity.waiting.length === 1 ? "1 waits for you" : `${activity.waiting.length} wait for you`);
  }
  if (activity.recent.length > 0) {
    parts.push(`${activity.recent.length} ended today`);
  }
  const lessons = activity.learned_today.filter((row) => row.kind === "lesson").length;
  if (lessons > 0) {
    parts.push(count(lessons, "lesson", "lessons"));
  }
  if (activity.learned_today.some((row) => row.kind === "journal")) {
    parts.push("the day's journal");
  }
  if (activity.due.length > 0) {
    parts.push(`${count(activity.due.length, "thing", "things")} due today`);
  }
  return parts.length === 0 ? "A quiet day so far: nothing works, nothing waits, nothing ended." : parts.join(" · ");
}

/** The rows of a section as the strip shows them: Working holds the runs only (a schedule due within the hour stands
 * under Due today here). */
function stripRows(activity, key) {
  return key === "working" ? activity.working.filter((row) => row.run_id !== null) : activity[key];
}

/** The sections of the Today strip that have rows: `{ key, title, rows, more }`, `more` the rows past the first five. */
export function todaySections(activity) {
  return SECTIONS.map(({ key, title }) => ({ key, title, nested: nestRows(stripRows(activity, key)) }))
    .filter(({ nested }) => nested.length > 0)
    .map(({ key, title, nested }) => ({ key, title, rows: nested.slice(0, ROWS_SHOWN), more: Math.max(0, nested.length - ROWS_SHOWN) }));
}

/** Build the presence: `element` (the face, the greeting and the now line), `strip` (the Today strip, placed where the
 * page wants it), `focusable`, and `dispose()`. */
export function buildPresence() {
  const face = avatar(overallState(getSnapshot()));
  const name = el("div", { class: "home-name" }, dotName());
  const hello = el("p", { class: "home-greeting" });
  const now = el("p", { class: "home-now", "aria-live": "polite" }, "Looking around…");
  const element = el("div", { class: "home-who" }, face, name, hello, now);
  const strip = el("section", { class: "home-today", "aria-label": "Today", hidden: true });
  let activity = null;
  let disposed = false;

  function paintGreeting(snapshot) {
    const state = overallState(snapshot);
    setFaceState(face, state);
    hello.textContent = greeting(new Date().getHours(), ownerName(), state);
  }

  function paintStrip() {
    if (activity === null) {
      return;
    }
    now.textContent = nowLine(activity);
    const sections = todaySections(activity);
    strip.hidden = sections.length === 0;
    strip.replaceChildren(
      ...sections.map(({ key, title, rows, more }) =>
        el(
          "div",
          { class: "home-today-section", "data-section": key },
          el("div", { class: "section-label" }, title),
          el(
            "div",
            { class: "card home-today-list" },
            ...rows.map(({ row, children }) => [
              activityRow(row, false),
              children.length === 0 ? null : el("div", { class: "activity-children" }, children.map((kid) => activityRow(kid, true))),
            ]),
            more === 0 ? null : el("a", { class: "activity-row home-today-more", href: "#/activity" }, `and ${more} more in Activity`),
          ),
        ),
      ),
    );
  }

  async function load() {
    try {
      const fresh = await api.get("/activity");
      if (disposed) {
        return;
      }
      activity = fresh;
      paintStrip();
    } catch (error) {
      if (disposed) {
        return;
      }
      console.warn("the activity of the home could not be loaded", error);
      if (activity === null) {
        now.textContent = "I could not read what is going on.";
      }
    }
  }

  paintGreeting(getSnapshot());
  const stopChange = onChange((snapshot) => {
    paintGreeting(snapshot);
    if (activity !== null && activity.working.length > 0) {
      load();
    }
  });
  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      load();
    }
  });
  load();
  return {
    element,
    strip,
    dispose: () => {
      disposed = true;
      stopChange();
      stopLifecycle();
    },
  };
}
