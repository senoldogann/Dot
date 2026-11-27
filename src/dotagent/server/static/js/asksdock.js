// What Dot asks of the owner, at the composer's place (item 973, the owner's ask after another agent app's "User input needed"):
// the cards that wait for an answer (a question, an approval, a step handed over, a question the run left) stand in one
// panel at the foot of the chat rather than in the conversation, on a phone and on a computer alike. While it is open the
// panel takes the composer's place, as the cards are where the answer is given, but for a run at work, whose composer
// holds its Stop and stays under the panel; its fold folds it to its head, and the composer is back.
//
// One card shows at a time (item 2001, as the phone's dock): "USER INPUT NEEDED", a title, a "1 / 3" count and the
// arrows that page between the cards. The questions are answered together: their cards pick and hold an answer, and the
// panel's one "Submit answers" sends every one of them once each has an answer.
//
// A card of the conversation (timeline.js) is held here while it waits: a mark keeps its place in the conversation,
// and once it is answered it goes back to that place, where the conversation keeps what was decided. A question the run
// left (views/run.js) has no place in the conversation: it stands here until it is answered or closed.

import { questionControls } from "./approvals.js";
import { el } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";

/**
 * The panel. `hold(node)` takes a card of the conversation and leaves a mark where it stood; `release(node)` puts it back
 * at its mark; `add(node)` takes a card that has no place in the conversation and `drop(node)` takes it away. The
 * composer gives the panel its place while it stands unfolded with a card in it (app.css, by the panel's own state).
 */
export function createAsksDock() {
  const title = el("div", { class: "asks-name" });
  const count = el("span", { class: "asks-count" });
  const previous = el("button", { class: "btn ghost sm asks-page", type: "button", "aria-label": t("asks.previous") }, icon("chevronLeft"));
  const next = el("button", { class: "btn ghost sm asks-page", type: "button", "aria-label": t("asks.next") }, icon("chevron"));
  const fold = el(
    "button",
    { class: "btn ghost sm asks-fold", type: "button", "aria-expanded": "true", "aria-label": t("asks.fold"), title: t("asks.fold") },
    el("span", { class: "asks-chev" }, icon("chevron")),
  );
  const list = el("div", { class: "asks-list" });
  const submit = el("button", { class: "btn primary asks-submit", type: "button" });
  const element = el(
    "section",
    { class: "asks", hidden: true, "aria-label": t("asks.label") },
    el(
      "div",
      { class: "asks-head" },
      el("div", { class: "asks-words" }, el("span", { class: "asks-title" }, t("asks.eyebrow")), title),
      el("div", { class: "asks-controls" }, count, previous, next, fold),
    ),
    list,
    submit,
  );
  /** The marks of the cards held, by card. */
  const marks = new Map();
  let folded = false;
  /** The card shown, kept while others come and go. */
  let current = null;

  /** @returns {HTMLElement[]} */
  function cards() {
    return /** @type {HTMLElement[]} */ ([...list.children]);
  }

  /** The question cards that wait for an answer, with how to read and send each. */
  function questions() {
    return cards().flatMap((card) => {
      const controls = questionControls.get(card);
      return controls === undefined || !card.classList.contains("question") ? [] : [controls];
    });
  }

  function paint() {
    const all = cards();
    element.hidden = all.length === 0;
    if (current === null || !all.includes(current)) {
      current = all[0] ?? null;
    }
    const at = current === null ? 0 : all.indexOf(current);
    all.forEach((card, index) => {
      card.hidden = index !== at;
    });
    count.textContent = all.length > 1 ? t("asks.count", { at: at + 1, count: all.length }) : "";
    previous.hidden = all.length < 2 || folded;
    next.hidden = all.length < 2 || folded;
    previous.disabled = at === 0;
    next.disabled = at >= all.length - 1;
    const asking = questions();
    title.textContent = current !== null && current.classList.contains("question") ? t("asks.questionTitle") : "";
    title.hidden = folded || title.textContent === "";
    // One Submit for every question (item 2001), sent once each has an answer.
    submit.hidden = folded || asking.length === 0;
    submit.textContent = asking.length > 1 ? t("asks.submitMany") : t("asks.submitOne");
    submit.disabled = asking.length === 0 || asking.some((question) => question.answer() === "");
    element.toggleAttribute("data-folded", folded);
    element.toggleAttribute("data-asking", asking.length > 0);
    list.hidden = folded;
    fold.setAttribute("aria-expanded", String(!folded));
    fold.setAttribute("aria-label", folded ? t("asks.show") : t("asks.fold"));
    fold.title = folded ? t("asks.show") : t("asks.fold");
  }

  function move(step) {
    const all = cards();
    const at = current === null ? 0 : all.indexOf(current);
    current = all[Math.min(Math.max(0, at + step), all.length - 1)] ?? null;
    paint();
  }

  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  fold.addEventListener("click", () => {
    folded = !folded;
    paint();
  });
  list.addEventListener("answerchange", () => paint());
  submit.addEventListener("click", () => {
    for (const question of questions()) {
      question.send();
    }
  });

  /** A new card unfolds the panel: it is something new to answer. */
  function arrived(node) {
    list.append(node);
    folded = false;
    paint();
  }

  return {
    element,
    /** @param {HTMLElement} node */
    hold(node) {
      const mark = document.createComment("a card waiting for an answer");
      node.replaceWith(mark);
      marks.set(node, mark);
      arrived(node);
    },
    /** @param {HTMLElement} node */
    release(node) {
      const mark = marks.get(node);
      marks.delete(node);
      if (mark === undefined) {
        return;
      }
      node.hidden = false;
      if (mark.isConnected) {
        mark.replaceWith(node);
      } else {
        node.remove();
      }
      paint();
    },
    /** @param {HTMLElement} node */
    add(node) {
      arrived(node);
    },
    /** @param {HTMLElement} node */
    drop(node) {
      node.remove();
      paint();
    },
  };
}
