// What the chat's run changed on this Mac (item 597): at the head of the side panel's Changes tab (item 841: it stood
// under the answer, which a chat with a personal agent does not want, Dot being no editor), once it has ended, the files its file tools
// created and changed, as the undo journal has them (`GET /runs/{id}/actions`), each a button to the file beside the
// conversation, with Review, the side panel's Changes tab and their diffs, and Undo, which takes the changes back after
// a question. Not an editor's list: one line of what Dot did and the way back, and nothing for a run that changed no
// file. A command's own writes are in no journal and so not here (docs/autonomy.md); the run's own notes are left out.

import { api } from "./api.js";
import { el, requestFile } from "./dom.js";
import { icon } from "./icons.js";
import { attempt, confirmDialog, toast } from "./ui.js";

// How many of the files stand by name; the rest are counted.
const NAMED = 3;
const NOTES_TOOL = "write_notes";

/** The files of the run's undo plan (`edits` of `GET /runs/{id}/actions`) the card shows: those a file tool changed for
 * the owner (not the run's notes) and that are not as they were before it. */
export function shownEdits(edits) {
  return edits.filter((edit) => edit.tool !== NOTES_TOOL && edit.state !== "as_before");
}

/** "a file" or "2 files". */
function files(count) {
  return count === 1 ? "a file" : `${count} files`;
}

/** What the run did to `edits` in words: "Created a file", "Changed 3 files", "Created 2 files and changed 1". A file
 * the undo deletes is one the run created; one it restores, one the run changed. */
export function changedWords(edits) {
  const created = edits.filter((edit) => edit.action === "delete").length;
  const changed = edits.length - created;
  if (created === 0) {
    return `Changed ${files(changed)}`;
  }
  if (changed === 0) {
    return `Created ${files(created)}`;
  }
  return `Created ${files(created)} and changed ${changed}`;
}

/** What Undo asks before it takes `ready` back, of `shown`: what goes back, what is deleted, what stays. */
export function undoQuestion(ready, shown) {
  const deleted = ready.filter((edit) => edit.action === "delete").length;
  const restored = ready.length - deleted;
  const parts = [];
  if (restored > 0) {
    parts.push(`${restored === 1 ? "A file goes" : `${restored} files go`} back as it was before the task`);
  }
  if (deleted > 0) {
    parts.push(`${deleted === 1 ? "the file it created is" : `the ${deleted} files it created are`} deleted`);
  }
  // A file changed since the run (a conflict) apart from one that cannot go back for another reason.
  const conflicts = shown.filter((edit) => edit.state === "conflict").length;
  const cannot = shown.filter((edit) => edit.state === "cannot").length;
  const stays = [
    conflicts === 0 ? "" : ` ${conflicts === 1 ? "A file changed since stays as it is." : `${conflicts} files changed since stay as they are.`}`,
    cannot === 0 ? "" : ` ${cannot === 1 ? "A file that cannot go back stays as it is." : `${cannot} files that cannot go back stay as they are.`}`,
  ].join("");
  const said = parts.join(", and ");
  return `${said.charAt(0).toUpperCase()}${said.slice(1)}.${stays}`;
}

/** The base name of `path`. */
function baseName(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

/**
 * The card of what the run `runId` changed, under its answer. `onReview()` opens the Changes tab; `onUndone()` runs
 * after an Undo, for the page to read its Changes and Files again. Returns `{ element, load() }`: `load()` reads the
 * run's undo plan and draws the card, which stays empty and hidden for a run that changed no file.
 */
export function runChangesCard({ runId, onReview, onUndone }) {
  const element = el("section", { class: "run-changes", "aria-label": "What the task changed", hidden: true });
  // Whether this card's Undo took files back: only then does an empty plan say the changes were taken back. A file the
  // run changed and changed back, or one the owner put back by hand, leaves nothing to say.
  let undone = false;
  let busy = false;

  function draw(actions) {
    const shown = shownEdits(actions.edits);
    if (shown.length === 0) {
      element.replaceChildren(...(undone ? [icon("undo"), el("span", { class: "run-changes-words" }, "Its file changes were taken back.")] : []));
      element.hidden = !undone;
      return;
    }
    const named = shown.slice(0, NAMED).map((edit) =>
      el(
        "button",
        { class: "run-changes-file", type: "button", title: edit.path, dataset: { path: edit.path }, onclick: (event) => requestFile(event.currentTarget) },
        baseName(edit.path),
      ),
    );
    const more = shown.length > NAMED ? el("span", { class: "run-changes-more" }, `+${shown.length - NAMED}`) : null;
    const ready = shown.filter((edit) => edit.state === "ready");
    // Whether the run and its helpers have ended is read when Undo is pressed, not here: a helper that winds down late
    // would otherwise keep it off until the page is opened again.
    const undo = el(
      "button",
      { class: "btn ghost sm", type: "button", disabled: ready.length === 0, onclick: () => takeBack() },
      icon("undo"),
      el("span", null, "Undo"),
    );
    if (ready.length === 0) {
      undo.title = "No file can go back as the task left it";
    }
    element.replaceChildren(
      icon("fileDiff"),
      el("div", { class: "run-changes-body" }, el("span", { class: "run-changes-words" }, changedWords(shown)), el("span", { class: "run-changes-files" }, ...named, more)),
      el(
        "div",
        { class: "run-changes-actions" },
        onReview === null ? null : el("button", { class: "btn ghost sm", type: "button", onclick: onReview }, "Review"),
        undo,
      ),
    );
    element.hidden = false;
  }

  /** Take the run's file changes back, from the plan as it is now, not as it was when the line was drawn: a file the
   * owner changed since, here or in the Changes tab, is no longer the run's to take back. The line is drawn again from
   * the plan afterwards, also when a file could not go back, so that it says what did. */
  // An action of the owner's says what went wrong as a toast (`attempt`): the plan not read, a file not taken back. Only
  // the line's own reading, as the answer comes, keeps quiet, the Changes tab having the files all the same.
  function takeBack() {
    // A second press while the first reads the plan or asks is let go, so that one Undo asks once.
    if (busy) {
      return Promise.resolve();
    }
    busy = true;
    return attempt(takeBackNow).finally(() => {
      busy = false;
    });
  }

  async function takeBackNow() {
    const now = await read();
    const shown = shownEdits(now.edits);
    const ready = shown.filter((edit) => edit.state === "ready");
    if (ready.length === 0 || !now.ended) {
      draw(now);
      if (!now.ended) {
        toast("error", "Undo waits for the task and its helpers to end.");
      }
      return;
    }
    const confirmed = await confirmDialog({
      title: "Take back what this task changed?",
      message: undoQuestion(ready, shown),
      confirmLabel: "Undo",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    try {
      for (const edit of ready) {
        await api.post(`/runs/${encodeURIComponent(runId)}/undo`, { path: edit.path });
        undone = true;
      }
    } finally {
      // The page's Changes tab and Files card read the files again, so that they tell what the line tells, and the line
      // is drawn from the plan as the Undo left it, also when a file could not go back.
      onUndone();
      draw(await read());
    }
  }

  /** The run's undo plan now; an error when it cannot be read. */
  function read() {
    return api.get(`/runs/${encodeURIComponent(runId)}/actions`);
  }

  async function load() {
    try {
      draw(await read());
    } catch (error) {
      // The line's own reading as the answer comes: a plan that cannot be read leaves the line out, said in the console,
      // and the Changes tab has the files all the same.
      console.warn("what the run changed could not be read", error);
    }
  }

  return { element, load };
}
