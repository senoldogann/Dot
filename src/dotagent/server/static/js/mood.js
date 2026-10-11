// What a run's character does (item 103), as pure functions of the run's status: the animation it rests in and the
// expression it wears, and the animation it enters with when the status changes under the reader's eyes. The card
// (charactercard.js) applies them with the engine's timing.

// A run ended done for this long sleeps.
export const SLEEP_AFTER_S = 600;

/** The animation and the expression (null for the look's own) the character rests in for a run's status. */
export function restingMood(status, endedFor) {
  switch (status) {
    case "queued":
    case "running":
      return { animation: "thinking", expression: null };
    case "blocked":
      return { animation: "notify", expression: null };
    case "done":
      return endedFor >= SLEEP_AFTER_S ? { animation: "sleep", expression: null } : { animation: "idle", expression: null };
    case "failed":
      return { animation: "idle", expression: "sad" };
    case "cancelled":
      return { animation: "idle", expression: "unimpressed" };
    default:
      return { animation: "idle", expression: null };
  }
}

/** The animation a change of status enters with, or null to go straight to rest: a run that starts hatches, a run that
 * ends well bursts. */
export function entranceOf(before, after) {
  if (before === null && (after === "queued" || after === "running")) {
    return "egg";
  }
  if (before !== null && before !== "done" && after === "done") {
    return "burst";
  }
  return null;
}
