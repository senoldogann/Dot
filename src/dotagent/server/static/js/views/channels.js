// Settings › Channels (item 572): the owner's own messaging apps as ways to reach Dot. A message of theirs starts a run in
// a chat of its own ("iMessage"), the answer comes back on the app, and an approval is answered in Dot, never in a message.
// iMessage reads the owner's conversation with themselves in Messages, or with their iPhone's Apple ID when it is not the
// Mac's own (item 595): its card has the switch, the numbers and addresses paired (each with a code sent to it that the
// owner types back from their iPhone), what macOS has granted (Full Disk Access to read Messages' database and Automation
// to send in it) and what the channel sees of that database, with Check now, which looks at once. A run started from
// there tells what it does while it works, a few messages (item 618): the card has a switch for that too.

import { api, holdsPhoneKey } from "../api.js";
import { linkFact, lookWords, seenFacts } from "../channelfacts.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { advancedFold, errorPanel, loadingPanel, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";
import { t } from "../i18n.js";

// How often the page reads the channels again while it is open and quiet, so that it follows what macOS is granted.
const REFRESH_MS = 10_000;
// How a permission stands: the badge's tone and words.
const DATABASE = {
  granted: ["b-ok", "granted"],
  missing: ["b-warn", "missing"],
  no_database: ["b-warn", t("views.channels.noMessagesSavedYet")],
};
// What a paired number or address means (item 595): what it writes to this Mac runs as the owner's command.
const RUNS_AS_YOU = t("views.channels.messagesFromThisAddressRun");
const AUTOMATION = {
  allowed: ["b-ok", "allowed"],
  not_asked: ["b-warn", t("views.channels.notAskedYet")],
  refused: ["b-warn", "refused"],
  not_open: ["b-muted", t("views.channels.messagesIsNotOpen")],
  unknown: ["b-muted", t("views.channels.notKnown")],
};

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function badge([tone, words]) {
  return el("span", { class: `badge ${tone}` }, words);
}

export function mount(root, ctx) {
  let disposed = false;
  // The channels as last read, what is drawn of them, and how many actions wait for their answer.
  let current = null;
  let shown = "";
  let busy = 0;
  let refresher = null;
  const holder = el("div", { class: "stack" }, loadingPanel());

  // The fields stay through every drawing of the page, so that what is typed in them is not lost when it reads the
  // channels again.
  const handleField = el("input", {
    class: "input",
    type: "text",
    placeholder: t("views.channels.358401234567Or"),
    "aria-label": t("views.channels.yourPhoneNumberOrApple"),
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
  });
  const codeField = el("input", {
    class: "input mono",
    type: "text",
    inputmode: "numeric",
    placeholder: "123 456",
    "aria-label": t("views.channels.theCodeYouReadOn"),
    autocomplete: "one-time-code",
    maxlength: "12",
  });

  /** A button that runs `action` once at a time, its errors shown. */
  function offer(label, iconName, action, className = "btn sm") {
    const button = el("button", { class: className, type: "button" }, icon(iconName), el("span", null, label));
    button.addEventListener("click", () =>
      attempt(async () => {
        button.disabled = true;
        busy += 1;
        try {
          await action();
        } finally {
          busy -= 1;
          button.disabled = false;
        }
      }),
    );
    return button;
  }

  async function post(path, body, done) {
    const next = await api.post(path, body);
    if (disposed) {
      return;
    }
    current = next;
    paint();
    if (done !== undefined) {
      toast("ok", done);
    }
  }

  // Check now looks at Messages' database at once, as the next poll would, and says what it saw. It is made once, so that
  // it keeps its place and its pressed state through every drawing of the page.
  const checkNow = async () => {
    const before = current?.imessage.seen.command?.rowid ?? null;
    await post("/channels/imessage/poll", {});
    if (!disposed) {
      toast(...lookWords(current.imessage, before));
    }
  };
  const checkButton = offer(t("views.channels.checkNow"), "refresh", checkNow);
  // The Advanced fold stays as the reader left it when the tab draws itself again as the channel's state changes.
  let advancedOpen = false;
  const sendCode = () => post("/channels/imessage/code", { handle: handleField.value }, t("views.channels.aCodeIsOnIts"));
  const confirmCode = async () => {
    await post("/channels/imessage/confirm", { code: codeField.value }, t("views.channels.connectedMessagesYouSendTo"));
    codeField.value = "";
    handleField.value = "";
  };
  const remove = (handle) => post("/channels/imessage/remove", { handle }, t("views.channels.thatNumberOrAddressIs"));
  const openPane = (pane) => api.post("/desktop/permissions/open", { pane });
  const sendButton = offer(t("views.channels.sendACode"), "send", sendCode, "btn sm primary");
  const confirmButton = offer(t("views.channels.confirm"), "check", confirmCode, "btn sm primary");
  const handleForm = el(
    "form",
    {
      class: "channel-form",
      onsubmit: (event) => {
        event.preventDefault();
        sendButton.click();
      },
    },
    handleField,
    sendButton,
  );
  const codeForm = el(
    "form",
    {
      class: "channel-form",
      onsubmit: (event) => {
        event.preventDefault();
        confirmButton.click();
      },
    },
    codeField,
    confirmButton,
  );

  /** The numbers and addresses paired, each with the button that forgets it and what pairing it means; the form that
   * sends a code to another, and the one that takes the code back. */
  function handlesRow(view) {
    const paired = view.handles.map((handle) =>
      el(
        "div",
        { class: "channel-paired" },
        el(
          "span",
          { class: "chip channel-handle" },
          el("span", { class: "mono" }, handle),
          el(
            "button",
            { class: "icon-btn", type: "button", title: t("views.channels.stopReadingHandle", { handle }), "aria-label": t("views.channels.stopReadingHandle", { handle }), onclick: () => attempt(() => remove(handle)) },
            icon("x"),
          ),
        ),
        el("span", { class: "hint" }, RUNS_AS_YOU),
      ),
    );
    const waiting =
      view.verifying === null
        ? null
        : el(
            "div",
            { class: "channel-verify", role: "group", "aria-label": t("views.channels.theCodeSent") },
            el(
              "p",
              { class: "hint" },
              t("views.channels.aCodeWentToHandle", { handle: view.verifying.handle, clockTime: clockTime(view.verifying.expires_at) }),
            ),
            codeForm,
          );
    return setRow({
      title: t("views.channels.yourNumberOrAppleId"),
      lead: t("views.channels.connectOnlyYourOwnNumber"),
      body: el(
        "div",
        { class: "channel-body" },
        paired.length === 0 ? el("p", { class: "hint" }, t("views.channels.noneConnectedYet")) : el("div", { class: "channel-pairings" }, paired),
        handleForm,
        waiting,
      ),
    });
  }

  /** What macOS has granted, and the step that sets each right. */
  function permissionRows(view) {
    const program = view.program ?? t("views.channels.theAppThatRunsDot");
    const database = DATABASE[view.database];
    const automation = AUTOMATION[view.automation];
    const databaseControl =
      view.database === "granted"
        ? [badge(database)]
        : view.database === "missing"
          ? [badge(database), offer(t("views.channels.openFullDiskAccess"), "external", () => openPane("full_disk_access"))]
          : [badge(database)];
    const automationControl =
      view.automation === "allowed"
        ? [badge(automation)]
        : view.automation === "refused"
          ? [badge(automation), offer(t("views.channels.openAutomation"), "external", () => openPane("automation"))]
          : [badge(automation), offer(t("views.channels.askMacos"), "hand", () => post("/channels/imessage/check", {}))];
    return [
      setRow({
        title: t("views.channels.fullDiskAccess"),
        lead:
          view.database === "no_database"
            ? t("views.channels.messagesHasNotSavedAny")
            : t("views.channels.letsProgramReadTheMessages", { program }),
        control: databaseControl,
      }),
      setRow({
        title: t("views.channels.automationForMessages"),
        lead: t("views.channels.letsProgramSendDotS", { program }),
        control: automationControl,
      }),
    ];
  }

  /** What the channel sees of Messages' database (item 595): the newest message, how far Dot has read, when it looked, the
   * two files, the last command and the last error, and Check now; and, in the same facts, what the links in Dot's
   * messages open at (item 611). */
  function seenRow(view) {
    const facts = el(
      "dl",
      { class: "kv kv-rows channel-seen" },
      [...seenFacts(view.seen), linkFact(view.links, view.links_off)].map(([label, value]) => el("div", null, el("dt", null, label), el("dd", null, value))),
    );
    const row = setRow({
      title: t("views.channels.whatDotSeesInMessages"),
      lead: t("views.channels.dotLooksAtMessagesDatabase"),
      control: checkButton,
      body: el("div", { class: "channel-body" }, facts),
    });
    row.classList.add("channel-seen-row");
    return row;
  }

  function imessageSection(view, progressOn) {
    const toggle = switchRowSetting({
      label: "iMessage",
      hint: t("views.channels.dotReadsYourConversationWith"),
      read: () => view.on,
      write: async (on) => {
        await post("/channels/imessage/switch", { on }, on ? t("views.channels.dotReadsYourConversationWith2") : t("views.channels.dotNoLongerReadsImessage"));
      },
      onToggled: () => toggle.paint(),
    });
    toggle.paint();
    // What a run started from the channel tells while it works (item 618): `[channels] progress`, live.
    const progress = switchRowSetting({
      label: t("views.channels.progressMessages"),
      hint: t("views.channels.whileATaskYouStarted"),
      read: () => progressOn,
      write: async (on) => {
        await post("/channels/progress", { on }, on ? t("views.channels.dotTellsYouWhatA") : t("views.channels.dotSendsOnlyTheAnswers"));
      },
      onToggled: () => progress.paint(),
    });
    progress.paint();
    const warning =
      view.seen.warning === null
        ? null
        : el("div", { class: "set-row" }, el("div", { class: "note" }, icon("alert"), el("span", null, view.seen.warning)));
    const problem =
      view.problem === null
        ? null
        : el("div", { class: "set-row" }, el("div", { class: "note" }, icon("alert"), el("span", null, view.problem)));
    const chat =
      view.chat_run_id === null
        ? null
        : el("a", { class: "btn sm", href: `#/runs/${view.chat_run_id}` }, icon("message"), el("span", null, t("views.channels.openTheChat")));
    const card = setCard(toggle.element, progress.element, handlesRow(view), ...permissionRows(view), warning, problem);
    card.setAttribute("role", "group");
    card.setAttribute("aria-label", "iMessage");
    return [
      setSection({
        title: "iMessage",
        lead: t("views.channels.messageYourselfFromYourIphone"),
        actions: chat,
        body: card,
      }),
      advancedFold({
        title: t("views.channels.advanced"),
        lead: t("views.channels.forTroubleshootingWhatDotSees"),
        body: seenRow(view),
        open: advancedOpen,
        onToggle: (open) => {
          advancedOpen = open;
        },
      }),
    ];
  }

  function paint() {
    if (current === null) {
      return;
    }
    const drawn = JSON.stringify(current);
    if (drawn === shown) {
      return;
    }
    shown = drawn;
    holder.replaceChildren(el("div", { class: "set-sections" }, ...imessageSection(current.imessage, current.progress)));
  }

  async function load() {
    let next;
    try {
      next = await api.get("/channels");
    } catch (error) {
      if (!disposed) {
        shown = "";
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      }
      return;
    }
    if (!disposed) {
      current = next;
      paint();
    }
  }

  async function retry() {
    shown = "";
    holder.replaceChildren(loadingPanel());
    await load();
  }

  /** Read the channels again while the page is open and nothing is under way or being typed. */
  function follow() {
    clearTimeout(refresher);
    refresher = setTimeout(async () => {
      if (disposed) {
        return;
      }
      const typing = document.activeElement === handleField || document.activeElement === codeField;
      if (busy === 0 && !typing && document.visibilityState === "visible") {
        await attempt(load);
      }
      follow();
    }, REFRESH_MS);
  }

  ctx.head.setLead(
    holdsPhoneKey()
      ? t("views.channels.reachDotFromTheMessages")
      : t("views.channels.reachDotFromTheMessages2"),
  );
  ctx.head.setActions(offer(t("views.channels.checkAgain"), "refresh", load));
  root.append(holder);
  attempt(load);
  follow();
  return () => {
    disposed = true;
    clearTimeout(refresher);
  };
}
