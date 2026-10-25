// The recap of a run (item 536): a few short lines on what the run did, what it does now and what waits for you, at
// the head of the side panel's Run tab, written by the fast model from the run's record (`GET /api/runs/{id}/recap`,
// `POST` to write one). A new one is asked for only while the section is in sight and one is due: when it comes into
// sight, and as the run takes steps, at most every LOOK_EVERY_MS. One written elsewhere (another page, the phone)
// comes on the run's stream as a `recap` event. Its button writes one at once.

import { api } from "./api.js";
import { el, hourMinute, modelName } from "./dom.js";
import { icon } from "./icons.js";
import { panelSection } from "./panels.js";

// The least time between two looks at whether a recap is due while the run takes steps.
const LOOK_EVERY_MS = 20_000;
// The events after which the run may have taken a step, so a recap may be due.
const STEP_KINDS = new Set(["tool_result", "run_finished"]);

/**
 * The Recap section of the run `runId`: `onEvent(record)` takes the run's events from its stream (a `recap` event is
 * painted, a step makes the section look again whether one is due); it stops when `signal` aborts. While the run has no
 * recap of its own, the recap of `earlierRunId`, the chat's turn before it, stands in its place, said to be that turn's
 * (item 650); it is only read, never written.
 */
export function recapSection({ runId, earlierRunId = null, signal }) {
  const text = el("div", { class: "recap-text" });
  const meta = el("div", { class: "small muted recap-meta" });
  const again = el(
    "button",
    { class: "icon-btn", type: "button", title: "Write the recap again", "aria-label": "Write the recap again" },
    icon("refresh"),
  );
  const body = el("div", { class: "recap", "aria-live": "polite" }, text, meta);
  const section = panelSection({ id: "run-recap", title: "Recap", actions: again, body });
  let inSight = false;
  // Whether this run's own recap is painted: the turn before's stands in only until then.
  let shown = false;
  let writing = false;
  // A write that failed: the section asks for none of its own again until its button is pressed or a recap arrives,
  // as each try would ask the models again for as long as the run takes steps.
  let stalled = false;
  let lastLook = 0;
  let timer = 0;

  // The recap of the chat's turn before, read once (undefined until then, null when it has none).
  let earlier;

  /** The earlier turn's recap, read the first time it is wanted. */
  async function earlierRecap() {
    if (earlierRunId === null) {
      return null;
    }
    if (earlier === undefined) {
      try {
        earlier = (await api.get(`/runs/${earlierRunId}/recap`)).recap;
      } catch (error) {
        console.warn("the recap of the chat's turn before could not be read", error);
        earlier = null;
      }
    }
    return earlier;
  }

  /** The recap as the view gives it, or what stands in for one: none yet (the turn before's, when it has one), or
   * recaps turned off. */
  function paint(view) {
    section.element.hidden = !view.enabled;
    if (view.recap === null) {
      text.replaceChildren(el("p", { class: "muted" }, "A recap comes once the task has taken a few steps."));
      meta.textContent = "";
      earlierRecap().then((before) => {
        // Only while this run still has none: its own, once written, has painted over this.
        if (before === null || before === undefined || signal.aborted || shown) {
          return;
        }
        text.replaceChildren(...before.text.split("\n").map((line) => el("p", null, line)));
        meta.textContent = `From the task before · ${modelName(before.model)} · Updated ${hourMinute(before.written_at)}. This task's own comes once it has taken a few steps.`;
      });
      return;
    }
    shown = true;
    text.replaceChildren(...view.recap.text.split("\n").map((line) => el("p", null, line)));
    const behind = view.steps > view.recap.steps ? ` of ${view.steps}` : "";
    meta.textContent = `As of step ${view.recap.steps}${behind} · ${modelName(view.recap.model)} · Updated ${hourMinute(view.recap.written_at)}`;
  }

  function failed(error) {
    meta.textContent = `The recap could not be written: ${error.message}`;
  }

  async function write(force) {
    if (writing) {
      return;
    }
    writing = true;
    again.disabled = true;
    body.setAttribute("aria-busy", "true");
    meta.textContent = "Writing a recap…";
    try {
      paint(await api.post(`/runs/${runId}/recap`, { force }));
      stalled = false;
    } catch (error) {
      failed(error);
      stalled = true;
    } finally {
      writing = false;
      again.disabled = false;
      body.removeAttribute("aria-busy");
    }
  }

  /** Read the run's recap, and write one when it is due; only while the section is in sight, and not after a write
   * failed (its failure stays in sight). */
  async function look() {
    if (!inSight || writing || stalled || signal.aborted) {
      return;
    }
    lastLook = Date.now();
    let view;
    try {
      view = await api.get(`/runs/${runId}/recap`);
    } catch (error) {
      failed(error);
      return;
    }
    paint(view);
    if (view.enabled && view.due) {
      await write(false);
    }
  }

  /** Look again soon, but not before LOOK_EVERY_MS after the last look. */
  function lookSoon() {
    if (timer !== 0 || !inSight) {
      return;
    }
    const wait = Math.max(0, lastLook + LOOK_EVERY_MS - Date.now());
    timer = window.setTimeout(() => {
      timer = 0;
      look();
    }, wait);
  }

  again.addEventListener("click", () => write(true));
  // The section is in sight while its tab is shown in a panel that is shown, beside the conversation, in the drawer or
  // in a phone's sheet: hidden, it has no box to intersect.
  const sight = new IntersectionObserver((entries) => {
    const now = entries.some((entry) => entry.isIntersecting);
    if (now && !inSight) {
      inSight = true;
      look();
    }
    inSight = now;
  });
  sight.observe(section.element);
  signal.addEventListener("abort", () => {
    sight.disconnect();
    window.clearTimeout(timer);
  });

  return {
    element: section.element,
    /** An event of the chat's runs, from the stream. */
    onEvent(record) {
      if (record.run_id !== runId) {
        return;
      }
      if (record.kind === "recap") {
        stalled = false;
        look();
      } else if (STEP_KINDS.has(record.kind)) {
        lookSoon();
      }
    },
  };
}
