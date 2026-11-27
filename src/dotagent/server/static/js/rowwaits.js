// What a chat's row says it waits on that is not the owner (item 2010, as DotNext's rows say it, item 1920): pure, so
// that it runs under Node without a page; sidebar.js draws it.

import { t, tCount } from "./i18n.js";

/** The statuses of a run at work, as the sidebar reads them. */
const WORKING = new Set(["queued", "running", "ending"]);
/** How much of what works in the background a row says: the line is cut by the sheet, its name for a screen reader here. */
const WHAT_CHARS = 80;

/**
 * What a chat waits on, in words for its last line, or null: while its run waits on its helpers "Waiting on 2 helpers…",
 * which the row draws shimmering as the work does (`live`); while the chat rests and something still works for it (a
 * helper let go, a command in the background, its loop: `background`, from `GET /threads/view`) "Waiting · <the first
 * of them>", quiet. A daemon from before sends neither.
 * @param {{ latest_status: string, helpers?: number, background?: Array<{ title: string }> }} chat
 * @returns {{ words: string, live: boolean } | null}
 */
export function waitsOn(chat) {
  const helpers = chat.helpers ?? 0;
  if (WORKING.has(chat.latest_status) && helpers > 0) {
    return { words: tCount(helpers, "side.waitingOnHelper", "side.waitingOnHelpers"), live: true };
  }
  const first = (chat.background ?? [])[0];
  if (!WORKING.has(chat.latest_status) && first !== undefined) {
    const what = first.title.length > WHAT_CHARS ? `${first.title.slice(0, WHAT_CHARS - 1)}…` : first.title;
    return { words: t("side.waitingOn", { what }), live: false };
  }
  return null;
}
