// The system page: how the daemon is doing, the emergency stop, the health checks and the models each provider offers.

import { DOT_ID, dotFace, lookOf, rememberLook } from "../actors.js";
import { api, clearToken, inPhoneApp, openPhoneAppSettings } from "../api.js";
import {
  AUTO_COMPUTER,
  AUTO_LINES,
  BUSY_SEND,
  HOME_DAY,
  READ_ALOUD,
  SEND_KEY,
  VOICE_LANGUAGE,
  VOICE_LANGUAGES,
  setValue,
  settingValue,
} from "../appearance/settings.js";
import {
  notificationsAvailable,
  notificationsBlocked,
  notificationsOn,
  homeScreenNeeded,
  setNotifications,
  setTellEvenWhenLooking,
  tellEvenWhenLooking,
} from "../attention.js";
import { compact, copyText, dateTimeFull, duration, el, modelName, } from "../dom.js";
import { offersChoice } from "../efforts.js";
import { engageFailsafe, releaseFailsafe } from "../failsafe.js";
import { saveAssistant } from "../assistant.js";
import { dotName, NAME_CHARS } from "../face.js";
import { OWNER_CHARS, ownerName, shownOwner } from "../presence.js";
import { icon } from "../icons.js";
import { lookPicker } from "../lookpicker.js";
import {
  choiceRowSetting,
  advancedFold,
  choiceSetting,
  field,
  foldCard,
  foldGroup,
  inlineError,
  inlineLoading,
  segmented,
  setCard,
  setRow,
  setSection,
  switchRowSetting,
  switchSetting,
} from "../panels.js";
import { answersRole, WHOLE_RUN, providerLabel, providerMark, providerOf, runsDotsTools } from "../providers.js";
import { modelPicker } from "../modelpicker.js";
import { anchoredPopover } from "../popover.js";
import { voiceLanguage } from "../readaloud.js";
import { reasoningSelect } from "../reasoning.js";
import { buildUsage } from "../usagesection.js";
import { approvalLabel } from "../runmodes.js";
import { t, UI_LANGUAGE_KEY, UI_LANGUAGES, uiLanguage, tCount } from "../i18n.js";
import { removePref, writePref } from "../prefs.js";
import { scrollCue } from "../scrollcue.js";
import { forgetModels, getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt, confirmDialog, formDialog, settingFold, toast } from "../ui.js";
import { canRecord } from "../capture.js";
import { macVoice, onMacVoice } from "../macvoice.js";
import { appleTouch, canListen, canSpeak, listeningWay, macOffer, sayOnce, stopReading } from "../voice.js";

const NOT_SET = "—";
// The roles a model is configured for, and the tab where a missing or refused key is put right.
const MODEL_ROLES = ["main", "vision", "review"];
const KEYS_TAB = "#/settings/keys";

function section(title, actions, body) {
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("span", { class: "grow" }, title), actions), body);
}

function entry(label, value) {
  return el("div", null, el("dt", null, label), el("dd", null, value));
}

/** An entry that takes a whole row of the grid, for a value too long for one cell, such as a path. */
function wideEntry(label, value) {
  return el("div", { class: "kv-wide" }, el("dt", null, label), el("dd", null, value));
}

function orDash(value) {
  return value === null || value === "" ? NOT_SET : String(value);
}

function desktopText(status) {
  if (status.failsafe_engaged) {
    return t("views.system.blockedByTheEmergencyStop");
  }
  return status.desktop_enabled ? "enabled" : "disabled";
}

/** Item 1310: a restart of Dot that waits for it to be idle, in one short line of the status; empty when none waits. */
function restartRows(status) {
  const pending = status.restart_pending;
  if (pending === null || pending === undefined) {
    return [];
  }
  const asked = t("views.system.askedDatetimefull", { dateTimeFull: dateTimeFull(pending.since) });
  if (pending.gave_up) {
    return [entry(t("views.system.restart"), t("views.system.waitsForDotToBe", { asked }))];
  }
  const holder = pending.waiting_for === "" ? t("views.system.theNightSWorkTo") : t("views.system.aTaskToEnd");
  return [entry(t("views.system.restart"), t("views.system.waitsForHolderAsked", { holder, asked }))];
}

/** Item 153: the MCP servers that could not start with the daemon, one line each with the reason; Dot runs without their tools. */
function leftOutServers(failures) {
  return [
    ...failures.map((failure) => el("div", null, el("span", { class: "badge b-warn" }, failure.server), ` ${failure.error}`)),
    el("div", { class: "muted" }, t("views.system.inTerminalDotxMcpList")),
  ];
}

/** The daemon's facts in four groups: what this Dot is, how it acts, what it works with, and its models. Each is the
 * group's name over a box of rows, a label at the left and its value at the right, as the settings list groups its tabs
 * (item 330), rather than one wide grid where a model name and an uptime shared a line by accident. */
function statusGroups(status) {
  return [
    {
      title: t("views.system.thisDot"),
      rows: [
        entry(t("views.system.version"), status.version),
        entry(t("views.system.processId"), String(status.pid)),
        entry(t("views.system.uptime"), duration(Math.max(0, Date.now() / 1000 - status.started_at))),
        entry(t("views.system.started"), dateTimeFull(status.started_at)),
        entry(t("views.system.address"), `${status.host}:${status.port}`),
        entry(t("views.system.home"), el("span", { class: "mono" }, status.home)),
        ...restartRows(status),
      ],
    },
    {
      title: t("views.system.howItActs"),
      rows: [
        entry(t("views.system.safetyLevel"), status.preset),
        entry(t("views.system.approvals"), approvalLabel(status.approvals)),
        entry(t("views.system.goalMode"), status.goal_mode ? "on" : "off"),
      ],
    },
    {
      title: t("views.system.whatItWorksWith"),
      rows: [
        entry(t("views.system.scheduler"), status.scheduler_running ? "running" : "stopped"),
        entry(
          t("views.system.phoneNotifications"),
          status.notifications === "down" ? "stopped" : status.notifications === "ok" ? "watching" : "off",
        ),
        entry(t("views.system.desktopControl"), desktopText(status)),
        entry(t("views.system.actionsFromConnectedApps"), String(status.mcp_tools)),
        entry(t("views.system.tasksWorking"), String(status.active_runs.length)),
        entry(t("views.system.waitingForYou"), tCount(status.pending_approvals, "views.system.countApproval", "views.system.countApprovals")),
      ],
    },
    {
      title: t("views.system.models"),
      rows: [
        entry(t("views.system.mainModel"), status.models.main),
        entry(t("views.system.visionModel"), orDash(status.models.vision)),
        entry(t("views.system.fastModel"), orDash(status.models.fast)),
        entry(t("views.system.reviewModel"), orDash(status.models.review)),
        entry(t("views.system.reasoningEffort"), orDash(status.models.effort)),
      ],
    },
  ];
}

/** The daemon's facts as they are drawn: the groups, and under them the MCP servers left out, if any, a whole row too
 * long for a group's. */
function statusFacts(status) {
  const groups = el(
    "div",
    { class: "kv-groups" },
    statusGroups(status).map((group) =>
      el("section", { class: "kv-group" }, el("h2", { class: "settings-group-title" }, group.title), el("dl", { class: "kv kv-rows" }, group.rows)),
    ),
  );
  if (status.mcp_failures.length === 0) {
    return [groups];
  }
  return [groups, el("dl", { class: "kv kv-wide-rows" }, wideEntry(t("views.system.connectedAppsLeftOut"), leftOutServers(status.mcp_failures)))];
}

/** Notifications in Dot's iPhone app (item 1005): the app's own, through APNs, switched in the iPhone's Settings; the
 * browser's switches would do nothing there, as its web view has no web push. */
function buildAppNotifications() {
  return section(
    t("views.system.notifications"),
    null,
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "p",
        { class: "hint" },
        t("views.system.theDotAppTellsYou"),
      ),
      el("button", { class: "btn", type: "button", onclick: openPhoneAppSettings }, icon("settings"), t("views.system.openIphoneSettings")),
    ),
  );
}

/** Whether the page runs as an iPhone's or an iPad's Home Screen app. */
function phoneHomeScreen() {
  const agent = navigator.userAgent;
  const apple = /iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1);
  return apple && window.matchMedia("(display-mode: standalone)").matches;
}

/** Notifications, on the desktop and on the phone: the one setting that also depends on what the browser allows. */
function buildNotifications() {
  if (inPhoneApp()) {
    return buildAppNotifications();
  }
  const setting = switchSetting({
    label: t("views.system.notifications"),
    hint: "",
    read: notificationsOn,
    write: setNotifications,
    onToggled: paint,
  });

  // Item 75: a run on the page you are looking at stays quiet unless you ask otherwise.
  const always = switchSetting({
    label: t("views.system.tellMeEvenWhenI"),
    hint: t("views.system.otherwiseATaskThatEnds"),
    read: tellEvenWhenLooking,
    write: async (on) => setTellEvenWhenLooking(on),
    onToggled: paint,
  });

  function paint() {
    setting.paint();
    always.paint();
    const available = notificationsAvailable();
    const blocked = notificationsBlocked();
    setting.toggle.disabled = !available || blocked;
    always.toggle.disabled = !notificationsOn();
    if (!available && homeScreenNeeded()) {
      setting.note.textContent =
        t("views.system.onAnIphoneOrAn");
    } else if (!available) {
      setting.note.textContent = t("views.system.thisBrowserCannotShowNotifications");
    } else if (blocked && phoneHomeScreen()) {
      // Item 1079: a Home Screen app has no site settings and no reload; what the owner allows in the iPhone's
      // Settings is read again when they come back to Dot.
      setting.note.textContent =
        t("views.system.notificationsForDotAreOff");
    } else if (blocked) {
      setting.note.textContent = t("views.system.theBrowserBlocksNotificationsFor");
    } else {
      setting.note.textContent =
        t("views.system.tellMeWhenDotNeeds");
    }
  }

  paint();
  // What the owner allowed meanwhile, in the browser's site settings or the iPhone's Settings, shows once they are back
  // (item 1079): before, the switch stayed as the page was first drawn until it was loaded again, which a Home Screen
  // app cannot be.
  const element = el("div", { class: "card-pad stack" }, setting.element, always.element);
  const again = () => {
    if (!element.isConnected) {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
      return;
    }
    if (document.visibilityState === "visible") {
      paint();
    }
  };
  document.addEventListener("visibilitychange", again);
  window.addEventListener("focus", again);
  return section(t("views.system.notifications"), null, element);
}

/** The composer's keys (item 230), kept in this browser: the key that sends, and what a message to a run at work
 * does. */
function buildComposerCard() {
  const sendKey = choiceSetting({
    label: t("views.system.sendWith"),
    hint: t("views.system.enterSendsAndShiftEnter"),
    options: [
      { id: "enter", label: "Enter", hint: t("views.system.enterSendsShiftEnterBreaks") },
      { id: "mod-enter", label: "⌘Enter", hint: t("views.system.enterOrCtrlEnterSends") },
    ],
    selected: settingValue(SEND_KEY),
    onSelect: (id) => setValue(SEND_KEY, id),
  });
  const busy = choiceSetting({
    label: t("settings.busy"),
    hint: t("settings.busyHint"),
    options: [
      { id: "queue", label: t("intent.queue"), hint: t("settings.busyQueueHint") },
      { id: "steer", label: t("intent.steer"), hint: t("settings.busySteerHint") },
    ],
    selected: settingValue(BUSY_SEND),
    onSelect: (id) => setValue(BUSY_SEND, id),
  });
  // The interface's own language (i18n.js), kept in this browser; English until another is chosen. The page is drawn
  // again in it, so that no screen mixes the two.
  const language = choiceSetting({
    label: t("settings.language"),
    hint: t("settings.languageHint"),
    options: UI_LANGUAGES.map((choice) => ({ id: choice.id, label: choice.label, hint: "" })),
    selected: uiLanguage(),
    onSelect: (id) => {
      if (id === "en") {
        removePref(UI_LANGUAGE_KEY);
      } else {
        writePref(UI_LANGUAGE_KEY, id);
      }
      location.reload();
    },
  });
  return section(t("views.system.messages"), null, el("div", { class: "card-pad stack" }, language.element, sendKey.element, busy.element));
}

/** What the Voice card says of how the microphone listens and where the voice goes (items 574 and 626), for the way the
 * page listens now and what the Mac said: the sentences, each a hint. */
function voiceListeningFacts() {
  const mac = macVoice();
  const way = listeningWay({
    recognition: canListen(),
    recording: canRecord(),
    mac: mac.transcribe,
  });
  // What the Mac still needs before it can write down what is said, as the daemon says it, and that it could not be asked.
  const macNeeds = macOffer(mac) === "" ? [] : [macOffer(mac)];
  const unasked =
    mac.failed === null ? [] : [t("views.system.theMacCouldNotBe", { failed: mac.failed })];
  if (way === "recording") {
    return [
      t("views.system.theMicrophoneRecordsYourVoice"),
    ];
  }
  if (way === "recognition") {
    return [
      t("views.system.theBrowserDoesTheListening"),
      ...(appleTouch(navigator.userAgent, navigator.maxTouchPoints)
        ? [t("views.system.safariOnAnIphoneOr")]
        : []),
      ...macNeeds,
      ...unasked,
    ];
  }
  return [t("views.system.thisBrowserHasNoSpeech"), ...macNeeds, ...unasked];
}

/** The voice (item 574), kept in this browser: the language the microphone listens in and an answer is read aloud in, and whether
 * an answer that comes in is read aloud. Where the browser has no speech recognition or no voice, the card says so. Returns
 * the card and `dispose()`: the card is made again as the Mac's answer comes in, when it comes after the card was made. */
function buildVoiceCard() {
  const language = el(
    "select",
    { class: "select voice-language", "aria-label": t("views.system.languageOfTheVoice") },
    VOICE_LANGUAGES.map((choice) => el("option", { value: choice.id }, choice.label)),
  );
  language.value = settingValue(VOICE_LANGUAGE);
  language.addEventListener("change", () => setValue(VOICE_LANGUAGE, language.value));
  const aloud = switchSetting({
    label: t("views.system.readAnswersAloud"),
    hint: t("views.system.offUntilYouTurnIt"),
    read: () => settingValue(READ_ALOUD) === "on",
    write: async (on) => {
      setValue(READ_ALOUD, on ? "on" : "off");
      if (on) {
        // Said at the press, so that a browser that speaks only after a touch speaks later, and the owner hears the voice.
        sayOnce(t("views.system.dotReadsAnswersAloudNow"), voiceLanguage());
      } else {
        stopReading();
      }
    },
    onToggled: () => aloud.paint(),
  });
  aloud.paint();
  const speech = canSpeak()
    ? t("views.system.anAnswerIsReadWith")
    : t("views.system.thisBrowserCannotSpeakSo");
  if (!canSpeak()) {
    aloud.toggle.disabled = true;
  }
  // The facts stand in a box of their own, so that they are written again when the Mac's answer comes in.
  const facts = el("div", { class: "stack" });
  const paintFacts = () => facts.replaceChildren(...[speech, ...voiceListeningFacts()].map((fact) => el("div", { class: "hint" }, fact)));
  paintFacts();
  const element = section(
    t("views.system.voice"),
    null,
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, t("views.system.language")), language),
        el("div", { class: "hint" }, t("views.system.whatTheMicrophoneListensFor")),
      ),
      aloud.element,
      facts,
    ),
  );
  return { element, dispose: onMacVoice(paintFacts) };
}

// What each reaction on one of Dot's messages does while its run works, for the six that have a setting for what they do
// once it ended (item 586, `[reactions]` in the config file); a pin has none: it always keeps the message as a note.
const REACTION_SETTINGS = [
  { key: "good", symbol: "👍", label: t("views.system.good"), works: t("views.system.whileATaskWorksIt") },
  { key: "love", symbol: "❤️", label: t("views.system.loveIt"), works: t("views.system.whileATaskWorksIt") },
  { key: "wrong", symbol: "👎", label: t("views.system.notRight"), works: t("views.system.whileATaskWorksIt2") },
  { key: "again", symbol: "🔁", label: t("views.system.doItAgain"), works: t("views.system.whileATaskWorksIt3") },
  { key: "explain", symbol: "❓", label: t("views.system.explain"), works: t("views.system.whileATaskWorksIt4") },
  { key: "hold", symbol: "✋", label: t("views.system.holdOn"), works: t("views.system.whileATaskWorksIt5") },
];
const AFTER_RUN = [
  { id: "follow_up", label: t("views.system.startAFollowUpTask") },
  { id: "feedback", label: t("views.system.onlyFeedback") },
];

/**
 * The reactions on Dot's messages (item 586), kept by the daemon in the config file and changed from here at once: the
 * switch that lets a reaction start a run at all, and for each of six reactions what it does once the run it is on has
 * ended, a follow-up run in the same chat or only feedback.
 */
function buildReactionsCard() {
  let settings = null;
  const body = el("div", { class: "card-pad stack" }, inlineLoading());
  // The card folds (its seven rows are long for a page read for its other cards), shut until it is opened, and stays as
  // the reader left it, in this browser; its head says what a reaction on a task that ended does.
  const card = foldCard({ id: "general-reactions", title: t("views.system.reactions"), actions: null, body, openByDefault: false });

  async function change(key, value) {
    settings = await api.post("/reactions/settings", { key, value });
  }

  const follow = switchSetting({
    label: t("views.system.startATaskForA"),
    hint: t("views.system.onAReactionOnA"),
    read: () => settings !== null && settings.follow_ups === true,
    write: (on) => change("follow_ups", on),
    onToggled: () => paint(),
  });
  const rows = REACTION_SETTINGS.map((row) => {
    const select = el(
      "select",
      { class: "select reaction-after", "aria-label": t("views.system.labelAfterTheTaskHas", { label: row.label }) },
      AFTER_RUN.map((choice) => el("option", { value: choice.id }, choice.label)),
    );
    select.addEventListener("change", () =>
      attempt(async () => {
        try {
          await change(row.key, select.value);
        } finally {
          paint();
        }
      }),
    );
    const element = el(
      "div",
      { class: "setting" },
      el("div", { class: "row" }, el("span", { class: "grow" }, `${row.symbol} ${row.label}`), select),
      el("div", { class: "hint" }, row.works),
    );
    return { key: row.key, select, element };
  });

  function paint() {
    if (settings === null) {
      return;
    }
    follow.paint();
    for (const row of rows) {
      row.select.value = String(settings[row.key]);
      row.select.disabled = settings.follow_ups !== true;
    }
    card.setSummary(settings.follow_ups === true ? t("views.system.followUpTasksOn") : t("views.system.onlyFeedback"));
  }

  async function load() {
    try {
      settings = await api.get("/reactions/settings");
    } catch (error) {
      body.replaceChildren(
        inlineError(error, () => {
          body.replaceChildren(inlineLoading());
          load();
        }),
      );
      return;
    }
    body.replaceChildren(
      follow.element,
      ...rows.map((row) => row.element),
      el("div", { class: "hint" }, t("views.system.rememberThisHasNoSetting")),
    );
    paint();
  }

  load();
  return card.element;
}

/** What a chat shows of the actions the review model approved (items 346 and 362), kept in this browser. */
function buildChatCard() {
  const lines = switchSetting({
    label: t("views.system.showWhatRanWithoutAsking"),
    hint: t("views.system.aLineUnderAStep"),
    read: () => settingValue(AUTO_LINES) === "shown",
    write: async (on) => setValue(AUTO_LINES, on ? "shown" : "hidden"),
    onToggled: () => lines.paint(),
  });
  lines.paint();
  const day = switchSetting({
    label: t("views.system.showTheDayInA"),
    hint: t("views.system.overTheComposerOfA"),
    read: () => settingValue(HOME_DAY) === "shown",
    write: async (on) => setValue(HOME_DAY, on ? "shown" : "hidden"),
    onToggled: () => day.paint(),
  });
  day.paint();
  return section(t("views.system.chat"), null, el("div", { class: "card-pad stack" }, lines.element, day.element));
}

/** The ways to Diagnostics (item 370) and Open source licences (item 371), pages of their own. */
function buildAbout() {
  const row = (href, title, text) =>
    el("a", { class: "list-row about-row", href }, el("div", { class: "row-main" }, el("div", { class: "row-title" }, title), el("div", { class: "row-text small muted" }, text)), icon("chevron"));
  return foldCard({
    id: "system-about",
    title: t("views.system.aboutThisDot"),
    actions: null,
    body: el(
      "div",
      { class: "list" },
      row("#/settings/diagnostics", t("views.system.diagnostics"), t("views.system.whichProgramsDotRunsHow")),
      row("#/settings/licences", t("views.system.openSourceLicences"), t("views.system.theNoticesOfTheOpen")),
    ),
    openByDefault: true,
  }).element;
}

function buildDaemon() {
  const body = el("div", null, inlineLoading());
  // Item 231: whether the daemon's next start resumes on its own the runs it cut off, kept in the config file.
  let resume = null;
  const goOn = switchSetting({
    label: t("views.system.goOnAfterARestart"),
    hint: "",
    read: () => resume !== null && resume.resume === "safe",
    write: async (on) => {
      resume = await api.put("/agent/resume", { resume: on ? "safe" : "off" });
      toast("ok", on ? t("views.system.theNextTimeDotStarts") : t("views.system.theNextTimeDotStarts2"));
      await refresh();
    },
    onToggled: () => paintResume(),
  });
  goOn.element.hidden = true;

  function paintResume() {
    goOn.paint();
    if (resume !== null) {
      const minutes = Math.round(resume.resume_within_s / 60);
      goOn.note.replaceChildren(t("views.system.whenDotStartsAgainA", { minutes }), settingFold("[agent] resume"));
    }
  }

  // Each section of the page folds (item 411), and stays as the reader left it, in this browser.
  const card = foldCard({
    id: "system-daemon",
    title: t("views.system.details"),
    actions: null,
    body: el("div", null, body, el("div", { class: "card-pad daemon-resume" }, goOn.element)),
    openByDefault: false,
  });
  return {
    element: card.element,
    paint(snapshot) {
      if (snapshot.status !== null) {
        card.setSummary(el("span", { class: "muted" }, t("views.system.runningForDuration", { duration: duration(Math.max(0, Date.now() / 1000 - snapshot.status.started_at)) })));
        body.replaceChildren(...statusFacts(snapshot.status));
        resume = snapshot.status.resume;
        goOn.element.hidden = false;
        paintResume();
      }
    },
  };
}

// How long "Restart now?" stands before the button is the quiet "Restart Dot" again (item 409).
const RESTART_ARMED_MS = 6000;

/** The desktop's permissions (item 409): what macOS granted the program that runs Dot, a button that makes macOS ask
 * (and puts the program in the panes' lists), one that opens the pane of each permission missing, and, when the daemon
 * is the service's, one that starts Dot again, since a running process keeps what it had when it started. */
function buildPermissions(onView) {
  let view = null;
  let armed = false;
  let disarm = null;
  const body = el("div", { class: "card-pad stack" }, inlineLoading());

  function arm(on) {
    armed = on;
    if (disarm !== null) {
      clearTimeout(disarm);
      disarm = null;
    }
    if (on) {
      disarm = setTimeout(() => arm(false), RESTART_ARMED_MS);
    }
    paint();
  }

  async function load() {
    try {
      view = await api.get("/desktop/permissions");
    } catch (error) {
      // The tile at the head of the page says so too, rather than Reading for ever (item 564).
      onView(null);
      body.replaceChildren(inlineError(error, () => attempt(load)));
      throw error;
    }
    paint();
  }

  async function ask() {
    view = await api.post("/desktop/permissions", null);
    paint();
    toast("ok", view.panes.length === 0 ? t("views.system.macosGrantedEverythingTheDesktop") : t("views.system.macosAskedSwitchDotOn"));
  }

  async function open(pane) {
    view = await api.post("/desktop/permissions/open", { pane });
    paint();
  }

  async function restart() {
    if (!armed) {
      arm(true);
      return;
    }
    arm(false);
    await api.post("/system/restart", null);
    toast("ok", t("views.system.dotIsClosingAndWill"));
  }

  function row(label, granted) {
    return entry(label, el("span", { class: `badge ${granted ? "b-ok" : "b-warn"}` }, granted ? t("views.system.granted") : t("views.system.missing")));
  }

  function paint() {
    if (view === null) {
      return;
    }
    const missing = view.panes.length > 0;
    const steps = missing
      ? t("views.system.turnDotOnInThe", { value: view.service ? t("views.system.thenPressRestartDot") : t("views.system.thenStartDotAgain") })
      : t("views.system.macosGrantedEverythingTheDesktop");
    const serviceNote = view.service_problem === null ? null : el("p", { class: "hint" }, icon("alert"), t("views.system.macosCouldNotBeAsked", { service_problem: view.service_problem }));
    const askButton = el("button", { class: "btn sm", type: "button", onclick: () => attempt(ask) }, icon("hand"), t("views.system.askMacos"));
    const panes = view.panes.map((pane) =>
      el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => open(pane)) }, icon("external"), t("views.system.openValue", { value: view.titles[pane] ?? pane })),
    );
    const restartButton = el(
      "button",
      { class: `btn sm ${armed ? "danger solid" : ""}`, type: "button", hidden: !view.service, onclick: () => attempt(restart) },
      icon("power"),
      armed ? t("views.system.restartNow") : t("views.system.restartDot"),
    );
    // A part with nothing to say is left out: replaceChildren writes a null as the word "null" (item 411).
    body.replaceChildren(
      ...[
        el("dl", { class: "kv kv-rows" }, row(t("views.system.accessibility"), view.accessibility), row(t("views.system.inputEvents"), view.input_events), row(t("views.system.screenRecording"), view.screen_recording), wideEntry(t("views.system.program"), el("span", { class: "mono" }, view.program))),
        el("p", { class: "hint" }, steps),
        serviceNote,
        el("div", { class: "cluster" }, askButton, ...panes, restartButton),
      ].filter((node) => node !== null),
    );
    const lacking = [
      view.accessibility ? null : t("views.system.accessibility2"),
      view.input_events ? null : t("views.system.inputEvents2"),
      view.screen_recording ? null : t("views.system.screenRecording2"),
    ].filter((name) => name !== null);
    card.setSummary(
      el("span", { class: `badge ${lacking.length === 0 ? "b-ok" : "b-warn"}` }, lacking.length === 0 ? t("views.system.allGranted") : t("views.system.missingJoin", { join: lacking.join(", ") })),
    );
    onView(lacking);
  }

  const refreshButton = el("button", { class: "btn ghost sm", type: "button", title: t("views.system.readThePermissionsAgain"), "aria-label": t("views.system.readThePermissionsAgain"), onclick: () => attempt(load) }, icon("refresh"));
  async function loadQuiet() {
    arm(false);
    await load();
  }

  const card = foldCard({ id: "system-permissions", title: t("views.system.permissions"), actions: refreshButton, body, openByDefault: true });
  return { element: card.element, load: loadQuiet };
}

/** The emergency stop (item 564): a card with its mark, its state and the button that stops everything, or, engaged,
 * the one that releases it. */
function buildFailsafe() {
  const state = el("span", { class: "badge b-muted" }, t("views.system.standby"));
  const text = el("div", { class: "set-row-lead" });
  const engage = el(
    "button",
    { class: "btn danger", type: "button", onclick: () => attempt(engageFailsafe) },
    icon("stop"),
    t("views.system.stopEverything"),
  );
  const release = el("button", { class: "btn", type: "button", onclick: () => attempt(releaseFailsafe) }, t("views.system.release"));
  const element = el(
    "section",
    { class: "failsafe", "aria-label": t("views.system.emergencyStop") },
    el("span", { class: "failsafe-mark", "aria-hidden": "true" }, icon("power")),
    el("div", { class: "failsafe-text" }, el("div", { class: "set-row-title failsafe-title" }, t("views.system.emergencyStop"), state), text),
    el("div", { class: "failsafe-actions" }, engage, release),
  );
  return {
    element,
    paint(snapshot) {
      const engaged = snapshot.status?.failsafe_engaged === true;
      state.className = engaged ? "badge b-bad" : "badge b-muted";
      state.textContent = engaged ? t("views.system.engaged") : t("views.system.standby");
      text.textContent = engaged
        ? t("views.system.theEmergencyStopIsOn")
        : t("views.system.cancelsEveryRunningTaskAnd");
      element.dataset.engaged = String(engaged);
      engage.hidden = engaged;
      release.hidden = !engaged;
    },
  };
}

// The groups the health checks are drawn in (item 564), by the names the doctor gives them; a check none of them takes
// stands in the last group. A group is open at first when the design draws it so.
const HEALTH_GROUPS = [
  { key: "setup", title: t("views.system.setup"), takes: /^(home directory|env file|ripgrep|google chrome|chrome|xcode tools|backups)\b/, open: true },
  { key: "desktop", title: t("views.system.desktopControl"), takes: /^desktop\b/, open: true },
  { key: "models", title: t("views.system.aiServicesAndModels"), takes: /( api| key$| login$|^ollama|^web search|^judge|^model\b|^review model)/, open: false },
  { key: "browser", title: t("views.system.browser"), takes: /^browser\b/, open: false },
  { key: "tools", title: t("views.system.memorySkillsAndPlugins"), takes: /^(memory|mcp|skills|plugins|office apps|one-time codes|onedrive)\b/, open: false },
  { key: "other", title: t("views.system.other"), takes: /./, open: false },
];

/** A check that needs a look: it failed. A required one is failing, any other optional. */
function needsLook(check) {
  return !check.ok;
}

// A check's finding longer than this shows its first two lines, the rest a button away (item 576): the skills check
// names every skill, more than a hundred of them, and filled the page.
const DETAIL_SHOWN = 180;

/** What a check found, its first lines alone when it says much, with the button that shows the rest. */
function healthDetail(text) {
  const detail = el("span", { class: "health-detail" }, text);
  if (text.length <= DETAIL_SHOWN) {
    return detail;
  }
  detail.classList.add("clipped");
  const more = el("button", { class: "btn ghost sm health-more", type: "button", "aria-expanded": "false" }, t("views.system.showAll"));
  more.addEventListener("click", () => {
    const open = detail.classList.toggle("clipped") === false;
    more.textContent = open ? t("views.system.showLess") : t("views.system.showAll");
    more.setAttribute("aria-expanded", String(open));
  });
  return el("span", { class: "health-detail-box" }, detail, more);
}

function healthRow(check) {
  let result = el("span", { class: "health-ok" }, icon("check"), "ok");
  if (!check.ok) {
    result = check.required ? el("span", { class: "badge b-bad" }, t("views.system.failing")) : el("span", { class: "badge b-warn" }, t("views.system.optional"));
  }
  return el(
    "div",
    { class: "set-row health-row", dataset: { check: check.name } },
    el("span", { class: "health-name" }, check.name),
    healthDetail(check.detail),
    result,
  );
}

/** What the Health tile says of the checks: how many passed, and what is left to look at. */
function doctorSummary(checks) {
  const failing = checks.filter((check) => !check.ok && check.required).length;
  const optional = checks.filter((check) => !check.ok && !check.required).length;
  const passed = checks.filter((check) => check.ok).length;
  const line =
    failing > 0
      ? t("views.system.partFailing", { part: tCount(failing, "views.system.countCheck", "views.system.countChecks") })
      : optional > 0
        ? t("views.system.partToLookAt", { part: tCount(optional, "views.system.countOptionalCheck", "views.system.countOptionalChecks") })
        : t("views.system.everyCheckPassed");
  return { tone: failing > 0 ? "bad" : optional > 0 ? "warn" : "ok", word: `${passed} of ${checks.length} ok`, line };
}

/** The Health checks (item 564): what needs a look first, then the checks that passed in their groups; the filter keeps the
 * first alone. They fold as the cards under them do (item 576), their head saying how they stand. `onChecks(checks)`
 * says what the checks found once they ran, or gets null when they could not run. */
function buildDoctor(isDisposed, onChecks) {
  const card = el("div", { class: "health" }, inlineLoading());
  const filterHolder = el("div", { class: "set-filter" });
  const again = el(
    "button",
    { class: "btn", type: "button", title: t("views.system.runTheHealthChecksAgain"), "aria-label": t("views.system.runAgain"), onclick: () => attempt(run) },
    icon("refresh"),
    el("span", { class: "label" }, t("views.system.runAgain")),
  );
  const fold = foldCard({
    id: "system-health",
    title: t("views.system.healthChecks"),
    actions: el("div", { class: "set-actions" }, filterHolder, again),
    body: card,
    openByDefault: false,
  });
  const element = fold.element;
  let checks = null;
  let only = "all";

  function paint() {
    const looks = checks.filter(needsLook);
    const rows = [];
    if (looks.length > 0) {
      rows.push(el("div", { class: "health-looks", role: "group", "aria-label": t("views.system.needsALook") }, el("h3", { class: "health-group-title warn" }, t("views.system.needsALook")), looks.map(healthRow)));
    }
    if (only === "all") {
      for (const group of HEALTH_GROUPS) {
        const passed = checks.filter((check) => check.ok && HEALTH_GROUPS.find((one) => one.takes.test(check.name)) === group);
        const lacking = checks.filter((check) => !check.ok && HEALTH_GROUPS.find((one) => one.takes.test(check.name)) === group).length;
        // A group's fold holds the checks that passed; one whose checks all need a look stands only above.
        if (passed.length === 0) {
          continue;
        }
        rows.push(
          foldGroup({
            id: `system-health-${group.key}`,
            head: el(
              "span",
              { class: "row grow" },
              el("span", { class: "health-group-title" }, group.title),
              lacking > 0
                ? el("span", { class: "fold-count warn" }, t("views.system.lackingToLookAt", { lacking }))
                : el("span", { class: "fold-count ok" }, icon("check"), t("views.system.allOk")),
            ),
            body: el("div", { class: "health-passed" }, passed.map(healthRow)),
            openByDefault: group.open,
          }),
        );
      }
    }
    if (rows.length === 0) {
      rows.push(el("div", { class: "set-row hint" }, t("views.system.nothingNeedsALook")));
    }
    card.replaceChildren(...rows);
  }

  function paintFilter() {
    const looking = checks.filter(needsLook).length;
    const filter = segmented(
      [
        { id: "all", label: t("views.system.allLength", { length: checks.length }), hint: t("views.system.everyCheckTheOnesThat") },
        { id: "look", label: t("views.system.needsALookLooking", { looking }), hint: t("views.system.onlyTheChecksThatNeed") },
      ],
      only,
      (id) => {
        only = id;
        paint();
      },
    );
    filter.element.setAttribute("aria-label", t("views.system.showTheChecks"));
    filterHolder.replaceChildren(filter.element);
  }

  async function run() {
    card.replaceChildren(inlineLoading());
    filterHolder.replaceChildren();
    // The head of the fold says what the checks found this time: never the count of an earlier run, which a folded card
    // would show over a failure it hides (item 576).
    fold.setSummary(el("span", { class: "muted" }, t("views.system.checking")));
    try {
      checks = await api.get("/doctor");
      if (isDisposed()) {
        return;
      }
      onChecks(checks);
      fold.setSummary(el("span", { class: "muted" }, doctorSummary(checks).word));
      paintFilter();
      paint();
    } catch (error) {
      if (!isDisposed()) {
        console.error("the health checks failed", error);
        onChecks(null);
        fold.setSummary(el("span", { class: "badge b-bad" }, t("views.system.couldNotRun")));
        card.replaceChildren(inlineError(error, () => attempt(run)));
      }
    }
  }

  return { element, run };
}

function capabilities(model) {
  return el(
    "div",
    { class: "cluster" },
    model.vision ? el("span", { class: "chip" }, t("views.system.vision")) : null,
    model.tools ? el("span", { class: "chip", title: t("views.system.itCanUseAppsFiles") }, t("views.system.actions")) : null,
    model.thinking ? el("span", { class: "chip" }, t("views.system.thinking")) : null,
    model.free ? el("span", { class: "chip" }, t("views.system.free")) : null,
    model.learns ? el("span", { class: "chip", title: t("views.system.itsServiceMayUseWhat") }, t("views.system.mayTrainOnYourData")) : null,
    model.fast_mode ? el("span", { class: "chip", title: t("views.system.fastModeChosenInThe") }, t("views.system.fastMode")) : null,
  );
}

/** The reasoning level of a model, or a dash when it has fewer than two levels and so nothing to choose. */
function reasoningControl(model) {
  if (!offersChoice(model)) {
    return el("span", { class: "faint", title: t("views.system.thisModelHasNoReasoning") }, NOT_SET);
  }
  return reasoningSelect(model, t("views.system.reasoningLevelOfRef", { ref: model.ref }));
}

// The days of runs the context evidence of a model is drawn from (routes_system.py).
const EVIDENCE_DAYS = 30;
// Where a model's window comes from (llm/types.py WindowSource), as the context facts say it.
const WINDOW_SOURCES = {
  provider: (provider) => t("views.system.providerlabelGivesItForThe", { providerLabel: providerLabel(provider) }),
  published: (provider) => t("views.system.providerlabelPublishesItDotKeeps", { providerLabel: providerLabel(provider) }),
  assumed: (provider) => t("views.system.providerlabelDoesNotSayIt", { providerLabel: providerLabel(provider) }),
  asked: () =>
    t("views.system.itIsTheSizeDot"),
};

function tokens(count) {
  return count.toLocaleString("en-GB");
}

/** The sizes a model's context may be given (item 250): Dot's default first, then the others, the largest marked. */
function sizeOptions(context) {
  return [
    el("option", { value: "" }, t("views.system.compactDefault", { compact: compact(context.default) })),
    ...context.sizes.map((size) => el("option", { value: String(size) }, size === context.ceiling ? t("views.system.compactMost", { compact: compact(size) }) : compact(size))),
  ];
}

/** Dot's default context size for a model (items 250, 396 and 732): the most its window allows up to [agent]
 * context_most_tokens, unless [agent] context_tokens sets one number for every model. */
function defaultFacts(context) {
  if (context.configured !== null) {
    const cut = context.default < context.configured ? t("views.system.threeQuartersOfWhatThis2", { tokens: tokens(context.default) }) : "";
    return t("views.system.tokensTokensSetForEvery", { tokens: tokens(context.configured), cut });
  }
  if (context.ceiling === null) {
    return t("views.system.tokensTokensAsTheModel", { tokens: tokens(context.default) });
  }
  if (context.most !== null && context.default === context.most && context.default < context.ceiling) {
    return t("views.system.tokensTokensDotSWorking", { tokens: tokens(context.default), tokens2: tokens(context.ceiling) });
  }
  return t("views.system.theMostThisModelCan", { tokens: tokens(context.default) });
}

/** What a model's context size does and where its numbers come from (item 250). */
function contextFacts(model) {
  const context = model.context;
  const provider = providerOf(model.ref);
  const own =
    model.own_context_length === null
      ? ""
      : t("views.system.theModelItselfCanTake", { tokens: tokens(model.own_context_length) });
  return [
    el(
      "p",
      null,
      t("views.system.eachStepSendsTheModel"),
      t("views.system.onceAStepSendsMore", { tokens: tokens(context.summarise_at), tokens2: tokens(context.tokens) }),
    ),
    el(
      "p",
      null,
      t("views.system.aLargerSizeMeansFewer"),
      t("views.system.butEveryStepThenSends"),
    ),
    el(
      "dl",
      { class: "context-facts" },
      el("dt", null, t("views.system.whatTheModelCanTake")),
      el(
        "dd",
        null,
        model.context_length === null
          ? t("views.system.providerlabelDoesNotSayHow", { providerLabel: providerLabel(provider) })
          : t("views.system.tokensTokensValueOwn", { tokens: tokens(model.context_length), value: WINDOW_SOURCES[model.window_source](provider), own }),
      ),
      el("dt", null, t("views.system.largestSize")),
      el(
        "dd",
        null,
        context.ceiling === null
          ? NOT_SET
          : t("views.system.tokensTokensThreeQuartersOf", { tokens: tokens(context.ceiling) }),
      ),
      el("dt", null, t("views.system.dotSDefault")),
      el(
        "dd",
        null,
        defaultFacts(context),
        el("span", { class: "faint" }, t("views.system.inTheSettingsFileAgent")),
      ),
      el("dt", null, t("views.system.smallestSize")),
      el("dd", null, t("views.system.tokensTokensATaskS", { tokens: tokens(context.floor) })),
    ),
    // The native replaceChildren writes a null as text: the note of a window too small for any size is left out instead.
    ...(context.sizes.length === 0 && context.ceiling !== null
      ? [el("p", { class: "hint" }, t("views.system.threeQuartersOfWhatThis"))]
      : []),
  ];
}

/** The seconds of a step by its input, as a small table: the buckets the model's steps fell in. */
function bucketTable(buckets) {
  const rows = buckets.map((bucket) => {
    const label = bucket.up_to === null ? t("views.system.overCompact", { compact: compact(bucket.above) }) : bucket.above === 0 ? `up to ${compact(bucket.up_to)}` : `${compact(bucket.above)} to ${compact(bucket.up_to)}`;
    return el("tr", null, el("td", null, label), el("td", null, tokens(bucket.steps)), el("td", null, `${bucket.seconds_median.toFixed(1)} s`));
  });
  return el(
    "table",
    { class: "table context-buckets" },
    el("thead", null, el("tr", null, el("th", null, t("views.system.aStepSInput")), el("th", null, t("views.system.steps")), el("th", null, t("views.system.secondsAtTheMedian")))),
    el("tbody", null, rows),
  );
}

/** What the model's runs of the last 30 days measured (GET /api/models/context). */
function evidenceFacts(evidence) {
  if (evidence.steps === 0) {
    return [el("p", { class: "hint" }, t("views.system.nothingIsMeasuredYetNo", { EVIDENCE_DAYS }))];
  }
  const sent = t("views.system.aStepSentTokensTokens", { tokens: tokens(evidence.input_median), tokens2: tokens(evidence.input_p95), tokens3: tokens(evidence.input_max) });
  const cached = evidence.cached_share === null ? "" : t("views.system.roundOfItTheService", { round: Math.round(evidence.cached_share * 100) });
  const first = evidence.first_step_median === null ? "" : t("views.system.aTaskSFirstStep", { tokens: tokens(evidence.first_step_median) });
  const summaries =
    evidence.compactions === 0
      ? t("views.system.noTaskWasSummedUp")
      : `${tCount(evidence.compactions, "views.system.countSummary", "views.system.countSummaries")} in ${tCount(evidence.compacted_runs, "views.system.countTask", "views.system.countTasks")}.`;
  return [
    el("p", null, t("views.system.partInPart2SentCached", { part: tCount(evidence.steps, "views.system.countStep", "views.system.countSteps"), part2: tCount(evidence.runs, "views.system.countTask", "views.system.countTasks"), sent, cached, first })),
    el("p", null, summaries),
    bucketTable(evidence.buckets),
  ];
}

/**
 * The context size of a model's runs (item 250): a menu of the sizes it may be given, Dot's default first, beside its
 * window, and a Why button that opens a row under the model's with what the size does and what the model's runs of the
 * last 30 days measured. A model whose window allows no other size shows its budget as text. A choice holds from the
 * model's next run; the menu shows what the daemon answered, and `remember(model)` keeps it for the models' list.
 */
function contextControl(initial, remember) {
  let model = initial;
  const name = modelName(model.ref);
  // Every other character spelled out by its code, so that two references never share an id.
  const whyId = `context-why-${[...model.ref].map((char) => (/[A-Za-z0-9-]/.test(char) ? char : `_${char.codePointAt(0).toString(16)}_`)).join("")}`;
  const select = el("select", { class: "select sm", "aria-label": t("views.system.chatSizeOfRef", { ref: model.ref }) });
  const why = el("button", { class: "btn ghost sm", type: "button", "aria-expanded": "false", "aria-controls": whyId, "aria-label": t("views.system.whyTheChatSizeOf", { ref: model.ref }) }, t("views.system.why"));
  const evidence = el("div", { class: "context-evidence" });
  const facts = el("div", { class: "context-why-facts" });
  const body = el("div", { class: "context-why-body" }, el("h4", null, t("views.system.chatSizeOfName", { name })), facts, el("h4", null, t("views.system.thisModelSTasksLast", { EVIDENCE_DAYS })), evidence);
  const row = el("tr", { class: "context-why", id: whyId, hidden: true }, el("td", { colspan: "3" }, body));
  const text = el("span", null);
  const windowText = el("span", { class: "muted" });
  const cell = el("td", { class: "context-cell" }, el("span", { class: "row" }, select, text, windowText, why));

  function paint() {
    const context = model.context;
    const choosable = context.sizes.length > 0;
    select.hidden = !choosable;
    text.hidden = choosable;
    if (choosable) {
      select.replaceChildren(...sizeOptions(context));
      select.value = context.chosen === null ? "" : String(context.chosen);
    }
    text.textContent = compact(context.tokens);
    // The window, and the model's own beside it when Dot asks Ollama for less of it (item 221).
    const own = model.context_length === null || model.own_context_length === null ? null : model.own_context_length;
    windowText.textContent = model.context_length === null ? "" : own === null ? t("views.system.ofCompact", { compact: compact(model.context_length) }) : t("views.system.ofCompactOwnCompact2", { compact: compact(model.context_length), compact2: compact(own) });
    windowText.title =
      own === null ? "" : t("views.system.dotAsksForTokensTokens", { tokens: tokens(model.context_length), tokens2: tokens(own) });
    facts.replaceChildren(...contextFacts(model));
  }

  async function loadEvidence() {
    evidence.replaceChildren(inlineLoading());
    try {
      const found = await api.get(`/models/context?model=${encodeURIComponent(model.ref)}`);
      evidence.replaceChildren(...evidenceFacts(found));
    } catch (error) {
      evidence.replaceChildren(inlineError(error, () => attempt(loadEvidence)));
    }
  }

  why.addEventListener("click", () => {
    const open = row.hidden;
    row.hidden = !open;
    why.setAttribute("aria-expanded", String(open));
    if (open) {
      attempt(loadEvidence);
    }
  });

  // Each choice is sent once the one before it was answered, so the last one made holds.
  let making = Promise.resolve();
  select.addEventListener("change", () => {
    const size = select.value === "" ? null : Number(select.value);
    making = making.then(() =>
      attempt(async () => {
        try {
          model = await api.put("/models/context", { model: model.ref, tokens: size });
          remember(model);
          forgetModels();
          toast("ok", size === null ? t("views.system.nameGetsDotSDefault", { name }) : t("views.system.nameGetsAChatSize", { name, tokens: tokens(size) }));
        } finally {
          paint();
        }
      }),
    );
  });

  paint();
  return { cell, row };
}

// OpenCode's agent and Claude Code keep their own windows and summarize them by themselves (items 283 and 1013): Dot's
// context size does not reach them.
function ownWindow(provider) {
  return { cell: el("td", { class: "hint" }, t("views.system.providerlabelSOwn", { providerLabel: providerLabel(provider) })), row: null };
}

function modelRows(model, mainModel, remember) {
  const provider = providerOf(model.ref);
  const context = WHOLE_RUN.has(provider) ? ownWindow(provider) : contextControl(model, remember);
  return [
    el(
      "tr",
      { dataset: { model: model.ref } },
      // What it can do goes under its name (item 778): as a column of its own it pushed the menus past the card's edge.
      el(
        "td",
        { class: "model-cell" },
        el("span", { class: "model-cell-name" }, el("span", { class: "mono" }, model.ref), model.ref === mainModel ? el("span", { class: "badge b-info" }, t("views.system.defaultBadge")) : null),
        capabilities(model),
      ),
      el("td", { class: "reasoning-cell" }, reasoningControl(model)),
      context.cell,
    ),
    context.row,
  ];
}

function modelTable(provider, mainModel, remember) {
  return el(
    "div",
    { class: "table-scroll models-scroll" },
    el(
      "table",
      { class: "table wide" },
      el("thead", null, el("tr", null, el("th", null, t("views.system.model")), el("th", null, t("views.system.reasoning")), el("th", null, t("views.system.chatSize")))),
      el("tbody", null, provider.models.map((model) => modelRows(model, mainModel, remember))),
    ),
  );
}

/** The providers the configured models run on: a provider problem can affect a run or its check. */
function providersInUse(status) {
  if (status === null) {
    return [];
  }
  const { models } = status;
  const refs = [...MODEL_ROLES.map((role) => models[role]), ...Object.values(models.kinds ?? {}), models.check_other_family ?? ""];
  return [...new Set(refs.filter((ref) => ref !== "").map(providerOf))];
}

// The problems that are the usual state of a provider the owner does not use: no key, or no program to run it on.
const QUIET_PROBLEMS = new Set(["no_key", "not_installed"]);

/** Whether a provider that did not answer is a problem: one without a key or a program is not, unless a configured
 * model runs on it. */
function isFailing(provider, inUse) {
  return provider.error !== null && (!QUIET_PROBLEMS.has(provider.problem) || inUse);
}

/** Why a provider listed no models; a missing or refused key links to the tab that saves keys. */
function problemNote(provider, inUse) {
  if (provider.problem === "no_key") {
    return el(
      "div",
      { class: inUse ? "note bad" : "note" },
      icon("key"),
      el("span", null, t("views.system.noProviderlabelKeyIsSet", { providerLabel: providerLabel(provider.provider) })),
      el("a", { href: KEYS_TAB }, t("views.system.addAKey")),
    );
  }
  if (provider.problem === "not_installed") {
    return el(
      "div",
      { class: inUse ? "note bad" : "note" },
      icon("download"),
      el("span", null, provider.error),
    );
  }
  return el(
    "div",
    { class: "note bad" },
    icon("alert"),
    el(
      "span",
      null,
      provider.error,
      // In the line's own flow, where a phone skips it as a link inside text and it reads as the sentence goes on.
      provider.problem === "key_refused" ? [" ", el("a", { href: KEYS_TAB }, t("views.system.checkTheKey"))] : null,
    ),
  );
}

function providerBody(provider, mainModel, inUse, remember) {
  if (provider.error !== null) {
    return problemNote(provider, inUse);
  }
  if (provider.models.length === 0) {
    return el("div", { class: "hint" }, t("views.system.thisServiceListsNoModels"));
  }
  return modelTable(provider, mainModel, remember);
}

// The badge of a provider that answered with a refusal: of its key (HTTP 401) or of the access (HTTP 403).
const PROBLEM_BADGES = { key_refused: t("views.system.keyRefused"), access_refused: t("views.system.accessRefused") };

function problemBadge(provider, inUse) {
  if (provider.error === null) {
    return null;
  }
  if (provider.problem === "no_key") {
    return el("span", { class: inUse ? "badge b-bad" : "badge b-muted" }, t("views.system.noKey"));
  }
  if (provider.problem === "not_installed") {
    return el("span", { class: inUse ? "badge b-bad" : "badge b-muted" }, t("views.system.notInstalled"));
  }
  return el("span", { class: "badge b-bad" }, PROBLEM_BADGES[provider.problem] ?? t("views.system.unavailable"));
}

/** One provider as a fold: open when it owns the default model or fails, so the cause is in sight. */
function providerBlock(provider, mainModel, inUse, remember) {
  const ownsMain = provider.models.some((model) => model.ref === mainModel);
  return foldGroup({
    id: `system-models-${provider.provider}`,
    head: el(
      "span",
      { class: "row grow" },
      el("strong", null, providerLabel(provider.provider)),
      problemBadge(provider, inUse),
      provider.error === null ? el("span", { class: "fold-count" }, tCount(provider.models.length, "views.system.countModel", "views.system.countModels")) : null,
    ),
    body: providerBody(provider, mainModel, inUse, remember),
    openByDefault: ownsMain || isFailing(provider, inUse),
  });
}

/** What the folded Models card shows: the providers, the models, any provider that fails and any without a key. */
function modelsSummary(providers, inUse) {
  const total = providers.reduce((count, provider) => count + provider.models.length, 0);
  const failing = providers.filter((provider) => isFailing(provider, inUse.includes(provider.provider))).length;
  const keyless = providers.filter((provider) => provider.problem === "no_key" && !inUse.includes(provider.provider)).length;
  return [
    el("span", null, `${tCount(providers.length, "views.system.countService", "views.system.countServices")} · ${tCount(total, "views.system.countModel", "views.system.countModels")}`),
    failing > 0 ? el("span", { class: "badge b-bad" }, t("views.system.failingUnavailable", { failing })) : null,
    keyless > 0 ? el("span", { class: "badge b-muted" }, t("views.system.keylessWithoutAKey", { keyless })) : null,
  ];
}

// The models of Dot's own work (item 232), which Settings › Models chooses under the default one: what each does, and
// the models that can do it.
const ROLE_SETTINGS = [
  {
    role: "fast",
    label: t("views.system.fastModel"),
    hint: t("views.system.writesTheSummaryOfA"),
    can: (model) => model.tools,
    takes: (provider) => answersRole("fast", provider),
    refused: t("views.system.theFastModelTakesA"),
  },
  {
    role: "review",
    label: t("views.system.reviewModel"),
    hint: t("views.system.aSecondAiThatDouble"),
    can: (model) => model.tools,
    takes: (provider) => answersRole("review", provider),
    refused: t("views.system.ofTheAgentProgramsOnly"),
  },
  {
    role: "vision",
    label: t("views.system.visionModel"),
    hint: t("views.system.readsImagesAndScreenshotsFor"),
    can: (model) => model.vision,
    takes: (provider) => answersRole("vision", provider),
    refused: t("views.system.theVisionModelReadsPictures"),
  },
];

// The roles of the night's learning (item 1033, `[harness]`), each played on the review model unless one is chosen for
// it. The selector is built but no step asks it yet, so it is not offered.
const LEARNING_SETTINGS = [
  { role: "proposer", label: t("views.system.proposer"), hint: t("views.system.readsWhatDotSRuns") },
  { role: "composer", label: t("views.system.composer"), hint: t("views.system.weighsTheVersionsTheNight") },
  { role: "experience", label: t("views.system.experienceWriter"), hint: t("views.system.writesDownWhatANight") },
];

// The models Claude Code offers Self-repair (item 933, `ClaudeModel` in repair/claude.py), "default" its own choice.
const REPAIR_MODELS = [
  { value: "default", label: t("views.system.claudeCodeSOwnChoice") },
  { value: "opus", label: "Opus" },
  { value: "sonnet", label: "Sonnet" },
  { value: "haiku", label: "Haiku" },
  { value: "fable", label: "Fable" },
];

// What "Use my Claude plan" chooses (item 1033): the review model on Opus of the plan, and the night's roles on it.
const PLAN_REVIEW = "claude_code:opus";

/**
 * What a model menu of Settings › Models offers (item 1044): the models that `can` do the role's work (call tools, or
 * see) of each provider it `takes`, and a provider it does not take (one whose models answer only as an agent program
 * of their own) but whose models could do the work, with none and the reason, so that its mark stays on the rail, as in
 * the composer's menu; `refused` says why the role cannot take them.
 */
function settingGroups(providers, can, takes, refused) {
  return providers
    .map((one) => {
      const able = one.models.filter(can);
      const models = takes(one.provider) ? able : [];
      const closed = !takes(one.provider) && able.length > 0;
      const why = closed ? t("views.system.providerlabelModelsAnswerOnlyAs", { providerLabel: providerLabel(one.provider), refused }) : null;
      return { provider: one.provider, models, why };
    })
    .filter((group) => group.models.length > 0 || group.why !== null);
}

/**
 * A model that Settings › Models chooses: the default one (item 199), the one a run that names none starts on, a new
 * chat's among them, or one of Dot's own work (item 232). Its box opens the composer's model menu (item 1044): the
 * favourites and the providers on a rail, a search, the models of the provider pressed. It offers the models that
 * `can` do its work (the default one, as the composer does, and the fast and review models call tools; the vision
 * model sees) from the providers it `takes`, and shows those it does not take with why; `none` names the choice of "" (the default model, for the
 * model that looks for work, item 916), null for none; `choose(ref)` makes the choice. `paint(providers, chosen)`
 * draws it again, and the menu with it while it is open.
 */
function modelSetting({ label, hint, className, can, takes, refused, none, choose }) {
  let shown = "";
  let groups = [];
  // The model the blank row stands for (the default model for the own work model, the review model for the night's
  // roles), null for none: its mark leads the row.
  let blankModel = null;
  const mark = el("span", { class: "model-pick-mark" });
  const name = el("span", { class: "model-pick-name mono" });
  // The provider of the model chosen stands in the box, before its chevron, as the design draws it.
  const provider = el("span", { class: "model-pick-provider", "aria-hidden": "true" });
  const button = el("button", { class: "select sm model-pick-button", type: "button" }, mark, name, provider);
  const element = setRow({ title: label, lead: hint, control: el("div", { class: "model-pick" }, button) });
  element.classList.add(className, "model-setting");
  element.hidden = true;
  // Each pick is sent once the one before it was answered, so the last one made holds; the box shows the model the
  // status says after each.
  let making = Promise.resolve();
  const picker = modelPicker({
    onPick: (ref) => {
      menu.close();
      if (ref === shown) {
        return;
      }
      making = making.then(() =>
        attempt(async () => {
          try {
            await choose(ref);
            await refresh();
          } finally {
            paintBox();
          }
        }),
      );
    },
    onMakeDefault: null,
  });
  const menu = anchoredPopover({ anchor: button, frame: element, label, build: () => picker.build({ groups, chosen: shown, fallback: blankModel, blank: none }) });

  function paintBox() {
    const words = shown === "" ? (none ?? "") : modelName(shown);
    const listed = shown === "" || groups.some((group) => group.models.some((model) => model.ref === shown));
    mark.replaceChildren(shown === "" ? "" : providerMark(providerOf(shown)));
    name.textContent = listed ? words : t("views.system.wordsNotListedNow", { words });
    provider.textContent = shown === "" ? "" : providerLabel(providerOf(shown));
    button.setAttribute("aria-label", `${label}: ${name.textContent}`);
    button.title = shown;
  }

  function paint(providers, chosen, fallback) {
    groups = settingGroups(providers, can, takes, refused);
    shown = chosen;
    blankModel = fallback;
    paintBox();
    menu.repaint();
    element.hidden = false;
  }

  function hide() {
    menu.close();
    element.hidden = true;
  }

  return { element, paint, hide, dispose: menu.dispose };
}

const STANDBY_HINT =
  t("views.system.whenATaskSModel");

/**
 * The fallback models (item 478, the standby models; drawn as the Fallbacks section, item 563): the models a run moves to,
 * in their order, when its own model cannot answer, each a numbered row with its arrows and its ×, over a row that adds
 * one of the models that call tools. `choose(refs)` sends the whole list, which the daemon keeps as `[models] standby`,
 * and gives back the models it answered with. `paint(providers, chosen)` draws it again; `hide()` hides it while the
 * models are read. The code review's fallbacks (item 1663) are the same list with words of their own: `title`,
 * `lead`, `noneText`, `addLabel` (its add box's name), `className`, `takes(provider)` (the providers whose models it
 * offers) and `listOf(answer)` (the list the daemon's answer holds).
 */
function standbySetting({ choose, title, lead, noneText, addLabel, className, takes, listOf }) {
  let shown = [];
  // The select lies over the row's words, clear, so that a press anywhere on the row opens its menu.
  const add = el("select", { class: "standby-add-select", "aria-label": addLabel });
  const list = el("ol", { class: "standby-list", "aria-label": t("views.system.titleInTheirOrder", { title }) });
  const none = el("div", { class: "set-row standby-none" }, noneText);
  const adder = el("div", { class: "set-row standby-add" }, el("span", { class: "standby-add-label" }, icon("plus"), addLabel), add);
  const element = setSection({ title, lead, body: setCard(list, none, adder) });
  element.classList.add(className);
  element.hidden = true;
  // Each change is sent once the one before it was answered, and made to the list that answer holds, so changes made
  // in a row all hold; the list is drawn again from the status after each.
  let making = Promise.resolve();
  function send(change) {
    making = making.then(() =>
      attempt(async () => {
        const answered = await choose(change(shown));
        shown = [...listOf(answered)];
        await refresh();
      }),
    );
  }
  add.addEventListener("change", () => {
    const ref = add.value;
    add.value = "";
    if (ref !== "") {
      send((list) => (list.includes(ref) ? list : [...list, ref]));
    }
  });

  /** `list` with `ref` moved `by` places (-1 up, 1 down), as far as the list goes. */
  function moved(list, ref, by) {
    const at = list.indexOf(ref);
    const to = at + by;
    if (at < 0 || to < 0 || to >= list.length) {
      return list;
    }
    const next = [...list];
    next.splice(at, 1);
    next.splice(to, 0, ref);
    return next;
  }

  function arrow(ref, at, by) {
    const up = by < 0;
    return el(
      "button",
      {
        class: "btn ghost sm icon-only standby-move",
        type: "button",
        title: up ? t("views.system.moveUpATaskTries") : t("views.system.moveDownATaskTries"),
        "aria-label": t(up ? "views.system.moveModelUp" : "views.system.moveModelDown", { model: modelName(ref) }),
        disabled: up ? at === 0 : at === shown.length - 1,
        onclick: () => send((list) => moved(list, ref, by)),
      },
      icon(up ? "arrowUp" : "arrowDown"),
    );
  }

  function row(ref, at, providers) {
    const provider = providers.find((one) => one.provider === providerOf(ref)) ?? null;
    const listed = provider?.models.some((model) => model.ref === ref) === true;
    const keyless = provider !== null && provider.problem === "no_key";
    return el(
      "li",
      { class: "set-row standby-row", dataset: { model: ref } },
      el("span", { class: "standby-order" }, String(at + 1)),
      el("span", { class: "grow standby-name" }, el("span", { class: "mono" }, modelName(ref)), el("span", { class: "muted" }, providerLabel(providerOf(ref)))),
      keyless ? el("span", { class: "badge b-muted", title: t("views.system.aTaskSkipsItUntil") }, t("views.system.noKey")) : null,
      !listed && !keyless ? el("span", { class: "badge b-muted", title: t("views.system.itsServiceDoesNotList") }, t("views.system.notListedNow")) : null,
      arrow(ref, at, -1),
      arrow(ref, at, 1),
      el("button", { class: "btn ghost sm icon-only", type: "button", title: t("views.system.takeItOffTheList"), "aria-label": t("views.system.removeModelname", { modelName: modelName(ref) }), onclick: () => send((list) => list.filter((model) => model !== ref)) }, icon("x")),
    );
  }

  function paint(providers, chosen) {
    shown = [...chosen];
    const groups = providers
      // A model that answers only as an agent program of its own (WHOLE_RUN) cannot be a standby model.
      .filter((provider) => takes(provider.provider))
      .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools && !shown.includes(model.ref)) }))
      .filter((group) => group.models.length > 0);
    add.replaceChildren(
      el("option", { value: "" }, t("views.system.addAModel")),
      ...groups.map((group) =>
        el("optgroup", { label: providerLabel(group.provider) }, group.models.map((model) => el("option", { value: model.ref }, modelName(model.ref)))),
      ),
    );
    add.value = "";
    list.replaceChildren(...shown.map((ref, at) => row(ref, at, providers)));
    list.hidden = shown.length === 0;
    none.hidden = shown.length > 0;
    element.hidden = false;
  }

  function hide() {
    element.hidden = true;
  }

  return { element, paint, hide };
}

// How often Import from Chrome looks again whether your Chrome was quit (item 917).
const IMPORT_LOOK_MS = 3000;

/** The Models page's sections: the roles, the fallbacks and every model by provider. `asked` is the model an address
 * asked for (`#/settings/models?model=<ref>`, the context meter's link, item 396), "" for none: once listed, its provider's
 * fold opens and its row comes into sight, marked, with the focus on its context size. `head` is the page's head: what the
 * providers hold stands in it, and the button that asks them again. */
function buildModels(isDisposed, asked, head) {
  let toShow = asked;
  const list = el("div", { class: "models-providers" }, inlineLoading());
  const main = modelSetting({
    label: t("views.system.defaultModel"),
    hint: t("views.system.newChatsUseItWhen"),
    className: "models-default",
    can: (model) => model.tools,
    takes: () => true,
    refused: t("views.system.aTaskSModelTakes"),
    none: null,
    choose: async (ref) => {
      await api.post("/models/main", { model: ref });
      toast("ok", t("views.system.newChatsStartOnModelname", { modelName: modelName(ref) }));
    },
  });
  const kinds = [
    { kind: "security", label: t("views.system.securityResearchModel"), hint: t("views.system.securityWorkThatNamesA") },
    { kind: "quick", label: t("views.system.quickTaskModel"), hint: t("views.system.aShortSingleLookupOr") },
    { kind: "deep", label: t("views.system.deepTaskModel"), hint: t("views.system.anExplicitInvestigationAnalysisOr") },
    { kind: "writing", label: t("views.system.writingModel"), hint: t("views.system.draftingOrEditingCopyDocuments") },
    { kind: "code", label: t("views.system.codingModel"), hint: t("views.system.implementingChangesToARepository") },
  ].map(({ kind, label, hint }) => ({
    kind,
    control: modelSetting({
      label,
      hint,
      className: "models-role",
      can: (model) => model.tools,
      takes: () => true,
      refused: t("views.system.aTaskNeedsAModel"),
      none: kind === "security" ? t("views.system.defaultModel") : t("views.system.usualModel"),
      choose: async (ref) => {
        await api.put("/models/kinds", { kind, model: ref });
        toast("ok", ref === "" ? t("views.system.taskModelCleared") : t("views.system.labelSetToModelname", { label, modelName: modelName(ref) }));
      },
    }),
  }));
  const alternateCheck = modelSetting({
    label: t("views.system.otherFamilyCheckModel"),
    hint: t("views.system.checksTaskGoalsAndCompleted"),
    className: "models-role",
    can: (model) => model.tools && model.family != null,
    takes: (provider) => answersRole("review", provider),
    refused: t("views.system.thisServiceSModelsCannot"),
    none: t("views.system.off"),
    choose: async (ref) => {
      await api.put("/models/check-other-family", { model: ref });
      toast("ok", ref === "" ? t("views.system.otherFamilyChecksTurnedOff") : t("views.system.otherFamilyChecksUseModelname", { modelName: modelName(ref) }));
    },
  });
  const roles = ROLE_SETTINGS.map((setting) => ({
    role: setting.role,
    control: modelSetting({
      label: setting.label,
      hint: setting.hint,
      className: "models-role",
      can: setting.can,
      takes: setting.takes,
      refused: setting.refused,
      none: null,
      choose: async (ref) => {
        await api.post("/models/roles", { role: setting.role, model: ref });
        toast("ok", t("views.system.theTolowercaseIsModelnameFrom", { toLowerCase: setting.label.toLowerCase(), modelName: modelName(ref) }));
      },
    }),
  }));
  // The model that looks for work on Dot's own initiative (item 916): kept as [initiative] look_model, "" for the
  // default model, and read from the daemon's view of its initiative rather than its status: null until it is read, and
  // the row stays hidden then, so that a view that cannot be read hides the row alone, not the page.
  let lookModel = null;
  const lookFailed = el("div", { class: "set-row models-look-failed", hidden: true });
  const look = modelSetting({
    label: t("views.system.ownWorkModel"),
    hint: t("views.system.decidesWhatIsWorthDoing"),
    className: "models-role",
    // The look is a run with tools OpenCode's agent is not given (its list, the owner's mail); the Claude plan's run has
    // every tool (items 916, 1032 and 1063).
    can: (model) => model.tools,
    takes: (provider) => runsDotsTools(provider),
    refused: t("views.system.itsLookUsesToolsIts"),
    none: t("views.system.defaultModel"),
    choose: async (ref) => {
      const view = await api.put("/initiative/look-model", { model: ref });
      paintLook(view.look_model);
      toast("ok", ref === "" ? t("views.system.dotSOwnWorkIs") : t("views.system.dotSOwnWorkIs2", { modelName: modelName(ref) }));
    },
  });
  // The roles of the night's learning (item 1033), read on their own as the look is: "" plays a role on the review
  // model, and null hides the rows until they are read.
  let learningChosen = null;
  const learningFailed = el("div", { class: "set-row models-look-failed", hidden: true });
  const learning = LEARNING_SETTINGS.map((setting) => ({
    role: setting.role,
    control: modelSetting({
      label: setting.label,
      hint: setting.hint,
      className: "models-role",
      can: (model) => model.tools,
      takes: (provider) => answersRole("review", provider),
      refused: t("views.system.ofTheAgentProgramsOnly"),
      none: t("views.system.reviewModel"),
      choose: async (ref) => {
        paintLearning(await api.put("/models/learning", { role: setting.role, model: ref }));
        const what = setting.label.toLowerCase();
        toast("ok", ref === "" ? t("views.system.theWhatPlaysOnThe", { what }) : t("views.system.theWhatIsModelnameFrom", { what, modelName: modelName(ref) }));
      },
    }),
  }));
  // Self-repair's model (item 933), Claude Code's own: the same choice as Settings › Self-repair makes.
  let worker = null;
  const repairSelect = el("select", { class: "select sm", "aria-label": t("views.system.selfRepairModel") }, ...REPAIR_MODELS.map((choice) => el("option", { value: choice.value }, choice.label)));
  const repairRow = setRow({ title: t("views.system.selfRepairModel"), lead: t("views.system.theModelOfYourClaude"), control: repairSelect });
  repairRow.hidden = true;
  repairSelect.addEventListener("change", () =>
    attempt(async () => {
      try {
        const view = await api.put("/repair/worker", { model: repairSelect.value, effort: worker.effort, fast: worker.fast });
        worker = view.worker;
        toast("ok", t("views.system.selfRepairWorksOnValue", { value: REPAIR_MODELS.find((choice) => choice.value === worker.model)?.label ?? worker.model }));
      } finally {
        repairSelect.value = worker.model;
      }
    }),
  );
  // One press puts the review model, and with it the night's roles, on Opus of the owner's Claude plan (item 1033).
  const planButton = el("button", { class: "btn sm", type: "button" }, t("views.system.useMyClaudePlan"));
  const planRow = setRow({
    title: t("views.system.yourClaudePlan"),
    lead: t("views.system.putsTheReviewModelAnd"),
    control: planButton,
  });
  planRow.hidden = true;
  planButton.addEventListener("click", () =>
    attempt(async () => {
      // The roles first: one that fails leaves the review model as it was, and the roles already on it follow it.
      for (const { role } of LEARNING_SETTINGS) {
        paintLearning(await api.put("/models/learning", { role, model: "" }));
      }
      await api.post("/models/roles", { role: "review", model: PLAN_REVIEW });
      await refresh();
      toast("ok", t("views.system.theReviewModelAndThe"));
    }),
  );
  // What the schedules and the agents run on, each chosen on its own page: counted here, so that this page says where
  // every model of Dot's is.
  const usage = el("div", { class: "set-row hint models-usage", hidden: true });
  const standby = standbySetting({
    title: t("views.system.fallbacks"),
    lead: STANDBY_HINT,
    noneText: t("views.system.noneATaskWhoseModel"),
    addLabel: t("views.system.addAFallbackModel"),
    className: "models-standby",
    takes: (provider) => !WHOLE_RUN.has(provider),
    listOf: (answered) => answered.standby,
    choose: async (refs) => {
      const answered = await api.put("/models/standby", { models: refs });
      toast("ok", refs.length === 0 ? t("views.system.aTaskWhoseModelCannot") : t("views.system.taskMovesTo", { models: refs.map(modelName).join(t("views.system.thenSeparator")) }));
      return answered;
    },
  });
  // The pre-push review of Dot's own repository (item 1663, `[code_review]`): read on its own, as the look is; null
  // hides its rows until it is read. Every change sends the whole table, which the daemon writes all or not at all.
  let review = null;
  const reviewFailed = el("div", { class: "set-row models-look-failed", hidden: true });
  async function saveReview(change) {
    const answered = await api.put("/code-review", {
      model: review.model,
      fallback: review.fallback,
      small_model: review.small_model,
      small_max_lines: review.small_max_lines,
      max_minutes: review.max_minutes,
      ...change,
    });
    paintReview(answered);
    return answered;
  }
  const REVIEW_REFUSED = t("views.system.aReviewIsARun");
  const reviewModel = modelSetting({
    label: t("views.system.codeReviewModel"),
    hint: t("views.system.reviewsEachChangeOfDot"),
    className: "models-review",
    can: (model) => model.tools,
    takes: (provider) => runsDotsTools(provider),
    refused: REVIEW_REFUSED,
    none: null,
    choose: async (ref) => {
      await saveReview({ model: ref, fallback: review.fallback.filter((one) => one !== ref) });
      toast("ok", t("views.system.theNextCodeReviewRuns", { modelName: modelName(ref) }));
    },
  });
  const smallModel = modelSetting({
    label: t("views.system.smallChangeModel"),
    hint: t("views.system.reviewsASmallChangeFirst"),
    className: "models-review",
    can: (model) => model.tools,
    takes: (provider) => runsDotsTools(provider),
    refused: REVIEW_REFUSED,
    none: t("views.system.codeReviewModel"),
    choose: async (ref) => {
      await saveReview({ small_model: ref });
      toast("ok", ref === "" ? t("views.system.smallChangesAreReviewedOn") : t("views.system.smallChangesAreReviewedOn2", { modelName: modelName(ref) }));
    },
  });
  const smallLines = el("input", { class: "input", type: "number", min: "0", max: "100000", step: "1", inputmode: "numeric", "aria-label": t("views.system.linesASmallChangeHas") });
  const maxMinutes = el("input", { class: "input", type: "number", min: "1", max: "240", step: "1", inputmode: "numeric", "aria-label": t("views.system.minutesAReviewMayTake") });
  const linesRow = setRow({ title: t("views.system.smallChange"), lead: t("views.system.changedLinesAddedAndRemoved"), control: smallLines });
  const minutesRow = setRow({ title: t("views.system.timeLimit"), lead: t("views.system.minutesAPushSReview"), control: maxMinutes });
  for (const [input, key] of [
    [smallLines, "small_max_lines"],
    [maxMinutes, "max_minutes"],
  ]) {
    input.addEventListener("change", () =>
      attempt(async () => {
        const value = Number(input.value);
        try {
          await saveReview({ [key]: value });
          toast("ok", key === "max_minutes" ? t("views.system.aCodeReviewStopsAfter", { value }) : t("views.system.aChangeOfAtMost", { value }));
        } finally {
          input.value = String(review[key]);
        }
      }),
    );
  }
  const reviewFallbacks = standbySetting({
    title: t("views.system.codeReviewFallbacks"),
    lead: t("views.system.whenTheReviewModelGives"),
    noneText: t("views.system.noneAChangeTheReview"),
    addLabel: t("views.system.addACodeReviewFallback"),
    className: "models-review-fallbacks",
    takes: (provider) => runsDotsTools(provider),
    listOf: (answered) => answered.fallback,
    choose: async (refs) => {
      const answered = await saveReview({ fallback: refs });
      toast("ok", refs.length === 0 ? t("views.system.aCodeReviewHasNo") : t("views.system.reviewFallsBackTo", { models: refs.map(modelName).join(t("views.system.thenSeparator")) }));
      return answered;
    },
  });
  const reviewRows = [linesRow, minutesRow];
  for (const row of reviewRows) {
    row.hidden = true;
  }
  // Every model by provider: a search over the folds of the providers, which keep the tables of their models.
  const search = el("input", { class: "input", type: "search", placeholder: t("views.system.searchModels"), "aria-label": t("views.system.searchModels"), autocomplete: "off", spellcheck: "false" });
  const noMatch = el("div", { class: "set-row hint", hidden: true }, t("views.system.noModelHasThatIn"));
  const element = el(
    "div",
    { class: "set-sections" },
    setSection({ title: t("views.system.roles"), body: setCard(planRow, main.element, ...roles.map(({ control }) => control.element), look.element, lookFailed, alternateCheck.element, usage) }),
    setSection({ title: t("views.system.taskModels"), lead: t("views.system.usedWhenYouHavenT"), body: setCard(...kinds.map(({ control }) => control.element)) }),
    setSection({ title: t("views.system.nightSLearningAndSelf"), body: setCard(...learning.map(({ control }) => control.element), learningFailed, repairRow) }),
    standby.element,
    setSection({ title: t("views.system.codeReview"), body: setCard(reviewModel.element, smallModel.element, ...reviewRows, reviewFailed) }),
    reviewFallbacks.element,
    setSection({ title: t("views.system.everyModelByService"), body: setCard(el("div", { class: "set-row models-search" }, el("span", { class: "models-search-box" }, icon("search"), search)), list, noMatch) }),
  );
  let providers = null;
  const reload = el(
    "button",
    { class: "btn", type: "button", title: t("views.system.askTheServicesForTheir"), onclick: () => attempt(load) },
    icon("refresh"),
    t("views.system.checkAgain"),
  );
  head.setActions(reload);
  let painted = null;

  // Called on every status update; the tables are only rebuilt when the list, the default model or the providers the
  // configured models run on changed, so a menu that is open stays open.
  function paint() {
    if (providers === null) {
      return;
    }
    const status = getSnapshot().status;
    const mainModel = status === null ? "" : status.models.main;
    const kindModels = kinds.map(({ kind }) => status?.models.kinds?.[kind] ?? "");
    const checkModel = status?.models.check_other_family ?? "";
    const chosen = JSON.stringify([...roles.map(({ role }) => status?.models[role] ?? ""), ...kindModels, checkModel]);
    const standbyModels = status === null ? [] : status.models.standby;
    const standbyKey = standbyModels.join(" ");
    const inUse = providersInUse(status);
    const usage = inUse.join(" ");
    if (
      painted !== null &&
      painted.providers === providers &&
      painted.mainModel === mainModel &&
      painted.chosen === chosen &&
      painted.standbyKey === standbyKey &&
      painted.usage === usage
    ) {
      return;
    }
    painted = { providers, mainModel, chosen, standbyKey, usage };
    head.setMeta(...modelsSummary(providers, inUse));
    search.placeholder = t("views.system.searchPart", { part: tCount(providers.reduce((count, provider) => count + provider.models.length, 0), "views.system.countModel", "views.system.countModels") });
    main.paint(providers, mainModel, null);
    alternateCheck.paint(providers, checkModel, null);
    for (const [index, { control }] of kinds.entries()) {
      control.paint(providers, kindModels[index], null);
    }
    for (const { role, control } of roles) {
      control.paint(providers, status === null ? "" : status.models[role], null);
    }
    if (lookModel !== null) {
      look.paint(providers, lookModel, mainModel === "" ? null : mainModel);
    }
    paintLearningRows();
    planRow.hidden = !providers.some((provider) => provider.models.some((model) => model.ref === PLAN_REVIEW));
    standby.paint(providers, standbyModels);
    paintReviewRows();
    list.replaceChildren(...providers.map((provider) => providerBlock(provider, mainModel, inUse.includes(provider.provider), remember)));
    cueTables();
    showAsked();
    findModels();
  }

  /** Show the models whose names hold what is typed in the search, the providers that have none out of sight, and a fold open while it has a match (what the owner folded stays as they left it). */
  function findModels() {
    const needle = search.value.trim().toLowerCase();
    let shown = 0;
    for (const group of list.querySelectorAll(".fold-group")) {
      const rows = [...group.querySelectorAll("tr[data-model]")];
      let matches = 0;
      for (const row of rows) {
        const matched = needle === "" || row.dataset.model.toLowerCase().includes(needle);
        const why = row.nextElementSibling?.classList.contains("context-why") ? row.nextElementSibling : null;
        row.classList.toggle("model-filtered", !matched);
        why?.classList.toggle("model-filtered", !matched);
        matches += matched ? 1 : 0;
      }
      group.classList.toggle("model-filtered", needle !== "" && matches === 0);
      shown += needle === "" || matches > 0 ? 1 : 0;
      const body = group.querySelector(":scope > .fold-body");
      if (body === null) {
        continue;
      }
      if (needle !== "" && matches > 0 && body.hidden) {
        body.dataset.searched = "";
        body.hidden = false;
      } else if ((needle === "" || matches === 0) && "searched" in body.dataset) {
        delete body.dataset.searched;
        body.hidden = true;
      }
    }
    noMatch.hidden = needle === "" || shown > 0 || providers === null;
  }
  search.addEventListener("input", findModels);

  // The asked model's row, once: a later paint leaves the page where the owner took it.
  function showAsked() {
    const row = [...list.querySelectorAll("tr[data-model]")].find((node) => node.dataset.model === toShow);
    if (row === undefined) {
      return;
    }
    toShow = "";
    const fold = row.closest(".fold-group");
    if (fold !== null && fold.dataset.open === "false") {
      fold.querySelector(".fold-toggle").click();
    }
    row.dataset.asked = "";
    // The size's menu, or its Why where the window allows no other size; on a phone the table scrolls sideways to it.
    const control = row.querySelector(".context-cell select:not([hidden])") ?? row.querySelector(".context-cell button");
    (control ?? row).scrollIntoView({ block: "center", inline: "nearest" });
    control?.focus({ preventScroll: true });
  }

  // A context size chosen in a model's row (item 250): the list keeps the model as the daemon answered, so the tables
  // built again later show it; the rows built now show it already.
  function remember(updated) {
    providers = providers.map((provider) => ({ ...provider, models: provider.models.map((model) => (model.ref === updated.ref ? updated : model)) }));
    if (painted !== null) {
      painted = { ...painted, providers };
    }
  }

  /** Keep the code review's table as the daemon answered, and draw it once the list is here. */
  function paintReview(view) {
    review = view;
    paintReviewRows();
  }

  function paintReviewRows() {
    if (providers === null || review === null) {
      return;
    }
    reviewModel.paint(providers, review.model, null);
    smallModel.paint(providers, review.small_model, review.model);
    reviewFallbacks.paint(providers, review.fallback);
    smallLines.value = String(review.small_max_lines);
    maxMinutes.value = String(review.max_minutes);
    for (const row of reviewRows) {
      row.hidden = false;
    }
  }

  /** The code review's table, read on its own: a failure stands in its place with a way to read it again. */
  async function loadReview() {
    reviewFailed.hidden = true;
    try {
      const view = await api.get("/code-review");
      if (!isDisposed()) {
        paintReview(view);
      }
    } catch (error) {
      if (!isDisposed()) {
        console.error("the models of the code review could not be read", error);
        reviewFailed.replaceChildren(inlineError(error, () => attempt(loadReview)));
        reviewFailed.hidden = false;
      }
    }
  }

  /** Keep the roles of the night's learning as the daemon answered, and draw them once the list is here. */
  function paintLearning(view) {
    learningChosen = view;
    paintLearningRows();
  }

  function paintLearningRows() {
    if (providers === null || learningChosen === null) {
      return;
    }
    // The review model as the status says it now, which a choice of its row changes after the roles were read.
    const status = getSnapshot().status;
    const review = status === null ? learningChosen.review : status.models.review;
    for (const { role, control } of learning) {
      control.paint(providers, learningChosen[role], review);
    }
  }

  /** The roles of the night's learning, read on their own: a failure stands in their place with a way to read them
   * again, and leaves the rest of the page as it is. */
  async function loadLearning() {
    learningFailed.hidden = true;
    try {
      const view = await api.get("/models/learning");
      if (!isDisposed()) {
        paintLearning(view);
      }
    } catch (error) {
      if (!isDisposed()) {
        console.error("the models of the night's learning could not be read", error);
        learningFailed.replaceChildren(inlineError(error, () => attempt(loadLearning)));
        learningFailed.hidden = false;
      }
    }
  }

  /** Self-repair's model, read from its page's view; the row stays hidden when it cannot be read. */
  async function loadRepair() {
    const view = await api.get("/repair");
    if (isDisposed()) {
      return;
    }
    worker = view.worker;
    repairSelect.value = worker.model;
    repairRow.hidden = false;
  }

  /** How many schedules and agents run on each model, "the default model" for those that name none. */
  async function loadUsage() {
    const [schedules, agents] = await Promise.all([api.get("/schedules"), api.get("/agents")]);
    if (isDisposed()) {
      return;
    }
    const count = (models) => {
      const counts = new Map();
      for (const model of models) {
        const name = model === null || model === "" ? t("views.system.theDefaultModel") : modelName(model);
        counts.set(name, (counts.get(name) ?? 0) + 1);
      }
      // The most used first, then by name: the order the store lists them in says nothing.
      const ordered = [...counts].sort(([one, oneMany], [other, otherMany]) => otherMany - oneMany || one.localeCompare(other));
      return ordered.map(([name, many]) => `${many} on ${name}`).join(", ");
    };
    // A schedule names its model in its options; one that names none runs on its agent's, and on the default model
    // when it has no agent or its agent names none either (scheduler/runner.py).
    const agentModels = new Map(agents.map((agent) => [agent.record.id, agent.record.model]));
    const scheduled = schedules.map((schedule) => {
      const options = schedule.record.options;
      return options.model ?? (options.agent_id === null ? null : (agentModels.get(options.agent_id) ?? null));
    });
    const made = agents.filter((agent) => agent.record.id !== DOT_ID).map((agent) => agent.record.model);
    usage.replaceChildren(
      el("span", null, scheduled.length === 0 ? t("views.system.noSchedules") : t("views.system.schedulesCount", { count: count(scheduled) })),
      el("span", null, made.length === 0 ? t("views.system.noAgents") : t("views.system.agentsCount", { count: count(made) })),
      el("span", null, t("views.system.eachIsChosenOnIts")),
      el("a", { href: "#/settings/schedules" }, t("views.system.scheduledTasks")),
      el("span", null, ", "),
      el("a", { href: "#/settings/agents" }, t("views.system.agents")),
      el("span", null, "."),
    );
    usage.hidden = false;
  }

  /** Draw the row of the model that looks for work alone, with `model` chosen, once the list is here: drawing the page
   * again would take the focus off the model an address asked for. */
  function paintLook(model) {
    lookModel = model;
    if (providers !== null) {
      const status = getSnapshot().status;
      look.paint(providers, lookModel, status === null ? null : status.models.main);
    }
  }

  /** The model that looks for work, read on its own: a failure stands in its place with a way to read it again, and
   * leaves the rest of the page as it is. */
  async function loadLook() {
    lookFailed.hidden = true;
    try {
      const view = await api.get("/initiative");
      if (typeof view.look_model !== "string") {
        throw new Error("the daemon's answer names no model that looks for work");
      }
      if (!isDisposed()) {
        paintLook(view.look_model);
      }
    } catch (error) {
      if (!isDisposed()) {
        console.error("the model that looks for work could not be read", error);
        lookFailed.replaceChildren(inlineError(error, () => attempt(loadLook)));
        lookFailed.hidden = false;
      }
    }
  }

  async function load() {
    // The list read again is drawn once it is here: a paint before it (the model that looks for work, read beside it)
    // draws nothing from the list before it (the pre-push review of item 916).
    providers = null;
    lookModel = null;
    learningChosen = null;
    review = null;
    attempt(loadLook);
    attempt(loadLearning);
    attempt(loadReview);
    attempt(loadRepair);
    attempt(loadUsage);
    forgetModels();
    painted = null;
    main.hide();
    for (const { control } of roles) {
      control.hide();
    }
    look.hide();
    for (const { control } of learning) {
      control.hide();
    }
    standby.hide();
    reviewModel.hide();
    smallModel.hide();
    reviewFallbacks.hide();
    for (const row of reviewRows) {
      row.hidden = true;
    }
    list.replaceChildren(inlineLoading());
    head.setMeta();
    try {
      const found = await loadModels();
      if (isDisposed()) {
        return;
      }
      providers = found;
      paint();
    } catch (error) {
      if (!isDisposed()) {
        console.error("the models could not be listed", error);
        head.setMeta(el("span", { class: "badge b-bad" }, t("views.system.couldNotList")));
        list.replaceChildren(inlineError(error, () => attempt(load)));
      }
    }
  }

  // On a phone the tables scroll sideways: a cue on each says while more lies to the right. Leaving the page stops
  // them all, and starts none.
  let cues = [];
  function stopCues() {
    for (const stop of cues) {
      stop();
    }
    cues = [];
  }
  function cueTables() {
    stopCues();
    cues = [...list.querySelectorAll(".table-scroll")].map(scrollCue);
  }

  // Leaving the page closes a model menu left open, and with it what it listens to on the document.
  function dispose() {
    stopCues();
    for (const setting of [main, look, reviewModel, smallModel, ...roles.map(({ control }) => control), ...learning.map(({ control }) => control)]) {
      setting.dispose();
    }
  }

  return { element, load, paint, dispose };
}

function buildTokenCard() {
  async function forgetToken() {
    const confirmed = await confirmDialog({
      title: t("views.system.signThisBrowserOut"),
      message: t("views.system.thisBrowserWillBeSigned"),
      confirmLabel: t("views.system.signOut"),
      danger: true,
      focusCancel: false,
    });
    if (confirmed) {
      clearToken();
      location.reload();
    }
  }

  return el(
    "section",
    { class: "card card-pad row" },
    el("div", { class: "grow" }, t("views.system.thisBrowserIsSignedIn")),
    el("button", { class: "btn", type: "button", onclick: () => attempt(forgetToken) }, t("views.system.signOutOfThisBrowser")),
  );
}

const BROWSER_MODES = [
  { id: "own", label: t("views.system.ownChrome"), hint: t("views.system.aChromeProfileOfDot") },
  { id: "attach", label: t("views.system.myChrome"), hint: t("views.system.yourChromeWithYourProfiles") },
];
// The sizes a page of Dot's own Chrome may have (item 354): its window's, or a screen's.
const PAGE_SIZES = [
  { id: "window", label: t("views.system.itsWindow"), fit: true, width: 0, height: 0 },
  { id: "1280x860", label: "1280 × 860", fit: false, width: 1280, height: 860 },
  { id: "1440x900", label: "1440 × 900", fit: false, width: 1440, height: 900 },
  { id: "1024x768", label: "1024 × 768", fit: false, width: 1024, height: 768 },
  { id: "820x1180", label: t("views.system.8201180ATabletS"), fit: false, width: 820, height: 1180 },
  { id: "390x844", label: t("views.system.390844APhoneS"), fit: false, width: 390, height: 844 },
];
// The names of the two profiles every Dot has (item 355); an added one is called by its own name.
const PROFILE_KINDS = { default: t("views.system.default"), incognito: t("views.system.incognito") };
// The colour scheme pages are told to prefer (item 354).
const APPEARANCES = [
  { id: "system", label: t("views.system.system"), hint: t("views.system.theMacS") },
  { id: "light", label: t("views.system.light"), hint: t("views.system.lightWhateverTheMacShows") },
  { id: "dark", label: t("views.system.dark"), hint: t("views.system.darkWhateverTheMacShows") },
];
// The zoom Dot's own Chrome draws pages at (item 358), in percent, as Chrome's own settings offer it.
const ZOOMS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200];

/**
 * Which Chrome the agent browses with. Switching to "My Chrome" needs Chrome to allow remote debugging, which only the
 * user can switch on; the card shows what Chrome allows right now and how to change it. A Chrome that is closed is
 * opened by the switch (item 261), and while the switch is under way the card says what it waits for. The DevTools
 * port (item 265) has Dot's own Chrome take connections from a run's scripts too. With Dot's own Chrome the card opens
 * its profile in a plain Chrome window, where the owner signs in to sites (item 344).
 */
// How often the browser card reads again how the Chrome of the user stands, while it is open (item 341).
const BROWSER_POLL_MS = 3000;

/** How Dot stands with the Chrome of the user (item 341): what the card's connection says, and the one line that says
 * what to do, with its tone; null in Dot's own Chrome. */
function attachStanding(view) {
  if (view.mode !== "attach") {
    return null;
  }
  if (view.connected) {
    return { word: "connected", line: t("views.system.dotIsConnectedToYour"), tone: "ok" };
  }
  if (view.waiting_allow) {
    return { word: t("views.system.waitingForAllow"), line: t("views.system.chromeAsksWhetherDotMay"), tone: "warn" };
  }
  if (!view.chrome_running) {
    return { word: t("views.system.notConnected"), line: t("views.system.chromeIsClosedOpenIt"), tone: "warn" };
  }
  if (!view.remote_debugging) {
    return { word: t("views.system.notConnected"), line: t("views.system.remoteDebuggingIsOffIn", { inspect_page: view.inspect_page }), tone: "warn" };
  }
  if (view.lost) {
    return { word: t("views.system.chromeRestarted"), line: t("views.system.chromeWasClosedOrRestarted"), tone: "warn" };
  }
  return { word: t("views.system.notConnected"), line: t("views.system.dotConnectsOnTheNext"), tone: "" };
}

function buildBrowser(isDisposed, head) {
  let view = null;
  let polling = null;
  // Whether the card last saw the sign-in window open, so that its closing is told once.
  let signing = false;
  const facts = el("dl", { class: "kv kv-pairs" }, inlineLoading());
  const standing = el("div", { class: "note attach-standing", "aria-live": "polite", hidden: true }, icon("plug"), el("span"));
  const problem = el("div", { class: "note bad", hidden: true }, icon("alert"), el("span"));
  const waiting = el(
    "div",
    { class: "note", role: "status", hidden: true },
    icon("clock"),
    el("span", null, t("views.system.connectingToYourChromeDot")),
  );
  const steps = el("ol", { class: "browser-steps" });
  const modes = segmented(BROWSER_MODES, BROWSER_MODES[0].id, (id) => attempt(() => choose(id)));
  const raise = switchRowSetting({
    label: t("views.system.bringChromeToTheFront"),
    hint:
      t("views.system.offDotSChromeStays"),
    read: () => view?.raise_window === true,
    // The words follow the change once it is made (item 559): a switch that failed says so alone.
    write: async (on) => {
      view = await api.post("/browser/raise", { raise_window: on });
      toast("ok", view.raise_window ? t("views.system.chromeComesToTheFront") : t("views.system.chromeStaysInTheBackground"));
    },
    onToggled: () => {
      if (!isDisposed()) {
        raise.paint();
      }
    },
  });
  // Whether the owner may use the page of a run they hold from the screen that shows it, a phone's too (item 470).
  const screenInput = switchRowSetting({
    label: t("views.system.useThePageFromThe"),
    hint: t("views.system.onATaskYouTook"),
    read: () => view?.remote_input === true,
    write: async (on) => {
      view = await api.post("/browser/remote-input", { remote_input: on });
      toast("ok", view.remote_input ? t("views.system.aTaskYouHoldCan") : t("views.system.aTaskYouHoldIs"));
    },
    onToggled: () => {
      if (!isDisposed()) {
        screenInput.paint();
      }
    },
  });
  // Settings › Browser (item 354): whether runs may use the browser, how its pages look, and whether a run's page shows
  // by itself.
  const enabled = switchRowSetting({
    label: t("views.system.tasksMayUseTheBrowser"),
    hint: t("views.system.offNoTaskIsOffered"),
    read: () => view?.enabled === true,
    write: async (on) => {
      view = await api.post("/browser/enabled", { enabled: on });
      toast("ok", view.enabled ? t("views.system.tasksMayUseTheBrowser2") : t("views.system.tasksDoNotUseThe"));
    },
    onToggled: () => {
      if (!isDisposed()) {
        enabled.paint();
      }
    },
  });
  const sizes = el("select", { class: "select sm", "aria-label": t("views.system.sizeOfAPage") });
  sizes.addEventListener("change", () => attempt(() => chooseSize(sizes.value)));
  const sizeSetting = setRow({
    title: t("views.system.sizeOfAPage"),
    lead: t("views.system.thePageATaskSees"),
    control: sizes,
  });
  const appearance = choiceRowSetting({
    label: t("views.system.appearanceOfPages"),
    hint: t("views.system.theColourSchemePagesAre"),
    options: APPEARANCES,
    selected: "system",
    onSelect: (id) => attempt(() => chooseLook({ ...view.look, appearance: id })),
  });
  const zooms = el("select", { class: "select sm", "aria-label": t("views.system.zoomOfPages") });
  zooms.addEventListener("change", () => attempt(() => chooseLook({ ...view.look, zoom: Number(zooms.value) })));
  const zoomSetting = setRow({
    title: t("views.system.zoomOfPages"),
    lead: t("views.system.howLargeDotSOwn"),
    control: zooms,
  });
  const browseWith = setRow({
    title: t("views.system.browseWith"),
    lead: t("views.system.ownChromeIsAProfile"),
    control: modes.element,
  });
  const autoComputer = switchRowSetting({
    label: t("views.system.showDotSComputerWhen"),
    hint: t("views.system.onAWideWindowThe"),
    read: () => settingValue(AUTO_COMPUTER) === "shown",
    write: async (on) => setValue(AUTO_COMPUTER, on ? "shown" : "hidden"),
    onToggled: () => autoComputer.paint(),
  });
  // The profiles of Dot's own Chrome (item 355): Default, Incognito and the ones added; runs work on
  // the one in use.
  const profileList = el("div", { class: "profile-list", role: "list", "aria-label": t("views.system.profiles") });
  // Add profile asks for the new profile's name in a dialog (item 615).
  const addButton = el(
    "button",
    {
      class: "set-link",
      type: "button",
      title: t("views.system.addAProfileOfDot"),
      "aria-label": t("views.system.addProfile"),
      "aria-haspopup": "dialog",
      onclick: () => openAddProfile(),
    },
    icon("plus"),
    el("span", { class: "label" }, t("views.system.addProfile")),
  );
  // Import from Chrome (item 917): the sign-ins of a profile of your Chrome copied into one of Dot's, in a dialog.
  const importButton = el(
    "button",
    {
      class: "set-link",
      type: "button",
      title: t("views.system.copyTheSignInsOf"),
      "aria-label": t("views.system.importFromChrome"),
      "aria-haspopup": "dialog",
      onclick: () => attempt(openImportChrome),
    },
    icon("download"),
    el("span", { class: "label" }, t("views.system.importFromChrome")),
  );
  const profileSetting = setSection({
    title: t("views.system.profiles"),
    lead: t("views.system.eachProfileOfDotS"),
    actions: el("div", { class: "profile-actions" }, importButton, addButton),
    body: setCard(profileList),
  });
  profileSetting.hidden = true;
  const port = el("input", {
    class: "input port-input mono",
    type: "text",
    inputmode: "numeric",
    maxlength: "5",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "0",
    disabled: true,
    "aria-label": t("views.system.devtoolsPort"),
  });
  port.addEventListener("change", () => attempt(() => choosePort(port.value)));
  port.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      port.blur();
    }
  });
  const portHint = el("span");
  const portSetting = setRow({ title: t("views.system.devtoolsPortOfDotS"), lead: portHint, control: port });
  const signIn = el(
    "button",
    {
      class: "btn",
      type: "button",
      title: t("views.system.signInToSitesIn"),
      "aria-label": t("views.system.signIn"),
      onclick: () => attempt(openSignIn),
    },
    icon("key"),
    el("span", { class: "label" }, t("views.system.signIn")),
  );
  const signInSetting = setRow({
    title: t("views.system.signInToSitesIn"),
    lead: t("views.system.opensDotSOwnProfile"),
    control: signIn,
  });
  signInSetting.hidden = true;
  const signingIn = el(
    "div",
    { class: "note signing-in", "aria-live": "polite", hidden: true },
    icon("key"),
    el("span", null, t("views.system.chromeIsOpenOnDot")),
  );
  const reload = el(
    "button",
    { class: "btn", type: "button", title: t("views.system.lookAtChromeAgain"), "aria-label": t("views.system.lookAgain"), onclick: () => attempt(load) },
    icon("refresh"),
    el("span", { class: "label" }, t("views.system.lookAgain")),
  );
  head.setActions(reload);

  function complain(message) {
    problem.lastChild.textContent = message;
    problem.hidden = false;
  }

  function copyStep() {
    return el(
      "li",
      null,
      t("views.system.openThisAddressInChrome"),
      el(
        "div",
        { class: "browser-copy" },
        el("code", { class: "code-line" }, view.inspect_page),
        el(
          "button",
          {
            class: "btn sm",
            type: "button",
            onclick: () =>
              attempt(async () => {
                await copyText(view.inspect_page);
                toast("ok", t("views.system.addressCopied"));
              }),
          },
          icon("copy"),
          t("views.system.copy"),
        ),
      ),
    );
  }

  // The port as the daemon has it, and where a run's scripts reach Dot's Chrome, or why they do not.
  function paintPort() {
    port.value = String(view.debug_port);
    port.disabled = false;
    if (view.devtools_address !== null) {
      portHint.replaceChildren(
        t("views.system.aTaskSScriptsDrive"),
        el("code", { class: "mono" }, view.devtools_address),
        t("views.system.withTheChromeDevtoolsProtocol"),
      );
    } else if (view.debug_port !== 0) {
      portHint.textContent =
        t("views.system.tasksThatWorkInDot");
    } else {
      portHint.textContent =
        t("views.system.aPortOfThisMac");
    }
  }

  async function choosePort(text) {
    if (view === null) {
      return;
    }
    const wanted = text.trim() === "" ? 0 : Number(text.trim());
    if (!Number.isInteger(wanted) || (wanted !== 0 && (wanted < 1024 || wanted > 65535))) {
      paintPort();
      toast("error", t("views.system.aPortIsAWhole"));
      return;
    }
    if (wanted === view.debug_port) {
      paintPort();
      return;
    }
    port.disabled = true;
    let changed;
    try {
      changed = await api.post("/browser/debug-port", { debug_port: wanted });
    } catch (error) {
      if (!isDisposed()) {
        paintPort();
      }
      throw error;
    }
    if (isDisposed()) {
      return;
    }
    view = changed;
    paint();
    toast("ok", wanted === 0 ? t("views.system.dotSChromeTakesNo") : t("views.system.dotSChromeTakesDevtools", { wanted }));
  }

  function profileLabel(profile) {
    return PROFILE_KINDS[profile.kind] ?? profile.name;
  }

  function profileAction(label, name, onclick, solid = false) {
    return el("button", { class: solid ? "btn sm" : "btn ghost sm", type: "button", "aria-label": name, onclick: () => attempt(onclick) }, label);
  }

  function paintProfiles() {
    profileSetting.hidden = view.mode !== "own";
    profileList.replaceChildren(
      ...view.profiles.map((profile) => {
        const label = profileLabel(profile);
        const inUse = profile.name === view.profile;
        return el(
          "div",
          { class: "set-row profile-row", role: "listitem", "data-profile": profile.name },
          el("span", { class: inUse ? "profile-dot on" : "profile-dot", "aria-hidden": "true" }),
          el(
            "span",
            { class: "grow profile-name" },
            el("span", { class: "profile-title" }, label),
            inUse ? el("span", { class: "badge b-accent" }, t("views.system.inUse")) : null,
            profile.kind === "incognito" ? el("span", { class: "profile-note" }, t("views.system.keepsNothingOnceDotS")) : null,
          ),
          inUse ? null : profileAction(t("views.system.use"), t("views.system.useLabelForTasks", { label }), () => changeProfiles(api.post("/browser/profile", { name: profile.name }), t("views.system.tasksUseLabelFromChrome", { label })), true),
          profile.kind === "incognito" ? null : profileAction(t("views.system.clear"), t("views.system.clearTheSignInsAnd", { label }), () => clearProfile(profile, label)),
          profile.kind !== "added" || inUse ? null : profileAction(t("views.system.delete"), t("views.system.deleteLabel", { label }), () => deleteProfile(profile, label)),
        );
      }),
    );
  }

  // A change of the profiles, answered with how the card stands; refused while a run works or a Chrome window holds the
  // profile, when the card shows again what holds.
  async function changeProfiles(request, done) {
    let changed = null;
    try {
      changed = await request;
    } finally {
      if (!isDisposed()) {
        view = changed ?? view;
        paint();
      }
    }
    if (!isDisposed()) {
      toast("ok", done);
    }
  }

  /** The name of a new profile, asked for in a dialog; the card shows the profile once the daemon made it. */
  function openAddProfile() {
    const name = el("input", {
      class: "input",
      type: "text",
      maxlength: "48",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: t("views.system.work"),
      "aria-label": t("views.system.nameOfTheNewProfile"),
    });
    formDialog({
      title: t("views.system.addAProfile"),
      description: t("views.system.eachProfileOfDotS"),
      body: field(t("views.system.name"), name, ""),
      submitLabel: t("views.system.add"),
      size: "narrow",
      submitOnEnter: true,
      returnFocus: addButton,
      onSubmit: async () => {
        const typed = name.value.trim();
        if (typed === "") {
          name.focus();
          return false;
        }
        await changeProfiles(api.post("/browser/profiles", { name: typed }), t("views.system.typedIsAddedUseMakes", { typed }));
        return true;
      },
    });
  }

  /**
   * Import from Chrome (item 917): the profiles of your Chrome that hold cookies on the left, the profile of Dot's to
   * fill on the right (a new one named after it, or one there is, whose sign-ins are replaced), then the copy and the
   * check that Chrome reads it. While your Chrome is open the dialog says to quit it and looks again every few seconds.
   */
  async function openImportChrome() {
    let options = await api.get("/browser/chrome-import");
    if (isDisposed()) {
      return;
    }
    const uid = `import-${Date.now()}`;
    const sources = el("div", { class: "import-choices", role: "radiogroup", "aria-label": t("views.system.from") });
    const targets = el("div", { class: "import-choices", role: "radiogroup", "aria-label": t("views.system.into") });
    const quit = el("div", { class: "note warn import-quit", role: "status", hidden: true }, icon("alert"), el("span", null, t("views.system.yourChromeIsOpenQuit")));
    const none = el("p", { class: "hint", hidden: true }, t("views.system.noProfileOfYourChrome"));
    const use = el("input", { type: "checkbox", checked: true });
    const useRow = el("label", { class: "import-use" }, use, el("span", null, t("views.system.tasksUseItFromChrome")));
    const replaced = el("p", { class: "hint import-replaced", hidden: true }, t("views.system.itsSignInsAreReplaced"));

    const picked = (group) => group.querySelector("input:checked")?.value ?? null;

    function choice(group, value, title, sub, checked) {
      const input = el("input", { class: "visually-hidden import-radio", type: "radio", name: `${uid}-${group}`, value, checked });
      return el(
        "label",
        { class: "import-choice" },
        input,
        el("span", { class: "import-choice-text" }, el("span", { class: "import-choice-title" }, title), el("span", { class: "import-choice-sub" }, sub)),
        el("span", { class: "import-check", "aria-hidden": "true" }, icon("check")),
      );
    }

    function cookiesOf(source) {
      if (source.uncounted !== null) {
        return t("views.system.itsCookiesCouldNotBe");
      }
      return source.cookies === null ? t("views.system.chromeIsOpen") : tCount(source.cookies, "views.system.countCookie", "views.system.countCookies");
    }

    function paintSources() {
      const was = picked(sources) ?? options.sources[0]?.folder ?? null;
      sources.replaceChildren(...options.sources.map((source) => choice("from", source.folder, source.name, cookiesOf(source), source.folder === was)));
      none.hidden = options.sources.length > 0;
      quit.hidden = !options.chrome_open || options.sources.length === 0;
    }

    function paintTargets() {
      const was = picked(targets) ?? "";
      const source = options.sources.find((one) => one.folder === picked(sources));
      const fresh = choice("into", "", t("views.system.newProfile"), source === undefined ? t("views.system.madeForTheseSignIns") : t("views.system.namedNewName", { new_name: source.new_name }), was === "");
      targets.replaceChildren(
        fresh,
        ...options.targets.map((target) =>
          choice("into", target.name, profileLabel(target), target.signed_in ? t("views.system.holdsSignIns") : t("views.system.empty"), target.name === was),
        ),
      );
      const into = options.targets.find((target) => target.name === picked(targets));
      replaced.hidden = into === undefined || !into.signed_in;
      // A profile in use is used already.
      useRow.hidden = into !== undefined && into.name === options.in_use;
    }

    sources.addEventListener("change", paintTargets);
    targets.addEventListener("change", paintTargets);
    paintSources();
    paintTargets();
    const body = el(
      "div",
      { class: "import-chrome" },
      el("div", { class: "import-side" }, el("div", { class: "import-label" }, t("views.system.from")), sources, none),
      el("span", { class: "import-arrow", "aria-hidden": "true" }, icon("arrowRight")),
      el("div", { class: "import-side" }, el("div", { class: "import-label" }, t("views.system.into")), targets, replaced, useRow),
    );
    let watching = 0;
    const dialog = formDialog({
      title: t("views.system.importFromChrome"),
      size: "wide",
      description: t("views.system.copyTheSignInsOf2"),
      body: [quit, body],
      submitLabel: t("views.system.import"),
      valid: () => !options.chrome_open && picked(sources) !== null,
      returnFocus: importButton,
      onSubmit: async () => {
        const into = picked(targets);
        const target = options.targets.find((one) => one.name === into);
        const result = await api.post("/browser/chrome-import", {
          source: picked(sources),
          into: into === "" ? null : into,
          replace: target?.signed_in === true,
          use: !useRow.hidden && use.checked,
        });
        view = result.browser;
        if (!isDisposed()) {
          paint();
        }
        const label = profileLabel(view.profiles.find((one) => one.name === result.profile) ?? { name: result.profile, kind: "added" });
        toast("ok", t("views.system.copiedPartIntoLabelChecking", { part: tCount(result.cookies, "views.system.countCookie", "views.system.countCookies"), label }));
        // The check runs once the dialog is closed (the pre-push review of item 917): the import is made, and one that
        // Chrome cannot read is imported again from a dialog opened anew, which lists the profile and replaces it.
        attempt(async () => {
          const check = await api.post(`/browser/profiles/${encodeURIComponent(result.profile)}/check`, {});
          toast("ok", t("views.system.chromeReadsPartInLabel", { part: tCount(check.readable, "views.system.countCookie", "views.system.countCookies"), label }));
        });
        return true;
      },
    });
    // While your Chrome is open, the list is read again every few seconds, until it is quit or the dialog closes.
    watching = window.setInterval(() => {
      if (!options.chrome_open) {
        return;
      }
      api.get("/browser/chrome-import").then(
        (fresh) => {
          dialog.fail(null);
          options = fresh;
          paintSources();
          paintTargets();
          dialog.revalidate();
        },
        (error) => dialog.fail(error),
      );
    }, IMPORT_LOOK_MS);
    dialog.closed.then(() => window.clearInterval(watching));
  }

  async function clearProfile(profile, label) {
    const sure = await confirmDialog({
      title: t("views.system.clearLabel", { label }),
      message: t("views.system.itsSignInsTheData"),
      confirmLabel: t("views.system.clear"),
      danger: true,
      focusCancel: true,
    });
    if (sure) {
      await changeProfiles(api.post(`/browser/profiles/${encodeURIComponent(profile.name)}/clear`, {}), t("views.system.labelIsCleared", { label }));
    }
  }

  async function deleteProfile(profile, label) {
    const sure = await confirmDialog({
      title: t("views.system.deleteLabel2", { label }),
      message: t("views.system.theProfileGoesWithIts"),
      confirmLabel: t("views.system.delete"),
      danger: true,
      focusCancel: true,
    });
    if (sure) {
      await changeProfiles(api.del(`/browser/profiles/${encodeURIComponent(profile.name)}`), t("views.system.labelIsDeleted", { label }));
    }
  }

  function paintSizes() {
    const look = view.look;
    const chosen = look.fit_window ? "window" : `${look.viewport_width}x${look.viewport_height}`;
    // A size set by hand in the config file is shown as it is, among the others.
    const known = PAGE_SIZES.some((size) => size.id === chosen);
    const options = known ? PAGE_SIZES : [...PAGE_SIZES, { id: chosen, label: `${look.viewport_width} × ${look.viewport_height}` }];
    sizes.replaceChildren(...options.map((size) => el("option", { value: size.id }, size.label)));
    sizes.value = chosen;
    sizes.disabled = view.mode !== "own";
  }

  function paintZooms() {
    const chosen = view.look.zoom;
    // A zoom set by hand in the config file is shown as it is, among the others.
    const options = ZOOMS.includes(chosen) ? ZOOMS : [...ZOOMS, chosen].sort((a, b) => a - b);
    zooms.replaceChildren(...options.map((zoom) => el("option", { value: String(zoom) }, `${zoom}%`)));
    zooms.value = String(chosen);
    zooms.disabled = view.mode !== "own";
  }

  async function chooseSize(id) {
    const size = PAGE_SIZES.find((candidate) => candidate.id === id);
    if (size === undefined) {
      paintSizes();
      return;
    }
    await chooseLook(size.fit ? { ...view.look, fit_window: true } : { ...view.look, fit_window: false, viewport_width: size.width, viewport_height: size.height });
  }

  // How pages look is kept by the daemon, which lets go of Chrome so that it starts again with it; refused while a run
  // works, when the controls show again what holds.
  async function chooseLook(look) {
    let changed = null;
    try {
      changed = await api.post("/browser/look", look);
    } finally {
      if (!isDisposed()) {
        view = changed ?? view;
        paint();
      }
    }
    if (!isDisposed()) {
      toast("ok", t("views.system.savedForChromeSNext"));
    }
  }

  function paint() {
    modes.set(view.mode);
    enabled.paint();
    paintProfiles();
    paintSizes();
    appearance.choice.set(view.look.appearance);
    paintZooms();
    autoComputer.paint();
    raise.paint();
    screenInput.paint();
    paintPort();
    signInSetting.hidden = view.mode !== "own";
    signIn.disabled = view.signing_in;
    signingIn.hidden = !view.signing_in;
    if (signing && !view.signing_in) {
      toast("ok", t("views.system.chromeClosedTheSignIns"));
    }
    signing = view.signing_in;
    const port = view.port === null ? "" : t("views.system.portPort", { port: view.port });
    const stands = attachStanding(view);
    facts.replaceChildren(
      entry("Chrome", view.chrome_running ? "running" : t("views.system.notRunning")),
      entry(t("views.system.remoteDebugging"), view.remote_debugging ? `on${port}` : "off"),
      entry(t("views.system.dotSConnection"), stands === null ? (view.connected ? "connected" : t("views.system.notConnected")) : stands.word),
      entry(t("views.system.connectTimeout"), `${view.attach_timeout_s} s`),
    );
    standing.hidden = stands === null;
    if (stands !== null) {
      standing.className = stands.tone === "" ? "note attach-standing" : `note attach-standing ${stands.tone}`;
      standing.lastChild.textContent = stands.line;
    }
    steps.replaceChildren(
      copyStep(),
      el("li", null, t("views.system.chromeAsksOnceWhetherDot")),
      el(
        "li",
        null,
        t("views.system.chooseMyChromeAboveDot"),
        el(
          "details",
          { class: "error-details" },
          el("summary", null, t("views.system.anotherWay")),
          el("p", { class: "small muted" }, t("views.system.withChromeOpenRun"), el("code", { class: "mono" }, "dotx browser attach"), t("views.system.inTerminal")),
        ),
      ),
    );
  }

  // Read how Chrome stands now and paint it; false when the card is gone or the read failed, whose error it shows.
  async function read() {
    try {
      const found = await api.get("/browser");
      if (isDisposed()) {
        return false;
      }
      view = found;
    } catch (error) {
      if (!isDisposed()) {
        facts.replaceChildren(inlineError(error, () => attempt(load)));
      }
      return false;
    }
    paint();
    return true;
  }

  async function load() {
    if (await read()) {
      problem.hidden = true;
    }
    keepReading();
  }

  // With My Chrome the card reads again how Chrome stands while it is open, so a restart of Chrome, its Allow and the
  // connection show as they happen; so it does while the sign-in window is open, which the owner quits.
  function keepReading() {
    clearTimeout(polling);
    polling = setTimeout(async () => {
      if (isDisposed()) {
        return;
      }
      if (view !== null && (view.mode === "attach" || view.signing_in)) {
        await read();
      }
      keepReading();
    }, BROWSER_POLL_MS);
  }

  // Dot's own Chrome closes for the window the owner signs in in; when it is open the owner is asked first.
  async function openSignIn() {
    if (view === null || view.signing_in) {
      return;
    }
    if (view.connected) {
      const sure = await confirmDialog({
        title: t("views.system.closeDotSChrome"),
        message:
          t("views.system.dotSChromeIsOpen"),
        confirmLabel: t("views.system.closeAndOpen"),
      });
      if (!sure || isDisposed()) {
        return;
      }
    }
    signIn.disabled = true;
    let changed;
    try {
      changed = await api.post("/browser/sign-in", {});
    } catch (error) {
      if (!isDisposed()) {
        signIn.disabled = false;
      }
      throw error;
    }
    if (isDisposed()) {
      return;
    }
    view = changed;
    paint();
    keepReading();
  }

  async function choose(id) {
    if (view === null || id === view.mode) {
      return;
    }
    problem.hidden = true;
    waiting.hidden = id !== "attach";
    let changed;
    try {
      changed = await api.post("/browser/mode", { mode: id });
    } catch (error) {
      if (!isDisposed()) {
        waiting.hidden = true;
        modes.set(view.mode);
        complain(error.message);
        // The switch may have opened Chrome meanwhile: the facts say how it stands now.
        await read();
      }
      return;
    }
    if (isDisposed()) {
      return;
    }
    waiting.hidden = true;
    view = changed;
    paint();
    toast("ok", t("views.system.browsingWithLabel", { label: BROWSER_MODES.find((mode) => mode.id === view.mode).label }));
  }

  // What Dot says of the moment (the connection of your Chrome, the sign-in window, the wait for Allow, a refusal) stands
  // over the sections while it holds.
  const element = el(
    "div",
    { class: "set-sections" },
    el("div", { class: "set-notices" }, standing, signingIn, waiting, problem),
    setSection({ title: t("views.system.whereItBrowses"), body: setCard(enabled.element, browseWith, signInSetting) }),
    profileSetting,
    setSection({ title: t("views.system.howPagesLook"), body: setCard(sizeSetting, appearance.element, zoomSetting) }),
    setSection({ title: t("views.system.whileATaskWorks"), body: setCard(raise.element, autoComputer.element, screenInput.element) }),
    setSection({ title: t("views.system.letDotJoinYourChrome"), body: el("div", { class: "set-card card-pad" }, steps) }),
    advancedFold({
      title: t("views.system.advanced"),
      lead: t("views.system.forTroubleshootingHowChromeStands"),
      body: [el("div", { class: "card-pad" }, facts), portSetting],
    }),
  );
  return { element, load };
}

/** What Dot is called in the greeting of a new chat and at the head of a run, and what it calls you (item 72): both
 * preferences of this browser. */
function buildNameCard() {
  const owner = el("input", {
    class: "input name-input",
    type: "text",
    maxlength: String(OWNER_CHARS),
    autocomplete: "off",
    placeholder: shownOwner() || t("views.system.yourName"),
    value: ownerName(),
    "aria-label": t("views.system.yourName"),
  });
  owner.addEventListener("change", () =>
    attempt(async () => {
      const saved = await saveAssistant({ owner: owner.value.trim().slice(0, OWNER_CHARS) });
      owner.value = saved.owner;
      toast("ok", saved.owner === "" ? t("views.system.dotCallsYouByYour") : t("views.system.dotCallsYouOwner", { owner: saved.owner }));
    }),
  );
  owner.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      owner.blur();
    }
  });
  const input = el("input", {
    class: "input name-input",
    type: "text",
    maxlength: String(NAME_CHARS),
    autocomplete: "off",
    placeholder: "Dot",
    value: dotName(),
    "aria-label": t("views.system.name"),
  });
  input.addEventListener("change", () =>
    attempt(async () => {
      const typed = input.value.trim().slice(0, NAME_CHARS);
      const saved = await saveAssistant({ name: typed === "" ? "Dot" : typed });
      input.value = saved.name;
      toast("ok", t("views.system.dotAnswersToName", { name: saved.name }));
    }),
  );
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  return section(
    t("views.system.name"),
    null,
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, t("views.system.whatDotIsCalled")), input),
        el("div", { class: "hint" }, t("views.system.usedInTheGreetingAnd")),
      ),
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, t("views.system.whatDotCallsYou")), owner),
        el("div", { class: "hint" }, t("views.system.usedInTheGreetingGood")),
      ),
    ),
  );
}

/** Dot's own face (item 102), kept by the daemon for every browser: the face it wears and Change (item 1136), which
 * opens the picker and Save; the picker is made the first time it opens. */
function buildDotLookCard() {
  let picker = null;
  const face = el("span", { class: "dot-look-face" }, dotFace("idle"));
  const pickerSlot = el("div", { class: "card-pad stack", hidden: true });
  const change = el("button", { class: "btn sm", type: "button", "aria-expanded": "false" }, t("views.system.change"));
  const save = el("button", { class: "btn primary", type: "button" }, icon("check"), t("views.system.saveTheLook"));
  change.addEventListener("click", () => {
    const opening = pickerSlot.hidden;
    if (opening && picker === null) {
      picker = lookPicker({ look: lookOf(DOT_ID, null), uid: "dot" });
      pickerSlot.replaceChildren(picker.element, el("div", { class: "cluster end" }, save));
    }
    pickerSlot.hidden = !opening;
    change.textContent = opening ? t("views.system.close") : t("views.system.change");
    change.setAttribute("aria-expanded", String(opening));
  });
  save.addEventListener("click", () =>
    attempt(async () => {
      save.disabled = true;
      try {
        const record = await api.put(`/agents/${DOT_ID}/look`, picker.value());
        rememberLook(DOT_ID, record.look);
        face.replaceChildren(dotFace("idle"));
        toast("ok", t("views.system.dotSLookSaved"));
      } finally {
        save.disabled = false;
      }
    }),
  );
  const element = el(
    "section",
    { class: "card", "aria-label": t("views.system.dotSLook") },
    el("div", { class: "card-pad row dot-look-row" }, face, el("span", { class: "grow" }, t("views.system.dotSLook")), change),
    pickerSlot,
  );
  return { element, dispose: () => picker?.dispose() };
}

export function mountGeneral(root, ctx) {
  const look = buildDotLookCard();
  const voice = buildVoiceCard();
  ctx.head.setLead(t("views.system.whatDotIsCalledAnd"));
  root.append(
    buildNameCard(),
    look.element,
    buildNotifications(),
    buildComposerCard(),
    voice.element,
    buildChatCard(),
    buildReactionsCard(),
    buildTokenCard(),
  );
  return () => {
    look.dispose();
    voice.dispose();
  };
}

/** The AI services Dot can reach and the models each of them offers; `?model=<ref>` in the address shows that model. */
export function mountModels(root, ctx) {
  let disposed = false;
  const asked = new URLSearchParams(location.hash.split("?")[1] ?? "").get("model") ?? "";
  const models = buildModels(() => disposed, asked, ctx.head);
  ctx.head.setLead(t("views.system.whichAiModelDoesEach"));
  // What the tasks used in the last week (item 1403) stands before the long list of every model, which is last.
  const usage = buildUsage();
  models.element.insertBefore(usage.element, models.element.lastElementChild);
  root.append(models.element);
  const stop = onChange(() => models.paint());
  models.paint();
  attempt(refresh);
  attempt(models.load);
  attempt(usage.load);
  return () => {
    disposed = true;
    stop();
    models.dispose();
  };
}

/** Which Chrome the agent browses with. */
export function mountBrowser(root, ctx) {
  let disposed = false;
  const browser = buildBrowser(() => disposed, ctx.head);
  ctx.head.setLead(t("views.system.whichChromeDotUsesTo"));
  root.append(browser.element);
  attempt(browser.load);
  return () => {
    disposed = true;
  };
}

/** One of the three tiles at the head of System (item 564): what it is, a dot and a word that say how it stands, and a line
 * under them. `action` is a button at the right of its name. */
function systemTile(label, action) {
  const dot = el("span", { class: "set-dot", "aria-hidden": "true" });
  const word = el("strong", { class: "set-tile-word" });
  const line = el("div", { class: "set-tile-line" });
  const element = el(
    "div",
    { class: "set-tile", role: "group", "aria-label": label },
    el("div", { class: "set-tile-head" }, el("span", null, label), action),
    el("div", { class: "set-tile-value" }, dot, word),
    line,
  );
  return {
    element,
    /** `tone` is ok, warn or bad (anything else is grey); the word and the line say how it stands. */
    set(tone, text, under) {
      dot.dataset.tone = tone;
      word.textContent = text;
      line.textContent = under;
    },
  };
}

/** The daemon, its permissions, its emergency stop and its health checks. */
export function mountSystem(root, ctx) {
  let disposed = false;
  const refreshButton = el(
    "button",
    { class: "icon-btn set-tile-action", type: "button", title: t("views.system.readDotSStateAgain"), "aria-label": t("views.system.readDotSStateAgain"), onclick: () => attempt(refresh) },
    icon("refresh"),
  );
  const tiles = { daemon: systemTile("Dot", refreshButton), permissions: systemTile(t("views.system.permissions"), null), health: systemTile(t("views.system.health"), null) };
  tiles.daemon.set("", "Reading", "");
  tiles.permissions.set("", "Reading", "");
  tiles.health.set("", "Checking", "");
  const daemon = buildDaemon();
  const permissions = buildPermissions((lacking) => {
    if (lacking === null) {
      tiles.permissions.set("bad", "Could not read", "Read them again below");
      return;
    }
    tiles.permissions.set(lacking.length === 0 ? "ok" : "warn", lacking.length === 0 ? "All granted" : "Missing", lacking.length === 0 ? "Accessibility, input, screen capture" : lacking.join(", "));
  });
  const failsafe = buildFailsafe();
  const doctor = buildDoctor(
    () => disposed,
    (checks) => {
      if (checks === null) {
        tiles.health.set("bad", "Could not run", "Run them again below");
        return;
      }
      const summary = doctorSummary(checks);
      tiles.health.set(summary.tone, summary.word, summary.line);
    },
  );

  function paint(snapshot) {
    daemon.paint(snapshot);
    failsafe.paint(snapshot);
    const status = snapshot.status;
    if (status === null || snapshot.stream !== "live") {
      tiles.daemon.set("warn", snapshot.stream === "live" ? "Reading" : "Not answering", "");
    } else {
      tiles.daemon.set("ok", "Running", `For ${duration(Math.max(0, Date.now() / 1000 - status.started_at))}`);
    }
  }

  ctx.head.setLead(t("views.system.whetherDotIsRunningWell"));
  root.append(
    el(
      "div",
      { class: "set-sections" },
      el("div", { class: "set-tiles" }, tiles.daemon.element, tiles.permissions.element, tiles.health.element),
      failsafe.element,
      doctor.element,
      daemon.element,
      permissions.element,
      buildAbout(),
    ),
  );
  const stop = onChange(paint);
  paint(getSnapshot());
  attempt(refresh);
  attempt(permissions.load);
  attempt(doctor.run);
  return () => {
    disposed = true;
    stop();
  };
}
