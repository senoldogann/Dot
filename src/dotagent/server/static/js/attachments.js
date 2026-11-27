// Files attached to a message: picked, dropped or pasted, uploaded at once, and listed with their state until the message goes.
// A long text pasted goes as a file of its own (item 269), and a file listed opens beside the conversation once uploaded.

import { api, blobUrl, partialBlob, uploadFile } from "./api.js";
import { clip, el, requestFile } from "./dom.js";
import { icon } from "./icons.js";
import { readsAsMarkdown } from "./markdown.js";
import { quotedFilesMessage, splitQuote } from "./quotebutton.js";
import { smallerPicture } from "./shrink.js";
import { toast } from "./ui.js";

// The flight of a picture Snapshots brought into its chip (item 414): as wide as this share of the page, at most this
// many pixels, from this far down the page, this long (another coding agent's captures fly as long).
const FLIGHT_SHARE = 0.5;
const FLIGHT_MAX_PX = 480;
const FLIGHT_TOP_SHARE = 0.18;
const FLIGHT_MS = 520;
// The daemon's limits (agent/attachments.py): a document or a picture, and a video or a sound (item 1933).
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_MEDIA_BYTES = 200 * 1024 * 1024;
// A video or a sound by its name when its type is empty or generic, as the daemon tells one (`_MEDIA_SUFFIXES`).
const VIDEO_SUFFIX = /\.(mov|mp4|m4v|3gp|webm|mkv)$/i;
// The most of a video's start read for its tile (its first frame and its length), and the longest side of the frame kept.
const TILE_BYTES = 4 * 1024 * 1024;
const FRAME_EDGE = 240;
const MEDIA_SUFFIX = /\.(mov|mp4|m4v|3gp|webm|mkv|caf|m4a|mp3|aac|amr|wav)$/i;
// A file above this size goes as the raw body of `PUT /attachments/stream`, written to disk as it arrives; a smaller
// one as base64 in JSON, which every daemon takes.
const STREAM_FROM_BYTES = 2 * 1024 * 1024;
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

/** Whether `file` is a video or a sound, as the daemon tells one: by its type, else by its name. */
export function isMedia(file) {
  return file.type.startsWith("video/") || file.type.startsWith("audio/") || MEDIA_SUFFIX.test(file.name);
}

/** The most bytes the daemon keeps of `file`: a video or a sound is kept as it came, up to 200 MiB. */
export function limitOf(file) {
  return isMedia(file) ? MAX_MEDIA_BYTES : MAX_BYTES;
}

/** Whether `file` goes as the raw body of the streamed upload: a video or a sound, or any file past a few megabytes. */
export function goesStreamed(file) {
  return isMedia(file) || file.size > STREAM_FROM_BYTES;
}

/** Whether `file` is a video (not a sound): it stands as a tile with its first frame and its length (item 1992). */
export function isVideo(file) {
  return file.type.startsWith("video/") || (!file.type.startsWith("audio/") && VIDEO_SUFFIX.test(file.name));
}

/** A length as the tiles say it, on the phone too: "0:14", "12:03". */
export function lengthWords(seconds) {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * The first frame (a JPEG data URL) and the length in seconds of the video in `blob`, read by a `<video>` that is never
 * shown; both `null` when the browser cannot read it (a codec it lacks, the index of the file past what was given).
 */
function videoFrame(blob) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    const done = (frame, length) => {
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      video.load();
      resolve({ frame, length });
    };
    video.addEventListener("error", () => done(null, null));
    video.addEventListener("loadedmetadata", () => {
      video.currentTime = Number.isFinite(video.duration) ? Math.min(0.1, video.duration / 2) : 0;
    });
    video.addEventListener("seeked", () => {
      const scale = Math.min(1, FRAME_EDGE / Math.max(video.videoWidth, video.videoHeight, 1));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
      done(canvas.toDataURL("image/jpeg", 0.7), Number.isFinite(video.duration) ? video.duration : null);
    });
    video.src = url;
  });
}

/** The play glyph, the length and the ring a video's tile wears over its picture; the ring fills as the file goes (0 to 1). */
function tileOverlay() {
  const play = el("span", { class: "media-play", "aria-hidden": "true" }, icon("play"));
  const length = el("span", { class: "media-length", hidden: true });
  const ring = el("span", { class: "media-ring", hidden: true, role: "progressbar", "aria-label": "Uploading" });
  return { play, length, ring, nodes: [play, length, ring] };
}

function showLength(overlay, seconds) {
  if (seconds !== null) {
    overlay.length.textContent = lengthWords(seconds);
    overlay.length.hidden = false;
  }
}

/**
 * Play `source` (a `blob:` address, or the promise of one) full screen over the page with the browser's controls, from a
 * button that stays where it was: Escape, the × and a click outside close it, and the video stops (item 1992).
 */
function playVideo(name, source) {
  const before = document.activeElement;
  const video = el("video", { class: "player-video", controls: true, autoplay: true, playsinline: true, "aria-label": `Video ${name}` });
  const status = el("p", { class: "player-status muted" }, "Loading…");
  const close = el("button", { class: "btn ghost player-close", type: "button", "aria-label": "Close", onclick: () => finish() }, icon("x"));
  const overlay = el("div", { class: "player-overlay", role: "dialog", "aria-modal": "true", "aria-label": `Video ${name}` }, close, status, video);
  let url = null;
  const keys = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      finish();
    }
  };
  function finish() {
    document.removeEventListener("keydown", keys, true);
    video.pause();
    video.removeAttribute("src");
    video.load();
    overlay.remove();
    if (url !== null && typeof source !== "string") {
      URL.revokeObjectURL(url);
    }
    if (before instanceof HTMLElement) {
      before.focus();
    }
  }
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      finish();
    }
  });
  document.addEventListener("keydown", keys, true);
  document.body.append(overlay);
  close.focus();
  Promise.resolve(source).then(
    (address) => {
      url = address;
      status.hidden = true;
      video.src = address;
    },
    (error) => {
      status.textContent = `The video could not be read: ${error.message}`;
    },
  );
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
  const others = files.filter((file) => file.kind !== "image" && file.kind !== "video");
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

/** The first frame and the length of the video kept as attachment `id`, read from the start of its file; both `null` when
 * they cannot be (the tile is then a plain one, which plays all the same). */
async function readTile(id) {
  try {
    return await videoFrame(await partialBlob(`/attachments/${encodeURIComponent(id)}/media`, TILE_BYTES));
  } catch {
    return { frame: null, length: null };
  }
}

/** One video a message carried: a tile of its first frame with a play glyph and its length, which plays it full screen (item
 * 1992). The frame and the length are read from the start of the file the daemon keeps; when they cannot be, the tile is
 * a plain one that plays all the same. */
function sentVideo(file) {
  const overlay = tileOverlay();
  const picture = el("img", { class: "sent-picture-image", alt: "", hidden: true });
  const tile = el(
    "button",
    {
      class: "sent-picture sent-video",
      type: "button",
      title: `Play video ${file.name}`,
      "aria-label": `Play video ${file.name}`,
      dataset: { attachment: file.id, name: file.name },
      onclick: () => playVideo(file.name, blobUrl(`/attachments/${encodeURIComponent(file.id)}/media`)),
    },
    picture,
    ...overlay.nodes,
  );
  readTile(file.id).then(({ frame, length }) => {
    if (frame !== null) {
      picture.src = frame;
      picture.hidden = false;
    }
    showLength(overlay, length);
  });
  return tile;
}

/** The pictures and videos a message carried, side by side as small squares: they stand over the message, as a chat
 * shows them. Null when it carried none. */
export function sentPictures(files) {
  const tiles = files.filter((file) => file.kind === "image" || file.kind === "video");
  return tiles.length === 0 ? null : el("div", { class: "attach-list sent pictures" }, tiles.map((file) => (file.kind === "video" ? sentVideo(file) : sentPicture(file))));
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

  // A video's tile plays it full screen: from the file on the page while it is there, else from the daemon's copy.
  function playEntry(entry) {
    playVideo(entry.name, entry.file !== null ? URL.createObjectURL(entry.file) : blobUrl(`/attachments/${encodeURIComponent(entry.id)}/media`));
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
    entry.picture = preview !== null || entry.kind === "image" || entry.kind === "video";
    entry.thumb = entry.picture ? el("img", { class: "attach-thumb", src: preview, alt: "" }) : icon("file");
    entry.overlay = entry.kind === "video" ? tileOverlay() : null;
    entry.open = el(
      "button",
      {
        class: "attach-open",
        type: "button",
        title: entry.kind === "video" ? `Play video ${name}` : `Show ${name}`,
        "aria-label": entry.kind === "video" ? `Play video ${name}` : entry.picture ? `Show ${name}` : null,
        disabled: true,
        onclick: (event) => (entry.kind === "video" ? playEntry(entry) : requestFile(event.currentTarget)),
      },
      entry.thumb,
      ...(entry.overlay === null ? [] : entry.overlay.nodes),
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
      // A large picture goes smaller (item 990): it is what made a phone's message wait.
      const sent = await smallerPicture(entry.file);
      if (sent.size > limitOf(sent)) {
        throw new Error(`larger than ${sizeLabel(limitOf(sent))}`);
      }
      const stored = goesStreamed(sent)
        ? await uploadFile(`/attachments/stream?${new URLSearchParams({ name: sent.name, media_type: sent.type })}`, sent, (share) => ring(entry, share))
        : await api.post("/attachments", { name: sent.name, media_type: sent.type, data_b64: await readBase64(sent) });
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

  // The ring of a video's tile fills with the share of the file sent, and goes when it is all there.
  function ring(entry, share) {
    if (entry.overlay === null) {
      return;
    }
    entry.overlay.ring.hidden = share >= 1;
    entry.overlay.ring.style.setProperty("--progress", String(share));
    entry.overlay.ring.setAttribute("aria-valuenow", String(Math.round(share * 100)));
  }

  // `file` listed and uploaded; `pasted` is the text it was made of (item 269), else null.
  function addOne(file, pasted) {
    const kind = file.type.startsWith("image/") ? "image" : isVideo(file) ? "video" : "file";
    const preview = kind === "image" ? URL.createObjectURL(file) : null;
    const status = el("span", { class: "muted" }, "Uploading…");
    const entry = { file, id: null, name: file.name, size: file.size, kind, state: "uploading", preview, pasted, status, open: null, node: null, picture: false, thumb: null };
    entry.node = chip(file.name, preview, status, entry);
    entries.push(entry);
    list.append(entry.node);
    if (entry.overlay !== null) {
      // The tile is on show at once; its frame and its length come when the browser has read the start of the file.
      ring(entry, 0);
      videoFrame(file).then(({ frame, length }) => {
        if (frame !== null) {
          entry.thumb.src = frame;
        }
        showLength(entry.overlay, length);
      });
    }
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

  /** A picture Snapshots brought flies into its chip (item 414), from the top of the page, where the eye is when
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
   * (a capture of Snapshots, item 405) and flies a picture into its chip (item 414). */
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
      // A draft's video is read from the daemon again for its first frame and its length.
      if (entry.kind === "video") {
        readTile(id).then(({ frame, length }) => {
          if (frame !== null) {
            entry.thumb.src = frame;
          }
          showLength(entry.overlay, length);
        });
      } else if (entry.picture) {
        // A draft's photo is read from the daemon again, to show itself where a file shows its name.
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
  // A quote alone (item 1972) is no message: the words after it decide.
  const words = splitQuote(text).words.trim() === "" ? "" : text.trim();
  // Files alone go with their line, and with the quote they were sent under.
  return words === "" && attachments.count() > 0 ? quotedFilesMessage(text, wordsForFiles(attachments)) : words;
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
