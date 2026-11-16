// A sheet that rises from the foot of a phone's screen over everything (item 1202, the owner's ask after another
// assistant's thread settings): a head with Close on its left, the title, and Done on its right, a body that scrolls,
// and a foot that stays (the search of the models). It takes the place of `anchoredPopover` (popover.js) on a phone
// and answers the same calls, so that whoever opens one need not know which it has: built when it opens and on each
// repaint, closed by Escape, by Close or Done, by a press on the dim behind it, or by a swipe down its head. The control
// that opened it says aria-expanded, and the focus goes back to it when the sheet closes.

import { el } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";

// A swipe down the head this far closes the sheet.
const SWIPE_CLOSE_PX = 80;

/**
 * `phoneSheet({ anchor, title, build })`: `anchor` is the button that opens the sheet, `title` its heading, and
 * `build()` returns `{ body, foot }` each time it opens or repaints: the children of the scrolled body and those of the
 * foot (an empty array for none). Returns `open()`, `close()`, `toggle()`, `repaint()`, `isOpen()` and `dispose()`.
 */
export function phoneSheet({ anchor, title, build }) {
  const body = el("div", { class: "sheet-body" });
  const foot = el("div", { class: "sheet-foot" });
  const head = el(
    "div",
    { class: "sheet-head" },
    el("button", { class: "sheet-round", type: "button", "aria-label": t("sheet.close"), onclick: () => close() }, icon("x")),
    el("h2", { class: "sheet-title" }, title),
    el("button", { class: "sheet-round sheet-done", type: "button", "aria-label": t("sheet.done"), onclick: () => close() }, icon("check")),
  );
  const panel = el("div", { class: "sheet-panel", role: "dialog", "aria-modal": "true", "aria-label": title }, el("div", { class: "sheet-grip", "aria-hidden": "true" }), head, body, foot);
  const root = el("div", { class: "sheet", hidden: true }, el("div", { class: "sheet-dim", onclick: () => close() }), panel);
  document.body.append(root);
  anchor.setAttribute("aria-haspopup", "dialog");
  anchor.setAttribute("aria-expanded", "false");

  // A repaint keeps where the body was scrolled to, so that a pick far down the list does not jump it to the top.
  function repaint() {
    const scrolled = body.scrollTop;
    const parts = build();
    body.replaceChildren(...parts.body);
    // The same foot is left in place: moved out and back, its field would lose the focus and the keyboard with it.
    const same = parts.foot.length === foot.children.length && parts.foot.every((node, index) => foot.children[index] === node);
    if (!same) {
      foot.replaceChildren(...parts.foot);
    }
    foot.hidden = parts.foot.length === 0;
    body.scrollTop = scrolled;
  }

  function onKeydown(event) {
    if (event.key === "Escape" && !event.defaultPrevented) {
      event.preventDefault();
      close();
    }
  }

  // The swipe down the head: `from` is where the touch began, null while none is on it.
  let from = null;
  head.addEventListener("touchstart", (event) => {
    from = event.touches[0].clientY;
  }, { passive: true });
  head.addEventListener("touchmove", (event) => {
    if (from === null) {
      return;
    }
    const moved = Math.max(0, event.touches[0].clientY - from);
    panel.style.transform = moved === 0 ? "" : `translateY(${moved}px)`;
  }, { passive: true });
  // A drag the system takes over (an edge gesture, a banner) puts the sheet back where it was.
  head.addEventListener("touchcancel", () => {
    from = null;
    panel.style.transform = "";
  });
  head.addEventListener("touchend", (event) => {
    if (from === null) {
      return;
    }
    const moved = event.changedTouches[0].clientY - from;
    from = null;
    panel.style.transform = "";
    if (moved > SWIPE_CLOSE_PX) {
      close();
    }
  });

  function open() {
    if (!root.hidden) {
      return;
    }
    body.scrollTop = 0;
    root.hidden = false;
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("keydown", onKeydown);
    repaint();
    // The chosen row in sight, the focus on the sheet itself: a phone's keyboard does not rise until a field is pressed.
    body.querySelector("[aria-selected='true']")?.scrollIntoView({ block: "center" });
    panel.setAttribute("tabindex", "-1");
    panel.focus({ preventScroll: true });
  }

  function close() {
    if (root.hidden) {
      return;
    }
    root.hidden = true;
    body.replaceChildren();
    foot.replaceChildren();
    anchor.setAttribute("aria-expanded", "false");
    document.removeEventListener("keydown", onKeydown);
    anchor.focus({ preventScroll: true });
  }

  function toggle() {
    if (root.hidden) {
      open();
    } else {
      close();
    }
  }

  anchor.addEventListener("click", toggle);
  return {
    open,
    close,
    toggle,
    repaint: () => {
      if (!root.hidden) {
        repaint();
      }
    },
    isOpen: () => !root.hidden,
    dispose: () => {
      close();
      anchor.removeEventListener("click", toggle);
      root.remove();
    },
  };
}
