// Settings › Channels (item 572): the owner's own messaging apps as ways to reach Dot. A message of theirs starts a run in
// a chat of its own ("iMessage"), the answer comes back on the app, and an approval is answered in Dot, never in a message.
// iMessage reads the owner's conversation with themselves in Messages, or with their iPhone's Apple ID when it is not the
// Mac's own (item 595): its card has the switch, the numbers and addresses paired (each with a code sent to it that the
// owner types back from their iPhone), what macOS has granted (Full Disk Access to read Messages' database and Automation
// to send in it) and what the channel sees of that database, with Check now, which looks at once.

import { api } from "../api.js";
import { lookWords, seenFacts } from "../channelfacts.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";

// How often the page reads the channels again while it is open and quiet, so that it follows what macOS is granted.
const REFRESH_MS = 10_000;
// How a permission stands: the badge's tone and words.
const DATABASE = {
  granted: ["b-ok", "granted"],
  missing: ["b-warn", "missing"],
  no_database: ["b-warn", "no Messages database yet"],
};
// What a paired number or address means (item 595): what it writes to this Mac runs as the owner's command.
const RUNS_AS_YOU = "Messages from this address run as you";
const AUTOMATION = {
  allowed: ["b-ok", "allowed"],
  not_asked: ["b-warn", "not asked yet"],
  refused: ["b-warn", "refused"],
  not_open: ["b-muted", "Messages is not open"],
  unknown: ["b-muted", "not known"],
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
    placeholder: "+358 40 123 4567, or you@icloud.com",
    "aria-label": "Your phone number or Apple ID email",
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
  });
  const codeField = el("input", {
    class: "input mono",
    type: "text",
    inputmode: "numeric",
    placeholder: "123 456",
    "aria-label": "The code you read on your iPhone",
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
  const checkButton = offer("Check now", "refresh", checkNow);
  const sendCode = () => post("/channels/imessage/code", { handle: handleField.value }, "A code is on its way to that number or address.");
  const confirmCode = async () => {
    await post("/channels/imessage/confirm", { code: codeField.value }, "Paired. Messages you send to yourself now reach Dot.");
    codeField.value = "";
    handleField.value = "";
  };
  const remove = (handle) => post("/channels/imessage/remove", { handle }, "That number or address is no longer read.");
  const openPane = (pane) => api.post("/desktop/permissions/open", { pane });
  const sendButton = offer("Send a code", "send", sendCode, "btn sm primary");
  const confirmButton = offer("Confirm", "check", confirmCode, "btn sm primary");
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
            { class: "icon-btn", type: "button", title: `Stop reading ${handle}`, "aria-label": `Stop reading ${handle}`, onclick: () => attempt(() => remove(handle)) },
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
            { class: "channel-verify", role: "group", "aria-label": "The code sent" },
            el(
              "p",
              { class: "hint" },
              `A code went to ${view.verifying.handle}. Type what you read on your iPhone before ${clockTime(view.verifying.expires_at)}.`,
            ),
            codeForm,
          );
    return setRow({
      title: "Your number or Apple ID email",
      lead: "Pair only your own number or Apple ID email: the conversation with yourself. If your iPhone uses another Apple ID than this Mac, pair the number or address it writes from, which names the conversation on this Mac. Dot runs what you write there as your command, and so what that number or address writes to this Mac: pairing someone else's gives them Dot. The code it sends shows only that it reached whoever holds it.",
      body: el(
        "div",
        { class: "channel-body" },
        paired.length === 0 ? el("p", { class: "hint" }, "None paired yet.") : el("div", { class: "channel-pairings" }, paired),
        handleForm,
        waiting,
      ),
    });
  }

  /** What macOS has granted, and the step that sets each right. */
  function permissionRows(view) {
    const program = view.program ?? "the app that runs Dot";
    const database = DATABASE[view.database];
    const automation = AUTOMATION[view.automation];
    const databaseControl =
      view.database === "granted"
        ? [badge(database)]
        : view.database === "missing"
          ? [badge(database), offer("Open Full Disk Access", "external", () => openPane("full_disk_access"))]
          : [badge(database)];
    const automationControl =
      view.automation === "allowed"
        ? [badge(automation)]
        : view.automation === "refused"
          ? [badge(automation), offer("Open Automation", "external", () => openPane("automation"))]
          : [badge(automation), offer("Ask macOS", "hand", () => post("/channels/imessage/check", {}))];
    return [
      setRow({
        title: "Full Disk Access",
        lead:
          view.database === "no_database"
            ? "Messages has made no database on this Mac yet: sign in to Messages with your Apple ID."
            : `Lets ${program} read your Messages database. After you switch it on in System Settings, restart Dot (Settings › System).`,
        control: databaseControl,
      }),
      setRow({
        title: "Automation for Messages",
        lead: `Lets ${program} send Dot's answers through Messages, each starting with ◆.`,
        control: automationControl,
      }),
    ];
  }

  /** What the channel sees of Messages' database (item 595): the newest message, how far Dot has read, when it looked, the
   * two files, the last command and the last error, and Check now. */
  function seenRow(view) {
    const facts = el(
      "dl",
      { class: "kv kv-rows channel-seen" },
      seenFacts(view.seen).map(([label, value]) => el("div", null, el("dt", null, label), el("dd", null, value))),
    );
    const row = setRow({
      title: "What Dot sees in Messages",
      lead: "Dot looks at Messages' database every few seconds while iMessage is on. Check now looks at once, and reads what is new when iMessage is on.",
      control: checkButton,
      body: el("div", { class: "channel-body" }, facts),
    });
    row.classList.add("channel-seen-row");
    return row;
  }

  function imessageSection(view) {
    const toggle = switchRowSetting({
      label: "iMessage",
      hint: "Dot reads your conversation with yourself on this Mac, or the one your iPhone's Apple ID has with this Mac's, and answers in it, and tells you there what it has to say on its own: the morning brief and your watchers. Only what you send from now on is read.",
      read: () => view.on,
      write: async (on) => {
        await post("/channels/imessage/switch", { on }, on ? "Dot reads your conversation with yourself." : "Dot no longer reads iMessage.");
      },
      onToggled: () => toggle.paint(),
    });
    toggle.paint();
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
        : el("a", { class: "btn sm", href: `#/runs/${view.chat_run_id}` }, icon("message"), el("span", null, "Open the chat"));
    const card = setCard(toggle.element, handlesRow(view), ...permissionRows(view), seenRow(view), warning, problem);
    card.setAttribute("role", "group");
    card.setAttribute("aria-label", "iMessage");
    return setSection({
      title: "iMessage",
      lead: "Message yourself from your iPhone, or write to this Mac's Apple ID from your own. Each answer of Dot comes back in the same conversation, marked ◆.",
      actions: chat,
      body: card,
    });
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
    holder.replaceChildren(el("div", { class: "set-sections" }, imessageSection(current.imessage)));
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

  ctx.head.setLead("Reach Dot from your iPhone's own Messages, without Tailscale or the web UI. A message of yours starts a run in a chat of its own and the answer comes back; an approval is answered in Dot, never in a message.");
  ctx.head.setActions(offer("Check again", "refresh", load));
  root.append(holder);
  attempt(load);
  follow();
  return () => {
    disposed = true;
    clearTimeout(refresher);
  };
}
