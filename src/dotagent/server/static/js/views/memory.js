// What Dot remembers: add a note, look things up by meaning or by words, correct a note and forget what is out of date.

import { api, query } from "../api.js";
import { ago, clip, dateTimeFull, debounce, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, paneHead } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const PAGE_SIZE = 30;
const SEARCH_LIMIT = 30;
const SEARCH_DELAY_MS = 250;
const STRONG_MATCH = 0.75;
const PREVIEW_CHARS = 420;
const QUOTE_CHARS = 160;

/** "work, Mac ,work" becomes ["work", "Mac"]: trimmed, without blanks or repeats. */
function parseTags(text) {
  const tags = [];
  for (const part of text.split(",")) {
    const tag = part.trim();
    if (tag !== "" && !tags.includes(tag)) {
      tags.push(tag);
    }
  }
  return tags;
}

/** The words of a note; a long note shows its beginning, with a button to read the rest. */
function noteText(text) {
  const preview = `${text.slice(0, PREVIEW_CHARS)}…`;
  if (text.length <= PREVIEW_CHARS) {
    return el("div", { class: "row-text" }, text);
  }
  let expanded = false;
  const body = el("div", { class: "row-text" }, preview);
  const toggle = el("button", { class: "btn ghost sm", type: "button" }, "Show more");
  toggle.addEventListener("click", () => {
    expanded = !expanded;
    body.textContent = expanded ? text : preview;
    toggle.textContent = expanded ? "Show less" : "Show more";
  });
  return el("div", null, body, toggle);
}

/** How the runs a note was handed to ended: "Handed to 5 runs, 4 done"; nothing until a run was handed it. */
export function scoreLine(score) {
  if (score === undefined || score.handed === 0) {
    return null;
  }
  return el(
    "span",
    { title: `${score.done} ended done, ${score.failed} failed` },
    `Handed to ${plural(score.handed, "run", "runs")}, ${score.done} done`,
  );
}

// The agents by id, from the page, so a lesson can say whose it is.
let agentNames = {};

/** Who wrote a note, whose lesson it is and how often runs used it. */
function provenance(record) {
  const whose = record.agent_id === null ? null : el("span", { class: "chip" }, `${agentNames[record.agent_id] ?? "An agent"}'s`);
  const author =
    record.source_run === "reflection"
      ? el("span", { class: "chip" }, "Written by the nightly reflection")
      : record.source_run
        ? el("a", { href: `#/runs/${record.source_run}` }, "Saved by a run")
        : el("span", { class: "chip" }, "You wrote it");
  const used =
    record.used_count > 0
      ? el("span", { title: dateTimeFull(record.last_used_at) }, `Used ${plural(record.used_count, "time", "times")}, last ${ago(record.last_used_at)}`)
      : el("span", null, "Not used yet");
  return [whose, author, used];
}

/** The note in place as a small form: its words and tags, saved with `onSave(content, tags)` or left with `onCancel`. */
function editForm(record, onSave, onCancel) {
  const words = el("textarea", { class: "textarea", rows: "3", maxlength: "20000", "aria-label": "Note" });
  words.value = record.content;
  const tagInput = el("input", { class: "input", type: "text", autocomplete: "off", "aria-label": "Tags" });
  tagInput.value = record.tags.join(", ");
  const save = el("button", { class: "btn primary sm", type: "button" }, icon("check"), "Save");
  const cancel = el("button", { class: "btn ghost sm", type: "button", onclick: onCancel }, "Cancel");
  save.addEventListener("click", () => {
    const text = words.value.trim();
    if (text === "") {
      words.focus();
      return;
    }
    save.disabled = true;
    onSave(text, parseTags(tagInput.value)).finally(() => {
      save.disabled = false;
    });
  });
  return el("div", { class: "stack tight grow" }, words, tagInput, el("div", { class: "row" }, el("span", { class: "spacer" }), cancel, save));
}

/**
 * One note with its tags, its age, who wrote it and buttons to correct or forget it. `similarity` is null unless the
 * row is a search hit; `actions` is `{ forget(record), edit(record, content, tags) }`.
 */
function memoryRow({ record, similarity, score }, actions) {
  const row = el("div", { class: "list-row", dataset: { memory: record.id } });
  const forget = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Forget this note",
      "aria-label": "Forget this note",
      onclick: () => actions.forget(record),
    },
    icon("trash"),
  );
  const edit = el("button", { class: "btn ghost sm icon-only", type: "button", title: "Edit this note", "aria-label": "Edit this note" }, icon("pencil"));
  const match =
    similarity === null
      ? null
      : el("span", { class: `badge ${similarity >= STRONG_MATCH ? "b-ok" : "b-muted"}` }, `${Math.round(similarity * 100)}% match`);
  function showNote() {
    row.replaceChildren(
      el(
        "div",
        { class: "row-main" },
        noteText(record.content),
        el(
          "div",
          { class: "row-meta" },
          match,
          record.tags.map((tag) => el("span", { class: "chip" }, tag)),
          el("span", { title: dateTimeFull(record.created_at) }, `Saved ${ago(record.created_at)}`),
          provenance(record),
          scoreLine(score),
        ),
      ),
      el("div", { class: "row-actions" }, edit, forget),
    );
  }
  edit.addEventListener("click", () => {
    row.replaceChildren(editForm(record, (content, tags) => actions.edit(record, content, tags), showNote));
  });
  showNote();
  return row;
}

/** What saving a note led to: remembered or merged, and the notes that say nearly the same. */
function outcomeNodes(saved, actions) {
  const verdict = saved.created
    ? "Remembered."
    : "Dot already remembers exactly this note; the tags were merged into it.";
  const nodes = [el("div", { class: "note" }, icon("check"), el("span", null, verdict))];
  if (saved.similar.length > 0) {
    nodes.push(
      el(
        "div",
        { class: "stack tight", dataset: { similar: "yes" } },
        el("div", { class: "note warn" }, icon("alert"), el("span", null, "Similar notes exist. Forget the ones that this note replaces:")),
        el("div", { class: "card" }, saved.similar.map((hit) => memoryRow(hit, actions))),
      ),
    );
  }
  return nodes;
}

export function mount(root, ctx) {
  let disposed = false;
  // Grows with every load, so a slow answer never overwrites a newer one.
  let ticket = 0;
  let hits = [];
  let total = 0;
  let shownQuery = "";
  // The ids of the listed notes that rest: no run was handed them for [memory] rest_days, or the reflection retired
  // them. They are listed apart; a search still finds them.
  let resting = new Set();

  const content = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: "20000",
    placeholder: "For example: I prefer short answers, and my invoices live in ~/Documents/Invoices.",
    "aria-label": "Note",
  });
  const tags = el("input", { class: "input", type: "text", autocomplete: "off", placeholder: "work, preferences", "aria-label": "Tags" });
  const add = el("button", { class: "btn primary", type: "button" }, icon("plus"), "Remember");
  const outcome = el("div", { class: "stack tight", hidden: true });
  const search = el("input", {
    class: "input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "500",
    placeholder: "Search by meaning or by words…",
    "aria-label": "Search notes",
  });
  const count = el("div", { class: "hint" });
  const holder = el("div", null, loadingPanel());
  const more = el("button", { class: "btn", type: "button", hidden: true }, "Load more");

  function paint() {
    count.textContent =
      shownQuery === ""
        ? plural(total, "note", "notes")
        : `${plural(hits.length, "result", "results")} for “${clip(shownQuery, 40)}”`;
    more.hidden = shownQuery !== "" || hits.length >= total;
    if (hits.length > 0) {
      const awake = hits.filter((hit) => !resting.has(hit.record.id));
      const asleep = hits.filter((hit) => resting.has(hit.record.id));
      const parts = [];
      if (awake.length > 0) {
        parts.push(el("div", { class: "card" }, awake.map((hit) => memoryRow(hit, rowActions))));
      }
      if (asleep.length > 0) {
        parts.push(
          el(
            "div",
            { class: "row section-head", dataset: { resting: "" } },
            el("div", { class: "section-label grow" }, "Resting"),
            el("span", { class: "hint" }, "No run was handed these lately; a search still finds them, runs no longer start with them."),
          ),
          el("div", { class: "card" }, asleep.map((hit) => memoryRow(hit, rowActions))),
        );
      }
      holder.replaceChildren(...parts);
    } else if (shownQuery === "") {
      holder.replaceChildren(emptyPanel("Nothing remembered yet", "Add a note above, or ask Dot to remember something during a run."));
    } else {
      holder.replaceChildren(emptyPanel("Nothing matches", "Try other words or a shorter query."));
    }
  }

  async function reload() {
    const mine = ++ticket;
    const text = search.value.trim();
    try {
      if (text === "") {
        const page = await api.get(`/memories${query({ limit: PAGE_SIZE, offset: 0 })}`);
        agentNames = page.agents;
        if (disposed || mine !== ticket) {
          return;
        }
        hits = page.items.map((record) => ({ record, similarity: null, score: page.scores[record.id] }));
        total = page.total;
        resting = new Set(page.resting);
      } else {
        const found = await api.get(`/memories/search${query({ q: text, limit: SEARCH_LIMIT })}`);
        if (disposed || mine !== ticket) {
          return;
        }
        hits = found;
        total = found.length;
        resting = new Set();
      }
      shownQuery = text;
      paint();
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      console.error("the notes could not be loaded", error);
      count.textContent = "";
      more.hidden = true;
      holder.replaceChildren(errorPanel(error, () => attempt(retry)));
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  async function loadMore() {
    const mine = ticket;
    more.disabled = true;
    try {
      const page = await api.get(`/memories${query({ limit: PAGE_SIZE, offset: hits.length })}`);
      if (disposed || mine !== ticket) {
        return;
      }
      hits = [...hits, ...page.items.map((record) => ({ record, similarity: null, score: page.scores[record.id] }))];
      total = page.total;
      resting = new Set([...resting, ...page.resting]);
      paint();
    } finally {
      more.disabled = false;
    }
  }

  async function forgetNote(record) {
    const confirmed = await confirmDialog({
      title: "Forget this note?",
      message: clip(record.content, QUOTE_CHARS),
      confirmLabel: "Forget",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/memories/${record.id}`);
    toast("ok", "Forgotten.");
    for (const node of root.querySelectorAll("[data-memory]")) {
      if (node.dataset.memory === record.id) {
        node.remove();
      }
    }
    // The warning about similar notes goes away with the last note it listed.
    for (const group of outcome.querySelectorAll("[data-similar]")) {
      if (group.querySelector("[data-memory]") === null) {
        group.remove();
      }
    }
    await reload();
  }

  async function editNote(record, text, tagList) {
    await api.patch(`/memories/${record.id}`, { content: text, tags: tagList });
    toast("ok", "Note updated.");
    await reload();
  }

  const rowActions = {
    forget: (record) => attempt(() => forgetNote(record)),
    edit: (record, text, tagList) => attempt(() => editNote(record, text, tagList)),
  };

  // True while a note is being saved: Enter does not look at the disabled button, so a second press is refused here,
  // or one note would be kept twice.
  let saving = false;

  async function save() {
    if (saving) {
      return;
    }
    const text = content.value.trim();
    if (text === "") {
      content.focus();
      return;
    }
    saving = true;
    add.disabled = true;
    try {
      const saved = await api.post("/memories", { content: text, tags: parseTags(tags.value) });
      if (disposed) {
        return;
      }
      content.value = "";
      tags.value = "";
      outcome.replaceChildren(...outcomeNodes(saved, rowActions));
      outcome.hidden = false;
      await reload();
    } finally {
      saving = false;
      add.disabled = false;
    }
  }

  const reloadSoon = debounce(() => {
    if (!disposed) {
      attempt(reload);
    }
  }, SEARCH_DELAY_MS);

  add.addEventListener("click", () => attempt(save));
  more.addEventListener("click", () => attempt(loadMore));
  search.addEventListener("input", reloadSoon);
  content.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      attempt(save);
    }
  });
  tags.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      attempt(save);
    }
  });

  // What Dot knows about you (item 93): the reflection's lines, read-only, and your own lines, which you edit.
  const dotLines = el("ul", { class: "owner-lines" });
  const ownLines = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: "6000",
    placeholder: "One line each, for example: I read Turkish; answer me in Turkish.",
    "aria-label": "Your lines",
  });
  const saveOwn = el("button", { class: "btn", type: "button" }, "Save your lines");
  const ownerHint = el("span", { class: "hint" });
  function paintOwner(section) {
    dotLines.replaceChildren(
      ...(section.dot_lines.length === 0
        ? [el("li", { class: "muted" }, "Nothing yet. The nightly reflection writes these lines from your notes and the day's runs.")]
        : section.dot_lines.map((line) => el("li", null, line))),
    );
    ownLines.value = section.owner_lines.join("\n");
    ownerHint.textContent = section.updated_at > 0 ? `Updated ${ago(section.updated_at)}` : "";
  }
  async function loadOwner() {
    paintOwner(await api.get("/memories/owner"));
  }
  saveOwn.addEventListener("click", () =>
    attempt(async () => {
      const lines = ownLines.value.split("\n").map((line) => line.trim()).filter((line) => line !== "");
      paintOwner(await api.put("/memories/owner", { lines }));
      toast("ok", "Your lines are kept; the reflection never rewrites them.");
    }),
  );

  root.append(
    paneHead("Notes that Dot keeps between runs. It recalls the ones that fit the task at hand.", null),
    el(
      "section",
      { class: "card card-pad stack" },
      el("div", { class: "row" }, el("div", { class: "section-label grow" }, "What Dot knows about you"), ownerHint),
      dotLines,
      field("Your lines", ownLines, "Corrections and facts in your own words; Dot's lines above never contradict them."),
      el("div", { class: "row" }, el("span", { class: "spacer" }), saveOwn),
    ),
    el(
      "section",
      { class: "card card-pad stack" },
      field("Remember something", content, "One fact, preference or instruction per note."),
      field("Tags", tags, "Optional, separated by commas."),
      el("div", { class: "row" }, el("span", { class: "spacer" }), el("span", { class: "hint" }, "⌘ / Ctrl + Enter"), add),
      outcome,
    ),
    el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, "Notes"), count),
    search,
    holder,
    el("div", { class: "cluster" }, more),
  );
  attempt(reload);
  attempt(loadOwner);
  return () => {
    disposed = true;
  };
}
