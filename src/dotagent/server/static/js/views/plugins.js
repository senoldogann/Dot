// Customize › Plugins (item 316): every service Dot works with, after Codex's page. A search, Connected and All, and the
// services in sections (Popular, Communication, Development, Business, Productivity, This Mac): each a tile with its
// mark, what Dot does with it and how it connects, and a "+" that takes its first step, or where it stands once it is
// connected. A tile opens the service's own page: what Dot does with it, each way it connects with where it stands and
// its step, its tools, where its data goes. The steps are Connect apps' (items 278 and 279): macOS's question for an
// application of the Mac, and after a no the Automation pane of System Settings; OneDrive itself, to sign in; the
// sign-in page in Dot's browser for an application of the web; an MCP server that Dot tries first, and signs in to in
// this browser on the service's own page when it asks. A service none of whose ways was tried says "Not tried yet" and
// offers no step. Under the sections, an MCP server of one's own by its address; Connected lists too the servers that
// come from Dot's config, a plugin or another agent of the Mac.

import { api } from "../api.js";
import { el, foldSearch, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, segmented } from "../panels.js";
import { attempt, confirmDialog } from "../ui.js";

const SECTIONS = [
  { id: "popular", title: "Popular" },
  { id: "communication", title: "Communication" },
  { id: "development", title: "Development" },
  { id: "business", title: "Business" },
  { id: "productivity", title: "Productivity" },
  { id: "mac", title: "This Mac" },
];
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
const CONNECTED_STATES = new Set(["allowed", "signed_in", "synced", "lent"]);
const STEP_LABELS = { ask: "Allow", settings: "Open System Settings", sign_in: "Sign in", open_app: "Open" };
const MCP_STATE_WORDS = { ready: "Ready", left_out: "Left out", disabled: "Off", trying: "Trying…", signing_in: "Waiting for your sign-in" };
const MCP_STATE_TONES = { ready: "b-ok", left_out: "b-bad", trying: "b-info", signing_in: "b-warn" };
const MCP_ORIGIN_WORDS = {
  config: "Set up in Dot's config.",
  plugin: "Comes with one of Dot's plugins.",
  elsewhere: "Added from another agent of this Mac (Customize › Skills › Found on this Mac).",
};
// What each way is called on a service's page, and on its tile when it is the service's first.
const WAY_NAMES = { mcp: "MCP server", mac: "AppleScript", web: "Dot's browser", folder: "Synced folder", untried: "Not tried yet" };
// While Dot tries a server or waits for a sign-in, the page asks how it stands this often.
const MCP_POLL_MS = 2000;
// A server's tile names this many of its tools.
const SHOWN_TOOLS = 6;
// A colour darker or lighter than these (its relative luminance) does not read on both grounds: its mark takes the ink.
const DARKEST_MARK = 0.06;
const LIGHTEST_MARK = 0.55;

/** The relative luminance of the colour `hex` (six digits, no "#"), as WCAG reckons it. */
function luminance(hex) {
  const [r, g, b] = [0, 2, 4].map((at) => {
    const value = parseInt(hex.slice(at, at + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** A service's mark: its logo, the interface's icon or its letters, on a tile tinted with its colour. The logo and the
 * letters take the colour where it reads on both grounds, else the ink. */
export function pluginMark(plugin) {
  const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" });
  if (plugin.colour !== null) {
    const shade = luminance(plugin.colour);
    mark.style.setProperty("--plugin-tint", `#${plugin.colour}`);
    if (shade >= DARKEST_MARK && shade <= LIGHTEST_MARK) {
      mark.style.setProperty("--plugin-ink", `#${plugin.colour}`);
    }
  }
  if (plugin.logo !== null) {
    const logo = el("span", { class: "plugin-logo" });
    logo.style.setProperty("--plugin-logo", `url("/icons/plugins/${plugin.logo}")`);
    mark.append(logo);
  } else if (plugin.glyph !== null) {
    mark.append(icon(plugin.glyph));
  } else {
    mark.append(el("span", { class: "plugin-letters" }, plugin.monogram));
  }
  return mark;
}

/** The tools a server offers to runs, the first few by name. */
function toolLine(tools) {
  const shown = tools.slice(0, SHOWN_TOOLS).join(", ");
  return `${plural(tools.length, "tool", "tools")}: ${shown}${tools.length > SHOWN_TOOLS ? ", …" : ""}`;
}

/** How to sign in to the applications of the web, the same for each of them: it depends on Dot's browser alone. */
function webNote(listing) {
  if (listing.browser_mode === "attach") {
    return "Dot works in your own Chrome (Settings › Browser › My Chrome), where you are signed in as you always are.";
  }
  return listing.headless
    ? "Dot's browser runs without a window, so there is none to sign in in: switch headless off in the config."
    : "Sign in opens the page in Dot's browser on the Mac; check again once you are in.";
}

/** What to do or to know for an application, by where it stands: one line, or none. `runner` is the name macOS asks
 * for Dot under and lists in Privacy & Security (Dot for the service, item 288). */
function appGuide(app, runner) {
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
function serverGuide(server) {
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

export function mount(root, ctx) {
  const pluginId = ctx.params[1];
  let disposed = false;
  let plugins = null;
  let listing = null;
  let servers = null;
  let polling = null;
  let shown = "all";
  let words = "";
  // The steps under way, by what they act on (`app:<id>`, `mcp:<service or name>`, `server:<name>`): their buttons stay
  // off while the page paints again, and an application's says that macOS asks.
  const busy = new Set();
  const holder = el("div", { class: "stack" }, loadingPanel());

  /** Where `way` stands now: its application of `GET /connections`, or its server and service of `GET /mcp/servers`. */
  function wayNow(way) {
    if (way.kind === "mcp") {
      return {
        server: servers.servers.find((one) => one.service === way.ref) ?? null,
        service: servers.services.find((one) => one.id === way.ref) ?? null,
      };
    }
    if (way.kind === "untried") {
      return {};
    }
    return { app: listing.apps.find((one) => one.id === way.ref) ?? null };
  }

  function wayConnected(way) {
    const now = wayNow(way);
    if (way.kind === "mcp") {
      return now.server !== null && now.server.state === "ready";
    }
    return now.app !== undefined && now.app !== null && CONNECTED_STATES.has(now.app.state);
  }

  function connected(plugin) {
    return plugin.ways.some(wayConnected);
  }

  /** The first step `plugin` offers, by its ways in order: `{ label, take }` for a button, `{ label, link }` for the
   * sign-in page an MCP server waits on, or null when it has none to take, as while Dot tries its server. A service
   * whose name a server of the config or of a plugin holds already is not added again. */
  function firstStep(plugin) {
    for (const way of plugin.ways) {
      const now = wayNow(way);
      if (way.kind === "mcp") {
        if (now.server === null) {
          if (now.service?.added) {
            continue;
          }
          const service = now.service;
          if (service !== undefined && service !== null && service.secrets.length > 0) {
            // A service that takes a key asks for it first (item 408): the form opens under the tile.
            return { label: "Add", key: `mcp:${way.ref}`, take: () => askSecrets(service) };
          }
          return { label: "Add", key: `mcp:${way.ref}`, take: () => add({ service: way.ref, name: null, address: null, secrets: {} }) };
        }
        if (now.server.state === "signing_in" && now.server.link !== null) {
          return { label: "Sign in", link: now.server.link };
        }
        if (now.server.state === "trying" || now.server.state === "signing_in") {
          return null;
        }
        if (now.server.state === "left_out" && now.server.origin === "connected") {
          const server = now.server;
          return { label: server.signs_in ? "Sign in again" : "Try again", key: `server:${server.name}`, take: () => connectAgain(server) };
        }
        continue;
      }
      if (now.app !== undefined && now.app !== null && now.app.step !== null) {
        const app = now.app;
        return { label: STEP_LABELS[app.step], key: `app:${app.id}`, take: () => takeStep(app) };
      }
    }
    return null;
  }

  /** Where `plugin` stands, in a badge: a way that is connected, else the server Dot has for it, else its first way;
   * null for a service not tried yet, and for an MCP service Dot has no server of. */
  function standing(plugin) {
    const ways = plugin.ways.filter((way) => way.kind !== "untried");
    const shown = ways.find(wayConnected) ?? ways.find((way) => way.kind === "mcp" && wayNow(way).server !== null) ?? ways[0];
    if (shown === undefined) {
      return null;
    }
    const now = wayNow(shown);
    if (shown.kind === "mcp") {
      return now.server === null ? null : el("span", { class: `badge ${MCP_STATE_TONES[now.server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[now.server.state] ?? now.server.state);
    }
    if (now.app === null) {
      return null;
    }
    const tone = CONNECTED_STATES.has(now.app.state) ? "b-ok" : now.app.state === "refused" ? "b-bad" : "b-muted";
    return el("span", { class: `badge ${tone}` }, STATE_WORDS[now.app.state] ?? now.app.state);
  }

  /** How `plugin` connects, in a chip: its first tried way, an MCP server's sign-in with it; else Not tried yet. */
  function howChip(plugin) {
    const first = plugin.ways.find((way) => way.kind !== "untried");
    if (first === undefined) {
      return el("span", { class: "chip plugin-untried" }, WAY_NAMES.untried);
    }
    if (first.kind === "mcp") {
      const service = servers.services.find((one) => one.id === first.ref);
      return el("span", { class: "chip" }, mcpWords(service));
    }
    return el("span", { class: "chip" }, WAY_NAMES[first.kind]);
  }

  /** Where a service of the catalogue stands before it is added, in a badge's words (item 408). */
  function serviceWords(service) {
    if (service === undefined || service === null) {
      return "Open to all";
    }
    if (service.kind === "local") {
      return "On this Mac";
    }
    if (service.secrets.length > 0) {
      return "Takes a key";
    }
    return service.signs_in ? "Signs in" : "Open to all";
  }

  /** How a service of the catalogue connects, in the chip's words (item 408): signed in, with a key, open to all, or
   * started on this Mac. */
  function mcpWords(service) {
    if (service === undefined || service === null) {
      return "MCP";
    }
    if (service.kind === "local") {
      return service.secrets.length > 0 ? "MCP · On this Mac · Key" : "MCP · On this Mac";
    }
    if (service.secrets.length > 0) {
      return "MCP · Key";
    }
    return service.signs_in ? "MCP · Sign in" : "MCP · Open to all";
  }

  // The service whose secrets are being asked for, and the fields typed into, kept while the page paints again.
  let asking = null;
  const secretFields = new Map();

  function askSecrets(service) {
    asking = service.id;
    paint();
    const fields = secretFields.get(service.id);
    if (fields !== undefined) {
      const [first] = fields.values();
      first?.focus();
    }
  }

  /** The form under a service's tile that takes its secrets (item 408): a password field for each, where to get one,
   * Add and Cancel. The values go to the daemon once, which keeps them in its .env; the fields are emptied after. */
  function secretsForm(service) {
    let fields = secretFields.get(service.id);
    if (fields === undefined) {
      fields = new Map(
        service.secrets.map((secret) => [
          secret.name,
          el("input", { class: "input mono", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-label": `${service.name}: ${secret.label}` }),
        ]),
      );
      secretFields.set(service.id, fields);
    }
    const addButton = el("button", { class: "btn primary sm", type: "button", disabled: busy.has(`mcp:${service.id}`) }, icon("plus"), "Add");
    addButton.addEventListener("click", () =>
      attempt(async () => {
        const secrets = Object.fromEntries([...fields].map(([name, input]) => [name, input.value]));
        await add({ service: service.id, name: null, address: null, secrets });
        for (const input of fields.values()) {
          input.value = "";
        }
        asking = null;
        paint();
      }),
    );
    const cancel = el("button", { class: "btn sm", type: "button" }, "Cancel");
    cancel.addEventListener("click", () => {
      asking = null;
      paint();
    });
    return el(
      "div",
      { class: "connect-add plugin-secrets" },
      ...service.secrets.map((secret) =>
        field(secret.label, fields.get(secret.name), el("a", { href: secret.help, target: "_blank", rel: "noopener noreferrer" }, "Where to get one")),
      ),
      el("div", { class: "connect-add-actions" }, addButton, cancel),
    );
  }

  function tile(plugin) {
    const isConnected = connected(plugin);
    const step = isConnected ? null : firstStep(plugin);
    const askedService = asking === null ? null : (servers.services.find((one) => one.id === asking && plugin.ways.some((way) => way.kind === "mcp" && way.ref === one.id)) ?? null);
    let action = null;
    if (step !== null && step.link !== undefined) {
      action = el(
        "a",
        { class: "btn sm primary plugin-add", href: step.link, target: "_blank", rel: "noopener noreferrer", "aria-label": `Open the sign-in page of ${plugin.name}` },
        icon("external"),
        step.label,
      );
    } else if (step !== null) {
      action = el(
        "button",
        {
          class: "btn sm icon-only plugin-add",
          type: "button",
          title: `${step.label}: ${plugin.name}`,
          "aria-label": `${step.label} ${plugin.name}`,
          disabled: busy.has(step.key),
        },
        icon("plus"),
      );
      action.addEventListener("click", () => attempt(step.take));
    }
    const waiting = plugin.ways.some((way) => way.kind === "mac" && busy.has(`app:${way.ref}`));
    return el(
      "div",
      { class: "plugin-tile", dataset: { plugin: plugin.id, connected: String(isConnected), tried: String(plugin.tried) } },
      pluginMark(plugin),
      el(
        "div",
        { class: "plugin-main" },
        el("a", { class: "plugin-name", href: `#/customize/plugins/${plugin.id}` }, plugin.name),
        el("div", { class: "plugin-does" }, plugin.does),
        el("div", { class: "plugin-meta" }, howChip(plugin), waiting ? el("span", { class: "badge b-warn" }, "Waiting for macOS…") : standing(plugin)),
        askedService === null ? null : secretsForm(askedService),
      ),
      action,
    );
  }

  /** The mark of a server added by hand: the logo of the service it is one of, by its address or its command (item
   * 408), else the plug. */
  function serverMark(server) {
    const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" });
    if (server.logo !== null) {
      const logo = el("span", { class: "plugin-logo" });
      logo.style.setProperty("--plugin-logo", `url("/icons/plugins/${server.logo}")`);
      mark.append(logo);
    } else {
      mark.append(icon("plug"));
    }
    return mark;
  }

  /** An MCP server that is no service of the catalogue: its name, address and tools, where it stands and its actions. */
  function serverTile(server) {
    return el(
      "div",
      { class: "plugin-tile plugin-server", dataset: { server: server.name, state: server.state } },
      serverMark(server),
      el(
        "div",
        { class: "plugin-main" },
        el("span", { class: "plugin-name" }, server.name),
        el("div", { class: "mono plugin-address" }, server.address),
        server.tools.length === 0 ? null : el("div", { class: "plugin-does" }, toolLine(server.tools)),
        server.origin in MCP_ORIGIN_WORDS ? el("div", { class: "plugin-does" }, MCP_ORIGIN_WORDS[server.origin]) : null,
        el("div", { class: "plugin-meta" }, el("span", { class: "chip" }, "MCP"), el("span", { class: `badge ${MCP_STATE_TONES[server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[server.state] ?? server.state)),
        serverGuide(server) === null ? null : el("div", { class: "plugin-guide" }, serverGuide(server)),
      ),
      serverActions(server, null),
    );
  }

  /** The buttons of an MCP server: its sign-in page while Dot waits for one, Sign in again or Try again, and Remove for
   * one added here; null when it has none. */
  function serverActions(server, service) {
    const working = server.state === "trying" || server.state === "signing_in";
    const actions = [];
    if (server.state === "signing_in" && server.link !== null) {
      actions.push(el("a", { class: "btn sm primary", href: server.link, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "Open the sign-in page"));
    }
    const again = server.origin === "connected" || (server.origin === "config" && server.signs_in);
    const acting = busy.has(`server:${server.name}`);
    if (again && !working && (server.signs_in || server.state === "left_out")) {
      const button = el("button", { class: "btn sm", type: "button", disabled: acting }, server.signs_in ? "Sign in again" : "Try again");
      button.addEventListener("click", () => attempt(() => connectAgain(server)));
      actions.push(button);
    }
    if (server.origin === "connected") {
      actions.push(el("button", { class: "btn sm", type: "button", disabled: acting, onclick: () => attempt(() => remove(server, service)) }, "Remove"));
    }
    return actions.length === 0 ? null : el("div", { class: "plugin-actions" }, ...actions);
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
        button.disabled = true;
        try {
          await add({ service: null, name: name.value.trim(), address: address.value.trim(), secrets: {} });
        } finally {
          button.disabled = false;
        }
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

  /** Do `work` with `key` busy: the page paints as it starts and once it is done. */
  async function busyWith(key, work) {
    busy.add(key);
    paint();
    try {
      await work();
    } finally {
      busy.delete(key);
      paint();
    }
  }

  function takeStep(app) {
    return busyWith(`app:${app.id}`, async () => {
      const changed = await api.post(`/connections/${encodeURIComponent(app.id)}`, {});
      if (!disposed) {
        listing = { ...listing, apps: listing.apps.map((one) => (one.id === changed.id ? changed : one)) };
      }
    });
  }

  function add(body) {
    return busyWith(`mcp:${body.service ?? body.name}`, async () => {
      await api.post("/mcp/servers", { ...body, origin: location.origin });
      await refreshServers();
    });
  }

  function connectAgain(server) {
    return busyWith(`server:${server.name}`, async () => {
      await api.post(`/mcp/servers/${encodeURIComponent(server.name)}/connect`, { origin: location.origin });
      await refreshServers();
    });
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
    await busyWith(`server:${server.name}`, async () => {
      const changed = await api.del(`/mcp/servers/${encodeURIComponent(server.name)}`);
      if (!disposed) {
        servers = changed;
      }
    });
  }

  /** Ask again how the MCP servers stand, and keep asking while Dot tries one or waits for a sign-in. The page paints
   * again only when they changed, so that a field being typed in, a tap and a step under way survive the asking. */
  async function refreshServers() {
    const found = await api.get("/mcp/servers");
    if (disposed) {
      return;
    }
    if (JSON.stringify(found) === JSON.stringify(servers)) {
      keepPolling();
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

  function matches(plugin) {
    return words === "" || foldSearch(`${plugin.name} ${plugin.does}`).includes(words);
  }

  // Made once, so that what is typed in them stays while the page paints again.
  const form = addForm();
  const search = el("input", { class: "input plugin-search", type: "search", placeholder: "Search plugins", "aria-label": "Search plugins", autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const tabs = segmented(
    [
      { id: "connected", label: "Connected", hint: "The services Dot is connected to" },
      { id: "all", label: "All", hint: "Every service Dot works with" },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  tabs.element.classList.add("plugin-tabs");
  tabs.element.setAttribute("aria-label", "Plugins to show");
  const recheck = el("button", { class: "btn sm", type: "button", onclick: () => attempt(load) }, icon("refresh"), "Check again");
  // Out of what paints again, so that the search keeps the focus while the tiles are drawn anew; a service's page has
  // none of it.
  const bar = el("div", { class: "plugin-bar", hidden: pluginId !== undefined }, search, tabs.element, recheck);

  function paintTabs() {
    const [connectedButton, allButton] = tabs.element.children;
    const others = servers.servers.filter((server) => server.service === null).length;
    connectedButton.textContent = `Connected ${plugins.filter(connected).length + others}`;
    allButton.textContent = `All ${plugins.length}`;
  }

  function sections(list) {
    return SECTIONS.map(({ id, title }) => {
      const members = list.filter((plugin) => plugin.section === id);
      return members.length === 0
        ? null
        : el(
            "section",
            { class: "plugin-section", dataset: { section: id } },
            el("h2", { class: "section-label plugin-section-title" }, title),
            el("div", { class: "plugin-grid" }, members.map(tile)),
          );
    });
  }

  function paintList() {
    paintTabs();
    const showing = plugins.filter((plugin) => (shown === "all" || connected(plugin)) && matches(plugin));
    const others = servers.servers.filter((server) => server.service === null && (words === "" || foldSearch(`${server.name} ${server.address}`).includes(words)));
    const parts = sections(showing);
    if (others.length > 0 && (shown === "connected" || words !== "")) {
      parts.push(
        el(
          "section",
          { class: "plugin-section", dataset: { section: "servers" } },
          el("h2", { class: "section-label plugin-section-title" }, "Other MCP servers"),
          el("div", { class: "plugin-grid" }, others.map(serverTile)),
        ),
      );
    }
    if (showing.length === 0 && (others.length === 0 || shown === "all")) {
      parts.push(
        words !== ""
          ? emptyPanel("search", "No plugin matches the search", "")
          : emptyPanel("plug", "Dot is connected to nothing yet", "All lists what it works with."),
      );
    }
    parts.push(
      el(
        "section",
        { class: "card connect-section plugin-own", dataset: { kind: "mcp" } },
        el("div", { class: "card-head" }, icon("plug"), el("span", { class: "grow" }, "Your own MCP server")),
        el(
          "div",
          null,
          el("p", { class: "hint connect-hint" }, "MCP servers hand their tools to Dot. Dot tries one before it keeps it, signs in to it in this browser when it asks, and offers its tools to the runs that start after."),
          shown === "all" && words === "" ? others.map(serverTile) : null,
          form,
        ),
      ),
    );
    holder.replaceChildren(...parts.filter((part) => part !== null));
  }

  /** One way of a service on its page: what it is, how it connects, where it stands, what to do and its step. */
  function wayRow(_plugin, way) {
    const now = wayNow(way);
    let badge = null;
    let guideLine = null;
    let actions = null;
    if (way.kind === "mcp") {
      if (now.server === null && now.service !== null && now.service.added) {
        badge = el("span", { class: "badge b-muted" }, "Name taken");
        guideLine = `A server of Dot's config or of a plugin is named ${way.ref} already: Connected lists it.`;
      } else if (now.server === null) {
        const service = now.service;
        const keyed = service !== null && service !== undefined && service.secrets.length > 0;
        const button = el("button", { class: "btn sm primary", type: "button", disabled: busy.has(`mcp:${way.ref}`) }, "Add");
        button.addEventListener("click", () => attempt(() => (keyed ? askSecrets(service) : add({ service: way.ref, name: null, address: null, secrets: {} }))));
        // A service that takes a key asks for it here too (item 408), under the row.
        actions = el("div", { class: "plugin-actions" }, button, keyed && asking === way.ref ? secretsForm(service) : null);
        badge = el("span", { class: "badge b-muted" }, serviceWords(service));
      } else {
        badge = el("span", { class: `badge ${MCP_STATE_TONES[now.server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[now.server.state] ?? now.server.state);
        guideLine = serverGuide(now.server);
        actions = serverActions(now.server, now.service);
      }
    } else if (way.kind !== "untried" && now.app !== null) {
      const app = now.app;
      badge = el("span", { class: `badge ${CONNECTED_STATES.has(app.state) ? "b-ok" : app.state === "refused" ? "b-bad" : "b-muted"}` }, STATE_WORDS[app.state] ?? app.state);
      guideLine = appGuide(app, listing.runner) ?? (way.kind === "web" && !CONNECTED_STATES.has(app.state) ? webNote(listing) : null);
      if (app.step !== null) {
        // While macOS asks, the button says so, as Connect apps' did.
        const asking = busy.has(`app:${app.id}`);
        const label = asking && app.step === "ask" ? "Waiting for macOS…" : STEP_LABELS[app.step];
        const button = el("button", { class: app.step === "settings" ? "btn sm" : "btn sm primary", type: "button", disabled: asking }, label);
        button.addEventListener("click", () => attempt(() => takeStep(app)));
        actions = el("div", { class: "plugin-actions" }, button);
      }
    }
    return el(
      "div",
      { class: "list-row plugin-way", dataset: { way: way.kind, ref: way.ref ?? "" } },
      el(
        "div",
        { class: "row-main" },
        el("div", { class: "row" }, el("span", { class: "row-title" }, WAY_NAMES[way.kind]), badge),
        el("div", { class: "row-text muted" }, way.how),
        guideLine === null ? null : el("div", { class: "row-text plugin-guide" }, guideLine),
        way.kind === "mcp" && now.server !== null ? el("div", { class: "row-text muted mono plugin-address" }, now.server.address) : null,
      ),
      actions,
    );
  }

  function paintPlugin(plugin) {
    ctx.setTitle(`${plugin.name} · Plugins`);
    const tools = plugin.ways
      .filter((way) => way.kind === "mcp")
      .map((way) => wayNow(way).server)
      .filter((server) => server !== null && server.tools.length > 0)
      .flatMap((server) => server.tools);
    // What each way lets Dot do, when its ways do more than the line under the service's name says.
    const does = [...new Set(plugin.ways.map((way) => way.does).filter((line) => line !== null))];
    const data = plugin.ways.filter((way) => way.data !== null);
    const named = (way, text) => (data.length > 1 ? `${WAY_NAMES[way.kind]}: ${text}` : text);
    const card = (kind, title, mark, body) =>
      el("section", { class: "card connect-section plugin-card", dataset: { kind } }, el("div", { class: "card-head" }, icon(mark), el("span", { class: "grow" }, title)), el("div", null, body));
    const again = el("button", { class: "btn sm", type: "button", onclick: () => attempt(load) }, icon("refresh"), "Check again");
    const parts = [
      el("div", { class: "plugin-page-bar" }, el("a", { class: "back", href: "#/customize/plugins" }, icon("arrowLeft"), "Plugins"), again),
      el(
        "header",
        { class: "plugin-hero", dataset: { plugin: plugin.id } },
        pluginMark(plugin),
        el("div", { class: "plugin-main" }, el("h2", { class: "plugin-title" }, plugin.name), el("div", { class: "plugin-does" }, plugin.does), el("div", { class: "plugin-meta" }, howChip(plugin), standing(plugin))),
      ),
      does.length < 2 ? null : card("does", "What Dot does with it", "sparkle", el("ul", { class: "plugin-list" }, does.map((line) => el("li", null, line)))),
      card("ways", "How it connects", "link", plugin.ways.map((way) => wayRow(plugin, way))),
      tools.length === 0 ? null : card("tools", "Tools", "bolt", el("ul", { class: "plugin-list plugin-tools mono" }, tools.map((name) => el("li", null, name)))),
      data.length === 0
        ? null
        : card("data", "Where its data goes", "shield", el("ul", { class: "plugin-list" }, data.map((way) => el("li", null, named(way, way.data))))),
    ];
    holder.replaceChildren(...parts.filter((part) => part !== null));
  }

  function paint() {
    if (disposed || plugins === null) {
      return;
    }
    if (pluginId === undefined) {
      paintList();
    } else {
      const plugin = plugins.find((one) => one.id === pluginId);
      if (plugin === undefined) {
        holder.replaceChildren(el("a", { class: "back", href: "#/customize/plugins" }, icon("arrowLeft"), "Plugins"), errorPanel(new Error(`Dot lists no plugin ${pluginId}.`)));
      } else {
        paintPlugin(plugin);
      }
    }
    keepPolling();
  }

  async function load() {
    let found;
    try {
      found = await Promise.all([api.get("/plugins"), api.get("/connections"), api.get("/mcp/servers")]);
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      [{ plugins }, listing, servers] = found;
      paint();
    }
  }

  root.append(bar, holder);
  attempt(load);
  return () => {
    disposed = true;
    clearTimeout(polling);
  };
}
