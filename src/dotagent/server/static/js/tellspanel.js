// What you do with Dot's tells, on Scheduled tasks (item 1743), under Dot's own initiative: for each kind of tell (the
// morning brief, the evening check-in, the watchers, what Dot does on its own, the ends of tasks and the other news of
// Dot's), how many went out in the last 30 days and how many you opened or acted on, dismissed or let be (`GET
// /api/tells`). It only shows: nothing Dot tells is changed by it. A watcher's own line is in its row (watcherspanel.js).

import { api } from "./api.js";
import { el } from "./dom.js";
import { inlineError } from "./panels.js";
import { onLifecycle } from "./state.js";
import { attempt } from "./ui.js";
import { tellLines, windowWords } from "./tellwords.js";
import { t } from "./i18n.js";

// A tell goes out and its half hour passes while the page is open: read again now and then, and when Dot tells.
const REFRESH_MS = 60_000;
const RELOAD_KINDS = new Set(["owner_told"]);

export function mountTells() {
  let disposed = false;
  let ticket = 0;
  const holder = el("div", { class: "watcher-list" }, el("div", { class: "hint" }, t("tellspanel.reading")));
  const window = el("span", { class: "hint" });
  const element = el(
    "section",
    { class: "memory-panel notes-panel watcher-panel tells-panel", dataset: { tells: "" } },
    el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, t("tellspanel.whatYouDoWithIts")), window),
    el(
      "p",
      { class: "hint watcher-lead" },
      t("tellspanel.howManyOfWhatDot"),
    ),
    holder,
  );

  async function reload() {
    const mine = ++ticket;
    try {
      const report = await api.get("/tells");
      if (disposed || mine !== ticket) {
        return;
      }
      window.textContent = windowWords(report.days);
      const lines = tellLines(report);
      holder.replaceChildren(
        lines.length === 0
          ? el("div", { class: "hint" }, t("tellspanel.nothingToldYet"))
          : el(
              "div",
              null,
              lines.map((line) =>
                el(
                  "div",
                  { class: "list-row", dataset: { tellKind: line.kind } },
                  el("div", { class: "row-main" }, el("div", { class: "row" }, el("span", { class: "row-title" }, line.label)), el("div", { class: "row-meta" }, line.text)),
                ),
              ),
            ),
      );
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      holder.replaceChildren(el("div", { class: "card" }, inlineError(error, () => attempt(reload))));
    }
  }

  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      attempt(reload);
    }
  });
  const timer = setInterval(() => {
    if (!disposed) {
      attempt(reload);
    }
  }, REFRESH_MS);
  attempt(reload);

  return {
    element,
    dispose() {
      disposed = true;
      clearInterval(timer);
      stopLifecycle();
    },
  };
}
