// The work told in one line (item 663): with Settings › Interface › Work in a chat on "What it is doing", the steps,
// the thoughts and the commands of a run fold out of the conversation and one line under it says what the run does
// now, in a few words ("Searching the web", "Reading a page", "Thinking"). Each phrase is of what the run's latest
// event is: nothing is said that the run is not doing, and the words change only when an event changes what it does —
// never on a timer while the same work goes on. The line is a button: pressed, the conversation shows every step again,
// and pressed again folds them back. While the run works it is its words alone, with no dots and no "Show the steps"
// beside them (item 971, the owner's ask): the line itself is the way. Once the run is over and has an answer, the way to
// the steps is the answer's foot (item 842), and the line goes; over with none, the line says "Show the steps", which is
// then all it says.
//
// As a messaging app's line under the last message (item 1091, the owner's ask with screenshots of another assistant):
// a command, a file, a search or a page is said with what it is ("Running gh repo view", "Reading README.md",
// "Searching the web for “Dot”"), and while the run waits for its model the line is the model typing: three dots that
// rise in turn in the face's place, and "Typing…". The model's own thinking, when it streams one, is "Thinking…" beside
// the face, as before.
//
// The states a run is in, each from an event: waiting to start (a queued run, until `run_started`), at work (the
// words of its latest call or its model), waiting for you (an approval it asked, until it is answered),
// and stopping (Stop was pressed, until it ends). A change of state, never of words, is said to a screen reader through
// `announcer`, a polite status region: the words of each call are the line's, read when the reader comes to it.

import { clip, el, setText } from "./dom.js";
import { t } from "./i18n.js";
import { callKind, describeArguments, narrates } from "./tools.js";

// The words for each kind of work (tools.js callKind): one phrase each, which stays while the same work goes on.
/** @type {Record<string, import("./i18n.js").WordKey>} */
const PHRASES = {
  typing: "work.typing",
  thinking: "work.thinking",
  command: "work.command",
  read: "work.read",
  write: "work.write",
  search: "work.search",
  page: "work.page",
  browser: "work.browser",
  desktop: "work.desktop",
  schedule: "work.schedule",
  memory: "work.memory",
  helper: "work.helper",
  other: "work.other",
};
// The tools whose call hands work to another run: the agent's name, when the call names one, is said.
const HANDOFF_TOOLS = new Set(["message_agent", "delegate"]);
// The calls the line says nothing of: the answer and the reaction on your message say them (item 664).
const UNSAID_TOOLS = new Set(["finish", "react"]);
// The event of a step that waits for the owner's answer, and the one that ends that wait. A question left for later
// (`question_left`) is no wait: the run goes on, and its next event says what it does.
// How much of what a call is about the line says, so that it stays one line on a phone.
const DETAIL_CHARS = 48;
// The kinds of work said with what they are about, by the call's main argument (tools.js describeArguments): the rest
// keep their phrases, as a click's or an app's argument says nothing to the reader.
const DETAILED = {
  command: (detail) => t("work.commandOf", { what: clip(detail.split("\n")[0], DETAIL_CHARS) }),
  read: (detail) => t("work.readOf", { what: clip(lastPart(detail), DETAIL_CHARS) }),
  write: (detail) => t("work.writeOf", { what: clip(lastPart(detail), DETAIL_CHARS) }),
  search: (detail) => t("work.searchOf", { what: clip(detail, DETAIL_CHARS) }),
  page: (detail) => t("work.readOf", { what: clip(hostOf(detail), DETAIL_CHARS) }),
};

/** What a step's message says it does next, as the line says it (item 1131): its first line, without the marks of
 * markdown or the stop at its end ("Kanıt netleşti. Son bir kontrol yapıp sonucu yazıyorum." stays whole). */
function toldWords(text) {
  const first = text.trim().split("\n")[0] ?? "";
  return first
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.:;…]+$/u, "");
}

/** The name a path ends with ("README.md" of "~/work/README.md"). */
function lastPart(path) {
  const trimmed = path.replace(/\/+$/, "");
  return trimmed.slice(trimmed.lastIndexOf("/") + 1) || trimmed;
}

/** The site of an address ("github.com" of "https://github.com/senoldogann"), or the words as they are. */
function hostOf(address) {
  try {
    return new URL(address).host || address;
  } catch {
    return address;
  }
}

/**
 * The line of `rootRunId`'s work, after `face` (the still face of who works, or null). `push(record)` takes each event of
 * the chat as the timeline does, `setActive(bool)` says whether the run is at work (`queued`: it waits to start, until
 * its `run_started`), and `stopping()` that Stop was pressed. `onToggle(shown)` runs when the reader asks for
 * the steps (`shown` true) or folds them back. `announcer` is the status region that says a change of state; the page
 * places it.
 */
export function createWorkStatus({ rootRunId, active, queued, onToggle, face }) {
  let words = el("span", { class: "work-status-words" });
  const more = el("span", { class: "work-status-more" });
  // The face of who works stands first, in the place the answer's face takes (item 843), and the words where the words
  // of the messages start.
  const element = el(
    "button",
    { class: "work-status", type: "button", "aria-expanded": "false" },
    face === null ? null : el("span", { class: "turn-face", "aria-hidden": "true" }, face),
    // The model typing (item 1091): drawn in the face's place while the line says "Typing…" (run.css).
    el("span", { class: "typing-dots", "aria-hidden": "true" }, el("span"), el("span"), el("span")),
    words,
    more,
  );
  const announcer = el("div", { class: "visually-hidden", role: "status" });
  let working = active;
  let waiting = queued;
  let kind = "typing";
  let name = "";
  // What the call at work is about, in the line's words, or "" (DETAILED).
  let detail = "";
  // The calls of the run that have not come back: a step's read-only calls run at once, and the model is asked again
  // only when the last of them is back.
  let open = 0;
  // An approval or a question of the run waits for the reader: its card says what, the line that the run waits.
  let asking = false;
  // The step writes its message: the message itself says what goes on, and the line steps aside.
  let writing = false;
  let halting = false;
  let shown = false;
  // What the run's latest message said it does next (item 1131), "" once a step says nothing: the line says it while
  // the step's calls run and the model is asked again, as the message folded with the steps.
  let told = "";
  // The state last said to a screen reader, so that each is said once.
  let said = "";

  /** The state the run is in, of those a reader is told of: "" while it is at work, whose words are the line's. */
  function state() {
    if (!working) {
      return "";
    }
    if (halting) {
      return "stopping";
    }
    if (waiting) {
      return "queued";
    }
    return asking ? "waiting" : "";
  }

  function phrase() {
    const now = state();
    if (now === "stopping") {
      return t("work.stopping");
    }
    if (now === "queued") {
      return t("work.queued");
    }
    if (now === "waiting") {
      return t("work.waitingForYou");
    }
    if (told !== "") {
      return told;
    }
    if (detail !== "") {
      return detail;
    }
    if (kind === "helper" && name !== "") {
      return t("work.waitingFor", { name });
    }
    return t(PHRASES[kind] ?? PHRASES.other);
  }

  function announce() {
    // A run that is over leaves what was said last: the page says its end there (the answer's), which a repaint of
    // the line must not wipe.
    if (!working) {
      said = "";
      return;
    }
    const now = state();
    if (now === said) {
      return;
    }
    said = now;
    announcer.textContent = now === "" ? "" : phrase();
  }

  function paint() {
    const now = state();
    element.dataset.state = working ? (writing && now === "" ? "quiet" : "working") : "over";
    // A state of its own draws no typing dots: the run does not wait for its model then.
    element.dataset.kind = now === "" ? kind : now;
    const line = working ? `${phrase()}…` : "";
    if (line !== words.textContent) {
      // A new span, so that the new words fade in as the first ones did.
      const fresh = el("span", { class: "work-status-words" }, line);
      words.replaceWith(fresh);
      words = fresh;
    }
    // Shown only once the run is over: while it works the line itself shows and folds the steps, and the words stay
    // in its name for a screen reader, out of sight (item 977), as its tooltip says them to the pointer.
    const toggleWords = shown ? t("work.hideSteps") : t("work.showSteps");
    setText(more, toggleWords);
    more.classList.toggle("visually-hidden", working);
    element.setAttribute("aria-expanded", String(shown));
    element.title = toggleWords;
    announce();
  }

  function become(next, agent, about) {
    kind = next;
    name = agent;
    detail = about;
    asking = false;
    writing = false;
    paint();
  }

  function toggle() {
    shown = !shown;
    paint();
    onToggle(shown);
  }

  element.addEventListener("click", toggle);

  function push(record) {
    if (record.run_id !== rootRunId) {
      return;
    }
    const data = record.data ?? {};
    if (record.kind === "run_started") {
      waiting = false;
      paint();
      return;
    }
    if (record.kind === "tool_call") {
      open += 1;
    } else if (record.kind === "tool_result") {
      open = Math.max(0, open - 1);
    }
    // The model is asked again once the last of the step's calls came back, and `model_call` is told when an answer
    // came: in between the run waits for the model, which is it typing.
    if (record.kind === "assistant") {
      const text = typeof data.text === "string" ? data.text : "";
      told = text.trim() !== "" && narrates(Array.isArray(data.tool_calls) ? data.tool_calls : []) ? toldWords(text) : "";
      asking = false;
      writing = false;
      paint();
    } else if (record.kind === "model_call" || (record.kind === "tool_result" && open === 0)) {
      become("typing", "", "");
    } else if (record.kind === "delta" && data.kind === "thinking") {
      become("thinking", "", "");
    } else if (record.kind === "delta" && data.kind !== "reset" && data.says !== "update" && String(data.text ?? "").trim() !== "") {
      // Words in sight step the line aside, as they say what goes on: a message's, and those that may be the answer
      // (item 1166). An update streams folded with the steps, and the line says the work while it does.
      writing = true;
      paint();
    } else if (record.kind === "tool_call" && typeof data.tool === "string" && !UNSAID_TOOLS.has(data.tool)) {
      const agent = typeof data.args?.agent === "string" ? data.args.agent : "";
      if (HANDOFF_TOOLS.has(data.tool)) {
        become("helper", agent, "");
        return;
      }
      const next = callKind(data.tool);
      const about = describeArguments(data.args).trim();
      become(next, "", Object.hasOwn(DETAILED, next) && about !== "" ? DETAILED[next](about) : "");
    } else if (record.kind === "approval_requested") {
      asking = true;
      writing = false;
      paint();
    } else if (record.kind === "approval_decided") {
      // Answered: the step goes on (or is refused), and the line says the work it was at again.
      asking = false;
      paint();
    }
  }

  function setActive(value) {
    if (value !== working) {
      working = value;
      told = "";
      kind = "typing";
      name = "";
      detail = "";
      open = 0;
      asking = false;
      writing = false;
      halting = false;
      waiting = false;
      paint();
    }
  }

  paint();
  return {
    element,
    announcer,
    push,
    setActive,
    stopping: () => {
      halting = true;
      paint();
    },
    // The steps shown or folded back from elsewhere: the answer's foot (item 842), which starts from whether they are.
    toggle,
    shown: () => shown,
  };
}
