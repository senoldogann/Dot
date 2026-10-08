// The menu that picks the reasoning level of a model. The System page and the composer both build it, and both read the same
// browser preference (see efforts.js), so a level chosen in one place is the level shown in the other.

import { el } from "./dom.js";
import { forgetLevel, levelLabel, offersChoice, savedLevel, saveLevel } from "./efforts.js";

function defaultText(model) {
  return model.default_effort === null ? "Default" : `Default (${levelLabel(model.default_effort)})`;
}

/**
 * A menu of the levels `model` offers plus "Default", which follows the daemon's own choice. Choosing a level keeps it in this
 * browser; choosing "Default" forgets it. The model has to offer a choice (see `offersChoice`).
 */
export function reasoningSelect(model, label) {
  if (!offersChoice(model)) {
    throw new Error(`${model.ref} has no reasoning levels to choose from`);
  }
  const select = el(
    "select",
    { class: "select sm", "aria-label": label },
    el("option", { value: "" }, defaultText(model)),
    model.efforts.map((level) => el("option", { value: level }, levelLabel(level))),
  );
  select.value = savedLevel(model) ?? "";
  select.addEventListener("change", () => {
    if (select.value === "") {
      forgetLevel(model);
    } else {
      saveLevel(model, select.value);
    }
  });
  return select;
}
