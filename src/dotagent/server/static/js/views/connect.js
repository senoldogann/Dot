// Connect apps (item 278): the applications Dot is set up with, a page of the sidebar under Campaigns (item 289). Each
// says what Dot does with it, where it stands and the one step that sets it up: macOS's question for an application of
// the Mac, and after a no the Automation pane of System Settings with what to switch on there; OneDrive itself, to sign
// in; the sign-in page in Dot's browser for an application of the web. Below them (item 279), the MCP servers Dot has,
// with their tools, and the services and addresses to add one from: Dot tries a server first, and one that asks for a
// sign-in gets it in this browser, on the service's own page, which comes back to this page's address.

import { api } from "../api.js";
import { el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, field, loadingPanel, pageHead } from "../panels.js";
import { attempt, confirmDialog } from "../ui.js";

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
const MCP_STATE_WORDS = { ready: "Ready", left_out: "Left out", disabled: "Off", trying: "Trying…", signing_in: "Waiting for your sign-in" };
const MCP_STATE_TONES = { ready: "b-ok", left_out: "b-bad", trying: "b-info", signing_in: "b-warn" };
const MCP_ORIGIN_WORDS = {
  config: "Set up in Dot's config.",
  plugin: "Comes with one of Dot's plugins.",
  elsewhere: "Added from another agent of this Mac (Skills › Found elsewhere).",
};
// While Dot tries a server or waits for a sign-in, the page asks how it stands this often.
const MCP_POLL_MS = 2000;
const SHOWN_TOOLS = 6;

/** How to sign in to the applications of the web, the same for each of them: it depends on Dot's browser alone. */
function webNote(listing) {
  if (listing.browser_mode === "attach") {
    return "Dot works in your own Chrome (Settings › Browser › My Chrome), where you are signed in as you always are.";
  }
  return listing.headless
    ? "Dot's browser runs without a window, so there is none to sign in in: switch headless off in the config."
    : "Sign in opens the page in Dot's browser on the Mac; check again once you are in.";
}

/** What to do or to know for one application, by where it stands: one line, or none. `runner` is the name macOS asks
 * for Dot under and lists in Privacy & Security (Dot for the service, item 288). */
function guide(app, runner) {
  if (app.waiting) {
    return "macOS is asking on the Mac: answer its question there, then check again.";
  }
  switch (app.state) {
    case "refused":
      return `macOS was told no. In Privacy & Security › Automation, open ${runner} and switch on ${app.name}.`;
    case "not_installed":
      return `Install ${app.name} to use it with Dot.`;
    case "not_synced":
      return `${app.name} syncs no folder yet: open it and sign in.`;
    default:
      return null;
  }
}

/** What to do or to know for one MCP server, by where it stands: one line, or none. */
function mcpGuide(server) {
  switch (server.state) {
    case "signing_in":
      return "Sign in on the service's page; Dot connects once it sends you back here.";
    case "trying":
      return "Dot is connecting to it.";
    case "left_out":
      return server.error;
    case "disabled":
      return "Switched off in Dot's config.";
    default:
      return null;
  }
}

/** The tools a server offers to runs, the first few by name. */
function toolLine(tools) {
  const shown = tools.slice(0, SHOWN_TOOLS).join(", ");
  return `${plural(tools.length, "tool", "tools")}: ${shown}${tools.length > SHOWN_TOOLS ? ", …" : ""}`;
}

export function mount(root, ctx) {
  let disposed = false;
  let listing = null;
  let servers = null;
  let polling = null;
  const holder = el("div", { class: "stack" }, loadingPanel());
  const recheck = el("button", { class: "btn", type: "button", onclick: () => attempt(load) }, icon("refresh"), "Check again");

  function row(app) {
    const line = guide(app, listing.runner);
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

  function serverRow(server) {
    const service = servers.services.find((one) => one.id === server.service);
    const busy = server.state === "trying" || server.state === "signing_in";
    const line = mcpGuide(server);
    const actions = [];
    if (server.state === "signing_in" && server.link !== null) {
      actions.push(
        el("a", { class: "btn sm primary", href: server.link, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "Open the sign-in page"),
      );
    }
    const again = server.origin === "connected" || (server.origin === "config" && server.signs_in);
    if (again && !busy && (server.signs_in || server.state === "left_out")) {
      const label = server.signs_in ? "Sign in again" : "Try again";
      const button = el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => connectAgain(server, button)) }, label);
      actions.push(button);
    }
    if (server.origin === "connected") {
      actions.push(el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => remove(server, service)) }, "Remove"));
    }
    return el(
      "div",
      { class: "list-row connect-row", dataset: { server: server.name, state: server.state } },
      el(
        "div",
        { class: "row-main" },
        el(
          "div",
          { class: "row" },
          el("span", { class: "row-title" }, service ? service.name : server.name),
          el("span", { class: `badge ${MCP_STATE_TONES[server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[server.state] ?? server.state),
        ),
        el("div", { class: "row-text muted mono connect-address" }, server.address),
        server.tools.length === 0 ? null : el("div", { class: "row-text muted" }, toolLine(server.tools)),
        server.origin in MCP_ORIGIN_WORDS ? el("div", { class: "row-text muted" }, MCP_ORIGIN_WORDS[server.origin]) : null,
        line === null ? null : el("div", { class: "row-text connect-guide" }, line),
      ),
      actions.length === 0 ? null : el("div", { class: "row-actions" }, ...actions),
    );
  }

  function serviceRow(service) {
    const button = el(
      "button",
      { class: "btn sm primary", type: "button", onclick: () => attempt(() => add({ service: service.id, name: null, address: null }, button)) },
      "Add",
    );
    return el(
      "div",
      { class: "list-row connect-row", dataset: { service: service.id } },
      el(
        "div",
        { class: "row-main" },
        el("div", { class: "row" }, el("span", { class: "row-title" }, service.name), el("span", { class: "badge b-muted" }, service.signs_in ? "Signs in" : "Open to all")),
        el("div", { class: "row-text muted" }, service.does),
      ),
      el("div", { class: "row-actions" }, button),
    );
  }

  function addForm() {
    const name = el("input", { class: "input", type: "text", maxlength: "24", autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: "my-server", "aria-label": "Server name" });
    const address = el("input", {
      class: "input mono",
      type: "text",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      placeholder: "https://example.com/mcp",
      "aria-label": "Server address or command",
    });
    const button = el("button", { class: "btn primary", type: "button" }, icon("plus"), "Add");
    button.addEventListener("click", () =>
      attempt(async () => {
        await add({ service: null, name: name.value.trim(), address: address.value.trim() }, button);
        name.value = "";
        address.value = "";
      }),
    );
    return el(
      "div",
      { class: "connect-add" },
      field("Name", name, "Lowercase letters, digits and dashes."),
      field("Address", address, "Its URL, or the command that starts it on this Mac."),
      el("div", { class: "connect-add-actions" }, button),
    );
  }

  function mcpSections() {
    const toAdd = servers.services.filter((service) => !service.added);
    return [
      el(
        "section",
        { class: "card", dataset: { kind: "mcp" } },
        el("div", { class: "card-head" }, icon("plug"), el("span", { class: "grow" }, "Tools of other services")),
        el(
          "p",
          { class: "hint connect-hint" },
          "MCP servers hand their tools to Dot. Dot tries one before it keeps it, signs in to it in this browser when it asks, and offers its tools to the runs that start after.",
        ),
        servers.servers.length === 0 ? el("p", { class: "hint connect-hint connect-note" }, "Dot has no MCP server yet.") : null,
        servers.servers.map(serverRow),
        form,
      ),
      toAdd.length === 0
        ? null
        : el(
            "section",
            { class: "card", dataset: { kind: "services" } },
            el("div", { class: "card-head" }, icon("plus"), el("span", { class: "grow" }, "Services to add")),
            el("p", { class: "hint connect-hint" }, "Each runs its own MCP server. One that signs in opens its sign-in page here; you sign in with your own account."),
            toAdd.map(serviceRow),
          ),
    ];
  }

  async function add(body, button) {
    button.disabled = true;
    try {
      await api.post("/mcp/servers", { ...body, origin: location.origin });
    } finally {
      button.disabled = false;
    }
    await refreshServers();
  }

  async function connectAgain(server, button) {
    button.disabled = true;
    try {
      await api.post(`/mcp/servers/${encodeURIComponent(server.name)}/connect`, { origin: location.origin });
    } finally {
      button.disabled = false;
    }
    await refreshServers();
  }

  async function remove(server, service) {
    const sure = await confirmDialog({
      title: `Remove ${service ? service.name : server.name} from Dot?`,
      message: "Its tools leave the runs that start after, and Dot forgets its address and its sign-in.",
      confirmLabel: "Remove",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    const changed = await api.del(`/mcp/servers/${encodeURIComponent(server.name)}`);
    if (!disposed) {
      servers = changed;
      paint();
    }
  }

  /** Ask again how the MCP servers stand, and keep asking while Dot tries one or waits for a sign-in. */
  async function refreshServers() {
    const found = await api.get("/mcp/servers");
    if (disposed) {
      return;
    }
    servers = found;
    paint();
  }

  function keepPolling() {
    clearTimeout(polling);
    polling = null;
    if (servers.servers.some((server) => server.state === "trying" || server.state === "signing_in")) {
      polling = setTimeout(() => attempt(refreshServers), MCP_POLL_MS);
    }
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
      ...mcpSections(),
    );
    keepPolling();
  }

  async function load() {
    let found;
    try {
      found = await Promise.all([api.get("/connections"), api.get("/mcp/servers")]);
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      [listing, servers] = found;
      paint();
    }
  }

  // Made once, so that what is typed in it stays while the page paints again.
  const form = addForm();
  root.append(pageHead("Connect apps", "The applications Dot works with, and the one step that lets it use each.", recheck), holder);
  attempt(load);
  return () => {
    disposed = true;
    clearTimeout(polling);
  };
}
