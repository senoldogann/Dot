// The queue of a chat (item 148): what is sent while a run works waits above the composer and starts
// as the chat's next run, one message after the other, once the run before it has ended done. A message can be moved
// (its up and down arrows, which a finger, a pointer and the keys press alike), edited in the composer, started now in
// place of the run at work, which stops (Send now, item 2106), or taken out; ⌘⇧Enter with the box empty steers the
// first one into the run at work. A stop, a failure or a crash of the daemon holds the queue until Resume; a run that a stop of the daemon
// cut leaves it to go on once the run, continued, ends done. The daemon keeps the queue, so it goes on with no page
// open, and every open page shows it as it changes; a queue the page could not read says so, with Read again.

import { api } from "./api.js";
import { el } from "./dom.js";
import { goTo } from "./hop.js";
import { icon } from "./icons.js";
import { viaOutbox } from "./outbox.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, toast } from "./ui.js";
import { t, tCount } from "./i18n.js";

const QUEUE_CHANGED = "queue_changed";

/** A message's text on one line, as its row shows it. */
function oneLine(text) {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * The queue's banner for the chat `threadId`, which goes right above the composer `frame`. `onSteered(text)` hears of
 * a message sent to the run at work, `onStarted(runId, afterRunId)` of one the daemon started as the chat's next run
 * after `afterRunId`, and `onEdit()` of the start and the end of an edit, which the composer's send names Save while
 * it lasts.
 *
 * Returns `{ element, add(body), steerFirst(), setActive(on), setForeign(on), editing(), saveEdit(text), cancelEdit(), count(),
 * expand(), dispose() }`: `add` queues a message (the body of `POST /threads/{id}/queue`), `editing()` is the id of the
 * message the composer edits or null, `saveEdit` keeps the composer's text as that message's, `count()` is how many
 * messages wait and `expand()` opens their list. `onChange({ count, held, problem, sending })` is called on every change
 * of the queue (item 352: the line over the composer counts them; item 1383: on a phone it says whether the queue is
 * held, could not be read, or has a message on its way, which the queue's own banner is not drawn there to say).
 */
export function createQueue({ threadId, frame, onSteered, onStarted, onEdit, onChange }) {
  const listId = `queue-${threadId}`;
  // Its reads end when the chat is left (item 1521).
  const leaving = new AbortController();
  let messages = [];
  let held = false;
  let active = false;
  // A run another process drives (a `dotx` command) works on the chat: Send now cannot stop it from here.
  let foreign = false;
  let expanded = true;
  let busy = false;
  // A message is on its way into the queue (`add`).
  let sendingNow = false;
  // Why the queue could not be read the last time, or null once it was.
  let problem = null;
  // The message the composer edits, and the draft it held before.
  let editingId = null;
  let draft = "";
  let draftFiles = [];

  const element = el("section", { class: "queue", role: "region", "aria-live": "polite", hidden: true });
  const toggleIcon = icon("chevron");
  toggleIcon.classList.add("queue-chevron");
  const count = el("span", { class: "queue-count" });
  const head = el(
    "button",
    { class: "queue-head", type: "button", "aria-controls": listId },
    icon("listOrdered"),
    el("span", { class: "queue-title" }, t("queue.queued")),
    count,
    toggleIcon,
  );
  const resume = el("button", { class: "btn sm ghost", type: "button" }, t("queue.resume"));
  const paused = el("div", { class: "queue-paused", hidden: true }, el("span", null, t("queue.pausedUntilYouResumeIt")), resume);
  const unreadText = el("span");
  const readAgain = el("button", { class: "btn sm ghost", type: "button" }, t("queue.readAgain"));
  const unread = el("div", { class: "queue-unread", hidden: true }, unreadText, readAgain);
  const list = el("ol", { class: "queue-list", id: listId });
  element.append(head, unread, paused, list);

  head.addEventListener("pointerdown", (event) => event.preventDefault());
  head.addEventListener("click", () => {
    expanded = !expanded;
    paint();
  });
  resume.addEventListener("click", () =>
    act(() => api.post(`/threads/${threadId}/queue/resume`, null), { method: "POST", path: `/threads/${threadId}/queue/resume`, body: null }),
  );
  readAgain.addEventListener("click", async () => {
    readAgain.disabled = true;
    await load();
    readAgain.disabled = false;
  });

  /** Show the queue as the daemon answered it, which was read then. */
  function show(view) {
    problem = null;
    messages = view.messages;
    held = view.held;
    if (editingId !== null && !messages.some((message) => message.id === editingId)) {
      // The message left the queue (it started, was steered or taken out elsewhere): its edit ends with it.
      endEdit();
    }
    paint();
  }

  // The reads of the queue in the order they were sent, and the newest whose answer is shown (item 559): two changes
  // close together (a message queued, then started) put two reads on their way, and an answer older than the one shown
  // is dropped, not painted over it. A request of the queue's own counts as a read, its answer being the queue as it
  // then is.
  let reads = 0;
  let shownRead = 0;

  /** Show `view`, the answer to the read or request `mine`, unless the answer of a newer one is shown already: the rows
   * are then drawn as they stand. */
  function showNewest(mine, view) {
    if (mine > shownRead) {
      shownRead = mine;
      show(view);
    } else {
      paint();
    }
  }

  /** Read the chat's queue; one that cannot be read keeps what was shown and says why, with Read again. */
  async function load() {
    const mine = ++reads;
    let view;
    try {
      view = await api.get(`/threads/${threadId}/queue`, { signal: leaving.signal });
    } catch (error) {
      if (leaving.signal.aborted) {
        return;
      }
      console.warn("the queue could not be read", error);
      if (mine > shownRead) {
        shownRead = mine;
        problem = error instanceof Error ? error.message : String(error);
        paint();
      }
      return;
    }
    showNewest(mine, view);
  }

  /** Run one request of the queue at a time, the others being ignored; its answer is the queue as it then is. With a
   * `descriptor` (the method, path and body of the request) a change made with no link is kept and sent when the link is
   * back, in order with what else was done in the chat (item 1523). */
  async function act(request, descriptor) {
    if (busy) {
      return;
    }
    busy = true;
    paint();
    try {
      await attempt(async () => {
        const mine = ++reads;
        const answer = descriptor === undefined ? await request() : await viaOutbox({ kind: "queue", chat: threadId, ...descriptor }, request);
        if (answer !== null && typeof answer === "object" && "kept" in answer) {
          return;
        }
        showNewest(mine, answer);
      });
    } finally {
      busy = false;
      paint();
    }
  }

  function order(ids) {
    return act(() => api.put(`/threads/${threadId}/queue`, { ids }), { method: "PUT", path: `/threads/${threadId}/queue`, body: { ids } });
  }

  function moved(id, to) {
    const ids = messages.map((message) => message.id).filter((other) => other !== id);
    ids.splice(to, 0, id);
    return ids;
  }

  function steer(message) {
    return act(async () => {
      const view = await api.post(`/queue/${message.id}/steer`, null);
      onSteered(message.text);
      return view;
    });
  }

  // Send now (item 2106): the run at work stops and the message starts in its place, the rest of the queue after it;
  // the page follows the chat to that run as it does to any run the queue starts (`onStarted`).
  function sendNow(message) {
    return act(() => api.post(`/queue/${message.id}/now`, null));
  }

  function startEdit(message) {
    if (editingId === null) {
      // The message the box holds while the edit lasts is not the draft: the draft the composer keeps stays as it was,
      // and the draft's files wait aside as they stand, one still uploading among them, until the edit ends.
      frame.pauseDraft();
      draft = frame.textarea.value;
      draftFiles = frame.attachments.setAside();
    }
    editingId = message.id;
    frame.textarea.value = message.text;
    // The files the message carries come back into the box with its text (item 376), small and each with its ×.
    frame.attachments.clear();
    frame.attachments.restoreSmall(message.files);
    frame.autosize();
    frame.textarea.focus();
    onEdit();
    paint();
  }

  function endEdit() {
    if (editingId === null) {
      return;
    }
    editingId = null;
    frame.textarea.value = draft;
    draft = "";
    // The files the edit put in the box leave with it; the ones the draft had come back.
    frame.attachments.clear();
    frame.attachments.putBack(draftFiles);
    draftFiles = [];
    // The draft the composer keeps follows the box again.
    frame.resumeDraft();
    frame.textarea.dispatchEvent(new Event("input"));
    frame.autosize();
    onEdit();
  }

  /** The arrow that moves `message` a place up or down; `disabled` at the end it points away from. */
  function moveButton(message, way, disabled) {
    const up = way === "up";
    const button = el(
      "button",
      {
        class: `queue-arrow queue-${way}`,
        type: "button",
        "data-id": message.id,
        "data-part": way,
        "aria-label": up ? t("queue.moveQueuedMessageUp") : t("queue.moveQueuedMessageDown"),
        title: up ? t("queue.moveUp") : t("queue.moveDown"),
        disabled,
      },
      icon("chevron"),
    );
    button.addEventListener("click", () => {
      const from = messages.findIndex((other) => other.id === message.id);
      const to = from + (up ? -1 : 1);
      if (from >= 0 && to >= 0 && to < messages.length) {
        order(moved(message.id, to));
      }
    });
    return button;
  }

  function row(message, index) {
    const editing = message.id === editingId;
    // The two arrows move the message a place; there is no drag, which on a phone took the swipe that scrolls the chat
    // (item 1348). An arrow at an end of the queue is disabled.
    const move = el(
      "span",
      { class: "queue-move" },
      moveButton(message, "up", index === 0),
      moveButton(message, "down", index === messages.length - 1),
    );

    const words = oneLine(message.text);
    const text = el("span", { class: "queue-text", title: message.text }, words);
    const files = message.files.length;
    const clip = files === 0 ? null : el("span", { class: "queue-files", title: tCount(files, "queue.countFile", "queue.countFiles") }, icon("paperclip"), String(files));
    let actions;
    if (editing) {
      const cancel = el(
        "button",
        { class: "queue-cancel", type: "button", "data-id": message.id, "data-part": "cancel", "aria-label": t("queue.cancelEditingQueuedMessage") },
        t("queue.cancel"),
      );
      cancel.addEventListener("click", () => {
        endEdit();
        paint();
      });
      actions = el("span", { class: "queue-actions" }, cancel);
    } else {
      const edit = el(
        "button",
        { class: "queue-icon", type: "button", "data-id": message.id, "data-part": "edit", "aria-label": t("queue.editQueuedMessage"), title: t("queue.editInTheComposer") },
        icon("pencil"),
      );
      edit.addEventListener("click", () => startEdit(message));
      const nowButton = el(
        "button",
        {
          class: "queue-steer",
          type: "button",
          "data-id": message.id,
          "data-part": "now",
          title: foreign ? t("queue.sendNowElsewhere") : active ? t("queue.sendNowStops") : t("queue.sendNowStarts"),
          disabled: foreign,
        },
        icon("arrowUp"),
        el("span", { class: "label" }, t("queue.sendNow")),
      );
      nowButton.addEventListener("click", () => sendNow(message));
      const remove = el(
        "button",
        { class: "queue-icon", type: "button", "data-id": message.id, "data-part": "remove", "aria-label": t("queue.removeQueuedMessage"), title: t("queue.removeFromQueue") },
        icon("x"),
      );
      remove.addEventListener("click", () =>
        act(() => api.del(`/queue/${message.id}`), { method: "DELETE", path: `/queue/${message.id}`, body: null }),
      );
      actions = el("span", { class: "queue-actions" }, edit, nowButton, remove);
    }

    const item = el("li", { class: "queue-row" }, move, text, clip, actions);
    if (editing) {
      item.setAttribute("aria-current", "true");
    }
    return item;
  }

  function paint() {
    element.hidden = messages.length === 0 && problem === null;
    head.hidden = messages.length === 0;
    unread.hidden = problem === null;
    unreadText.textContent = problem === null ? "" : t("queue.theQueueCouldNotBe", { problem });
    element.setAttribute("aria-label", t("queue.lengthQueuedMessageValue", { length: messages.length, value: messages.length === 1 ? "" : "s" }));
    count.textContent = String(messages.length);
    head.setAttribute("aria-expanded", String(expanded));
    head.setAttribute("aria-label", expanded ? t("queue.collapseQueuedMessages") : t("queue.expandQueuedMessages"));
    element.toggleAttribute("data-collapsed", !expanded);
    list.hidden = !expanded;
    paused.hidden = !held;
    resume.disabled = busy;
    // The rows are drawn anew, on every change of the queue from any page: the control that has the focus hands it to
    // its like in the new rows (the arrow pressed moved with its message), or to the box once that is gone; in a phone's
    // sheet (item 1383), which covers the box, to the sheet, so that no keyboard rises behind it.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && list.contains(active) ? active : null;
    list.replaceChildren(...messages.map(row));
    if (focused !== null) {
      // The arrow pressed has moved with its message; at the end of the queue it is disabled, and the focus goes to the
      // other arrow of the row, or to its pencil, before it goes to the box.
      const parts = [focused.dataset.part, focused.dataset.part === "up" ? "down" : "up", "edit"];
      const next = parts
        .map((part) => /** @type {HTMLButtonElement | null} */ (list.querySelector(`[data-id="${focused.dataset.id}"][data-part="${part}"]`)))
        .find((button) => button !== null && !button.disabled);
      const sheet = element.closest(".dock-sheet-panel");
      if (next === undefined && sheet instanceof HTMLElement) {
        sheet.focus({ preventScroll: true });
      } else {
        (next ?? frame.textarea).focus();
      }
    }
    tell();
  }

  /** Say what the queue holds to whoever draws it beside the composer (`onChange`). */
  function tell() {
    onChange({ count: messages.length, held, problem: problem !== null, sending: sendingNow });
  }

  const stopLifecycle = onLifecycle((event) => {
    if (event.kind !== QUEUE_CHANGED || event.data.thread_id !== threadId) {
      return;
    }
    if (event.data.error !== null) {
      toast("error", t("queue.theNextQueuedMessageCould", { error: event.data.error }));
    }
    if (event.data.started_run_id !== null) {
      onStarted(event.data.started_run_id, event.data.after_run_id);
    }
    load();
  });
  const stopBack = onStreamBack(load);

  load();

  // `add` and `saveEdit` throw what fails, so that the page keeps the composer's text when its message is not kept.
  return {
    element,
    add: async (body) => {
      const mine = ++reads;
      // While the run works the fast model is asked whether the message stands on its own (item 915), which takes a
      // few seconds: the queue says the message is on its way meanwhile (the pre-push review).
      const sending = el("div", { class: "queue-sending small muted", role: "status" }, t("queue.sending"));
      const hidden = element.hidden;
      element.hidden = false;
      element.append(sending);
      sendingNow = true;
      tell();
      let view;
      try {
        view = await api.post(`/threads/${threadId}/queue`, body);
      } finally {
        sending.remove();
        element.hidden = hidden;
        sendingNow = false;
        tell();
      }
      showNewest(mine, view);
      // A request of its own started at once in a chat of its own (item 915): said, with the way to it.
      if (typeof view.beside_run_id === "string") {
        const runId = view.beside_run_id;
        toast("ok", t("queue.aRequestOfItsOwn"), {
          label: t("queue.open"),
          run: () => {
            goTo(`#/runs/${runId}`);
          },
        });
      }
    },
    // The queue as it was last shown, for the copy of the chat the phone keeps (item 1520); null while none was read.
    view: () => (shownRead === 0 ? null : { held, messages }),
    // The queue of the copy the chat is drawn from, shown until the daemon's own answer comes; an answer that came first
    // stands, and a queue that could not be read says so beside it.
    seed: (view) => {
      if (shownRead === 0 && view !== null && typeof view === "object" && Array.isArray(view.messages)) {
        messages = view.messages;
        held = view.held === true;
        paint();
      }
    },
    steerFirst: () => (messages.length > 0 && active ? steer(messages[0]) : Promise.resolve()),
    setActive: (on) => {
      active = on;
      paint();
    },
    setForeign: (on) => {
      foreign = on;
      paint();
    },
    editing: () => editingId,
    saveEdit: async (text) => {
      const mine = ++reads;
      const body = { text, attachments: frame.attachments.ids() };
      const path = `/queue/${editingId}`;
      const view = await viaOutbox({ kind: "queue", chat: threadId, method: "PATCH", path, body }, () => api.patch(path, body));
      endEdit();
      if (view !== null && typeof view === "object" && "kept" in view) {
        return;
      }
      showNewest(mine, view);
    },
    cancelEdit: () => {
      endEdit();
      paint();
    },
    count: () => messages.length,
    expand: () => {
      expanded = true;
      paint();
    },
    dispose: () => {
      leaving.abort();
      stopLifecycle();
      stopBack();
    },
  };
}
