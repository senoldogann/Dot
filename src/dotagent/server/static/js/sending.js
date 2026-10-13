// The keys of the messages a composer sends (item 189). A message keeps its key until it is sent, so that sending it
// again after an answer that was lost gets what its first sending made, instead of a second chat, run or queued
// message; a message that changed (its text, its files, its choices, where it goes) is a new message with a new key.

/**
 * A composer's keys: `of(...parts)` is the key of the message made of `parts` (what it is sent as, where to and its
 * body), `sent()` ends it once it went.
 */
export function messageKeys() {
  let key = null;
  let made = "";
  return {
    of(...parts) {
      const content = JSON.stringify(parts);
      if (key === null || content !== made) {
        key = crypto.randomUUID();
        made = content;
      }
      return key;
    },
    sent() {
      key = null;
    },
  };
}
