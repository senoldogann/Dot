// The line under the bar (item 1524): one thin line that says how the link stands, in the words of linewords.js, in the
// bar itself so that it takes its own room and never covers what is read. It has no height while nothing is said, and no
// clock runs while it is not shown: the one that keeps "3 min ago" true starts when a copy is shown and ends with it.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { linkFacts, onLinkFacts, phoneScreen } from "./linkfacts.js";
import { lineOf } from "./linewords.js";
import { breakToldAt, getSnapshot, onChange } from "./state.js";

// How often "3 min ago" is made true again while a copy is shown.
const AGE_EVERY_MS = 30_000;
const ICONS = { offline: "cloud", reconnecting: "refresh", refused: "alert" };

/**
 * The line: `{ element, dispose }`. `onPair()` is what its button does when the phone's key was refused (the sign-in's own
 * code field takes the focus).
 * @param {{ onPair?: () => void }} [options]
 */
export function createConnectionLine(options = {}) {
  if (!phoneScreen()) {
    // A Mac's page talks to a Dot on the same Mac, which a network that is down does not take away: a line that said
    // "Offline" there would be wrong, and the bar's pill and the chat's note say what they always said.
    return { element: el("div", { class: "link-line", hidden: true }), dispose() {} };
  }
  const words = el("span", { class: "link-line-text" });
  const mark = el("span", { class: "link-line-mark", "aria-hidden": "true" });
  const action = el("button", { class: "btn sm link-line-action", type: "button", hidden: true });
  action.addEventListener("click", () => options.onPair?.());
  const element = el("div", { class: "link-line", role: "status", "aria-live": "polite", hidden: true }, mark, words, action);
  // When the stream first stood not live, for the moment a break is told after (`breakToldAt`); null while it is live.
  let downSince = null;
  let timer = null;
  let ticker = null;

  function reconnecting(now) {
    return downSince !== null && now >= breakToldAt(downSince);
  }

  function paint() {
    clearTimeout(timer);
    clearInterval(ticker);
    ticker = null;
    const facts = linkFacts();
    const now = Date.now();
    const line = lineOf({
      online: navigator.onLine,
      copyFrom: facts.copyFrom,
      waiting: facts.waiting,
      reconnecting: navigator.onLine && reconnecting(now),
      refused: facts.refused,
      now,
    });
    element.hidden = line === null;
    if (line === null) {
      // A break not told yet is told when its quiet time is over.
      if (navigator.onLine && downSince !== null) {
        timer = setTimeout(paint, Math.max(breakToldAt(downSince) - now, 0) + 50);
      }
      return;
    }
    element.dataset.kind = line.kind;
    words.textContent = line.text;
    mark.replaceChildren(icon(ICONS[line.kind]));
    action.hidden = line.action === null;
    action.textContent = line.action ?? "";
    if (facts.copyFrom !== null && line.kind === "offline") {
      ticker = setInterval(paint, AGE_EVERY_MS);
    }
  }

  const stopFacts = onLinkFacts(paint);
  const stopState = onChange((snapshot) => {
    const down = snapshot.stream !== "live";
    if (down && downSince === null) {
      downSince = Date.now();
    } else if (!down) {
      downSince = null;
    }
    paint();
  });
  window.addEventListener("online", paint);
  window.addEventListener("offline", paint);
  const snapshot = getSnapshot();
  downSince = snapshot.stream === "live" ? null : Date.now();
  paint();

  return {
    element,
    dispose() {
      clearTimeout(timer);
      clearInterval(ticker);
      stopFacts();
      stopState();
      window.removeEventListener("online", paint);
      window.removeEventListener("offline", paint);
    },
  };
}
