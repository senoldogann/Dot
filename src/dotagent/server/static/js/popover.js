// A panel that opens above a control of a frame (the composer's pills), or below it where there is more room: built when it opens, closed by Escape, by a
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

  // The control's place in the frame is measured on the screen: the offsets of a control inside a box of its own count
  // from that box (the capsule's plan part, item 644, sits in a bar whose backdrop filter makes it one), and a row that
  // scrolls sideways moves the control without changing them.
  function place() {
    const box = frame.getBoundingClientRect();
    const at = anchor.getBoundingClientRect();
    const room = frame.clientWidth - panel.offsetWidth - EDGE_PX;
    panel.style.left = `${Math.max(EDGE_PX, Math.min(at.left - box.left - frame.clientLeft, room))}px`;
    // Above the control, where it fits. Else below it, where it fits there: the home's box stands mid-screen, and on a
    // phone-sized window the four modes scrolled in the room above it. Else, when the window holds it whole, below
    // and moved up over the control as far as it must to end inside the window. Else on the roomier side. The panel
    // keeps within the room it is given (app.css), so it never leaves the window.
    const needed = panel.scrollHeight;
    const whole = Math.max(0, window.innerHeight - 2 * EDGE_PX);
    const above = Math.max(0, at.top - 2 * EDGE_PX);
    const below = Math.max(0, window.innerHeight - at.bottom - 2 * EDGE_PX);
    let side = "above";
    let top = 0;
    let height = above;
    if (needed > above && below >= needed) {
      side = "below";
      top = at.bottom + EDGE_PX;
      height = below;
    } else if (needed > above && needed <= whole) {
      side = "over";
      top = Math.min(at.bottom + EDGE_PX, window.innerHeight - EDGE_PX - needed);
      height = whole;
    } else if (needed > above && below > above) {
      side = "below";
      top = at.bottom + EDGE_PX;
      height = below;
    }
    if (side === "above") {
      panel.style.top = "";
      panel.style.bottom = `${frame.clientHeight - (at.top - box.top - frame.clientTop) + EDGE_PX}px`;
    } else {
      panel.style.bottom = "";
      panel.style.top = `${top - box.top - frame.clientTop}px`;
    }
    panel.dataset.side = side;
    panel.style.setProperty("--room", `${height}px`);
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
