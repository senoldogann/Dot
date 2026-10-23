// DOM helpers and formatters. Text always goes in as text nodes; nothing here ever builds markup from a string.

const SVG_NS = "http://www.w3.org/2000/svg";

function applyProps(node, props) {
  if (props === null || props === undefined) {
    return;
  }
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) {
      continue;
    }
    if (key === "class") {
      node.setAttribute("class", value);
    } else if (key === "dataset") {
      Object.assign(node.dataset, value);
    } else if (key === "style") {
      throw new Error("inline styles are blocked by the content security policy; use a class");
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (key === "value" || key === "checked" || key === "disabled" || key === "hidden") {
      node[key] = value;
    } else {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
}

function appendChildren(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) {
      continue;
    }
    if (Array.isArray(child)) {
      appendChildren(node, child);
    } else if (child instanceof Node) {
      node.append(child);
    } else {
      node.append(document.createTextNode(String(child)));
    }
  }
}

/** The event a button of a file sends up the page to show its file in the panel beside the conversation (item 195). */
export const OPEN_FILE_EVENT = "dot:open-file";
/** The file a button names: `{ attachment, name }` for a file a message carries, `{ path }` for one named by its path. */
function fileSource(node) {
  const { attachment, name, path } = node.dataset;
  return attachment === undefined ? { path } : { attachment, name };
}

/** Ask the page to show the file `node` names beside the conversation (filepanel.js). */
export function requestFile(node) {
  node.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { bubbles: true, detail: fileSource(node) }));
}

/** Create an HTML element: `el("div", { class: "x" }, "text", childNode)`. */
export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  applyProps(node, props);
  appendChildren(node, children);
  return node;
}

/** Create an SVG element. */
export function svg(tag, props, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  applyProps(node, props);
  appendChildren(node, children);
  return node;
}

/** Put `text` in `node` when it reads otherwise (item 310): the same text written again is a new text node, which the
 * browser lays out and paints anew for nothing, and a page that follows a run with helpers would do so on every event. */
export function setText(node, text) {
  if (node.textContent !== text) {
    node.textContent = text;
  }
}

/**
 * Make `nodes` the children of `parent`, in their order, moving only what is out of place (item 328). A child that
 * stays where it stood is not touched, so its animations go on and an open menu of it stays open; `replaceChildren`
 * takes every child out and puts it back.
 */
export function keepChildren(parent, nodes) {
  nodes.forEach((node, index) => {
    const there = parent.childNodes[index] ?? null;
    if (there !== node) {
      parent.insertBefore(node, there);
    }
  });
  while (parent.childNodes.length > nodes.length) {
    parent.lastChild.remove();
  }
}

/**
 * Let a tap anywhere on `row` turn the switch `toggle` in it, as a row of iOS's settings does (item 329): a tap on a
 * control of the row, the switch among them, is that control's own. Returns `row`.
 */
export function switchRow(row, toggle) {
  row.classList.add("switch-row");
  row.addEventListener("click", (event) => {
    if (event.target.closest("a, button, input, select, textarea, summary") === null) {
      toggle.click();
    }
  });
  return row;
}

/** Show `node` once what it tells has held for `ms`, and hide it as soon as it no longer holds: returns `set(shown)`.
 * `ms` is a number, or a function that tells it when the wait starts. A break of the connection that mends within it
 * (a phone back from the background) shows nothing (items 286 and 328). */
export function showAfter(node, ms) {
  let timer = null;
  return (shown) => {
    if (!shown) {
      clearTimeout(timer);
      timer = null;
      node.hidden = true;
    } else if (node.hidden && timer === null) {
      timer = setTimeout(
        () => {
          timer = null;
          node.hidden = false;
        },
        typeof ms === "function" ? ms() : ms,
      );
    }
  };
}

/** A function that runs `fn` once `ms` after the last call. */
export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    if (timer !== null) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, ms);
  };
}

export function clip(text, limit) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1)}…`;
}

/** The first `words` words of `text`, and at most `limit` characters, with an ellipsis when something was cut. */
export function clipWords(text, words, limit) {
  const flat = text.replace(/\s+/g, " ").trim();
  const parts = flat.split(" ");
  const short = parts.length <= words ? flat : `${parts.slice(0, words).join(" ")}…`;
  return short.length <= limit ? short : clip(short, limit);
}

// A reply of fewer words, after its first line, is the answer said again only word for word (item 567).
const REPEAT_MIN_WORDS = 20;
// A reply's first line is the answer's reworded when at least this share of its words are in the answer's first line.
const REWORDED_SHARE = 0.5;

/** The words of a text as they are compared: its marks of markdown left out, in lower case. */
function wordsOf(text) {
  return text
    .toLowerCase()
    .replace(/[*_`#>[\]()|]/g, " ")
    .split(/\s+/)
    .filter((word) => word !== "");
}

/** The lines of a text as they are compared, each its words (wordsOf) joined, the blank ones left out. */
function linesOf(text) {
  return text
    .split("\n")
    .map((line) => wordsOf(line).join(" "))
    .filter((line) => line !== "");
}

/**
 * Whether `line` is `other` reworded, its punctuation left out: at least half its words are in `other`, and it says
 * nothing more. A line that only adds to the other ("The tests do not pass:" for "The tests pass:") or has a number of
 * its own says more.
 */
function rewordedFrom(line, other) {
  const plain = (text) => text.split(/[^\p{L}\p{N}]+/u).filter((word) => word !== "");
  const words = plain(line);
  const theirs = new Set(plain(other));
  const mine = new Set(words);
  const added = words.filter((word) => !theirs.has(word));
  const adds = added.length > 0 && [...theirs].every((word) => mine.has(word));
  if (words.length === 0 || adds || added.some((word) => /\p{N}/u.test(word))) {
    return false;
  }
  return words.length - added.length >= words.length * REWORDED_SHARE;
}

/**
 * Whether a run's reply says nothing its answer does not (item 567), so that the answer alone may stand for it: the
 * same words, whatever their spacing and marks, or the same lines after a first line the answer rewords, those lines
 * twenty words and more. A model told to finish after a plain reply finishes with that reply again and may reword its
 * first line on the way. A reply with a line the answer lacks, more on one of its lines, or a first line of its own is
 * shown, however much of it the answer holds: what it adds would be lost.
 */
export function repeatsAnswer(reply, answer) {
  if (wordsOf(reply).join(" ") === wordsOf(answer).join(" ")) {
    return true;
  }
  const said = linesOf(reply);
  const told = linesOf(answer);
  const rest = said.slice(1);
  if (said.length !== told.length || rest.join(" ").split(" ").length < REPEAT_MIN_WORDS) {
    return false;
  }
  return rest.join("\n") === told.slice(1).join("\n") && rewordedFrom(said[0], told[0]);
}

/**
 * `text` as it is compared when looking for a name: case, accents and the Turkish dotted and dotless i do not matter, so
 * "İstanbul", "ISTANBUL" and "istanbul" are one word, and so are "Işık" and "isik". The same fold as `fold_search` in
 * `core/text.py`: going up to capitals and back down lowers the dotless i like Python's `casefold`, and the two letters
 * that still differ after it (ß, the final sigma) are set right by hand.
 */
export function foldSearch(text) {
  const lower = text.toUpperCase().toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
  return lower.normalize("NFKD").replace(/\p{M}/gu, "").trim();
}

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const DATE_TIME_FULL = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "medium",
  hour12: false,
});
const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const HOUR_MINUTE = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const DAY_YEAR = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const DAY_MS = 86400 * 1000;
const WEEK_S = 7 * 86400;

/** "5 Oct, 14:03" */
export function dateTime(ts) {
  return DATE_TIME.format(new Date(ts * 1000));
}

/** "5 Oct 2026, 14:03:22" */
export function dateTimeFull(ts) {
  return DATE_TIME_FULL.format(new Date(ts * 1000));
}

export function clock(ts) {
  return CLOCK.format(new Date(ts * 1000));
}

/** "14:03" */
export function hourMinute(ts) {
  return HOUR_MINUTE.format(new Date(ts * 1000));
}

/** The start of the local day of `date`, in milliseconds. */
function dayStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * When a message was sent or an answer came, as short as its day allows (item 205): "14:03" today,
 * "Yesterday, 14:03", "5 Oct, 14:03" this year, "5 Oct 2025, 14:03" before.
 */
export function messageTime(ts) {
  const date = new Date(ts * 1000);
  const now = new Date();
  // Rounded, so that a day of 23 or 25 hours, when the clocks change, is a day all the same.
  const days = Math.round((dayStart(now) - dayStart(date)) / DAY_MS);
  if (days <= 0) {
    return HOUR_MINUTE.format(date);
  }
  if (days === 1) {
    return `Yesterday, ${HOUR_MINUTE.format(date)}`;
  }
  return date.getFullYear() === now.getFullYear() ? dateTime(ts) : `${DAY_YEAR.format(date)}, ${HOUR_MINUTE.format(date)}`;
}

/** "just now", "5m ago", "3h ago", "yesterday", then the date. */
export function ago(ts) {
  const seconds = Date.now() / 1000 - ts;
  if (seconds < 45) {
    return "just now";
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m ago`;
  }
  if (seconds < 86400) {
    return `${Math.round(seconds / 3600)}h ago`;
  }
  if (seconds < 172800) {
    return "yesterday";
  }
  return dateTime(ts);
}

/** The time since `ts` in the fewest characters, for the right edge of a row (item 121): "now", "5m", "3h", "2d", "3w",
 * then the day ("5 Oct"). */
export function age(ts) {
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) {
    return "now";
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m`;
  }
  if (seconds < 86400) {
    return `${Math.floor(seconds / 3600)}h`;
  }
  if (seconds < WEEK_S) {
    return `${Math.floor(seconds / 86400)}d`;
  }
  if (seconds < 8 * WEEK_S) {
    return `${Math.floor(seconds / WEEK_S)}w`;
  }
  return DAY.format(new Date(ts * 1000));
}

/** How long something has worked since `ts`, in the sidebar's units (item 122): "12s", "4m", "1h 5m". */
export function workingFor(ts) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** "850 ms", "12.4 s", "3m 05s", "2h 10m" */
/** The milliseconds of a motion token of the stylesheet ("--motion-base"), for a script that waits for a slide to end. */
export function motionMs(token) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  if (raw.endsWith("ms")) {
    return Number.parseFloat(raw);
  }
  if (raw.endsWith("s")) {
    return Number.parseFloat(raw) * 1000;
  }
  throw new Error(`the motion token ${token} is not a duration: "${raw}"`);
}

export function duration(seconds) {
  if (seconds < 1) {
    return `${Math.round(seconds * 1000)} ms`;
  }
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
  }
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** "950", "12.3k", "1.4M" */
export function compact(count) {
  if (count < 1000) {
    return String(count);
  }
  if (count < 1_000_000) {
    return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
  }
  return `${(count / 1_000_000).toFixed(1)}M`;
}

/** The parts that are there, each after a dot: one line of words that wraps, not a row of chips. */
export function joined(parts) {
  const there = parts.filter((part) => part !== null && part !== "");
  return there.flatMap((part, index) => (index === 0 ? [part] : [" · ", part]));
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

/** The model part of a `provider:model` reference: `ollama:gpt-oss:20b-cloud` reads `gpt-oss:20b-cloud`. */
export function modelName(ref) {
  return ref.slice(ref.indexOf(":") + 1);
}

export async function copyText(text) {
  await navigator.clipboard.writeText(text);
}
