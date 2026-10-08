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

export function sleep(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
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

/** "850 ms", "12.4 s", "3m 05s", "2h 10m" */
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
