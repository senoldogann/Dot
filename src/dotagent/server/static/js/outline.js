// The marks of a conversation's messages (item 201), after T3 Code's: a column of short marks at the left of the
// conversation, one for each message the owner wrote, top to bottom as they stand in it. Resting the pointer on the column
// shows the message nearest to it and the start of its answer, and a click goes to that message; from the keyboard the
// arrows walk the marks and Enter goes. The marks of the messages in sight stand out, and follow the scroll. The column
// needs a pointer that rests and room at the left of the conversation: on a phone, or when the side panel presses the
// conversation against the left edge, it stays away, so that it never covers the text.

import { debounce, el } from "./dom.js";
import { trackHeight } from "./layout.js";

// A conversation of one message has nothing to walk.
const MIN_MESSAGES = 2;
// The marks stand this far apart until the column is as tall as the window lets it be; past that they come closer.
const SPACING_PX = 8;
// The column stands this far into the room at the left of the conversation, and is at most this wide.
const INSET_PX = 12;
const WIDTH_PX = 40;
// With less room than this at the left of the conversation, the column stays away: the inset, a whole mark (24 px) and a
// little air. Beside the side panel, a window of 1512 px or more leaves 42.
const ROOM_PX = 40;
// The marks are counted again once the conversation has stopped changing for this long.
const SETTLE_MS = 150;
// How far a mark is from the one chosen, as its data says it; the ones farther away are all the shortest.
const FARTHEST = 3;
const FINE_POINTER = window.matchMedia("(hover: hover) and (pointer: fine)");
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const MESSAGE = ".bubble.user";
// What Dot wrote in a turn: its answer, or a message on the way to it. A delegated run's sit in its own row.
const REPLY = "article.answer, .bubble[role='article']";
const DELEGATED = "details.child";
// The layout's data that move the conversation without the window changing size: the side panel and its width.
const PLACING = ["data-inspector", "data-stage", "data-file", "style"];

function folded(text) {
  return text.replace(/\s+/g, " ").trim();
}

/** The words of what Dot wrote in `node`: an answer's text or why there is none, or a message as it streams. */
function replyBody(node) {
  return node.querySelector(".prose") ?? node.querySelector(".stream") ?? node.querySelector(":scope > .muted");
}

/** The words of `node` as they read, a space between its paragraphs and items: read when a mark is chosen, not before. */
function words(node) {
  return node === null ? "" : folded(node.innerText);
}

/**
 * The owner's messages of `thread`, in order, each with what Dot last wrote before the next one: `{ node, reply }`, the
 * reply `null` while there is none. The messages and replies of a delegated run are its own, and are left out.
 */
function messagesOf(thread) {
  const found = [];
  for (const node of thread.querySelectorAll(`${MESSAGE}, ${REPLY}`)) {
    if (node.closest(DELEGATED) !== null) {
      continue;
    }
    if (node.matches(MESSAGE)) {
      found.push({ node, reply: null });
    } else if (found.length > 0) {
      const body = replyBody(node);
      if (body !== null && body.textContent.trim() !== "") {
        found[found.length - 1].reply = body;
      }
    }
  }
  return found;
}

/** The index of the mark nearest to `y` on a column of `count` marks from `top` to `top + height`. */
function nearest(count, top, height, y) {
  if (count === 1 || height <= 0) {
    return 0;
  }
  const along = Math.max(0, Math.min(1, (y - top) / height));
  return Math.round(along * (count - 1));
}

/** Where the mark `index` of `count` stands on the column, in percent of its height from the top. */
function placeOf(index, count) {
  return count < 2 ? 0 : (index / (count - 1)) * 100;
}

/** How far down the window the stage bar reaches: what is under it is not in sight. */
function stagebarBottom() {
  const height = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--stagebar-h"));
  return Number.isNaN(height) ? 0 : height;
}

/**
 * The column of marks of the messages of `thread`, the conversation that `main` holds above `dock`, for the run's
 * `layout`: its element goes first in the layout, which places it at the layout's left edge. `dispose()` stops it.
 */
export function messageOutline({ layout, main, thread, dock }) {
  const marks = el("span", { class: "outline-marks" });
  const asked = el("span", { class: "outline-asked" });
  const reply = el("span", { class: "outline-reply" });
  const preview = el("div", { class: "outline-preview", "aria-hidden": "true", hidden: true }, asked, reply);
  const rail = el(
    "button",
    {
      class: "outline-rail",
      type: "button",
      "aria-label": "Jump to a message",
      onpointermove: (event) => point(event.clientY),
      onpointerleave: () => {
        if (document.activeElement !== rail) {
          choose(null);
        }
      },
      onclick: (event) => {
        // Enter and Space come here too, with no pointer (`detail` 0): they go to the mark the arrows chose.
        const pointer = event.detail > 0;
        if (pointer) {
          point(event.clientY);
        }
        jump();
        if (pointer) {
          rail.blur();
        }
      },
      onfocus: () => choose(active ?? Math.max(0, sight().first)),
      onblur: () => choose(null),
      onkeydown: walk,
    },
    el("span", { class: "outline-line" }),
    marks,
  );
  const element = el("nav", { class: "outline", "aria-label": "Messages of this conversation", hidden: true }, rail, preview);
  const stopDock = trackHeight(dock, element, "--dock-h");

  let messages = [];
  let active = null;
  let painting = false;

  /** The messages in sight between the stage bar and the dock: the first of them (else the last one above), and all. */
  function sight() {
    const top = stagebarBottom();
    const bottom = dock.getBoundingClientRect().top;
    const seen = new Set();
    let first = -1;
    let above = -1;
    messages.forEach((message, index) => {
      const box = message.node.getBoundingClientRect();
      if (box.bottom > top && box.top < bottom) {
        seen.add(index);
        first = first === -1 ? index : first;
      } else if (box.top <= top) {
        above = index;
      }
    });
    return { first: first === -1 ? above : first, seen };
  }

  function point(y) {
    const box = rail.getBoundingClientRect();
    choose(nearest(messages.length, box.top, box.height, y));
  }

  function choose(index) {
    active = index === null || messages.length === 0 ? null : Math.max(0, Math.min(messages.length - 1, index));
    paintMarks();
    if (active === null) {
      preview.hidden = true;
      rail.setAttribute("aria-label", "Jump to a message");
      return;
    }
    const message = messages[active];
    const text = words(message.node);
    asked.textContent = text;
    reply.textContent = words(message.reply);
    reply.hidden = reply.textContent === "";
    preview.style.top = `${placeOf(active, messages.length)}%`;
    preview.hidden = false;
    rail.setAttribute("aria-label", `Jump to message: ${text}`);
  }

  function jump() {
    if (active !== null) {
      messages[active].node.scrollIntoView({ block: "start", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
    }
  }

  function walk(event) {
    const moves = { ArrowDown: (active ?? -1) + 1, ArrowUp: (active ?? messages.length) - 1, Home: 0, End: messages.length - 1 };
    if (Object.hasOwn(moves, event.key)) {
      event.preventDefault();
      choose(moves[event.key]);
    }
  }

  /** Each mark's distance from the chosen one, and whether its message is in sight. */
  function paintMarks() {
    const { seen } = sight();
    Array.from(marks.children).forEach((mark, index) => {
      mark.dataset.near = active === null ? "" : String(Math.min(FARTHEST, Math.abs(index - active)));
      mark.dataset.seen = String(seen.has(index));
    });
  }

  /** Show the column where it has the room it needs at the left of the conversation, as wide as that room lets it. */
  function arrange() {
    const room = main.getBoundingClientRect().left - layout.getBoundingClientRect().left;
    const shown = FINE_POINTER.matches && messages.length >= MIN_MESSAGES && room >= ROOM_PX;
    element.hidden = !shown;
    if (shown) {
      element.style.setProperty("--outline-w", `${Math.min(WIDTH_PX, Math.floor(room) - INSET_PX)}px`);
    }
  }

  function count() {
    messages = messagesOf(thread);
    marks.replaceChildren(
      ...messages.map((_, index) => {
        const mark = el("span", { class: "outline-mark" });
        mark.style.top = `${placeOf(index, messages.length)}%`;
        return mark;
      }),
    );
    element.style.setProperty("--outline-h", `${Math.max(1, (messages.length - 1) * SPACING_PX)}px`);
    arrange();
    choose(active !== null && active < messages.length ? active : null);
  }

  function repaint() {
    if (painting) {
      return;
    }
    painting = true;
    requestAnimationFrame(() => {
      painting = false;
      arrange();
      paintMarks();
    });
  }

  const watch = new MutationObserver(debounce(count, SETTLE_MS));
  watch.observe(thread, { childList: true, subtree: true, characterData: true });
  const placing = new MutationObserver(repaint);
  placing.observe(layout, { attributes: true, attributeFilter: PLACING });
  // A layout put in the page, a window or a side panel that changes size: each changes the room at the left.
  const sizes = new ResizeObserver(repaint);
  sizes.observe(layout);
  sizes.observe(main);
  window.addEventListener("scroll", repaint, { passive: true });
  FINE_POINTER.addEventListener("change", repaint);
  count();

  return {
    element,
    dispose() {
      watch.disconnect();
      placing.disconnect();
      sizes.disconnect();
      stopDock();
      window.removeEventListener("scroll", repaint);
      FINE_POINTER.removeEventListener("change", repaint);
    },
  };
}
