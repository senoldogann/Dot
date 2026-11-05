// What Dot asks of the owner, at the composer's place (item 973, the owner's ask after another agent app's "User input needed"):
// the cards that wait for an answer (a question, an approval, a step handed over, a question the run left) stand in one
// panel at the foot of the chat rather than in the conversation, on a phone and on a computer alike. While it is open the
// panel takes the composer's place, as the cards are where the answer is given; its fold folds it to its head, and the
// composer is back. Several cards scroll inside the panel, which is never taller than half the window.
//
// A card of the conversation (timeline.js) is held here while it waits: a mark keeps its place in the conversation,
// and once it is answered it goes back to that place, where the conversation keeps what was decided. A question the run
// left (views/run.js) has no place in the conversation: it stands here until it is answered or closed.

import { el } from "./dom.js";
import { icon } from "./icons.js";

/**
 * The panel. `hold(node)` takes a card of the conversation and leaves a mark where it stood; `release(node)` puts it back
 * at its mark; `add(node)` takes a card that has no place in the conversation and `drop(node)` takes it away. The
 * composer gives the panel its place while it stands unfolded with a card in it (app.css, by the panel's own state).
 */
export function createAsksDock() {
  const count = el("span", { class: "asks-count" });
  const fold = el(
    "button",
    { class: "btn ghost sm asks-fold", type: "button", "aria-expanded": "true", "aria-label": "Fold what Dot asked", title: "Fold" },
    el("span", { class: "asks-chev" }, icon("chevron")),
  );
  const list = el("div", { class: "asks-list" });
  const element = el(
    "section",
    { class: "asks", hidden: true, "aria-label": "Dot asked you" },
    el("div", { class: "asks-head" }, el("span", { class: "asks-title" }, "Your answer is needed"), count, fold),
    list,
  );
  /** The marks of the cards held, by card. */
  const marks = new Map();
  let folded = false;

  function paint() {
    const cards = list.children.length;
    element.hidden = cards === 0;
    count.textContent = cards > 1 ? String(cards) : "";
    element.toggleAttribute("data-folded", folded);
    list.hidden = folded;
    fold.setAttribute("aria-expanded", String(!folded));
    fold.setAttribute("aria-label", folded ? "Show what Dot asked" : "Fold what Dot asked");
    fold.title = folded ? "Show" : "Fold";
  }

  fold.addEventListener("click", () => {
    folded = !folded;
    paint();
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
