// Dot's presence on the home (item 72): the face in its state, a greeting by the hour with your name, one line of now
// and the Today strip (what waits, what works, what is due, what was learned, what ended), read from `GET /activity`
// and kept fresh by the lifecycle stream. The words are pure functions of the activity, so they can be read alone.

import { activityRow, LIFECYCLE_KINDS } from "./activityrow.js";
import { api } from "./api.js";
import { el } from "./dom.js";
import { liveFace, setActorState } from "./actors.js";
import { dotName, overallState } from "./face.js";
import { icon } from "./icons.js";
import { nestRows } from "./nesting.js";
import { readFolds, readPref, writePref } from "./prefs.js";
import { getSnapshot, onChange, onLifecycle, onStreamBack, readFailed } from "./state.js";

const OWNER_KEY = "dot.owner";
export const OWNER_CHARS = 40;
// How many rows a section of the strip shows before its "Show N more" (item 364): two, so the strip stays short under the
// composer (item 124).
const ROWS_SHOWN = 2;
// Which sections of the strip the owner folded, kept between visits.
const FOLDS_KEY = "dot.homeFolds";
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

/** The sections of the Today strip that have rows: `{ key, title, count, rows, rest }`. `rows` are the section's first
 * two and `rest` the others its "Show N more" opens, each a run without the runs it delegated; `count` is how many `rows`
 * and `rest` hold together, so the title counts what the section shows. */
export function todaySections(activity) {
  return SECTIONS.map(({ key, title }) => ({ key, title, all: stripRows(activity, key) }))
    .filter(({ all }) => all.length > 0)
    .map(({ key, title, all }) => {
      const runs = nestRows(all).map(({ row }) => row);
      return { key, title, count: runs.length, rows: runs.slice(0, ROWS_SHOWN), rest: runs.slice(ROWS_SHOWN) };
    });
}

// Where the face looks while the composer has the focus (down at it), and for how long it stays attentive after a
// keystroke, and excited after a send.
const AT_THE_COMPOSER = { yaw: 0, pitch: -34, mix: 1, spin: 0, wander: 0.2 };
const TYPING_MS = 1500;
const SENDING_MS = 1500;

/** Build the presence: `element` (the face, the greeting and the now line), `strip` (the Today strip, placed where the
 * page wants it), `listen` (`{ focus(on), typing(), sending() }`: the face looks down at the composer while it has the
 * focus, goes attentive as the owner types and excited for a moment on send), and `dispose()`. */
export function buildPresence() {
  // Dot's face moves here (item 103), with a wink now and then at rest.
  const live = liveFace("dot", overallState(getSnapshot()), null, 96);
  const face = live.element;
  const name = el("div", { class: "home-name" }, dotName());
  const hello = el("p", { class: "home-greeting" });
  const now = el("p", { class: "home-now", "aria-live": "polite" }, "Looking around…");
  const element = el("div", { class: "home-who" }, face, name, hello, now);
  const strip = el("section", { class: "home-today", "aria-label": "Today", hidden: true });
  let activity = null;
  let disposed = false;
  let moodTimer = 0;
  let folds = readFolds(FOLDS_KEY);
  // The sections whose "Show N more" was pressed: they show all their rows until the page is left.
  const opened = new Set();

  function expressFor(expression, ms) {
    clearTimeout(moodTimer);
    live.express(expression);
    moodTimer = setTimeout(() => live.express(null), ms);
  }
  const listen = {
    focus: (on) => live.look(on ? AT_THE_COMPOSER : null),
    typing: () => expressFor("attentive", TYPING_MS),
    sending: () => expressFor("excited", SENDING_MS),
  };

  function paintGreeting(snapshot) {
    const state = overallState(snapshot);
    setActorState(face, state);
    hello.textContent = greeting(new Date().getHours(), ownerName(), state);
  }

  function toggleFold(key) {
    folds = { ...folds, [key]: folds[key] !== true };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    paintStrip();
    // The paint replaced the title that had the focus: the new one takes it, so the keyboard stays where it was.
    strip.querySelector(`[data-section="${key}"] .home-today-title`)?.focus();
  }

  function openSection(key) {
    opened.add(key);
    paintStrip();
    // The button that had the focus is gone: the section's first new row takes it.
    strip.querySelectorAll(`[data-section="${key}"] .home-today-list > *`)[ROWS_SHOWN]?.focus();
  }

  function paintStrip() {
    if (activity === null) {
      return;
    }
    now.textContent = nowLine(activity);
    const sections = todaySections(activity);
    strip.hidden = sections.length === 0;
    strip.replaceChildren(
      ...sections.map(({ key, title, count, rows, rest }) => {
        const folded = folds[key] === true;
        return el(
          "div",
          { class: "home-today-section", "data-section": key },
          el(
            "h2",
            { class: "section-label" },
            el(
              "button",
              { class: "home-today-title", type: "button", "aria-expanded": folded ? "false" : "true", onclick: () => toggleFold(key) },
              icon("chevron"),
              `${title} `,
              el("span", { class: "home-today-count" }, String(count)),
            ),
          ),
          folded
            ? null
            : el(
                "div",
                { class: "home-today-list" },
                ...(opened.has(key) ? [...rows, ...rest] : rows).map((row) => activityRow(row, false)),
                rest.length === 0 || opened.has(key)
                  ? null
                  : el("button", { class: "activity-row home-today-more", type: "button", onclick: () => openSection(key) }, `Show ${rest.length} more`),
              ),
        );
      }),
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
      if (activity === null) {
        console.warn("the activity of the home could not be loaded", error);
        now.textContent = "I could not read what is going on.";
      } else {
        readFailed("What is going on", error);
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
  const stopBack = onStreamBack(load);
  load();
  return {
    element,
    strip,
    listen,
    dispose: () => {
      disposed = true;
      clearTimeout(moodTimer);
      live.dispose();
      stopChange();
      stopLifecycle();
      stopBack();
    },
  };
}
