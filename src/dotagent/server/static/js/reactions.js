// The reactions on Dot's messages (item 586): under each message of Dot's a button opens a small palette, the reactions
// that stand show as chips beside it, and a second press of a chip takes one back. What each does is the daemon's
// (agent/reactions.py): while the run works it reads the reaction at its next step, a hold (✋) holds it until the owner
// writes, and after it ended a "not right", a "do it again" and an "explain" start a follow-up run in the same chat, a pin
// keeps the message as a note. A message of a run is told apart by the run and a key: `answer` for the answer the run ended
// with, `step-<n>` for what it said in its step `n`.

import { api } from "./api.js";
import { debounce, el } from "./dom.js";
import { icon } from "./icons.js";
import { readFailed } from "./state.js";
import { attempt, toast } from "./ui.js";

/** The palette, in the order it is shown: the daemon's name of a reaction, its sign and the words that say what it means. */
export const REACTIONS = [
  { id: "good", symbol: "👍", label: "Good" },
  { id: "love", symbol: "❤️", label: "Love it" },
  { id: "wrong", symbol: "👎", label: "Not right" },
  { id: "again", symbol: "🔁", label: "Do it again" },
  { id: "remember", symbol: "📌", label: "Remember this" },
  { id: "explain", symbol: "❓", label: "Explain" },
  { id: "hold", symbol: "✋", label: "Hold on" },
];

/** The events that say a reaction was put on a message or taken back, which the lifecycle stream tells every page. */
export const REACTION_EVENTS = new Set(["reaction_added", "reaction_removed"]);

/** The key of what a run said in its step `step`. */
export function stepMessage(step) {
  return `step-${step}`;
}

// How long the page waits for more events before it reads a chat's reactions again.
const REFRESH_MS = 120;
// The palette keeps this far from the window's edges, and this far from the button it opens from.
const EDGE_PX = 8;
const GAP_PX = 6;
// What a press that did something the owner should hear of says in a toast of a warning, rather than a plain one: the
// daemon refused the follow-up (a limit, a chat that takes none) and kept the reaction as feedback.
const WARNED = new Set(["limit", "feedback"]);

let openPalette = null;

/** Close the palette that is open, if one is. */
function closePalette() {
  if (openPalette !== null) {
    openPalette.close(false);
  }
}

/**
 * The palette of reactions opened from `anchor`: a row of buttons, each a reaction with its sign, the ones that stand
 * ticked. The arrow keys, Home and End move between them, Enter or Space puts a reaction on or takes it back and closes
 * the palette, Escape closes it and gives the focus back to `anchor`, and so does a pick; Tab, a click outside, a scroll,
 * a change of the window's size or of the page close it. One is open at a time.
 * @param {{ anchor: HTMLElement, has: (id: string) => boolean, pick: (id: string) => void }} palette
 */
function showPalette({ anchor, has, pick }) {
  closePalette();
  const choices = REACTIONS.map((reaction) =>
    el(
      "button",
      {
        class: "reaction-choice",
        type: "button",
        role: "menuitemcheckbox",
        "aria-checked": String(has(reaction.id)),
        "aria-label": reaction.label,
        title: reaction.label,
        tabindex: "-1",
        onclick: () => {
          close(true);
          pick(reaction.id);
        },
      },
      el("span", { "aria-hidden": "true" }, reaction.symbol),
    ),
  );
  const node = el("div", { class: "reaction-palette", role: "menu", "aria-orientation": "horizontal", "aria-label": "React to this message" }, choices);

  function move(event) {
    const at = choices.indexOf(/** @type {HTMLButtonElement} */ (document.activeElement));
    const last = choices.length - 1;
    const next = { ArrowRight: at === last ? 0 : at + 1, ArrowDown: at === last ? 0 : at + 1, ArrowLeft: at <= 0 ? last : at - 1, ArrowUp: at <= 0 ? last : at - 1, Home: 0, End: last }[event.key];
    if (next !== undefined) {
      event.preventDefault();
      choices[next].focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "Tab") {
      close(false);
    }
  }

  function outside(event) {
    if (!node.contains(event.target) && !anchor.contains(event.target)) {
      close(false);
    }
  }

  function away() {
    close(false);
  }

  function close(giveBack) {
    if (openPalette !== handle) {
      return;
    }
    openPalette = null;
    node.remove();
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("scroll", away, true);
    window.removeEventListener("resize", away);
    window.removeEventListener("hashchange", away);
    anchor.setAttribute("aria-expanded", "false");
    if (giveBack && anchor.isConnected) {
      anchor.focus();
    }
  }

  const handle = { close };
  openPalette = handle;
  node.addEventListener("keydown", move);
  document.body.append(node);
  // Above the button where there is room for it, else under it; never past the window's edges.
  const box = anchor.getBoundingClientRect();
  const above = box.top - node.offsetHeight - GAP_PX >= EDGE_PX;
  node.style.top = `${Math.max(EDGE_PX, above ? box.top - node.offsetHeight - GAP_PX : box.bottom + GAP_PX)}px`;
  node.style.left = `${Math.max(EDGE_PX, Math.min(box.left, window.innerWidth - node.offsetWidth - EDGE_PX))}px`;
  anchor.setAttribute("aria-expanded", "true");
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("scroll", away, true);
  window.addEventListener("resize", away);
  window.addEventListener("hashchange", away);
  (choices.find((choice) => choice.getAttribute("aria-checked") === "true") ?? choices[0]).focus({ preventScroll: true });
}

/**
 * The reactions of the chat `threadId` as the daemon has them, and the controls that show and change them. The chat's
 * reactions are read once (`load`) and again when the daemon tells that one changed (`refresh`); a press is told to the
 * daemon and shown from its answer. Nothing is read or changed once `signal` aborted.
 * @param {{ threadId: string, signal: AbortSignal }} chat
 */
export function createReactions({ threadId, signal }) {
  /** @type {Map<string, Set<string>>} The reactions that stand on each message, by its run and key. */
  const standing = new Map();
  /** @type {Set<() => boolean>} What draws the chips of a message again; one that finds its chips gone says so. */
  const painters = new Set();
  /** @type {Set<string>} The presses on their way to the daemon, so that a second press of one waits for its answer. */
  const pressing = new Set();
  let reads = 0;

  const keyOf = (runId, message) => `${runId}|${message}`;

  function paintAll() {
    for (const paint of [...painters]) {
      if (!paint()) {
        painters.delete(paint);
      }
    }
  }

  /** The message's reactions, from the daemon's rows of them. */
  function setMessage(runId, message, rows) {
    const key = keyOf(runId, message);
    if (rows.length === 0) {
      standing.delete(key);
    } else {
      standing.set(key, new Set(rows.map((row) => row.reaction)));
    }
    paintAll();
  }

  /** Read the chat's reactions; only the newest read paints. */
  async function load() {
    reads += 1;
    const mine = reads;
    const rows = await api.get(`/threads/${encodeURIComponent(threadId)}/reactions`);
    if (signal.aborted || mine !== reads) {
      return;
    }
    standing.clear();
    for (const row of rows) {
      const key = keyOf(row.run_id, row.message);
      standing.set(key, (standing.get(key) ?? new Set()).add(row.reaction));
    }
    paintAll();
  }

  /** Read them again once a burst of events has settled; a read that fails is told as the pages tell theirs. */
  const refresh = debounce(() => {
    load().catch((error) => readFailed("The reactions", error));
  }, REFRESH_MS);

  const path = (runId, message) => `/runs/${runId}/messages/${message}/reactions`;

  /** Put the reaction on the message, or take it back when it stands; the daemon's answer says what it did. */
  function press(runId, message, reaction) {
    const key = `${keyOf(runId, message)}|${reaction}`;
    if (pressing.has(key) || signal.aborted) {
      return Promise.resolve();
    }
    pressing.add(key);
    return attempt(async () => {
      const on = standing.get(keyOf(runId, message))?.has(reaction) === true;
      const outcome = on ? await api.del(`${path(runId, message)}/${reaction}`) : await api.post(path(runId, message), { reaction });
      setMessage(runId, message, outcome.reactions);
      if (outcome.said !== "") {
        toast(WARNED.has(outcome.effect) ? "error" : "ok", outcome.said);
      }
    }).finally(() => pressing.delete(key));
  }

  function chip(runId, message, reaction) {
    return el(
      "button",
      {
        class: "reaction-chip",
        type: "button",
        "aria-pressed": "true",
        "aria-label": reaction.label,
        title: `${reaction.label}: press to take it back`,
        onclick: () => press(runId, message, reaction.id),
      },
      el("span", { "aria-hidden": "true" }, reaction.symbol),
    );
  }

  /**
   * The controls of a message of the run `runId`, `message` its key: the button that opens the palette and the chips of
   * what stands on it, to put in the message's foot. They follow the chat's reactions as they change.
   */
  function controls(runId, message) {
    const button = el(
      "button",
      {
        class: "btn ghost sm icon-only react-button",
        type: "button",
        title: "React to this message",
        "aria-label": "React to this message",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
      },
      icon("smilePlus"),
    );
    const chips = el("span", { class: "reaction-chips", role: "group", "aria-label": "Your reactions", "aria-live": "polite", "aria-relevant": "additions removals" });
    button.addEventListener("click", () => {
      if (openPalette !== null && button.getAttribute("aria-expanded") === "true") {
        closePalette();
        return;
      }
      showPalette({
        anchor: button,
        has: (id) => standing.get(keyOf(runId, message))?.has(id) === true,
        pick: (id) => press(runId, message, id),
      });
    });
    // The chips are held weakly: a message that leaves the page leaves them to be collected, and the painter drops out.
    const held = new WeakRef(chips);
    let shown = "";
    const paint = () => {
      const node = held.deref();
      if (node === undefined) {
        return false;
      }
      const stands = standing.get(keyOf(runId, message)) ?? new Set();
      const ids = REACTIONS.filter((reaction) => stands.has(reaction.id)).map((reaction) => reaction.id);
      // A chip is drawn again only when what stands changed, so that the one that has the focus keeps it.
      if (ids.join(",") !== shown) {
        shown = ids.join(",");
        node.replaceChildren(...REACTIONS.filter((reaction) => stands.has(reaction.id)).map((reaction) => chip(runId, message, reaction)));
      }
      return true;
    };
    painters.add(paint);
    paint();
    return [button, chips];
  }

  return {
    load,
    refresh,
    controls,
    /** Whether a reaction stands on a message. */
    has: (runId, message, reaction) => standing.get(keyOf(runId, message))?.has(reaction) === true,
    dispose: () => {
      closePalette();
      painters.clear();
    },
  };
}

/**
 * The foot of one of Dot's messages in the middle of a conversation (an answer has its own, `answerMessage`): the
 * reactions' controls of the message `message` of the run `runId`.
 */
export function sayFoot(reactions, runId, message) {
  return el("div", { class: "say-foot" }, ...reactions.controls(runId, message));
}
