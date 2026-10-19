// Dot's first conversation (item 420). On a first open the home greets the owner by name, asks what to call Dot, says
// in a few lines what Dot does, offers to connect Google (Gmail and Calendar, in Dot's browser) and, once that is done,
// offers to look through the inbox for something to take off the owner's hands. No model is called before the owner
// asks for that look. It ends for good with that look, with Not now, with Skip the introduction, or with the first
// message the owner sends from the composer under it.

import { api } from "./api.js";
import { assistant, saveAssistant } from "./assistant.js";
import { el } from "./dom.js";
import { NAME_CHARS } from "./face.js";
import { shownOwner } from "./presence.js";
import { getSnapshot } from "./state.js";
import { attempt } from "./ui.js";

const DEFAULT_NAME = "Dot";
// Names offered besides the one Dot has; any other can be typed.
const SUGGESTED_NAMES = ["Miro", "Nova", "Juno"];
const GOOGLE = "google";
// The approval modes in which the policy's questions come to the owner (Auto has a review model answer them).
const ASKED_MODES = ["ask", "supervised"];
// How often the Google card asks whether the sign-in in Dot's browser is done.
const SIGN_IN_POLL_MS = 3000;
// The look through the inbox: reading only, with the choice left to the owner.
const INBOX_GOAL =
  "Look through my Gmail inbox in Dot's browser and find up to three things you could take off my hands: mail to " +
  "answer, something to put in my calendar, a task to do. Only read: send, archive and delete nothing. Say in a line " +
  "each what you would do, then ask me which to do.";

/** Whether the Google of `view` (a ConnectionView) is ready for Dot: signed in in Dot's browser, or lent by the
 * owner's own Chrome, whose sign-ins Dot uses as they are. */
function googleReady(view) {
  return view.state === "signed_in" || view.state === "lent";
}

async function googleView() {
  const connections = await api.get("/connections");
  return connections.apps.find((app) => app.id === GOOGLE) ?? null;
}

/**
 * The first conversation, for the home to show in the place of its greeting. `startLook(goal, approvals)` sends the
 * inbox's goal from the composer, in the approval mode its promises were said in; `onSkipped()` gives the home its
 * greeting back. Returns `element`, `sent()` (the owner sent a
 * message: the introduction is over) and `dispose()`.
 */
export function buildFirstChat({ startLook, onSkipped }) {
  let over = false;
  let polling = null;
  const flow = el("div", { class: "first-flow", role: "log", "aria-live": "polite" });
  const skip = el("button", { class: "btn ghost sm", type: "button" }, "Skip the introduction");
  const element = el("section", { class: "first-chat", "aria-label": "First conversation" }, el("div", { class: "first-head" }, skip), flow);

  function stopPolling() {
    if (polling !== null) {
      clearInterval(polling);
      polling = null;
    }
  }

  /** The introduction is over: the daemon keeps it, and the home never shows it again. */
  async function close() {
    if (over) {
      return;
    }
    over = true;
    stopPolling();
    skip.hidden = true;
    await saveAssistant({ introduced: true });
  }

  function say(...parts) {
    flow.append(el("div", { class: "first-say" }, ...parts));
  }

  function mine(text) {
    flow.append(el("div", { class: "user-message" }, el("div", { class: "bubble user" }, text)));
  }

  function noted(text) {
    flow.append(el("p", { class: "first-note", role: "status" }, text));
  }

  // The approval mode every promise here is said in, taken once: the look starts in it too, whatever the composer's
  // own choice is, so that what was said holds.
  const mode = getSnapshot().status?.approvals ?? "ask";

  function greet() {
    const owner = shownOwner();
    const name = assistant()?.name ?? DEFAULT_NAME;
    say(
      el("p", null, owner === "" ? `Hi! I'm ${name}, your assistant on this Mac.` : `Hi ${owner}! I'm ${name}, your assistant on this Mac.`),
      el("p", null, "I take work off your hands, at your desk and while you're away."),
    );
  }

  function askName() {
    const current = assistant()?.name ?? DEFAULT_NAME;
    const names = [...new Set([current, DEFAULT_NAME, ...SUGGESTED_NAMES])];
    const typed = el("input", {
      class: "input",
      type: "text",
      maxlength: String(NAME_CHARS),
      autocomplete: "off",
      placeholder: "Or type a name",
      "aria-label": "A name of your own",
    });
    const own = el("button", { class: "btn primary sm", type: "button" }, "Call me that");
    const card = el(
      "section",
      { class: "card first-card", "aria-label": "What to call Dot" },
      el("p", { class: "first-question" }, "What would you like to call me? You can change it any time in Settings."),
      el(
        "div",
        { class: "first-names", role: "group", "aria-label": "Names" },
        names.map((name) =>
          el(
            "button",
            { class: "first-name", type: "button", onclick: () => attempt(() => named(name, card)) },
            el("span", { class: "first-name-mark", "aria-hidden": "true" }, name.slice(0, 1).toUpperCase()),
            el("span", null, name),
          ),
        ),
      ),
      el("div", { class: "row first-own" }, typed, own),
    );
    own.addEventListener("click", () =>
      attempt(async () => {
        const name = typed.value.trim();
        if (name === "") {
          typed.focus();
          return;
        }
        await named(name, card);
      }),
    );
    typed.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        own.click();
      }
    });
    flow.append(card);
  }

  async function named(name, card) {
    for (const button of card.querySelectorAll("button, input")) {
      button.disabled = true;
    }
    const before = assistant()?.name ?? DEFAULT_NAME;
    let saved;
    try {
      saved = await saveAssistant({ name });
    } catch (error) {
      for (const button of card.querySelectorAll("button, input")) {
        button.disabled = false;
      }
      throw error;
    }
    card.remove();
    mine(saved.name);
    if (saved.name !== before) {
      noted(`Name changed to ${saved.name}`);
    }
    const owner = shownOwner();
    say(el("p", null, owner === "" ? `${saved.name} it is! Nice to meet you.` : `${saved.name} it is! Nice to meet you, ${owner}.`));
    introduce();
    await offerGoogle(saved.name);
  }

  function introduce() {
    const lines = [
      "I keep working on this Mac while you're away: on a goal until it is done, on schedules and on campaigns.",
      "I connect to your apps, such as mail and calendar, and use them as you would.",
    ];
    if (ASKED_MODES.includes(mode)) {
      lines.push("I ask you before what is yours to decide: money, passwords and messages sent in your name.");
    } else if (mode === "auto") {
      lines.push("Money and passwords stay yours; a review model answers my other questions for you and asks you what it cannot decide.");
    }
    say(el("p", null, "A little about me:"), el("ul", { class: "first-list" }, lines.map((line) => el("li", null, line))));
  }

  async function offerGoogle(name) {
    const google = await googleView();
    if (google === null) {
      say(el("p", null, "Tell me what you need help with, in the box below."));
      return;
    }
    if (googleReady(google)) {
      say(
        el(
          "p",
          null,
          google.state === "lent"
            ? "I work in your own Chrome, so Gmail and Google Calendar are there as you are signed in to them."
            : "Gmail and Google Calendar are connected already.",
        ),
      );
      offerLook();
      return;
    }
    const promise = ASKED_MODES.includes(mode) ? " Nothing is sent without your approval." : "";
    say(el("p", null, `Connect your Gmail and I'll find something I can take care of right away.${promise} Or tell me what you need help with.`));
    flow.append(googleCard(google, name));
  }

  function googleCard(google, name) {
    const hint = el("p", { class: "hint first-connect-hint", role: "status", hidden: true });
    const connect = el("button", { class: "btn primary", type: "button" }, "Connect");
    const card = el(
      "section",
      { class: "card first-connect", "aria-label": "Gmail and Google Calendar" },
      el(
        "div",
        { class: "row" },
        el("img", { class: "first-connect-mark", src: "/icons/plugins/google.svg", alt: "", width: "28", height: "28" }),
        el("div", { class: "grow" }, el("div", { class: "first-connect-name" }, "Gmail and Google Calendar"), el("div", { class: "hint" }, `Let ${name} use them, signed in to Google in Dot's browser.`)),
        connect,
      ),
      hint,
    );
    if (google.step !== "sign_in") {
      connect.hidden = true;
      hint.hidden = false;
      hint.textContent = "Dot's browser runs without a window, so there is none to sign in in: switch headless off in the config file.";
      return card;
    }
    connect.addEventListener("click", () =>
      attempt(async () => {
        connect.disabled = true;
        try {
          await api.post(`/connections/${GOOGLE}`, null);
        } finally {
          connect.disabled = false;
        }
        hint.hidden = false;
        hint.textContent = "Sign in to Google in Dot's browser window. This card follows it.";
        follow(card, connect, hint);
      }),
    );
    return card;
  }

  function follow(card, connect, hint) {
    stopPolling();
    polling = setInterval(async () => {
      let google;
      try {
        google = await googleView();
      } catch (error) {
        console.warn("the Google sign-in could not be read", error);
        return;
      }
      if (google !== null && googleReady(google) && polling !== null) {
        stopPolling();
        connect.hidden = true;
        hint.textContent = "Connected.";
        card.classList.add("connected");
        offerLook();
      }
    }, SIGN_IN_POLL_MS);
  }

  function offerLook() {
    const yes = el("button", { class: "btn primary", type: "button" }, "Yes, have a look");
    const later = el("button", { class: "btn", type: "button" }, "Not now");
    const asks = ASKED_MODES.includes(mode)
      ? "I'll only read, and ask you before I do anything."
      : "I'll only read, and tell you what I could do.";
    say(el("p", null, `Shall I look through your inbox for something I can take off your hands? ${asks}`), el("div", { class: "row first-actions" }, yes, later));
    yes.addEventListener("click", () =>
      attempt(async () => {
        yes.disabled = true;
        later.disabled = true;
        await close();
        await startLook(INBOX_GOAL, mode);
      }),
    );
    later.addEventListener("click", () =>
      attempt(async () => {
        yes.disabled = true;
        later.disabled = true;
        mine("Not now");
        say(el("p", null, "All right. Tell me what you need help with, in the box below."));
        await close();
      }),
    );
  }

  skip.addEventListener("click", () =>
    attempt(async () => {
      await close();
      onSkipped();
    }),
  );

  greet();
  askName();
  return {
    element,
    sent: () => {
      void attempt(close);
    },
    dispose: stopPolling,
  };
}
