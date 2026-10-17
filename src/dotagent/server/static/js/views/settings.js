// The settings. In a Mac's window, one page with the tabs down its left side; on a phone, a list of the tabs in groups
// whose rows open each tab as a page of its own, after T3 Code's (item 330). Each tab is drawn by the module that owns it.

import { api, query } from "../api.js";
import { el, modelName } from "../dom.js";
import { icon } from "../icons.js";
import { PHONE } from "../layout.js";
import { scrollCue } from "../scrollcue.js";
import { setStageTitle } from "../stage.js";
import { getSnapshot, onChange } from "../state.js";
import { findTheme, onSchemeChange, onThemeChange, shownScheme, themePicks } from "../theme.js";
import { attempt } from "../ui.js";
import * as appearance from "./appearance.js";
import * as keys from "./keys.js";
import * as memory from "./memory.js";
import * as phone from "./phone.js";
import * as rules from "./rules.js";
import * as schedules from "./schedules.js";
import { mountBrowser, mountGeneral, mountModels, mountSystem } from "./system.js";

const DEFAULT_TAB = "general";
const TABS = [
  { id: "general", label: "General", icon: "sliders", mount: mountGeneral },
  { id: "appearance", label: "Appearance", icon: "palette", mount: appearance.mount },
  { id: "models", label: "Models", icon: "layers", mount: mountModels },
  { id: "keys", label: "API keys", icon: "key", mount: keys.mount },
  { id: "browser", label: "Browser", icon: "globe", mount: mountBrowser },
  { id: "rules", label: "Rules", icon: "shield", mount: rules.mount },
  { id: "memory", label: "Memory", icon: "database", mount: memory.mount },
  { id: "schedules", label: "Schedules", icon: "calendar", mount: schedules.mount },
  { id: "phone", label: "Phone", icon: "smartphone", mount: phone.mount },
  { id: "system", label: "System", icon: "monitor", mount: mountSystem },
];
// The phone's list: the tabs in groups, each group's name over its rows.
const GROUPS = [
  { title: "Interface", tabs: ["general", "appearance"] },
  { title: "Models", tabs: ["models", "keys"] },
  { title: "How Dot works", tabs: ["rules", "memory", "schedules"] },
  { title: "This Mac", tabs: ["browser", "phone", "system"] },
];
const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

function tabLink(tab, current) {
  return el(
    "a",
    { class: "settings-tab", href: `#/settings/${tab.id}`, "aria-current": current ? "page" : null },
    icon(tab.icon),
    el("span", null, tab.label),
  );
}

/** A row of the phone's list: the tab's mark and name, `value` (what the tab holds, or nothing) and an arrow. */
function listRow(tab, value) {
  return el(
    "a",
    { class: "settings-row", href: `#/settings/${tab.id}` },
    icon(tab.icon),
    el("span", { class: "settings-row-label" }, tab.label),
    value,
    icon("chevron"),
  );
}

/** The phone's list of the settings. Appearance tells the theme on screen, Models the main model, Memory how many notes
 * Dot keeps and Schedules how many schedules there are. */
function mountList(root, ctx) {
  ctx.setTitle("Settings");
  const values = new Map(TABS.map((tab) => [tab.id, el("span", { class: "settings-row-value" })]));
  const rows = (group) => group.tabs.map((id) => listRow(TABS.find((tab) => tab.id === id), values.get(id)));
  root.append(
    el(
      "nav",
      { class: "settings-list", "aria-label": "Settings" },
      GROUPS.map((group) =>
        el("section", { class: "settings-group" }, el("h2", { class: "settings-group-title" }, group.title), el("div", { class: "card" }, rows(group))),
      ),
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
    const found = await api.get("/schedules");
    values.get("schedules").textContent = String(found.length);
  });
  return () => {
    SYSTEM_DARK.removeEventListener("change", paintTheme);
    for (const stop of stops) {
      stop();
    }
  };
}

/** Draw the settings of the route. On a phone `#/settings` is the list of the tabs; in a wider window it leads to
 * General, and so does an unknown tab. `#/settings/<tab>` draws that tab. */
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
  const pane = el("div", { class: "settings-pane" });
  const links = TABS.map((candidate) => tabLink(candidate, candidate.id === tab.id));
  const nav = el("nav", { class: "settings-nav", "aria-label": "Settings" }, links);
  // On a phone the rail is hidden (settings.css): the bar's Back at the top left leads to the list (item 338).
  root.append(el("div", { class: "settings-frame" }, el("div", { class: "settings" }, el("div", { class: "settings-side" }, nav), pane)));
  // In the row that scrolls sideways (a narrow window, item 272) the open tab is brought to its middle, and the row's
  // edge fades while more tabs lie to the right. A row drawn before the webfont came in was measured in the fallback
  // face, which is narrower, so it is centred again once the fonts are in, unless it was moved meanwhile (item 328).
  const centre = () => {
    const open = links[TABS.indexOf(tab)].getBoundingClientRect();
    const row = nav.getBoundingClientRect();
    nav.scrollLeft += open.left - row.left - (row.width - open.width) / 2;
  };
  centre();
  const centred = nav.scrollLeft;
  document.fonts.ready.then(() => {
    if (nav.isConnected && nav.scrollLeft === centred) {
      centre();
    }
  });
  const stopCue = scrollCue(nav);
  const dispose = tab.mount(pane, ctx);
  // The bar names the page and the open tab names the pane; on a phone, whose page is the tab alone, the bar names the
  // tab.
  const paintTitle = () => setStageTitle(el("h1", { class: "stage-h1" }, PHONE.matches ? tab.label : "Settings"));
  paintTitle();
  PHONE.addEventListener("change", paintTitle);
  return () => {
    PHONE.removeEventListener("change", paintTitle);
    stopCue();
    dispose();
  };
}
