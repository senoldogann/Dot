// Settings › System › Diagnostics (item 370): the daemon's process and those it started, with what each takes of the
// CPU and of memory, read again every few seconds while the page is seen; the room the files Dot keeps take on disk; and
// the logs, the last lines of one, with a filter. A process is named by its executable alone, and a log's lines come
// with their secrets masked (dotagent/server/diagnostics.py).

import { api } from "../api.js";
import { sizeLabel } from "../attachments.js";
import { ago, duration, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { inlineError, inlineLoading, pageHead } from "../panels.js";
import { scrollCue } from "../scrollcue.js";
import { attempt } from "../ui.js";

// How often the processes are read again while the page is seen.
const PROCESSES_EVERY_MS = 5000;
// How many of a log's last lines the page asks for.
const LOG_LINES = 300;

function card(title, side, body) {
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("span", { class: "grow" }, title), side), body);
}

/** A table whose cells never break: on a narrow window it scrolls sideways in its own box (item 360), and the box's
 * edge fades while more lies to the right (scrollcue.js, which the caller starts). */
function wideTable(heads, rows) {
  return el(
    "div",
    { class: "table-scroll" },
    el("table", { class: "table wide" }, el("thead", null, el("tr", null, heads.map((head) => el("th", null, head)))), el("tbody", null, rows)),
  );
}

function refreshButton(label, onclick) {
  return el("button", { class: "btn ghost sm", type: "button", "aria-label": label, title: label, onclick }, icon("refresh"), "Refresh");
}

/** A process of the daemon's tree, set in by how deep it is under the daemon. */
function processRow(process, daemonPid) {
  return el(
    "tr",
    { dataset: { pid: String(process.pid) } },
    el("td", null, el("span", { class: "process-name", dataset: { depth: String(Math.min(process.depth, 4)) } }, process.pid === daemonPid ? `${process.name} (Dot's daemon)` : process.name)),
    el("td", { class: "mono" }, String(process.pid)),
    el("td", null, `${process.cpu_percent.toFixed(1)} %`),
    el("td", null, sizeLabel(process.memory_bytes)),
    el("td", null, process.running_s < 1 ? "under a second" : duration(process.running_s)),
  );
}

/** The room one kind of file Dot keeps takes: its name over where it is, its size at the side. */
function folderRow(folder) {
  return el(
    "div",
    { class: "list-row folder-row" },
    el("div", { class: "row-main" }, el("div", { class: "row-title" }, folder.name), ...folder.paths.map((path) => el("div", { class: "row-text mono small muted folder-path" }, path))),
    el("span", { class: "folder-size" }, sizeLabel(folder.bytes)),
  );
}

export function mount(root) {
  let disposed = false;
  let timer = 0;
  const totals = el("span", { class: "small muted" });
  const processesBody = el("div", null, inlineLoading());
  const diskBody = el("div", null, inlineLoading());
  const logsBody = el("div", null, inlineLoading());

  // The cue on the table now shown; the table is drawn again at every reading.
  let stopCue = () => {};

  async function loadProcesses() {
    try {
      const view = await api.get("/diagnostics/processes");
      if (disposed) {
        return;
      }
      totals.textContent = `${plural(view.processes.length, "process", "processes")} · ${view.cpu_percent.toFixed(1)} % CPU · ${sizeLabel(view.memory_bytes)}`;
      const table = wideTable(["Process", "PID", "CPU", "Memory", "Running for"], view.processes.map((process) => processRow(process, view.daemon_pid)));
      stopCue();
      processesBody.replaceChildren(table);
      stopCue = scrollCue(table);
    } catch (error) {
      if (!disposed) {
        stopCue();
        stopCue = () => {};
        processesBody.replaceChildren(inlineError(error, () => attempt(loadProcesses)));
      }
    }
  }

  // Read again every few seconds while the page is seen; a page out of sight waits for its next look.
  function follow() {
    timer = setTimeout(async () => {
      if (document.visibilityState === "visible") {
        await loadProcesses();
      }
      if (!disposed) {
        follow();
      }
    }, PROCESSES_EVERY_MS);
  }

  async function loadDisk() {
    diskBody.replaceChildren(inlineLoading());
    try {
      const view = await api.get("/diagnostics/disk");
      if (!disposed) {
        diskBody.replaceChildren(el("div", { class: "list" }, view.folders.map(folderRow)));
      }
    } catch (error) {
      if (!disposed) {
        diskBody.replaceChildren(inlineError(error, () => attempt(loadDisk)));
      }
    }
  }

  // The logs: the one written last is shown first; the filter keeps the lines that hold its words.
  let chosen = null;
  let lines = [];
  const picker = el("select", { class: "select", "aria-label": "Log", onchange: () => attempt(() => loadTail(picker.value)) });
  const filter = el("input", { class: "input", type: "search", placeholder: "Filter the lines", "aria-label": "Filter the lines", oninput: () => paintLines() });
  const out = el("pre", { class: "log-lines", tabindex: "0", "aria-label": "The last lines of the log" });

  function paintLines() {
    const words = filter.value.trim().toLowerCase();
    const shown = words === "" ? lines : lines.filter((line) => line.toLowerCase().includes(words));
    out.textContent = shown.length > 0 ? shown.join("\n") : words === "" ? "The log is empty." : "No line holds these words.";
    out.scrollTop = out.scrollHeight;
  }

  async function loadTail(name) {
    chosen = name;
    const tail = await api.get(`/diagnostics/logs/${encodeURIComponent(name)}?lines=${LOG_LINES}`);
    if (!disposed && chosen === name) {
      lines = tail.lines;
      paintLines();
    }
  }

  async function loadLogs() {
    try {
      const view = await api.get("/diagnostics/logs");
      if (disposed) {
        return;
      }
      if (view.files.length === 0) {
        logsBody.replaceChildren(el("div", { class: "card-pad hint" }, `No log in ${view.folder} yet.`));
        return;
      }
      const name = view.files.some((file) => file.name === chosen) ? chosen : view.files[0].name;
      picker.replaceChildren(...view.files.map((file) => el("option", { value: file.name, selected: file.name === name }, `${file.name} · ${sizeLabel(file.bytes)} · ${ago(file.modified_at)}`)));
      logsBody.replaceChildren(el("div", { class: "card-pad stack" }, el("div", { class: "log-controls" }, picker, filter), out));
      await loadTail(name);
    } catch (error) {
      if (!disposed) {
        logsBody.replaceChildren(inlineError(error, () => attempt(loadLogs)));
      }
    }
  }

  root.append(
    el("a", { class: "back", href: "#/settings/system" }, icon("arrowLeft"), "System"),
    pageHead("Diagnostics", "Dot's processes and what they take, the room its files take, and its logs, read on this Mac.", null),
    card("Processes", totals, processesBody),
    card("Disk", refreshButton("Measure the folders again", () => attempt(loadDisk)), diskBody),
    card("Logs", refreshButton("Read the log again", () => attempt(loadLogs)), logsBody),
  );
  attempt(async () => {
    await loadProcesses();
    if (!disposed) {
      follow();
    }
  });
  attempt(loadDisk);
  attempt(loadLogs);
  return () => {
    disposed = true;
    clearTimeout(timer);
    stopCue();
  };
}
