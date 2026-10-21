// One tool call of a run: a collapsed row that shows what ran and how it went, and opens to the arguments and output.

import { imageUrl } from "./api.js";
import { clip, copyText, duration, el, plural } from "./dom.js";
import { icon, toolIconName } from "./icons.js";
import { showImage, toast } from "./ui.js";
import { showsWhy, whyText } from "./why.js";

const PRIMARY_KEYS = ["command", "url", "path", "query", "goal", "text", "name", "app", "ref", "title", "script", "key"];
const SUMMARY_CHARS = 200;
const TEXT_PREVIEW_CHARS = 6000;

// What a tool call is, in the words of a flow's summary (item 374): "Ran 2 commands and read 1 file".
const KIND_OF_TOOL = {
  shell: "command",
  // A terminal is opened on a command and typed into; reading it and closing it say nothing a count of them would.
  terminal_open: "command",
  terminal_send: "command",
  web_extract: "page",
  memory_update: "memory",
  schedule_delete: "schedule",
  read_file: "read",
  write_file: "write",
  edit_file: "write",
  web_search: "search",
  web_fetch: "page",
  schedule_create: "schedule",
  schedule_list: "schedule",
  memory_save: "memory",
};

/** What one tool call counted as in a flow's summary. */
export function callKind(tool) {
  if (Object.hasOwn(KIND_OF_TOOL, tool)) {
    return KIND_OF_TOOL[tool];
  }
  if (tool.startsWith("browser_")) {
    return "browser";
  }
  if (tool.startsWith("desktop_")) {
    return "desktop";
  }
  return "other";
}

// How each count reads: the first part says what it did, the others go on with it ("Ran 2 commands and read 1 file").
const FLOW_WORDS = {
  command: (count) => `Ran ${plural(count, "command", "commands")}`,
  read: (count) => `read ${plural(count, "file", "files")}`,
  write: (count) => `wrote ${plural(count, "file", "files")}`,
  search: (count) => `searched the web ${count === 1 ? "once" : `${count} times`}`,
  page: (count) => `opened ${plural(count, "page", "pages")}`,
  browser: (count) => `took ${plural(count, "browser step", "browser steps")}`,
  desktop: (count) => `used the desktop ${count === 1 ? "once" : `${count} times`}`,
  schedule: (count) => `changed ${plural(count, "schedule", "schedules")}`,
  memory: (count) => `kept ${plural(count, "note", "notes")}`,
  other: (count) => `used ${plural(count, "tool", "tools")}`,
};
// The order the parts are said in, whatever order the calls came in.
const FLOW_ORDER = ["command", "read", "write", "search", "page", "browser", "desktop", "schedule", "memory", "other"];

// The mark of each kind of call, as its rows wear it (icons.js `toolIconName`): a group of steps that is over wears the
// mark of the kind its summary starts with (item 402).
const KIND_ICONS = {
  command: "terminal",
  read: "file",
  write: "file",
  search: "globe",
  page: "globe",
  browser: "globe",
  desktop: "monitor",
  schedule: "calendar",
  memory: "database",
  other: "wrench",
};

/** The mark of a group of calls — the terminal's for "Ran 2 commands and read 1 file" — or null when it holds no call. */
export function flowIcon(kinds) {
  const first = FLOW_ORDER.find((kind) => (kinds[kind] ?? 0) > 0);
  return first === undefined ? null : KIND_ICONS[first];
}

/** The summary of a group of calls — "Ran 2 commands and read 1 file" — or "" when it holds no call. */
export function flowWords(kinds) {
  const parts = FLOW_ORDER.filter((kind) => (kinds[kind] ?? 0) > 0).map((kind) => FLOW_WORDS[kind](kinds[kind]));
  if (parts.length === 0) {
    return "";
  }
  const said = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `${said[0].toUpperCase()}${said.slice(1)}`;
}

/** The counts of the calls a group of steps made, by `callKind`. */
export function countKinds(tools) {
  const kinds = {};
  for (const tool of tools) {
    const kind = callKind(tool);
    kinds[kind] = (kinds[kind] ?? 0) + 1;
  }
  return kinds;
}

/** A short line for what the model asked the tool to do, taken from the most telling argument. */
export function describeArguments(args) {
  if (args === null || typeof args !== "object") {
    return "";
  }
  const action = typeof args.action === "string" ? args.action : "";
  for (const key of PRIMARY_KEYS) {
    const value = args[key];
    if (typeof value === "string" && value !== "") {
      return clip(`${action} ${value}`, SUMMARY_CHARS);
    }
  }
  return action;
}

function firstLine(text) {
  const line = text.split("\n").find((candidate) => candidate.trim() !== "");
  return line === undefined ? "" : clip(line, SUMMARY_CHARS);
}

function textBlock(text, failed) {
  const view = el("pre", { class: failed ? "out bad" : "out" }, text.length > TEXT_PREVIEW_CHARS ? text.slice(0, TEXT_PREVIEW_CHARS) : text);
  if (text.length <= TEXT_PREVIEW_CHARS) {
    return view;
  }
  const more = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      onclick: () => {
        view.textContent = text;
        more.remove();
      },
    },
    `Show all (${text.length.toLocaleString("en-GB")} characters)`,
  );
  return el("div", { class: "stack tight" }, view, more);
}

function thumbnail(name) {
  const image = el("img", { alt: "Screenshot", loading: "lazy" });
  const holder = el("button", { class: "shot", type: "button", "aria-label": "Show the screenshot at full size" }, image);
  imageUrl(name).then(
    (url) => {
      image.src = url;
      holder.addEventListener("click", () => showImage(url, "Screenshot"));
    },
    (error) => {
      holder.replaceChildren(el("span", { class: "small muted" }, `Image unavailable: ${error.message}`));
      holder.disabled = true;
    },
  );
  return holder;
}

/** Why the step ran (item 468), with the way to the rules when one of them decided it. */
function whyLine(decision, flags) {
  return el(
    "p",
    { class: "small why-line" },
    whyText(decision, flags),
    decision.source === "rule" ? " " : null,
    decision.source === "rule" ? el("a", { class: "why-rules", href: "#/settings/rules" }, "Rules") : null,
  );
}

function section(title, content, copyable) {
  return el(
    "div",
    null,
    el(
      "div",
      { class: "row" },
      el("h4", null, title),
      el("span", { class: "spacer" }),
      copyable === null
        ? null
        : el(
            "button",
            {
              class: "btn ghost sm",
              type: "button",
              onclick: () =>
                copyText(copyable).then(
                  () => toast("ok", "Copied"),
                  (error) => toast("error", `Could not copy: ${error.message}`),
                ),
            },
            icon("copy"),
            "Copy",
          ),
    ),
    content,
  );
}

/** A tool's name, with a place to break after each `__` (an MCP tool's is `mcp__server__tool`) for a row too narrow for
 * it (item 360). */
function nameParts(tool) {
  return tool.split(/(?<=__)/).flatMap((part, index) => (index === 0 ? [part] : [el("wbr", null), part]));
}

/**
 * A row for a tool call, one line in line with its group's head (item 402): its mark (a spinner while it runs, then the
 * mark of its kind, in the failure's colour when it failed; item 403), what it did and the way into it. A command is the command itself, its
 * tool's name for screen readers alone and the policy's words its tooltip, since its mark says what it is; another call,
 * whose mark several tools share (a file's: read, write, edit), keeps its tool's name and says what it did in the
 * policy's words once it is done ("Read notes.txt"). `finish(result)` fills in the outcome once the `tool_result` event
 * arrives.
 */
export function toolRow({ tool, args }) {
  const state = el("span", { class: "tool-state" }, el("span", { class: "spinner" }));
  const words = describeArguments(args);
  const command = callKind(tool) === "command";
  const named = words === "" || !command;
  const summary = el("span", { class: "tool-sum" }, words);
  const time = el("span", { class: "tool-time" });
  const badges = el("span", { class: "cluster" });
  const head = el(
    "summary",
    { class: "tool-head", title: tool },
    state,
    el("span", { class: named ? "tool-name" : "tool-name visually-hidden" }, ...nameParts(tool)),
    summary,
    badges,
    time,
    el("span", { class: "chev" }, icon("chevron")),
  );
  const body = el("div", { class: "tool-body" });
  const details = el("details", { class: "tool running" }, head, body);
  const shots = el("div", { class: "shots" });
  const node = el("div", { class: "stack tight" }, details, shots);
  let result = null;
  let cut = false;
  let built = false;

  function buildBody() {
    built = true;
    const argsText = JSON.stringify(args, null, 2);
    body.replaceChildren(
      section("Arguments", el("pre", { class: "out" }, argsText), argsText),
      result === null
        ? el("div", { class: "small muted" }, cut ? "Cut off: the run ended before this call reported back." : "Still running…")
        : section(
            result.ok ? "Result" : "Error",
            result.text === "" ? el("div", { class: "small muted" }, "(no text)") : textBlock(result.text, !result.ok),
            result.text === "" ? null : result.text,
          ),
      result === null || !showsWhy(result.decision, result.kind) ? null : section("Why it ran", whyLine(result.decision, result.flags ?? []), null),
    );
  }

  details.addEventListener("toggle", () => {
    if (details.open && (!built || result !== null)) {
      buildBody();
    }
  });

  function finish(outcome) {
    result = outcome;
    details.classList.remove("running");
    state.replaceChildren(icon(toolIconName(tool)));
    state.className = `tool-state ${outcome.ok ? "ok" : "bad"}`;
    if (!outcome.ok) {
      summary.className = "tool-sum bad";
    } else if (!command && outcome.summary !== "") {
      summary.textContent = clip(outcome.summary, SUMMARY_CHARS);
    }
    // A command keeps its own words, what it came to its tooltip; a failure's first line is the tooltip of any call.
    head.title = outcome.ok ? outcome.summary || tool : firstLine(outcome.text) || "failed";
    time.textContent = duration(outcome.seconds);
    shots.replaceChildren(...outcome.images.map(thumbnail));
    if (details.open) {
      buildBody();
    }
  }

  /** The run is over: a call that never reported back was cut off. Returns whether this one was. */
  function settle() {
    if (result !== null || cut) {
      return false;
    }
    cut = true;
    details.classList.remove("running");
    state.replaceChildren(icon("alert"));
    state.className = "tool-state cut";
    badges.replaceChildren(el("span", { class: "badge b-warn" }, "cut off"));
    if (details.open) {
      buildBody();
    }
    return true;
  }

  return { node, finish, settle };
}
