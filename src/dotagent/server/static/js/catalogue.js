// The services Dot works with and where each stands (item 316), for Customize › Connectors (views/connectors.js), where
// Connect apps leads (item 545): the listings of `GET /plugins`, the applications of `GET /connections` and the MCP
// servers of `GET /mcp/servers`, the first step each service offers and the steps under way. The steps (items 278 and
// 279) are macOS's question for an application of the Mac, and after a no the Automation pane of System Settings;
// OneDrive itself, to sign in; the sign-in page in Dot's browser for an application of the web; an MCP server that Dot
// tries first, and signs in to in this browser on the service's own page when it asks.

import { api, blobUrl } from "./api.js";
import { clip, el, plural } from "./dom.js";
import { markContent, markTile } from "./marks.js";
import { field } from "./panels.js";
import { attempt, confirmDialog, formDialog } from "./ui.js";

export const STATE_WORDS = {
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
export const CONNECTED_STATES = new Set(["allowed", "signed_in", "synced", "lent"]);
export const STEP_LABELS = { ask: "Allow", settings: "Open System Settings", sign_in: "Sign in", open_app: "Open" };
export const MCP_STATE_WORDS = { ready: "Ready", left_out: "Left out", disabled: "Off", trying: "Trying…", signing_in: "Waiting for your sign-in" };
export const MCP_STATE_TONES = { ready: "b-ok", left_out: "b-bad", trying: "b-info", signing_in: "b-warn" };
// What each way is called on a service's page, and on its tile when it is the service's first.
export const WAY_NAMES = { mcp: "Connected app", mac: "Mac app", web: "Dot's browser", folder: "Synced folder", vault: "1Password" };
// Where a service account token for 1Password is made (item 493), at 1password.com.
const VAULT_TOKEN_HELP = "https://start.1password.com/developer-tools/infrastructure-secrets/serviceaccount/";
// While Dot tries a server or waits for a sign-in, the page asks how it stands this often.
const MCP_POLL_MS = 2000;

/**
 * A service's mark on its tile: its logo in its own colours on a tile that is light in both colour schemes, else the
 * interface's icon or the plug on a tile of the listing's colour (item 542). `appIcon` is the pending `blob:` URL of
 * the icon of the application of this Mac the service stands for, as the owner has it installed
 * (`GET /connections/{id}/icon`), or null; the icon takes the tile's place once it is read, and the mark is as drawn
 * meanwhile, and as it stays when the icon cannot be read (the one that asked says why).
 */
export function pluginMark(plugin, appIcon) {
  const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" }, markContent(plugin));
  markTile(mark, plugin, plugin.id);
  if (appIcon !== null) {
    appIcon.then(
      (url) => {
        mark.dataset.icon = "app";
        delete mark.dataset.tint;
        delete mark.dataset.light;
        mark.replaceChildren(el("img", { class: "plugin-logo", src: url, alt: "" }));
      },
      () => undefined,
    );
  }
  return mark;
}

/** How a service of the catalogue connects, in a chip's words (item 408): signed in, with a key, open to all, or
 * started on this Mac. */
function mcpWords(service) {
  if (service === undefined || service === null) {
    return "Connected app";
  }
  if (service.kind === "local") {
    return service.secrets.length > 0 ? "On this Mac · Key" : "On this Mac";
  }
  if (service.secrets.length > 0) {
    return "Key";
  }
  return service.signs_in ? "Sign in" : "Open to all";
}

/** Where a service of the catalogue stands before it is added, in a badge's words (item 408). */
export function serviceWords(service) {
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

/**
 * The dialog that chooses the tools of an MCP server (`offered`, of `GET /mcp/servers/{name}/offered`): a tick for
 * each, those of its allow list ticked, or every one when it has none, and All tools to tick or untick them at once.
 * Resolves to the names chosen, or null when the owner closed it.
 */
function toolsDialog(offered) {
  return new Promise((resolve) => {
    const ticks = [];
    const dialog = el("dialog", { class: "modal import-dialog", "aria-labelledby": "tools-title" });
    const go = el("button", { class: "btn primary", type: "button", onclick: () => dialog.close("ok") });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, "Cancel");
    const count = el("span", { class: "hint" });
    const chosen = new Set(offered.allow.length === 0 ? offered.tools.map((tool) => tool.name) : offered.allow);
    const all = el("input", {
      type: "checkbox",
      onchange: () => {
        for (const tick of ticks) {
          tick.box.checked = all.checked;
        }
        paint();
      },
    });

    function paint() {
      const picked = ticks.filter((tick) => tick.box.checked).length;
      go.textContent = picked === 0 ? "Save" : `Save ${picked}`;
      go.disabled = picked === 0;
      count.textContent = `${picked} of ${ticks.length}`;
      all.checked = picked === ticks.length;
      all.indeterminate = picked > 0 && picked < ticks.length;
    }

    const rows = offered.tools.map((tool) => {
      const box = el("input", { type: "checkbox", checked: chosen.has(tool.name), onchange: paint });
      ticks.push({ box, name: tool.name });
      return el(
        "label",
        { class: "import-row" },
        box,
        el("span", { class: "import-row-main" }, el("span", { class: "import-row-name mono" }, tool.name), el("span", { class: "import-row-detail" }, clip(tool.description, 140))),
      );
    });
    dialog.append(
      el(
        "div",
        { class: "modal-body" },
        el("h3", { id: "tools-title" }, `Choose the actions of ${offered.server}`),
        el("p", { class: "muted" }, `It offers ${plural(offered.tools.length, "action", "actions")}. The ones you tick are offered to the tasks that start next; with all of them ticked, so are the actions it adds later.`),
        count,
        el("label", { class: "import-row" }, all, el("span", { class: "import-row-main" }, el("span", { class: "import-row-name" }, "All actions"))),
        el("div", { class: "import-list" }, rows),
      ),
      el("div", { class: "modal-actions" }, cancel, go),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(dialog.returnValue === "ok" ? ticks.filter((one) => one.box.checked).map((one) => one.name) : null);
    });
    paint();
    document.body.append(dialog);
    dialog.showModal();
    go.focus();
  });
}

/**
 * The services and where each stands, read with `load()`: `onChange()` runs whenever what is shown should be drawn
 * again (a step started or ended, the servers changed). While Dot tries a server or waits for a sign-in, it asks again
 * how the servers stand by itself. Returns the state (`plugins`, `listing`, `servers`) and what reads and acts on it; a
 * service's key and 1Password's token are asked for in a dialog each (item 615). `dispose()` stops the asking and lets go
 * of the icons read.
 */
export function createCatalogue(onChange) {
  let disposed = false;
  let plugins = null;
  let listing = null;
  let servers = null;
  // Where the owner's 1Password stands (item 493, `GET /vault`).
  let vault = null;
  let polling = null;
  // The steps under way, by what they act on (`app:<id>`, `mcp:<service or name>`, `server:<name>`): their buttons stay
  // off while the page paints again, and an application's says that macOS asks.
  const busy = new Set();
  // The icons of the applications of this Mac shown, by application: the `blob:` URL of each, pending or made.
  const appIcons = new Map();
  function changed() {
    if (!disposed) {
      onChange();
    }
  }

  /** The pending `blob:` URL of the icon of the application `plugin` stands for, read once, when that application is
   * installed and has one; else null. */
  function appIcon(plugin) {
    const app = plugin.app === null ? undefined : listing.apps.find((one) => one.id === plugin.app);
    if (app === undefined || !app.icon) {
      return null;
    }
    let pending = appIcons.get(app.id);
    if (pending === undefined) {
      pending = blobUrl(`/connections/${encodeURIComponent(app.id)}/icon`);
      appIcons.set(app.id, pending);
      pending.catch((error) => {
        appIcons.delete(app.id);
        console.error(`the icon of ${app.name} could not be read`, error);
      });
    }
    return pending;
  }

  /** Where `way` stands now: its application of `GET /connections`, or its server and service of `GET /mcp/servers`. */
  function wayNow(way) {
    if (way.kind === "mcp") {
      return {
        server: servers.servers.find((one) => one.service === way.ref) ?? null,
        service: servers.services.find((one) => one.id === way.ref) ?? null,
      };
    }
    if (way.kind === "vault") {
      return { vault };
    }
    return { app: listing.apps.find((one) => one.id === way.ref) ?? null };
  }

  function wayConnected(way) {
    const now = wayNow(way);
    if (way.kind === "mcp") {
      return now.server !== null && now.server.state === "ready";
    }
    if (way.kind === "vault") {
      return vaultReady();
    }
    return now.app !== undefined && now.app !== null && CONNECTED_STATES.has(now.app.state);
  }

  /** Whether 1Password is connected and its vaults were read (item 493). */
  function vaultReady() {
    return vault?.connected === true && vault.error === null && vault.vaults.length > 0;
  }

  function connected(plugin) {
    return plugin.ways.some(wayConnected);
  }

  /** The first step `plugin` offers, by its ways in order: `{ label, take }` for a button, `{ label, link }` for the
   * sign-in page an MCP server waits on, or null when it has none to take, as while Dot tries its server. A service
   * whose name a server of the config or of a plugin holds already is not added again. A service that takes a key
   * (item 408) asks for it first (`keyed`): its step opens the dialog that takes it (item 615). 1Password's step is its
   * page (`page`, item 493), which offers its two ways to connect. */
  function firstStep(plugin) {
    for (const way of plugin.ways) {
      const now = wayNow(way);
      if (way.kind === "vault") {
        // Its page says what Connect makes and offers a token instead (item 493): the step is that page (`page`).
        if (vault !== null && !vault.connected) {
          return { label: "Connect", key: "vault", keyed: true, page: true, take: async () => undefined };
        }
        continue;
      }
      if (way.kind === "mcp") {
        if (now.server === null) {
          if (now.service?.added) {
            continue;
          }
          const service = now.service;
          if (service !== undefined && service !== null && service.secrets.length > 0) {
            const button = () => document.querySelector(`[data-plugin="${plugin.id}"] .plugin-add`);
            return { label: "Add", key: `mcp:${way.ref}`, keyed: true, take: () => askSecrets(service, button) };
          }
          return { label: "Add", key: `mcp:${way.ref}`, keyed: false, take: () => add({ service: way.ref, name: null, address: null, secrets: {} }) };
        }
        if (now.server.state === "signing_in" && now.server.link !== null) {
          return { label: "Sign in", link: now.server.link };
        }
        if (now.server.state === "trying" || now.server.state === "signing_in") {
          return null;
        }
        if (now.server.state === "left_out" && now.server.origin === "connected") {
          const server = now.server;
          return { label: server.signs_in ? "Sign in again" : "Try again", key: `server:${server.name}`, keyed: false, take: () => connectAgain(server) };
        }
        continue;
      }
      if (now.app !== undefined && now.app !== null && now.app.step !== null) {
        const app = now.app;
        return { label: STEP_LABELS[app.step], key: `app:${app.id}`, keyed: false, take: () => takeStep(app) };
      }
    }
    return null;
  }

  /** Where `plugin` stands, in a badge: a way that is connected, else the server Dot has for it, else its first way;
   * null for an MCP service Dot has no server of. */
  function standing(plugin) {
    const { ways } = plugin;
    const shown = ways.find(wayConnected) ?? ways.find((way) => way.kind === "mcp" && wayNow(way).server !== null) ?? ways[0];
    if (shown === undefined) {
      return null;
    }
    const now = wayNow(shown);
    if (shown.kind === "vault") {
      return vaultBadge();
    }
    if (shown.kind === "mcp") {
      return now.server === null ? null : el("span", { class: `badge ${MCP_STATE_TONES[now.server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[now.server.state] ?? now.server.state);
    }
    if (now.app === null) {
      return null;
    }
    const tone = CONNECTED_STATES.has(now.app.state) ? "b-ok" : now.app.state === "refused" ? "b-bad" : "b-muted";
    return el("span", { class: `badge ${tone}` }, STATE_WORDS[now.app.state] ?? now.app.state);
  }

  /** Where 1Password stands, in a badge (item 493). */
  function vaultBadge() {
    if (vault === null) {
      return null;
    }
    if (!vault.connected) {
      return el("span", { class: "badge b-muted" }, vault.cli === null ? "Not installed" : "Not connected");
    }
    if (vault.error !== null) {
      return el("span", { class: "badge b-bad" }, "Cannot read");
    }
    const logins = vault.vaults.reduce((sum, one) => sum + one.logins, 0);
    return el("span", { class: "badge b-ok" }, plural(logins, "login", "logins"));
  }

  /** Ask for a service account token for 1Password, on its page, in a dialog (items 493 and 615): a password field,
   * where to make one, Cancel and Connect. The token goes to the daemon once, which keeps it in its .env; the field is
   * emptied as the dialog closes. Resolves once the dialog is closed. `returnFocus` finds the button the dialog gives
   * the focus back to when the page was drawn again meanwhile. */
  function askVault(returnFocus) {
    const input = el("input", { class: "input mono", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-label": "1Password: service account key" });
    return formDialog({
      title: "Use a service account key",
      description: "A key you made at 1password.com that reads a vault of yours. Dot keeps it on this Mac.",
      body: field("Service account key", input, el("a", { href: VAULT_TOKEN_HELP, target: "_blank", rel: "noopener noreferrer" }, "Make one that reads a vault of yours")),
      submitLabel: "Connect",
      size: "narrow",
      returnFocus,
      onSubmit: () => pasteVaultToken(input.value.trim()),
    }).closed;
  }

  /** Connect 1Password with the owner's op, through the 1Password app, which asks them first (item 493). */
  function connectVault() {
    return busyWith("vault", async () => {
      const now = await api.post("/vault/connect", {});
      if (!disposed) {
        vault = now;
      }
    });
  }

  function pasteVaultToken(token) {
    return busyWith("vault", async () => {
      const now = await api.post("/vault/token", { token });
      if (!disposed) {
        vault = now;
      }
    });
  }

  async function disconnectVault() {
    const sure = await confirmDialog({
      title: "Disconnect 1Password from Dot?",
      message: "Tasks fill in no login from it any more, and Dot forgets the service account's key. The vault and the service account stay in 1Password, whose page removes them.",
      confirmLabel: "Disconnect",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await busyWith("vault", async () => {
      const now = await api.del("/vault");
      if (!disposed) {
        vault = now;
      }
    });
  }

  /** How `plugin` connects, in a chip: its first way, an MCP server's sign-in with it. */
  function howChip(plugin) {
    const [first] = plugin.ways;
    if (first.kind === "mcp") {
      const service = servers.services.find((one) => one.id === first.ref);
      return el("span", { class: "chip" }, mcpWords(service));
    }
    return el("span", { class: "chip" }, WAY_NAMES[first.kind]);
  }

  /**
   * Ask for the secrets of a service that takes them (item 408), in a dialog (item 615): a password field for each,
   * where to get one, Cancel and Add. The values go to the daemon once, which keeps them in its .env; the fields are
   * emptied as the dialog closes, so a key typed and then cancelled is gone when the dialog opens again (item 559).
   * Resolves once the dialog is closed. `returnFocus` finds the button the dialog gives the focus back to when the page
   * was drawn again meanwhile.
   */
  function askSecrets(service, returnFocus) {
    const inputs = service.secrets.map((secret) => [
      secret,
      el("input", { class: "input mono", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-label": `${service.name}: ${secret.label}` }),
    ]);
    return formDialog({
      title: `Add ${service.name}`,
      description: "Dot tries the connection before it keeps it, and keeps the key on this Mac.",
      body: inputs.map(([secret, input]) =>
        field(secret.label, input, secret.help === null ? "" : el("a", { href: secret.help, target: "_blank", rel: "noopener noreferrer" }, "Where to get one")),
      ),
      submitLabel: "Add",
      size: "narrow",
      returnFocus,
      onSubmit: () => add({ service: service.id, name: null, address: null, secrets: Object.fromEntries(inputs.map(([secret, input]) => [secret.name, input.value])) }),
    }).closed;
  }

  /** Do `work` with `key` busy: what is shown is drawn again as it starts and once it is done. */
  async function busyWith(key, work) {
    busy.add(key);
    changed();
    try {
      await work();
    } finally {
      busy.delete(key);
      changed();
    }
  }

  function takeStep(app) {
    return busyWith(`app:${app.id}`, async () => {
      const now = await api.post(`/connections/${encodeURIComponent(app.id)}`, {});
      if (!disposed) {
        listing = { ...listing, apps: listing.apps.map((one) => (one.id === now.id ? now : one)) };
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

  /** Switch `server`, one that comes with Dot (item 583), on or off: on starts it and its tools go to the runs that
   * start after, off stops it; the config file keeps the switch. The page shows the listing the daemon answers with. */
  function switchServer(server, on) {
    return busyWith(`server:${server.name}`, async () => {
      const now = await api.put(`/mcp/servers/${encodeURIComponent(server.name)}/switch`, { on });
      if (!disposed) {
        servers = now;
      }
    });
  }

  /** Choose the tools of `server`: they become its allow list (none with every one chosen), and Dot connects it again
   * with them. */
  async function chooseTools(server) {
    const path = `/mcp/servers/${encodeURIComponent(server.name)}`;
    let offered = null;
    await busyWith(`server:${server.name}`, async () => {
      offered = await api.get(`${path}/offered`);
    });
    const tools = disposed || offered === null ? null : await toolsDialog(offered);
    if (tools === null) {
      return;
    }
    await busyWith(`server:${server.name}`, async () => {
      await api.put(`${path}/allow`, { tools });
      await refreshServers();
    });
  }

  async function remove(server, service) {
    // One added from another agent of this Mac is taken back from Dot alone (item 497): the agent keeps it.
    const sure = await confirmDialog({
      title: `Remove ${service ? service.name : server.name} from Dot?`,
      message:
        server.origin === "elsewhere"
          ? "Dot stops it now and its actions leave the tasks that start after; the app it came from keeps it."
          : "Its actions leave the tasks that start after, and Dot forgets its address and its sign-in.",
      confirmLabel: "Remove",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await busyWith(`server:${server.name}`, async () => {
      const now = await api.del(`/mcp/servers/${encodeURIComponent(server.name)}`);
      if (!disposed) {
        servers = now;
      }
    });
  }

  /** Take the plugin `server` comes with out of Dot (item 497): its servers stop now, its skills leave the runs that
   * start after, and its folder goes to the Trash. */
  async function removePlugin(server) {
    const sure = await confirmDialog({
      title: `Remove the plugin ${server.plugin} from Dot?`,
      message: "Its connected apps stop now and its skills leave the tasks that start after. Its folder goes to the Trash, where you can put it back.",
      confirmLabel: "Remove",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    await busyWith(`server:${server.name}`, async () => {
      const now = await api.del(`/installed-plugins/${encodeURIComponent(server.plugin)}`);
      if (!disposed) {
        servers = now;
      }
    });
  }

  /** Ask again how the MCP servers stand, and keep asking while Dot tries one or waits for a sign-in. What is shown is
   * drawn again only when they changed, so that a field being typed in, a tap and a step under way survive the asking. */
  async function refreshServers() {
    const found = await api.get("/mcp/servers");
    if (disposed) {
      return;
    }
    if (JSON.stringify(found) !== JSON.stringify(servers)) {
      servers = found;
      changed();
    }
    keepPolling();
  }

  function keepPolling() {
    clearTimeout(polling);
    polling = null;
    if (!disposed && servers !== null && servers.servers.some((server) => server.state === "trying" || server.state === "signing_in")) {
      polling = setTimeout(() => attempt(refreshServers), MCP_POLL_MS);
    }
  }

  /** Read the services, the applications, the servers and 1Password; it throws when the daemon could not be asked. */
  async function load() {
    const found = await Promise.all([api.get("/plugins"), api.get("/connections"), api.get("/mcp/servers"), api.get("/vault")]);
    if (disposed) {
      return;
    }
    [{ plugins }, listing, servers, vault] = found;
    changed();
    keepPolling();
  }

  return {
    get plugins() {
      return plugins;
    },
    get listing() {
      return listing;
    },
    get servers() {
      return servers;
    },
    get vault() {
      return vault;
    },
    /** Whether the step that acts on `key` is under way. */
    busy: (key) => busy.has(key),
    appIcon,
    wayNow,
    connected,
    firstStep,
    standing,
    howChip,
    askSecrets,
    askVault,
    vaultBadge,
    connectVault,
    disconnectVault,
    takeStep,
    add,
    connectAgain,
    switchServer,
    chooseTools,
    remove,
    removePlugin,
    load,
    dispose() {
      disposed = true;
      clearTimeout(polling);
      for (const pending of appIcons.values()) {
        // An icon that could not be read was reported where it was asked for; there is no URL to let go of.
        pending.then((url) => URL.revokeObjectURL(url), () => undefined);
      }
      appIcons.clear();
    },
  };
}
