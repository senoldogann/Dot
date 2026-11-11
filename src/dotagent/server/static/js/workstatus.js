// The work told in one line (item 663): with Settings › Interface › Work in a chat on "What it is doing", the steps,
// the thoughts and the commands of a run fold out of the conversation and one line under it says what the run does
// now, in a few words that change as it goes ("Searching the web", "Reading a page", "Thinking"). Each phrase is of
// what the run's latest event is: nothing is said that the run is not doing. While the same work goes on, the line
// turns through the other words for it, so that a long step still reads as alive. The line is a button: pressed, the
// conversation shows every step again, and pressed again folds them back. While the run works it is its words alone,
// with no dots and no "Show the steps" beside them (item 971, the owner's ask): the line itself is the way. Once the run
// is over and has an answer, the way to the steps is the answer's foot (item 842), and the line goes; over with none,
// the line says "Show the steps", which is then all it says.
//
// As a messaging app's line under the last message (item 1091, the owner's ask with screenshots of another assistant):
// a command, a file, a search or a page is said with what it is ("Running gh repo view", "Reading README.md",
// "Searching the web for “Dot”"), and while the run waits for its model the line is the model typing: three dots that
// rise in turn in the face's place, and "Typing…". The model's own thinking, when it streams one, is "Thinking…" beside
// the face, as before.

import { clip, el, setText } from "./dom.js";
import { callKind, describeArguments, narrates } from "./tools.js";

// How long one phrase stands before the next words for the same work take its place.
const TURN_MS = 4000;
// The words for each kind of work (tools.js callKind), the first one said first.
const PHRASES = {
  typing: ["Typing"],
  thinking: ["Thinking", "Working it out", "Putting it together"],
  command: ["Running a command", "Working in the terminal"],
  read: ["Reading a file", "Going through the files"],
  write: ["Writing a file", "Putting it in a file"],
  search: ["Searching the web", "Looking through the results"],
  page: ["Reading a page", "Going through the page"],
  browser: ["Working in the browser", "Clicking through the page"],
  desktop: ["Working on the Mac", "Using an app"],
  schedule: ["Setting up a schedule"],
  memory: ["Checking its notes", "Keeping a note"],
  helper: ["Handing a part to a helper", "Waiting for the helper"],
  other: ["Working", "On it"],
};
// The tools whose call hands work to another run: the agent's name, when the call names one, is said.
const HANDOFF_TOOLS = new Set(["message_agent", "delegate"]);
// The calls the line says nothing of: the answer and the reaction on your message say them (item 664).
const UNSAID_TOOLS = new Set(["finish", "react"]);
// The events after which the run is no longer at work in a way the line should tell: a card or the message says it.
const QUIET = new Set(["approval_requested", "question_left"]);
// How much of what a call is about the line says, so that it stays one line on a phone.
const DETAIL_CHARS = 48;
// The kinds of work said with what they are about, by the call's main argument (tools.js describeArguments): the rest
// keep their phrases, as a click's or an app's argument says nothing to the reader.
const DETAILED = {
  command: (detail) => `Running ${clip(detail.split("\n")[0], DETAIL_CHARS)}`,
  read: (detail) => `Reading ${clip(lastPart(detail), DETAIL_CHARS)}`,
  write: (detail) => `Writing ${clip(lastPart(detail), DETAIL_CHARS)}`,
  search: (detail) => `Searching the web for “${clip(detail, DETAIL_CHARS)}”`,
  page: (detail) => `Reading ${clip(hostOf(detail), DETAIL_CHARS)}`,
};

/** What a step's message says it does next, as the line says it (item 1131): its first line, without the marks of
 * markdown or the stop at its end ("Kanıt netleşti. Son bir kontrol yapıp sonucu yazıyorum." stays whole). */
export function toldWords(text) {
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
 * the chat as the timeline does, `setActive(bool)` says whether the run is at work, and `dispose()` stops its timer.
 * `onToggle(shown)` runs when the reader asks for the steps (`shown` true) or folds them back.
 */
export function createWorkStatus({ rootRunId, active, onToggle, face = null }) {
  let words = el("span", { class: "work-status-words" });
  const more = el("span", { class: "work-status-more" });
  // The face of who works stands first, in the place the answer's face takes (item 843), and the words where the words
  // of the messages start.
  const element = el(
    "button",
    { class: "work-status", type: "button", "aria-pressed": "false" },
    face === null ? null : el("span", { class: "turn-face", "aria-hidden": "true" }, face),
    // The model typing (item 1091): drawn in the face's place while the line says "Typing…" (run.css).
    el("span", { class: "typing-dots", "aria-hidden": "true" }, el("span"), el("span"), el("span")),
    words,
    more,
  );
  let working = active;
  let kind = "typing";
  let name = "";
  // What the call at work is about, in the line's words, or "" (DETAILED).
  let detail = "";
  // The calls of the run that have not come back: a step's read-only calls run at once, and the model is asked again
  // only when the last of them is back.
  let open = 0;
  let turn = 0;
  let quiet = false;
  let shown = false;
  let timer = 0;
  // What the run's latest message said it does next (item 1131), "" once a step says nothing: the line says it while
  // the step's calls run and the model is asked again, as the message folded with the steps.
  let told = "";

  function phrase() {
    if (told !== "") {
      return told;
    }
    if (detail !== "") {
      return detail;
    }
    if (kind === "helper" && name !== "") {
      return turn % 2 === 0 ? `Asking ${name}` : `Waiting for ${name}`;
    }
    const list = PHRASES[kind] ?? PHRASES.other;
    return list[turn % list.length];
  }

  function paint() {
    element.dataset.state = working ? (quiet ? "quiet" : "working") : "over";
    element.dataset.kind = kind;
    const said = working ? `${phrase()}…` : "";
    if (said !== words.textContent) {
      // A new span, so that the new words fade in as the first ones did.
      const fresh = el("span", { class: "work-status-words" }, said);
      words.replaceWith(fresh);
      words = fresh;
    }
    // Shown only once the run is over: while it works the line itself shows and folds the steps, and the words stay
    // in its name for a screen reader, out of sight (item 977), as its tooltip says them to the pointer.
    setText(more, shown ? "Hide the steps" : "Show the steps");
    more.classList.toggle("visually-hidden", working);
    element.setAttribute("aria-pressed", String(shown));
    element.title = shown ? "Hide the steps" : "Show the steps";
  }

  function restart() {
    window.clearInterval(timer);
    timer = 0;
    if (working && !quiet) {
      timer = window.setInterval(() => {
        turn += 1;
        paint();
      }, TURN_MS);
    }
  }

  function become(next, agent = "", about = "") {
    if (next !== kind || agent !== name || about !== detail || quiet) {
      kind = next;
      name = agent;
      detail = about;
      turn = 0;
      quiet = false;
      restart();
    }
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
      quiet = false;
      restart();
      paint();
    } else if (record.kind === "model_call" || (record.kind === "tool_result" && open === 0)) {
      become("typing");
    } else if (record.kind === "delta" && data.kind === "thinking") {
      become("thinking");
    } else if (record.kind === "delta" && data.kind !== "reset" && String(data.text ?? "").trim() !== "") {
      // The step writes its message: the message itself says what goes on.
      quiet = true;
      restart();
      paint();
    } else if (record.kind === "tool_call" && typeof data.tool === "string" && !UNSAID_TOOLS.has(data.tool)) {
      const agent = typeof data.args?.agent === "string" ? data.args.agent : "";
      if (HANDOFF_TOOLS.has(data.tool)) {
        become("helper", agent);
        return;
      }
      const next = callKind(data.tool);
      const about = describeArguments(data.args).trim();
      become(next, "", Object.hasOwn(DETAILED, next) && about !== "" ? DETAILED[next](about) : "");
    } else if (QUIET.has(record.kind)) {
      quiet = true;
      restart();
      paint();
    }
  }

  function setActive(value) {
    if (value !== working) {
      working = value;
      if (working) {
        told = "";
        kind = "typing";
        name = "";
        detail = "";
        open = 0;
        turn = 0;
        quiet = false;
      }
      restart();
      paint();
    }
  }

  paint();
  restart();
  return {
    element,
    push,
    setActive,
    // The steps shown or folded back from elsewhere: the answer's foot (item 842), which starts from whether they are.
    toggle,
    shown: () => shown,
    dispose: () => window.clearInterval(timer),
  };
}
