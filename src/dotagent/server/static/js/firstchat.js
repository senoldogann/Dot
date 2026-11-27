// Dot's first conversation (item 420). On a first open the home greets the owner by name, asks what to call Dot, says
// in a few lines what Dot does, offers to connect Google (Gmail and Calendar, in Dot's browser) and, once that is done,
// offers to look through the inbox for something to take off the owner's hands. No model is called before the owner
// asks for that look. It ends for good with that look, with Not now, with Skip the introduction, or with the first
// message the owner sends from the composer under it.

import { api } from "./api.js";
import { assistant, saveAssistant } from "./assistant.js";
import { el } from "./dom.js";
import { NAME_CHARS } from "./face.js";
import { markContent } from "./marks.js";
import { shownOwner } from "./presence.js";
import { getSnapshot } from "./state.js";
import { attempt } from "./ui.js";
import { t } from "./i18n.js";

const DEFAULT_NAME = "Dot";
// Names offered besides the one Dot has; any other can be typed.
const SUGGESTED_NAMES = ["Miro", "Nova", "Juno"];
const GOOGLE = "google";
// The approval modes in which the policy's questions come to the owner (Auto has a review model answer them).
const ASKED_MODES = ["ask", "supervised"];
// How often the Google card asks whether the sign-in in Dot's browser is done.
const SIGN_IN_POLL_MS = 3000;
// The look through the inbox: reading only, with the choice left to the owner.
const INBOX_GOAL = t("firstchat.inboxGoal");

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
  const skip = el("button", { class: "btn ghost sm", type: "button" }, t("firstchat.skipTheIntroduction"));
  const element = el("section", { class: "first-chat", "aria-label": t("firstchat.firstConversation") }, el("div", { class: "first-head" }, skip), flow);

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
    flow.append(el("div", { class: "user-message fresh" }, el("div", { class: "bubble user" }, text)));
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
      el("p", null, owner === "" ? t("firstchat.hiIMNameYour", { name }) : t("firstchat.hiOwnerIMName", { owner, name })),
      el("p", null, t("firstchat.iTakeWorkOffYour")),
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
      placeholder: t("firstchat.orTypeAName"),
      "aria-label": t("firstchat.aNameOfYourOwn"),
    });
    const own = el("button", { class: "btn primary sm", type: "button" }, t("firstchat.callMeThat"));
    const card = el(
      "section",
      { class: "card first-card", "aria-label": t("firstchat.whatToCallDot") },
      el("p", { class: "first-question" }, t("firstchat.whatWouldYouLikeTo")),
      el(
        "div",
        { class: "first-names", role: "group", "aria-label": t("firstchat.names") },
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
      noted(t("firstchat.nameChangedToName", { name: saved.name }));
    }
    const owner = shownOwner();
    say(el("p", null, owner === "" ? t("firstchat.nameItIsNiceTo", { name: saved.name }) : t("firstchat.nameItIsNiceTo2", { name: saved.name, owner })));
    introduce();
    await offerGoogle(saved.name);
  }

  function introduce() {
    const lines = [
      t("firstchat.iKeepWorkingOnThis"),
      t("firstchat.iConnectToYourApps"),
    ];
    if (ASKED_MODES.includes(mode)) {
      lines.push(t("firstchat.iAskYouBeforeWhat"));
    } else if (mode === "auto") {
      lines.push(t("firstchat.moneyAndPasswordsStayYours"));
    }
    say(el("p", null, t("firstchat.aLittleAboutMe")), el("ul", { class: "first-list" }, lines.map((line) => el("li", null, line))));
  }

  async function offerGoogle(name) {
    const google = await googleView();
    if (google === null) {
      say(el("p", null, t("firstchat.tellMeWhatYouNeed")));
      return;
    }
    if (googleReady(google)) {
      say(
        el(
          "p",
          null,
          google.state === "lent"
            ? t("firstchat.iWorkInYourOwn")
            : t("firstchat.gmailAndGoogleCalendarAre"),
        ),
      );
      offerLook();
      return;
    }
    const promise = ASKED_MODES.includes(mode) ? t("firstchat.nothingIsSentWithoutYour") : "";
    say(el("p", null, t("firstchat.connectYourGmailAndI", { promise })));
    flow.append(googleCard(google, name));
  }

  function googleCard(google, name) {
    const hint = el("p", { class: "hint first-connect-hint", role: "status", hidden: true });
    const connect = el("button", { class: "btn primary", type: "button" }, t("firstchat.connectGoogle"));
    const card = el(
      "section",
      { class: "card first-connect", "aria-label": t("firstchat.gmailAndGoogleCalendar") },
      el(
        "div",
        { class: "row" },
        el("span", { class: "first-connect-mark", "aria-hidden": "true" }, markContent({ logo: "google-icon.svg", glyph: null })),
        el("div", { class: "grow" }, el("div", { class: "first-connect-name" }, t("firstchat.gmailAndGoogleCalendar")), el("div", { class: "hint" }, t("firstchat.letNameUseThemSigned", { name }))),
        connect,
      ),
      hint,
    );
    if (google.step !== "sign_in") {
      connect.hidden = true;
      hint.hidden = false;
      hint.textContent = t("firstchat.dotSBrowserWorksWithout");
      // For whoever edits the settings file: the window comes back with the setting (item 625).
      hint.title = t("firstchat.inTheSettingsFileBrowser");
      hint.dataset.expert = "";
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
        hint.textContent = t("firstchat.signInToGoogleIn");
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
        hint.textContent = t("firstchat.connected");
        card.classList.add("connected");
        offerLook();
      }
    }, SIGN_IN_POLL_MS);
  }

  function offerLook() {
    const yes = el("button", { class: "btn primary", type: "button" }, t("firstchat.yesHaveALook"));
    const later = el("button", { class: "btn", type: "button" }, t("firstchat.notNow"));
    const asks = ASKED_MODES.includes(mode)
      ? t("firstchat.iLlOnlyReadAnd")
      : t("firstchat.iLlOnlyReadAnd2");
    say(el("p", null, t("firstchat.shallILookThroughYour", { asks })), el("div", { class: "row first-actions" }, yes, later));
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
        mine(t("firstchat.notNow"));
        say(el("p", null, t("firstchat.allRightTellMeWhat")));
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
