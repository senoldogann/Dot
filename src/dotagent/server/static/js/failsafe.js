// The emergency stop: cancels every run and blocks desktop control until it is released.

import { api } from "./api.js";
import { el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { refresh } from "./state.js";
import { attempt, confirmDialog, toast } from "./ui.js";

/** Ask for confirmation, then stop everything. */
export async function engageFailsafe() {
  const confirmed = await confirmDialog({
    title: "Emergency stop",
    message: "This cancels every running task and blocks desktop control until you release it.",
    confirmLabel: "Stop everything",
    danger: true,
    focusCancel: false,
  });
  if (!confirmed) {
    return;
  }
  const result = await api.post("/failsafe", null);
  toast("ok", `Emergency stop engaged; ${plural(result.cancelled_runs.length, "run", "runs")} cancelled.`);
  await refresh();
}

/** The emergency stop's button, a red square whose words are in its tooltip, in the sidebar's foot beside Settings (item
 * 120); while the sidebar is closed it is a tap away, behind the sidebar's opener (item 273). */
export function failsafeButton() {
  return el(
    "button",
    {
      class: "btn danger sm failsafe",
      type: "button",
      title: "Emergency stop: cancel every run and block desktop control",
      "aria-label": "Emergency stop",
      onclick: () => attempt(engageFailsafe),
    },
    icon("stop"),
    el("span", { class: "label" }, "Emergency stop"),
  );
}

export async function releaseFailsafe() {
  await api.del("/failsafe");
  toast("ok", "Emergency stop released.");
  await refresh();
}
