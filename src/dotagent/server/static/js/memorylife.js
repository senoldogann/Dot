// The life of the notes on Settings › Memory (item 584): the notes proposed to be forgotten, the trash of the ones the
// owner forgot, and the choice of the order the notes are listed in. Nothing here deletes a note on its own: a note that
// rested for [memory] forget_after_days is only proposed, and the owner's Forget moves it to the trash, where it waits
// [memory] trash_days and Restore brings it back.

import { api } from "./api.js";
import { ago, clip, dateTime, dateTimeFull, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { setCard, setSection } from "./panels.js";
import { attempt, confirmDialog, toast } from "./ui.js";

const QUOTE_CHARS = 160;
const PREVIEW_CHARS = 420;
// The most ids one request names (NoteIdsRequest in server/views.py takes 500): more of them go in turns.
const MOST_IDS = 200;

// How the notes can be listed (MemoryOrder in store/models.py): by when they were changed, by when a run was last handed
// them, by how often, and the least used first (the notes no run was handed come first).
const ORDERS = [
  { id: "changed", label: "Changed last" },
  { id: "used", label: "Used last" },
  { id: "most_used", label: "Most used" },
  { id: "least_used", label: "Least used" },
];

/**
 * The choice of the order the notes are listed in: a label and a select. `onChange(id)` runs after a choice;
 * `setDisabled(why)` turns the choice off, saying why (a search lists by how well a note matches), and `setDisabled(null)`
 * turns it on again.
 * @param {string} selected
 * @param {(id: string) => void} onChange
 */
export function orderControl(selected, onChange) {
  const select = el("select", { class: "select sm", "aria-label": "Sort the notes by" }, ORDERS.map((order) => el("option", { value: order.id }, order.label)));
  select.value = selected;
  select.addEventListener("change", () => onChange(select.value));
  const element = el("label", { class: "note-order" }, el("span", { class: "note-order-label" }, "Sort by"), select);
  return {
    element,
    value: () => select.value,
    setDisabled(why) {
      select.disabled = why !== null;
      element.title = why ?? "";
    },
  };
}

/** How often runs were handed a note and when last, in words: "used 3 times, last 2d ago" or "never used". */
function usedWords(record) {
  return record.used_count > 0 ? `used ${plural(record.used_count, "time", "times")}, last ${ago(record.last_used_at)}` : "never used";
}

/**
 * A row of a list of notes the owner decides on: the note's words, its tags, what is said of it (`facts`, a list of
 * nodes and strings) and the buttons. `noteText(content)` makes the node of the words (the page's, with its code type).
 */
function decisionRow(record, noteText, facts, buttons) {
  return el(
    "div",
    { class: "note-row", dataset: { decided: record.id } },
    el(
      "div",
      { class: "note-main" },
      noteText(clip(record.content, PREVIEW_CHARS)),
      el(
        "div",
        { class: "note-meta" },
        record.tags.map((tag) => el("span", { class: "chip" }, tag)),
        el("span", { class: "note-when" }, ...facts.flatMap((fact, index) => (index === 0 ? [fact] : [" · ", fact]))),
      ),
    ),
    el("div", { class: "note-actions note-decide" }, buttons),
  );
}

/**
 * The notes proposed to be forgotten (item 584): those that rested for `forget_after_days`, the longest first, each with
 * Keep and Forget, and Forget all. They are listed whole in a box that scrolls inside, so that what Forget all forgets is
 * what the owner can see; the ids sent are the ones listed. `hooks.noteText(content)` draws a note's words;
 * `hooks.onDecided()` runs after a decision, to read the notes and the trash again. Returns the section, hidden while no
 * note is proposed, and `load()`.
 */
export function proposedSection(hooks) {
  let view = null;
  const count = el("span", { class: "life-count" });
  const rows = el("div", { class: "note-rows note-proposed-rows" });
  const forgetAll = el("button", { class: "btn danger sm", type: "button" }, icon("trash"), "Forget all");
  const section = setSection({
    title: ["Proposed to forget", count],
    lead: "",
    actions: forgetAll,
    body: setCard(rows),
  });
  section.classList.add("notes-proposed");
  section.dataset.proposed = "";
  section.hidden = true;
  const lead = section.querySelector(".set-section-text");

  /** Make the owner's decision on `ids`, as many at a time as a request takes, say what it did and read it all again. */
  async function decide(action, ids, words) {
    let done = 0;
    let skipped = 0;
    try {
      for (let first = 0; first < ids.length; first += MOST_IDS) {
        const outcome = await api.post(`/memories/proposals/${action}`, { ids: ids.slice(first, first + MOST_IDS) });
        done += outcome.done.length;
        skipped += outcome.skipped.length;
      }
    } finally {
      if (done > 0) {
        toast("ok", words(done));
      }
      if (skipped > 0) {
        toast("error", `${plural(skipped, "note was", "notes were")} no longer proposed, and left as they are.`);
      }
      await load();
      hooks.onDecided();
    }
  }

  const forgetWords = (days) => (done) => `Forgot ${plural(done, "note", "notes")}; ${done === 1 ? "it waits" : "they wait"} ${plural(days, "day", "days")} in the trash, where Restore brings ${done === 1 ? "it" : "them"} back.`;
  const keepWords = (done) => `Kept ${plural(done, "note", "notes")}: ${done === 1 ? "it wakes" : "they wake"}, and ${done === 1 ? "is" : "are"} proposed again only after as long a rest.`;

  async function forgetOne(item) {
    const confirmed = await confirmDialog({
      title: "Forget this note?",
      message: `${clip(item.record.content, QUOTE_CHARS)} It goes to the trash and waits ${plural(view.trash_days, "day", "days")}; Restore brings it back.`,
      confirmLabel: "Forget",
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await decide("forget", [item.record.id], forgetWords(view.trash_days));
    }
  }

  async function forgetEvery() {
    const ids = view.items.map((item) => item.record.id);
    const confirmed = await confirmDialog({
      title: `Forget ${plural(ids.length, "note", "notes")}?`,
      message: `The ${ids.length === 1 ? "note" : "notes"} listed under Proposed to forget go to the trash and wait ${plural(view.trash_days, "day", "days")}; Restore brings one back. Nothing is deleted for good before then.`,
      confirmLabel: `Forget ${ids.length === 1 ? "it" : "all"}`,
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await decide("forget", ids, forgetWords(view.trash_days));
    }
  }

  forgetAll.addEventListener("click", () => attempt(forgetEvery));

  function paint() {
    if (view === null || view.items.length === 0) {
      section.hidden = true;
      return;
    }
    count.textContent = String(view.items.length);
    forgetAll.lastChild.textContent = view.items.length === 1 ? "Forget it" : `Forget all ${view.items.length}`;
    lead.querySelector(".set-section-lead")?.remove();
    lead.append(
      el(
        "p",
        { class: "set-section-lead" },
        `These notes rested for ${plural(view.forget_after_days, "day", "days")}: no task was handed them. Nothing is forgotten until you press Forget; a forgotten note waits ${plural(view.trash_days, "day", "days")} in the trash.`,
      ),
    );
    rows.replaceChildren(
      ...view.items.map((item) => {
        const keep = el("button", { class: "btn ghost sm", type: "button", title: "Keep this note: it wakes and is proposed again only after as long a rest" }, icon("check"), "Keep");
        keep.addEventListener("click", () => attempt(() => decide("keep", [item.record.id], keepWords)));
        const forget = el("button", { class: "btn ghost sm", type: "button", title: "Forget this note: it goes to the trash" }, icon("trash"), "Forget");
        forget.addEventListener("click", () => attempt(() => forgetOne(item)));
        const rested = el("span", { title: dateTimeFull(item.rested_since) }, `Resting since ${dateTime(item.rested_since)}`);
        return decisionRow(item.record, hooks.noteText, [rested, usedWords(item.record)], [keep, forget]);
      }),
    );
    section.hidden = false;
  }

  async function load() {
    view = await api.get("/memories/proposals");
    paint();
  }

  return { element: section, load };
}

/**
 * The trash of the notes the owner forgot (item 584): each with when it was forgotten, when the reflection deletes it for
 * good, Restore and Delete for good. Returns the section, hidden while the trash is empty, and `load()`.
 * `hooks.noteText(content)` draws a note's words; `hooks.onDecided()` runs after a restore, to read the notes again.
 */
export function trashSection(hooks) {
  let view = null;
  const count = el("span", { class: "life-count" });
  const rows = el("div", { class: "note-rows note-proposed-rows" });
  const section = setSection({ title: ["Forgotten notes", count], lead: "", body: setCard(rows) });
  section.classList.add("notes-trash");
  section.dataset.trash = "";
  section.hidden = true;
  const lead = section.querySelector(".set-section-text");

  async function restore(item) {
    await api.post(`/memories/trash/${encodeURIComponent(item.record.id)}/restore`, {});
    toast("ok", "The note is back, awake.");
    await load();
    hooks.onDecided();
  }

  async function deleteForGood(item) {
    const confirmed = await confirmDialog({
      title: "Delete this note for good?",
      message: `${clip(item.record.content, QUOTE_CHARS)} It cannot come back.`,
      confirmLabel: "Delete for good",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    view = await api.del(`/memories/trash/${encodeURIComponent(item.record.id)}`);
    toast("ok", "Deleted for good.");
    paint();
  }

  function paint() {
    if (view === null || view.items.length === 0) {
      section.hidden = true;
      return;
    }
    count.textContent = String(view.total);
    lead.querySelector(".set-section-lead")?.remove();
    const capped = view.items.length < view.total ? ` Showing the newest ${view.items.length}.` : "";
    lead.append(el("p", { class: "set-section-lead" }, `A note you forgot waits ${plural(view.trash_days, "day", "days")} here, then the nightly reflection deletes it for good.${capped}`));
    rows.replaceChildren(
      ...view.items.map((item) => {
        const back = el("button", { class: "btn ghost sm", type: "button", title: "Bring this note back, awake" }, icon("undo"), "Restore");
        back.addEventListener("click", () => attempt(() => restore(item)));
        const gone = el("button", { class: "btn ghost sm", type: "button", title: "Delete this note for good, now" }, icon("trash"), "Delete for good");
        gone.addEventListener("click", () => attempt(() => deleteForGood(item)));
        const forgotten = el("span", { title: dateTimeFull(item.trashed_at) }, `Forgotten ${ago(item.trashed_at)}`);
        const until = el("span", { title: dateTimeFull(item.purged_at) }, `Deleted for good ${dateTime(item.purged_at)}`);
        return decisionRow(item.record, hooks.noteText, [forgotten, until], [back, gone]);
      }),
    );
    section.hidden = false;
  }

  async function load() {
    view = await api.get("/memories/trash");
    paint();
  }

  return { element: section, load };
}
