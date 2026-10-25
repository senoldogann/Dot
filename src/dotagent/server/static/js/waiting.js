// What Dot shows while it opens and waits for the daemon (item 328): a curtain of the page's ground over the page that
// is being made, and on a phone that opens a chat it showed before, that chat's bar and composer where its page has
// them. A phone that let Dot go in the background so shows the chat at once, and the chat fills in once the phone's
// network has woken. Nothing says that Dot connects unless that takes longer than a phone's network takes to wake
// (RETURN_GRACE_MS); the curtain lifts once the page shows what it was opened for (PAGE_READY_EVENT, main.js).

import { CHAT_BOX_PX, composerFrame } from "./composer.js";
import { el, showAfter } from "./dom.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";
import { RETURN_GRACE_MS } from "./state.js";

// What a chat's page showed last in its bar and its composer: `{ runId, title, sub, placeholder }`.
const LOOK_KEY = "dot.chatLook";

/** The event a page sends up once it shows what it was opened for, or why it cannot. */
export const PAGE_READY_EVENT = "dot:page-ready";

/** Remember what the page of the chat `runId` shows in its bar (its title, and the agent and the model under it) and as
 * its composer's placeholder. */
export function keepChatLook(runId, title, sub, placeholder) {
  const look = JSON.stringify({ runId, title, sub, placeholder });
  if (readPref(LOOK_KEY) !== look) {
    writePref(LOOK_KEY, look);
  }
}

/** What the page of the chat `runId` showed last, or null when it was another chat's page or nothing readable is kept. */
function chatLook(runId) {
  let look;
  try {
    look = JSON.parse(readPref(LOOK_KEY) ?? "null");
  } catch {
    return null;
  }
  const fits =
    look !== null &&
    typeof look === "object" &&
    look.runId === runId &&
    typeof look.title === "string" &&
    typeof look.sub === "string" &&
    typeof look.placeholder === "string";
  return fits ? look : null;
}

/** The chat's page as far as it is known before the daemon answers: its bar, an empty conversation and its composer, in
 * the classes of the shell and the run page, so that each stands where the page will have it. The face before the title
 * keeps its room empty: an agent's look comes from the daemon. */
function chatPage(look, frame, note) {
  return el(
    "div",
    { class: "stage" },
    el(
      "header",
      { class: "stagebar" },
      el(
        "div",
        { class: "stagebar-row" },
        el("div", { class: "stagebar-left" }, el("span", { class: "icon-btn" }, icon("chevronLeft"))),
        el(
          "div",
          { class: "stage-title" },
          el("span", { class: "avatar" }),
          el("span", { class: "stage-heading" }, el("span", { class: "stage-heading-title" }, look.title), el("span", { class: "stage-heading-sub" }, look.sub)),
        ),
      ),
    ),
    el(
      "main",
      { class: "page", "data-layout": "run" },
      el("div", { class: "run-layout" }, el("div", { class: "run-main" }, el("div", { class: "run-thread" }, note), el("div", { class: "dock" }, frame.element))),
    ),
  );
}

/**
 * The curtain for a page that opens on the chat `runId` (null for any other page): `{ element, lift() }`. Its composer
 * shows the chat's draft and takes nothing (the curtain is inert); the page's own composer is the chat's once it is
 * lifted.
 */
export function openingCurtain(runId) {
  const note = el("div", { class: "waiting-note", hidden: true }, el("div", { class: "spinner" }), el("span", null, "Connecting to Dot…"));
  const showNote = showAfter(note, RETURN_GRACE_MS);
  showNote(true);
  const look = runId === null ? null : chatLook(runId);
  const frame =
    look === null
      ? null
      : composerFrame({
          label: "Message",
          boxLabel: "Message",
          placeholder: look.placeholder,
          maxPx: CHAT_BOX_PX,
          commands: [],
          send: () => undefined,
          sendLabel: "Send",
          draftKey: runId,
          listener: null,
        });
  const element = el("div", { class: "waiting", "aria-hidden": "true", inert: true }, look === null ? note : chatPage(look, frame, note));
  return {
    element,
    lift() {
      showNote(false);
      frame?.dispose();
      element.remove();
    },
  };
}
