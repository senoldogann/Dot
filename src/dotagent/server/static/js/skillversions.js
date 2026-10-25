// The versions of a learned skill (item 585, Customize › Skills): a dialog that lists them, the newest first, each with when
// it was made, how (a run rewrote it, a merge, a restore, the owner's words), whether its steps carry the mark of content
// third parties can author, and the steps it changed from the version before it. Restore puts one back as a new version of
// its own, so that the put-back can be taken back: the steps the skill has now stay in the list.

import { api } from "./api.js";
import { dateTime, dateTimeFull, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { countChanges, describeVersion, markOf, stepChanges } from "./skilldiff.js";
import { attempt, confirmDialog, toast } from "./ui.js";

/** @typedef {import("./skilldiff.js").StepChange} StepChange */

// What a line of a version's steps says of the step, for a reader that does not see its colour.
const SIGN_WORDS = { "+": "Added", "-": "Dropped", " ": "" };

/**
 * The steps a version changed from the one before it: each step once, an added one marked and a dropped one struck
 * through, and the count of the unchanged ones that are kept in. The first version lists its steps.
 * @param {readonly string[]} before the steps of the version before it, or null for the first
 * @param {readonly string[]} after
 */
function stepsList(before, after) {
  const changes = before === null ? /** @type {StepChange[]} */ (after.map((text) => ({ sign: " ", text }))) : stepChanges(before, after);
  const counts = countChanges(changes);
  const list = el(
    "ol",
    { class: "version-steps" },
    changes.map(({ sign, text }) =>
      el(
        "li",
        { class: `version-step ${sign === "+" ? "added" : sign === "-" ? "dropped" : "kept"}` },
        el("span", { class: "version-sign", "aria-hidden": "true" }, sign === " " ? "" : sign),
        sign === " " ? null : el("span", { class: "visually-hidden" }, `${SIGN_WORDS[sign]}: `),
        text,
      ),
    ),
  );
  const note =
    before === null
      ? null
      : el(
          "div",
          { class: "hint version-counts" },
          counts.added + counts.dropped === 0
            ? "The steps are the same as the version before."
            : `${plural(counts.added, "step", "steps")} added, ${plural(counts.dropped, "step", "steps")} dropped, ${counts.kept} kept.`,
        );
  return [note, list];
}

/** What the owner is asked before a version is put back: what it becomes, what is kept and what its mark means. */
function restoreQuestion(skill, detail, current) {
  const made = current.note.n + 1;
  const marked =
    detail.note.tainted === false
      ? ""
      : " This version carries the mark of content other people can write, so tasks are offered it with the web addresses, commands and file names in its steps withheld.";
  return {
    title: `Put version ${detail.note.n} of ${skill.name} back?`,
    message: `Its steps become version ${made}. The steps the skill has now stay as version ${current.note.n}, so this can be taken back.${marked}`,
    confirmLabel: "Put it back",
    focusCancel: true,
  };
}

/** One version as a block: its number, when, how it was made, its mark, Restore (but for the current one) and its steps. */
function versionBlock(detail, earlier, current, onRestore) {
  const { note } = detail;
  const mark = markOf(note.tainted);
  const restore = detail.current
    ? null
    : el("button", { class: "btn ghost sm", type: "button", title: `Put version ${note.n} back as the skill's steps` }, icon("undo"), "Restore");
  if (restore !== null) {
    restore.addEventListener("click", () => onRestore(detail, current));
  }
  const by =
    note.run_id === null || note.run_id === ""
      ? null
      : el("a", { href: `#/runs/${encodeURIComponent(note.run_id)}` }, "Open the task");
  return el(
    "div",
    { class: "version-block", dataset: { version: String(note.n), current: String(detail.current) } },
    el(
      "div",
      { class: "version-head" },
      el("span", { class: "version-number" }, `Version ${note.n}`),
      detail.current ? el("span", { class: "badge b-ok" }, "Current") : null,
      mark === null ? null : el("span", { class: "badge b-warn", title: "Tasks are offered these steps with addresses, commands and file names withheld." }, icon("alert"), mark),
      el("span", { class: "grow" }),
      restore,
    ),
    el("div", { class: "version-how" }, el("span", { title: dateTimeFull(note.at) }, dateTime(note.at)), " · ", describeVersion(note), by === null ? null : " · ", by),
    stepsList(earlier === null ? null : earlier.text.steps, detail.text.steps),
  );
}

/**
 * Show the versions of the learned skill `skill` in a dialog until the owner closes it. `onChanged(list)` gets the list of
 * learned skills after a restore.
 * @param {{ name: string }} skill
 * @param {(list: object[]) => void} onChanged
 * @returns {Promise<void>}
 */
export function showVersions(skill, onChanged) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "modal versions-dialog", "aria-labelledby": "versions-title" });
    const body = el("div", { class: "versions-list" }, el("div", { class: "hint" }, "Reading the versions…"));
    const close = el("button", { class: "btn", type: "button", onclick: () => dialog.close() }, "Close");

    async function restore(detail, current) {
      if (!(await confirmDialog(restoreQuestion(skill, detail, current)))) {
        return;
      }
      const page = await api.post(`/skills/learned/${encodeURIComponent(skill.name)}/restore`, { version: detail.note.n });
      toast("ok", `Put version ${detail.note.n} of ${skill.name} back.`);
      onChanged(page.items);
      await load();
    }

    function paint(details) {
      const current = details.find((detail) => detail.current) ?? details[details.length - 1];
      // The newest first; each is compared with the one made before it.
      body.replaceChildren(
        ...details.map((detail, index) => versionBlock(detail, index === 0 ? null : details[index - 1], current, (...picked) => attempt(() => restore(...picked)))).reverse(),
      );
    }

    async function load() {
      try {
        const view = await api.get(`/skills/learned/${encodeURIComponent(skill.name)}/versions`);
        paint(view.items);
      } catch (error) {
        body.replaceChildren(el("p", { class: "hint", role: "alert" }, `The versions could not be read: ${error.message}`));
      }
    }

    dialog.append(
      el(
        "div",
        { class: "modal-body" },
        el("h3", { id: "versions-title" }, `Versions of ${skill.name}`),
        el("p", { class: "muted" }, "Each rewrite, merge or restore keeps the steps it replaces. Restore puts one back as a new version, which can be put back in turn."),
        body,
      ),
      el("div", { class: "modal-actions" }, close),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve();
    });
    document.body.append(dialog);
    dialog.showModal();
    attempt(load);
  });
}
