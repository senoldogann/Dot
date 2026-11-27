// The thin line under the bar that says how the link stands (item 1524): what it says, from the facts alone. Pure, so that it
// runs under Node without a page; connectionline.js reads the facts and draws it. The words are the iPhone app's own
// (`ConnectionBanner`): offline, with the copy of the chats it shows and how old it is, or with what waits to be sent;
// reconnecting, when Dot cannot be reached for more than a moment; and the phone's key refused, with the way to pair
// again. A link that is well says nothing: null.

import { t, uiLocale } from "./i18n.js";

const LAST_CONTACT_CLOCK = new Intl.DateTimeFormat(uiLocale(), { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * What the page knows of its link.
 * @typedef {object} LineFacts
 * @property {boolean} online whether the phone has a link at all (`navigator.onLine`)
 * @property {number | null} copyFrom when the copy of the chats the page shows was kept (ms), null when it shows what Dot says
 * @property {number} waiting how many things done with no link wait to be sent
 * @property {boolean} reconnecting whether Dot has not been reachable for more than a moment while the phone has a link
 * @property {number | null} lastContact last successful daemon contact (ms), if known
 * @property {boolean} refused whether Dot refused the phone's key
 * @property {number} now the time (ms)
 */

/**
 * What the line says.
 * @typedef {object} Line
 * @property {"refused" | "offline" | "reconnecting"} kind
 * @property {string} text
 * @property {string | null} action the words of what can be pressed, or null: only the refused key has a way on
 */

/**
 * How long ago `then` was, in few words: just now, 3 min ago, 2 h ago, 4 d ago.
 * @param {number} then
 * @param {number} now
 * @param {(key: import("./i18n.js").WordKey, values?: Record<string, string | number>) => string} say
 */
export function ageWords(then, now, say = t) {
  const ago = Math.max(now - then, 0);
  if (ago < MINUTE_MS) {
    return say("line.ageNow");
  }
  if (ago < HOUR_MS) {
    return say("line.ageMinutes", { count: Math.floor(ago / MINUTE_MS) });
  }
  if (ago < DAY_MS) {
    return say("line.ageHours", { count: Math.floor(ago / HOUR_MS) });
  }
  return say("line.ageDays", { count: Math.floor(ago / DAY_MS) });
}

/**
 * The line for `facts`, or null when the link is well. A key that was refused comes first (nothing else can be done
 * until the phone is paired again); then no link, which says what waits to be sent before it says what is shown, since
 * that is what the owner can act on; then a daemon that cannot be reached.
 * @param {LineFacts} facts
 * @param {(key: import("./i18n.js").WordKey, values?: Record<string, string | number>) => string} [say] the words of a language
 * @returns {Line | null}
 */
export function lineOf(facts, say = t) {
  if (facts.refused) {
    return { kind: "refused", text: say("line.refused"), action: say("line.pairAgain") };
  }
  if (!facts.online) {
    if (facts.waiting > 0) {
      return { kind: "offline", text: say("line.offlineWaiting", { count: facts.waiting }), action: null };
    }
    if (facts.copyFrom !== null) {
      return { kind: "offline", text: say("line.offlineCopy", { age: ageWords(facts.copyFrom, facts.now, say) }), action: null };
    }
    return { kind: "offline", text: say("line.offline"), action: null };
  }
  if (facts.reconnecting) {
    return {
      kind: "reconnecting",
      text: facts.lastContact === null ? say("line.macUnreachable") : say("line.macUnreachableSince", { time: LAST_CONTACT_CLOCK.format(facts.lastContact) }),
      action: null,
    };
  }
  return null;
}
