// Small per-browser preferences (look and layout). They are kept in localStorage; where storage is blocked a preference
// lasts only for the page that set it.

const setHere = new Map();

/** The chat screen the owner was on last, which Dot opens at (`#/runs/<id>`, or `#/runs` for New chat). */
export const LAST_CHAT_KEY = "dot.lastChat";

// The chat screen this page opened at from `LAST_CHAT_KEY` (item 810), for as long as the page lives.
let openedAt = null;

/** The page opened at `path`, the chat screen `LAST_CHAT_KEY` held. */
export function openedAtLastChat(path) {
  openedAt = path;
}

/** Whether the page opened at `path` from `LAST_CHAT_KEY`; it is asked once, and forgotten after. */
export function tookOpening(path) {
  const took = openedAt === path;
  if (took) {
    openedAt = null;
  }
  return took;
}

/**
 * The opening of the chat page at `path` (item 810), taken as the page mounts: `gone()` says whether a read of it that
 * found nothing is the opening's, which is so for the page's reads until one has found the chat (`found()`). A chat
 * deleted after it was read once, then read again (a Reload), is the owner's to see as not found.
 */
export function openingOf(path) {
  let opening = tookOpening(path);
  return {
    gone: () => opening,
    found: () => {
      opening = false;
    },
  };
}

/** The saved value of a preference, or null when none was saved. */
export function readPref(key) {
  try {
    const stored = localStorage.getItem(key);
    if (stored !== null) {
      return stored;
    }
  } catch {
    // Storage is unavailable here: only what this page set is known.
  }
  return setHere.has(key) ? setHere.get(key) : null;
}

/** Save a preference (a string). */
export function writePref(key, value) {
  setHere.set(key, value);
  try {
    localStorage.setItem(key, value);
  } catch {
    // Same as above: the page keeps the value in memory.
  }
}

/** Forget a preference, so that it reads as never saved. */
export function removePref(key) {
  setHere.delete(key);
  try {
    localStorage.removeItem(key);
  } catch {
    // Same as above: only the page's own copy is gone.
  }
}

/** The sections folded under the preference `key` (`{ [section]: true }`), as saved; anything unreadable counts as nothing
 * folded. */
export function readFolds(key) {
  try {
    const folds = JSON.parse(readPref(key) ?? "{}");
    return folds !== null && typeof folds === "object" ? folds : {};
  } catch {
    return {};
  }
}
