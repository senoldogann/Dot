// The context sections of a run's Run tab (item 539), under its recap: the skills and MCP servers it can use, the files
// its chat wrote, the pages and searches it read and the notes it saved. The readings and notes are gathered from the
// run's events as they come, and again from its history, each once and newest first (contextpanel.js); nothing is shown
// of what the run did not do. Each row wears the theme's own colours (run.css), so the panel reads in every theme.

import { addMemory, addReadings, readingsOf } from "./contextpanel.js";
import { clip, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { loadPalette } from "./palette.js";
import { panelSection } from "./panels.js";

// How many rows a section shows before it counts the rest; the readings and the notes keep their own longer lists.
const SECTION_ROWS = 5;
const MEMORY_WORDS_CHARS = 80;
// What the conversation says of a memory change (timeline.js), in the same words.
const MEMORY_CHANGES = { Saved: "Saved to memory", Updated: "Updated memory", Forgot: "Removed from memory" };

/** The mark of a row on its own tile: an icon, or the letters of a file's kind. */
function tile(mark) {
  return el("span", { class: "context-tile" }, typeof mark === "string" ? icon(mark) : mark);
}

/** A row of a section: its mark and its line, and beside them what says how it stands. */
function row(mark, name, note) {
  return [tile(mark), el("span", { class: "context-name" }, name), note === "" ? null : el("span", { class: "context-note" }, note)];
}

/** A file's mark: the letters of its kind ("MD", "TS") on an accent tile, or a folded page when its kind has no short
 * name. */
function fileMark(path) {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  const kind = dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
  return kind.length >= 1 && kind.length <= 3 ? el("span", { class: "context-kind" }, kind) : "file";
}

/** The skills and the MCP servers a run can use (`GET /palette`), read once; the section keeps out of sight until the
 * palette names one and stays there when it fails. */
function skillsSection() {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-skills", title: "Skills & MCP", actions: null, body });
  section.element.hidden = true;
  loadPalette().then(
    (palette) => {
      const { skills, servers } = palette;
      if (skills.length === 0 && servers.length === 0) {
        return;
      }
      const rows = [
        ...skills.map((skill) =>
          el("a", { class: "context-row", href: "#/customize/skills", title: skill.description }, row("hammer", skill.name, "")),
        ),
        ...servers.map((server) =>
          el(
            "a",
            { class: "context-row", href: "#/customize/plugins", title: server.tools.map((tool) => tool.name).join(", ") },
            row("plug", server.name, plural(server.tools.length, "tool", "tools")),
          ),
        ),
      ];
      const shown = rows.slice(0, SECTION_ROWS);
      body.replaceChildren(...shown);
      if (rows.length > shown.length) {
        // The skills stand before the servers, so the link goes where the first row it hides lives.
        const href = skills.length > SECTION_ROWS ? "#/customize/skills" : "#/customize/plugins";
        body.append(el("a", { class: "context-more", href }, `${rows.length - shown.length} more in Customize`));
      }
      const counts = [];
      if (skills.length > 0) {
        counts.push(plural(skills.length, "skill", "skills"));
      }
      if (servers.length > 0) {
        counts.push(plural(servers.length, "MCP server", "MCP servers"));
      }
      section.setSummary(el("span", null, counts.join(" · ")));
      section.element.hidden = false;
    },
    (error) => console.warn("the skills and MCP servers could not be read", error),
  );
  return section.element;
}

/** The files the chat wrote, each with its counts, opening the Changes tab where its diff is; `set(files)` takes the
 * Changes section's reading. */
function artifactsSection(onOpen) {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-artifacts", title: "Artifacts", actions: null, body });
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
          { class: "context-row", type: "button", title: `${file.path} — open Changes`, onclick: onOpen },
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
        el("button", { class: "context-more", type: "button", onclick: onOpen }, `${files.length - shown.length} more in Changes`),
      );
    }
    section.setSummary(el("span", null, plural(files.length, "file", "files")));
    section.element.hidden = false;
  }

  return { element: section.element, set };
}

/** The searches and pages the run read; `add(readings)` takes those of one call, newest first. */
function readingSection() {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-reading", title: "Web reading", actions: null, body });
  section.element.hidden = true;
  let readings = [];

  function paint() {
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
    add(more) {
      readings = addReadings(readings, more);
      paint();
    },
  };
}

/** The notes the run saved, corrected or forgot, each opening its note in Settings › Memory; `add(change)` takes one
 * `{ id, change, words }`. */
function memorySection() {
  const body = el("div", { class: "context-list" });
  const section = panelSection({ id: "run-memory", title: "Memory updated", actions: null, body });
  section.element.hidden = true;
  let notes = [];

  function paint() {
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
    add(change) {
      notes = addMemory(notes, change);
      paint();
    },
  };
}

/**
 * The context sections of the Run tab, in the order the panel shows them: Skills & MCP, Artifacts, Web reading and
 * Memory updated (each hidden until it has something to say). `onOpenChanges` shows the Changes tab. `onTool(call)`
 * takes a tool call of the run (`{ tool, args }`), `onMemory(change)` a change of the memory and `onFiles(files)` the
 * chat's changed files.
 */
export function createContextPanel({ onOpenChanges }) {
  const skills = skillsSection();
  const artifacts = artifactsSection(onOpenChanges);
  const reading = readingSection();
  const memory = memorySection();
  return {
    elements: [skills, artifacts.element, reading.element, memory.element],
    onTool: (call) => {
      const readings = readingsOf(call.tool, call.args ?? {});
      if (readings.length > 0) {
        reading.add(readings);
      }
    },
    onMemory: (change) => memory.add(change),
    onFiles: (files) => artifacts.set(files),
  };
}
