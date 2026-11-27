// The chips and the finds of the sidebar's search (the owner's ask of 2026-11-27, after Grok's; the phone's home search is
// the same): All, Messages, Bots, Files over the list, in a row that scrolls sideways. The chats the search finds by their
// titles stay the list's own rows (sidebar.js); above them stand what the chats say (`GET /api/threads/search`), the bots
// (`GET /api/agents`) and the files (`GET /api/threads/search/files`), each kind under its heading, a few of each under
// All with "See all", every one under its chip. Bots and Files hide the list. A find in a message opens its chat at that
// message (chatfind.js); a file opens the chat it is in; a bot its conversation.

import { ApiError, api, query, sharedGet } from "./api.js";
import { DOT_ID } from "./actors.js";
import { findOnOpen } from "./chatfind.js";
import { clip, debounce, el } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { botsMatching, cutShort, markedWord, SEARCH_SCOPES, scopedFinds } from "./searchscope.js";

// How long the search waits after the last key before it asks the Mac, and how many finds it asks for.
const ASK_AFTER_MS = 300;
const MESSAGE_LIMIT = 20;
const FILE_LIMIT = 30;
const SCOPE_WORDS = { all: "search.all", messages: "search.messages", bots: "search.bots", files: "search.files" };

/** A snippet with its matched words (the daemon's `[...]`) in bold. */
function snippetNodes(snippet) {
  return snippet.split(/(\[[^\]]*\])/).map((part) => (part.startsWith("[") && part.endsWith("]") ? el("strong", null, part.slice(1, -1)) : part));
}

/** Why a search of the Mac could not be made, in its words. */
function why(error) {
  return error instanceof ApiError ? error.message : String(error);
}

/**
 * Build the chips and the finds. `chatTitle(id)` names a chat the list holds ("" for one it does not), `homeHref` is
 * Dot's conversation, `onScope(scope)` tells the sidebar which chip is chosen, `onFound()` that the finds came and
 * `onNavigate()` closes a phone's drawer. Returns `chips`, `finds`, `setQuery(text)`, `scope()` and `hasFinds()`.
 * @param {{ chatTitle: (id: string) => string, homeHref: string, onScope: (scope: string) => void, onFound: () => void, onNavigate: () => void }} options
 */
export function buildSearchFinds({ chatTitle, homeHref, onScope, onFound, onNavigate }) {
  let scope = "all";
  let words = "";
  let ticket = 0;
  /** @type {{ bots: any[], messages: any[], files: any[] }} */
  let found = { bots: [], messages: [], files: [] };
  /** @type {{ messages: string | null, files: string | null }} */
  let problems = { messages: null, files: null };
  let searching = false;
  const chipNodes = new Map();
  const chips = el(
    "div",
    { class: "side-chips", role: "group", "aria-label": t("search.kinds"), hidden: true },
    ...SEARCH_SCOPES.map((each) => {
      const chip = el("button", { class: "side-chip", type: "button", "aria-pressed": String(each === scope), dataset: { scope: each }, onclick: () => pick(each) }, t(SCOPE_WORDS[each]));
      chipNodes.set(each, chip);
      return chip;
    }),
  );
  const finds = el("div", { class: "side-finds", "aria-live": "polite", hidden: true });

  function pick(each) {
    scope = each;
    for (const [key, chip] of chipNodes) {
      chip.setAttribute("aria-pressed", String(key === scope));
    }
    onScope(scope);
    paint();
  }

  function section(kind, title, rows) {
    const more = scope === "all" && cutShort(found).has(kind);
    return el(
      "section",
      { class: "side-finds-group", dataset: { kind } },
      el(
        "div",
        { class: "side-finds-head" },
        el("h3", { class: "side-label" }, title),
        more ? el("button", { class: "btn ghost sm side-see-all", type: "button", onclick: () => pick(kind) }, t("search.seeAll")) : null,
      ),
      ...rows,
    );
  }

  function messageRow(hit) {
    const title = chatTitle(hit.thread_id);
    const word = markedWord(hit.snippet);
    const href = `#/runs/${hit.run_id}`;
    return el(
      "a",
      {
        class: "side-find",
        href,
        onclick: () => {
          if (word !== null) {
            findOnOpen(hit.run_id, word, hit.snippet);
          }
          onNavigate();
        },
      },
      title === "" ? null : el("span", { class: "side-find-title" }, clip(title, 120)),
      el("span", { class: "side-find-line" }, ...snippetNodes(hit.snippet)),
    );
  }

  function botRow({ record }) {
    return el(
      "a",
      { class: "side-find", href: record.id === DOT_ID ? homeHref : `#/agents/${record.id}`, onclick: onNavigate },
      el("span", { class: "side-find-title" }, record.name),
      record.role === "" ? null : el("span", { class: "side-find-line" }, clip(record.role, 160)),
    );
  }

  function fileRow(file) {
    const chat = chatTitle(file.thread_id);
    const place = file.kind === "attachment" ? t("search.attached") : file.path;
    return el(
      "a",
      { class: "side-find side-find-file", href: `#/runs/${file.run_id}`, title: file.path, onclick: onNavigate },
      el("span", { class: "side-find-title" }, icon(file.kind === "attachment" ? "paperclip" : "file"), el("span", null, file.name)),
      el("span", { class: "side-find-line" }, chat === "" ? place : `${place} · ${clip(chat, 80)}`),
    );
  }

  function paint() {
    const shown = scopedFinds(found, scope);
    const groups = [
      shown.messages.length === 0 ? null : section("messages", t("search.messages"), shown.messages.map(messageRow)),
      shown.bots.length === 0 ? null : section("bots", t("search.bots"), shown.bots.map(botRow)),
      shown.files.length === 0 ? null : section("files", t("search.files"), shown.files.map(fileRow)),
    ];
    const notes = [
      problems.messages !== null && (scope === "all" || scope === "messages") ? el("div", { class: "side-note" }, t("search.messagesFailed", { why: problems.messages })) : null,
      problems.files !== null && (scope === "all" || scope === "files") ? el("div", { class: "side-note" }, t("search.filesFailed", { why: problems.files })) : null,
    ];
    const nothing = !searching && groups.every((group) => group === null) && (scope === "bots" || scope === "files");
    // The native replaceChildren writes a null as the text "null": only the parts that are there go in.
    const parts = [
      ...groups,
      ...notes,
      searching ? el("div", { class: "side-note" }, t("search.searching")) : null,
      nothing ? el("div", { class: "side-note", dataset: { nothing: "" } }, t("search.nothing")) : null,
    ];
    finds.replaceChildren(...parts.filter((part) => part !== null));
    finds.hidden = words === "";
  }

  const ask = debounce(async () => {
    // The words were cleared while the ask waited: nothing to ask (the daemon refuses an empty search).
    if (words === "") {
      return;
    }
    const mine = ++ticket;
    const asked = words;
    const [messages, files, agents] = await Promise.allSettled([
      api.get(`/threads/search${query({ q: asked, limit: MESSAGE_LIMIT })}`),
      api.get(`/threads/search/files${query({ q: asked, limit: FILE_LIMIT })}`),
      sharedGet("/agents"),
    ]);
    if (mine !== ticket) {
      return;
    }
    searching = false;
    found = {
      // A find of a chat with no run has no page to open: it is left out.
      messages: messages.status === "fulfilled" ? messages.value.filter((hit) => hit.run_id !== null) : [],
      files: files.status === "fulfilled" ? files.value : [],
      bots: agents.status === "fulfilled" ? botsMatching(agents.value, asked) : [],
    };
    problems = {
      messages: messages.status === "rejected" ? why(messages.reason) : null,
      files: files.status === "rejected" ? why(files.reason) : null,
    };
    if (agents.status === "rejected") {
      console.warn("the agents could not be read for the search", agents.reason);
    }
    paint();
    onFound();
  }, ASK_AFTER_MS);

  return {
    chips,
    finds,
    /** The search's words as typed; the chips show while there are words, and All is chosen again when they go. */
    setQuery(text) {
      words = text.trim();
      chips.hidden = words === "";
      if (words === "") {
        ticket += 1;
        searching = false;
        found = { bots: [], messages: [], files: [] };
        problems = { messages: null, files: null };
        if (scope !== "all") {
          pick("all");
        }
        paint();
        return;
      }
      searching = true;
      problems = { messages: null, files: null };
      paint();
      void ask();
    },
    scope: () => scope,
    /** Whether the search found anything beyond the chats' titles, under its chip. */
    hasFinds() {
      const shown = scopedFinds(found, scope);
      return shown.messages.length + shown.bots.length + shown.files.length > 0;
    },
  };
}
