// What the chat's run changed on this Mac (item 597), once it has ended: at the head of the side panel's Changes tab
// (item 841: it stood under the answer, which a chat with a personal agent does not want, Dot being no editor), the
// files its file tools created and changed, as the undo journal has them (`GET /runs/{id}/actions`), each a button to
// the file beside the conversation, and Undo, which takes the changes back after a question; Review, which opened the
// Changes tab, is left out where the line stands in it. Not an editor's list: one line of what Dot did and the way
// back, and nothing for a run that changed no file. A command's own writes are in no journal and so not here
// (docs/autonomy.md); the run's own notes are left out. A run that also acted with a tool the journal does not hold (a
// command, the browser, the screen, a message) says how many such steps it ran and that Undo cannot take them back, with
// no claim of what they did (item 1404); that line stands alone for a run that changed no file. Under it, what the run's
// record says it checked, sent and left open (item 1746), never what the model said of it.

import { api } from "./api.js";
import { el, plural, requestFile } from "./dom.js";
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

// What a step outside the undo journal is called, by its category (`outside` of `GET /runs/{id}/actions`).
const OUTSIDE_NOUNS = {
  shell: ["command", "commands"],
  browser: ["browser action", "browser actions"],
  desktop: ["desktop action", "desktop actions"],
  message: ["message step", "message steps"],
  app: ["connected app action", "connected app actions"],
};

/**
 * What the run did that no undo can take back, in a sentence ("Dot also ran 3 commands and 2 browser actions. Undo
 * takes back files only, so it cannot take these back."), from `outside` (the categories of `GET /runs/{id}/actions`
 * that have steps); "" when there are none. It counts steps that ran and says nothing of what they did.
 */
export function outsideWords(outside) {
  const counted = outside.filter((item) => item.steps > 0 && item.category in OUTSIDE_NOUNS);
  if (counted.length === 0) {
    return "";
  }
  const parts = counted.map((item) => plural(item.steps, ...OUTSIDE_NOUNS[item.category]));
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  const total = counted.reduce((sum, item) => sum + item.steps, 0);
  return `Dot also ran ${list}. Undo takes back files only, so it cannot take ${total === 1 ? "this" : "these"} back.`;
}

/**
 * What the run did by its record, not by its model (item 1746), from `account` of `GET /runs/{id}/actions`: how many
 * browser and desktop steps were seen to hold and how many were not, how many things it sent, and the plan steps it
 * marked done that no tool result shows and those it left open. A list of sentences, empty when there is nothing to
 * say.
 */
export function accountWords(account) {
  const words = [];
  const acts = account.acts;
  const unseen = acts.contradicted + acts.uncertain + acts.not_checked;
  if (acts.verified + unseen + acts.failed > 0) {
    const parts = [
      acts.not_checked === 0 ? "" : `${acts.not_checked} not checked`,
      acts.uncertain === 0 ? "" : `${acts.uncertain} uncertain`,
      acts.contradicted === 0 ? "" : `${acts.contradicted} contradicted`,
    ].filter((part) => part !== "");
    const failed = acts.failed === 0 ? "" : ` ${plural(acts.failed, "step", "steps")} failed.`;
    words.push(`Checked on the page or the screen: ${acts.verified} seen to hold${unseen === 0 ? "" : `, ${unseen} not (${parts.join(", ")})`}.${failed}`);
  }
  if (account.sent_count > 0) {
    words.push(`Sent ${plural(account.sent_count, "thing", "things")}: ${account.sent.join("; ")}.`);
  }
  if (account.unshown.length > 0) {
    words.push(`Marked done with no tool result that shows it: ${account.unshown.join("; ")}.`);
  }
  if (account.open_steps.length > 0) {
    words.push(`Left open: ${account.open_steps.join("; ")}.`);
  }
  return words;
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
 * The card of what the run `runId` changed, at the head of the Changes tab (item 841). `onReview()` opens that tab, and
 * null leaves Review out, as the run page does; `onUndone()` runs after an Undo, for the page to read its Changes and
 * Files again. Returns `{ element, load() }`: `load()` reads the
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
    // A daemon older than item 1404 sends no `outside`: no such line then.
    const outside = outsideWords(actions.outside ?? []);
    const outsideLine = outside === "" ? null : el("div", { class: "run-changes-outside", dataset: { outside: "" } }, outside);
    // A daemon older than item 1746 sends no `account`: no such lines then.
    const account = actions.account === undefined ? [] : accountWords(actions.account);
    const accountLine =
      account.length === 0 ? null : el("div", { class: "run-changes-account", dataset: { account: "" } }, ...account.map((line) => el("p", null, line)));
    if (shown.length === 0) {
      const taken = undone ? [icon("undo"), el("span", { class: "run-changes-words" }, "Its file changes were taken back.")] : [];
      element.replaceChildren(...taken, ...[outsideLine, accountLine].filter((part) => part !== null));
      element.hidden = !undone && outsideLine === null && accountLine === null;
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
    // The native replaceChildren writes a null as the word: a line that is not there is left out first.
    element.replaceChildren(
      ...[
        icon("fileDiff"),
        el("div", { class: "run-changes-body" }, el("span", { class: "run-changes-words" }, changedWords(shown)), el("span", { class: "run-changes-files" }, ...named, more)),
        el(
          "div",
          { class: "run-changes-actions" },
          onReview === null ? null : el("button", { class: "btn ghost sm", type: "button", onclick: onReview }, "Review"),
          undo,
        ),
        outsideLine,
        accountLine,
      ].filter((part) => part !== null),
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
