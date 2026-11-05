// The settings. In a Mac's window the sidebar is their nav (settingsnav.js, item 562) and the page is the open tab alone: its
// section's name, the row of its tabs when it has more than one (item 845), a line that says what the tab is for and what
// it holds, in a column of its own; on a phone, one sheet of rows in groups, a row a tab that opens as a page of its own
// (items 330 and 995). Each tab is drawn by the module that owns it.

import { api, query } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { PHONE } from "../layout.js";
import { settingsHead } from "../panels.js";
import { DEFAULT_TAB, PHONE_GROUPS, sectionOf, TABS } from "../settingstabs.js";
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
import * as trading from "./trading.js";
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
  trading: trading.mount,
  rules: rules.mount,
  memory: memory.mount,
  harness: harness.mount,
  repair: repair.mount,
  phone: phone.mount,
  channels: channels.mount,
  snapshots: snapshots.mount,
  system: mountSystem,
};
const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

/** A row of the phone's sheet (item 995): its mark, its name over the line that says what it holds, `value` (what it
 * holds, or nothing) and an arrow; it opens its tab, or the page it names. */
function listRow(row, value) {
  const tab = row.tab === undefined ? null : TABS.find((candidate) => candidate.id === row.tab);
  const label = tab === null ? row.label : tab.label;
  return el(
    "a",
    { class: "settings-row", href: tab === null ? row.href : `#/settings/${tab.id}` },
    icon(tab === null ? row.icon : tab.icon),
    el("span", { class: "settings-row-words" }, el("span", { class: "settings-row-label" }, label), el("span", { class: "settings-row-line" }, row.line)),
    value ?? null,
    icon("chevron"),
  );
}

/** The row of a section's tabs over the open one (item 845), none for a section of one tab: links, the open one marked. */
function partsRow(section, open) {
  if (section === null || section.tabs.length < 2) {
    return null;
  }
  return el(
    "nav",
    { class: "segmented settings-parts", "aria-label": section.label },
    section.tabs.map((id) => {
      const tab = TABS.find((candidate) => candidate.id === id);
      return el("a", { class: "settings-part", href: `#/settings/${id}`, "aria-current": id === open ? "page" : null }, tab.label);
    }),
  );
}

/** The phone's sheet of the settings (item 995): the first steps while one is left, then the groups of rows.
 * Appearance tells the theme on screen, Memory how many notes Dot keeps and Vault how many entries it holds. */
function mountList(root, ctx) {
  ctx.setTitle("Settings");
  const values = new Map(TABS.map((tab) => [tab.id, el("span", { class: "settings-row-value" })]));
  const groups = PHONE_GROUPS.map((group) =>
    el(
      "section",
      { class: "settings-group", "aria-label": group.title ?? null },
      group.title === null ? null : el("h2", { class: "settings-group-title" }, group.title),
      el("div", { class: "card" }, group.rows.map((row) => listRow(row, row.value === undefined ? null : values.get(row.value)))),
    ),
  );
  const sheet = el("nav", { class: "settings-list", "aria-label": "Settings" }, ...groups);
  root.append(sheet);
  // What a Dot that started afresh has to set up (item 811) heads the sheet while a step is left.
  const steps = buildFirstSteps((card) => sheet.before(card));
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
  return () => {
    steps.dispose();
    SYSTEM_DARK.removeEventListener("change", paintTheme);
    for (const stop of stops) {
      stop();
    }
  };
}

/** Draw the settings of the route. On a phone `#/settings` is the sheet of the tabs (item 995); in a wider window it leads to
 * General, and so does an unknown tab. `#/settings/<tab>` draws that tab, under the head of its section (its name, and
 * the row of its tabs): the tab is given `ctx.head`, to say what it is for (`setLead`), what it holds (`setMeta`) and the
 * buttons that belong to the whole page (`setActions`). */
export function mount(root, ctx) {
  if (ctx.params[0] === undefined && PHONE.matches) {
    return mountList(root, ctx);
  }
  const tab = TABS.find((candidate) => candidate.id === ctx.params[0]);
  if (tab === undefined) {
    location.replace(`${location.pathname}${location.search}#/settings/${DEFAULT_TAB}`);
    return () => undefined;
  }
  ctx.setTitle(`${tab.label} · Settings`);
  // Trading stands in no section (the Trader agent's, item 847): its page is named for itself.
  const section = sectionOf(tab.id);
  const name = section === null ? tab.label : section.label;
  const head = settingsHead(name);
  const parts = partsRow(section, tab.id);
  if (parts !== null) {
    head.element.querySelector(".set-title")?.after(parts);
  }
  const pane = el("div", { class: "settings-pane" }, head.element);
  root.append(el("div", { class: "settings-frame" }, pane));
  const dispose = MOUNTS[tab.id](pane, { ...ctx, head });
  // The page names itself in its head; on a phone, whose page is the tab alone, the bar names the tab. The bar's name is
  // always there and the width alone shows one or the other (settings.css), as the page's own name is hidden: two
  // choices made apart, one by the script as the page was drawn and one by the style, showed both a moment after a
  // phone turned (item 966).
  // On a phone the page is the tab its row named (item 995): the bar names the tab, not its section.
  setStageTitle(el("h1", { class: "stage-h1 set-phone-title" }, tab.label));
  return dispose;
}
