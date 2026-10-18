// How a run was set up (item 365): the notes and the skills it was handed and how they were found, its context budget,
// its tool groups, the model a step went to. The conversation keeps none of these lines; the side panel's Run tab lists
// them under Set up, its head naming them in short. A line that warns (the embedding service failed, so the notes were
// found by their words alone) keeps its tone there.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

/**
 * The head of the lines `lines` (each `{ kind, short }`): their short parts in the order they came, and the notes and
 * the skills found by their words alone said once ("notes and skills found by their words alone").
 */
export function setupSummary(lines) {
  const alone = lines.filter((line) => line.kind === "words").map((line) => line.short);
  const parts = [];
  let saidAlone = false;
  for (const line of lines) {
    if (line.kind !== "words") {
      parts.push(line.short);
    } else if (!saidAlone) {
      saidAlone = true;
      parts.push(`${alone.join(" and ")} found by their words alone`);
    }
  }
  return parts.join(" · ");
}

/**
 * The Set up section of the Run tab, hidden until the run's first line of how it was set up. `add(line)` lists one
 * more: `{ kind, short, iconName, text, tone }`, `tone` "" or "warn" or "bad".
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

  return { element: section.element, add };
}
