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

// The tools whose open call holds a run until a helper ends: `delegate` (unless it asked for no wait) and the agent tools
// of the coding CLIs. The phone's `helperWaitTools` (DotEngine) is the same list.
const HELPER_WAITS = new Set(["delegate", "message_agent", "task", "agent"]);

/**
 * Whether a run has stopped and only waits for its helpers, from the calls of its newest step that have no result yet
 * (`open`, each `{ tool, args }`): there is one, and every one waits on a helper. While any other call runs, or none is
 * open, the run works: its capsule says "Working" and the helpers keep their own segment (the owner's report of
 * 2026-11-27; the phone's `waitsOnHelpers` is the same rule).
 * @param {{ tool: string, args: Record<string, unknown> }[]} open
 * @returns {boolean}
 */
export function waitsOnHelpers(open) {
  return open.length > 0 && open.every((call) => HELPER_WAITS.has(call.tool.toLowerCase()) && call.args.wait !== false);
}
