// Settings › Connectors (items 316, 542 and 545): every service Dot works with, where Connect apps at the foot of the
// sidebar leads. The head the three tabs share (customizehead.js) with the search, Connected and All and Check again;
// the accounts; the sections as chips, one showing its section alone; and the services in sections (Popular, which
// the page opens on, its chip pressed, until All, another chip, a search or Connected lists the rest, item 1000; Code, Design, Data, Research,
// Communication, Productivity, Business, Support, This Mac): each a card with its mark, what Dot does with it and how it connects, and its first step in words
// (Add, Allow, Sign in), or where it stands once it is connected. A card opens the service's own page: what Dot does
// with it, each way it connects with where it stands and its step, its tools, where its data goes. The services, where
// they stand and their steps are catalogue.js's; a service Dot has no way to reach is not listed (item 542). A service's
// key, 1Password's token and an MCP server of one's own are asked for in dialogs (item 615). Under the sections, an MCP
// server of one's own by its address; Connected lists too the servers that come from Dot's config, a
// plugin or another agent of the Mac, and the one that comes with Dot, whose switch is on its tile (item 583). The
// plugins Dot installs are Plugins' (views/plugins.js).

import { accountsCard } from "../accounts.js";
import { createCatalogue, MCP_STATE_TONES, MCP_STATE_WORDS, pluginMark, serviceWords, STATE_WORDS, STEP_LABELS, WAY_NAMES, CONNECTED_STATES } from "../catalogue.js";
import { customizeHead } from "../customizehead.js";
import { el, foldSearch, } from "../dom.js";
import { icon } from "../icons.js";
import { markContent, markTile } from "../marks.js";
import { emptyPanel, errorPanel, field, loadingPanel, segmented, switchToggle } from "../panels.js";
import { attempt, formDialog } from "../ui.js";
import { t, tCount } from "../i18n.js";

/** The sections of the services, in the order the page shows them and its chips name them (items 492 and 545). */
const SECTIONS = [
  { id: "popular", title: t("views.connectors.popular") },
  { id: "passwords", title: t("views.connectors.passwordManagers") },
  { id: "code", title: t("views.connectors.code") },
  { id: "design", title: t("views.connectors.design") },
  { id: "data", title: t("views.connectors.data") },
  { id: "research", title: t("views.connectors.research") },
  { id: "communication", title: t("views.connectors.communication") },
  { id: "productivity", title: t("views.connectors.productivity") },
  { id: "business", title: t("views.connectors.business") },
  { id: "support", title: t("views.connectors.support") },
  { id: "mac", title: t("views.connectors.thisMac") },
];
// The chip the page opens on (item 1000).
const POPULAR = "popular";
const MCP_ORIGIN_WORDS = {
  config: t("views.connectors.setUpInDotS"),
  plugin: t("views.connectors.comesWithOneOfDot"),
  elsewhere: t("views.connectors.addedFromAnotherAgentOf"),
  builtin: t("views.connectors.comesWithDotItSearches"),
};
// A server's tile names this many of its tools.
const SHOWN_TOOLS = 6;

/** The tools a server offers to runs, the first few by name. */
function toolLine(tools) {
  const shown = tools.slice(0, SHOWN_TOOLS).join(", ");
  return `${tCount(tools.length, "views.connectors.countAction", "views.connectors.countActions")}: ${shown}${tools.length > SHOWN_TOOLS ? ", …" : ""}`;
}

/** How to sign in to the applications of the web, the same for each of them: it depends on Dot's browser alone. */
function webNote(listing) {
  if (listing.browser_mode === "attach") {
    return t("views.connectors.dotWorksInYourOwn");
  }
  return listing.headless
    ? t("views.connectors.dotSBrowserWorksWithout")
    : t("views.connectors.signInOpensThePage");
}

/** What to do or to know for an application, by where it stands: one line, or none. `runner` is the name macOS asks
 * for Dot under and lists in Privacy & Security (Dot for the service, item 288). */
function appGuide(app, runner) {
  if (app.waiting) {
    return t("views.connectors.macosIsAskingOnThe");
  }
  switch (app.state) {
    case "refused":
      return t("views.connectors.macosWasToldNoIn", { runner, name: app.name });
    case "not_installed":
      return t("views.connectors.installNameToUseIt", { name: app.name });
    case "not_synced":
      return t("views.connectors.nameSyncsNoFolderYet", { name: app.name });
    default:
      return null;
  }
}

/** What to do or to know for one MCP server, by where it stands: one line, or none. */
function serverGuide(server) {
  switch (server.state) {
    case "signing_in":
      return t("views.connectors.signInOnTheService");
    case "trying":
      return t("views.connectors.dotIsConnectingToIt");
    case "left_out":
      return server.error;
    case "disabled":
      return server.origin === "builtin" ? t("views.connectors.switchedOffItsSwitchStarts") : t("views.connectors.switchedOffInDotS");
    default:
      return null;
  }
}

/** The buttons of an MCP server of `catalogue`: its sign-in page while Dot waits for one, Sign in again or Try again,
 * Remove for one added here or from another agent, and Remove plugin for one of a plugin (item 497); the one that comes
 * with Dot has its switch, and Try again when it was left out (item 583); null when it has none. */
function serverActions(catalogue, server, service) {
  const working = server.state === "trying" || server.state === "signing_in";
  const actions = [];
  if (server.state === "signing_in" && server.link !== null) {
    actions.push(el("a", { class: "btn sm primary", href: server.link, target: "_blank", rel: "noopener noreferrer" }, icon("external"), t("views.connectors.openTheSignInPage")));
  }
  const again = server.origin === "connected" || server.origin === "builtin" || (server.origin === "config" && server.signs_in);
  const acting = catalogue.busy(`server:${server.name}`);
  if (again && !working && (server.signs_in || server.state === "left_out")) {
    const button = el("button", { class: "btn sm", type: "button", disabled: acting }, server.signs_in ? t("views.connectors.signInAgain") : t("views.connectors.tryAgain"));
    button.addEventListener("click", () => attempt(() => catalogue.connectAgain(server)));
    actions.push(button);
  }
  if (server.picks_tools && server.origin === "connected" && !working) {
    // The way back in for a server left out with its allow list; for one that runs, a way to cut its tools down.
    const kind = server.state === "left_out" ? "btn sm primary" : "btn sm";
    actions.push(el("button", { class: kind, type: "button", disabled: acting, onclick: () => attempt(() => catalogue.chooseTools(server)) }, t("views.connectors.chooseActions")));
  }
  if (server.origin === "connected" || server.origin === "elsewhere") {
    actions.push(el("button", { class: "btn sm", type: "button", disabled: acting, onclick: () => attempt(() => catalogue.remove(server, service)) }, t("views.connectors.remove")));
  }
  if (server.origin === "builtin") {
    // The server is on while it is not switched off: left out, it is still on, and Try again starts it once more.
    const on = server.state !== "disabled";
    const toggle = switchToggle({ label: t("views.connectors.useName", { name: server.name }), read: () => on, write: (next) => catalogue.switchServer(server, next), onToggled: () => undefined });
    toggle.setAttribute("aria-checked", String(on));
    toggle.disabled = acting || working;
    actions.push(toggle);
  }
  if (server.origin === "plugin" && server.plugin !== null) {
    actions.push(
      el("button", { class: "btn sm", type: "button", disabled: acting, title: t("views.connectors.takeThePluginPluginOut", { plugin: server.plugin }), onclick: () => attempt(() => catalogue.removePlugin(server)) }, t("views.connectors.removePlugin")),
    );
  }
  return actions.length === 0 ? null : el("div", { class: "plugin-actions" }, ...actions);
}

/** What to do or to know for 1Password (item 493), by where it stands: one line. */
function vaultGuide(vault) {
  if (!vault.connected) {
    return vault.cli === null
      ? el("span", null, t("views.connectors.the1passwordCommandLineTool"), el("details", { class: "error-details" }, el("summary", null, t("views.connectors.details")), el("p", { class: "small muted" }, t("views.connectors.inTerminal"), el("code", { class: "mono" }, "brew install 1password-cli"))))
      : vault.app === false
        ? t("views.connectors.1passwordSAppIsNot")
        : t("views.connectors.connectMakesTheVaultShared");
  }
  if (vault.error !== null) {
    return vault.error;
  }
  const names = vault.vaults.map((one) => one.name).join(", ");
  const logins = vault.vaults.reduce((sum, one) => sum + one.logins, 0);
  return t("views.connectors.readsPartInNamesThe", { part: tCount(logins, "views.connectors.countLogin", "views.connectors.countLogins"), names });
}

/** The buttons of 1Password's way (item 493): Connect 1Password and a token instead while it is not connected, the token
 * asked for in a dialog (item 615); Disconnect once it is. */
function vaultActions(catalogue, vault) {
  const acting = catalogue.busy("vault");
  if (vault.connected) {
    return el("div", { class: "plugin-actions" }, el("button", { class: "btn sm", type: "button", disabled: acting, onclick: () => attempt(() => catalogue.disconnectVault()) }, t("views.connectors.disconnect")));
  }
  const connect = el(
    "button",
    { class: "btn sm primary", type: "button", disabled: acting || vault.cli === null, onclick: () => attempt(() => catalogue.connectVault()) },
    acting ? t("views.connectors.waitingFor1password") : t("views.connectors.connect1password"),
  );
  // A token is read with op too: without it neither way connects.
  const token = el(
    "button",
    {
      class: "btn sm ghost",
      type: "button",
      "aria-haspopup": "dialog",
      dataset: { vaultToken: "" },
      disabled: acting || vault.cli === null,
      onclick: () => attempt(() => catalogue.askVault(() => document.querySelector("[data-vault-token]"))),
    },
    t("views.connectors.useAServiceAccountKey"),
  );
  return el("div", { class: "plugin-actions" }, connect, token);
}

/** One way of a service on its page: what it is, how it connects, where it stands, what to do and its step. */
function wayRow(catalogue, way) {
  const now = catalogue.wayNow(way);
  let badge = null;
  let guideLine = null;
  // For whoever edits the settings file, beside the note of a browser without a window (item 625).
  let guideTitle = null;
  let actions = null;
  if (way.kind === "mcp") {
    if (now.server === null && now.service !== null && now.service.added) {
      badge = el("span", { class: "badge b-muted" }, t("views.connectors.nameTaken"));
      guideLine = t("views.connectors.aConnectionSetUpIn", { ref: way.ref });
    } else if (now.server === null) {
      const service = now.service;
      const keyed = service !== null && service !== undefined && service.secrets.length > 0;
      const button = el("button", { class: "btn sm primary", type: "button", disabled: catalogue.busy(`mcp:${way.ref}`) }, t("views.connectors.add"));
      if (keyed) {
        button.setAttribute("aria-haspopup", "dialog");
      }
      const here = () => document.querySelector(`[data-way="mcp"][data-ref="${way.ref}"] .btn.primary`);
      button.addEventListener("click", () => attempt(() => (keyed ? catalogue.askSecrets(service, here) : catalogue.add({ service: way.ref, name: null, address: null, secrets: {} }))));
      // A service that takes a key asks for it here too (item 408): in a dialog, with its own Add (item 615).
      actions = el("div", { class: "plugin-actions" }, button);
      badge = el("span", { class: "badge b-muted" }, serviceWords(service));
    } else {
      badge = el("span", { class: `badge ${MCP_STATE_TONES[now.server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[now.server.state] ?? now.server.state);
      guideLine = serverGuide(now.server);
      actions = serverActions(catalogue, now.server, now.service);
    }
  } else if (way.kind === "vault") {
    if (now.vault !== null) {
      badge = catalogue.vaultBadge();
      guideLine = vaultGuide(now.vault);
      actions = vaultActions(catalogue, now.vault);
    }
  } else if (now.app !== null) {
    const app = now.app;
    const listing = catalogue.listing;
    badge = el("span", { class: `badge ${CONNECTED_STATES.has(app.state) ? "b-ok" : app.state === "refused" ? "b-bad" : "b-muted"}` }, STATE_WORDS[app.state] ?? app.state);
    guideLine = appGuide(app, listing.runner) ?? (way.kind === "web" && !CONNECTED_STATES.has(app.state) ? webNote(listing) : null);
    if (guideLine !== null && guideLine === webNote(listing) && listing.headless && listing.browser_mode !== "attach") {
      guideTitle = t("views.connectors.inTheSettingsFileBrowser");
    }
    if (app.step !== null) {
      // While macOS asks, the button says so, as Connect apps' did.
      const asking = catalogue.busy(`app:${app.id}`);
      const label = asking && app.step === "ask" ? t("views.connectors.waitingForMacos") : STEP_LABELS[app.step];
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
      guideLine === null ? null : el("div", { class: "row-text plugin-guide", ...(guideTitle === null ? {} : { title: guideTitle, "data-expert": "" }) }, guideLine),
      way.kind === "mcp" && now.server !== null ? el("div", { class: "row-text muted mono plugin-address" }, now.server.address) : null,
    ),
    actions,
  );
}

/**
 * What a service's own page shows of `plugin`, a listing of `catalogue`, under its bar: its mark, name, what Dot does
 * with it and where it stands; what each way lets Dot do, when its ways do more than that line; how it connects, each
 * way with its step; the tools of its servers; and where its data goes.
 */
function serviceParts(catalogue, plugin) {
  const mcpServers = plugin.ways
    .filter((way) => way.kind === "mcp")
    .map((way) => catalogue.wayNow(way).server)
    .filter((server) => server !== null);
  const tools = mcpServers.filter((server) => server.tools.length > 0).flatMap((server) => server.tools);
  const leftOut = mcpServers.flatMap((server) => server.left_out ?? []);
  const does = [...new Set(plugin.ways.map((way) => way.does).filter((line) => line !== null))];
  const data = plugin.ways.filter((way) => way.data !== null);
  const named = (way, text) => (data.length > 1 ? `${WAY_NAMES[way.kind]}: ${text}` : text);
  const card = (kind, title, mark, body) =>
    el("section", { class: "card connect-section plugin-card", dataset: { kind } }, el("div", { class: "card-head" }, icon(mark), el("span", { class: "grow" }, title)), el("div", null, body));
  const parts = [
    el(
      "header",
      { class: "plugin-hero", dataset: { plugin: plugin.id } },
      pluginMark(plugin),
      el("div", { class: "plugin-main" }, el("h2", { class: "plugin-title" }, plugin.name), el("div", { class: "plugin-does" }, plugin.does), el("div", { class: "plugin-meta" }, catalogue.howChip(plugin), catalogue.standing(plugin))),
    ),
    does.length < 2 ? null : card("does", t("views.connectors.whatDotDoesWithIt"), "sparkle", el("ul", { class: "plugin-list" }, does.map((line) => el("li", null, line)))),
    card("ways", t("views.connectors.howItConnects"), "link", plugin.ways.map((way) => wayRow(catalogue, way))),
    tools.length === 0 ? null : card("tools", t("views.connectors.actions"), "bolt", el("ul", { class: "plugin-list plugin-tools mono" }, tools.map((name) => el("li", null, name)))),
    leftOut.length === 0
      ? null
      : card(
          "left-out",
          t("views.connectors.notOffered"),
          "alert",
          el(
            "ul",
            { class: "plugin-list" },
            leftOut.map((item) => el("li", null, el("span", { class: "mono" }, item.name), `: ${item.reason}`)),
          ),
        ),
    data.length === 0 ? null : card("data", t("views.connectors.whereItsDataGoes"), "shield", el("ul", { class: "plugin-list" }, data.map((way) => el("li", null, named(way, way.data))))),
  ];
  return parts.filter((part) => part !== null);
}

export function mount(root, ctx) {
  const pluginId = ctx.params[1];
  let disposed = false;
  // The list is drawn once the Accounts card above it has read the accounts (item 511): the rows the card gets then
  // would push the tiles down under a finger on its way to one, and the tap would land between two. A service's page
  // has no card to wait for.
  let accountsRead = pluginId !== undefined;
  let shown = "all";
  let words = "";
  const holder = el("div", { class: "stack" }, loadingPanel());
  const catalogue = createCatalogue(paint);

  function tile(plugin) {
    const isConnected = catalogue.connected(plugin);
    const step = isConnected ? null : catalogue.firstStep(plugin);
    let action = null;
    if (step?.page) {
      // A service whose step is its page (1Password, item 493): its button leads there.
      action = el("a", { class: "btn sm plugin-add", href: `#/settings/connectors/${plugin.id}`, title: `${step.label}: ${plugin.name}`, "aria-label": `${step.label} ${plugin.name}` }, step.label);
    } else if (step !== null && step.link !== undefined) {
      action = el(
        "a",
        { class: "btn sm primary plugin-add", href: step.link, target: "_blank", rel: "noopener noreferrer", "aria-label": t("views.connectors.openTheSignInPage2", { name: plugin.name }) },
        icon("external"),
        step.label,
      );
    } else if (step !== null) {
      // The step in its words (item 545), as Connect apps' dialog had them: Add, Allow, Sign in.
      action = el(
        "button",
        {
          class: "btn sm plugin-add",
          type: "button",
          title: `${step.label}: ${plugin.name}`,
          "aria-label": `${step.label} ${plugin.name}`,
          // A service that takes a key asks for it in a dialog (item 615).
          "aria-haspopup": step.keyed ? "dialog" : null,
          disabled: catalogue.busy(step.key),
        },
        step.label,
      );
      action.addEventListener("click", () => attempt(step.take));
    }
    const waiting = plugin.ways.some((way) => way.kind === "mac" && catalogue.busy(`app:${way.ref}`));
    return el(
      "div",
      { class: "plugin-tile", dataset: { plugin: plugin.id, connected: String(isConnected) } },
      pluginMark(plugin),
      el(
        "div",
        { class: "plugin-main" },
        el("a", { class: "plugin-name", href: `#/settings/connectors/${plugin.id}` }, plugin.name),
        el("div", { class: "plugin-does" }, plugin.does),
        el("div", { class: "plugin-meta" }, catalogue.howChip(plugin), waiting ? el("span", { class: "badge b-warn" }, t("views.connectors.waitingForMacos")) : catalogue.standing(plugin)),
      ),
      action,
    );
  }

  /** The mark of a server added by hand: the mark of the service it is one of, by its address or its command, as the
   * service's tile wears it (items 408 and 477), else Dot's own icon (item 581). */
  function serverMark(server) {
    const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" }, markContent(server.mark));
    markTile(mark, server.mark);
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
        server.origin in MCP_ORIGIN_WORDS
          ? el("div", { class: "plugin-does", title: server.origin === "config" ? t("views.connectors.itIsSetUpIn") : null }, MCP_ORIGIN_WORDS[server.origin])
          : null,
        el(
          "div",
          { class: "plugin-meta" },
          el("span", { class: "chip" }, t("views.connectors.connectedApp")),
          server.origin === "builtin" ? el("span", { class: "chip" }, t("views.connectors.builtIn")) : null,
          el("span", { class: `badge ${MCP_STATE_TONES[server.state] ?? "b-muted"}` }, MCP_STATE_WORDS[server.state] ?? server.state),
        ),
        serverGuide(server) === null ? null : el("div", { class: "plugin-guide" }, serverGuide(server)),
      ),
      serverActions(catalogue, server, null),
    );
  }

  /** The form that adds an MCP server of one's own by its name and address, in a dialog (item 615): Dot tries the server
   * before it keeps it. */
  function addServer() {
    const name = el("input", { class: "input", type: "text", maxlength: "24", autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: "my-app", "aria-label": t("views.connectors.nameOfTheConnectedApp") });
    const address = el("input", {
      class: "input mono",
      type: "text",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      placeholder: "https://example.com/your-app",
      "aria-label": t("views.connectors.addressOrCommand"),
    });
    formDialog({
      title: t("views.connectors.addYourOwnConnectedApp"),
      description: t("views.connectors.dotTriesTheConnectionBefore"),
      body: [field(t("views.connectors.name"), name, t("views.connectors.lowercaseLettersDigitsAndDashes")), field(t("views.connectors.address"), address, t("views.connectors.itsWebAddressOrThe"))],
      submitLabel: t("views.connectors.add"),
      size: "narrow",
      returnFocus: () => document.querySelector("[data-add-server]"),
      onSubmit: () => catalogue.add({ service: null, name: name.value.trim(), address: address.value.trim(), secrets: {} }),
    });
  }

  function matches(plugin) {
    return words === "" || foldSearch(`${plugin.name} ${plugin.does}`).includes(words);
  }

  const search = el("input", { class: "input customize-search", type: "search", placeholder: t("views.connectors.searchConnectors"), "aria-label": t("views.connectors.searchConnectors"), autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const tabs = segmented(
    [
      { id: "connected", label: t("views.connectors.connected"), hint: t("views.connectors.theServicesDotIsConnected") },
      { id: "all", label: t("views.connectors.all"), hint: t("views.connectors.everyServiceDotWorksWith") },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  tabs.element.classList.add("plugin-tabs");
  tabs.element.setAttribute("aria-label", t("views.connectors.connectorsToShow"));
  const recheck = el("button", { class: "btn sm", type: "button", onclick: () => attempt(load) }, icon("refresh"), t("views.connectors.checkAgain"));
  // Out of what paints again, so that the search keeps the focus while the tiles are drawn anew; a service's page has
  // none of it.
  const head = customizeHead({
    line: t("views.connectors.dotWorksInTheApps"),
    search,
    controls: [tabs.element, recheck],
  });
  head.element.hidden = pluginId !== undefined;
  // The sections as chips (item 545, as Connect apps' dialog had them): one shows its section alone, All every one;
  // the page opens on Popular (item 1000).
  let chosen = POPULAR;
  const chipButtons = [{ id: "all", title: t("views.connectors.all") }, ...SECTIONS].map(({ id, title }) =>
    el(
      "button",
      {
        class: "chip customize-chip",
        type: "button",
        dataset: { chip: id },
        "aria-pressed": String(id === chosen),
        onclick: () => {
          chosen = id;
          for (const button of chipButtons) {
            button.setAttribute("aria-pressed", String(button.dataset.chip === chosen));
          }
          paint();
        },
      },
      title,
    ),
  );
  const chips = el("div", { class: "customize-chips", role: "group", "aria-label": t("views.connectors.sections"), hidden: pluginId !== undefined }, chipButtons);

  function paintTabs() {
    const [connectedButton, allButton] = tabs.element.children;
    const others = catalogue.servers.servers.filter((server) => server.service === null).length;
    connectedButton.textContent = t("views.connectors.connectedValue", { value: catalogue.plugins.filter(catalogue.connected).length + others });
    allButton.textContent = t("views.connectors.allLength", { length: catalogue.plugins.length });
  }

  // The page opens on its Popular chip (item 1000, the owner's ask of 2026-11-05 for a plain page: every section at
  // once was 43,000 px on a phone); All, another chip, a search (which looks in every section) or Connected lists the
  // rest.
  function browsing() {
    return chosen === POPULAR && words === "" && shown === "all";
  }

  /** Whether every section is listed: All, a search or Connected. */
  function everySection() {
    return chosen === "all" || words !== "" || shown === "connected";
  }

  function sections(list) {
    return SECTIONS.filter(({ id }) => everySection() || id === chosen).map(({ id, title }) => {
      const members = list.filter((plugin) => plugin.section === id);
      return members.length === 0
        ? null
        : el(
            "section",
            { class: "plugin-section", dataset: { section: id } },
            el("h2", { class: "section-label plugin-section-title", dataset: { count: String(members.length) } }, title),
            el("div", { class: "plugin-grid" }, members.map(tile)),
          );
    });
  }

  function paintList() {
    paintTabs();
    const showing = catalogue.plugins.filter((plugin) => (shown === "all" || catalogue.connected(plugin)) && matches(plugin));
    const others = catalogue.servers.servers.filter((server) => server.service === null && (words === "" || foldSearch(`${server.name} ${server.address}`).includes(words)));
    const parts = sections(showing);
    if (browsing()) {
      const rest = showing.filter((plugin) => plugin.section !== "popular").length;
      parts.push(el("p", { class: "hint connect-browse" }, t("views.connectors.partChooseAllOrA", { part: tCount(rest, "views.connectors.countMoreApp", "views.connectors.countMoreApps") })));
    }
    // The servers of no service belong to no section: Connected and a search, which look in every section, list them.
    if (others.length > 0 && (shown === "connected" || words !== "")) {
      parts.push(
        el(
          "section",
          { class: "plugin-section", dataset: { section: "servers" } },
          el("h2", { class: "section-label plugin-section-title" }, t("views.connectors.otherConnectedApps")),
          el("div", { class: "plugin-grid" }, others.map(serverTile)),
        ),
      );
    }
    // Nothing drawn (a search that found nothing, or Connected with nothing connected): the page says so rather than
    // stand empty. A search and Connected look in every section, whatever chip is pressed (item 1000).
    if (parts.every((part) => part === null)) {
      parts.push(
        words !== ""
          ? emptyPanel("search", t("views.connectors.noConnectorMatchesTheSearch"), "")
          : emptyPanel("plug", t("views.connectors.dotIsConnectedToNothing"), t("views.connectors.allListsWhatItWorks")),
      );
    }
    parts.push(
      el(
        "section",
        { class: "card connect-section plugin-own", dataset: { kind: "mcp" } },
        el(
          "div",
          { class: "card-head" },
          icon("plug"),
          el("span", { class: "grow" }, t("views.connectors.yourOwnConnectedApp")),
          el("button", { class: "btn sm", type: "button", "aria-haspopup": "dialog", dataset: { addServer: "" }, onclick: addServer }, icon("plus"), t("views.connectors.addConnectedApp")),
        ),
        el(
          "div",
          null,
          el("p", { class: "hint connect-hint" }, t("views.connectors.aConnectedAppGivesDot")),
          shown === "all" && words === "" ? others.map(serverTile) : null,
        ),
      ),
    );
    holder.replaceChildren(...parts.filter((part) => part !== null));
  }

  function paintPlugin(plugin) {
    ctx.setTitle(t("views.connectors.nameConnectors", { name: plugin.name }));
    const again = el("button", { class: "btn sm", type: "button", onclick: () => attempt(load) }, icon("refresh"), t("views.connectors.checkAgain"));
    holder.replaceChildren(el("div", { class: "plugin-page-bar" }, el("a", { class: "back", href: "#/settings/connectors" }, icon("arrowLeft"), t("views.connectors.connectors")), again), ...serviceParts(catalogue, plugin));
  }

  function paint() {
    if (disposed || catalogue.plugins === null || !accountsRead) {
      return;
    }
    if (pluginId === undefined) {
      paintList();
      return;
    }
    const plugin = catalogue.plugins.find((one) => one.id === pluginId);
    if (plugin === undefined) {
      holder.replaceChildren(el("a", { class: "back", href: "#/settings/connectors" }, icon("arrowLeft"), t("views.connectors.connectors")), errorPanel(new Error(`Dot lists no connector ${pluginId}.`)));
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

  // The official sign-ins of Google and Microsoft (item 422) head the list of the connectors, not a service's own page.
  const accounts = pluginId === undefined ? accountsCard() : null;
  accounts?.read.then(() => {
    accountsRead = true;
    paint();
  });
  root.append(head.element, ...(accounts === null ? [] : [accounts.element]), chips, holder);
  attempt(load);
  return () => {
    disposed = true;
    accounts?.dispose();
    catalogue.dispose();
  };
}
