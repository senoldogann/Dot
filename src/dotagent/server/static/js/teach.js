// Teaching Dot a task (item 423): the panel the composer's "+" opens. The owner records the task once in the chat's tab,
// which comes to the front on the Mac and is shown here as it is painted; the steps they take are listed as they come,
// and Stop hands them to the review model, which writes the skill. The owner reads it and keeps it or throws it away.
//
// A saved task can repeat (item 666): every day, on weekdays or on a day of the week, at a time, in this chat, its runs
// starting with the skill picked, as Run it now's does.
//
// A recording is the tab's alone and lasts while the panel is open on it: closing the panel stops it and nothing of it
// is saved. A browser without a window refuses to start one, and the panel says why.

import { api, frameUrl, watchInView } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { loopRhythm, REPEAT_RHYTHMS, repeatCron, WEEK_DAYS } from "./rhythm.js";
import { toast } from "./ui.js";

// How often the panel reads the steps while the owner works: often enough to see one's own click, seldom enough to
// leave the daemon alone.
const POLL_MS = 700;

/**
 * The panel that records a task in the chat `threadId` and writes the skill of it, over the page until it is closed.
 * `onSaved(name)` is called with the skill's name once the owner keeps it.
 */
export function openTeachPanel({ threadId, onSaved }) {
  let poll = null;
  let view = null;
  let shownFrame = null;
  let complained = false;
  let skill = null;
  let saved = null;
  let recording = false;

  const steps = el("ol", { class: "teach-steps" });
  const address = el("p", { class: "teach-address" }, "The tab comes to the front: work in it, and every step you take shows here.");
  const words = el("textarea", {
    class: "textarea teach-words",
    rows: "2",
    placeholder: "What is this task for? “Pay the month's invoices” or “Book a meeting room for the team”",
    "aria-label": "What is this task for",
  });
  const image = el("img", { class: "teach-frame", alt: "", hidden: true });
  const live = el("div", { class: "teach-live" }, image, address);
  const draft = el("section", { class: "teach-draft", hidden: true });
  const start = el("button", { class: "btn primary", type: "button" }, "Start recording");
  const stop = el("button", { class: "btn", type: "button", hidden: true }, "Stop");
  const make = el("button", { class: "btn", type: "button", hidden: true }, "Write the skill");
  const keep = el("button", { class: "btn primary", type: "button", hidden: true }, "Save it");
  const go = el("button", { class: "btn", type: "button", hidden: true }, "Run it now");
  const close = el("button", { class: "btn ghost", type: "button", "aria-label": "Close" }, icon("x"));
  const rhythm = el("select", { class: "select", "aria-label": "How often" }, REPEAT_RHYTHMS.map((each) => el("option", { value: each.id }, each.label)));
  const weekDay = el("select", { class: "select", "aria-label": "Which day", hidden: true }, WEEK_DAYS.map(([value, name]) => el("option", { value }, name)));
  const time = el("input", { class: "input teach-time", type: "time", value: "09:00", "aria-label": "At what time" });
  const repeat = el("button", { class: "btn", type: "button" }, "Repeat it");
  const repeated = el("p", { class: "teach-repeated", hidden: true });
  const repeating = el(
    "section",
    { class: "teach-repeat", hidden: true, "aria-label": "Repeat this task" },
    el("h3", null, "Repeat it by itself"),
    el("div", { class: "teach-repeat-row" }, rhythm, weekDay, el("span", { class: "hint" }, "at"), time, repeat),
    repeated,
  );

  const dialog = el(
    "dialog",
    { class: "modal teach", "aria-label": "Teach a task" },
    el(
      "div",
      { class: "modal-body" },
      el("header", { class: "teach-head" }, el("h2", null, "Teach a task"), close),
      el(
        "p",
        { class: "teach-hint" },
        "Show me the task once in Dot's browser: open the page you start from, then do it. Stop when it is done, and I ",
        "will write it down as a skill for later tasks.",
      ),
      live,
      steps,
      el("label", { class: "teach-label" }, "What is it for?", words),
      draft,
      repeating,
      el("div", { class: "teach-actions" }, start, stop, make, go, keep),
    ),
  );

  // Something the panel needs stopped working: said once, so that a poll every few hundred milliseconds does not fill
  // the page with the same message, and said again after it works once more.
  function complain(what, error) {
    if (complained) {
      return;
    }
    complained = true;
    toast("error", `${what}: ${error.message}`);
  }

  function paint() {
    start.hidden = recording || steps.children.length > 0 || skill !== null;
    stop.hidden = !recording;
    make.hidden = recording || steps.children.length === 0 || skill !== null;
    keep.hidden = skill === null || saved;
    go.hidden = !saved;
    repeating.hidden = !saved;
    weekDay.hidden = rhythm.value !== "week";
    start.disabled = recording;
  }

  function listed(lines) {
    steps.replaceChildren(...lines.map((line) => el("li", null, line)));
  }

  function stopView() {
    view?.abort();
    view = null;
    if (shownFrame !== null) {
      URL.revokeObjectURL(shownFrame);
      shownFrame = null;
    }
  }

  function watch() {
    stopView();
    view = new AbortController();
    watchInView({
      path: `/teach/${encodeURIComponent(threadId)}/screencast`,
      signal: view.signal,
      onMessage: (message) => {
        if (message.event !== "frame") {
          return;
        }
        // The frame's address is the page's, not an image: what is painted is its JPEG beside it.
        const next = frameUrl(message.data.jpeg_b64);
        if (shownFrame !== null) {
          URL.revokeObjectURL(shownFrame);
        }
        shownFrame = next;
        image.src = next;
        image.hidden = false;
      },
    }).catch((error) => complain("The view of the tab stopped", error));
  }

  async function read() {
    const state = await api.get(`/teach/${encodeURIComponent(threadId)}`);
    listed(state.recording ? state.steps : state.stopped);
    if (state.address !== "") {
      address.textContent = state.address;
    }
    complained = false;
    if (!state.recording && recording) {
      // The tab was closed, or the daemon ended the recording: its steps are gone with it, and the panel says so.
      recording = false;
      stopPolling();
      stopView();
      image.hidden = true;
      address.textContent = "The recording ended, and the steps it had are gone. Start again to show the task.";
      paint();
    }
  }

  function startPolling() {
    if (poll === null) {
      poll = setInterval(() => {
        read().catch((error) => complain("The steps stopped coming", error));
      }, POLL_MS);
    }
  }

  function stopPolling() {
    if (poll !== null) {
      clearInterval(poll);
      poll = null;
    }
  }

  async function begin() {
    start.disabled = true;
    try {
      const state = await api.post("/teach", { thread_id: threadId });
      recording = true;
      address.textContent = state.address || "The tab comes to the front: work in it.";
      listed([]);
      paint();
      watch();
      startPolling();
    } catch (error) {
      toast("error", `The recording did not start: ${error.message}`);
    } finally {
      start.disabled = false;
    }
  }

  async function end() {
    stop.disabled = true;
    try {
      const state = await api.post(`/teach/${encodeURIComponent(threadId)}/stop`, { thread_id: threadId });
      listed(state.steps);
      recording = false;
      stopPolling();
      stopView();
      image.hidden = true;
      paint();
    } catch (error) {
      toast("error", `The recording did not stop: ${error.message}`);
    } finally {
      stop.disabled = false;
    }
  }

  async function write() {
    make.disabled = true;
    try {
      skill = await api.post(`/teach/${encodeURIComponent(threadId)}/draft`, {
        thread_id: threadId,
        words: words.value.trim(),
      });
      draft.replaceChildren(
        el("h3", null, skill.description),
        el("p", { class: "teach-when" }, skill.when),
        el("ol", { class: "teach-steps" }, ...skill.steps.map((step) => el("li", null, step))),
        skill.parameters.length === 0
          ? el("p", { class: "teach-parameters" }, "No parameters: it does the same thing every time.")
          : el("p", { class: "teach-parameters" }, `Parameters: ${skill.parameters.join("; ")}`),
      );
      draft.hidden = false;
      paint();
    } catch (error) {
      toast("error", `The skill was not written: ${error.message}`);
    } finally {
      make.disabled = false;
    }
  }

  async function save() {
    keep.disabled = true;
    try {
      const written = await api.post(`/teach/${encodeURIComponent(threadId)}/save`, { thread_id: threadId, skill });
      saved = written.name;
      toast("info", `Saved “${saved}”: a later task will be offered it like a learned skill.`);
      onSaved?.(saved);
      paint();
    } catch (error) {
      toast("error", `The skill was not saved: ${error.message}`);
    } finally {
      keep.disabled = false;
    }
  }

  // "Run it now" starts the run the skill is for, in this chat, with the skill picked for it.
  async function runIt() {
    go.disabled = true;
    try {
      await api.post("/runs", { thread_id: threadId, goal: skill.description, skills: [saved] });
      finish();
    } catch (error) {
      toast("error", `The task did not start: ${error.message}`);
    } finally {
      go.disabled = false;
    }
  }

  // "Repeat it" makes a schedule of the task in this chat (item 666): the line under it then says when, and leads to it.
  async function repeatIt() {
    repeat.disabled = true;
    try {
      const cron = repeatCron(rhythm.value, weekDay.value, time.value || "09:00");
      const made = await api.post(`/teach/${encodeURIComponent(threadId)}/repeat`, { name: saved, cron });
      const when = loopRhythm(made.cron) ?? "on its schedule";
      repeated.replaceChildren(
        icon("calendar"),
        el("span", null, `It repeats ${when}, in this chat. `),
        el("a", { href: `#/schedules?schedule=${encodeURIComponent(made.id)}`, onclick: () => finish() }, "See it in Schedules"),
      );
      repeated.hidden = false;
    } catch (error) {
      toast("error", `It does not repeat: ${error.message}`);
    } finally {
      repeat.disabled = false;
    }
  }

  function finish() {
    stopPolling();
    stopView();
    dialog.close();
    dialog.remove();
  }

  async function discard() {
    try {
      await api.del(`/teach/${encodeURIComponent(threadId)}`);
    } catch (error) {
      // Nothing was recorded, or it had already ended: there is nothing to throw away. Anything else means the daemon
      // did not stop it, and the panel stays: a recording that goes on with its panel closed is one the owner cannot see.
      if (error.status !== 404) {
        toast("error", `The recording did not stop: ${error.message}`);
        return;
      }
    }
    finish();
  }

  start.addEventListener("click", () => begin());
  stop.addEventListener("click", () => end());
  make.addEventListener("click", () => write());
  keep.addEventListener("click", () => save());
  go.addEventListener("click", () => runIt());
  repeat.addEventListener("click", () => repeatIt());
  rhythm.addEventListener("change", () => paint());
  close.addEventListener("click", () => discard());
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    discard();
  });
  words.addEventListener("input", () => paint());

  paint();
  document.body.append(dialog);
  dialog.showModal();
  begin();
  return { element: dialog, dispose: discard };
}
