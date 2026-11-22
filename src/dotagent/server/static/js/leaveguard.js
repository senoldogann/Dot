// The router's "may the page be left?" (item 1337): a page that holds what the owner typed registers a hook, and every
// change of the address that would swap the page asks it first, whoever made the change (a link, the bar's Back, the
// settings' search, a shortcut, the browser's own Back). `hashchange` comes after the address has changed, so the change
// is put back at once and stays undone while the question is open: the owner's "leave" makes it again and opens the page,
// a "keep" has nothing left to do.
//
// How it is put back depends on what the change did to the history (told by the entries' index, the Navigation API's):
// - an entry was added or the history was walked (a link, `location.hash = ...`, the bar's Back, the browser's Back):
//   the walk is undone with `history.go(-delta)`, so that every entry stays as it was, and made again with
//   `history.go(delta)` on "leave";
// - the entry was replaced (a phone's `location.replace`), or the browser tells no index: the address of the page is
//   written over the entry with `replaceState` (no new entry, no event). Without an index a held Back leaves the page's
//   address in the entry it walked to, so that the next Back goes one entry further than it would have.
// The events of the walks the guard makes itself are told by a count (`undoing`) and the one that goes on by the address
// it was allowed (`allowed`), so that they neither ask again nor open the page a second time. A walk made again waits for
// the event of the one that undid it (a browser drops a walk that a second one overtakes). A hook that answers at once
// with a boolean (a form nobody changed) puts nothing back and brings nothing back: the change goes through as it came;
// a change is put back only for a hook that answers later, with a promise (a question on screen).

/**
 * @typedef {{ hash: string, state: unknown, index: number | null }} Place
 * @typedef {() => boolean | Promise<boolean>} BeforeLeave a boolean at once, or a promise of one while a question is asked
 */

/**
 * @param {{
 *   now: () => Place,
 *   restore: (place: Place) => void,
 *   go: (delta: number) => void,
 *   proceed: () => void,
 *   kept?: () => void,
 * }} on `now` reads the address in the bar and the entry's index, `restore` writes `place` over the current history entry
 *   (no new entry, no event), `go` walks the history by `delta` entries, `proceed` opens the page the address names and
 *   `kept` is told when the owner stayed.
 */
export function createLeaveGuard({ now, restore, go, proceed, kept = () => undefined }) {
  /** @type {Set<BeforeLeave>} */
  const hooks = new Set();
  /** @type {Place | null} */
  let shown = null;
  let asking = false;
  let stopped = false;
  // The events of the walks that undo a change that are still to come.
  let undoing = 0;
  // The address a change was allowed to, whose event is to open the page without a question.
  /** @type {string | null} */
  let allowed = null;
  // The walk to make again once the event of the one that undid the change has come.
  /** @type {number | null} */
  let redo = null;

  /** How many entries `here` is from the page on screen, or 0 when the entry was replaced or no index is told. */
  function distance(here) {
    return shown !== null && shown.index !== null && here.index !== null ? here.index - shown.index : 0;
  }

  /** Put the change back: the walk undone, or the address written over the entry. */
  function putBack(here) {
    const delta = distance(here);
    if (delta !== 0) {
      undoing += 1;
      go(-delta);
    } else if (shown !== null) {
      restore(shown);
    }
    return delta;
  }

  return {
    /** The page on screen stands at `place`: where a held change is put back to. */
    shownAt(place) {
      shown = place;
    },

    /** Ask `hook` before the page is left; false keeps the page. Returns the function that takes the hook away. */
    onBeforeLeave(hook) {
      hooks.add(hook);
      return () => {
        hooks.delete(hook);
      };
    },

    /** The hooks of the page that was left go with it (the page's own disposer has run by then). */
    clear() {
      hooks.clear();
    },

    /** The address changed: open the page it names, unless a hook of the page on screen keeps it. */
    async arrived() {
      if (stopped) {
        return;
      }
      const here = now();
      if (undoing > 0 && shown !== null && here.hash === shown.hash) {
        // The event of a walk that put a held change back.
        undoing -= 1;
        if (undoing === 0 && redo !== null) {
          const delta = redo;
          redo = null;
          go(delta);
        }
        return;
      }
      if (allowed !== null && here.hash === allowed) {
        allowed = null;
        proceed();
        return;
      }
      if (asking) {
        // A change made while the question is open is answered with the question: the page and its address stay.
        if (shown !== null && here.hash !== shown.hash) {
          putBack(here);
        }
        return;
      }
      if (shown === null || hooks.size === 0 || here.hash === shown.hash) {
        proceed();
        return;
      }
      asking = true;
      let delta = 0;
      let heldBack = false;
      const holdBack = () => {
        if (!heldBack) {
          heldBack = true;
          delta = putBack(here);
        }
      };
      let leave = true;
      try {
        for (const hook of [...hooks]) {
          const answer = hook();
          if (typeof answer !== "boolean") {
            holdBack();
          }
          if (!(await answer)) {
            holdBack();
            leave = false;
            break;
          }
        }
      } catch (error) {
        // A hook that fails never traps the owner on the page.
        console.error("a page's question before leaving failed", error);
      } finally {
        asking = false;
      }
      if (stopped) {
        return;
      }
      if (!leave) {
        kept();
      } else if (!heldBack) {
        proceed();
      } else if (delta !== 0) {
        // The walk is made again; its event opens the page.
        allowed = here.hash;
        if (undoing > 0) {
          redo = delta;
        } else {
          go(delta);
        }
      } else {
        restore(here);
        proceed();
      }
    },

    /** The session ended: nothing is asked or opened any more. */
    stop() {
      stopped = true;
      hooks.clear();
    },
  };
}
