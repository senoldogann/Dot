// A button that puts a text on the clipboard (item 205): the icon turns to a check for a moment once the text is
// there, and a copy that fails says why. Messages, answers, tables and the file panel share it (item 403).

import { copyText, el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

// How long a copy button shows its check once the text is on the clipboard.
const COPIED_MS = 1500;

/** The button; `text` is the text to copy, or a function that makes it when the button is pressed. */
export function copyButton(text, label) {
  let restore = null;
  const button = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: label,
      "aria-label": label,
      onclick: () =>
        copyText(typeof text === "function" ? text() : text).then(
          () => {
            clearTimeout(restore);
            button.replaceChildren(icon("check"));
            button.setAttribute("aria-label", "Copied");
            restore = setTimeout(() => {
              button.replaceChildren(icon("copy"));
              button.setAttribute("aria-label", label);
            }, COPIED_MS);
          },
          (error) => toast("error", `Could not copy: ${error.message}`),
        ),
    },
    icon("copy"),
  );
  return button;
}
