// Connect apps (item 492): the services Dot works with in a dialog over the page, opened from the foot of the sidebar.
// A search, the sections as chips, Suggested for you (the popular services) and each section's first tiles with See all;
// each tile shows one step (Add, Sign in, Allow…) or where the service stands once it is connected, and opens the
// service's own page in the dialog, as Customize › Plugins shows it, with a way back. "Connected" lists what Dot is
// connected to. The services, where they stand and their steps are catalogue.js's, shared with Customize › Plugins,
// which keeps what the dialog leaves out: the accounts, the MCP servers of one's own and the skills.

import { createCatalogue, pluginMark } from "./catalogue.js";
import { el, foldSearch } from "./dom.js";
import { icon } from "./icons.js";
import { emptyPanel, errorPanel, loadingPanel } from "./panels.js";
import { attempt } from "./ui.js";
import { SECTIONS, serviceParts } from "./views/plugins.js";

// A section shows this many tiles under All; See all shows the rest.
const SECTION_TILES = 6;
// The popular services lead the dialog under this title.
const SUGGESTED = "Suggested for you";
const ALL = "all";
const CONNECTED = "connected";

/**
 * Open the Connect apps dialog over the page. `onClose()` runs once it has closed, when what is connected may have
 * changed. Returns `close()`.
 */
export function openConnectApps({ onClose }) {
  // What the dialog shows: the tiles of a chip (`all`, `connected` or a section's id), or a service's page (`service`).
  let chip = ALL;
  let service = null;
  let words = "";
  // Where the tiles were scrolled to when a service's page opened, and the service, for the way back.
  let listScroll = 0;
  let cameFrom = null;
  let failed = null;
  const catalogue = createCatalogue(paint);

  const back = el("button", { class: "icon-btn connect-apps-back", type: "button", title: "Back", "aria-label": "Back to the apps", hidden: true, onclick: () => showList() }, icon("chevronLeft"));
  const title = el("h2", { class: "connect-apps-title", id: "connect-apps-title" }, "Connect apps");
  const connectedButton = el("button", { class: "btn sm ghost connect-apps-connected", type: "button", onclick: () => choose(CONNECTED) });
  const close = el("button", { class: "icon-btn connect-apps-close", type: "button", title: "Close", "aria-label": "Close Connect apps", onclick: () => dialog.close() }, icon("x"));
  const search = el("input", {
    class: "input connect-apps-search",
    type: "search",
    placeholder: "Search apps",
    "aria-label": "Search apps",
    autocomplete: "off",
    spellcheck: "false",
  });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const customize = el("a", { class: "btn sm connect-apps-customize", href: "#/customize/plugins", title: "Your own MCP servers, the accounts and the skills", onclick: () => dialog.close() }, "Customize", icon("external"));
  const chips = [{ id: ALL, title: "All" }, ...SECTIONS.filter((section) => section.id !== "popular")].map(({ id, title: label }) =>
    el("button", { class: "chip connect-apps-chip", type: "button", dataset: { chip: id }, "aria-pressed": String(id === chip), onclick: () => choose(id) }, label),
  );
  const chipRow = el("div", { class: "connect-apps-chips", role: "group", "aria-label": "Sections" }, ...chips);
  const tools = el("div", { class: "connect-apps-tools" }, el("label", { class: "connect-apps-find" }, icon("search"), search), customize);
  const body = el("div", { class: "connect-apps-body" }, loadingPanel());
  const dialog = el(
    "dialog",
    { class: "modal connect-apps", "aria-labelledby": "connect-apps-title" },
    el("div", { class: "connect-apps-head" }, back, title, connectedButton, close),
    tools,
    chipRow,
    body,
  );

  function choose(id) {
    chip = id;
    service = null;
    body.scrollTop = 0;
    paint();
  }

  function showList() {
    const left = cameFrom;
    service = null;
    cameFrom = null;
    paint();
    body.scrollTop = listScroll;
    const tile = left === null ? null : body.querySelector(`.connect-tile[data-plugin="${left}"] .connect-tile-open`);
    (tile ?? search).focus({ preventScroll: true });
  }

  function showService(plugin) {
    listScroll = body.scrollTop;
    cameFrom = plugin.id;
    service = plugin.id;
    paint();
    body.scrollTop = 0;
    back.focus({ preventScroll: true });
  }

  /** The one thing a tile offers: its first step as a button, the sign-in page an MCP server waits on, a key's form on
   * the service's page for a service that takes one; else where it stands, or that it was not tried yet. */
  function tileAction(plugin) {
    if (catalogue.connected(plugin)) {
      return catalogue.standing(plugin);
    }
    if (plugin.ways.some((way) => way.kind === "mac" && catalogue.busy(`app:${way.ref}`))) {
      return el("span", { class: "badge b-warn" }, "Waiting for macOS…");
    }
    const step = catalogue.firstStep(plugin);
    if (step === null) {
      return plugin.tried ? catalogue.standing(plugin) : el("span", { class: "chip plugin-untried" }, "Not tried yet");
    }
    if (step.link !== undefined) {
      return el("a", { class: "btn sm connect-tile-step", href: step.link, target: "_blank", rel: "noopener noreferrer", "aria-label": `Open the sign-in page of ${plugin.name}` }, step.label);
    }
    const button = el("button", { class: "btn sm connect-tile-step", type: "button", "aria-label": `${step.label} ${plugin.name}`, disabled: catalogue.busy(step.key) }, step.label);
    button.addEventListener("click", () =>
      attempt(async () => {
        if (step.keyed) {
          // The key is asked for on the service's page, beside what it is for and where to get one.
          showService(plugin);
        }
        await step.take();
      }),
    );
    return button;
  }

  function tile(plugin) {
    return el(
      "div",
      { class: "connect-tile", dataset: { plugin: plugin.id, connected: String(catalogue.connected(plugin)) } },
      pluginMark(plugin, catalogue.appIcon(plugin)),
      el(
        "button",
        { class: "connect-tile-open", type: "button", "aria-label": `${plugin.name}: ${plugin.does}`, onclick: () => showService(plugin) },
        el("span", { class: "connect-tile-name" }, plugin.name),
        el("span", { class: "connect-tile-does" }, plugin.does),
      ),
      tileAction(plugin),
    );
  }

  /** A section of tiles: its title, See all when it holds more than are shown, and its tiles. */
  function section(id, heading, members, limit) {
    const shown = limit === null ? members : members.slice(0, limit);
    const more =
      limit !== null && members.length > limit
        ? el("button", { class: "btn sm ghost connect-apps-more", type: "button", "aria-label": `See all of ${heading}`, onclick: () => choose(id) }, "See all")
        : null;
    return el(
      "section",
      { class: "connect-apps-section", dataset: { section: id } },
      el("div", { class: "connect-apps-section-head" }, el("h3", { class: "connect-apps-section-title" }, heading), more),
      el("div", { class: "connect-apps-grid" }, shown.map(tile)),
    );
  }

  function matches(plugin) {
    return words === "" || foldSearch(`${plugin.name} ${plugin.does}`).includes(words);
  }

  function paintTiles() {
    const plugins = catalogue.plugins.filter(matches);
    let parts;
    if (chip === CONNECTED) {
      const linked = plugins.filter(catalogue.connected);
      parts = linked.length === 0 ? [] : [section(CONNECTED, "Connected", linked, null)];
    } else if (chip !== ALL) {
      const { title: heading } = SECTIONS.find((one) => one.id === chip);
      const members = plugins.filter((plugin) => plugin.section === chip);
      parts = members.length === 0 ? [] : [section(chip, heading, members, null)];
    } else {
      // A search shows every service it finds; at rest each section shows its first tiles.
      const limit = words === "" ? SECTION_TILES : null;
      parts = SECTIONS.map(({ id, title: heading }) => {
        const members = plugins.filter((plugin) => plugin.section === id);
        if (members.length === 0) {
          return null;
        }
        return id === "popular" ? section(id, SUGGESTED, members, null) : section(id, heading, members, limit);
      }).filter((part) => part !== null);
    }
    if (parts.length === 0) {
      parts = [
        words !== ""
          ? emptyPanel("search", "No app matches the search", "")
          : chip === CONNECTED
            ? emptyPanel("plug", "Dot is connected to nothing yet", "Add a service, or sign in to one, and it shows here.")
            : emptyPanel("plug", "No app here yet", ""),
      ];
    }
    body.replaceChildren(...parts);
  }

  function paint() {
    const count = catalogue.plugins === null ? 0 : catalogue.plugins.filter(catalogue.connected).length;
    connectedButton.replaceChildren(el("span", null, `${count} connected`), icon("chevron"));
    connectedButton.setAttribute("aria-pressed", String(chip === CONNECTED && service === null));
    for (const button of chips) {
      button.setAttribute("aria-pressed", String(service === null && button.dataset.chip === chip));
    }
    const plugin = service === null || catalogue.plugins === null ? null : catalogue.plugins.find((one) => one.id === service);
    dialog.dataset.view = plugin === null ? "list" : "service";
    back.hidden = plugin === null;
    tools.hidden = plugin !== null;
    chipRow.hidden = plugin !== null;
    if (failed !== null) {
      body.replaceChildren(errorPanel(failed, () => attempt(load)));
      return;
    }
    if (catalogue.plugins === null) {
      return;
    }
    if (plugin !== null) {
      body.replaceChildren(el("div", { class: "stack connect-apps-service" }, ...serviceParts(catalogue, plugin)));
      return;
    }
    paintTiles();
  }

  async function load() {
    failed = null;
    try {
      await catalogue.load();
    } catch (error) {
      failed = error;
      paint();
    }
  }

  dialog.addEventListener("close", () => {
    catalogue.dispose();
    dialog.remove();
    onClose();
  });
  document.body.append(dialog);
  dialog.showModal();
  search.focus();
  paint();
  attempt(load);
  return { close: () => dialog.close() };
}
