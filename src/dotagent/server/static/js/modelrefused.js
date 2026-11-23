// A message the daemon refused because its model cannot answer (no key for its provider, the program it runs on is not
// installed, the service retired it): the message stays in the box, the error names the model, and a button sends it on
// the default model the error names, when there is one.

import { modelName } from "./dom.js";
import { refusedModel } from "./errors.js";
import { t } from "./i18n.js";
import { attempt, toast, toastError } from "./ui.js";

/**
 * Run `action` as `attempt` does; a message refused for its model says so in a toast whose button runs `resend(fallback)`,
 * the same message sent on the default model. The words stay in the box either way: nothing was started.
 * @param {() => Promise<void>} action
 * @param {(fallback: string) => Promise<void>} resend
 */
export async function attemptWithFallback(action, resend) {
  let refused = null;
  await attempt(async () => {
    try {
      await action();
    } catch (error) {
      refused = refusedModel(error);
      if (refused === null) {
        throw error;
      }
      const { fallback } = refused;
      const words = /** @type {{ message: string }} */ (error).message;
      if (fallback === null) {
        toastError(error);
      } else {
        toast("error", words, { label: t("modelRefused.sendOn", { model: modelName(fallback) }), run: () => void resend(fallback) });
      }
    }
  });
}
