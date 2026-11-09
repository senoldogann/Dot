/**
 * A message's sheet (item 1064): on a screen with no pointer to rest a message shows no buttons under it, only the reactions that
 * stand on it, and holding it opens a sheet from the foot of the screen with what its foot holds. On top, when it was
 * sent and how long its work took; then the reactions, a press of one putting it on the message or taking it back;
 * then what the message can do, one row each: copy it, select its text, read it aloud, edit from here, open its task.
 * Each row presses the foot's own button, which a touch screen does not show, so the sheet does what the foot does.
 *
 * Its states are explicit: the hold (`idle`, then `pressing` from a touch until it is held, moved or let go) and the
 * sheet, open or not, one at a time.
 */
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { REACTIONS, reactionsOf } from "./reactions.js";

// How long a touch rests on a message before it is a hold, and how far it may move meanwhile.
const HOLD_MS = 450;
const SLOP_PX = 10;
// How long after a hold is let go its click, if the browser sends one, is the sheet's.
const AFTER_HOLD_MS = 400;
// A swipe down the sheet this far closes it.
const SWIPE_CLOSE_PX = 80;
// What can be held: your message, an answer, and what Dot said between steps, live and in an earlier turn.
const HOLDABLE = ".user-message, article.answer, .bubble.say, .turn-say";
// The foot of each, which holds its buttons.
const FOOT = ":scope > :is(.message-foot, .answer-foot, .say-foot)";
// The body whose text "Select text" selects.
const BODY = ":scope > .bubble.user > .prose, :scope > .prose";
// A screen with no pointer to rest, whose messages show no buttons (app.css): a long press there is the sheet's alone.
const NO_HOVER = window.matchMedia("(hover: none)");

/** The rows of what a message's `foot` can do: its buttons but the reactions', and its links, in the foot's order. */
function actionsOf(foot) {
  return [...foot.querySelectorAll(":scope > button, :scope > a")].filter(
    (node) => !node.classList.contains("react-button") && !node.closest(".reaction-chips"),
  );
}

function row(glyph, label, act) {
  return el("button", { class: "sheet-action", type: "button", onclick: act }, glyph, el("span", null, label));
}

/** Select the text of `container`'s message, which a touch screen otherwise keeps from a hold (app.css). */
function selectText(container) {
  const body = container.querySelector(BODY);
  if (body === null) {
    return;
  }
  container.classList.add("selecting");
  const range = document.createRange();
  range.selectNodeContents(body);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  // The text is held selectable until the selection leaves it.
  const leave = () => {
    if (!selection || selection.isCollapsed || !body.contains(selection.anchorNode)) {
      container.classList.remove("selecting");
      document.removeEventListener("selectionchange", leave);
    }
  };
  setTimeout(() => document.addEventListener("selectionchange", leave), 0);
}

/** Open the sheet of the message `container`; the focus goes back to it as the sheet closes. */
function openMessageSheet(container) {
  const foot = container.querySelector(FOOT);
  const when = foot?.querySelector("time.message-time");
  const facts = [
    when === null || when === undefined ? "" : when.title || when.textContent || "",
    ...[...(foot?.querySelectorAll(".answer-facts > span") ?? [])].map((node) => node.textContent ?? ""),
  ].filter((part) => part !== "");
  const button = foot?.querySelector(".react-button");
  const reacting = button ? reactionsOf(button) : undefined;
  const dialog = el("dialog", { class: "message-sheet", "aria-label": "Message actions", tabindex: "-1" });
  const close = () => dialog.close();
  const reactionRow =
    reacting === undefined
      ? null
      : el(
          "div",
          { class: "sheet-reactions", role: "group", "aria-label": "React to this message" },
          ...REACTIONS.map((reaction) =>
            el(
              "button",
              {
                class: "sheet-reaction",
                type: "button",
                title: reaction.label,
                "aria-label": reaction.label,
                "aria-pressed": String(reacting.has(reaction.id)),
                onclick: () => {
                  reacting.pick(reaction.id);
                  close();
                },
              },
              el("span", { "aria-hidden": "true" }, reaction.symbol),
            ),
          ),
        );
  // A foot kept back everywhere (your message while its turn's run works: its Edit from here would be refused) lends
  // the sheet none of its controls (the pre-push review).
  const actions = foot && getComputedStyle(foot).display !== "none" ? actionsOf(foot) : [];
  const rows = [
    ...actions.map((node) => {
      const label = node.getAttribute("aria-label") || node.textContent?.trim() || "";
      const glyph = node.querySelector("svg")?.cloneNode(true) ?? icon(node.tagName === "A" ? "external" : "ellipsis");
      // The foot's own control does the work, inside this press, so that a copy is still the owner's gesture.
      return row(glyph, label, () => {
        node.click();
        close();
      });
    }),
  ];
  if (container.querySelector(BODY) !== null) {
    rows.splice(
      Math.min(1, rows.length),
      0,
      row(icon("textCursor"), "Select text", () => {
        close();
        selectText(container);
      }),
    );
  }
  const handle = el("div", { class: "sheet-handle", "aria-hidden": "true" });
  dialog.append(
    handle,
    facts.length === 0 ? null : el("div", { class: "sheet-facts" }, facts.join(" · ")),
    reactionRow,
    rows.length === 0 ? null : el("div", { class: "sheet-actions" }, ...rows),
  );
  // A press on the backdrop, outside the sheet's box, closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) {
        close();
      }
    }
  });
  // A swipe down from its top closes it.
  let from = null;
  dialog.addEventListener("pointerdown", (event) => {
    if (event.target === handle || event.target === dialog || (event.target instanceof Node && dialog.firstChild?.contains(event.target))) {
      from = event.clientY;
    }
  });
  dialog.addEventListener("pointermove", (event) => {
    if (from !== null) {
      dialog.style.transform = `translateY(${Math.max(0, event.clientY - from)}px)`;
    }
  });
  const settle = (event) => {
    if (from === null) {
      return;
    }
    const moved = event.clientY - from;
    from = null;
    dialog.style.transform = "";
    if (moved > SWIPE_CLOSE_PX) {
      close();
    }
  };
  dialog.addEventListener("pointerup", settle);
  dialog.addEventListener("pointercancel", settle);
  dialog.addEventListener("close", () => {
    dialog.remove();
    // The focus goes back to the message, which takes it for that alone.
    if (!container.hasAttribute("tabindex")) {
      container.setAttribute("tabindex", "-1");
    }
    container.focus({ preventScroll: true });
  });
  document.body.append(dialog);
  dialog.showModal();
  // The sheet itself takes the focus, not its first reaction, which would stand ringed as if chosen.
  dialog.focus();
  return dialog;
}

/**
 * Open a message's sheet when it is held on a touch screen, from anywhere under `root`; a hold's own end (the click
 * and the browser's menu after it) does nothing else.
 * @param {Document | HTMLElement} root
 */
export function listenForHolds(root) {
  /** @type {"idle" | "pressing"} */
  let state = "idle";
  let timer = 0;
  let start = { x: 0, y: 0 };
  // Whether the touch at work became a hold, and until when a click is its end (iOS sends none after a hold, Chrome on
  // Android one): the time, not a flag left up, so that no later press is taken for it.
  let held = false;
  let swallowUntil = 0;

  const cancel = () => {
    clearTimeout(timer);
    state = "idle";
  };
  const letGo = () => {
    cancel();
    if (held) {
      held = false;
      swallowUntil = Date.now() + AFTER_HOLD_MS;
    }
  };

  root.addEventListener("pointerdown", (event) => {
    // A new touch is no hold's end: its click is its own.
    swallowUntil = 0;
    const pointer = /** @type {PointerEvent} */ (event);
    const target = /** @type {Element} */ (pointer.target);
    const container = target instanceof Element ? target.closest(HOLDABLE) : null;
    // A finger holds, where no pointer can rest; a mouse or a trackpad has the buttons that show where it rests, and on a
    // screen that has one too the hold stays the browser's (its selection and menu are not kept off there, app.css).
    if (!NO_HOVER.matches || pointer.pointerType !== "touch" || container === null || target.closest("button, a, input, textarea, select, summary, .code-block")) {
      return;
    }
    if (container.classList.contains("selecting")) {
      return;
    }
    state = "pressing";
    held = false;
    start = { x: pointer.clientX, y: pointer.clientY };
    timer = window.setTimeout(() => {
      if (state !== "pressing") {
        return;
      }
      state = "idle";
      held = true;
      openMessageSheet(/** @type {HTMLElement} */ (container));
    }, HOLD_MS);
  });
  root.addEventListener("pointermove", (event) => {
    const pointer = /** @type {PointerEvent} */ (event);
    if (state === "pressing" && Math.hypot(pointer.clientX - start.x, pointer.clientY - start.y) > SLOP_PX) {
      cancel();
    }
  });
  root.addEventListener("pointerup", letGo);
  root.addEventListener("pointercancel", letGo);
  root.addEventListener("scroll", cancel, { capture: true, passive: true });
  // The click a hold ends with, and the browser's own menu of a long press, are the sheet's.
  root.addEventListener(
    "click",
    (event) => {
      const target = event.target;
      if (Date.now() < swallowUntil && !(target instanceof Element && target.closest(".message-sheet"))) {
        swallowUntil = 0;
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true,
  );
  root.addEventListener("contextmenu", (event) => {
    const target = event.target;
    // A link keeps the browser's own menu of a long press.
    if (NO_HOVER.matches && target instanceof Element && target.closest(HOLDABLE) !== null && !target.closest(".selecting, a")) {
      event.preventDefault();
    }
  });
}
