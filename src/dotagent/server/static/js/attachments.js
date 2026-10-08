// Files attached to a message: picked, dropped or pasted, uploaded at once, and listed with their state until the message goes.

import { api } from "./api.js";
import { clip, el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";

// The daemon's limits (agent/attachments.py).
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 10;
const NAME_CHARS = 40;

export function sizeLabel(size) {
  if (size < 1024) {
    return `${size} B`;
  }
  return size < 1024 * 1024 ? `${Math.round(size / 1024)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
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

/** The attachments a message carried, as chips (used by the timeline for sent messages). */
export function sentFiles(files) {
  return el(
    "div",
    { class: "attach-list sent" },
    files.map((file) =>
      el("span", { class: "attach-chip ready", title: file.name }, icon(file.kind === "image" ? "image" : "file"), el("span", null, clip(file.name, NAME_CHARS)), el("span", { class: "muted" }, sizeLabel(file.size))),
    ),
  );
}

/**
 * The attach button, the list of attached files and the drop/paste handling for one message box. `dropTarget` takes
 * dropped files and `input` pasted ones. `ids()` names the uploaded files for the message, `busy()` is true while one
 * is still uploading, and `clear()` empties the list after the message went.
 */
export function createAttachments({ dropTarget, input }) {
  const entries = [];
  const picker = el("input", { type: "file", multiple: true, hidden: true, "aria-hidden": "true", tabindex: "-1" });
  const button = el(
    "button",
    { class: "btn ghost sm attach-button", type: "button", title: "Attach files (or drop or paste them)", "aria-label": "Attach files", onclick: () => picker.click() },
    icon("paperclip"),
  );
  const list = el("div", { class: "attach-list", hidden: true });

  function paint() {
    list.hidden = entries.length === 0;
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
  }

  async function upload(entry) {
    try {
      if (entry.file.size > MAX_BYTES) {
        throw new Error(`larger than ${sizeLabel(MAX_BYTES)}`);
      }
      const data = await readBase64(entry.file);
      const stored = await api.post("/attachments", { name: entry.file.name, media_type: entry.file.type, data_b64: data });
      entry.id = stored.id;
      setState(entry, "ready", sizeLabel(stored.size));
    } catch (error) {
      setState(entry, "failed", `Failed: ${error.message}`);
      entry.node.title = `${entry.file.name}: ${error.message}`;
    }
  }

  function add(files) {
    for (const file of files) {
      if (entries.length >= MAX_FILES) {
        toast("error", `At most ${MAX_FILES} files go with one message`);
        return;
      }
      const isImage = file.type.startsWith("image/");
      const preview = isImage ? URL.createObjectURL(file) : null;
      const status = el("span", { class: "muted" }, "Uploading…");
      const entry = { file, id: null, state: "uploading", preview, status, node: null };
      entry.node = el(
        "span",
        { class: "attach-chip uploading", title: file.name },
        preview === null ? icon("file") : el("img", { class: "attach-thumb", src: preview, alt: "" }),
        el("span", null, clip(file.name, NAME_CHARS)),
        status,
        el("button", { class: "btn ghost sm attach-remove", type: "button", title: "Remove", "aria-label": `Remove ${file.name}`, onclick: () => remove(entry) }, icon("x")),
      );
      entries.push(entry);
      list.append(entry.node);
      upload(entry);
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
    if (event.clipboardData !== null && event.clipboardData.files.length > 0) {
      event.preventDefault();
      add([...event.clipboardData.files]);
    }
  });

  return {
    button,
    picker,
    list,
    ids: () => entries.filter((entry) => entry.state === "ready").map((entry) => entry.id),
    busy: () => entries.some((entry) => entry.state === "uploading"),
    failed: () => entries.some((entry) => entry.state === "failed"),
    clear: () => {
      for (const entry of [...entries]) {
        remove(entry);
      }
    },
  };
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
