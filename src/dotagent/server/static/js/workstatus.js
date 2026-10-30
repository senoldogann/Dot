// The work told in one line (item 663): with Settings › Interface › Work in a chat on "What it is doing", the steps,
// the thoughts and the commands of a run fold out of the conversation and one line under it says what the run does
// now, in a few words that change as it goes ("Searching the web", "Reading a page", "Thinking"). Each phrase is of
// what the run's latest event is: nothing is said that the run is not doing. While the same work goes on, the line
// turns through the other words for it, so that a long step still reads as alive. The line is a button: pressed, the
// conversation shows every step again, and pressed again folds them back; once the run is over and has an answer, the
// way to the steps is the answer's foot (item 842), and the line goes.

import { el, setText } from "./dom.js";
import { callKind } from "./tools.js";

// How long one phrase stands before the next words for the same work take its place.
const TURN_MS = 4000;
// The words for each kind of work (tools.js callKind), the first one said first.
const PHRASES = {
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

/**
 * The line of `rootRunId`'s work. `push(record)` takes each event of the chat as the timeline does, `setActive(bool)`
 * says whether the run is at work, and `dispose()` stops its timer. `onToggle(shown)` runs when the reader asks for the
 * steps (`shown` true) or folds them back.
 */
export function createWorkStatus({ rootRunId, active, onToggle }) {
  const dots = el("span", { class: "work-status-dots", "aria-hidden": "true" }, el("span"), el("span"), el("span"));
  let words = el("span", { class: "work-status-words" });
  const more = el("span", { class: "work-status-more" });
  const element = el("button", { class: "work-status", type: "button", "aria-pressed": "false" }, dots, words, more);
  let working = active;
  let kind = "thinking";
  let name = "";
  let turn = 0;
  let quiet = false;
  let shown = false;
  let timer = 0;

  function phrase() {
    if (kind === "helper" && name !== "") {
      return turn % 2 === 0 ? `Asking ${name}` : `Waiting for ${name}`;
    }
    const list = PHRASES[kind] ?? PHRASES.other;
    return list[turn % list.length];
  }

  function paint() {
    element.dataset.state = working ? (quiet ? "quiet" : "working") : "over";
    const said = working ? `${phrase()}…` : "";
    if (said !== words.textContent) {
      // A new span, so that the new words fade in as the first ones did.
      const fresh = el("span", { class: "work-status-words" }, said);
      words.replaceWith(fresh);
      words = fresh;
    }
    setText(more, shown ? "Hide the steps" : "Show the steps");
    element.setAttribute("aria-pressed", String(shown));
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

  function become(next, agent = "") {
    if (next !== kind || agent !== name || quiet) {
      kind = next;
      name = agent;
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
    if (record.kind === "model_call" || (record.kind === "delta" && data.kind === "thinking")) {
      become("thinking");
    } else if (record.kind === "delta" && data.kind !== "reset") {
      // The step writes its message: the message itself says what goes on.
      quiet = true;
      restart();
      paint();
    } else if (record.kind === "tool_call" && typeof data.tool === "string" && !UNSAID_TOOLS.has(data.tool)) {
      const agent = typeof data.args?.agent === "string" ? data.args.agent : "";
      become(HANDOFF_TOOLS.has(data.tool) ? "helper" : callKind(data.tool), HANDOFF_TOOLS.has(data.tool) ? agent : "");
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
        kind = "thinking";
        name = "";
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
