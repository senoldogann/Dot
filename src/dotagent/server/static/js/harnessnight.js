// What a night of learning that took no task says about the test tasks (item 762): pure, so it runs under Node.

/**
 * Where a night learned from no task, what it did with the pool of test tasks, in words; null for a night that is not
 * one of those (its record does not name what has headroom and it was not trying the pool).
 *
 * A night that tried the pool names the tasks it tried (`calibrated`); one that found every task known and too few with
 * headroom names none, and must not say that it tried any.
 *
 * @param {string} state The night's state ("calibrating", "keep", ...).
 * @param {{calibrated?: unknown, headroom?: unknown}} facts The night's record.
 * @returns {string | null}
 */
export function poolNightWords(state, facts) {
  if (state !== "calibrating" && facts.headroom === undefined) {
    return null;
  }
  const tried = Array.isArray(facts.calibrated) ? facts.calibrated.length : 0;
  // The state alone says a night was trying the pool, whatever its record names.
  if (tried > 0 || state === "calibrating") {
    return "tried test tasks, learned from no task";
  }
  return "tried no test task, learned from no task";
}
