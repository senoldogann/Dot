// The one sheet of a phone's chat (item 1383): a segment of the capsule over the composer (dockbar.js) opens its detail
// here, at the foot of the screen over a dim, and never more than one at a time. What the stacked panes above the
// composer showed (the queue, the loops, the commands at work) is shown in it as it was: the pane itself is put in the
// sheet while it is open and back in the dock when it closes, so that its rows, its buttons and their calls are the
// same; the helpers and the plan are drawn from their words by `build()`.
// The sheet closes by a tap on the dim, by Escape, by the handle at its top, and by a swipe down the handle and the title.
// The segment that opened it says `aria-expanded`, and the focus goes back to it once it closes.

import { el } from "./dom.js";
import { t } from "./i18n.js";
import { PHONE } from "./layout.js";

// A drag of the handle moves the sheet once it has gone this far down, and closes it when let go past the second.
const DRAG_SLOP_PX = 6;
const DRAG_CLOSE_PX = 96;
const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/**
 * What one sheet shows and where it opens from.
 * @typedef {object} SheetSpec
 * @property {HTMLElement} anchor the segment that opens it
 * @property {() => string} title its heading
 * @property {HTMLElement} [adopt] a pane of the dock that stands in the sheet while it is open
 * @property {() => Node[]} [build] the sheet's content, drawn again by `repaint(id)` while it is open (without `adopt`)
 * @property {() => void} [onOpen] called when the sheet has opened
 * @property {() => void} [onClose] called when the sheet has closed
 */

/**
 * The sheets of one chat page. `add(id, spec)` names a sheet, `open(id)` shows it (the one that is open closes first),
 * `close(id, restoreFocus)` closes it (or whichever is open, with no id; the focus goes back to its segment unless
 * `restoreFocus` is false), `isOpen(id)`, `repaint(id)` draws the content of one that is
 * open again when it changed, and `dispose()` takes the sheets away. The sheet closes by itself when the window is no
 * longer a phone's.
 */
export function createDockSheets() {
  const handle = el("button", { class: "dock-sheet-handle", type: "button", "aria-label": t("sheet.close") }, el("span", { class: "sheet-grip", "aria-hidden": "true" }));
  const title = el("h2", { class: "dock-sheet-title" });
  const head = el("div", { class: "dock-sheet-head" }, handle, title);
  const body = el("div", { class: "dock-sheet-body" });
  const panel = el("section", { class: "sheet-panel dock-sheet-panel", role: "dialog", "aria-modal": "true", tabindex: "-1" }, head, body);
  const dim = el("div", { class: "sheet-dim" });
  const root = el("div", { class: "sheet dock-sheet", hidden: true }, dim, panel);
  document.body.append(root);

  /** @type {Map<string, SheetSpec>} */
  const specs = new Map();
  /** @type {{ id: string, spec: SheetSpec, mark: Comment | null, signature: string } | null} */
  let current = null;

  /** What a drawn content says and how it is marked (the classes of its parts: a plan's step done or not), to tell whether
   * a repaint changed it. */
  const signatureOf = (/** @type {Node[]} */ nodes) =>
    nodes
      .map((node) =>
        node instanceof Element
          ? [node.getAttribute("class"), node.textContent, ...[...node.querySelectorAll("[class]")].map((part) => part.getAttribute("class"))].join("|")
          : node.textContent,
      )
      .join("\n");

  function open(/** @type {string} */ id) {
    const spec = specs.get(id);
    if (spec === undefined || current?.id === id) {
      return;
    }
    close();
    const words = spec.title();
    title.textContent = words;
    panel.setAttribute("aria-label", words);
    let mark = null;
    let signature = "";
    if (spec.adopt !== undefined) {
      mark = document.createComment(t("docksheet.aPaneThatStandsIn"));
      spec.adopt.replaceWith(mark);
      body.replaceChildren(spec.adopt);
    } else if (spec.build !== undefined) {
      const nodes = spec.build();
      signature = signatureOf(nodes);
      body.replaceChildren(...nodes);
    }
    current = { id, spec, mark, signature };
    spec.anchor.setAttribute("aria-expanded", "true");
    body.scrollTop = 0;
    root.hidden = false;
    document.addEventListener("keydown", onKey);
    spec.onOpen?.();
    panel.focus({ preventScroll: true });
  }

  function close(/** @type {string} */ id = "", /** @type {boolean} */ restoreFocus = true) {
    if (current === null || (id !== "" && current.id !== id)) {
      return;
    }
    const { spec, mark } = current;
    current = null;
    root.hidden = true;
    // The pane goes back where it stood in the dock, whose rules draw it there.
    if (spec.adopt !== undefined && mark !== null) {
      mark.replaceWith(spec.adopt);
    }
    body.replaceChildren();
    document.removeEventListener("keydown", onKey);
    panel.style.transform = "";
    spec.anchor.setAttribute("aria-expanded", "false");
    spec.onClose?.();
    // A segment that went with what it told of has nothing to take the focus back, and neither has one whose sheet gave way to
    // a box the focus is in (an edit of a queued message).
    if (restoreFocus && spec.anchor.isConnected && spec.anchor.getClientRects().length > 0) {
      spec.anchor.focus({ preventScroll: true });
    }
  }

  function repaint(/** @type {string} */ id) {
    if (current === null || current.id !== id || current.spec.build === undefined) {
      return;
    }
    const words = current.spec.title();
    title.textContent = words;
    panel.setAttribute("aria-label", words);
    const nodes = current.spec.build();
    const signature = signatureOf(nodes);
    if (signature !== current.signature) {
      current.signature = signature;
      body.replaceChildren(...nodes);
    }
  }

  /** Keep the Tab key inside the sheet, which is modal. */
  function keepFocus(/** @type {KeyboardEvent} */ event) {
    const inside = [...panel.querySelectorAll(FOCUSABLE)].filter((node) => node instanceof HTMLElement && node.getClientRects().length > 0);
    if (inside.length === 0) {
      event.preventDefault();
      return;
    }
    const first = inside[0];
    const last = inside[inside.length - 1];
    const at = document.activeElement;
    if (!panel.contains(at) || at === panel) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && at === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && at === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function onKey(/** @type {KeyboardEvent} */ event) {
    // A dialog over the sheet (Stop repeating asks first) takes the Escape that answers it.
    if (event.target instanceof Element && event.target.closest("dialog[open]") !== null) {
      return;
    }
    if (event.key === "Escape" && !event.defaultPrevented) {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      keepFocus(event);
    }
  }

  dim.addEventListener("click", () => close());

  // The swipe down the handle and the title: `drag` is where the touch began, null while none is on them; a drag that
  // moved the sheet is no tap on the handle.
  /** @type {{ id: number, from: number, down: number } | null} */
  let drag = null;
  let dragged = false;
  head.addEventListener("pointerdown", (event) => {
    if (event.isPrimary && event.button === 0) {
      drag = { id: event.pointerId, from: event.clientY, down: 0 };
      dragged = false;
    }
  });
  head.addEventListener("pointermove", (event) => {
    if (drag === null || event.pointerId !== drag.id) {
      return;
    }
    // A mouse let go outside the head sends it no let go: the next move, with no button down, ends the drag.
    if (event.pointerType === "mouse" && (event.buttons & 1) === 0) {
      springBack();
      return;
    }
    drag.down = Math.max(0, event.clientY - drag.from);
    if (drag.down > DRAG_SLOP_PX) {
      dragged = true;
      head.setPointerCapture(event.pointerId);
      panel.style.transition = "none";
      panel.style.transform = `translateY(${drag.down}px)`;
    }
  });
  function springBack() {
    drag = null;
    panel.style.transition = "";
    panel.style.transform = "";
  }
  function letGo(/** @type {PointerEvent} */ event) {
    if (drag === null || event.pointerId !== drag.id) {
      return;
    }
    const { down } = drag;
    springBack();
    if (down > DRAG_CLOSE_PX) {
      close();
    }
  }
  head.addEventListener("pointerup", letGo);
  // A drag that the system takes away (a cancel, or the capture lost) puts the sheet back where it was.
  head.addEventListener("pointercancel", (event) => {
    if (drag !== null && event.pointerId === drag.id) {
      springBack();
    }
  });
  head.addEventListener("lostpointercapture", (event) => {
    if (drag !== null && event.pointerId === drag.id) {
      springBack();
    }
  });
  handle.addEventListener("click", () => {
    if (dragged) {
      dragged = false;
      return;
    }
    close();
  });

  const stopWatching = () => {
    if (!PHONE.matches) {
      close();
    }
  };
  PHONE.addEventListener("change", stopWatching);

  return {
    add: (/** @type {string} */ id, /** @type {SheetSpec} */ spec) => {
      specs.set(id, spec);
      spec.anchor.setAttribute("aria-haspopup", "dialog");
      spec.anchor.setAttribute("aria-expanded", "false");
      spec.anchor.addEventListener("click", () => (current?.id === id ? close() : open(id)));
    },
    open,
    close,
    isOpen: (/** @type {string} */ id = "") => current !== null && (id === "" || current.id === id),
    repaint,
    dispose: () => {
      close();
      PHONE.removeEventListener("change", stopWatching);
      root.remove();
    },
  };
}
