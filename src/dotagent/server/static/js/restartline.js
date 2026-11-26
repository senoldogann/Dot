// The line over the composer of a run whose model declined its task (item 1889): the model's no stands, Dot does not
// argue with it nor move to another model on its own, and one action, Restart on…, starts the task again in this chat
// on the model the owner picks from the composer's model menu. The pick is the owner's, and the old run records it so.

import { api } from "./api.js";
import { el, modelName } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { modelPicker } from "./modelpicker.js";
import { anchoredPopover } from "./popover.js";
import { loadModels } from "./state.js";
import { attempt, toast } from "./ui.js";

// The ending the daemon stores for a run whose model declined its task (dotagent.store.endings).
export const MODEL_REFUSED = "model_refused";

/**
 * The models that can take a task's steps, by provider, but the one that declined it.
 * @param {Array<{ provider: string, models: Array<{ ref: string, tools: boolean }> }>} providers
 * @param {string} declined
 */
function restartGroups(providers, declined) {
  return providers
    .map((one) => ({ provider: one.provider, models: one.models.filter((model) => model.tools && model.ref !== declined), why: null }))
    .filter((group) => group.models.length > 0);
}

/**
 * `restartLine({ onStarted })`: `element` is the line, hidden until `paint(run)` is given a run that ended on its
 * model's refusal; a model picked starts the run's task again on it and `onStarted(run)` gets the new run.
 * @param {{ onStarted: (run: { id: string }) => void }} options
 */
export function restartLine({ onStarted }) {
  let runId = null;
  let declined = "";
  let groups = [];
  const words = el("span", { class: "grow" });
  const button = el("button", { class: "btn primary sm", type: "button", title: t("restart.hint") }, icon("refresh"), el("span", { class: "label" }, t("restart.on")));
  const element = el("div", { class: "note resume-line restart-note refused-line", hidden: true }, icon("alert"), words, button);
  const picker = modelPicker({
    onPick: (ref) => {
      menu.close();
      if (ref === "" || runId === null) {
        return;
      }
      const from = runId;
      attempt(async () => {
        const next = await api.post(`/runs/${from}/restart`, { model: ref });
        toast("ok", t("restart.started", { model: modelName(ref) }));
        onStarted(next);
      });
    },
    onMakeDefault: null,
  });
  const menu = anchoredPopover({ anchor: button, frame: element, label: t("restart.on"), build: () => picker.build({ groups, chosen: "", fallback: null, blank: null }) });

  /**
   * Show the line for `run` when its model declined its task, and hide it otherwise.
   * @param {{ id: string, model: string, end_reason: string | null, finished_at: number | null }} run
   */
  function paint(run) {
    const refused = run.finished_at !== null && run.end_reason === MODEL_REFUSED;
    element.hidden = !refused;
    if (!refused) {
      menu.close();
      return;
    }
    runId = run.id;
    declined = run.model;
    words.textContent = t("restart.declined", { model: modelName(declined) });
    loadModels()
      .then((providers) => {
        groups = restartGroups(providers, declined);
        menu.repaint();
      })
      .catch((error) => console.error("the models to restart on could not be read", error));
  }

  return { element, paint, dispose: menu.dispose };
}
