// Small per-browser preferences (look and layout). They are kept in localStorage; where storage is blocked a preference
// lasts only for the page that set it.

const setHere = new Map();

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

/** Forget a preference. */
export function clearPref(key) {
  setHere.delete(key);
  try {
    localStorage.removeItem(key);
  } catch {
    // Same as above.
  }
}
