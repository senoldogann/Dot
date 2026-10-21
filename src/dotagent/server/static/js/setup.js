// How a run was set up (item 365): the notes and the skills it was handed and how they were found, its context budget,
// its tool groups, the model a step went to. The conversation keeps none of these lines; the side panel's Run tab lists
// them under Set up, in its More (item 452), its head naming them in short. A line that warns (the embedding service
// failed, so the notes were found by their words alone) keeps its tone there, and More counts it beside its title.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

// The kinds of line a long run gives again and again, said once with how many there are: each time the goal was
// repeated to the model, each step sent to another model, and each such step asked again on the run's model. The
// lines said once a run (how often the goal is repeated: "cadence") are kinds of their own.
const REPEATED = {
  reminder: (count) => `goal repeated ${count} times`,
  route: (count) => `${count} steps on another model`,
  "route-back": (count) => `${count} steps back on the run's model`,
};

/**
 * The head of the lines `lines` (each `{ kind, short }`): their short parts in the order they came; the notes and the
 * skills found by their words alone said once ("notes and skills found by their words alone"), and a kind that came
 * more than once of those a long run repeats (`REPEATED`) said once, with its count.
 */
export function setupSummary(lines) {
  const alone = lines.filter((line) => line.kind === "words").map((line) => line.short);
  const counts = new Map();
  for (const line of lines) {
    counts.set(line.kind, (counts.get(line.kind) ?? 0) + 1);
  }
  const parts = [];
  const said = new Set();
  for (const line of lines) {
    if (line.kind === "words") {
      if (!said.has("words")) {
        said.add("words");
        parts.push(`${alone.join(" and ")} found by their words alone`);
      }
    } else if (line.kind in REPEATED && counts.get(line.kind) > 1) {
      if (!said.has(line.kind)) {
        said.add(line.kind);
        parts.push(REPEATED[line.kind](counts.get(line.kind)));
      }
    } else {
      parts.push(line.short);
    }
  }
  return parts.join(" · ");
}

/**
 * The Set up section of the Run tab, hidden until the run's first line of how it was set up. `add(line)` lists one
 * more: `{ kind, short, iconName, text, tone }`, `tone` "" or "warn" or "bad"; `warnings()` counts the lines that warn.
 */
export function setupSection() {
  const body = el("div", { class: "setup-lines" });
  const section = panelSection({ id: "run-setup", title: "Set up", actions: null, body });
  section.element.hidden = true;
  const lines = [];

  function add(line) {
    lines.push(line);
    body.append(el("div", { class: line.tone === "" ? "note" : `note ${line.tone}` }, icon(line.iconName), el("span", null, line.text)));
    section.setSummary(el("span", { class: "setup-summary" }, setupSummary(lines)));
    section.element.hidden = false;
  }

  return { element: section.element, add, warnings: () => lines.filter((line) => line.tone !== "").length };
}
