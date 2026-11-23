// How many of a run's subagents it waits for and how many work on in the background (item 1351): the line over the
// composer says the run waits only while it is blocked on one.

// The states of a subagent that works: a run waits on it, or it works on without the run.
const LIVE = new Set(["queued", "running", "ending", "blocked"]);

/**
 * `statuses` are those of all the run's subagents, `released` those of the subagents the run stopped waiting for (the
 * owner wrote to it, or it asked for no wait). Returns how many work (`live`), how many of them the run is blocked on
 * (`waitedFor`) and how many it let go (`letGo`).
 */
export function waitCounts(statuses, released) {
  const live = statuses.filter((each) => LIVE.has(each)).length;
  const letGo = Math.min(released.filter((each) => LIVE.has(each)).length, live);
  return { live, letGo, waitedFor: live - letGo };
}
