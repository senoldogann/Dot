// Customize › Plugins (item 316): every service Dot works with, after Codex's page. A search, Connected and All, and the
// services in sections (Popular, Code, Design, Data, Research, Communication, Productivity, Business, Support, This Mac):
// each a tile with its mark, what Dot does with it and how it connects, and a "+" that takes its first step, or where it
// stands once it is connected. A tile opens the service's own page: what Dot does with it, each way it connects with
// where it stands and its step, its tools, where its data goes. The services, where they stand and their steps are
// catalogue.js's, which the Connect apps dialog shares (item 492); a service none of whose ways was tried says "Not
// tried yet" and offers no step. Under the sections, an MCP server of one's own by its address; Connected lists too the
// servers that come from Dot's config, a plugin or another agent of the Mac.

import { accountsCard } from "../accounts.js";
import { createCatalogue, MCP_STATE_TONES, MCP_STATE_WORDS, pluginMark, serviceWords, STATE_WORDS, STEP_LABELS, WAY_NAMES, CONNECTED_STATES } from "../catalogue.js";
import { el, foldSearch, plural } from "../dom.js";
import { icon } from "../icons.js";
import { markContent, markInk } from "../marks.js";
import { emptyPanel, errorPanel, field, loadingPanel, segmented } from "../panels.js";
import { attempt } from "../ui.js";

/** The sections of the services, in the order the page and the Connect apps dialog show them (item 492). */
export const SECTIONS = [
  { id: "popular", title: "Popular" },
  { id: "code", title: "Code" },
  { id: "design", title: "Design" },
  { id: "data", title: "Data" },
  { id: "research", title: "Research" },
  { id: "communication", title: "Communication" },
  { id: "productivity", title: "Productivity" },
  { id: "business", title: "Business" },
  { id: "support", title: "Support" },
  { id: "mac", title: "This Mac" },
];
const MCP_ORIGIN_WORDS = {
  config: "Set up in Dot's config.",
  plugin: "Comes with one of Dot's plugins.",
  elsewhere: "Added from another agent of this Mac (Customize › Skills › Found on this Mac).",
};
// A server's tile names this many of its tools.
const SHOWN_TOOLS = 6;

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

/** The buttons of an MCP server of `catalogue`: its sign-in page while Dot waits for one, Sign in again or Try again,
 * and Remove for one added here; null when it has none. */
function serverActions(catalogue, server, service) {
  const working = server.state === "trying" || server.state === "signing_in";
  const actions = [];
  if (server.state === "signing_in" && server.link !== null) {
    actions.push(el("a", { class: "btn sm primary", href: server.link, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "Open the sign-in page"));
  }
  const again = server.origin === "connected" || (server.origin === "config" && server.signs_in);
  const acting = catalogue.busy(`server:${server.name}`);
  if (again && !working && (server.signs_in || server.state === "left_out")) {
    const button = el("button", { class: "btn sm", type: "button", disabled: acting }, server.signs_in ? "Sign in again" : "Try again");
    button.addEventListener("click", () => attempt(() => catalogue.connectAgain(server)));
    actions.push(button);
  }
  if (server.picks_tools && server.origin === "connected" && !working) {
    actions.push(el("button", { class: "btn sm primary", type: "button", disabled: acting, onclick: () => attempt(() => catalogue.chooseTools(server)) }, "Choose tools"));
  }
  if (server.origin === "connected") {
    actions.push(el("button", { class: "btn sm", type: "button", disabled: acting, onclick: () => attempt(() => catalogue.remove(server, service)) }, "Remove"));
  }
  return actions.length === 0 ? null : el("div", { class: "plugin-actions" }, ...actions);
}

/** One way of a service on its page: what it is, how it connects, where it stands, what to do and its step. */
function wayRow(catalogue, way) {
  const now = catalogue.wayNow(way);
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
      const button = el("button", { class: "btn sm primary", type: "button", disabled: catalogue.busy(`mcp:${way.ref}`) }, "Add");
      button.addEventListener("click", () => attempt(() => (keyed ? catalogue.askSecrets(service) : catalogue.add({ service: way.ref, name: null, address: null, secrets: {} }))));
      // A service that takes a key asks for it here too (item 408), under the row.
      actions = el("div", { class: "plugin-actions" }, button, keyed && catalogue.asking() === way.ref ? catalogue.secretsForm(service) : null);
      badge = el("span", { class: "badge b-muted" }, serviceWords(service));
    } else {
      badge = el("span", { class: `badge ${MCP_STATE_TONES[now.server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[now.server.state] ?? now.server.state);
      guideLine = serverGuide(now.server);
      actions = serverActions(catalogue, now.server, now.service);
    }
  } else if (way.kind !== "untried" && now.app !== null) {
    const app = now.app;
    const listing = catalogue.listing;
    badge = el("span", { class: `badge ${CONNECTED_STATES.has(app.state) ? "b-ok" : app.state === "refused" ? "b-bad" : "b-muted"}` }, STATE_WORDS[app.state] ?? app.state);
    guideLine = appGuide(app, listing.runner) ?? (way.kind === "web" && !CONNECTED_STATES.has(app.state) ? webNote(listing) : null);
    if (app.step !== null) {
      // While macOS asks, the button says so, as Connect apps' did.
      const asking = catalogue.busy(`app:${app.id}`);
      const label = asking && app.step === "ask" ? "Waiting for macOS…" : STEP_LABELS[app.step];
      const button = el("button", { class: app.step === "settings" ? "btn sm" : "btn sm primary", type: "button", disabled: asking }, label);
      button.addEventListener("click", () => attempt(() => catalogue.takeStep(app)));
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

/**
 * What a service's own page shows of `plugin`, a listing of `catalogue`, under its bar: its mark, name, what Dot does
 * with it and where it stands; what each way lets Dot do, when its ways do more than that line; how it connects, each
 * way with its step; the tools of its servers; and where its data goes. The Plugins page and the Connect apps dialog
 * (item 492) both show it.
 */
export function serviceParts(catalogue, plugin) {
  const tools = plugin.ways
    .filter((way) => way.kind === "mcp")
    .map((way) => catalogue.wayNow(way).server)
    .filter((server) => server !== null && server.tools.length > 0)
    .flatMap((server) => server.tools);
  const does = [...new Set(plugin.ways.map((way) => way.does).filter((line) => line !== null))];
  const data = plugin.ways.filter((way) => way.data !== null);
  const named = (way, text) => (data.length > 1 ? `${WAY_NAMES[way.kind]}: ${text}` : text);
  const card = (kind, title, mark, body) =>
    el("section", { class: "card connect-section plugin-card", dataset: { kind } }, el("div", { class: "card-head" }, icon(mark), el("span", { class: "grow" }, title)), el("div", null, body));
  const parts = [
    el(
      "header",
      { class: "plugin-hero", dataset: { plugin: plugin.id } },
      pluginMark(plugin, catalogue.appIcon(plugin)),
      el("div", { class: "plugin-main" }, el("h2", { class: "plugin-title" }, plugin.name), el("div", { class: "plugin-does" }, plugin.does), el("div", { class: "plugin-meta" }, catalogue.howChip(plugin), catalogue.standing(plugin))),
    ),
    does.length < 2 ? null : card("does", "What Dot does with it", "sparkle", el("ul", { class: "plugin-list" }, does.map((line) => el("li", null, line)))),
    card("ways", "How it connects", "link", plugin.ways.map((way) => wayRow(catalogue, way))),
    tools.length === 0 ? null : card("tools", "Tools", "bolt", el("ul", { class: "plugin-list plugin-tools mono" }, tools.map((name) => el("li", null, name)))),
    data.length === 0 ? null : card("data", "Where its data goes", "shield", el("ul", { class: "plugin-list" }, data.map((way) => el("li", null, named(way, way.data))))),
  ];
  return parts.filter((part) => part !== null);
}

export function mount(root, ctx) {
  const pluginId = ctx.params[1];
  let disposed = false;
  let shown = "all";
  let words = "";
  const holder = el("div", { class: "stack" }, loadingPanel());
  const catalogue = createCatalogue(paint);

  function tile(plugin) {
    const isConnected = catalogue.connected(plugin);
    const step = isConnected ? null : catalogue.firstStep(plugin);
    const asked = catalogue.asking();
    const askedService = asked === null ? null : (catalogue.servers.services.find((one) => one.id === asked && plugin.ways.some((way) => way.kind === "mcp" && way.ref === one.id)) ?? null);
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
          disabled: catalogue.busy(step.key),
        },
        icon("plus"),
      );
      action.addEventListener("click", () => attempt(step.take));
    }
    const waiting = plugin.ways.some((way) => way.kind === "mac" && catalogue.busy(`app:${way.ref}`));
    return el(
      "div",
      { class: "plugin-tile", dataset: { plugin: plugin.id, connected: String(isConnected), tried: String(plugin.tried) } },
      pluginMark(plugin, catalogue.appIcon(plugin)),
      el(
        "div",
        { class: "plugin-main" },
        el("a", { class: "plugin-name", href: `#/customize/plugins/${plugin.id}` }, plugin.name),
        el("div", { class: "plugin-does" }, plugin.does),
        el("div", { class: "plugin-meta" }, catalogue.howChip(plugin), waiting ? el("span", { class: "badge b-warn" }, "Waiting for macOS…") : catalogue.standing(plugin)),
        askedService === null ? null : catalogue.secretsForm(askedService),
      ),
      action,
    );
  }

  /** The mark of a server added by hand: the mark of the service it is one of, by its address or its command, as the
   * service's tile wears it (items 408 and 477), else the plug. */
  function serverMark(server) {
    const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" }, markContent(server.mark));
    const ink = server.mark === null ? null : markInk(server.mark.colour);
    if (ink !== null) {
      mark.style.setProperty("--plugin-ink", ink);
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
      serverActions(catalogue, server, null),
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
        button.disabled = true;
        try {
          await catalogue.add({ service: null, name: name.value.trim(), address: address.value.trim(), secrets: {} });
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
    const others = catalogue.servers.servers.filter((server) => server.service === null).length;
    connectedButton.textContent = `Connected ${catalogue.plugins.filter(catalogue.connected).length + others}`;
    allButton.textContent = `All ${catalogue.plugins.length}`;
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
    const showing = catalogue.plugins.filter((plugin) => (shown === "all" || catalogue.connected(plugin)) && matches(plugin));
    const others = catalogue.servers.servers.filter((server) => server.service === null && (words === "" || foldSearch(`${server.name} ${server.address}`).includes(words)));
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

  function paintPlugin(plugin) {
    ctx.setTitle(`${plugin.name} · Plugins`);
    const again = el("button", { class: "btn sm", type: "button", onclick: () => attempt(load) }, icon("refresh"), "Check again");
    holder.replaceChildren(el("div", { class: "plugin-page-bar" }, el("a", { class: "back", href: "#/customize/plugins" }, icon("arrowLeft"), "Plugins"), again), ...serviceParts(catalogue, plugin));
  }

  function paint() {
    if (disposed || catalogue.plugins === null) {
      return;
    }
    if (pluginId === undefined) {
      paintList();
      return;
    }
    const plugin = catalogue.plugins.find((one) => one.id === pluginId);
    if (plugin === undefined) {
      holder.replaceChildren(el("a", { class: "back", href: "#/customize/plugins" }, icon("arrowLeft"), "Plugins"), errorPanel(new Error(`Dot lists no plugin ${pluginId}.`)));
    } else {
      paintPlugin(plugin);
    }
  }

  async function load() {
    try {
      await catalogue.load();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
    }
  }

  // The official sign-ins of Google and Microsoft (item 422) head the list of the plugins, not a service's own page.
  const accounts = pluginId === undefined ? accountsCard() : null;
  root.append(bar, ...(accounts === null ? [] : [accounts.element]), holder);
  attempt(load);
  return () => {
    disposed = true;
    accounts?.dispose();
    catalogue.dispose();
  };
}
