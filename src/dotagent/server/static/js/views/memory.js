// Settings › Memory (items 218 and 566): add a note, look things up by meaning or by words, narrow the notes to the lessons, the
// preferences or the ones saved while a run read outside content, correct a note and forget what is out of date. Under the
// notes: what Dot knows about you and how its learning fares. The notes are read a page at a time (item 218).

import { api, query } from "../api.js";
import { ago, clip, dateTime, dateTimeFull, debounce, el, joined, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, inlineError, inlineLoading, segmented, setCard, setRow, setSection } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const PAGE_SIZE = 30;
// The most notes the route reads at once (MemoriesQuery in server/views.py).
const MOST_READ = 200;
// The most notes read through to narrow them to a filter, the newest first: the route has no filter of its own.
const MOST_FILTERED = 2000;
const SEARCH_LIMIT = 30;
// The most hits the search route gives (SearchQuery in server/views.py): a filter on a search takes them all.
const SEARCH_MOST = 50;
const SEARCH_DELAY_MS = 250;
const STRONG_MATCH = 0.75;
const PREVIEW_CHARS = 420;
const QUOTE_CHARS = 160;

// What the notes can be narrowed to, by what each carries: its tags and whether the run that saved it had read outside
// content. A lesson is a note tagged "lesson" (agent/lessons.py); "preference" and "preferences" are the tags the page's own
// example uses.
const FILTERS = [
  { id: "all", label: "All", hint: "Every note" },
  { id: "lessons", label: "Lessons", hint: "The notes tagged lesson: what a run learned" },
  { id: "preferences", label: "Preferences", hint: "The notes tagged preference or preferences" },
  { id: "outside", label: "From outside content", hint: "The notes saved while a run read content third parties can author" },
];
const FILTER_TESTS = {
  all: () => true,
  lessons: (record) => record.tags.includes("lesson"),
  preferences: (record) => record.tags.some((tag) => tag === "preference" || tag === "preferences"),
  outside: (record) => record.tainted,
};
// What an empty filter says about itself.
const FILTER_EMPTY = {
  lessons: "A note tagged lesson shows here: Dot writes one when a run taught it something.",
  preferences: "Tag a note preference or preferences and it shows here.",
  outside: "A note saved while a run read content third parties can author shows here.",
};

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

/** `text` as text nodes, with what stands between backticks in code type: nodes to put in an element, never markup. */
export function codeText(text) {
  return text.split(/`([^`\n]+)`/).map((part, index) => (index % 2 === 1 ? el("code", { class: "mono" }, part) : part));
}

/** The words of a note; a long note shows its beginning, with a button to read the rest. */
function noteText(text) {
  const preview = `${text.slice(0, PREVIEW_CHARS)}…`;
  if (text.length <= PREVIEW_CHARS) {
    return el("div", { class: "note-text" }, codeText(text));
  }
  let expanded = false;
  const body = el("div", { class: "note-text" }, codeText(preview));
  const toggle = el("button", { class: "note-fold", type: "button" }, "Show more");
  toggle.addEventListener("click", () => {
    expanded = !expanded;
    body.replaceChildren(...codeText(expanded ? text : preview));
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

/** When a note was saved and by whom: "Saved 2h ago by a run" (the run's name leads to it), by the nightly reflection, or by you. */
function savedLine(record) {
  const by =
    record.source_run === "reflection"
      ? " by the nightly reflection"
      : record.source_run
        ? [" by ", el("a", { href: `#/runs/${record.source_run}` }, "a run")]
        : " by you";
  return el("span", { title: dateTimeFull(record.created_at) }, "Saved ", ago(record.created_at), by);
}

/** Whose lesson a note is, and how often runs used it. */
function usedLine(record) {
  const whose = record.agent_id === null ? null : el("span", null, `${agentNames[record.agent_id] ?? "An agent"}'s lesson`);
  const used =
    record.used_count > 0
      ? el("span", { title: dateTimeFull(record.last_used_at) }, `used ${plural(record.used_count, "time", "times")}, last ${ago(record.last_used_at)}`)
      : el("span", null, "not used yet");
  return [whose, used];
}

/**
 * How long the note holds (item 306): until when a note saved for a while holds or since when it ran out, and that another
 * note replaced one the nightly reflection put aside as outdated or folded into a note that says the same.
 */
function lifespan(record) {
  const until =
    record.expires_at === null
      ? null
      : el(
          "span",
          { title: dateTimeFull(record.expires_at) },
          record.expires_at * 1000 > Date.now() ? `Holds until ${dateTime(record.expires_at)}` : `Ran out ${dateTime(record.expires_at)}`,
        );
  const replaced =
    record.replaced_by !== null && record.tags.includes("rest") ? el("span", { title: record.replaced_by }, "Replaced by another note") : null;
  return [until, replaced];
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
  const row = el("div", { class: "note-row", dataset: { memory: record.id } });
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
    const [whose, used] = usedLine(record);
    row.replaceChildren(
      el(
        "div",
        { class: "note-main" },
        noteText(record.content),
        el(
          "div",
          { class: "note-meta" },
          record.tags.map((tag) => el("span", { class: "chip" }, tag)),
          match,
          record.tainted
            ? el(
                "span",
                { class: "badge b-warn", title: "The run that saved it had read content third parties can author." },
                icon("alert"),
                "From outside content",
              )
            : null,
          el("span", { class: "note-when" }, ...joined([whose, savedLine(record), ...lifespan(record), used, scoreLine(score)])),
        ),
      ),
      el("div", { class: "note-actions" }, edit, forget),
    );
  }
  edit.addEventListener("click", () => {
    row.replaceChildren(editForm(record, (content, tags) => actions.edit(record, content, tags), showNote));
  });
  showNote();
  return row;
}

/**
 * Make `nodes` the children of `parent`, in order, leaving in place the ones that already are: WebKit, the desktop app's
 * engine, moves the page when the rows in view are taken out and put back (item 204).
 */
function placeChildren(parent, nodes) {
  const wanted = new Set(nodes);
  for (const child of [...parent.children]) {
    if (!wanted.has(child)) {
      child.remove();
    }
  }
  nodes.forEach((node, index) => {
    const current = parent.children[index];
    if (current !== node) {
      parent.insertBefore(node, current ?? null);
    }
  });
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
        { class: "note-similar", dataset: { similar: "yes" } },
        el("div", { class: "note warn" }, icon("alert"), el("span", null, "Similar notes exist. Forget the ones that this note replaces:")),
        setCard(...saved.similar.map((hit) => memoryRow(hit, actions))),
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
  // What `hits` is: the notes read a page at a time ("list"), the notes read through and narrowed to a filter, of which
  // `drawn` show ("filtered"), or what a search found ("search").
  let mode = "list";
  let drawn = PAGE_SIZE;
  // How many notes were read through to narrow them, when that was fewer than the notes there are.
  let lookedAt = 0;
  let filter = "all";
  // The ids of the listed notes that rest: no run was handed them for [memory] rest_days, or the reflection retired
  // them. They are listed apart; a search still finds them.
  let resting = new Set();

  const content = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: "20000",
    placeholder: "Remember something… e.g. I prefer short answers, and my invoices live in ~/Documents/Invoices.",
    "aria-label": "Note",
  });
  const tags = el("input", {
    class: "input",
    type: "text",
    autocomplete: "off",
    placeholder: "work, preferences",
    title: "Optional, separated by commas",
    "aria-label": "Tags",
  });
  const add = el("button", { class: "btn primary", type: "button" }, icon("plus"), "Remember");
  const outcome = el("div", { class: "note-outcome", hidden: true });
  const search = el("input", {
    class: "input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "500",
    placeholder: "Search notes",
    "aria-label": "Search notes",
  });
  const count = el("span", { class: "note-count" });
  const holder = el("div", null, inlineLoading());
  const more = el("button", { class: "note-more", type: "button" }, "Show more notes");
  const lookedNote = el("p", { class: "note-looked", hidden: true });
  const foot = el("div", { class: "note-foot", hidden: true }, lookedNote, more);
  const awakeRows = el("div", { class: "note-rows" });
  // The hint under the name, on a line of its own: beside it, a hint longer than the row left the name no room.
  const restingHead = el(
    "div",
    { class: "note-rest-head", dataset: { resting: "" } },
    el("div", { class: "note-rest-title" }, "Resting"),
    el(
      "div",
      { class: "set-row-lead" },
      "Runs no longer start with these: no run was handed them lately, they did not help, their time ran out, or another note replaced them. A search still finds the ones with no rest tag that have not run out.",
    ),
  );
  const restingRows = el("div", { class: "note-rows" });
  // The note a line of a conversation leads to (item 313), named in the address after the tab's name: shown first, to be
  // read, changed or forgotten there; a note forgotten since is said to be gone.
  const askedId = new URLSearchParams(location.hash.split("?")[1] ?? "").get("note") ?? "";
  const asked = el("div", { hidden: true, dataset: { asked: "" } });
  // The rows painted, by note id, with the hit each shows: a paint keeps the row of a note that did not change, so that
  // the page keeps its place and a note being corrected its form (item 204).
  let rowsShown = new Map();

  function countText() {
    if (shownQuery !== "") {
      return `${plural(hits.length, "result", "results")} for “${clip(shownQuery, 40)}”`;
    }
    return mode === "filtered" ? `${hits.length} of ${total}` : String(total);
  }

  /** Whether the notes the page can show are all drawn: a search shows what it found, a list what it has read. */
  function allDrawn() {
    if (mode === "search") {
      return true;
    }
    return mode === "filtered" ? drawn >= hits.length : hits.length >= total;
  }

  function paint() {
    count.textContent = countText();
    lookedNote.hidden = !(mode === "filtered" && lookedAt < total);
    lookedNote.textContent = `Looked through the newest ${lookedAt} of ${total} notes.`;
    more.hidden = allDrawn();
    foot.hidden = more.hidden && lookedNote.hidden;
    const rows = new Map();
    const rowOf = (hit) => {
      const shown = JSON.stringify(hit);
      const kept = rowsShown.get(hit.record.id);
      const row = kept !== undefined && kept.shown === shown ? kept.row : memoryRow(hit, rowActions);
      rows.set(hit.record.id, { shown, row });
      return row;
    };
    const drawing = mode === "filtered" ? hits.slice(0, drawn) : hits;
    const awake = drawing.filter((hit) => !resting.has(hit.record.id)).map(rowOf);
    const asleep = drawing.filter((hit) => resting.has(hit.record.id)).map(rowOf);
    rowsShown = rows;
    if (hits.length > 0) {
      placeChildren(awakeRows, awake);
      placeChildren(restingRows, asleep);
      placeChildren(holder, [...(awake.length > 0 ? [awakeRows] : []), ...(asleep.length > 0 ? [restingHead, restingRows] : [])]);
    } else if (shownQuery !== "") {
      holder.replaceChildren(emptyPanel("search", "Nothing matches", filter === "all" ? "Try other words or a shorter query." : "Try other words, a shorter query or another filter."));
    } else if (filter !== "all") {
      holder.replaceChildren(emptyPanel("search", "No notes here", FILTER_EMPTY[filter]));
    } else {
      holder.replaceChildren(emptyPanel("brain", "Nothing remembered yet", "Add a note above, or ask Dot to remember something during a run."));
    }
  }

  /** The first `count` notes of the list (fewer when there are fewer), read as many at a time as the route allows. */
  async function readList(count) {
    let page = await api.get(`/memories${query({ limit: Math.min(count, MOST_READ), offset: 0 })}`);
    const read = { agents: page.agents, items: [...page.items], scores: { ...page.scores }, resting: [...page.resting], total: page.total };
    while (read.items.length < Math.min(count, read.total) && page.items.length > 0) {
      page = await api.get(`/memories${query({ limit: Math.min(count - read.items.length, MOST_READ), offset: read.items.length })}`);
      read.items.push(...page.items);
      Object.assign(read.scores, page.scores);
      read.resting.push(...page.resting);
      read.total = page.total;
    }
    return read;
  }

  async function reload() {
    const mine = ++ticket;
    const text = search.value.trim();
    const narrowed = filter !== "all";
    const passes = FILTER_TESTS[filter];
    try {
      if (text !== "") {
        const found = await api.get(`/memories/search${query({ q: text, limit: narrowed ? SEARCH_MOST : SEARCH_LIMIT })}`);
        if (disposed || mine !== ticket) {
          return;
        }
        hits = found.filter((hit) => passes(hit.record));
        total = hits.length;
        resting = new Set();
        mode = "search";
      } else if (narrowed) {
        const listed = await readList(MOST_FILTERED);
        if (disposed || mine !== ticket) {
          return;
        }
        agentNames = listed.agents;
        hits = listed.items.filter(passes).map((record) => ({ record, similarity: null, score: listed.scores[record.id] }));
        total = listed.total;
        lookedAt = listed.items.length;
        resting = new Set(listed.resting);
        mode = "filtered";
      } else {
        // After a change the list reads again as many notes as it showed, so that it keeps its length and the page its
        // place (item 204); a first read, or one after a search or a filter, takes a page.
        const listed = await readList(mode === "list" ? Math.max(PAGE_SIZE, hits.length) : PAGE_SIZE);
        if (disposed || mine !== ticket) {
          return;
        }
        agentNames = listed.agents;
        hits = listed.items.map((record) => ({ record, similarity: null, score: listed.scores[record.id] }));
        total = listed.total;
        resting = new Set(listed.resting);
        mode = "list";
      }
      shownQuery = text;
      paint();
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      console.error("the notes could not be loaded", error);
      count.textContent = "";
      foot.hidden = true;
      holder.replaceChildren(inlineError(error, () => attempt(retry)));
    }
  }

  async function retry() {
    holder.replaceChildren(inlineLoading());
    await reload();
  }

  async function loadAsked() {
    if (askedId === "") {
      return;
    }
    let shown;
    try {
      const record = await api.get(`/memories/${encodeURIComponent(askedId)}`);
      shown = memoryRow({ record, similarity: null, score: undefined }, rowActions);
    } catch (error) {
      if (error.status !== 404) {
        throw error;
      }
      shown = el("div", { class: "note note-gone" }, icon("info"), el("span", null, "This note is no longer in memory: it was forgotten since."));
    }
    if (disposed) {
      return;
    }
    asked.replaceChildren(setSection({ title: "The note from the conversation", body: setCard(shown) }));
    asked.hidden = false;
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

  /** More notes: the next page of the list, or the next of the notes a filter kept, which are all read already. */
  async function showMore() {
    if (mode === "filtered") {
      drawn += PAGE_SIZE;
      paint();
      return;
    }
    await loadMore();
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
    await loadAsked();
  }

  async function editNote(record, text, tagList) {
    await api.patch(`/memories/${record.id}`, { content: text, tags: tagList });
    toast("ok", "Note updated.");
    await reload();
    await loadAsked();
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

  const narrowing = segmented(FILTERS, filter, (id) => {
    filter = id;
    drawn = PAGE_SIZE;
    attempt(reload);
  });
  narrowing.element.setAttribute("aria-label", "Show notes");
  add.addEventListener("click", () => attempt(save));
  more.addEventListener("click", () => attempt(showMore));
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

  // What Dot knows about you (item 93): the reflection's lines, read-only, and your own lines, which you edit. Neither is
  // drawn before it is read: a box left empty by a failed read would, saved, wipe the lines you wrote.
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
    ownerSection.hidden = false;
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

  // How Dot's learning fares (review O25): when the reflection, the lessons and the skills last went well, their
  // failures in a row with the last error, and why learning has stopped, if it has.
  const learningCard = setCard();
  const learningHint = el("span", { class: "hint" });
  // A work that was skipped, for there was nothing to learn from, leaves no mark: with neither a success nor a failure
  // recorded, it has not failed, so the line says only that there is nothing yet.
  function learningRow(name, work) {
    const went =
      work.last_success !== null
        ? `last went well ${ago(work.last_success)}`
        : work.last_failure === null
          ? "nothing recorded yet"
          : "has not gone well yet";
    const failing = work.failures_in_row > 0 ? `; failed ${plural(work.failures_in_row, "time", "times")} in a row since: ${work.last_error}` : "";
    return setRow({ title: name, lead: `${went}${failing}` });
  }
  function paintLearning(view) {
    learningCard.replaceChildren(
      ...(view.health === null
        ? [setRow({ title: "Nothing yet", lead: "The first reflection, or the lessons or skill of a run, start the record." })]
        : [
            learningRow("The nightly reflection", view.health.reflection),
            learningRow("Lessons from runs", view.health.lessons),
            learningRow("Skills from runs", view.health.playbooks),
            // A step that [harness] step_time keeps from running, with no record yet, is off, not failing.
            !view.harness_on && view.health.harness.last_success === null && view.health.harness.last_failure === null
              ? setRow({
                  title: "The nightly self-improvement",
                  lead: el("span", null, "off. ", el("span", { class: "faint" }, "In the config file: [harness] step_time.")),
                })
              : learningRow("The nightly self-improvement", view.health.harness),
          ]),
    );
    learningHint.textContent = view.stalled === null ? "" : `Learning has stopped: ${view.stalled}`;
    learningSection.hidden = false;
  }
  async function loadLearning() {
    paintLearning(await api.get("/memories/learning"));
  }

  const composer = setCard(
    content,
    el("div", { class: "note-compose" }, el("label", { class: "note-tags" }, el("span", { class: "note-tags-label" }, "Tags"), tags), el("span", { class: "note-compose-hint", title: "⌘ or Ctrl + Enter keeps the note" }, "One fact per note · ⌘↵"), add),
    outcome,
  );
  composer.classList.add("note-composer");
  const notesSection = setSection({
    title: ["Notes", count],
    actions: el("div", { class: "note-tools" }, narrowing.element, el("label", { class: "note-search" }, icon("search"), search)),
    body: el("div", { class: "set-card note-card" }, holder, foot),
  });
  notesSection.classList.add("notes-section");
  const ownerSection = setSection({
    title: "What Dot knows about you",
    actions: ownerHint,
    body: setCard(
      setRow({ title: "Learned by reflection", body: dotLines }),
      setRow({
        title: "Your lines",
        lead: "Corrections and facts in your own words; Dot's lines above never contradict them.",
        body: el("div", { class: "owner-edit" }, ownLines, el("div", { class: "owner-save" }, saveOwn)),
      }),
    ),
  });
  ownerSection.hidden = true;
  const learningSection = setSection({ title: "How learning fares", actions: learningHint, body: learningCard });
  learningSection.hidden = true;
  learningSection.dataset.learning = "";
  learningSection.classList.add("learning-section");

  ctx.head.setLead("Notes Dot keeps between runs. It recalls the ones that fit the task at hand.");
  root.append(asked, el("div", { class: "set-sections" }, composer, notesSection, ownerSection, learningSection));
  attempt(reload);
  attempt(loadAsked);
  attempt(loadOwner);
  attempt(loadLearning);
  return () => {
    disposed = true;
  };
}
