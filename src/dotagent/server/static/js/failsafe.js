// The emergency stop: cancels every run and blocks desktop control until it is released. Settings › System engages and
// releases it; the banner of an engaged one releases it too.

import { api } from "./api.js";
import { refresh } from "./state.js";
import { confirmDialog, toast } from "./ui.js";
import { t, tCount } from "./i18n.js";

/** Ask for confirmation, then stop everything. */
export async function engageFailsafe() {
  const confirmed = await confirmDialog({
    title: t("failsafe.emergencyStop"),
    message: t("failsafe.thisCancelsEveryRunningTask"),
    confirmLabel: t("failsafe.stopEverything"),
    danger: true,
    focusCancel: false,
  });
  if (!confirmed) {
    return;
  }
  const result = await api.post("/failsafe", null);
  toast("ok", t("failsafe.emergencyStopEngagedPartStopped", { part: tCount(result.cancelled_runs.length, "failsafe.countTask", "failsafe.countTasks") }));
  await refresh();
}

export async function releaseFailsafe() {
  await api.del("/failsafe");
  toast("ok", t("failsafe.emergencyStopReleased"));
  await refresh();
}
