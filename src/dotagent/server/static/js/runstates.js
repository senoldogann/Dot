// The states in which a run is at work, as the store has them (store/models.py WORKING_STATUSES): a page asks this
// set, not a list of its own, whether a run works. `ending` is among them: its end is decided and not stored yet, so it
// still holds its chat, its agent and its place in the counts.
export const WORKING_STATES = new Set(["queued", "running", "blocked", "ending"]);
