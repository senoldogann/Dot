// The Changes of a chat (item 368): the files its runs wrote or created with write_file and edit_file, read from their
// journals of file changes (`GET /api/runs/{id}/changes`), each with the lines added and removed and opening on its
// unified diff; a file without a diff says why. The side panel's Changes tab holds them and counts the files.

import { api } from "./api.js";
import { changeFormat, changeLayout, highlightLines, prettyJSON } from "./diffview.js";
import { el, plural, requestFile } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { panelSection, segmented } from "./panels.js";
import { prose } from "./prose.js";

/** How many rows a code view draws at a time (item 2017): a new file of thousands of lines draws its first rows
 * at once and the next as the reader scrolls to them. */
const ROWS_AT_A_TIME = 400;

/**
 * A line of a code view: its kind (a line added, removed or kept, a hunk's head, a line of a file), its numbers (the
 * old and the new for a diff, the one for a file) and its text.
 * @typedef {{ kind: "hunk" | "added" | "removed" | "context" | "file", numbers: (number | null)[], text: string }} CodeLine
 */

/** The coloured pieces of a line. */
function tokenNodes(tokens) {
  return tokens.map((token) => (token.kind === "plain" ? token.text : el("span", { class: `tok-${token.kind}` }, token.text)));
}

/** One row of a code view: its numbers, a sign for a line added or removed, its coloured text; a hunk's head as a quiet
 * band that names where the next lines stand. */
function codeRow(line, tokens, signed) {
  if (line.kind === "hunk") {
    return el("div", { class: "code-row code-hunk", role: "separator" }, el("span", { class: "code-hunk-text" }, line.text === "" ? "⋯" : `⋯ ${line.text}`));
  }
  const sign = line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " ";
  // A file's line is `code-file`: `.code-line` is a box of its own elsewhere (app.css).
  return el(
    "div",
    { class: `code-row code-${line.kind}` },
    el("span", { class: "code-nums", "aria-hidden": "true" }, ...line.numbers.map((number) => el("span", { class: "code-num" }, number === null ? "" : String(number)))),
    signed ? el("span", { class: "code-sign", "aria-hidden": "true" }, sign) : null,
    el("span", { class: "code-text" }, ...tokenNodes(tokens)),
  );
}

/** The tokens of each line, coloured in `language` (none: plain), each hunk read on its own. */
function lineTokens(lines, language) {
  if (language === null) {
    return lines.map((line) => [{ kind: "plain", text: line.text }]);
  }
  const tokens = [];
  let start = 0;
  for (let index = 0; index <= lines.length; index += 1) {
    if (index === lines.length || lines[index].kind === "hunk") {
      tokens.push(...highlightLines(lines.slice(start, index).map((line) => line.text), language));
      if (index < lines.length) {
        tokens.push([]);
      }
      start = index + 1;
    }
  }
  return tokens;
}

/** A view of code: no line wraps, it scrolls sideways, the numbers stand at its left; the rows are drawn
 * `ROWS_AT_A_TIME` at a time as the reader scrolls (item 2017). `signed` gives each row its + or −. */
function codeView(lines, language, signed) {
  const tokens = lineTokens(lines, language);
  const widest = Math.max(1, ...lines.flatMap((line) => line.numbers.map((number) => String(number ?? "").length)));
  const rows = el("div", { class: "code-lines" });
  const view = el("div", { class: "code-view", tabindex: "0", dataset: { signed: String(signed) } }, rows);
  // The numbers' column is as wide as the longest number (CSSOM: the page's policy refuses a style attribute).
  view.style.setProperty("--num-width", `${widest}ch`);
  let drawn = 0;
  const sentinel = el("div", { class: "code-more", "aria-hidden": "true" });
  const more = () => {
    const next = Math.min(lines.length, drawn + ROWS_AT_A_TIME);
    const nodes = [];
    for (let index = drawn; index < next; index += 1) {
      nodes.push(codeRow(lines[index], tokens[index], signed));
    }
    drawn = next;
    sentinel.remove();
    rows.append(...nodes);
    if (drawn < lines.length) {
      rows.append(sentinel);
    }
  };
  more();
  if (drawn < lines.length) {
    const watch = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        more();
        if (drawn >= lines.length) {
          watch.disconnect();
        }
      }
    }, { root: view, rootMargin: "400px" });
    watch.observe(sentinel);
  }
  return view;
}

/** The lines of a file's text, numbered from 1. */
function fileLines(text) {
  return text.split("\n").map((line, index) => ({ kind: "file", numbers: [index + 1], text: line }));
}

/** A new Markdown file: formatted as an answer is, its source a press away. */
function markdownView(text) {
  const holder = el("div", { class: "change-markdown" });
  const show = (id) => holder.replaceChildren(id === "formatted" ? el("div", { class: "file-markdown" }, prose(text)) : codeView(fileLines(text), "markdown", false));
  const tabs = segmented(
    [
      { id: "formatted", label: t("change.formatted"), hint: t("change.formattedHint") },
      { id: "source", label: t("change.source"), hint: t("change.sourceHint") },
    ],
    "formatted",
    show,
  ).element;
  tabs.classList.add("change-tabs");
  tabs.setAttribute("aria-label", t("change.view"));
  show("formatted");
  return el("div", { class: "change-new" }, tabs, holder);
}

/** What an opened change shows, by its file's format: a diff with both numbers, a new file read as the file (Markdown
 * formatted, JSON laid out), or why there is no diff. An image or a PDF opens in the file panel instead. */
function changeView(file, layout) {
  switch (layout.kind) {
    case "diff": {
      const lines = layout.rows.map((row) => ({ kind: row.kind, numbers: [row.old, row.new], text: row.text }));
      const format = changeFormat(file.path);
      const language = format.kind === "code" ? format.language : format.kind;
      return codeView(lines, language, true);
    }
    case "newFile": {
      if (layout.format.kind === "markdown") {
        return markdownView(layout.text);
      }
      if (layout.format.kind === "json") {
        return codeView(fileLines(prettyJSON(layout.text) ?? layout.text), "json", false);
      }
      return codeView(fileLines(layout.text), layout.format.kind === "code" ? layout.format.language : null, false);
    }
    case "note":
      return el("div", { class: "note" }, icon("info"), el("span", null, layout.note === "" ? t("change.noDiff") : layout.note));
    default:
      return null;
  }
}

/** The lines added and removed, each in its own tone; nothing for none. */
function counts(added, removed) {
  return el(
    "span",
    { class: "change-counts" },
    added > 0 ? el("span", { class: "change-add" }, `+${added}`) : null,
    removed > 0 ? el("span", { class: "change-del" }, `−${removed}`) : null,
  );
}

/** The button that shows the file `path` as it is now beside the conversation (item 1077): a page drawn, a PDF's
 * pages, a Markdown file formatted, a text. A press does not open the diff the row holds. */
function previewButton(path, name) {
  return el(
    "button",
    {
      class: "btn ghost sm icon-only change-preview",
      type: "button",
      title: `Show ${name} as it is now`,
      "aria-label": `Show ${name} as it is now`,
      dataset: { path },
      onclick: (event) => {
        event.preventDefault();
        event.stopPropagation();
        requestFile(event.currentTarget);
      },
    },
    icon("eye"),
  );
}

/** A file the chat's runs changed: its name over its folder, as the Files card has them, whether they created it or
 * it is gone, its counts, a button that shows it as it is now unless it is gone; opened, its change as its format reads. */
function fileRow(file, open) {
  const state = file.created ? "created" : file.gone ? "gone" : null;
  const cut = file.path.lastIndexOf("/");
  const name = file.path.slice(cut + 1);
  const head = el(
    "summary",
    { class: "change-head" },
    icon("file"),
    el(
      "span",
      { class: "change-path", title: file.path },
      el("span", { class: "change-name" }, name),
      cut <= 0 ? null : el("span", { class: "change-where" }, file.path.slice(0, cut)),
    ),
    state === null ? null : el("span", { class: "badge" }, state),
    counts(file.added, file.removed),
    file.gone ? null : previewButton(file.path, name),
    el("span", { class: "chev" }, icon("chevron")),
  );
  // What a change shows is drawn when the file is first opened (item 674): a chat's diffs run to thousands of lines,
  // and the section is drawn again after each file a run writes. An image or a PDF opens in the file panel, as it is now.
  const layout = changeLayout(file);
  const body = el("div", { class: "change-body" });
  const node = el("details", { class: "change", open: open && layout.kind !== "media" }, head, body);
  node.dataset.path = file.path;
  node.dataset.layout = layout.kind;
  if (layout.kind === "media") {
    head.addEventListener("click", (event) => {
      event.preventDefault();
      requestFile(head.querySelector(".change-preview") ?? head);
    });
  }
  const draw = () => {
    if (node.open && body.childElementCount === 0) {
      const view = changeView(file, layout);
      if (view !== null) {
        body.replaceChildren(view);
      }
    }
  };
  node.addEventListener("toggle", draw);
  draw();
  return node;
}

/**
 * The Changes section of the run `runId`'s chat: `refresh()` reads them again, a file opened staying open; `onCount(n)`
 * gets the number of files each time they changed, `onFiles(files)` the files themselves; `head`, a node over the files
 * (item 841: what the run changed, with its Undo).
 */
export function changesSection({ runId, onCount, onFiles, head = null }) {
  const body = el("div", { class: "changes" });
  // `head` stands over the files: the line of what the run changed and its Undo (item 841, runchanges.js).
  const section = panelSection({ id: "run-changes", title: "Changes", actions: null, body: el("div", { class: "changes-holder" }, head, body) });
  // What the section was last drawn from: the same changes read again are not drawn again.
  let drawn = null;

  async function refresh() {
    const opened = new Set([...body.querySelectorAll("details.change[open]")].map((node) => node.dataset.path));
    let view;
    try {
      view = await api.get(`/runs/${runId}/changes`);
    } catch (error) {
      drawn = null;
      body.replaceChildren(el("div", { class: "note bad" }, icon("alert"), el("span", null, `The changes could not be read: ${error.message}`)));
      return;
    }
    const read = JSON.stringify(view);
    if (read === drawn) {
      return;
    }
    drawn = read;
    section.setSummary(
      view.files.length === 0 ? null : el("span", { class: "changes-total" }, plural(view.files.length, "file", "files"), counts(view.added, view.removed)),
    );
    body.replaceChildren(
      ...(view.files.length === 0
        ? [el("div", { class: "small muted" }, "No file was written in this chat yet: the files its tasks write or edit show here.")]
        : view.files.map((file) => fileRow(file, opened.has(file.path)))),
    );
    onCount(view.files.length);
    onFiles(view.files);
  }

  return { element: section.element, refresh };
}
