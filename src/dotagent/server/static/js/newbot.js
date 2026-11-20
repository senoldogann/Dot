// A new agent from the sidebar's "+" (item 1259): its face and its name, nothing else, and then its conversation, where
// the first message to it goes. What else an agent has (its instructions, its model, its actions) starts at the
// defaults and is set on its page later, with Edit.

import { api } from "./api.js";
import { el } from "./dom.js";
import { fallbackLook, rememberLook } from "./actors.js";
import { t } from "./i18n.js";
import { lookPicker } from "./lookpicker.js";
import { formDialog } from "./ui.js";

/** Show the dialog of a new agent; once it is made, open its page, whose box talks to it. */
export function openNewBot() {
  const look = lookPicker({ look: fallbackLook("new-bot"), uid: "new-bot" });
  const name = el("input", {
    class: "input new-bot-name",
    type: "text",
    maxlength: "60",
    autocomplete: "off",
    placeholder: t("newBot.namePlaceholder"),
    "aria-label": t("newBot.name"),
  });
  // The name stands under the face, over the shapes.
  look.element.firstElementChild?.after(name);
  const form = formDialog({
    title: t("newBot.title"),
    body: el("div", { class: "new-bot" }, look.element),
    submitLabel: t("newBot.create"),
    valid: () => name.value.trim() !== "",
    submitOnEnter: true,
    size: "narrow",
    onSubmit: async () => {
      const saved = await api.post("/agents", {
        name: name.value.trim(),
        role: "",
        instructions: "",
        model: "",
        effort: null,
        approvals: null,
        // Every action, as the editor's All actions says it.
        tools: null,
        look: look.value(),
      });
      rememberLook(saved.id, saved.look);
      location.hash = `#/agents/${saved.id}`;
    },
  });
  // The preview moves until it is let go, as the agent editor's is.
  void form.closed.then(() => look.dispose());
  return form;
}
