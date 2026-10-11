// A panel that opens above a control of a frame (the composer's pills): built when it opens, closed by Escape, by a
// click outside it, or by whoever opened it. The control says aria-expanded.

import { el } from "./dom.js";

// The panel's edge keeps this much from the frame's.
const EDGE_PX = 8;

/**
 * `anchoredPopover({ anchor, frame, label, build })`: `anchor` is the button that opens the panel, `frame` the
 * positioned box the panel is placed in, `build()` returns the panel's children each time it opens or repaints.
 * Returns `open()`, `close()`, `toggle()`, `repaint()`, `isOpen()` and `dispose()`.
 */
export function anchoredPopover({ anchor, frame, label, build }) {
  const panel = el("div", { class: "composer-popover", role: "dialog", "aria-label": label, hidden: true });
  frame.append(panel);
  anchor.setAttribute("aria-haspopup", "dialog");
  anchor.setAttribute("aria-expanded", "false");

  function place() {
    const room = frame.clientWidth - panel.offsetWidth - EDGE_PX;
    panel.style.left = `${Math.max(EDGE_PX, Math.min(anchor.offsetLeft, room))}px`;
    panel.style.bottom = `${frame.clientHeight - anchor.offsetTop + EDGE_PX}px`;
  }

  // A repaint replaces the panel's children, so the focus goes back to the one chosen, or the first control.
  function repaint() {
    const hadFocus = panel.contains(document.activeElement);
    panel.replaceChildren(...build());
    place();
    if (hadFocus || document.activeElement === document.body) {
      (panel.querySelector("[aria-selected='true']") ?? panel.querySelector("button, [tabindex]"))?.focus();
    }
  }

  function onPointerDown(event) {
    if (!panel.contains(event.target) && !anchor.contains(event.target)) {
      close();
    }
  }

  function onKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      anchor.focus();
    }
  }

  function open() {
    if (!panel.hidden) {
      return;
    }
    panel.hidden = false;
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeydown);
    repaint();
  }

  function close() {
    if (panel.hidden) {
      return;
    }
    panel.hidden = true;
    panel.replaceChildren();
    anchor.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("keydown", onKeydown);
  }

  function toggle() {
    if (panel.hidden) {
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
      if (!panel.hidden) {
        repaint();
      }
    },
    isOpen: () => !panel.hidden,
    dispose: () => {
      close();
      anchor.removeEventListener("click", toggle);
      panel.remove();
    },
  };
}
