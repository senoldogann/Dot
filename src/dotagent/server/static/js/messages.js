// The messages of a conversation that more than one view draws: what the user wrote, and how a run ended with Dot's answer.

import { copyText, dateTimeFull, duration, el } from "./dom.js";
import { actorFace } from "./actors.js";
import { faceStateAtEnd } from "./face.js";
import { icon } from "./icons.js";
import { prose } from "./prose.js";
import { toast } from "./ui.js";

const OUTCOMES = {
  done: { label: "Finished", empty: "The run finished without a written answer." },
  failed: { label: "Failed", empty: "The run failed without saying why." },
  cancelled: { label: "Cancelled", empty: "The run was cancelled before it wrote an answer." },
};

/** What the user wrote, as a bubble on the right. The text is shown as typed, never read as markdown. */
export function userMessage(text, title) {
  return el("div", { class: "bubble user", title }, el("div", { class: "prose" }, el("p", { class: "para" }, text)));
}

function copyButton(text) {
  return el(
    "button",
    {
      class: "btn ghost sm",
      type: "button",
      title: "Copy the answer",
      "aria-label": "Copy the answer",
      onclick: () =>
        copyText(text).then(
          () => toast("ok", "Answer copied"),
          (error) => toast("error", `Could not copy: ${error.message}`),
        ),
    },
    icon("copy"),
    el("span", { class: "label" }, "Copy"),
  );
}

/**
 * How a run ended, as the actor's answer: the face of `actorId` (an agent id, a run id, or "dot") on the left, the answer
 * itself or why there is none, then one line with the outcome, when it ended and how long the run took. `startedAt` is null
 * when the start of the run is not known; `href` leads to the run itself (null when the reader is there).
 */
export function answerMessage({ actorId, status, text, endedAt, startedAt, href }) {
  const outcome = OUTCOMES[status] ?? { label: status, empty: "" };
  const written = text.trim() !== "";
  const facts = [outcome.label, dateTimeFull(endedAt)];
  if (startedAt !== null) {
    facts.push(duration(Math.max(0, endedAt - startedAt)));
  }
  return el(
    "article",
    { class: `answer ${status}`, "aria-label": "Answer" },
    el("span", { class: "turn-face" }, actorFace(actorId, faceStateAtEnd(status), null)),
    status === "failed" ? el("div", { class: "answer-alert" }, icon("alert"), el("strong", null, "The run failed")) : null,
    written ? prose(text) : el("div", { class: "muted" }, outcome.empty),
    el(
      "div",
      { class: "answer-foot" },
      written ? copyButton(text) : null,
      el("span", { class: "answer-facts meta-facts" }, ...facts.map((fact) => el("span", null, fact))),
      href === null ? null : el("a", { class: "small", href }, "Open run"),
    ),
  );
}
