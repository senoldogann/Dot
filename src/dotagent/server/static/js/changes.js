// The Changes of a chat (item 368): the files its runs wrote or created with write_file and edit_file, read from their
// journals of file changes (`GET /api/runs/{id}/changes`), each with the lines added and removed and opening on its
// unified diff; a file without a diff says why. The side panel's Changes tab holds them and counts the files.

import { api } from "./api.js";
import { el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

/** One line of a diff's hunks, in the tone of what it does: a hunk's head, a line added, removed or kept. */
function diffLine(text) {
  const kind = text.startsWith("@@") ? "hunk" : text.startsWith("+") ? "add" : text.startsWith("-") ? "del" : "same";
  return el("span", { class: `diff-line diff-${kind}` }, text);
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

/** A file the chat's runs changed: its name over its folder, as the Files card has them, whether they created it or
 * it is gone, its counts; opened, its diff. */
function fileRow(file, open) {
  const state = file.created ? "created" : file.gone ? "gone" : null;
  const cut = file.path.lastIndexOf("/");
  const head = el(
    "summary",
    { class: "change-head" },
    icon("file"),
    el(
      "span",
      { class: "change-path", title: file.path },
      el("span", { class: "change-name" }, file.path.slice(cut + 1)),
      cut <= 0 ? null : el("span", { class: "change-where" }, file.path.slice(0, cut)),
    ),
    state === null ? null : el("span", { class: "badge" }, state),
    counts(file.added, file.removed),
    el("span", { class: "chev" }, icon("chevron")),
  );
  // The lines of a diff are drawn when the file is first opened (item 674): a chat's diffs run to thousands of lines,
  // and the section is drawn again after each file a run writes.
  const diff = el("pre", { class: "diff" });
  const body = file.note === null ? diff : el("div", { class: "note" }, icon("info"), el("span", null, file.note));
  const node = el("details", { class: "change", open }, head, body);
  node.dataset.path = file.path;
  const draw = () => {
    if (node.open && file.note === null && diff.childElementCount === 0 && file.diff !== "") {
      diff.replaceChildren(...file.diff.replace(/\n$/, "").split("\n").map(diffLine));
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
