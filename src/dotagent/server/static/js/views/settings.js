// The settings. In a Mac's window the sidebar is their nav (settingsnav.js, item 562) and the page is the open tab alone: its
// section's name, the row of its tabs when it has more than one (item 845), a line that says what the tab is for and what
// it holds, in a column of its own; on a phone, a list of the seven sections whose rows open each as a page of its own
// (item 330). Each tab is drawn by the module that owns it.

import { api, query } from "../api.js";
import { el, modelName } from "../dom.js";
import { icon } from "../icons.js";
import { PHONE } from "../layout.js";
import { settingsHead } from "../panels.js";
import { DEFAULT_TAB, SECTIONS, sectionOf, TABS } from "../settingstabs.js";
import { setStageTitle } from "../stage.js";
import { getSnapshot, onChange } from "../state.js";
import { findTheme, onSchemeChange, onThemeChange, shownScheme, themePicks } from "../theme.js";
import { attempt } from "../ui.js";
import * as appearance from "./appearance.js";
import * as channels from "./channels.js";
import * as harness from "./harness.js";
import * as keys from "./keys.js";
import * as memory from "./memory.js";
import * as phone from "./phone.js";
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
  phone: phone.mount,
  channels: channels.mount,
  snapshots: snapshots.mount,
  system: mountSystem,
};
const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

/** A row of the phone's list: the section's mark and name, `value` (what it holds, or nothing) and an arrow; it opens the
 * section's first tab. */
function listRow(section, value) {
  return el(
    "a",
    { class: "settings-row", href: `#/settings/${section.tabs[0]}` },
    icon(section.icon),
    el("span", { class: "settings-row-label" }, section.label),
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

/** The phone's list of the settings' sections. Appearance tells the theme on screen, Models the main model, Rules and
 * memory how many notes Dot keeps and Browser and Vault how many entries the Vault holds. */
function mountList(root, ctx) {
  ctx.setTitle("Settings");
  const values = new Map(TABS.map((tab) => [tab.id, el("span", { class: "settings-row-value" })]));
  root.append(
    el(
      "nav",
      { class: "settings-list", "aria-label": "Settings" },
      el("section", { class: "settings-group" }, el("div", { class: "card" }, SECTIONS.map((section) => listRow(section, section.value === null ? null : values.get(section.value))))),
    ),
  );
  setStageTitle(el("h1", { class: "stage-h1" }, "Settings"));

  function paintTheme() {
    values.get("appearance").textContent = findTheme(themePicks()[shownScheme()]).label;
  }
  function paintModel(snapshot) {
    values.get("models").textContent = snapshot.status === null ? "" : modelName(snapshot.status.models.main);
  }
  paintTheme();
  paintModel(getSnapshot());
  const stops = [onThemeChange(paintTheme), onSchemeChange(paintTheme), onChange(paintModel)];
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
    SYSTEM_DARK.removeEventListener("change", paintTheme);
    for (const stop of stops) {
      stop();
    }
  };
}

/** Draw the settings of the route. On a phone `#/settings` is the list of the sections; in a wider window it leads to
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
  // The page names itself in its head; on a phone, whose page is the tab alone, the bar names the tab.
  const paintTitle = () => setStageTitle(PHONE.matches ? el("h1", { class: "stage-h1" }, name) : null);
  paintTitle();
  PHONE.addEventListener("change", paintTitle);
  return () => {
    PHONE.removeEventListener("change", paintTitle);
    dispose();
  };
}
