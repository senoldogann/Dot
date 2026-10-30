// The messages of a conversation that more than one view draws: what the user wrote, and how a run ended with Dot's answer.

import { dateTimeFull, duration, el, messageTime } from "./dom.js";
import { actorFace } from "./actors.js";
import { sentFiles, sentPictures } from "./attachments.js";
import { copyButton } from "./copybutton.js";
import { faceStateAtEnd } from "./face.js";
import { icon } from "./icons.js";
import { sentPicks } from "./picks.js";
import { prose } from "./prose.js";
import { readAloudButton } from "./readaloud.js";
import { loopRhythm } from "./rhythm.js";

const OUTCOMES = {
  done: { label: "Finished", empty: "The task finished without a written answer." },
  failed: { label: "Failed", empty: "The task failed without saying why." },
  cancelled: { label: "Cancelled", empty: "The task was stopped before it wrote an answer." },
};

/** When a message was sent or an answer came, in sight; `title` says more where the pointer rests. */
function stamp(ts, title) {
  return el("time", { class: "message-time", datetime: new Date(ts * 1000).toISOString(), title }, messageTime(ts));
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
 * What the user wrote, as a bubble on the right, the pictures that came with it (`files`, as the timeline has them) as
 * small squares over it, its other files under it, and under them (item 205): when it was sent (`sentAt`), a copy of it
 * as it was written and, unless `editFromHere` is null, Edit from here, which rewinds the chat to before it (item 282).
 * The foot shows once the turn has its answer: a run at work keeps it back (`.run-working`, `.turn-working`). The text
 * reads as markdown, as an answer does (item 206).
 */
export function userMessage({ text, sentAt, files, editFromHere }) {
  return el(
    "div",
    { class: "user-message" },
    sentPictures(files),
    el("div", { class: "bubble user" }, prose(text)),
    sentFiles(files),
    el(
      "div",
      { class: "message-foot" },
      stamp(sentAt, dateTimeFull(sentAt)),
      copyButton(text, "Copy the message"),
      editFromHere === null ? null : editFromHereButton(editFromHere),
    ),
  );
}

/** The line a schedule a run made leaves in the conversation (item 665): the calendar, "Made a schedule", its name and
 * when it runs ("every Monday at 09:00", "once, 25 Oct 08:00"), a link to it on the Schedules page. `made` is the
 * `schedule_made` event's data. */
export function scheduleLine(made) {
  const rhythm = typeof made.cron === "string" ? loopRhythm(made.cron) : null;
  const when = rhythm ?? (typeof made.cron === "string" ? "on a repeating schedule" : String(made.when ?? ""));
  return el(
    "a",
    { class: "note schedule-line", href: `#/schedules?schedule=${encodeURIComponent(made.schedule_id)}` },
    icon("calendar"),
    el("span", null, el("span", { class: "schedule-made" }, "Made a schedule"), " ", el("strong", null, made.name), when === "" ? null : ` · ${when}`),
  );
}

// What each reaction of Dot's on a message of yours says, for its tooltip and a screen reader (item 664).
const DOT_REACTION_WORDS = { "👍": "understood", "👀": "looking into it" };

/** Dot's reaction `reaction` on the message `message` (a `userMessage`, item 664): a small round badge on the corner of
 * its bubble, as a chat shows one. A second one takes the first one's place. Its name says what it means, the same on
 * the run's page and on an earlier turn, whose agent the page may not know by name. */
export function showDotReaction(message, reaction) {
  const words = DOT_REACTION_WORDS[reaction];
  if (words === undefined) {
    return;
  }
  const label = `Reacted ${reaction}: ${words}`;
  const badge = el("span", { class: "dot-reaction", role: "img", "aria-label": label, title: label }, reaction);
  const bubble = message.querySelector(".bubble.user");
  message.querySelector(".dot-reaction")?.remove();
  bubble?.append(badge);
}

/** The files that came with the message `message` (a `userMessage`), once they are known: its pictures over its bubble,
 * the other files over its foot. */
export function showMessageFiles(message, files) {
  message.querySelector(".bubble.user").before(...nodes(sentPictures(files)));
  message.querySelector(".message-foot").before(...nodes(sentFiles(files)));
}

/** A node as a list of nodes to insert: none for null. */
function nodes(node) {
  return node === null ? [] : [node];
}

/** What the owner picked for the message `message` in the composer (item 318; `picks`, a `picks` event's data), once
 * it is known: words at the head of its text, in their colours (item 858); a message whose text opens with no
 * paragraph (a list, a quote, code) has them on a line of their own over it. */
export function showMessagePicks(message, picks) {
  const text = message.querySelector(".bubble.user .prose");
  if (text === null) {
    message.querySelector(".message-foot").before(sentPicks(picks));
    return;
  }
  const opening = text.firstElementChild;
  const words = sentPicks(picks, true);
  if (opening instanceof HTMLParagraphElement) {
    opening.prepend(words, " ");
  } else {
    text.prepend(el("p", null, words));
  }
}

/**
 * How a run ended, as the actor's answer: the face of `actorId` (an agent id, a run id, or "dot") on the left, the answer
 * itself or why there is none, then one line with a copy of it, a speaker that reads it aloud (item 574; where the browser
 * can speak), the button of the reactions with the chips of those that stand (item 586; where `reactions` is given and
 * `runId` is the run the answer is of), when it ended, the outcome and how long the run took. `startedAt` is null when
 * the start of the run is not known; `href` leads to the run itself (null when the reader is there). `readNow` says the
 * answer has just come in, to be read aloud as it appears when Settings › General says so.
 */
export function answerMessage({ actorId, status, text, endedAt, startedAt, href, readNow = false, reactions = null, runId = null }) {
  const outcome = OUTCOMES[status] ?? { label: status, empty: "" };
  const written = text.trim() !== "";
  // A run that ended well needs no word for it, as a message that arrived needs none (item 832); a failure and a stop
  // say so.
  const facts = [stamp(endedAt, dateTimeFull(endedAt)), status === "done" ? null : el("span", null, outcome.label)];
  if (startedAt !== null) {
    facts.push(el("span", { class: "answer-took" }, duration(Math.max(0, endedAt - startedAt))));
  }
  return el(
    "article",
    { class: `answer ${status}`, "aria-label": "Answer" },
    el("span", { class: "turn-face" }, actorFace(actorId, faceStateAtEnd(status), null)),
    status === "failed" ? el("div", { class: "answer-alert" }, icon("alert"), el("strong", null, "The task failed")) : null,
    written ? prose(text) : el("div", { class: "muted" }, outcome.empty),
    el(
      "div",
      { class: "answer-foot" },
      written ? copyButton(text, "Copy the answer") : null,
      written ? readAloudButton(text, readNow && status === "done") : null,
      written && reactions !== null && runId !== null ? reactions.controls(runId, "answer") : null,
      el("span", { class: "answer-facts meta-facts" }, ...facts),
      href === null ? null : el("a", { class: "small", href }, "Open task"),
    ),
  );
}
