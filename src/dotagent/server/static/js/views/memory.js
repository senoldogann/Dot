// Settings › Memory (items 218 and 566): add a note, look things up by meaning or by words, narrow the notes to the lessons, the
// preferences or the ones saved while a run read outside content, correct a note (in a dialog, item 615) and forget what
// is out of date. Under the
// notes: what Dot knows about you and how its learning fares. The notes are read a page at a time (item 218). Item 584: the
// notes can be listed by when and how often they were used, pinned (a pinned note never rests) and, when the nightly
// reflection merged notes that say the same, the merge taken back; the notes that rested long are proposed to be forgotten,
// and a forgotten note waits in a trash (memorylife.js).

import { api, query } from "../api.js";
import { ago, clip, dateTime, dateTimeFull, debounce, el, joined, } from "../dom.js";
import { icon } from "../icons.js";
import { changesSection, orderControl, proposedSection, trashSection, waitingSection } from "../memorylife.js";
import { moreButton } from "../menu.js";
import { emptyPanel, field, inlineError, inlineLoading, segmented, setCard, setRow, setSection } from "../panels.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";
import { t, tCount } from "../i18n.js";

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
const QUOTE_CHARS = 160;

// What the notes can be narrowed to, by what each carries: its tags and whether the run that saved it had read outside
// content. A lesson is a note tagged "lesson" (agent/lessons.py); "preference" and "preferences" are the tags the page's own
// example uses.
const FILTERS = [
  { id: "all", label: t("views.memory.all"), hint: t("views.memory.everyNote") },
  { id: "lessons", label: t("views.memory.lessons"), hint: t("views.memory.theNotesTaggedLessonWhat") },
  { id: "preferences", label: t("views.memory.preferences"), hint: t("views.memory.theNotesTaggedPreferenceOr") },
  { id: "outside", label: t("views.memory.fromOutsideContent"), hint: t("views.memory.theNotesSavedWhileA") },
];
const FILTER_TESTS = {
  all: () => true,
  lessons: (record) => record.tags.includes("lesson"),
  preferences: (record) => record.tags.some((tag) => tag === "preference" || tag === "preferences"),
  outside: (record) => record.tainted,
};
// What an empty filter says about itself.
const FILTER_EMPTY = {
  lessons: t("views.memory.aNoteTaggedLessonShows"),
  preferences: t("views.memory.tagANotePreferenceOr"),
  outside: t("views.memory.aNoteSavedWhileA"),
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

/** How the runs a note was handed to ended: "Handed to 5 runs, 4 done"; nothing until a run was handed it. */
export function scoreLine(score) {
  if (score === undefined || score.handed === 0) {
    return null;
  }
  return el(
    "span",
    { title: t("views.memory.doneEndedDoneFailedFailed", { done: score.done, failed: score.failed }) },
    t("views.memory.handedToPartDoneDone", { part: tCount(score.handed, "views.memory.countTask", "views.memory.countTasks"), done: score.done }),
  );
}

// The agents by id, from the page, so a lesson can say whose it is.
let agentNames = {};

// Who saved a note, by its origin (item 1864), which Dot sets from the run that saved it: a note stored before origins
// were kept, whose run is gone, has none and says "a task".
const ORIGIN_TASKS = {
  chat: t("views.memory.aTaskInAChat"),
  schedule: t("views.memory.aScheduledTask"),
  watcher: t("views.memory.aWatcherSTask"),
  helper: t("views.memory.aHelperTask"),
  initiative: t("views.memory.dotOnItsOwn"),
};

/** When a note was saved and by whom: "Saved 2h ago by a scheduled task" (the task leads to its run), by the nightly reflection, or by you. */
function savedLine(record) {
  const by =
    record.origin === "reflection" || record.source_run === "reflection"
      ? t("views.memory.byTheNightlyReflection")
      : record.source_run
        ? [" by ", el("a", { href: `#/runs/${record.source_run}` }, ORIGIN_TASKS[record.origin] ?? t("views.memory.aTask"))]
        : t("views.memory.byYou");
  return el("span", { title: dateTimeFull(record.created_at) }, t("views.memory.saved"), ago(record.created_at), by);
}

/** Whose lesson a note is, and how often runs used it. */
function usedLine(record) {
  const whose = record.agent_id === null ? null : el("span", null, t("views.memory.valueSLesson", { value: agentNames[record.agent_id] ?? t("views.memory.anAgent") }));
  const used =
    record.used_count > 0
      ? el("span", { title: dateTimeFull(record.last_used_at) }, t("views.memory.usedPartLastAgo", { part: tCount(record.used_count, "views.memory.countTime", "views.memory.countTimes"), ago: ago(record.last_used_at) }))
      : el("span", null, t("views.memory.notUsedYet"));
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
          record.expires_at * 1000 > Date.now() ? t("views.memory.holdsUntilDatetime", { dateTime: dateTime(record.expires_at) }) : t("views.memory.ranOutDatetime", { dateTime: dateTime(record.expires_at) }),
        );
  const replaced =
    record.replaced_by !== null && record.tags.includes("rest") ? el("span", { title: record.replaced_by }, t("views.memory.replacedByAnotherNote")) : null;
  const merged = record.merged_into === null ? null : el("span", { title: record.merged_into }, t("views.memory.mergedIntoAnotherNote"));
  return [until, replaced, merged];
}

/** Since when a note rests, for the notes that rest by a date (item 584): nothing for one awake. */
function restedLine(rested) {
  return rested === undefined ? null : el("span", { title: dateTimeFull(rested) }, t("views.memory.restingSinceDatetime", { dateTime: dateTime(rested) }));
}

/** What a merged note says of the notes it was made of (item 584): how many, and that the merge can be taken back. */
function mergedBadge(record) {
  return record.merged_from.length === 0
    ? null
    : el(
        "span",
        { class: "badge b-muted", title: t("views.memory.theNightlyReflectionMergedNotes") },
        icon("layers"),
        t("views.memory.mergedFromPart", { part: tCount(record.merged_from.length, "views.memory.countNote", "views.memory.countNotes") }),
      );
}

// The tags a folded note shows; the rest are counted (item 937).
const FOLDED_TAGS = 2;

/** The tags of a folded note: the first few, and how many more (item 937). */
function foldedTags(tags, most) {
  const shown = tags.slice(0, most).map((tag) => el("span", { class: "chip" }, tag));
  return tags.length > most ? [...shown, el("span", { class: "chip note-more-tags", title: tags.slice(most).join(", ") }, `+${tags.length - most}`)] : shown;
}

/**
 * One note with its tags, its age, who wrote it and its "…" to pin, correct or forget it (and to take back the merge that
 * made it). `similarity` is null unless the row is a search hit; `rested` is since when the note rests, when it does by a
 * date; `actions` is `{ forget(record), edit(record), pin(record), unmerge(record), fold }`, `edit` opening the dialog that
 * corrects the note (item 615), `fold` `{ isOpen(id), toggle(id) }` (item 937): a note is folded to its first two lines,
 * a few tags and its age until it is opened.
 */
function memoryRow({ record, similarity, score, rested }, actions) {
  const row = el("div", { class: "note-row", dataset: { memory: record.id } });
  const pinned = record.pinned_at !== null;
  // One action a row (item 848): its "…" holds the pin, Edit, the merge's Undo and Forget.
  const more = moreButton({
    name: t("views.memory.thisNote"),
    items: () => [
      { label: pinned ? t("views.memory.takeThePinOff") : t("views.memory.pinIt"), icon: "pin", run: () => actions.pin(record) },
      { label: t("views.memory.edit"), icon: "pencil", run: () => actions.edit(record) },
      ...(record.merged_from.length === 0 ? [] : [{ label: t("views.memory.undoTheMerge"), icon: "undo", run: () => actions.unmerge(record) }]),
      "separator",
      { label: t("views.memory.forget"), icon: "trash", danger: true, run: () => actions.forget(record) },
    ],
  });
  const match =
    similarity === null
      ? null
      : el("span", { class: `badge ${similarity >= STRONG_MATCH ? "b-ok" : "b-muted"}` }, t("views.memory.roundMatch", { round: Math.round(similarity * 100) }));
  /** The badges of a note, folded or open: its match, its pin, a run's wish that it go everywhere, its merge and its taint. */
  const badges = () => [
    match,
    pinned ? el("span", { class: "badge b-muted", title: t("views.memory.aPinnedNoteNeverRests") }, icon("pin"), t("views.memory.pinned")) : null,
    // A pinned note a task saved while reading outside content is handed to no task until the owner vouches for its words.
    pinned && record.tainted
      ? el("span", { class: "badge b-warn", title: t("views.memory.tasksAreNotHandedThis") }, t("views.memory.notHandedToTasks"))
      : null,
    // A run's core note goes to every run only once the owner pins it; till then it goes where it fits (item 613).
    !pinned && record.source_run !== null && record.tags.includes("core")
      ? el(
          "span",
          {
            class: "badge b-muted note-asks",
            title: t("views.memory.aTaskAskedThatThis"),
          },
          t("views.memory.alwaysOncePinned"),
        )
      : null,
    mergedBadge(record),
    record.proposed ? el("span", { class: "badge b-muted", title: t("views.memory.aTaskProposedNewWords") }, t("views.memory.changeProposed")) : null,
    record.tainted
      ? el("span", { class: "badge b-warn", title: t("views.memory.theTaskThatSavedIt") }, icon("alert"), t("views.memory.fromOutsideContent"))
      : null,
  ];
  /** The button that opens or folds the note, its words saying which. */
  function toggle(open) {
    return el(
      "button",
      {
        class: "note-toggle",
        type: "button",
        "aria-expanded": String(open),
        "aria-label": open ? t("views.memory.foldTheNote") : t("views.memory.openTheNote"),
        onclick: () => {
          actions.fold.toggle(record.id);
          paintRow();
        },
      },
      icon("chevron"),
    );
  }
  function showFolded() {
    row.classList.add("note-folded");
    row.replaceChildren(
      el(
        "div",
        { class: "note-main" },
        el(
          "button",
          {
            class: "note-head",
            type: "button",
            onclick: () => {
              actions.fold.toggle(record.id);
              paintRow();
            },
          },
          el("span", { class: "note-text note-clamp" }, codeText(record.content)),
        ),
        el(
          "div",
          { class: "note-meta" },
          foldedTags(record.tags, FOLDED_TAGS),
          badges(),
          el("span", { class: "note-age", title: dateTimeFull(record.created_at) }, ago(record.created_at)),
        ),
      ),
      el("div", { class: "note-actions" }, toggle(false), more),
    );
  }
  function paintRow() {
    if (actions.fold.isOpen(record.id)) {
      showNote();
    } else {
      showFolded();
    }
  }
  function showNote() {
    row.classList.remove("note-folded");
    const [whose, used] = usedLine(record);
    row.replaceChildren(
      el(
        "div",
        { class: "note-main" },
        el("div", { class: "note-text" }, codeText(record.content)),
        el(
          "div",
          { class: "note-meta" },
          record.tags.map((tag) => el("span", { class: "chip" }, tag)),
          badges(),
          el("span", { class: "note-when" }, ...joined([whose, savedLine(record), restedLine(rested), ...lifespan(record), used, scoreLine(score)])),
        ),
      ),
      el("div", { class: "note-actions" }, toggle(true), more),
    );
  }
  paintRow();
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
    ? t("views.memory.remembered")
    : t("views.memory.dotAlreadyRemembersExactlyThis");
  const nodes = [el("div", { class: "note" }, icon("check"), el("span", null, verdict))];
  if (saved.similar.length > 0) {
    nodes.push(
      el(
        "div",
        { class: "note-similar", dataset: { similar: "yes" } },
        el("div", { class: "note warn" }, icon("alert"), el("span", null, t("views.memory.similarNotesExistForgetThe"))),
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
  // The order the notes are listed in (item 584): see memorylife.js.
  let order = "changed";

  const content = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: "20000",
    placeholder: t("views.memory.rememberSomethingEGI"),
    "aria-label": t("views.memory.note"),
  });
  const tags = el("input", {
    class: "input",
    type: "text",
    autocomplete: "off",
    placeholder: t("views.memory.workPreferences"),
    title: t("views.memory.optionalSeparatedByCommas"),
    "aria-label": t("views.memory.tags"),
  });
  const add = el("button", { class: "btn primary", type: "button" }, icon("plus"), t("views.memory.remember"));
  const outcome = el("div", { class: "note-outcome", hidden: true });
  const search = el("input", {
    class: "input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "500",
    placeholder: t("views.memory.searchNotes"),
    "aria-label": t("views.memory.searchNotes"),
  });
  const count = el("span", { class: "note-count" });
  const holder = el("div", null, inlineLoading());
  const more = el("button", { class: "note-more", type: "button" }, t("views.memory.showMoreNotes"));
  const lookedNote = el("p", { class: "note-looked", hidden: true });
  const foot = el("div", { class: "note-foot", hidden: true }, lookedNote, more);
  const awakeRows = el("div", { class: "note-rows" });
  // The hint under the name, on a line of its own: beside it, a hint longer than the row left the name no room.
  const restingHead = el(
    "div",
    { class: "note-rest-head", dataset: { resting: "" } },
    el("div", { class: "note-rest-title" }, t("views.memory.resting")),
    el(
      "div",
      { class: "set-row-lead" },
      t("views.memory.tasksNoLongerStartWith"),
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
      return t("views.memory.partForClip", { part: tCount(hits.length, "views.memory.countResult", "views.memory.countResults"), clip: clip(shownQuery, 40) });
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
    lookedNote.textContent =
      order === "changed"
        ? t("views.memory.lookedThroughTheNewestLookedat", { lookedAt, total })
        : t("views.memory.lookedThroughTheFirstLookedat", { lookedAt, total });
    more.hidden = allDrawn();
    foot.hidden = more.hidden && lookedNote.hidden;
    orderBox.setDisabled(mode === "search" ? t("views.memory.aSearchListsTheNotes") : null);
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
      holder.replaceChildren(emptyPanel("search", t("views.memory.nothingMatches"), filter === "all" ? t("views.memory.tryOtherWordsOrA") : t("views.memory.tryOtherWordsAShorter")));
    } else if (filter !== "all") {
      holder.replaceChildren(emptyPanel("search", t("views.memory.noNotesHere"), FILTER_EMPTY[filter]));
    } else {
      holder.replaceChildren(emptyPanel("brain", t("views.memory.nothingRememberedYet"), t("views.memory.addANoteAboveOr")));
    }
  }

  /** The first `count` notes of the list (fewer when there are fewer), read as many at a time as the route allows. */
  async function readList(count) {
    let page = await api.get(`/memories${query({ limit: Math.min(count, MOST_READ), offset: 0, order })}`);
    const read = {
      agents: page.agents,
      items: [...page.items],
      scores: { ...page.scores },
      resting: [...page.resting],
      rested: { ...page.rested },
      total: page.total,
    };
    while (read.items.length < Math.min(count, read.total) && page.items.length > 0) {
      page = await api.get(`/memories${query({ limit: Math.min(count - read.items.length, MOST_READ), offset: read.items.length, order })}`);
      read.items.push(...page.items);
      Object.assign(read.scores, page.scores);
      Object.assign(read.rested, page.rested);
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
        hits = listed.items.filter(passes).map((record) => ({ record, similarity: null, score: listed.scores[record.id], rested: listed.rested[record.id] }));
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
        hits = listed.items.map((record) => ({ record, similarity: null, score: listed.scores[record.id], rested: listed.rested[record.id] }));
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
      shown = memoryRow({ record, similarity: null, score: undefined, rested: undefined }, rowActions);
    } catch (error) {
      if (error.status !== 404) {
        throw error;
      }
      shown = el("div", { class: "note note-gone" }, icon("info"), el("span", null, t("views.memory.thisNoteIsNoLonger")));
    }
    if (disposed) {
      return;
    }
    asked.replaceChildren(setSection({ title: t("views.memory.theNoteFromTheConversation"), body: setCard(shown) }));
    asked.hidden = false;
  }

  async function loadMore() {
    const mine = ticket;
    more.disabled = true;
    try {
      const page = await api.get(`/memories${query({ limit: PAGE_SIZE, offset: hits.length, order })}`);
      if (disposed || mine !== ticket) {
        return;
      }
      hits = [...hits, ...page.items.map((record) => ({ record, similarity: null, score: page.scores[record.id], rested: page.rested[record.id] }))];
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
      title: t("views.memory.forgetThisNote"),
      message: clip(record.content, QUOTE_CHARS),
      confirmLabel: t("views.memory.forget"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/memories/${record.id}`);
    toast("ok", t("views.memory.forgotten"));
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
    // A note forgotten is in the trash now, and may have been one that was proposed to be forgotten.
    await refreshLife();
  }

  /** Pin a note so that it never rests (a resting note wakes), or take the pin off. */
  async function pinNote(record) {
    const pinned = record.pinned_at === null;
    await api.put(`/memories/${encodeURIComponent(record.id)}/pin`, { pinned });
    toast("ok", pinned ? t("views.memory.pinnedThisNoteNeverRests") : t("views.memory.thePinIsOffThis"));
    await reload();
    await loadAsked();
    await refreshLife();
  }

  /** Take back the merge that made a note, after a yes: its sources wake apart and it goes to the trash. */
  async function unmergeNote(record) {
    const confirmed = await confirmDialog({
      title: t("views.memory.undoThisMerge"),
      message: t(record.merged_from.length === 1 ? "views.memory.unmergeOne" : "views.memory.unmergeMany", { count: record.merged_from.length }),
      confirmLabel: t("views.memory.undoTheMerge"),
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.post(`/memories/${encodeURIComponent(record.id)}/unmerge`, {});
    toast("ok", t("views.memory.theMergeIsUndone"));
    await reload();
    await loadAsked();
    await refreshLife();
  }

  async function editNote(record, text, tagList) {
    await api.patch(`/memories/${record.id}`, { content: text, tags: tagList });
    toast("ok", t("views.memory.noteUpdated"));
    // The daemon took the change: reading the notes again failing says nothing of the save, so it does not fail the
    // dialog (the review of item 615); the page shows what it read last.
    try {
      await reload();
      await loadAsked();
    } catch (error) {
      console.warn("the notes could not be read again", error);
    }
  }

  /** The words and tags of a note, in a dialog (item 615); the notes are read again once the daemon took the change. The
   * Remember box at the head of the page stays where it is: it is the page's main input. */
  function openEdit(record) {
    const words = el("textarea", { class: "textarea", rows: "6", maxlength: "20000", "aria-label": t("views.memory.note") });
    words.value = record.content;
    const tagInput = el("input", { class: "input", type: "text", autocomplete: "off", "aria-label": t("views.memory.tags") });
    tagInput.value = record.tags.join(", ");
    formDialog({
      title: t("views.memory.editNote"),
      description: t("views.memory.whatDotRemembersInYour"),
      body: [field(t("views.memory.note"), words, ""), field(t("views.memory.tags"), tagInput, t("views.memory.optionalSeparatedByCommas2"))],
      submitLabel: t("views.memory.save"),
      // The notes are drawn again once one is saved: the row's own "…" is found anew.
      returnFocus: () => document.querySelector(`[data-memory="${record.id}"] .row-more`),
      onSubmit: async () => {
        const text = words.value.trim();
        if (text === "") {
          words.focus();
          return false;
        }
        await editNote(record, text, parseTags(tagInput.value));
        return true;
      },
    });
  }

  // Which notes are open (item 937): folded until opened, all of them while "Open all" holds, but those folded since.
  let allOpen = false;
  const flipped = new Set();
  const expandAll = el("button", { class: "btn ghost sm note-expand", type: "button", "aria-pressed": "false" }, t("views.memory.openAll"));
  const rowActions = {
    forget: (record) => attempt(() => forgetNote(record)),
    edit: (record) => openEdit(record),
    pin: (record) => attempt(() => pinNote(record)),
    unmerge: (record) => attempt(() => unmergeNote(record)),
    fold: {
      isOpen: (id) => allOpen !== flipped.has(id),
      toggle: (id) => {
        if (!flipped.delete(id)) {
          flipped.add(id);
        }
      },
    },
  };
  expandAll.addEventListener("click", () => {
    allOpen = !allOpen;
    flipped.clear();
    expandAll.textContent = allOpen ? t("views.memory.foldAll") : t("views.memory.openAll");
    expandAll.setAttribute("aria-pressed", String(allOpen));
    rowsShown = new Map();
    paint();
  });

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

  const orderBox = orderControl(order, (id) => {
    order = id;
    drawn = PAGE_SIZE;
    attempt(reload);
  });
  // The notes proposed to be forgotten and the trash of the ones the owner forgot (item 584): each is drawn only while it
  // holds a note, and a decision in either reads the notes again.
  const lifeHooks = {
    noteText: (words) => el("div", { class: "note-text" }, codeText(words)),
    onDecided: () => {
      if (!disposed) {
        attempt(reload);
        attempt(refreshLife);
      }
    },
  };
  const changes = changesSection(lifeHooks);
  const waiting = waitingSection(lifeHooks);
  const proposed = proposedSection(lifeHooks);
  const trash = trashSection(lifeHooks);
  async function refreshLife() {
    await Promise.all([changes.load(), waiting.load(), proposed.load(), trash.load()]);
  }
  const narrowing = segmented(FILTERS, filter, (id) => {
    filter = id;
    drawn = PAGE_SIZE;
    attempt(reload);
  });
  narrowing.element.setAttribute("aria-label", t("views.memory.showNotes"));
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
    placeholder: t("views.memory.oneLineEachForExample"),
    "aria-label": t("views.memory.yourLines"),
  });
  const saveOwn = el("button", { class: "btn", type: "button" }, t("views.memory.saveYourLines"));
  const ownerHint = el("span", { class: "hint" });
  function paintOwner(section) {
    dotLines.replaceChildren(
      ...(section.dot_lines.length === 0
        ? [el("li", { class: "muted" }, t("views.memory.nothingYetTheNightlyReflection"))]
        : section.dot_lines.map((line) => el("li", null, line))),
    );
    ownLines.value = section.owner_lines.join("\n");
    ownerHint.textContent = section.updated_at > 0 ? t("views.memory.updatedAgo", { ago: ago(section.updated_at) }) : "";
    ownerSection.hidden = false;
  }
  async function loadOwner() {
    paintOwner(await api.get("/memories/owner"));
  }
  saveOwn.addEventListener("click", () =>
    attempt(async () => {
      const lines = ownLines.value.split("\n").map((line) => line.trim()).filter((line) => line !== "");
      paintOwner(await api.put("/memories/owner", { lines }));
      toast("ok", t("views.memory.yourLinesAreKeptThe"));
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
        ? t("views.memory.lastWentWellAgo", { ago: ago(work.last_success) })
        : work.last_failure === null
          ? t("views.memory.nothingRecordedYet")
          : t("views.memory.hasNotGoneWellYet");
    const failing = work.failures_in_row > 0 ? t("views.memory.failedPartInARow", { part: tCount(work.failures_in_row, "views.memory.countTime", "views.memory.countTimes"), last_error: work.last_error }) : "";
    return setRow({ title: name, lead: `${went}${failing}` });
  }
  function paintLearning(view) {
    learningCard.replaceChildren(
      ...(view.health === null
        ? [setRow({ title: t("views.memory.nothingYet"), lead: t("views.memory.theFirstReflectionOrThe") })]
        : [
            learningRow(t("views.memory.theNightlyReflection"), view.health.reflection),
            learningRow(t("views.memory.lessonsFromTasks"), view.health.lessons),
            learningRow(t("views.memory.skillsFromTasks"), view.health.playbooks),
            // A step its schedule keeps from running is off, not failing, and says so though it once ran (item 613: the
            // page said it last went well days after it was switched off); it is switched on on its page (item 721).
            !view.harness_on
              ? setRow({
                  title: t("views.memory.theNightlySelfImprovement"),
                  lead: el(
                    "span",
                    null,
                    view.health.harness.last_success === null ? t("views.memory.off") : t("views.memory.offItLastWentWell", { ago: ago(view.health.harness.last_success) }),
                    t("views.memory.switchItOnInSettings"),
                  ),
                })
              : learningRow(t("views.memory.theNightlySelfImprovement"), view.health.harness),
          ]),
    );
    learningHint.textContent = view.stalled === null ? "" : t("views.memory.learningHasStoppedStalled", { stalled: view.stalled });
    learningSection.hidden = false;
  }
  async function loadLearning() {
    paintLearning(await api.get("/memories/learning"));
  }

  const composer = setCard(
    content,
    el(
      "div",
      { class: "note-compose" },
      el("label", { class: "note-tags" }, el("span", { class: "note-tags-label" }, t("views.memory.tags")), tags),
      // The key is a keyboard's: a touch screen has none, and the line says only what a note holds (item 576).
      el("span", { class: "note-compose-hint", title: t("views.memory.orCtrlEnterKeepsThe") }, t("views.memory.oneFactPerNote"), el("span", { class: "note-compose-keys" }, " · ⌘↵")),
      add,
    ),
    outcome,
  );
  composer.classList.add("note-composer");
  const notesSection = setSection({
    title: [t("views.memory.notes"), count],
    actions: el("div", { class: "note-tools" }, narrowing.element, el("label", { class: "note-search" }, icon("search"), search)),
    body: el("div", { class: "set-card note-card" }, el("div", { class: "note-bar" }, expandAll, orderBox.element), holder, foot),
  });
  notesSection.classList.add("notes-section");
  const ownerSection = setSection({
    title: t("views.memory.whatDotKnowsAboutYou"),
    actions: ownerHint,
    body: setCard(
      setRow({ title: t("views.memory.learnedByReflection"), body: dotLines }),
      setRow({
        title: t("views.memory.yourLines"),
        lead: t("views.memory.correctionsAndFactsInYour"),
        body: el("div", { class: "owner-edit" }, ownLines, el("div", { class: "owner-save" }, saveOwn)),
      }),
    ),
  });
  ownerSection.hidden = true;
  const learningSection = setSection({ title: t("views.memory.howLearningFares"), actions: learningHint, body: learningCard });
  learningSection.hidden = true;
  learningSection.dataset.learning = "";
  learningSection.classList.add("learning-section");

  ctx.head.setLead(t("views.memory.notesDotKeepsBetweenTasks"));
  root.append(asked, el("div", { class: "set-sections" }, composer, changes.element, waiting.element, proposed.element, notesSection, trash.element, ownerSection, learningSection));
  attempt(reload);
  attempt(refreshLife);
  attempt(loadAsked);
  attempt(loadOwner);
  attempt(loadLearning);
  return () => {
    disposed = true;
  };
}
