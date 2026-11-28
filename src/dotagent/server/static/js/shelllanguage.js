// The desktop shell's own words (its menu, its tip, the page it shows while the daemon is down, its update dialogs)
// follow the language of the web UI in its window. The language is kept in the window's own storage (i18n.js), which
// the shell cannot read, so the page writes it into the daemon's config (`[desktop_app] language`), which the shell
// reads each second. A page in a browser writes nothing: its language may differ from the shell's window's.

import { api } from "./api.js";
import { uiLanguage } from "./i18n.js";

/** Whether the page runs in the desktop shell's window (boot.js marks it). */
function inShell() {
  return document.documentElement.dataset.shell === "desktop";
}

/**
 * Tell the daemon the language of this page when it runs in the shell and the config holds another: one read as the
 * page opens, and one write only when they differ. A failure is logged: the shell keeps the words it has.
 * @returns {Promise<void>}
 */
export async function tellShellLanguage() {
  if (!inShell()) {
    return;
  }
  const language = uiLanguage();
  try {
    /** @type {{start_at_login: boolean, language: string}} */
    const held = await api.get("/desktop-app");
    if (held.language !== language) {
      await api.put("/desktop-app", { language });
    }
  } catch (error) {
    console.warn("the desktop shell could not be told the interface's language", error);
  }
}
