// Keyboard shortcuts of the page, and the signal that asks the home page to put the cursor in its goal box.

const composeListeners = new Set();
const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Ask the home page to focus its goal box. Nothing happens while another page is on screen. */
export function requestCompose() {
  for (const listener of composeListeners) {
    listener();
  }
}

/** Run `listener` whenever something asks for a new run. Returns the function that stops it. */
export function onCompose(listener) {
  composeListeners.add(listener);
  return () => composeListeners.delete(listener);
}

function isTyping(target) {
  return target instanceof HTMLElement && (target.isContentEditable || TYPING_TAGS.has(target.tagName));
}

/**
 * Install the shortcuts: ⌘K or Ctrl+K and "/" search the runs, ⇧⌘O or Ctrl+Shift+O starts a new run, ⌥⌘A or
 * Ctrl+Alt+A opens the Appearance settings and ⇧⌥⌘A or Ctrl+Alt+Shift+A moves to the next colour scheme (item 126).
 * The browser keeps ⌘N and ⌘T for itself, which is why a new run is not on those. Returns the function
 * that removes them.
 */
export function installShortcuts({ search, newRun, appearance, nextScheme }) {
  function onKeydown(event) {
    if (event.isComposing) {
      return;
    }
    const command = event.metaKey || event.ctrlKey;
    // The Option key changes the character a key types on a Mac (⌥A is "å"), so the key is read by where it sits.
    if (event.altKey && command && event.code === "KeyA") {
      event.preventDefault();
      if (event.shiftKey) {
        nextScheme();
      } else {
        appearance();
      }
      return;
    }
    if (event.altKey) {
      return;
    }
    const key = event.key.toLowerCase();
    if (command && !event.shiftKey && key === "k") {
      event.preventDefault();
      search();
    } else if (command && event.shiftKey && key === "o") {
      event.preventDefault();
      newRun();
    } else if (!command && key === "/" && !isTyping(event.target)) {
      event.preventDefault();
      search();
    }
  }
  window.addEventListener("keydown", onKeydown);
  return () => window.removeEventListener("keydown", onKeydown);
}
