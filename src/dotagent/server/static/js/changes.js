// The Changes of a chat (item 368): the files its runs wrote or created with write_file and edit_file, read from their
// journals of file changes (`GET /api/runs/{id}/changes`), each with the lines added and removed and opening on its
// unified diff; a file without a diff says why. The side panel's Changes tab holds them and counts the files.

import { api } from "./api.js";
import { codeView, fileLines } from "./codeview.js";
import { changeFormat, changeLayout, prettyJSON } from "./diffview.js";
import { el, requestFile } from "./dom.js";
import { t, tCount } from "./i18n.js";
import { icon } from "./icons.js";
import { panelSection, segmented } from "./panels.js";
import { prose } from "./prose.js";

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
      title: t("changes.showNameAsItIs", { name }),
      "aria-label": t("changes.showNameAsItIs", { name }),
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
  const section = panelSection({ id: "run-changes", title: t("changes.changes"), actions: null, body: el("div", { class: "changes-holder" }, head, body) });
  // What the section was last drawn from: the same changes read again are not drawn again.
  let drawn = null;

  async function refresh() {
    const opened = new Set([...body.querySelectorAll("details.change[open]")].map((node) => node.dataset.path));
    let view;
    try {
      view = await api.get(`/runs/${runId}/changes`);
    } catch (error) {
      drawn = null;
      body.replaceChildren(el("div", { class: "note bad" }, icon("alert"), el("span", null, t("changes.theChangesCouldNotBe", { message: error.message }))));
      return;
    }
    const read = JSON.stringify(view);
    if (read === drawn) {
      return;
    }
    drawn = read;
    section.setSummary(
      view.files.length === 0 ? null : el("span", { class: "changes-total" }, tCount(view.files.length, "changes.countFile", "changes.countFiles"), counts(view.added, view.removed)),
    );
    body.replaceChildren(
      ...(view.files.length === 0
        ? [el("div", { class: "small muted" }, t("changes.noFileWasWrittenIn"))]
        : view.files.map((file) => fileRow(file, opened.has(file.path)))),
    );
    onCount(view.files.length);
    onFiles(view.files);
  }

  return { element: section.element, refresh };
}
