// Overlays shared by every page: toasts, the dialogs (a question, a choice, a form) and the screenshot viewer.

import { el } from "./dom.js";
import { explain } from "./errors.js";
import { createFormFlow, firstFieldOf, stateOfControls } from "./formflow.js";
import { icon } from "./icons.js";

const TOAST_MS = 3500;
const ERROR_TOAST_MS = 9000;
// A toast with something to do stays long enough to do it.
const ACTION_TOAST_MS = 15000;

let region = null;
let viewer = null;
// How many dialogs were made: the number that keeps the ids of one dialog's title and line apart from another's.
let dialogCount = 0;

function toastRegion() {
  if (region === null) {
    region = el("div", { class: "toast-region", role: "status", "aria-live": "polite" });
    document.body.append(region);
  }
  return region;
}

/**
 * A closed "Details" fold holding `text`, the program's own words for a failure (item 625), or null when there are none.
 * The sentence above it is the one a person reads.
 */
export function detailsFold(text) {
  if (text === "") {
    return null;
  }
  return el("details", { class: "error-details" }, el("summary", null, "Details"), el("p", { class: "small muted mono" }, text));
}

/**
 * A closed "Details" fold that names the key of the settings file a setting is kept under (item 625), for those who edit
 * the file: the page says what the setting does, and this says where it lives.
 */
export function settingFold(key, lead = "In the settings file:") {
  return el("details", { class: "error-details" }, el("summary", null, "Details"), el("p", { class: "small muted" }, `${lead} `, el("code", { class: "mono" }, key)));
}

/** Show a short message; `kind` is "ok" or "error". `action` (`{ label, run }`), when given, is a button in it that runs
 * `run()` (item 616: Reload, for a microphone that Safari stopped hearing). `details` are the program's own words for an
 * error, in a fold of their own under the sentence, which stays long enough to be opened (item 625). */
export function toast(kind, text, action = null, details = "") {
  const fold = detailsFold(details);
  const node = el("div", { class: `toast ${kind}` }, icon(kind === "error" ? "alert" : "check"), el("span", null, text, fold));
  if (action !== null) {
    node.append(el("button", { class: "btn sm toast-action", type: "button", onclick: action.run }, action.label));
  }
  node.addEventListener("click", (event) => {
    if (fold === null || !fold.contains(event.target)) {
      node.remove();
    }
  });
  toastRegion().append(node);
  setTimeout(() => node.remove(), action !== null || fold !== null ? ACTION_TOAST_MS : kind === "error" ? ERROR_TOAST_MS : TOAST_MS);
}

/** Tell in a toast what `error` came to, in words a person reads, and its Details apart (item 625). */
export function toastError(error) {
  const { text, details } = explain(error);
  toast("error", text, null, details);
}

/** Run `action`; when it fails, tell the user why instead of leaving the click without any effect. */
export async function attempt(action) {
  try {
    await action();
  } catch (error) {
    console.error("action failed", error);
    toastError(error);
  }
}

/**
 * The frame every dialog of the app has (item 615): a `<dialog class="modal">` named by its title and described by its
 * line, a head (the title, the line under it and, when `onCross` is given, a cross at the right), the `content` between,
 * if any, and the foot of `actions`, which is `.modal-actions`. Returns the dialog; nothing is shown yet.
 * @param {{ className?: string, title: string, line: string, content?: Array<Node | null>, actions: Array<Node | null>, onCross?: (() => void) | null }} parts
 */
function dialogFrame({ className = "modal", title, line, content = [], actions, onCross = null }) {
  dialogCount += 1;
  const titleId = `dialog-${dialogCount}-title`;
  const lineId = `dialog-${dialogCount}-line`;
  const said = line === "" ? null : el("p", { class: "muted", id: lineId }, line);
  const cross =
    onCross === null
      ? null
      : el("button", { class: "btn ghost icon-only modal-close", type: "button", title: "Close", "aria-label": "Close", onclick: onCross }, icon("x"));
  const dialog = el("dialog", { class: className, "aria-labelledby": titleId, "aria-describedby": said === null ? null : lineId });
  dialog.append(
    el("div", { class: "modal-body modal-head" }, el("div", { class: "modal-titles" }, el("h3", { id: titleId }, title), said), cross),
    ...content,
    el("div", { class: "modal-actions" }, ...actions),
  );
  return { dialog, cross };
}

/**
 * Ask for a yes or no; resolves to true when confirmed. `focusCancel` puts the focus on Cancel instead of the confirm
 * button, for the questions a stray Enter must not answer with yes; `cancelLabel` is the words of the other button.
 * @param {{ title: string, message: string, confirmLabel: string, cancelLabel?: string, danger?: boolean, focusCancel?: boolean }} question
 * @returns {Promise<boolean>}
 */
export function confirmDialog({ title, message, confirmLabel, cancelLabel = "Cancel", danger, focusCancel }) {
  return new Promise((resolve) => {
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, cancelLabel);
    const ok = el(
      "button",
      { class: danger ? "btn danger solid" : "btn primary", type: "button", onclick: () => dialog.close("ok") },
      confirmLabel,
    );
    const { dialog } = dialogFrame({ title, line: message, actions: [cancel, ok] });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(dialog.returnValue === "ok");
    });
    document.body.append(dialog);
    dialog.showModal();
    (focusCancel ? cancel : ok).focus();
  });
}

// The look of a choice of `choiceDialog`, by its tone.
const CHOICE_TONES = { plain: "btn", primary: "btn primary", danger: "btn danger" };

/**
 * A dialog that offers `choices` (`{ value, label, tone }`, `tone` one of "plain", "primary" and "danger") after Cancel,
 * in their order; resolves with the value of the choice pressed, or null when it is cancelled, Escape too. Cancel takes
 * the focus.
 */
export function choiceDialog({ title, message, choices }) {
  return new Promise((resolve) => {
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("") }, "Cancel");
    const buttons = choices.map((choice, index) =>
      el("button", { class: CHOICE_TONES[choice.tone], type: "button", onclick: () => dialog.close(String(index)) }, choice.label),
    );
    const { dialog } = dialogFrame({ title, line: message, actions: [cancel, ...buttons] });
    dialog.addEventListener("close", () => {
      dialog.remove();
      const chosen = dialog.returnValue === "" ? undefined : choices[Number(dialog.returnValue)];
      resolve(chosen === undefined ? null : chosen.value);
    });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

// The controls whose state says whether a form was changed (formflow.js `stateOfControls`).
const FORM_CONTROLS = "input, textarea, select, [role='switch'], [aria-pressed]";
const FORM_FIELDS = "input, textarea, select";
// The types of input that are no field of text: Enter in one of them is its own.
const NOT_TEXT = new Set(["button", "checkbox", "radio", "submit", "reset", "file", "range", "color"]);

/**
 * A form in a dialog over the page (item 615), where it does not make the page longer: the `title`, the line
 * `description` of what it does, the fields (`body`, a node or a list of nodes as the page builds them) and a foot of
 * Cancel and the main button, `submitLabel`; a cross at the top right closes it too. The focus goes to the first field as
 * it opens and back to the button that opened it when it closes: `returnFocus` is that button, a node or a function that
 * finds it as the dialog closes (a list drawn again has a new one); without it, what had the focus as the dialog opened.
 * On a phone it is a sheet of the full width, its foot in view above the keyboard.
 *
 * `onSubmit()` is asynchronous. While it runs the main button is busy, the other buttons are off and the fields stay as
 * they are. It resolves to close the dialog; it resolves to `false` when something was missing and the field said so (the
 * dialog stays); it throws for a request that failed, and the dialog stays open with what was typed and the reason in it,
 * above the foot. Cancel, the cross and Escape close it, asking first ("Discard your changes?") when a field was
 * changed; none of them works while a submit runs. It closes by itself, and asks nothing, when the page goes to another.
 *
 * `alternative` (`{ label, onSubmit }`) is a second way to submit, between Cancel and the main button; `leading` are
 * nodes for the foot's left end (an action of the form that is no submit); `ready` is a promise that settles once the
 * fields a page fills in later are filled (the models, the agents), after which an untouched form counts as unchanged;
 * `submitOnEnter` makes Enter in a one-line field submit; `size` is "narrow", "regular" or "wide"; `danger` makes the
 * main button the red one.
 *
 * Returns `closed`, a promise of whether it was submitted, and `fail(error)`, which shows the reason of a failure of an
 * action of the form that is no submit (and clears it for null).
 * @param {{
 *   title: string,
 *   description?: string,
 *   body: Node | Node[],
 *   submitLabel: string,
 *   onSubmit: () => Promise<unknown>,
 *   danger?: boolean,
 *   alternative?: { label: string, onSubmit: () => Promise<unknown> } | null,
 *   leading?: Node[],
 *   ready?: Promise<unknown> | null,
 *   submitOnEnter?: boolean,
 *   returnFocus?: HTMLElement | (() => HTMLElement | null) | null,
 *   size?: "narrow" | "regular" | "wide",
 * }} form
 */
export function formDialog({
  title,
  description = "",
  body,
  submitLabel,
  onSubmit,
  danger = false,
  alternative = null,
  leading = [],
  ready = null,
  submitOnEnter = false,
  returnFocus = null,
  size = "regular",
}) {
  const active = document.activeElement;
  const opener = active instanceof HTMLElement && active !== document.body ? active : null;
  /** @type {(submitted: boolean) => void} */
  let settle = () => undefined;
  const closed = new Promise((resolve) => {
    settle = resolve;
  });

  const fields = el("div", { class: "modal-fields" }, ...(Array.isArray(body) ? body : [body]));
  const problem = el("div", { class: "note bad form-dialog-error", role: "alert", hidden: true }, icon("alert"), el("span"));
  const cancel = el("button", { class: "btn", type: "button", onclick: () => void flow.dismiss() }, "Cancel");
  const primary = el("button", { class: danger ? "btn danger solid" : "btn primary", type: "button", onclick: () => void flow.submit(onSubmit, "primary") }, submitLabel);
  const other =
    alternative === null
      ? null
      : el("button", { class: "btn", type: "button", onclick: () => void flow.submit(alternative.onSubmit, "alternative") }, alternative.label);
  const { dialog, cross } = dialogFrame({
    className: `modal form-dialog form-dialog-${size}`,
    title,
    line: description,
    content: [fields, problem],
    actions: [leading.length === 0 ? null : el("div", { class: "modal-actions-lead" }, ...leading), cancel, other, primary],
    onCross: () => void flow.dismiss(),
  });

  const spinner = el("span", { class: "spinner", "aria-hidden": "true" });
  // What the owner has typed or picked, as the fields stood when the dialog was shown (and, with `ready`, when the fields
  // a page fills in later were filled, unless the owner had touched one by then).
  let baseline = "";
  let touched = false;
  const stateNow = () => stateOfControls(fields.querySelectorAll(FORM_CONTROLS));
  for (const kind of ["input", "change", "click"]) {
    fields.addEventListener(kind, () => {
      touched = true;
    });
  }

  // The focus goes back to the button the page names, found as the dialog closes (a repainted list has a new one); with
  // none named, to what had it as the dialog opened. A click does not focus a button in WebKit, so what had it then may
  // be the page's own frame, which is why the page names the button.
  function restoreFocus() {
    const named = typeof returnFocus === "function" ? returnFocus() : returnFocus;
    const target = named?.isConnected ? named : opener;
    if (target?.isConnected) {
      target.focus({ preventScroll: true });
    }
  }

  /** Show why something failed above the foot, where it is seen though the fields scroll; null clears it. The strip
   * takes room from the fields: what stood at their foot, where the owner pressed the button, stays in sight. */
  function showProblem(error) {
    if (error === null) {
      problem.hidden = true;
      return;
    }
    console.error("a form could not be submitted", error);
    const belowFoot = fields.scrollHeight - fields.clientHeight - fields.scrollTop;
    const { text, details } = explain(error);
    problem.lastChild.replaceChildren(...[text, detailsFold(details)].filter((part) => part !== null));
    problem.hidden = false;
    fields.scrollTop = fields.scrollHeight - fields.clientHeight - belowFoot;
  }

  const flow = createFormFlow({
    changed: () => stateNow() !== baseline,
    discard: () =>
      confirmDialog({
        title: "Discard your changes?",
        message: "What you typed here is not saved.",
        confirmLabel: "Discard",
        cancelLabel: "Keep editing",
        danger: true,
        focusCancel: true,
      }),
    busy: (which) => {
      const at = which !== null;
      dialog.setAttribute("aria-busy", String(at));
      for (const button of [cross, cancel, other, primary]) {
        if (button !== null) {
          button.disabled = at;
        }
      }
      spinner.remove();
      if (at) {
        (which === "alternative" && other !== null ? other : primary).prepend(spinner);
      } else if (dialog.isConnected && !dialog.contains(document.activeElement)) {
        primary.focus({ preventScroll: true });
      }
    },
    problem: showProblem,
    finish: (submitted) => {
      window.removeEventListener("hashchange", leave);
      // A key typed in a field of a password does not stay in the page.
      for (const secret of fields.querySelectorAll("input[type='password']")) {
        secret.value = "";
      }
      if (dialog.open) {
        dialog.close();
      }
      dialog.remove();
      restoreFocus();
      settle(submitted);
    },
    lost: (error) => {
      console.error("a form could not be submitted", error);
      toastError(error);
    },
  });
  // The page the dialog belongs to is gone when the address changes (a link inside the form, the browser's back button).
  const leave = () => flow.abandon();
  window.addEventListener("hashchange", leave);

  // Escape asks first when a field was changed; the browser's own close request (its cancel event) is answered the same.
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) {
      event.preventDefault();
      void flow.dismiss();
    } else if (submitOnEnter && event.key === "Enter" && !event.isComposing && event.keyCode !== 229 && !event.shiftKey) {
      const target = event.target;
      if (target instanceof HTMLInputElement && !NOT_TEXT.has(target.type)) {
        event.preventDefault();
        void flow.submit(onSubmit, "primary");
      }
    }
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    void flow.dismiss();
  });
  // A link in the form that leads to another page of the app (an account to add, the last run of the brief) takes the
  // dialog with it: it asks first, as Cancel does, when a field was changed, and goes on once the owner said so.
  dialog.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("a[href^='#/']") : null;
    const plain = !event.defaultPrevented && event.button === 0 && !(event.metaKey || event.ctrlKey || event.shiftKey);
    if (link === null || !plain || stateNow() === baseline) {
      return;
    }
    event.preventDefault();
    const address = link.getAttribute("href");
    void flow.dismiss().then((closed) => {
      if (closed && address !== null) {
        location.hash = address;
      }
    });
  });
  // A dialog the browser closed by itself (its second Escape in a row, whatever the page answered) is a dialog dismissed.
  dialog.addEventListener("close", () => flow.abandon());

  document.body.append(dialog);
  dialog.showModal();
  const first = firstFieldOf(fields.querySelectorAll(FORM_FIELDS), (field) => field.getClientRects().length > 0);
  (first ?? primary).focus();
  baseline = stateNow();
  if (ready !== null) {
    const settled = () => {
      if (!touched && dialog.isConnected) {
        baseline = stateNow();
      }
    };
    ready.then(settled, settled);
  }

  return { closed, fail: showProblem };
}

/** Show a screenshot at full size until it is clicked or Escape is pressed. */
export function showImage(url, description) {
  if (viewer === null) {
    const image = el("img", { alt: "" });
    viewer = el("dialog", { class: "lightbox" }, image);
    viewer.addEventListener("click", () => viewer.close());
    // Escape closes the picture before the page behind it: the panel a picture was opened from answers Escape too, and
    // would take the key first, leaving the picture over what it showed.
    window.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Escape" && viewer.open) {
          event.preventDefault();
          event.stopPropagation();
          viewer.close();
        }
      },
      true,
    );
    document.body.append(viewer);
  }
  const image = viewer.querySelector("img");
  image.src = url;
  image.alt = description;
  viewer.showModal();
}
