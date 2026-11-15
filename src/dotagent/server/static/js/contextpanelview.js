// The sections of a run's Task tab (items 539 and 546), what came of the task first: the files its answer names, which
// are what it hands over, and how it ended with what it needs from the owner (both from the run's end, never from a
// guess at what a file is), then, after the recap, the pages and searches it read; the technical last: the files its
// chat changed with their lines, the skills and MCP servers it used and the notes it saved. The usage is gathered from the run's
// tool calls, and the readings and notes from its events as they come, and again from its history, each once
// (contextpanel.js); nothing is shown of what the run did not do. Each row wears the theme's own colours (run.css), so
// the panel reads in every theme.

import { addMemory, addReadings, addUsage, chatMemory, chatUsage, languageOf, memoryChange, readingsOf, usageOf } from "./contextpanel.js";
import { clip, el, plural, requestFile } from "./dom.js";
import { t, tCount } from "./i18n.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

// How many rows a section shows before it counts the rest; the readings and the notes keep their own longer lists.
const SECTION_ROWS = 5;
const MEMORY_WORDS_CHARS = 80;
// What the conversation says of a memory change (timeline.js), in the same words.
const MEMORY_CHANGES = { Saved: "Saved to memory", Updated: "Updated memory", Forgot: "Removed from memory" };

/** The mark of a row on its own tile: an icon, a language's logo, or the letters of a file's kind. A logo keeps the
 * light tile of a service's mark, so that a dark logo (Rust, JSON) reads in the dark scheme too. */
function tile(mark) {
  const logo = typeof mark !== "string" && mark.classList.contains("context-logo");
  return el("span", { class: logo ? "context-tile context-tile-logo" : "context-tile" }, typeof mark === "string" ? icon(mark) : mark);
}

/** A row of a section: its mark and its line, and beside them what says how it stands. */
function row(mark, name, note) {
  return [tile(mark), el("span", { class: "context-name" }, name), note === "" ? null : el("span", { class: "context-note" }, note)];
}

/** A file's mark: its language's own logo (item 547), the letters of its kind ("MD", "TS") on an accent tile when its
 * language has no mark, or a folded page when its kind has no short name. */
function fileMark(path) {
  const language = languageOf(path);
  if (language !== null) {
    return el("img", { class: "context-logo", src: `/icons/languages/${language}.svg`, alt: "" });
  }
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  const kind = dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
  return kind.length >= 1 && kind.length <= 3 ? el("span", { class: "context-kind" }, kind) : "file";
}

/** The kind of a file in a few letters ("CSV", "PDF"), from its name; "" for a name with no suffix. */
function kindOf(path) {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
}

/** The files the run's answer names (its file chips and file words, prose.js), which are what the task hands over: each
 * by its name, its kind and its folder, opening beside the conversation as the chips do. `set(answer)` takes the answer
 * node, whose `[data-path]` buttons say them; nothing is guessed of what a file is. Out of sight with none. */
function resultsSection() {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-results", title: t("panel.results"), actions: null, body });
  section.element.hidden = true;

  function set(answer) {
    const paths = [...new Set([...answer.querySelectorAll(".prose [data-path]")].map((node) => node.dataset.path ?? ""))].filter((path) => path !== "");
    body.replaceChildren(
      ...paths.map((path) => {
        const name = path.slice(path.lastIndexOf("/") + 1);
        const folder = path.slice(0, Math.max(path.lastIndexOf("/"), 0));
        const kind = kindOf(path);
        return el(
          "button",
          {
            class: "context-row context-result",
            type: "button",
            title: t("panel.openFile", { path }),
            "aria-label": t("panel.openFile", { path }),
            dataset: { path },
            onclick: (event) => requestFile(event.currentTarget),
          },
          tile(fileMark(path)),
          el("span", { class: "context-name" }, el("span", { class: "context-file" }, name), folder === "" ? null : el("span", { class: "context-folder" }, folder)),
          kind === "" ? null : el("span", { class: "context-note" }, kind),
        );
      }),
    );
    section.setSummary(el("span", null, tCount(paths.length, "panel.fileOne", "panel.fileMany")));
    section.element.hidden = paths.length === 0;
  }

  return { element: section.element, set };
}

// How each way a task ends is said in the Outcome section: what happened, never more than the run's end says (a run
// that ended done wrote its answer; whether the work holds is the answer's and the files' to show).
/** @type {Record<string, { icon: string, words: import("./i18n.js").WordKey }>} */
const OUTCOME_LINES = {
  done: { icon: "check", words: "panel.outcomeDone" },
  failed: { icon: "alert", words: "panel.outcomeFailed" },
  cancelled: { icon: "stop", words: "panel.outcomeCancelled" },
  waiting: { icon: "hand", words: "panel.outcomeWaiting" },
};

/** How the task ended and what it needs from the owner, from its end (`set({ status, needs })`); out of sight until it
 * has ended. */
function outcomeSection() {
  const body = el("div", { class: "context-outcome" });
  const section = panelSection({ id: "run-outcome", title: t("panel.outcome"), actions: null, body });
  section.element.hidden = true;

  function set({ status, needs }) {
    const line = OUTCOME_LINES[status] ?? null;
    const parts = [
      line === null ? null : el("p", { class: "context-outcome-line", dataset: { outcome: status } }, icon(line.icon), el("span", null, t(line.words))),
      needs.trim() === "" ? null : el("div", { class: "context-needs" }, el("strong", null, t("outcome.needs")), el("p", null, needs)),
    ];
    body.replaceChildren(...parts.filter((part) => part !== null));
    section.element.hidden = line === null;
  }

  return { element: section.element, set };
}

/** The skills and the MCP servers a run used (item 546), from its tool calls; the section keeps out of sight until it
 * uses one. A skill is the `skill` tool's name, a server the one in `mcp__server__tool`. */
function skillsSection() {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-skills", title: t("panel.skills"), actions: null, body });
  section.element.hidden = true;
  // What the open run used, and what the chat's earlier turns did (item 650).
  let own = [];
  let before = [];

  function paint() {
    const usages = chatUsage(before, own);
    body.replaceChildren(
      ...usages.map((usage) =>
        el(
          "a",
          {
            class: "context-row",
            href: usage.kind === "skill" ? "#/customize/skills" : "#/customize/connectors",
            title: `${usage.name} — open ${usage.kind === "skill" ? "Skills" : "Connectors"} in Customize`,
          },
          row(
            usage.kind === "skill" ? "hammer" : "plug",
            usage.name,
            usage.count === 1 ? "used once" : `used ${usage.count} times`,
          ),
        ),
      ),
    );
    const skills = usages.filter((usage) => usage.kind === "skill").length;
    const servers = usages.length - skills;
    const counts = [];
    if (skills > 0) {
      counts.push(plural(skills, "skill", "skills"));
    }
    if (servers > 0) {
      counts.push(plural(servers, "connected app", "connected apps"));
    }
    section.setSummary(el("span", null, counts.join(" · ")));
    section.element.hidden = usages.length === 0;
  }

  return {
    element: section.element,
    add(call, earlier = false) {
      const usage = usageOf(call.tool, call.args ?? {});
      if (usage !== null) {
        if (earlier) {
          before = addUsage(before, usage);
        } else {
          own = addUsage(own, usage);
        }
        paint();
      }
    },
  };
}

/** The files the chat wrote, each with the lines it added and removed (for work on code), opening the Changes tab
 * where its diff is; `set(files)` takes the Changes section's reading. With the technical sections, after what came of
 * the task. */
function artifactsSection(onOpen) {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-artifacts", title: t("panel.changed"), actions: null, body });
  section.element.hidden = true;

  function set(files) {
    if (files.length === 0) {
      section.element.hidden = true;
      return;
    }
    const shown = files.slice(0, SECTION_ROWS);
    body.replaceChildren(
      ...shown.map((file) =>
        el(
          "button",
          { class: "context-row", type: "button", title: t("panel.openChanges", { path: file.path }), onclick: onOpen },
          row(
            fileMark(file.path),
            file.path.slice(file.path.lastIndexOf("/") + 1),
            [file.added > 0 ? `+${file.added}` : "", file.removed > 0 ? `−${file.removed}` : ""].filter(Boolean).join(" "),
          ),
        ),
      ),
    );
    if (files.length > shown.length) {
      body.append(
        el("button", { class: "context-more", type: "button", onclick: onOpen }, t("panel.moreInChanges", { count: files.length - shown.length })),
      );
    }
    section.setSummary(el("span", null, plural(files.length, "file", "files")));
    section.element.hidden = false;
  }

  return { element: section.element, set };
}

/** The searches and pages the run read; `add(readings)` takes those of one call, newest first. */
function readingSection() {
  // A list of its own that scrolls past eight rows (item 650), as a chat reads many pages.
  const body = el("div", { class: "context-list context-scroll" });
  const section = panelSection({ id: "run-reading", title: t("panel.sources"), actions: null, body });
  section.element.hidden = true;
  let own = [];
  let before = [];

  function paint() {
    // The open run's readings first, then the earlier turns' (item 650).
    const readings = addReadings(before, own);
    body.replaceChildren(
      ...readings.map((reading) =>
        reading.kind === "page"
          ? el(
              "a",
              { class: "context-row", href: reading.url, target: "_blank", rel: "noreferrer", title: reading.url },
              row("globe", reading.text, ""),
            )
          : el("div", { class: "context-row", title: reading.text }, row("search", reading.text, "")),
      ),
    );
    const pages = readings.filter((reading) => reading.kind === "page").length;
    const parts = [];
    if (pages > 0) {
      parts.push(plural(pages, "page", "pages"));
    }
    if (readings.length > pages) {
      parts.push(plural(readings.length - pages, "search", "searches"));
    }
    section.setSummary(el("span", null, parts.join(" · ")));
    section.element.hidden = readings.length === 0;
  }

  return {
    element: section.element,
    add(more, earlier = false) {
      if (earlier) {
        before = addReadings(before, more);
      } else {
        own = addReadings(own, more);
      }
      paint();
    },
  };
}

/** The notes the run saved, corrected or forgot, each opening its note in Settings › Memory; `add(change)` takes one
 * `{ id, change, words }`. */
function memorySection() {
  const body = el("div", { class: "context-list context-scroll" });
  const section = panelSection({ id: "run-memory", title: t("panel.memory"), actions: null, body });
  section.element.hidden = true;
  let own = [];
  let before = [];

  function paint() {
    const notes = chatMemory(before, own);
    body.replaceChildren(
      ...notes.map((note) => {
        const parts = row(
          "database",
          MEMORY_CHANGES[note.change] ?? note.change,
          note.words.trim() === "" ? "" : `“${clip(note.words, MEMORY_WORDS_CHARS)}”`,
        );
        return note.change === "Forgot"
          ? el("div", { class: "context-row" }, parts)
          : el("a", { class: "context-row", href: `#/settings/memory?note=${encodeURIComponent(note.id)}` }, parts);
      }),
    );
    section.setSummary(el("span", null, plural(notes.length, "note", "notes")));
    section.element.hidden = notes.length === 0;
  }

  return {
    element: section.element,
    add(change, earlier = false) {
      if (earlier) {
        before = addMemory(before, change);
      } else {
        own = addMemory(own, change);
      }
      paint();
    },
  };
}

/**
 * The sections of the Task tab (each hidden until it has something to say): `head`, what came of the task (Results,
 * Outcome), which stands over the recap, and `rest`, under it: Sources, then the technical (Changed files, Skills &
 * connected apps, Memory updated). `onOpenChanges` shows the Changes tab. `onTool(call)` takes a tool call of the run
 * (`{ tool, args }`), `onMemory(change)` a change of the memory, `onFiles(files)` the chat's changed files and
 * `onAnswer(answer, outcome)` the run's answer node with how it ended (`{ status, needs }`).
 */
export function createContextPanel({ onOpenChanges }) {
  const results = resultsSection();
  const outcome = outcomeSection();
  const skills = skillsSection();
  const artifacts = artifactsSection(onOpenChanges);
  const reading = readingSection();
  const memory = memorySection();
  return {
    head: [results.element, outcome.element],
    rest: [reading.element, artifacts.element, skills.element, memory.element],
    onAnswer: (answer, ended) => {
      results.set(answer);
      outcome.set(ended);
    },
    onTool: (call) => {
      skills.add(call);
      const readings = readingsOf(call.tool, call.args ?? {});
      if (readings.length > 0) {
        reading.add(readings);
      }
    },
    onMemory: (change) => memory.add(change),
    onFiles: (files) => artifacts.set(files),
    // What the chat's turns before the open run used, read and remembered (item 650, `GET /runs/{id}/chat-context`),
    // oldest first: it stands under the open run's in each section, so a new message starts from the chat's context.
    onEarlier: (steps) => {
      for (const step of steps) {
        skills.add(step, true);
        const readings = readingsOf(step.tool, step.args ?? {});
        if (readings.length > 0) {
          reading.add(readings, true);
        }
        const change = memoryChange({ kind: step.kind, ok: true, text: step.text, summary: step.summary }, step.args ?? {});
        if (change !== null) {
          memory.add(change, true);
        }
      }
    },
  };
}
