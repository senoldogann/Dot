// The messages of a conversation that more than one view draws: what the user wrote, and how a run ended with Dot's answer.

import { dateTimeFull, duration, el, messageTime } from "./dom.js";
import { actorFace } from "./actors.js";
import { sentFiles, sentPictures } from "./attachments.js";
import { copyButton } from "./copybutton.js";
import { serviceFailure } from "./errors.js";
import { faceStateAtEnd } from "./face.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { sentPicks } from "./picks.js";
import { prose } from "./prose.js";
import { readAloudButton } from "./readaloud.js";
import { loopRhythm } from "./rhythm.js";

/** @type {Record<string, { label: import("./i18n.js").WordKey, empty: import("./i18n.js").WordKey }>} */
const OUTCOMES = {
  done: { label: "outcome.done", empty: "outcome.doneEmpty" },
  failed: { label: "outcome.failed", empty: "outcome.failedEmpty" },
  cancelled: { label: "outcome.cancelled", empty: "outcome.cancelledEmpty" },
  // A run that did what it could and stopped for what only the owner gives (item 1061): it is stored failed, and it
  // said what it needs.
  waiting: { label: "outcome.waiting", empty: "outcome.waitingEmpty" },
};

/**
 * How a run ended as the conversation shows it (item 1061): a run stored failed that said what it needs from the owner
 * (`finish` with `success: false` and `needs_from_user`: an approval to send, a sign-in) waits on them, which is not a
 * failure of its work; on the owner's Dot five such answers of six "failed" ones in five days said "The task failed".
 * @param {string} status the run's stored status
 * @param {string} needs what the run said it needs from the owner, "" for nothing
 */
export function outcomeOf(status, needs) {
  return status === "failed" && needs.trim() !== "" ? "waiting" : status;
}

/** What a run said, as it ended, it needs from the owner, under its answer. */
export function needsAside(needs) {
  const head = el("div", { class: "aside-head" }, icon("hand"), el("span", null, t("outcome.needs")));
  return el("div", { class: "needs", "aria-label": t("outcome.needs") }, head, prose(needs));
}

// The line over an answer that did not end done: what went wrong, or what it waits for.
function outcomeAlert(status) {
  if (status === "failed") {
    return el("div", { class: "answer-alert" }, icon("alert"), el("strong", null, t("outcome.failedAlert")));
  }
  if (status === "waiting") {
    return el("div", { class: "answer-alert answer-waiting" }, icon("hand"), el("strong", null, t("outcome.waiting")));
  }
  return null;
}

/** The words of a run its model service stopped (`serviceFailure`): what stopped, that what was done stays, what the
 * owner can do, and the service's own words, a link of theirs among them, under a closed Details. */
function serviceStopped(failure) {
  return [
    el("div", { class: "answer-alert" }, icon("alert"), el("strong", null, t("errors.serviceStopped"))),
    el("div", { class: "prose answer-service" }, el("p", null, failure.next), el("p", { class: "muted" }, t("errors.serviceKept"))),
    el("details", { class: "error-details answer-service-details" }, el("summary", null, t("errors.details")), prose(failure.details)),
  ];
}

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

/** The line a message sent while the chat worked leaves in it when it was a request of its own and started at once
 * in a chat of its own (item 915): what it asked, and the way to that chat. `started` is the `started_beside` event's
 * data. */
export function besideLine(started) {
  return el(
    "a",
    { class: "note schedule-line beside-line", href: `#/runs/${encodeURIComponent(started.run_id)}` },
    icon("branch"),
    el("span", null, el("span", { class: "schedule-made" }, "Started in a chat of its own"), " ", el("strong", null, started.text)),
  );
}

/** The line a note from another chat leaves in this one (item 957): what a run of Dot's at work there told the run at
 * work here, and the way to that chat. The words are Dot's, never yours. `noted` is the `chat_note` event's data. */
export function chatNoteLine(noted) {
  return el(
    "a",
    { class: "note schedule-line chat-note-line", href: `#/runs/${encodeURIComponent(noted.from_run_id)}` },
    icon("message"),
    el("span", null, el("span", { class: "schedule-made" }, "Note from another chat:"), " ", noted.text),
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
  const outcome = OUTCOMES[status] ?? null;
  const written = text.trim() !== "";
  // A run its model service stopped says so in words of its own, not the service's line (item 1136 left its sentence,
  // a billing link and all, as the answer); the line itself stands under Details.
  const failure = status === "failed" ? serviceFailure(text) : null;
  // A run that ended well needs no word for it, as a message that arrived needs none (item 832); a failure and a stop
  // say so.
  const label = outcome === null ? status : t(outcome.label);
  const facts = [stamp(endedAt, dateTimeFull(endedAt)), status === "done" ? null : el("span", null, label)];
  if (startedAt !== null) {
    facts.push(el("span", { class: "answer-took" }, duration(Math.max(0, endedAt - startedAt))));
  }
  return el(
    "article",
    { class: `answer answer-${status}`, "aria-label": t("outcome.answer") },
    el("span", { class: "turn-face" }, actorFace(actorId, faceStateAtEnd(status === "waiting" ? "blocked" : status), null)),
    ...(failure === null
      ? [outcomeAlert(status), written ? prose(text) : el("div", { class: "muted" }, outcome === null ? "" : t(outcome.empty))]
      : serviceStopped(failure)),
    el(
      "div",
      { class: "answer-foot" },
      written ? copyButton(text, t("outcome.copy")) : null,
      written ? readAloudButton(text, readNow && status === "done") : null,
      written && reactions !== null && runId !== null ? reactions.controls(runId, "answer") : null,
      el("span", { class: "answer-facts meta-facts" }, ...facts),
      href === null ? null : el("a", { class: "small", href }, t("outcome.openTask")),
    ),
  );
}
