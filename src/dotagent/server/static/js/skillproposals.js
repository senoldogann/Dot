// The changes to learned skills that wait for the owner's yes (item 585, Customize › Skills): each is a card that shows
// everything the yes would write to the first skill (its name, description, kind of work, when it fits, steps and
// parameters, each against what the skill says now) and what happens to the other, with a yes and a no. Nothing is
// written to a skill until the yes: the nightly reflection and the owner's reactions only propose. The server lists the
// fields (`view.fields`, as the file will keep them), so that nothing but what the card shows is written.

import { api } from "./api.js";
import { clip, el } from "./dom.js";
import { icon } from "./icons.js";
import { markOf, proposalEffects, proposalTitle, stepsSummary } from "./skilldiff.js";
import { toast } from "./ui.js";

const REASON_CHARS = 160;

/** One line of a field, marked against the first skill: a dropped one struck through, an added one marked. */
function lineItem({ sign, text }) {
  return el(
    "li",
    { class: `version-step ${sign === "+" ? "added" : sign === "-" ? "dropped" : "kept"}` },
    el("span", { class: "version-sign", "aria-hidden": "true" }, sign === " " ? "" : sign),
    sign === " " ? null : el("span", { class: "visually-hidden" }, sign === "+" ? "Added: " : "Dropped: "),
    text,
  );
}

/** One field the yes writes: what it is called, and its lines, or "none" for a list the skill would have empty. */
function fieldBlock(field) {
  const settled = !field.changed && field.field !== "name";
  return el(
    "div",
    { class: "proposal-field", dataset: { field: field.field, changed: String(field.changed) } },
    el("div", { class: "proposal-field-label" }, field.label, settled ? el("span", { class: "hint" }, " (unchanged)") : null),
    field.lines.length > 0 ? el("ul", { class: "version-steps" }, field.lines.map(lineItem)) : el("p", { class: "hint proposal-none" }, "none"),
  );
}

/**
 * A proposal to change or merge learned skills, as `/api/skills/proposals` gives it.
 * @typedef {{ id: string, kind: string, names: string[], tainted: boolean, reason: string, words: string | null }} SkillProposal
 */

/** Everything the yes writes, each field against what the first skill says now, with the steps counted above them. */
function fieldsDiff(view) {
  return [
    el("div", { class: "hint version-counts" }, stepsSummary(view.fields, view.proposal.names[0])),
    el("div", { class: "proposal-fields" }, view.fields.map(fieldBlock)),
  ];
}

/**
 * One proposal as a card: what it does, why, every field it would write, what happens to each skill, and Yes and No. A
 * proposal that cannot be applied now (a skill changed since) says why and has No alone. `onAccepted(list)` gets the learned
 * skills after a yes, `onDismissed(view)` the proposals after a no.
 * @param {{ proposal: SkillProposal, stale: string | null, current: object[], fields: object[] }} view
 * @param {{ onAccepted: (list: object[]) => void, onDismissed: (view: { items: object[] }) => void }} handlers
 */
export function proposalCard(view, { onAccepted, onDismissed }) {
  const { proposal } = view;
  const id = encodeURIComponent(proposal.id);
  const yes = el("button", { class: "btn primary sm", type: "button" }, icon("check"), proposal.kind === "merge" ? "Merge" : "Change it");
  const no = el("button", { class: "btn ghost sm", type: "button", title: proposal.kind === "merge" ? "Keep them apart: they are not proposed again until one of them changes." : "Leave the skill as it is." }, view.stale === null ? "No" : "Dismiss");
  const buttons = [yes, no];
  for (const button of buttons) {
    button.addEventListener("click", async () => {
      for (const one of buttons) {
        one.disabled = true;
      }
      try {
        if (button === yes) {
          const page = await api.post(`/skills/proposals/${id}/accept`, {});
          toast("ok", proposal.kind === "merge" ? `Merged ${proposal.names.join(" and ")}.` : `Changed ${proposal.names[0]}.`);
          onAccepted(page.items);
        } else {
          onDismissed(await api.del(`/skills/proposals/${id}`));
        }
      } catch (error) {
        toast("error", error.message);
        for (const one of buttons) {
          one.disabled = false;
        }
      }
    });
  }
  const marked = proposal.tainted ? markOf(true) : null;
  return el(
    "div",
    { class: "skill-card proposal-card", dataset: { proposal: proposal.id, kind: proposal.kind } },
    el("span", { class: "skill-mark", "aria-hidden": "true" }, icon(proposal.kind === "merge" ? "layers" : "pencil")),
    el(
      "div",
      { class: "skill-main" },
      el("div", { class: "skill-head" }, el("div", { class: "skill-name" }, proposalTitle(proposal))),
      el("div", { class: "skill-does" }, proposal.kind === "merge" ? `Why: ${clip(proposal.reason, REASON_CHARS)}` : `You asked: ${clip(proposal.words ?? proposal.reason, REASON_CHARS)}`),
      marked === null
        ? null
        : el("div", { class: "row-meta" }, el("span", { class: "badge b-warn", title: "Tasks are offered the new steps with addresses, commands and file names withheld." }, icon("alert"), marked)),
      view.stale === null ? fieldsDiff(view) : el("p", { class: "hint proposal-stale", role: "note" }, `It cannot be applied now: ${view.stale}.`),
      view.stale === null ? el("ul", { class: "proposal-effects" }, proposalEffects(proposal).map((line) => el("li", null, line))) : null,
      el("div", { class: "skill-actions" }, view.stale === null ? yes : null, no),
    ),
  );
}
