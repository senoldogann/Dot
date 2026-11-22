// The settings. In a Mac's window the sidebar is their nav (settingsnav.js, item 562: a search and the groups of rows, items
// 999 and 1336) and the page is the open page alone: its place in the bar as a breadcrumb ("Settings / General"), a line
// that says what it is for and what it holds, then its sections, each a name over a card of rows; on a phone, one sheet of
// the same rows, a row a page that opens on its own (items 330 and 995). Each page is drawn by the module that owns it: the
// first ones come with this module, the others (Scheduled tasks, Connectors, Plugins, Skills, Agents, Campaigns,
// Diagnostics, Open source licences) are loaded when their page is asked for.

import { api, query } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { PHONE } from "../layout.js";
import { hostPageHeads, settingsHead } from "../panels.js";
import { DEFAULT_TAB, matchesSearch, SETTINGS_GROUPS, settingsHref, tabOf } from "../settingstabs.js";
import { setStageTitle } from "../stage.js";
import { findTheme, onSchemeChange, onThemeChange, shownScheme, themePicks } from "../theme.js";
import { attempt } from "../ui.js";
import { buildFirstSteps } from "../firststeps.js";
import * as appearance from "./appearance.js";
import * as channels from "./channels.js";
import * as harness from "./harness.js";
import * as keys from "./keys.js";
import * as memory from "./memory.js";
import * as phone from "./phone.js";
import * as repair from "./repair.js";
import * as rules from "./rules.js";
import * as snapshots from "./snapshots.js";
import * as websearch from "./websearch.js";
import * as vault from "./vault.js";
import { mountBrowser, mountGeneral, mountModels, mountSystem } from "./system.js";

const MOUNTS = {
  general: mountGeneral,
  appearance: appearance.mount,
  models: mountModels,
  keys: keys.mount,
  websearch: websearch.mount,
  browser: mountBrowser,
  vault: vault.mount,
  rules: rules.mount,
  memory: memory.mount,
  harness: harness.mount,
  repair: repair.mount,
  phone: phone.mount,
  channels: channels.mount,
  snapshots: snapshots.mount,
  system: mountSystem,
};
// The pages whose module is loaded when the page is asked for.
// `params` is what the module reads of its address: `[page, id]` for those that take an id after the page, `[id]` for
// the two that were pages of their own (`#/agents`, `#/campaigns/<id>`).
const LOADED = {
  schedules: { load: () => import("./schedules.js") },
  connectors: { load: () => import("./connectors.js") },
  plugins: { load: () => import("./plugins.js") },
  skills: { load: () => import("./skills.js") },
  agents: { load: () => import("./agents.js"), params: ([, id]) => [id] },
  campaigns: { load: () => import("./campaigns.js"), params: ([, id]) => [id] },
  diagnostics: { load: () => import("./diagnostics.js") },
  licences: { load: () => import("./licences.js") },
};
const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

/** A row of the phone's sheet (item 995): its mark, its name over the line that says what it holds, `value` (what it
 * holds, or nothing) and an arrow; it opens its page. */
function listRow(row, value) {
  const tab = tabOf(row.tab);
  return el(
    "a",
    { class: "settings-row", href: settingsHref(tab.id) },
    icon(tab.icon),
    el("span", { class: "settings-row-words" }, el("span", { class: "settings-row-label" }, tab.label), el("span", { class: "settings-row-line" }, row.line)),
    value ?? null,
    icon("chevron"),
  );
}

/** The phone's sheet of the settings (item 995): a search, the groups of rows, then the first steps while one is left.
 * Appearance tells the theme on screen, Memory how many notes Dot keeps, Vault how many entries it holds and Scheduled
 * tasks how many are on. */
function mountList(root, ctx) {
  ctx.setTitle("Settings");
  const values = new Map(["appearance", "memory", "vault", "schedules"].map((id) => [id, el("span", { class: "settings-row-value" })]));
  const entries = [];
  const groups = SETTINGS_GROUPS.map((group) => {
    const rows = group.rows.map((row) => {
      const link = listRow(row, row.value === undefined ? null : values.get(row.value));
      entries.push({ tab: tabOf(row.tab), line: row.line, link });
      return link;
    });
    const section = el(
      "section",
      { class: "settings-group", "aria-label": group.title ?? null },
      group.title === null ? null : el("h2", { class: "settings-group-title" }, group.title),
      el("div", { class: "card" }, rows),
    );
    return { section, rows };
  });
  const search = el("input", { class: "snav-input", type: "search", autocomplete: "off", spellcheck: "false", placeholder: "Search settings", "aria-label": "Search settings" });
  const empty = el("p", { class: "snav-empty", hidden: true }, "No setting matches.");
  search.addEventListener("input", () => {
    const typed = search.value.trim();
    let shown = 0;
    for (const entry of entries) {
      entry.link.hidden = typed !== "" && !matchesSearch(entry.tab, entry.line, typed);
      shown += entry.link.hidden ? 0 : 1;
    }
    for (const group of groups) {
      group.section.hidden = group.rows.every((link) => link.hidden);
    }
    empty.hidden = shown !== 0;
  });
  const sheet = el("nav", { class: "settings-list", "aria-label": "Settings" }, el("label", { class: "snav-search" }, icon("search"), search), ...groups.map((group) => group.section), empty);
  root.append(sheet);
  // What a Dot that started afresh has to set up (item 811) stands under the sheet while a step is left: it comes once
  // the daemon answers, and over the rows it pushed them down under a finger on its way to one (item 996, main's CI).
  const steps = buildFirstSteps((card) => sheet.after(card));
  setStageTitle(el("h1", { class: "stage-h1" }, "Settings"));

  function paintTheme() {
    values.get("appearance").textContent = findTheme(themePicks()[shownScheme()]).label;
  }
  paintTheme();
  const stops = [onThemeChange(paintTheme), onSchemeChange(paintTheme)];
  SYSTEM_DARK.addEventListener("change", paintTheme);
  attempt(async () => {
    const notes = await api.get(`/memories${query({ limit: 1, offset: 0 })}`);
    values.get("memory").textContent = String(notes.total);
  });
  attempt(async () => {
    const counts = await api.get("/vault/counts");
    const held = counts.login + counts.card + counts.contact;
    values.get("vault").textContent = held === 0 ? "" : String(held);
  });
  attempt(async () => {
    const schedules = await api.get("/schedules");
    const on = schedules.filter((view) => view.record.enabled).length;
    values.get("schedules").textContent = on === 0 ? "" : String(on);
  });
  return () => {
    steps.dispose();
    SYSTEM_DARK.removeEventListener("change", paintTheme);
    for (const stop of stops) {
      stop();
    }
  };
}

/**
 * The page's place in the bar (item 1336): "Settings / <page>", and a third part (`setDetail`) for a page of the page, a
 * schedule's editor. The parts before the last lead back. A phone's bar has the last part alone: its Back goes up.
 */
function breadcrumb(tab) {
  // The nav of the parts (`aria-label` Breadcrumb) holds the page's own name as its h1, the last part.
  const element = el("nav", { class: "crumbs", "aria-label": "Breadcrumb" });
  let detail = "";
  const sep = () => el("span", { class: "crumb-sep crumb-far", "aria-hidden": "true" }, "/");
  const here = (text) => el("h1", { class: "crumb crumb-here" }, text);
  function paint() {
    element.replaceChildren(
      el("a", { class: "crumb crumb-far", href: "#/settings" }, "Settings"),
      sep(),
      ...(detail === "" ? [here(tab.label)] : [el("a", { class: "crumb crumb-far", href: settingsHref(tab.id) }, tab.label), sep(), here(detail)]),
    );
  }
  paint();
  return {
    element,
    setDetail(text) {
      detail = text;
      paint();
    },
  };
}

/** Draw the settings of the route. On a phone `#/settings` is the sheet of the pages (item 995); in a wider window it leads
 * to General, and so does an unknown page. `#/settings/<page>` draws that page: the page is given `ctx.head`, to say what it is for (`setLead`),
 * what it holds (`setMeta`) and the buttons that belong to the whole page (`setActions`), and `ctx.setCrumb(text)`, which names a page of the page
 * in the bar. */
export function mount(root, ctx) {
  if (ctx.params[0] === undefined && PHONE.matches) {
    return mountList(root, ctx);
  }
  const tab = tabOf(ctx.params[0]);
  if (tab === undefined) {
    location.replace(`${location.pathname}${location.search}#/settings/${DEFAULT_TAB}`);
    return () => undefined;
  }
  ctx.setTitle(`${tab.label} · Settings`);
  const head = settingsHead();
  const pane = el("div", { class: "settings-pane", dataset: { page: tab.id } }, head.element);
  root.append(el("div", { class: "settings-frame" }, pane));
  // An agent's own page is its conversation, which is not a page of the settings (item 1233).
  if (tab.id === "agents" && ctx.params[1] !== undefined) {
    location.replace(`${location.pathname}${location.search}#/agents/${ctx.params[1]}`);
    return () => undefined;
  }
  const crumbs = breadcrumb(tab);
  setStageTitle(crumbs.element);
  const pageCtx = {
    ...ctx,
    head,
    setCrumb: (text) => {
      crumbs.setDetail(text);
      setStageTitle(crumbs.element);
    },
  };
  const own = MOUNTS[tab.id];
  if (own !== undefined) {
    return own(pane, pageCtx);
  }
  // A page loaded when asked for: its module draws into the pane once it is here, and the page it leaves stops it.
  let gone = false;
  let dispose = () => undefined;
  // A page that heads itself with `pageHead` has its title in the breadcrumb and its line under it.
  const letGo = hostPageHeads({
    say: (title, subtitle) => {
      pageCtx.setCrumb(title === tab.label ? "" : title);
      head.setLead(subtitle);
    },
  });
  attempt(async () => {
    const { load, params = (found) => found } = LOADED[tab.id];
    const module = await load();
    if (!gone) {
      dispose = module.mount(pane, { ...pageCtx, params: params(ctx.params) }) ?? dispose;
    }
  });
  return () => {
    gone = true;
    letGo();
    dispose();
  };
}
