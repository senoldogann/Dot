// What a chat shows of the outbox (item 1523): under its conversation, each thing the owner did with no link that has not
// gone yet, as a row: a message with its words and "Waiting to send", Retry now and Edit; an answer, a Stop and a change of
// the queue as a line that says they wait, with Retry now. A message that waited more than a day, and one Dot would not take,
// say "Not sent" and why, and offer Send again, Edit and Discard.

import { el } from "./dom.js";
import { t } from "./i18n.js";
import { again, discard, onOutbox, retryNow, takeBack } from "./outbox.js";
import { ofChat } from "./outboxrules.js";

/** The words a kind of entry has for its row when it has no words of the owner's own. */
const KIND_WORDS = {
  answer: "outbox.answerWaiting",
  stop: "outbox.stopWaiting",
  queue: "outbox.queueWaiting",
};

/**
 * The rows of the chat `chat`: `{ element, dispose }`. `onEdit(text)` is told what the owner wrote in a message taken back
 * to be edited, which it puts in the composer.
 * @param {{ chat: string, onEdit: (text: string) => void }} options
 */
export function createOutboxRows({ chat, onEdit }) {
  const element = el("div", { class: "outbox", role: "region", "aria-label": t("outbox.label"), "aria-live": "polite", hidden: true });

  /** @param {string} part @param {string} label @param {() => void} run */
  function button(part, label, run) {
    return el("button", { class: "btn sm ghost outbox-button", type: "button", "data-part": part, onclick: run }, label);
  }

  /** @param {import("./outboxrules.js").Entry} entry */
  function row(entry) {
    const message = entry.kind === "message";
    const kept = entry.state === "waiting" || entry.state === "sending";
    const words = message ? entry.text : t(KIND_WORDS[entry.kind]);
    const state = entry.state === "sending" ? t("outbox.sending") : kept ? t("outbox.waiting") : t("outbox.notSent");
    const why =
      entry.state === "left"
        ? t("outbox.left")
        : entry.state === "refused"
          ? entry.why === undefined || entry.why === ""
            ? t("outbox.refusedNoWhy")
            : t("outbox.refused", { why: entry.why })
          : null;
    const actions = [];
    if (kept) {
      actions.push(button("retry", t("outbox.retry"), () => retryNow()));
    } else if (message) {
      actions.push(button("again", t("outbox.sendAgain"), () => again(entry.id)));
    }
    if (message) {
      actions.push(
        button("edit", t("outbox.edit"), async () => {
          const back = await takeBack(entry.id);
          if (back !== null) {
            onEdit(back.text);
          }
        }),
      );
    }
    if (!kept) {
      actions.push(button("discard", t("outbox.discard"), () => discard(entry.id)));
    }
    return el(
      "div",
      { class: "outbox-row", "data-state": entry.state, "data-kind": entry.kind },
      el("div", { class: "outbox-text" }, words),
      el("div", { class: "outbox-state" }, state),
      why === null ? null : el("div", { class: "outbox-why" }, why),
      el("div", { class: "outbox-actions" }, ...actions),
    );
  }

  const stop = onOutbox((all) => {
    const mine = ofChat(all, chat);
    element.replaceChildren(...mine.map(row));
    element.hidden = mine.length === 0;
  });
  return { element, dispose: stop };
}
