// Files attached to a message: picked, dropped or pasted, uploaded at once, and listed with their state until the message goes.
// A long text pasted goes as a file of its own (item 269), and a file listed opens beside the conversation once uploaded.

import { api, blobUrl } from "./api.js";
import { clip, el, requestFile } from "./dom.js";
import { icon } from "./icons.js";
import { readsAsMarkdown } from "./markdown.js";
import { toast } from "./ui.js";

// The flight of a picture SnapShots brought into its chip (item 414): as wide as this share of the page, at most this
// many pixels, from this far down the page, this long (another coding agent's captures fly as long).
const FLIGHT_SHARE = 0.5;
const FLIGHT_MAX_PX = 480;
const FLIGHT_TOP_SHARE = 0.18;
const FLIGHT_MS = 520;
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

/** The files a message carried that are no pictures, as chips (used by the timeline for sent messages); a chip shows
 * its file beside the conversation. Null when every file is a picture, which `sentPictures` shows. */
export function sentFiles(files) {
  const others = files.filter((file) => file.kind !== "image");
  if (others.length === 0) {
    return null;
  }
  return el(
    "div",
    { class: "attach-list sent" },
    others.map((file) =>
      el(
        "button",
        {
          class: "attach-chip ready",
          type: "button",
          title: `Show ${file.name}`,
          dataset: { attachment: file.id, name: file.name },
          onclick: (event) => requestFile(event.currentTarget),
        },
        icon("file"),
        el("span", null, clip(file.name, NAME_CHARS)),
        el("span", { class: "muted" }, sizeLabel(file.size)),
      ),
    ),
  );
}

// The blob URL of each picture a message carried, made once for the page on screen: a chat drawn again (a resync)
// shows the same picture without fetching it and opening a new URL each time, and a page change lets them go
// (`forgetSentPictures`), so they do not pile up in a window kept open for days.
const sentUrls = new Map();

/** The blob URL of the picture `id`, made once; one that could not be made is not kept, so the next drawing asks again
 * (the drawing that asked shows why it failed). */
function sentPictureUrl(id) {
  const kept = sentUrls.get(id);
  if (kept !== undefined) {
    return kept;
  }
  const made = blobUrl(`/attachments/${encodeURIComponent(id)}/image`);
  sentUrls.set(id, made);
  made.then(null, () => {
    if (sentUrls.get(id) === made) {
      sentUrls.delete(id);
    }
  });
  return made;
}

/** Let go of the sent pictures' blob URLs: the page that showed them has closed. */
export function forgetSentPictures() {
  for (const pending of sentUrls.values()) {
    // A picture that could not be read was reported where it was shown; there is no URL to let go of.
    pending.then((url) => URL.revokeObjectURL(url), () => undefined);
  }
  sentUrls.clear();
}

/** One picture a message carried, as a small square of the picture itself; its name is its tooltip, and a click shows
 * it beside the conversation. A picture that cannot be read shows the mark of a fault and why, and still opens. */
function sentPicture(file) {
  const picture = el("img", { class: "sent-picture-image", alt: "" });
  const tile = el(
    "button",
    {
      class: "sent-picture",
      type: "button",
      title: `Show ${file.name}`,
      "aria-label": `Show ${file.name}`,
      dataset: { attachment: file.id, name: file.name },
      onclick: (event) => requestFile(event.currentTarget),
    },
    picture,
  );
  sentPictureUrl(file.id).then(
    (url) => {
      picture.src = url;
    },
    (error) => {
      // The picture could not be read: the square says so where it stands, as a chip of the box does.
      tile.replaceChildren(icon("alert"));
      tile.title = `${file.name}: ${error.message}`;
      tile.setAttribute("aria-label", `${file.name}: ${error.message}`);
    },
  );
  return tile;
}

/** The pictures a message carried, side by side as small squares: they stand over the message, as a chat shows them.
 * Null when it carried none. */
export function sentPictures(files) {
  const pictures = files.filter((file) => file.kind === "image");
  return pictures.length === 0 ? null : el("div", { class: "attach-list sent pictures" }, pictures.map(sentPicture));
}

/**
 * The file picker, the list of attached files and the drop/paste handling for one message box (the composer's "+"
 * opens the picker, item 318). `dropTarget` takes dropped files and `input` pasted ones, and a long text pasted into
 * `input` as a file (item 269), which can be put back into the box as text. A file uploaded opens beside the
 * conversation from its chip (`requestFile`). `ids()` names the uploaded files for the message, `ready()` describes
 * them (`{ id, name, size, kind }`) for a draft and `restore(files)` lists them again from one, `restoreSmall(files)`
 * lists a queued message's files small in the box that edits it (`{ id, name, size, kind }`, item 376), `count()` counts
 * the files listed, whatever their state, `busy()` is true while one is still uploading, `clear()` empties the list after
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
    mark(entry, state);
    entry.status.textContent = text;
    onChange();
  }

  /** The chip wears its state, and a picture's tile its shape. */
  function mark(entry, state) {
    entry.node.className = entry.picture ? `attach-chip ${state} picture` : `attach-chip ${state}`;
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

  // A photo shows the picture itself, its name its tooltip; another file shows an icon, its name and its size. `preview`
  // is the picture's address once there is one.
  function chip(name, preview, status, entry) {
    entry.picture = preview !== null || entry.kind === "image";
    entry.thumb = entry.picture ? el("img", { class: "attach-thumb", src: preview, alt: "" }) : icon("file");
    entry.open = el(
      "button",
      {
        class: "attach-open",
        type: "button",
        title: `Show ${name}`,
        "aria-label": entry.picture ? `Show ${name}` : null,
        disabled: true,
        onclick: (event) => requestFile(event.currentTarget),
      },
      entry.thumb,
      entry.picture ? null : el("span", null, clip(name, NAME_CHARS)),
      entry.picture ? null : status,
    );
    return el(
      "span",
      { class: entry.picture ? "attach-chip uploading picture" : "attach-chip uploading", title: name },
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
      if (entry.picture) {
        // A photo that failed keeps its red tile and says why in its tooltip and to a screen reader; its × takes it away.
        entry.open.replaceChildren(icon("alert"));
        entry.open.setAttribute("aria-label", `${entry.file.name}: ${error.message}`);
      }
    }
  }

  // `file` listed and uploaded; `pasted` is the text it was made of (item 269), else null.
  function addOne(file, pasted) {
    const kind = file.type.startsWith("image/") ? "image" : "file";
    const preview = kind === "image" ? URL.createObjectURL(file) : null;
    const status = el("span", { class: "muted" }, "Uploading…");
    const entry = { file, id: null, name: file.name, size: file.size, kind, state: "uploading", preview, pasted, status, open: null, node: null, picture: false, thumb: null };
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

  function openPicker() {
    picker.click();
  }
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

  /** A picture SnapShots brought flies into its chip (item 414), from the top of the page, where the eye is when
   * Dot's tab comes to the front, unless the reader asked for less motion. */
  function flyIn(thumb, url) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !thumb.isConnected) {
      return;
    }
    const end = thumb.getBoundingClientRect();
    if (end.width === 0) {
      return;
    }
    const width = Math.min(window.innerWidth * FLIGHT_SHARE, FLIGHT_MAX_PX);
    const left = (window.innerWidth - width) / 2;
    const top = window.innerHeight * FLIGHT_TOP_SHARE;
    const flight = el("img", { class: "attach-flight", src: url, alt: "" });
    flight.style.left = `${left}px`;
    flight.style.top = `${top}px`;
    flight.style.width = `${width}px`;
    document.body.append(flight);
    const animation = flight.animate(
      [
        { transform: "translate(0, 0) scale(1)", opacity: 1 },
        { transform: `translate(${end.left - left}px, ${end.top - top}px) scale(${end.width / width})`, opacity: 0.85 },
      ],
      { duration: FLIGHT_MS, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
    );
    const land = () => flight.remove();
    animation.addEventListener("finish", land);
    animation.addEventListener("cancel", land);
  }

  /** List `files` (`{id, name, size, kind}`, uploaded already) as files of the draft; `animated` springs them in
   * (a capture of SnapShots, item 405) and flies a picture into its chip (item 414). */
  function restoreFiles(files, animated) {
    for (const { id, name, size, kind } of files) {
      if (entries.length >= MAX_FILES) {
        // A capture past the limit is left out here, where the owner sees it, not refused when the message is sent.
        toast("error", `At most ${MAX_FILES} files go with one message`);
        break;
      }
      const status = el("span", { class: "muted" }, sizeLabel(size));
      const entry = { file: null, id, name, size, kind: kind ?? "file", state: "ready", preview: null, pasted: null, status, open: null, node: null, picture: false, thumb: null };
      entry.node = chip(name, null, status, entry);
      mark(entry, "ready");
      // A draft's photo is read from the daemon again, to show itself where a file shows its name.
      if (entry.picture) {
        blobUrl(`/attachments/${encodeURIComponent(id)}/image`).then(
          (url) => {
            // A chip taken away before its picture came lets the picture go at once.
            if (entries.includes(entry)) {
              entry.preview = url;
              entry.thumb.src = url;
              if (animated) {
                flyIn(entry.thumb, url);
              }
            } else {
              URL.revokeObjectURL(url);
            }
          },
          (error) => {
            // The picture could not be read: the chip says so where it stands, and the file still opens.
            entry.open.replaceChildren(icon("alert"));
            entry.open.title = `${name}: ${error.message}`;
            entry.open.setAttribute("aria-label", `${name}: ${error.message}`);
          },
        );
      }
      opens(entry);
      entries.push(entry);
      list.append(entry.node);
      if (animated) {
        entry.node.classList.add("arrived");
      }
    }
    paint();
  }

  return {
    picker,
    openPicker,
    list,
    ids: () => entries.filter((entry) => entry.state === "ready").map((entry) => entry.id),
    ready: () => entries
      .filter((entry) => entry.state === "ready")
      .map((entry) => ({ id: entry.id, name: entry.name, size: entry.size, kind: entry.kind })),
    restore: (files) => restoreFiles(files, false),
    arrive: (files, animated) => restoreFiles(files, animated),
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
              entry.open.setAttribute("aria-label", `${name}: ${error.message}`);
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

/** What a message of files alone says, as the goal its run is given (item 445): a pasted text or a picture goes without a
 * word typed, and this line is what the run reads — the files themselves are what the owner meant. */
function wordsForFiles(attachments) {
  return attachments.count() === 1 ? "See the attached file." : "See the attached files.";
}

/** The words a message goes with (item 445): what is in the box, else the line its files are worth, else nothing — an
 * empty send with nothing in it is no message at all. */
export function messageText(text, attachments) {
  const words = text.trim();
  return words === "" && attachments.count() > 0 ? wordsForFiles(attachments) : words;
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
