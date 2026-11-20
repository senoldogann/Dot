// What the conversation leaves out until the reader asks for the internal record. This is how the page draws a
// run. It does not change the events, and it does not change what a model is given.

/**
 * Whether `event` is drawn in the conversation. A compaction is the internal record: the conversation shows one short
 * line for it, and its sizes wait for Show internal events. With `details` it is drawn too.
 * @param {{ kind?: string }} event
 * @param {{ details?: boolean }} [options]
 */
export function visibleInConversation(event, options = {}) {
  return options.details === true || event.kind !== "compacted";
}
