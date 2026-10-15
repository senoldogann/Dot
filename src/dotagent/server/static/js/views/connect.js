// Connect apps (item 278): the applications Dot is set up with, opened from the sidebar's foot. Each says what Dot does
// with it, where it stands and the one step that sets it up: macOS's question for an application of the Mac, and after
// a no the Automation pane of System Settings with what to switch on there; OneDrive itself, to sign in; the sign-in
// page in Dot's browser for an application of the web.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, pageHead } from "../panels.js";
import { attempt } from "../ui.js";

const STATE_WORDS = {
  allowed: "Allowed",
  not_asked: "Not asked yet",
  not_open: "Not checked",
  refused: "Refused",
  not_installed: "Not installed",
  signed_in: "Signed in",
  signed_out: "Not signed in",
  lent: "Your Chrome",
  synced: "Syncing",
  not_synced: "Not syncing",
};
const STATE_TONES = { allowed: "b-ok", signed_in: "b-ok", synced: "b-ok", lent: "b-ok", refused: "b-bad" };
const STEP_LABELS = { ask: "Allow", settings: "Open System Settings", sign_in: "Sign in", open_app: "Open" };
const SECTIONS = [
  {
    kind: "mac",
    title: "On this Mac",
    mark: "monitor",
    hint: "Dot drives these with AppleScript once macOS lets it. Allow asks macOS, which shows its question on the Mac, and opens the application hidden first when it is not open.",
  },
  { kind: "folder", title: "Your files", mark: "cloud", hint: "Dot reads and writes the files these keep in sync on this Mac." },
  { kind: "web", title: "On the web", mark: "globe", hint: "Dot works in these in its own browser, once you have signed in there." },
];

/** How to sign in to the applications of the web, the same for each of them: it depends on Dot's browser alone. */
function webNote(listing) {
  if (listing.browser_mode === "attach") {
    return "Dot works in your own Chrome (Settings › Browser › My Chrome), where you are signed in as you always are.";
  }
  return listing.headless
    ? "Dot's browser runs without a window, so there is none to sign in in: switch headless off in the config."
    : "Sign in opens the page in Dot's browser on the Mac; check again once you are in.";
}

/** What to do or to know for one application, by where it stands: one line, or none. */
function guide(app) {
  if (app.waiting) {
    return "macOS is asking on the Mac: answer its question there, then check again.";
  }
  switch (app.state) {
    case "refused":
      return `macOS was told no. In Privacy & Security › Automation, open the app that runs Dot (Terminal, iTerm, Claude, or the Dot service) and switch on ${app.name}.`;
    case "not_installed":
      return `Install ${app.name} to use it with Dot.`;
    case "not_synced":
      return `${app.name} syncs no folder yet: open it and sign in.`;
    default:
      return null;
  }
}

export function mount(root, ctx) {
  let disposed = false;
  let listing = null;
  const holder = el("div", { class: "stack" }, loadingPanel());
  const recheck = el("button", { class: "btn", type: "button", onclick: () => attempt(load) }, icon("refresh"), "Check again");

  function row(app) {
    const line = guide(app);
    const button =
      app.step === null
        ? null
        : el(
            "button",
            { class: app.step === "settings" ? "btn sm" : "btn sm primary", type: "button", onclick: () => attempt(() => takeStep(app, button)) },
            STEP_LABELS[app.step],
          );
    return el(
      "div",
      { class: "list-row connect-row", dataset: { app: app.id, state: app.state } },
      el(
        "div",
        { class: "row-main" },
        el("div", { class: "row" }, el("span", { class: "row-title" }, app.name), el("span", { class: `badge ${STATE_TONES[app.state] ?? "b-muted"}` }, STATE_WORDS[app.state] ?? app.state)),
        el("div", { class: "row-text muted" }, app.does),
        line === null ? null : el("div", { class: "row-text connect-guide" }, line),
      ),
      button === null ? null : el("div", { class: "row-actions" }, button),
    );
  }

  async function takeStep(app, button) {
    button.disabled = true;
    if (app.step === "ask") {
      button.textContent = "Waiting for macOS…";
    }
    let changed;
    try {
      changed = await api.post(`/connections/${encodeURIComponent(app.id)}`, {});
    } finally {
      button.disabled = false;
      button.textContent = STEP_LABELS[app.step];
    }
    if (disposed) {
      return;
    }
    listing = { ...listing, apps: listing.apps.map((one) => (one.id === changed.id ? changed : one)) };
    paint();
  }

  function paint() {
    holder.replaceChildren(
      ...SECTIONS.map(({ kind, title, mark, hint }) =>
        el(
          "section",
          { class: "card", dataset: { kind } },
          el("div", { class: "card-head" }, icon(mark), el("span", { class: "grow" }, title)),
          el("p", { class: "hint connect-hint" }, hint),
          kind === "web" ? el("p", { class: "hint connect-hint connect-note" }, webNote(listing)) : null,
          listing.apps.filter((app) => app.kind === kind).map(row),
        ),
      ),
    );
  }

  async function load() {
    let found;
    try {
      found = await api.get("/connections");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      listing = found;
      paint();
    }
  }

  root.append(pageHead("Connect apps", "The applications Dot works with, and the one step that lets it use each.", recheck), holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}
