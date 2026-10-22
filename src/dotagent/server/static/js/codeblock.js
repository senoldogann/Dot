// A block of code in a box of its own, with its head (item 537): what it is (the language it was written for, or a
// command, a result) at the left of the head, and at its right a switch that wraps its long lines and a copy of it. It
// is the block of an answer's code, of a message's, and of what a tool step shows (its command, its result, its error).

import { copyButton } from "./copybutton.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";

/** Whether long lines wrap in a block of code that was not switched: Settings › Appearance (item 127) says so. */
function wrapsByDefault() {
  return document.documentElement.hasAttribute("data-wrap-code");
}

/**
 * The block for `text`: `label` stands in its head, `language` (when it is code of one) is kept for a copy of the
 * answer, `copyText` is what its copy puts on the clipboard when that is more than it shows (a result that is cut), and
 * `tone` "bad" paints it as a failure, and `wrapped` says whether its lines wrap from the start (null: as Settings ›
 * Appearance says). Its `pre` is `block.querySelector("pre")`.
 * @param {{ text: string, label: string, language?: string, copyText?: string, tone?: "bad" | null, wrapped?: boolean | null, className?: string }} options
 */
export function codeBlock({ text, label, language = "", copyText = text, tone = null, wrapped = null, className = "" }) {
  const wrap = el(
    "button",
    {
      class: "btn ghost sm icon-only code-wrap",
      type: "button",
      title: "Wrap long lines",
      "aria-label": "Wrap long lines",
      "aria-pressed": String(wrapped ?? wrapsByDefault()),
    },
    icon("wrapText"),
  );
  const block = el(
    "div",
    {
      class: ["code-block", tone === "bad" ? "bad" : "", className].filter((name) => name !== "").join(" "),
      role: "group",
      "aria-label": label,
      dataset: wrapped === null ? { language } : { language, wrap: wrapped ? "on" : "off" },
    },
    el("div", { class: "code-head" }, el("span", { class: "code-label" }, label), el("span", { class: "spacer" }), wrap, copyButton(copyText, "Copy the code")),
    el("pre", null, el("code", null, text)),
  );
  wrap.addEventListener("click", () => {
    const now = wrap.getAttribute("aria-pressed") !== "true";
    wrap.setAttribute("aria-pressed", String(now));
    block.dataset.wrap = now ? "on" : "off";
  });
  return block;
}
