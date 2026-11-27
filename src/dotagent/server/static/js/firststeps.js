// The first steps of a Dot that started with an empty store (item 811): on the home, under the composer, a card of the
// six things to set up, each with the place it is done and a mark once it is. It reads `/api/assistant/first-steps`
// once as the home opens, and shows only while Dot is fresh (its first run not two weeks old) and a step is left; Hide
// puts it away for good.

import { api } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { FIRST_STEPS_KEY, HIDDEN, showsFirstSteps, stepsOf } from "./firststepslist.js";
import { readPref, writePref } from "./prefs.js";
import { t } from "./i18n.js";

/** @param {{ id: string, title: string, where: string, href: string | null, done: boolean }} step */
function stepRow(step) {
  const title =
    step.href === null || step.done
      ? el("span", { class: "first-step-title" }, step.title)
      : el("a", { class: "first-step-title", href: step.href }, step.title);
  return el(
    "li",
    { class: "first-step", dataset: { step: step.id, done: String(step.done) } },
    el("span", { class: "first-step-mark", "aria-hidden": "true" }, icon(step.done ? "check" : "circleDashed")),
    el("span", { class: "first-step-words" }, title, el("span", { class: "hint" }, step.where)),
    step.done ? el("span", { class: "visually-hidden" }, t("firststeps.done")) : null,
  );
}

/**
 * The card, which `place(card)` puts on the page once the steps are read and it is to be shown: a home with nothing to set
 * up holds no trace of it. Returns `dispose()`.
 * @param {(card: HTMLElement) => void} place
 */
export function buildFirstSteps(place) {
  const list = el("ol", { class: "first-steps-list" });
  const count = el("span", { class: "hint first-steps-count" });
  const hide = el("button", { class: "btn ghost sm", type: "button", title: t("firststeps.putTheseStepsAwayFor") }, t("firststeps.hide"));
  const element = el(
    "section",
    { class: "card first-steps", "aria-labelledby": "first-steps-title" },
    el("div", { class: "card-head" }, icon("squareCheck"), el("span", { id: "first-steps-title", class: "grow" }, t("firststeps.firstSteps")), count, hide),
    el("div", { class: "card-pad" }, list),
  );
  let disposed = false;
  hide.addEventListener("click", () => {
    writePref(FIRST_STEPS_KEY, HIDDEN);
    element.remove();
  });
  if (readPref(FIRST_STEPS_KEY) !== HIDDEN) {
    api
      .get("/assistant/first-steps")
      .then((view) => {
        if (disposed || !showsFirstSteps(view, readPref(FIRST_STEPS_KEY))) {
          return;
        }
        const { steps, finished } = stepsOf(view);
        list.replaceChildren(...steps.map(stepRow));
        count.textContent = t("firststeps.finishedOfLengthDone", { finished, length: steps.length });
        place(element);
      })
      .catch((error) => {
        console.warn("the first steps are unavailable", error);
      });
  }
  return {
    dispose() {
      disposed = true;
    },
  };
}
