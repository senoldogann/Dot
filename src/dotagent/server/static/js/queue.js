// The queue of a chat (item 148): what is sent while a run works waits above the composer and starts
// as the chat's next run, one message after the other, once the run before it has ended done. A message can be moved
// (its handle is dragged, or takes the arrow keys), edited in the composer, sent to the run at work now (Steer) or
// taken out. A stop, a failure or a crash of the daemon holds the queue until Resume; a run that a stop of the daemon
// cut leaves it to go on once the run, continued, ends done. The daemon keeps the queue, so it goes on with no page
// open, and every open page shows it as it changes; a queue the page could not read says so, with Read again.

import { api } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, toast } from "./ui.js";

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
 * Returns `{ element, add(body), steerFirst(), setActive(on), editing(), saveEdit(text), cancelEdit(), count(),
 * expand(), dispose() }`: `add` queues a message (the body of `POST /threads/{id}/queue`), `editing()` is the id of the
 * message the composer edits or null, `saveEdit` keeps the composer's text as that message's, `count()` is how many
 * messages wait and `expand()` opens their list. `onCount(count)` is called on every change of the queue (item 352: the
 * line over the composer counts them).
 */
export function createQueue({ threadId, frame, onSteered, onStarted, onEdit, onCount }) {
  const listId = `queue-${threadId}`;
  let messages = [];
  let held = false;
  let active = false;
  let expanded = true;
  let busy = false;
  // Why the queue could not be read the last time, or null once it was.
  let problem = null;
  // The message the composer edits, and the draft it held before.
  let editingId = null;
  let draft = "";
  let draftFiles = [];
  // The row whose handle was pressed, which alone may be dragged; the row dragged and where it would land.
  let armedId = null;
  let draggedId = null;
  let dropIndex = null;

  const element = el("section", { class: "queue", role: "region", "aria-live": "polite", hidden: true });
  const toggleIcon = icon("chevron");
  toggleIcon.classList.add("queue-chevron");
  const count = el("span", { class: "queue-count" });
  const head = el(
    "button",
    { class: "queue-head", type: "button", "aria-controls": listId },
    icon("listOrdered"),
    el("span", { class: "queue-title" }, "Queued"),
    count,
    toggleIcon,
  );
  const resume = el("button", { class: "btn sm ghost", type: "button" }, "Resume");
  const paused = el("div", { class: "queue-paused", hidden: true }, el("span", null, "Paused until you resume it"), resume);
  const unreadText = el("span");
  const readAgain = el("button", { class: "btn sm ghost", type: "button" }, "Read again");
  const unread = el("div", { class: "queue-unread", hidden: true }, unreadText, readAgain);
  const list = el("ol", { class: "queue-list", id: listId });
  element.append(head, unread, paused, list);

  head.addEventListener("pointerdown", (event) => event.preventDefault());
  head.addEventListener("click", () => {
    expanded = !expanded;
    paint();
  });
  resume.addEventListener("click", () => act(() => api.post(`/threads/${threadId}/queue/resume`, null)));
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
      view = await api.get(`/threads/${threadId}/queue`);
    } catch (error) {
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

  /** Run one request of the queue at a time, the others being ignored; its answer is the queue as it then is. */
  async function act(request) {
    if (busy) {
      return;
    }
    busy = true;
    paint();
    try {
      await attempt(async () => {
        const mine = ++reads;
        showNewest(mine, await request());
      });
    } finally {
      busy = false;
      paint();
    }
  }

  function order(ids) {
    return act(() => api.put(`/threads/${threadId}/queue`, { ids }));
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

  function row(message, index) {
    const editing = message.id === editingId;
    const grip = el(
      "button",
      {
        class: "queue-grip",
        type: "button",
        "data-id": message.id,
        "data-part": "grip",
        "aria-label": "Reorder queued message (drag, or press the arrow keys)",
        disabled: messages.length < 2,
      },
      icon("grip"),
    );
    grip.addEventListener("pointerdown", () => {
      armedId = message.id;
    });
    grip.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
      const to = index + step;
      if (step === 0 || to < 0 || to >= messages.length) {
        return;
      }
      event.preventDefault();
      order(moved(message.id, to));
    });

    const words = oneLine(message.text);
    const text = el("span", { class: "queue-text", title: message.text }, words);
    const files = message.files.length;
    const clip = files === 0 ? null : el("span", { class: "queue-files", title: `${files} file${files === 1 ? "" : "s"}` }, icon("paperclip"), String(files));
    let actions;
    if (editing) {
      const cancel = el(
        "button",
        { class: "queue-cancel", type: "button", "data-id": message.id, "data-part": "cancel", "aria-label": "Cancel editing queued message" },
        "Cancel",
      );
      cancel.addEventListener("click", () => {
        endEdit();
        paint();
      });
      actions = el("span", { class: "queue-actions" }, cancel);
    } else {
      const edit = el(
        "button",
        { class: "queue-icon", type: "button", "data-id": message.id, "data-part": "edit", "aria-label": "Edit queued message", title: "Edit in the composer" },
        icon("pencil"),
      );
      edit.addEventListener("click", () => startEdit(message));
      const steerTitle = active ? "Send to the run now, as a steer" : "No run here can be steered";
      const steerButton = el(
        "button",
        { class: "queue-steer", type: "button", "data-id": message.id, "data-part": "steer", title: steerTitle, disabled: !active },
        icon("cornerUpRight"),
        el("span", { class: "label" }, "Steer"),
      );
      steerButton.addEventListener("click", () => steer(message));
      const remove = el(
        "button",
        { class: "queue-icon", type: "button", "data-id": message.id, "data-part": "remove", "aria-label": "Remove queued message", title: "Remove from queue" },
        icon("x"),
      );
      remove.addEventListener("click", () => act(() => api.del(`/queue/${message.id}`)));
      actions = el("span", { class: "queue-actions" }, edit, steerButton, remove);
    }

    const item = el("li", { class: "queue-row", draggable: String(messages.length > 1) }, grip, text, clip, actions);
    if (editing) {
      item.setAttribute("aria-current", "true");
    }
    item.addEventListener("dragstart", (event) => {
      if (armedId !== message.id) {
        event.preventDefault();
        return;
      }
      event.dataTransfer.setData("text/plain", message.id);
      event.dataTransfer.effectAllowed = "move";
      draggedId = message.id;
      item.classList.add("dragging");
    });
    item.addEventListener("dragend", () => {
      armedId = null;
      draggedId = null;
      dropIndex = null;
      paint();
    });
    item.addEventListener("dragover", (event) => {
      if (draggedId === null) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const box = item.getBoundingClientRect();
      const at = event.clientY < box.top + box.height / 2 ? index : index + 1;
      if (at !== dropIndex) {
        dropIndex = at;
        markDrop();
      }
    });
    item.addEventListener("drop", (event) => {
      if (draggedId === null || dropIndex === null) {
        return;
      }
      event.preventDefault();
      const from = messages.findIndex((other) => other.id === draggedId);
      // Taking the row out first moves every place after it up by one.
      const to = dropIndex > from ? dropIndex - 1 : dropIndex;
      const id = draggedId;
      draggedId = null;
      dropIndex = null;
      if (to !== from) {
        order(moved(id, to));
      } else {
        paint();
      }
    });
    return item;
  }

  /** Mark where the dragged message would land, on the rows as they are. */
  function markDrop() {
    const items = [...list.children];
    items.forEach((item, index) => {
      item.classList.toggle("drop-before", index === dropIndex);
      item.classList.toggle("drop-after", dropIndex === items.length && index === items.length - 1);
    });
  }

  function paint() {
    // A drag keeps the rows it started on, which are drawn anew once it ends.
    if (draggedId !== null) {
      return;
    }
    element.hidden = messages.length === 0 && problem === null;
    head.hidden = messages.length === 0;
    unread.hidden = problem === null;
    unreadText.textContent = problem === null ? "" : `The queue could not be read: ${problem}`;
    element.setAttribute("aria-label", `${messages.length} queued message${messages.length === 1 ? "" : "s"}`);
    count.textContent = String(messages.length);
    head.setAttribute("aria-expanded", String(expanded));
    head.setAttribute("aria-label", expanded ? "Collapse queued messages" : "Expand queued messages");
    element.toggleAttribute("data-collapsed", !expanded);
    list.hidden = !expanded;
    paused.hidden = !held;
    resume.disabled = busy;
    // The rows are drawn anew, on every change of the queue from any page: the control that has the focus hands it to
    // its like in the new rows (a handle moved from the keyboard to its new place), or to the box once that is gone.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && list.contains(active) ? active : null;
    list.replaceChildren(...messages.map(row));
    if (focused !== null) {
      const like = /** @type {HTMLButtonElement | HTMLTextAreaElement | null} */ (
        list.querySelector(`[data-id="${focused.dataset.id}"][data-part="${focused.dataset.part}"]`)
      );
      (like !== null && !like.disabled ? like : frame.textarea).focus();
    }
    onCount(messages.length);
  }

  const stopLifecycle = onLifecycle((event) => {
    if (event.kind !== QUEUE_CHANGED || event.data.thread_id !== threadId) {
      return;
    }
    if (event.data.error !== null) {
      toast("error", `The next queued message could not start: ${event.data.error}`);
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
      showNewest(mine, await api.post(`/threads/${threadId}/queue`, body));
    },
    steerFirst: () => (messages.length > 0 && active ? steer(messages[0]) : Promise.resolve()),
    setActive: (on) => {
      active = on;
      paint();
    },
    editing: () => editingId,
    saveEdit: async (text) => {
      const mine = ++reads;
      const view = await api.patch(`/queue/${editingId}`, { text, attachments: frame.attachments.ids() });
      endEdit();
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
      stopLifecycle();
      stopBack();
    },
  };
}
