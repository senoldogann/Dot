// What a form in a dialog does that needs no page (item 615): whether the owner changed it, which field takes the focus
// when it opens, and what a press of its buttons does (submit, close). `formDialog` (ui.js) draws it; the logic is here so
// that it can be tried without a browser (tests/js/formflow.test.js).

/**
 * The state of a form's controls as one string, to tell whether the owner changed it: the value of a field, whether a box
 * is ticked, and what a switch or a pressed button of a segmented choice says of itself in its attributes.
 * @param {Iterable<{ tagName: string, type?: string, value?: string, checked?: boolean, getAttribute(name: string): string | null }>} controls
 * @returns {string}
 */
export function stateOfControls(controls) {
  const parts = [];
  for (const control of controls) {
    const tag = control.tagName.toLowerCase();
    if (tag === "input" && (control.type === "checkbox" || control.type === "radio")) {
      parts.push(String(control.checked));
    } else if (tag === "input" || tag === "textarea" || tag === "select") {
      parts.push(control.value);
    } else {
      parts.push([control.getAttribute("aria-checked"), control.getAttribute("aria-pressed")]);
    }
  }
  return JSON.stringify(parts);
}

/**
 * The field a dialog puts the caret in as it opens: the first of `controls` that takes text or a choice, is not off or
 * read-only, and `shown` says is on the screen (a field of a folded or hidden part is not).
 * @param {Iterable<any>} controls fields (`type`, `disabled` and `readOnly` are read)
 * @param {(control: any) => boolean} shown
 * @returns {any} the field, or null
 */
export function firstFieldOf(controls, shown) {
  for (const control of controls) {
    if (control.type === "hidden" || control.disabled === true || control.readOnly === true) {
      continue;
    }
    if (shown(control)) {
      return control;
    }
  }
  return null;
}

/**
 * What the buttons of a form in a dialog do, as the owner presses them. The dialog gives the flow what it needs:
 * `changed()` says whether a field was changed, `discard()` asks the owner whether to drop the changes (a promise of
 * yes or no), `busy(which)` shows the button `which` at work, or none (null) once nothing is, `problem(error)` shows the
 * reason a submit failed, or clears it (null), `finish(submitted)` closes the dialog and `lost(error)` tells of a
 * failure that came after the dialog was gone.
 *
 * `submit(action, which)` runs `action`: the buttons are busy while it does, a second press is refused, and a failure is
 * shown with the dialog left open and the fields as they were. An action that comes back `false` has found something
 * missing and left it to the field to say so: the dialog stays, too. Anything else is done, and the dialog closes.
 * `dismiss()` is Cancel, the cross and Escape: refused while a submit is at work, asking first when a field was changed.
 * `abandon()` closes it at once, as when the page it belongs to goes away.
 * @param {{
 *   changed: () => boolean,
 *   discard: () => Promise<boolean>,
 *   busy: (which: string | null) => void,
 *   problem: (error: Error | null) => void,
 *   finish: (submitted: boolean) => void,
 *   lost: (error: Error) => void,
 * }} view
 */
export function createFormFlow(view) {
  let working = false;
  let asking = false;
  let finished = false;

  return {
    /** Whether a submit is at work. */
    working: () => working,
    /**
     * @param {() => Promise<unknown>} action
     * @param {string} which
     * @returns {Promise<boolean>} whether the dialog was closed by it
     */
    async submit(action, which) {
      if (working || asking || finished) {
        return false;
      }
      working = true;
      view.problem(null);
      view.busy(which);
      let outcome;
      try {
        outcome = await action();
      } catch (error) {
        working = false;
        if (finished) {
          view.lost(error);
          return false;
        }
        view.busy(null);
        view.problem(error);
        return false;
      }
      working = false;
      if (finished) {
        return false;
      }
      view.busy(null);
      if (outcome === false) {
        return false;
      }
      finished = true;
      view.finish(true);
      return true;
    },
    /** @returns {Promise<boolean>} whether the dialog was closed by it */
    async dismiss() {
      if (working || asking || finished) {
        return false;
      }
      if (view.changed()) {
        asking = true;
        let drop;
        try {
          drop = await view.discard();
        } finally {
          asking = false;
        }
        if (!drop || working || finished) {
          return false;
        }
      }
      finished = true;
      view.finish(false);
      return true;
    },
    abandon() {
      if (finished) {
        return;
      }
      finished = true;
      view.finish(false);
    },
  };
}
