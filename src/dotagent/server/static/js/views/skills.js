// Skills Dot learned from its own work: one row each with the kind of work, where it acted, the runs it comes from and
// how it fared; a retired one (read by two runs that then failed) is listed apart. Drop removes one.

import { api } from "../api.js";
import { ago, el, plural } from "../dom.js";
import { emptyPanel, errorPanel, loadingPanel, paneHead } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

/** One learned skill as a row: the name and the description, then the kind, the place, the runs and the record. */
function skillRow(skill, onDrop) {
  const drop = el("button", { class: "btn ghost sm", type: "button" }, "Drop");
  drop.addEventListener("click", () => onDrop(skill));
  const record =
    skill.uses + skill.failures === 0
      ? "Not read by a run yet"
      : `${plural(skill.uses, "run", "runs")} done after reading it, ${skill.failures} failed`;
  return el(
    "div",
    { class: "row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, skill.name),
      el("div", { class: "row-text" }, skill.description),
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: "chip" }, skill.kind),
        el("span", null, skill.host),
        el("span", null, `Learned from ${plural(skill.runs.length, "run", "runs")}, last ${ago(skill.updated_at)}`),
        el("span", null, record),
      ),
    ),
    el("div", { class: "row-actions" }, drop),
  );
}

export function mount(root) {
  let disposed = false;
  let items = null;
  const holder = el("div", null, loadingPanel());

  function paint() {
    if (items === null) {
      return;
    }
    if (items.length === 0) {
      holder.replaceChildren(
        emptyPanel("No learned skills yet", "After a task that went well, Dot writes how it did it as a skill for the next one of its kind."),
      );
      return;
    }
    const active = items.filter((skill) => !skill.retired);
    const retired = items.filter((skill) => skill.retired);
    const parts = [];
    if (active.length > 0) {
      parts.push(el("div", { class: "card" }, active.map((skill) => skillRow(skill, drop))));
    }
    if (retired.length > 0) {
      parts.push(
        el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, "Retired")),
        el("div", { class: "card" }, retired.map((skill) => skillRow(skill, drop))),
      );
    }
    holder.replaceChildren(...parts);
  }

  async function load() {
    try {
      const page = await api.get("/skills/learned");
      if (disposed) {
        return;
      }
      items = page.items;
      paint();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error));
      }
    }
  }

  async function drop(skill) {
    const confirmed = await confirmDialog({
      title: `Drop ${skill.name}?`,
      message: "The skill is removed; a later run of its kind may learn it again.",
      confirmLabel: "Drop",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await attempt(async () => {
      const page = await api.del(`/skills/learned/${encodeURIComponent(skill.name)}`);
      items = page.items;
      paint();
      toast("ok", `Dropped ${skill.name}.`);
    });
  }

  root.replaceChildren(paneHead("What Dot learned from its own work, offered to later runs as advice.", []), holder);
  load();
  return () => {
    disposed = true;
  };
}
