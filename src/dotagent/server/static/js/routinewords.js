// The words of a message a routine sent into a chat (owner 2026-11-27): which runs a routine started, and the title its
// row says. Pure, so that a test reads them without a page.

/** The sources of the runs a routine started, not the owner: a schedule's firing (a hook's too) and a watcher's. */
export const ROUTINE_SOURCES = new Set(["schedule", "watcher"]);
// A routine's title is cut where a phone's line ends; the row cuts it again to its width.
const ROUTINE_TITLE_CHARS = 40;

/** The first sentence of `text`, a one-word label it opens with ("Task:") left out, its first letter capital, cut at a
 * word under ROUTINE_TITLE_CHARS with an ellipsis (the phone's shortTitle). Pure. */
export function shortTitle(text) {
  const flat = text.replace(/^\s*[\p{L}\p{N}_]+:\s+(?=\S)/u, "").split(/\s+/).filter(Boolean).join(" ");
  const end = flat.search(/[.!?:;](\s|$)/);
  const sentence = (end === -1 ? flat : flat.slice(0, end)).replace(/^[\s,\-–—]+|[\s,\-–—]+$/g, "");
  const first = sentence.charAt(0).toLocaleUpperCase() + sentence.slice(1);
  if (first.length <= ROUTINE_TITLE_CHARS) {
    return first;
  }
  const cut = first.slice(0, ROUTINE_TITLE_CHARS - 1);
  const word = cut.lastIndexOf(" ") > 0 ? cut.slice(0, cut.lastIndexOf(" ")) : cut;
  return `${word.replace(/[\s,]+$/, "")}…`;
}

/** The title of a routine's message: the name of the chat's schedule that sends `goal`, else of its one schedule, else
 * the words' first sentence (the phone's routineTitle). `schedules` are the chat's automations
 * (`/threads/{id}/automations`). Pure. */
export function routineTitle(goal, schedules) {
  const records = schedules.map((item) => item.schedule.record);
  const named = records.find((record) => record.goal === goal) ?? (records.length === 1 ? records[0] : undefined);
  return named !== undefined && named.name !== "" ? named.name : shortTitle(goal);
}

