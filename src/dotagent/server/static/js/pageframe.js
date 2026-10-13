/**
 * The pages a run shows in the conversation (`show_page`, item 193). Each sits in a frame sandboxed to its scripts
 * alone, so its origin is opaque: it reads nothing of the UI's and reaches no API. The frame speaks three messages of
 * the MCP Apps protocol with the page's bootstrap (dotagent/server/routes_pages.py): it sends the conversation's theme
 * as CSS variables, at first and on every change; it takes the page's height, between MIN_HEIGHT and MAX_HEIGHT; and it
 * opens in the user's browser a link they clicked in the page. A page that loads another page in its frame is
 * closed, and can be shown again.
 */
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { shownScheme } from "./theme.js";

const MIN_HEIGHT = 80;
const MAX_HEIGHT = 2000;
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

function frames() {
  return [...document.querySelectorAll("iframe.shown-page-frame")];
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
    if (typeof height === "number" && Number.isFinite(height)) {
      frame.style.height = `${Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(height)))}px`;
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
        shown.forEach((frame) => sendTheme(frame, theme));
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
 * The frame of the page at `address`, sent the theme on screen when it loads. A page that loads another in its place (a
 * script or a refresh of its own) shows what the run did not write, so `onLeft(frame)` takes its frame away. A frame
 * moved in the document loads again too, so the card never moves it.
 */
function pageFrame(address, title, onLeft) {
  const frame = el("iframe", { class: "shown-page-frame", src: themed(address), sandbox: "allow-scripts", title, loading: "lazy", referrerpolicy: "no-referrer" });
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

/** The card of the page `pageId` that a run showed: its title, a way to open it full size, and the page in its frame. */
export function pageCard(pageId, title) {
  if (!watching) {
    watch();
  }
  const address = `/pages/${encodeURIComponent(pageId)}`;
  const show = () =>
    pageFrame(address, title, (frame) => {
      const again = el("button", { class: "btn sm ghost", type: "button" }, "Show it again");
      const left = el("div", { class: "shown-page-left" }, el("span", null, "The page loaded another page in its place, so Dot closed it."), again);
      again.addEventListener("click", () => left.replaceWith(show()));
      frame.replaceWith(left);
    });
  const open = el(
    "a",
    { class: "shown-page-open", href: address, target: "_blank", rel: "noopener noreferrer", title: "Open the page full size in your browser" },
    icon("external"),
    el("span", null, "Open"),
  );
  open.addEventListener("click", () => {
    open.href = themed(address);
  });
  return el("figure", { class: "shown-page" }, el("figcaption", { class: "shown-page-head" }, icon("image"), el("span", { class: "shown-page-title" }, title), open), show());
}
