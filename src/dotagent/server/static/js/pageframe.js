/**
 * The pages a run shows in the conversation (`show_page`, item 193). Each sits in a frame sandboxed to its scripts
 * alone, so its origin is opaque: it reads nothing of the UI's and reaches no API. The frame speaks three messages of
 * the MCP Apps protocol with the page's bootstrap (dotagent/server/routes_pages.py): it sends the conversation's theme
 * as CSS variables, at first and on every change; it takes the page's height, between MIN_HEIGHT and MAX_HEIGHT; and it
 * opens in the user's browser a link they clicked in the page. A page that loads another page in its frame is
 * closed, and can be shown again.
 *
 * Since item 651 a page is part of the answer: no title over it and no box round it, on the
 * conversation's own ground, with Full screen (in a dialog of the UI) and Open in the browser over its corner. The
 * height a page took is kept in this browser by its width, so a page shown again stands at its height at once and
 * the conversation does not jump under the reader.
 */
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { shownScheme } from "./theme.js";

const MIN_HEIGHT = 80;
const MAX_HEIGHT = 2000;
// The height a frame took is kept once the page has stood still this long, not at every step of its growth.
const SETTLED_MS = 600;
// The heights pages took, by page and by width (a step of WIDTH_STEP pixels), for the KEPT_PAGES shown last.
const HEIGHTS_KEY = "dot.pageHeights";
const WIDTH_STEP = 40;
const KEPT_PAGES = 100;
const CONTEXT_CHANGED = "ui/notifications/host-context-changed";
const SIZE_CHANGED = "ui/notifications/size-changed";
const OPEN_LINK = "ui/open-link";
// The colours of a page (THEME_VARIABLES of dotagent/agent/pages.py), each with the token of the UI it is taken from.
const COLOURS = [
  ["--background", "--bg"],
  ["--foreground", "--text"],
  ["--muted", "--surface-2"],
  ["--muted-foreground", "--muted"],
  ["--card", "--surface"],
  ["--border", "--border-strong"],
  ["--accent", "--accent"],
  ["--accent-foreground", "--on-accent"],
  ["--destructive", "--bad"],
  ["--warning", "--warn"],
  ["--success", "--ok"],
  ["--chart-1", "--accent"],
  ["--chart-2", "--character-blue"],
  ["--chart-3", "--character-green"],
  ["--chart-4", "--character-amber"],
  ["--chart-5", "--character-violet"],
  ["--chart-6", "--character-pink"],
];

let watching = false;

/** @returns {Record<string, Record<string, number>>} */
function keptHeights() {
  try {
    const kept = JSON.parse(localStorage.getItem(HEIGHTS_KEY) ?? "{}");
    return kept !== null && typeof kept === "object" ? kept : {};
  } catch {
    return {};
  }
}

const widthStep = (width) => String(Math.round(width / WIDTH_STEP) * WIDTH_STEP);

/** The height the page `pageId` took at about `width` in this browser, or null. */
function heightAt(pageId, width) {
  const height = keptHeights()[pageId]?.[widthStep(width)];
  return typeof height === "number" && Number.isFinite(height) ? height : null;
}

/** Keep the height the page `pageId` took at about `width`, for the next time it is shown. */
function keepHeight(pageId, width, height) {
  const kept = keptHeights();
  const step = widthStep(width);
  if (kept[pageId]?.[step] === height) {
    return;
  }
  const own = { ...(kept[pageId] ?? {}), [step]: height };
  delete kept[pageId];
  kept[pageId] = own;
  const pages = Object.keys(kept);
  for (const old of pages.slice(0, Math.max(0, pages.length - KEPT_PAGES))) {
    delete kept[old];
  }
  try {
    localStorage.setItem(HEIGHTS_KEY, JSON.stringify(kept));
  } catch {
    // A browser that keeps nothing (private, or full) shows the page all the same.
  }
}

/**
 * The theme on screen as a page takes it, `{ theme, styles: { variables } }`: every colour worked out for the scheme on
 * screen (a token of the UI can name a light and a dark value), the corners and the two families of type.
 */
function context() {
  const probe = el("span", { hidden: true });
  document.body.append(probe);
  const variables = {};
  for (const [name, token] of COLOURS) {
    probe.style.color = `var(${token})`;
    variables[name] = getComputedStyle(probe).color;
  }
  probe.style.fontFamily = "var(--mono)";
  variables["--font-mono"] = getComputedStyle(probe).fontFamily;
  probe.remove();
  variables["--radius"] = getComputedStyle(document.documentElement).getPropertyValue("--radius").trim();
  variables["--font-sans"] = getComputedStyle(document.body).fontFamily;
  return { theme: shownScheme(), styles: { variables } };
}

function sendTheme(frame, theme) {
  frame.contentWindow?.postMessage({ jsonrpc: "2.0", method: CONTEXT_CHANGED, params: theme }, "*");
}

/** @returns {HTMLIFrameElement[]} */
function frames() {
  return [.../** @type {NodeListOf<HTMLIFrameElement>} */ (document.querySelectorAll("iframe.shown-page-frame"))];
}

/** Open `url`, a link the user clicked in the page of `frame`, in their browser; what kept it shut, or null. */
function openLink(frame, url) {
  let address;
  try {
    address = new URL(url);
  } catch {
    return "the link is not an address";
  }
  if (address.protocol !== "http:" && address.protocol !== "https:") {
    return "only web addresses are opened";
  }
  // A click in the frame counts here too, and leaves the frame with the focus: a page's script cannot open a link
  // without a click of the user's, nor with one the user made elsewhere on the page.
  if (navigator.userActivation?.isActive !== true || document.activeElement !== frame) {
    return "no click of the user in the page asked for it";
  }
  window.open(address.href, "_blank", "noopener,noreferrer");
  return null;
}

function onMessage(event) {
  const frame = frames().find((candidate) => candidate.contentWindow === event.source);
  const message = event.data;
  if (frame === undefined || message === null || typeof message !== "object" || message.jsonrpc !== "2.0") {
    return;
  }
  if (message.method === SIZE_CHANGED) {
    const height = message.params?.height;
    // The frame of a page full screen fills its dialog: the page scrolls in it.
    if (typeof height === "number" && Number.isFinite(height) && frame.dataset.full !== "true") {
      const fitted = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(height)));
      frame.style.height = `${fitted}px`;
      const page = frame.dataset.page;
      if (page !== undefined) {
        clearTimeout(Number(frame.dataset.keeping ?? 0));
        frame.dataset.keeping = String(setTimeout(() => keepHeight(page, frame.clientWidth, fitted), SETTLED_MS));
      }
    }
    return;
  }
  if (message.method === OPEN_LINK && (typeof message.id === "string" || typeof message.id === "number")) {
    const refused = openLink(frame, message.params?.url);
    const answer = refused === null ? { result: {} } : { error: { code: -32000, message: refused } };
    event.source.postMessage({ jsonrpc: "2.0", id: message.id, ...answer }, "*");
  }
}

/** Every page on screen takes the theme again when it changes: a look or a scheme picked, or the system's scheme. */
function watch() {
  watching = true;
  window.addEventListener("message", onMessage);
  let queued = false;
  const resend = () => {
    if (queued) {
      return;
    }
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const shown = frames();
      if (shown.length > 0) {
        const theme = context();
        for (const frame of shown) {
          sendTheme(frame, theme);
        }
      }
    });
  };
  new MutationObserver(resend).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-scheme", "style"] });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", resend);
}

/** The address of the page at `address` in the theme on screen. */
function themed(address) {
  return `${address}#dot-theme=${encodeURIComponent(JSON.stringify(context()))}`;
}

/**
 * The frame of the page `pageId` at `address`, sent the theme on screen when it loads. A page that loads another in its
 * place (a script or a refresh of its own) shows what the run did not write, so `onLeft(frame)` takes its frame away. A
 * frame moved in the document loads again too, so the card never moves it. `full` is the frame of the full-screen
 * dialog, which fills it.
 */
function pageFrame(pageId, address, title, onLeft, full = false) {
  const frame = el("iframe", { class: "shown-page-frame", src: themed(address), sandbox: "allow-scripts", title, loading: full ? "eager" : "lazy", referrerpolicy: "no-referrer" });
  if (full) {
    frame.dataset.full = "true";
  } else {
    frame.dataset.page = pageId;
  }
  let loads = 0;
  frame.addEventListener("load", () => {
    loads += 1;
    if (loads === 1) {
      // A frame that loads after a change of the theme is sent the theme on screen.
      sendTheme(frame, context());
      return;
    }
    onLeft(frame);
  });
  return frame;
}

/** The page full screen, in a dialog of the UI (item 651): Escape, its Close or a click beside it closes it. */
function showFull(pageId, address, title, opener) {
  const close = el("button", { class: "icon-btn", type: "button", "aria-label": "Close" }, icon("x"));
  const dialog = el("dialog", { class: "modal page-dialog", "aria-label": title });
  const frame = pageFrame(pageId, address, title, () => dialog.close(), true);
  dialog.append(el("div", { class: "page-dialog-head" }, el("span", { class: "page-dialog-title" }, title), close), frame);
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => {
    dialog.remove();
    opener.focus();
  });
  document.body.append(dialog);
  dialog.showModal();
}

/**
 * The page `pageId` that a run showed, as part of the answer (item 651): the page in its frame, as tall as it was
 * the last time this browser showed it at this width, and over its corner Full screen and Open in the browser.
 */
export function pageCard(pageId, title) {
  if (!watching) {
    watch();
  }
  const address = `/pages/${encodeURIComponent(pageId)}`;
  const show = () => {
    const frame = pageFrame(pageId, address, title, (shown) => {
      const again = el("button", { class: "btn sm ghost", type: "button" }, "Show it again");
      const left = el("div", { class: "shown-page-left" }, el("span", null, "The page loaded another page in its place, so Dot closed it."), again);
      again.addEventListener("click", () => left.replaceWith(show()));
      shown.replaceWith(left);
    });
    // The width the card will have is the conversation's; its height is the one the page took there before.
    requestAnimationFrame(() => {
      const known = heightAt(pageId, card.clientWidth);
      if (known !== null && frame.isConnected && frame.style.height === "") {
        frame.style.height = `${known}px`;
      }
    });
    return frame;
  };
  const full = el("button", { class: "shown-page-tool", type: "button", "aria-label": "Full screen", title: "Full screen" }, icon("maximize"));
  full.addEventListener("click", () => showFull(pageId, address, title, full));
  const open = el(
    "a",
    { class: "shown-page-tool shown-page-open", href: address, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open in your browser", title: "Open in your browser" },
    icon("external"),
  );
  open.addEventListener("click", () => {
    open.href = themed(address);
  });
  const card = el("figure", { class: "shown-page", "aria-label": title }, show(), el("div", { class: "shown-page-tools" }, full, open));
  return card;
}
