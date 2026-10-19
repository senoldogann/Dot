// Overlays shared by every page: toasts, the confirmation dialog and the screenshot viewer.

import { el } from "./dom.js";
import { icon } from "./icons.js";

const TOAST_MS = 3500;
const ERROR_TOAST_MS = 9000;

let region = null;
let viewer = null;

function toastRegion() {
  if (region === null) {
    region = el("div", { class: "toast-region", role: "status", "aria-live": "polite" });
    document.body.append(region);
  }
  return region;
}

/** Show a short message; `kind` is "ok" or "error". */
export function toast(kind, text) {
  const node = el("div", { class: `toast ${kind}` }, icon(kind === "error" ? "alert" : "check"), el("span", null, text));
  node.addEventListener("click", () => node.remove());
  toastRegion().append(node);
  setTimeout(() => node.remove(), kind === "error" ? ERROR_TOAST_MS : TOAST_MS);
}

/** Run `action`; when it fails, tell the user why instead of leaving the click without any effect. */
export async function attempt(action) {
  try {
    await action();
  } catch (error) {
    console.error("action failed", error);
    toast("error", error.message);
  }
}

/**
 * Ask for a yes or no; resolves to true when confirmed. `focusCancel` puts the focus on Cancel instead of the confirm
 * button, for the questions a stray Enter must not answer with yes.
 * @param {{ title: string, message: string, confirmLabel: string, danger?: boolean, focusCancel?: boolean }} question
 * @returns {Promise<boolean>}
 */
export function confirmDialog({ title, message, confirmLabel, danger, focusCancel }) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "modal" });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, "Cancel");
    const ok = el(
      "button",
      { class: danger ? "btn danger solid" : "btn primary", type: "button", onclick: () => dialog.close("ok") },
      confirmLabel,
    );
    dialog.append(
      el("div", { class: "modal-body" }, el("h3", null, title), el("p", { class: "muted" }, message)),
      el("div", { class: "modal-actions" }, cancel, ok),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(dialog.returnValue === "ok");
    });
    document.body.append(dialog);
    dialog.showModal();
    (focusCancel ? cancel : ok).focus();
  });
}

// The look of a choice of `choiceDialog`, by its tone.
const CHOICE_TONES = { plain: "btn", primary: "btn primary", danger: "btn danger" };

/**
 * A dialog that offers `choices` (`{ value, label, tone }`, `tone` one of "plain", "primary" and "danger") after Cancel,
 * in their order; resolves with the value of the choice pressed, or null when it is cancelled, Escape too. Cancel takes
 * the focus.
 */
export function choiceDialog({ title, message, choices }) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "modal" });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("") }, "Cancel");
    const buttons = choices.map((choice, index) =>
      el("button", { class: CHOICE_TONES[choice.tone], type: "button", onclick: () => dialog.close(String(index)) }, choice.label),
    );
    dialog.append(
      el("div", { class: "modal-body" }, el("h3", null, title), el("p", { class: "muted" }, message)),
      el("div", { class: "modal-actions" }, cancel, ...buttons),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      const chosen = dialog.returnValue === "" ? undefined : choices[Number(dialog.returnValue)];
      resolve(chosen === undefined ? null : chosen.value);
    });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

/** Show a screenshot at full size until it is clicked or Escape is pressed. */
export function showImage(url, description) {
  if (viewer === null) {
    const image = el("img", { alt: "" });
    viewer = el("dialog", { class: "lightbox" }, image);
    viewer.addEventListener("click", () => viewer.close());
    document.body.append(viewer);
  }
  const image = viewer.querySelector("img");
  image.src = url;
  image.alt = description;
  viewer.showModal();
}
