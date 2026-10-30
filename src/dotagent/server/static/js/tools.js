// One tool call of a run: a collapsed row that shows what ran and how it went, and opens to the arguments and output.

import { imageUrl } from "./api.js";
import { codeBlock } from "./codeblock.js";
import { clip, duration, el, plural } from "./dom.js";
import { icon, toolIconName } from "./icons.js";
import { showImage } from "./ui.js";
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
  other: (count, alone) => `took ${plural(count, alone ? "step" : "other step", alone ? "steps" : "other steps")}`,
};
// The order the parts are said in, whatever order the calls came in.
const FLOW_ORDER = ["command", "read", "write", "search", "page", "browser", "desktop", "schedule", "memory", "other"];

// The mark of each kind of call, as its rows wear it (icons.js `toolIconName`): a group of steps that is over wears the
// mark of the kind its summary starts with (item 402).
const KIND_ICONS = {
  command: "terminal",
  read: "eye",
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
  const present = FLOW_ORDER.filter((kind) => (kinds[kind] ?? 0) > 0);
  const parts = present.map((kind) => FLOW_WORDS[kind](kinds[kind], present.length === 1));
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

// What a step shows of its command, its arguments and its result is a box of code with its head (item 537): the
// words of the part at its left, the wrap switch (the lines wrap from the start) and the copy of the whole of it.
function stepBlock(label, text, failed, copyText = text) {
  return codeBlock({ text, label, copyText, tone: failed ? "bad" : null, wrapped: true, className: "code-out" });
}

function textBlock(text, failed) {
  const view = stepBlock(failed ? "Error" : "Result", text.length > TEXT_PREVIEW_CHARS ? text.slice(0, TEXT_PREVIEW_CHARS) : text, failed, text);
  if (text.length <= TEXT_PREVIEW_CHARS) {
    return view;
  }
  const more = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      onclick: () => {
        view.querySelector("code").textContent = text;
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

function section(title, content) {
  return el("div", null, el("div", { class: "row" }, el("h4", null, title)), content);
}

// What a row calls each of Dot's own tools (item 513): what it does, in a few words, rather than the name a model calls.
const TOOL_WORDS = {
  shell: "Command",
  terminal_open: "Open terminal",
  terminal_send: "Type in terminal",
  terminal_read: "Read terminal",
  terminal_close: "Close terminal",
  read_file: "Read file",
  write_file: "Write file",
  edit_file: "Edit file",
  list_dir: "List folder",
  search_files: "Search in files",
  find_files: "Find files",
  web_search: "Web search",
  web_fetch: "Fetch page",
  web_extract: "Read page",
  web_crawl: "Crawl site",
  browser_open: "Open page",
  browser_new_tab: "New tab",
  browser_snapshot: "Look at page",
  browser_find: "Find on page",
  browser_links: "List links",
  browser_act: "Act on page",
  browser_eval: "Run script on page",
  browser_scroll: "Scroll page",
  browser_read_text: "Read page text",
  browser_screenshot: "Screenshot page",
  browser_wait_text: "Wait for text",
  browser_back: "Go back",
  browser_switch_tab: "Switch tab",
  browser_close_tab: "Close tab",
  browser_upload: "Upload file",
  browser_fill_login: "Fill in login",
  browser_create_login: "Make a new login",
  browser_logins: "List logins",
  desktop_apps: "List apps",
  desktop_open_app: "Open app",
  desktop_read: "Read window",
  desktop_screenshot: "Screenshot screen",
  desktop_snapshot: "Look at window",
  desktop_act: "Act in app",
  desktop_clipboard_read: "Read clipboard",
  desktop_clipboard_write: "Copy to clipboard",
  apple_script: "Control an app",
  mail_read: "Read mail",
  mail_search: "Search mail",
  calendar_events: "Calendar",
  memory_save: "Save note",
  memory_search: "Search notes",
  memory_update: "Update note",
  memory_forget: "Forget note",
  write_notes: "Write notes",
  update_plan: "Update plan",
  mark_plan_step: "Mark plan step",
  skill: "Use skill",
  delegate: "Delegate",
  message_agent: "Message agent",
  create_agent: "Make agent",
  consult: "Ask a helper",
  notify: "Notify",
  show_page: "Show page",
  draft_message: "Draft message",
  ask_user: "Ask you",
  ask_secret: "Ask you for a secret",
  finish: "Finish",
  use_tools: "Turn on more actions",
  schedule_create: "Add schedule",
  schedule_list: "List schedules",
  schedule_delete: "Delete schedule",
  find_help: "Find help",
  code_fetch: "Fetch code",
};

/** A name in words, its first letter capital: `draft_reply` reads "Draft reply". */
function sentence(name) {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words === "" ? name : `${words[0].toUpperCase()}${words.slice(1)}`;
}

/** A server's name as a title: `microsoft-learn` reads "Microsoft Learn". */
function titled(name) {
  return name
    .split(/[_-]+/)
    .filter((word) => word !== "")
    .map((word) => `${word[0].toUpperCase()}${word.slice(1)}`)
    .join(" ");
}

/**
 * A task's words with the paths in it cut to their last two parts (item 669): a helper's row names what it is for, and
 * "/Users/dogan/Library/Application Support/Dot/workspace/housestory/brief.md" filled the row before the words that
 * said it. A path in backticks is cut whole, spaces and all; a bare one ends at a space. A path of two parts or fewer
 * stays as it is.
 */
export function shortPaths(text) {
  const cut = (path) => {
    const parts = path.split("/").filter((part) => part !== "");
    return parts.length <= 2 ? path : `…/${parts.slice(-2).join("/")}`;
  };
  return text.replace(/`(\/[^`\n]+)`/g, (_match, path) => `\`${cut(path)}\``).replace(/(^|\s)(\/(?:[^\s/]+\/){2,}[^\s/]*)/g, (_match, space, path) => `${space}${cut(path)}`);
}

/** What a tool's row calls it (item 513): one of Dot's in its own words ("Edit file"), an MCP tool by its server and
 * what it does, the server's name taken off the tool's own ("mcp__tavily__tavily_search" reads "Tavily · Search"), and
 * any other in words of its name. The name itself is the words' title. */
export function toolWords(tool) {
  if (Object.hasOwn(TOOL_WORDS, tool)) {
    return TOOL_WORDS[tool];
  }
  const mcp = /^mcp__(.+?)__(.+)$/.exec(tool);
  if (mcp === null) {
    return sentence(tool);
  }
  const [, server, name] = mcp;
  const own = server.replace(/-/g, "_").toLowerCase();
  const rest = name.toLowerCase().startsWith(`${own}_`) && name.length > own.length + 1 ? name.slice(own.length + 1) : name;
  return `${titled(server)} · ${sentence(rest)}`;
}

/**
 * A row for a tool call, one line in line with its group's head (item 402): its mark (a spinner while it runs, then the
 * mark of its kind, in the failure's colour when it failed; item 403), what it did and the way into it. A command is the
 * command itself, its tool's name for screen readers alone and the policy's words its tooltip, since its mark says what
 * it is; a read or a write says Read, Write or Edit and what it acts on from the start ("Read notes.txt"), its mark
 * saying which it is, and keeps no tool's name (item 506), the policy's words taking that place once it is done;
 * another call keeps its tool's name beside its words. `finish(result)` fills in the outcome once the `tool_result` event
 * arrives.
 */
export function toolRow({ tool, args }) {
  const state = el("span", { class: "tool-state" }, el("span", { class: "spinner" }));
  const words = describeArguments(args);
  const kind = callKind(tool);
  const command = kind === "command";
  // Opened, a command shows the command itself rather than its arguments (item 506), as long as it carries nothing else
  // that matters (a working folder for one), and then its arguments show whole.
  const asked = typeof args === "object" && args !== null ? Object.entries(args) : [];
  const aside = asked.filter(([key, value]) => key !== "command" && value !== null && value !== undefined && value !== "");
  const plain = typeof args === "object" && args !== null && typeof args.command === "string" && aside.length === 0 ? args.command : null;
  // A read or a write is read as what it is by its mark and its words, so it keeps no tool's name (item 506).
  const named = words === "" || (!command && kind !== "read" && kind !== "write");
  // A read or a write says which it is before its result comes, so a call still running, or one cut off, is read as what
  // it was; the policy's words take its place once it is done.
  const spoken = kind === "read" ? "Read" : kind === "write" ? (tool === "edit_file" ? "Edit" : "Write") : "";
  const summary = el("span", { class: "tool-sum" }, spoken === "" || words === "" ? words : `${spoken} ${words}`);
  const time = el("span", { class: "tool-time" });
  const badges = el("span", { class: "cluster" });
  const head = el(
    "summary",
    { class: "tool-head", title: tool },
    state,
    // The words name the tool, and the tool's own name is theirs to show on a hover (item 513).
    el("span", { class: named ? "tool-name" : "tool-name visually-hidden", title: tool }, toolWords(tool)),
    summary,
    badges,
    time,
    el("span", { class: "chev" }, icon("chevron")),
  );
  const body = el("div", { class: "tool-body" });
  const details = el("details", { class: command ? "tool command running" : "tool running" }, head, body);
  const shots = el("div", { class: "shots" });
  const node = el("div", { class: "stack tight" }, details, shots);
  let result = null;
  let cut = false;
  let built = false;

  function buildBody() {
    built = true;
    const argsText = JSON.stringify(args, null, 2);
    // replaceChildren writes a null as the word "null": a section that is not there is left out before (item 472).
    const parts = [
      plain === null ? stepBlock("What Dot sent", argsText, false) : stepBlock("Command", plain, false),
      result === null
        ? el("div", { class: "small muted" }, cut ? "Cut off: the task ended before this step reported back." : "Still running…")
        : result.text === ""
          ? section(result.ok ? "Result" : "Error", el("div", { class: "small muted" }, "(no text)"))
          : textBlock(result.text, !result.ok),
      result === null || !showsWhy(result.decision, result.kind) ? null : section("Why it ran", whyLine(result.decision, result.flags ?? [])),
    ];
    body.replaceChildren(...parts.filter((part) => part !== null));
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
