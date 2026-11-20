// What the conversation leaves out until the reader asks for the internal record. This is how the page draws a
// run. It does not change the events, and it does not change what a model is given.

/** The line a reader sees where the chat was shortened to fit. The sizes and the summary stay in the details. */
export function compactionNotice() {
  return "Earlier messages were condensed";
}

/**
 * Whether `event` is drawn in the conversation. With `details` the internal record is drawn too: a compaction, a
 * system message, a hidden wake, and a tool call kept only as plumbing.
 * @param {{ kind?: string, role?: string, hidden?: boolean, plumbing?: boolean }} event
 * @param {{ details?: boolean }} [options]
 */
export function visibleInConversation(event, options = {}) {
  if (options.details === true) {
    return true;
  }
  if (event.role === "system") {
    return false;
  }
  if (event.kind === "wake" && event.hidden === true) {
    return false;
  }
  if (event.kind === "hidden_wake" || event.kind === "compacted" || event.kind === "system" || event.kind === "summary") {
    return false;
  }
  if (event.kind === "tool_call" && event.plumbing === true) {
    return false;
  }
  return true;
}
