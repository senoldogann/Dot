// Files attached to a message: picked, dropped or pasted, uploaded at once, and listed with their state until the message goes.
// A long text pasted goes as a file of its own (item 269), and a file listed opens beside the conversation once uploaded.

import { api, blobUrl } from "./api.js";
import { clip, el, requestFile } from "./dom.js";
import { icon } from "./icons.js";
import { readsAsMarkdown } from "./markdown.js";
import { toast } from "./ui.js";

// The daemon's limits (agent/attachments.py).
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 10;
const NAME_CHARS = 40;
// A pasted text this long, in characters or in lines, goes as a file rather than into the box (item 269).
const LONG_PASTE_CHARS = 2000;
const LONG_PASTE_LINES = 40;

export function sizeLabel(size) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return size < 1024 ** 3 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024 ** 3).toFixed(1)} GB`;
}

/** Whether a pasted text goes as a file rather than into the box (item 269). */
function isLongPaste(text) {
  return text.length >= LONG_PASTE_CHARS || text.split("\n").length >= LONG_PASTE_LINES;
}

/** A pasted text as a file named for the time `at` (item 269): Markdown when it reads as Markdown, else plain text. */
function pastedFile(text, at) {
  const time = [at.getHours(), at.getMinutes(), at.getSeconds()].map((part) => String(part).padStart(2, "0")).join("");
  const markdown = readsAsMarkdown(text);
  return new File([text], `pasted-text-${time}.${markdown ? "md" : "txt"}`, { type: markdown ? "text/markdown" : "text/plain" });
}

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const url = String(reader.result);
      resolve(url.slice(url.indexOf(",") + 1));
    });
    reader.addEventListener("error", () => reject(reader.error ?? new Error("the file could not be read")));
    reader.readAsDataURL(file);
  });
}

/** The attachments a message carried, as chips (used by the timeline for sent messages); a chip shows its file beside the conversation. */
export function sentFiles(files) {
  return el(
    "div",
    { class: "attach-list sent" },
    files.map((file) =>
      el(
        "button",
        {
          class: "attach-chip ready",
          type: "button",
          title: `Show ${file.name}`,
          dataset: { attachment: file.id, name: file.name },
          onclick: (event) => requestFile(event.currentTarget),
        },
        icon(file.kind === "image" ? "image" : "file"),
        el("span", null, clip(file.name, NAME_CHARS)),
        el("span", { class: "muted" }, sizeLabel(file.size)),
      ),
    ),
  );
}

/**
 * The file picker, the list of attached files and the drop/paste handling for one message box (the composer's "+" menu
 * opens the picker, item 318). `dropTarget` takes dropped files and `input` pasted ones, and a long text pasted into
 * `input` as a file (item 269), which can be put back into the box as text. A file uploaded opens beside the
 * conversation from its chip (`requestFile`). `ids()` names the uploaded files for the message, `ready()` describes
 * them (`{ id, name, size }`) for a draft and `restore(files)` lists them again from one, `restoreSmall(files)` lists a
 * queued message's files small in the box that edits it (`{ id, name, size, kind }`, item 376), `count()` counts the
 * files listed, whatever their state, `busy()` is true while one is still uploading, `clear()` empties the list after
 * the message went, `setAside()` takes the files out of the list as they stand and `putBack(kept)` lists them again
 * (the box that edits a queued message borrows the list, item 376), and `onChange()` runs after every change.
 */
export function createAttachments({ dropTarget, input, onChange }) {
  const entries = [];
  const picker = el("input", { type: "file", multiple: true, hidden: true, "aria-hidden": "true", tabindex: "-1" });
  const list = el("div", { class: "attach-list", hidden: true });

  function paint() {
    list.hidden = entries.length === 0;
    onChange();
  }

  function remove(entry) {
    const at = entries.indexOf(entry);
    if (at !== -1) {
      entries.splice(at, 1);
    }
    if (entry.preview !== null) {
      URL.revokeObjectURL(entry.preview);
    }
    entry.node.remove();
    paint();
  }

  function setState(entry, state, text) {
    entry.state = state;
    entry.node.className = `attach-chip ${state}`;
    entry.status.textContent = text;
    onChange();
  }

  // The chip's file, uploaded, opens beside the conversation as a sent file's does.
  function opens(entry) {
    entry.open.dataset.attachment = entry.id;
    entry.open.dataset.name = entry.name;
    entry.open.disabled = false;
  }

  // A pasted text back in the box, where the caret is (item 269).
  function unpaste(entry) {
    const text = entry.pasted;
    remove(entry);
    input.focus();
    input.setRangeText(text, input.selectionStart, input.selectionEnd, "end");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function chip(name, preview, status, entry) {
    entry.open = el(
      "button",
      { class: "attach-open", type: "button", title: `Show ${name}`, disabled: true, onclick: (event) => requestFile(event.currentTarget) },
      preview === null ? icon("file") : el("img", { class: "attach-thumb", src: preview, alt: "" }),
      el("span", null, clip(name, NAME_CHARS)),
      status,
    );
    return el(
      "span",
      { class: "attach-chip uploading", title: name },
      entry.open,
      entry.pasted === null
        ? null
        : el(
            "button",
            { class: "btn ghost sm attach-unpaste", type: "button", title: "Put the text in the message instead", "aria-label": `Put ${name} in the message as text`, onclick: () => unpaste(entry) },
            icon("pencil"),
          ),
      el("button", { class: "btn ghost sm attach-remove", type: "button", title: "Remove", "aria-label": `Remove ${name}`, onclick: () => remove(entry) }, icon("x")),
    );
  }

  async function upload(entry) {
    try {
      if (entry.file.size > MAX_BYTES) {
        throw new Error(`larger than ${sizeLabel(MAX_BYTES)}`);
      }
      const data = await readBase64(entry.file);
      const stored = await api.post("/attachments", { name: entry.file.name, media_type: entry.file.type, data_b64: data });
      entry.id = stored.id;
      opens(entry);
      setState(entry, "ready", sizeLabel(stored.size));
    } catch (error) {
      setState(entry, "failed", `Failed: ${error.message}`);
      entry.node.title = `${entry.file.name}: ${error.message}`;
    }
  }

  // `file` listed and uploaded; `pasted` is the text it was made of (item 269), else null.
  function addOne(file, pasted) {
    const isImage = file.type.startsWith("image/");
    const preview = isImage ? URL.createObjectURL(file) : null;
    const status = el("span", { class: "muted" }, "Uploading…");
    const entry = { file, id: null, name: file.name, size: file.size, state: "uploading", preview, pasted, status, open: null, node: null };
    entry.node = chip(file.name, preview, status, entry);
    entries.push(entry);
    list.append(entry.node);
    upload(entry);
  }

  function add(files) {
    for (const file of files) {
      if (entries.length >= MAX_FILES) {
        toast("error", `At most ${MAX_FILES} files go with one message`);
        break;
      }
      addOne(file, null);
    }
    paint();
  }

  picker.addEventListener("change", () => {
    add([...picker.files]);
    picker.value = "";
  });
  dropTarget.addEventListener("dragover", (event) => {
    if (event.dataTransfer !== null && [...event.dataTransfer.types].includes("Files")) {
      event.preventDefault();
      dropTarget.classList.add("dropping");
    }
  });
  dropTarget.addEventListener("dragleave", () => dropTarget.classList.remove("dropping"));
  dropTarget.addEventListener("drop", (event) => {
    dropTarget.classList.remove("dropping");
    if (event.dataTransfer !== null && event.dataTransfer.files.length > 0) {
      event.preventDefault();
      add([...event.dataTransfer.files]);
    }
  });
  input.addEventListener("paste", (event) => {
    if (event.clipboardData === null) {
      return;
    }
    if (event.clipboardData.files.length > 0) {
      event.preventDefault();
      add([...event.clipboardData.files]);
      return;
    }
    // A long text goes as a file while there is room for one; else into the box, as any paste.
    const text = event.clipboardData.getData("text/plain");
    if (isLongPaste(text) && entries.length < MAX_FILES) {
      event.preventDefault();
      addOne(pastedFile(text, new Date()), text);
      paint();
    }
  });

  return {
    picker,
    list,
    ids: () => entries.filter((entry) => entry.state === "ready").map((entry) => entry.id),
    ready: () => entries.filter((entry) => entry.state === "ready").map((entry) => ({ id: entry.id, name: entry.name, size: entry.size })),
    restore: (files) => {
      for (const { id, name, size } of files) {
        const status = el("span", { class: "muted" }, sizeLabel(size));
        const entry = { file: null, id, name, size, state: "ready", preview: null, pasted: null, status, open: null, node: null };
        entry.node = chip(name, null, status, entry);
        entry.node.className = "attach-chip ready";
        opens(entry);
        entries.push(entry);
        list.append(entry.node);
      }
      paint();
    },
    restoreSmall: (files) => {
      for (const { id, name, size, kind } of files) {
        // A queued message's file in the box that edits it (item 376): small, the picture itself (or a file's icon)
        // and its ×, no words on it; the name is its tooltip and opens it beside the conversation.
        const entry = { file: null, id, name, size, state: "ready", preview: null, pasted: null, status: null, open: null, node: null };
        const picture = el("img", { class: "attach-thumb", alt: "" });
        entry.open = el(
          "button",
          { class: "attach-open", type: "button", title: `Show ${name}`, "aria-label": `Show ${name}`, disabled: true, onclick: (event) => requestFile(event.currentTarget) },
          kind === "image" ? picture : icon("file"),
        );
        entry.node = el(
          "span",
          { class: "attach-chip ready small", title: name },
          entry.open,
          el("button", { class: "btn ghost sm attach-remove", type: "button", title: "Remove", "aria-label": `Remove ${name}`, onclick: () => remove(entry) }, icon("x")),
        );
        if (kind === "image") {
          blobUrl(`/attachments/${encodeURIComponent(id)}/image`).then(
            (url) => {
              // A chip taken away before its picture came lets the picture go at once.
              if (entries.includes(entry)) {
                entry.preview = url;
                picture.src = url;
              } else {
                URL.revokeObjectURL(url);
              }
            },
            (error) => {
              // The picture could not be read: the chip says so where it stands, and the file still opens.
              entry.open.replaceChildren(icon("alert"));
              entry.open.title = `${name}: ${error.message}`;
            },
          );
        }
        opens(entry);
        entries.push(entry);
        list.append(entry.node);
      }
      paint();
    },
    count: () => entries.length,
    busy: () => entries.some((entry) => entry.state === "uploading"),
    failed: () => entries.some((entry) => entry.state === "failed"),
    clear: () => {
      for (const entry of [...entries]) {
        remove(entry);
      }
    },
    // Out of the list and back as they were: a photo keeps its picture, a long paste its way back into the box.
    setAside: () => {
      const kept = entries.splice(0, entries.length);
      for (const entry of kept) {
        entry.node.remove();
      }
      paint();
      return kept;
    },
    putBack: (kept) => {
      for (const entry of kept) {
        entries.push(entry);
        list.append(entry.node);
      }
      paint();
    },
  };
}

/**
 * A message is the owner's words, and the files that come with it are material to work with: with files listed and no
 * words, a send says so rather than nothing (item 269), since a long paste leaves the box empty.
 */
export function askForWords(attachments) {
  if (attachments.count() > 0) {
    toast("error", "Say in a few words what to do with the files, or put a pasted text back into the box with its pencil");
  }
}

/** Whether a message may go now; says why not when files are still uploading or failed. */
export function readyToSend(attachments) {
  if (attachments.busy()) {
    toast("error", "Wait until the files are uploaded");
    return false;
  }
  if (attachments.failed()) {
    toast("error", "Remove the files that failed to upload, or attach them again");
    return false;
  }
  return true;
}
