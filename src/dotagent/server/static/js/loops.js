// The loops of a chat (item 281): right above the composer, as the commands at work are (background.js), a row for each
// schedule of the chat, made with "/loop 30m <message>" or by a run of it (item 291): its message, how often it asks
// again, when it asks next, and a "Stop repeating" that deletes the schedule, every one in one column (item 591). The
// rows fold under a head that says, folded, how often it repeats and when it asks next (item 591), the choice kept per
// browser. A loop at rest is that one line until the owner opens it: the rows of a card that stood open took a phone's
// reading room above the composer; one that cannot be read says so open. A run of the loop can end it itself once the
// work is done for good. The daemon tells of no change of a schedule, so the rows are read again when the chat's runs
// come and go, and as the page comes back.

import { api } from "./api.js";
import { clip, debounce, el, setText } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";
import { loopRhythm, nextFiring } from "./rhythm.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, confirmDialog } from "./ui.js";

const NAME_CHARS = 80;
const RUN_MOMENTS = new Set(["run_queued", "run_finished"]);
const READ_AGAIN_MS = 400;
// Whether the loops' rows are folded, kept as the folds of the pages are (panels.js): "open" or "closed", folded when
// nothing is kept.
const FOLD_KEY = "dot.fold.chat-loops";
// "/loop", a rhythm (30m, 2h) and the message that the chat asks again on it (item 281); "/loop" alone or with a word
// that is not a rhythm is said how to write it (LOOP_HOW).
const LOOP_PATTERN = /^\/loop\s+(\d+\s*[mh])\s+([\s\S]+)$/i;
export const LOOP_WORD = /^\/loop(?:\s|$)/i;
export const LOOP_HOW = "Write /loop, how often and the message: /loop 30m check the build";
/** "/loop" in the slash menu (composer.js `commands`) of the box of a run, and of a new chat's (item 400). */
export const LOOP_COMMAND = { label: "/loop", hint: "Ask again every so often until it is done: /loop 30m <message>", insert: "/loop " };

/** The rhythm (`every`, as the daemon reads it) and the message of `text` written as "/loop 30m <message>", or null. */
export function loopOf(text) {
  const found = LOOP_PATTERN.exec(text);
  return found === null ? null : { every: found[1].replace(/\s+/g, ""), message: found[2].trim() };
}

/** How often a loop asks again, in words (rhythm.js: "every 30 minutes", "every day at 10:00"); a rhythm of another
 * form is said as the daemon says it. */
function rhythmWords(view) {
  return loopRhythm(view.record.cron ?? "") ?? view.when;
}

/**
 * The line of the loops of the chat `threadId`, which goes right above the composer. Returns `{ element, refresh(),
 * dispose() }`: `refresh()` reads the loops again (after "/loop" made one).
 */
export function chatLoops({ threadId }) {
  const list = el("ul", { class: "background-list", id: `chat-loops-${threadId}` });
  const count = el("span", { class: "chat-loops-count" });
  const soonest = el("span", { class: "chat-loops-next" });
  const title = el("span", { class: "chat-loops-title" }, t("loops.title"));
  const toggle = el(
    "button",
    { class: "fold-toggle chat-loops-toggle", type: "button", "aria-controls": list.id, onclick: () => fold(!folded) },
    el("span", { class: "chev" }, icon("chevron")),
    el("span", { class: "chat-loops-mark", "aria-hidden": "true" }, icon("refresh")),
    title,
    count,
    soonest,
  );
  let folded = readPref(FOLD_KEY) !== "open";
  // What the folded head says of the loops: how often the one repeats, or how many there are.
  let summary = t("loops.title");
  const problem = el("span", null);
  const unread = el(
    "div",
    { class: "background-unread", role: "alert", hidden: true },
    problem,
    el("button", { class: "btn sm ghost", type: "button", onclick: () => load() }, t("loops.readAgain")),
  );
  // A region, not a live one: it is read again with every task of the chat, which a screen reader would say each time.
  const element = el("section", { class: "chat-loops", role: "region", "aria-label": t("loops.region"), hidden: true }, toggle, unread, list);

  /** Fold the rows away (`shut`) or open them, and keep the choice. */
  function fold(shut) {
    folded = shut;
    writePref(FOLD_KEY, shut ? "closed" : "open");
    paintFold();
  }

  function paintFold() {
    toggle.setAttribute("aria-expanded", String(!folded));
    toggle.title = folded ? t("loops.show") : t("loops.hide");
    list.hidden = folded;
    setText(title, folded ? summary : t("loops.title"));
    count.hidden = folded;
    soonest.hidden = !folded || soonest.textContent === "";
    element.dataset.folded = String(folded);
  }

  function row(view) {
    const name = clip(view.record.name.replace(/^Loop: /, ""), NAME_CHARS);
    const at = view.record.next_run_at;
    const next = at === null ? "" : ` · ${t("loops.next", { when: nextFiring(at, Date.now() / 1000) })}`;
    const stop = el(
      "button",
      { class: "btn ghost sm", type: "button", "aria-label": t("loops.stopLabel", { name }), onclick: () => stopLoop(view, name) },
      t("loops.stop"),
    );
    // The message over how often it asks again on a phone, beside it in a wider window: the Stop stands in a column of
    // its own at the row's end either way, so that the Stops of the rows stand in one line.
    return el(
      "li",
      { class: "background-row loop-row", "data-schedule": view.record.id, title: view.record.name },
      el("span", { class: "status-dot", "aria-hidden": "true" }),
      el(
        "span",
        { class: "loop-text" },
        el("span", { class: "background-label" }, el("span", { class: "background-word" }, t("loops.word")), el("span", { class: "background-command" }, name)),
        el("span", { class: "background-time" }, `${rhythmWords(view)}${next}`),
      ),
      stop,
    );
  }

  async function load() {
    try {
      const views = await api.get("/schedules");
      const loops = views.filter((view) => view.record.thread_id === threadId && view.record.enabled);
      unread.hidden = true;
      list.replaceChildren(...loops.map(row));
      setText(count, String(loops.length));
      summary = loops.length === 1 ? t("loops.one", { rhythm: rhythmWords(loops[0]) }) : t("loops.many", { count: loops.length });
      const times = loops.map((view) => view.record.next_run_at).filter((at) => at !== null);
      setText(soonest, times.length === 0 ? "" : t("loops.next", { when: nextFiring(Math.min(...times), Date.now() / 1000) }));
      paintFold();
      element.hidden = loops.length === 0;
    } catch (error) {
      problem.textContent = t("loops.unread", { problem: error.message });
      unread.hidden = false;
      element.hidden = false;
    }
  }

  async function stopLoop(view, name) {
    const confirmed = await confirmDialog({
      title: t("loops.stopTitle"),
      message: t("loops.stopMessage", { name }),
      confirmLabel: t("loops.stop"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await attempt(async () => {
      await api.del(`/schedules/${encodeURIComponent(view.record.id)}`);
      await load();
    });
  }

  const readAgain = debounce(() => load(), READ_AGAIN_MS);
  const stopLifecycle = onLifecycle((event) => {
    if (RUN_MOMENTS.has(event.kind)) {
      readAgain();
    }
  });
  const stopBack = onStreamBack(load);

  paintFold();
  load();
  return {
    element,
    refresh: load,
    dispose: () => {
      stopLifecycle();
      stopBack();
    },
  };
}
