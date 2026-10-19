// A panel that opens above a control of a frame (the composer's pills): built when it opens, closed by Escape, by a
// click outside it, or by whoever opened it. The control says aria-expanded. The focus moves into the panel when it
// opens (to a control marked `data-autofocus`, else the chosen option, or the first control), the arrow keys move it
// between the panel's controls unless a control answered them itself, and it goes back to the control when the panel
// closes around it.

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
    // The height the window has above the control, which the panel keeps within (app.css), so its top never leaves it.
    panel.style.setProperty("--room-above", `${Math.max(0, anchor.getBoundingClientRect().top - 2 * EDGE_PX)}px`);
  }

  function focusChoice() {
    (
      panel.querySelector("[data-autofocus]") ??
      panel.querySelector("[aria-selected='true']") ??
      panel.querySelector("button, [tabindex]")
    )?.focus();
  }

  // A repaint replaces the panel's children, so the focus goes back to the one chosen, or the first control.
  function repaint() {
    const hadFocus = panel.contains(document.activeElement);
    panel.replaceChildren(...build());
    place();
    if (hadFocus || document.activeElement === document.body) {
      focusChoice();
    }
  }

  function moveFocus(step) {
    const controls = [...panel.querySelectorAll("button:not(:disabled), [tabindex]:not([tabindex='-1'])")];
    if (controls.length === 0) {
      return;
    }
    const at = controls.indexOf(document.activeElement);
    const next = at === -1 ? (step > 0 ? 0 : controls.length - 1) : (at + step + controls.length) % controls.length;
    controls[next].focus();
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
    } else if (
      (event.key === "ArrowDown" || event.key === "ArrowUp") &&
      !event.defaultPrevented &&
      panel.contains(document.activeElement)
    ) {
      event.preventDefault();
      moveFocus(event.key === "ArrowDown" ? 1 : -1);
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
    focusChoice();
  }

  function close() {
    if (panel.hidden) {
      return;
    }
    const hadFocus = panel.contains(document.activeElement);
    panel.hidden = true;
    panel.replaceChildren();
    if (hadFocus) {
      anchor.focus();
    }
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
