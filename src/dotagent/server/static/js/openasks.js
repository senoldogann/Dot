// Whether a part of the conversation still waits on the owner (item 1050): the read-only calls of a step run side by
// side, and two of them may each ask; the first answered leaves the run waiting while the other's card is open.

/**
 * Whether any of the open cards belongs to `scope`.
 * @template S
 * @param {Iterable<{ scope: S }>} open the cards still waiting for an answer
 * @param {S} scope a part of the conversation: the run's, or a helper's
 * @returns {boolean}
 */
export function stillAsks(open, scope) {
  for (const entry of open) {
    if (entry.scope === scope) {
      return true;
    }
  }
  return false;
}
