// The messages of a conversation that more than one view draws: what the user wrote, and how a run ended with Dot's answer.

import { copyText, dateTimeFull, duration, el, messageTime } from "./dom.js";
import { actorFace } from "./actors.js";
import { sentFiles } from "./attachments.js";
import { faceStateAtEnd } from "./face.js";
import { icon } from "./icons.js";
import { sentPicks } from "./picks.js";
import { prose } from "./prose.js";
import { toast } from "./ui.js";

const OUTCOMES = {
  done: { label: "Finished", empty: "The run finished without a written answer." },
  failed: { label: "Failed", empty: "The run failed without saying why." },
  cancelled: { label: "Cancelled", empty: "The run was cancelled before it wrote an answer." },
};
// How long a copy button shows its check once the text is on the clipboard.
const COPIED_MS = 1500;

/** When a message was sent or an answer came, in sight; `title` says more where the pointer rests. */
function stamp(ts, title) {
  return el("time", { class: "message-time", datetime: new Date(ts * 1000).toISOString(), title }, messageTime(ts));
}

// A copy of `text` as it was written, after T3 Code's (item 205): the icon turns to a check for a moment once the text
// is on the clipboard; a copy that fails says why.
function copyButton(text, label) {
  let restore = null;
  const button = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: label,
      "aria-label": label,
      onclick: () =>
        copyText(text).then(
          () => {
            clearTimeout(restore);
            button.replaceChildren(icon("check"));
            button.setAttribute("aria-label", "Copied");
            restore = setTimeout(() => {
              button.replaceChildren(icon("copy"));
              button.setAttribute("aria-label", label);
            }, COPIED_MS);
          },
          (error) => toast("error", `Could not copy: ${error.message}`),
        ),
    },
    icon("copy"),
  );
  return button;
}

// `editFromHere()` asks whether to rewind the chat to before the message and does what the owner chose (item 282); it
// resolves once that is done or dropped, and the button waits meanwhile.
function editFromHereButton(editFromHere) {
  const button = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Edit from here",
      "aria-label": "Edit from here",
      onclick: () => {
        button.disabled = true;
        editFromHere().finally(() => {
          button.disabled = false;
        });
      },
    },
    icon("undo"),
  );
  return button;
}

/**
 * What the user wrote, as a bubble on the right, the files that came with it (`files`, as the timeline has them), and
 * under them (item 205): when it was sent (`sentAt`), a copy of it as it was written and, unless `editFromHere` is null,
 * Edit from here, which rewinds the chat to before it (item 282, after T3 Code). The foot shows once the turn has its
 * answer: a run at work keeps it back (`.run-working`, `.turn-working`). The text reads as markdown, as an answer does
 * (item 206).
 */
export function userMessage({ text, sentAt, files, editFromHere }) {
  return el(
    "div",
    { class: "user-message" },
    el("div", { class: "bubble user" }, prose(text)),
    files.length > 0 ? sentFiles(files) : null,
    el(
      "div",
      { class: "message-foot" },
      stamp(sentAt, dateTimeFull(sentAt)),
      copyButton(text, "Copy the message"),
      editFromHere === null ? null : editFromHereButton(editFromHere),
    ),
  );
}

/** The files that came with the message `message` (a `userMessage`), once they are known: over its foot. */
export function showMessageFiles(message, files) {
  message.querySelector(".message-foot").before(sentFiles(files));
}

/** What the owner picked for the message `message` in the composer (item 318; `picks`, a `picks` event's data), once
 * it is known: over its foot, after its files. */
export function showMessagePicks(message, picks) {
  message.querySelector(".message-foot").before(sentPicks(picks));
}

/**
 * How a run ended, as the actor's answer: the face of `actorId` (an agent id, a run id, or "dot") on the left, the answer
 * itself or why there is none, then one line with a copy of it, when it ended, the outcome and how long the run took.
 * `startedAt` is null when the start of the run is not known; `href` leads to the run itself (null when the reader is
 * there).
 */
export function answerMessage({ actorId, status, text, endedAt, startedAt, href }) {
  const outcome = OUTCOMES[status] ?? { label: status, empty: "" };
  const written = text.trim() !== "";
  const facts = [stamp(endedAt, dateTimeFull(endedAt)), el("span", null, outcome.label)];
  if (startedAt !== null) {
    facts.push(el("span", { class: "answer-took" }, duration(Math.max(0, endedAt - startedAt))));
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
      written ? copyButton(text, "Copy the answer") : null,
      el("span", { class: "answer-facts meta-facts" }, ...facts),
      href === null ? null : el("a", { class: "small", href }, "Open run"),
    ),
  );
}
