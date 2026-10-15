// The emergency stop: cancels every run and blocks desktop control until it is released. Settings › System engages and
// releases it; the banner of an engaged one releases it too.

import { api } from "./api.js";
import { plural } from "./dom.js";
import { refresh } from "./state.js";
import { confirmDialog, toast } from "./ui.js";

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

export async function releaseFailsafe() {
  await api.del("/failsafe");
  toast("ok", "Emergency stop released.");
  await refresh();
}
