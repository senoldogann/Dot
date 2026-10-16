// The settings: one page with the tabs down its left side; each tab is drawn by the module that owns it.

import { el } from "../dom.js";
import { setStageTitle } from "../stage.js";
import { icon } from "../icons.js";
import { scrollCue } from "../scrollcue.js";
import * as appearance from "./appearance.js";
import * as keys from "./keys.js";
import * as memory from "./memory.js";
import * as phone from "./phone.js";
import * as rules from "./rules.js";
import * as schedules from "./schedules.js";
import * as skills from "./skills.js";
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
  { id: "skills", label: "Skills", icon: "sparkle", mount: skills.mount },
  { id: "schedules", label: "Schedules", icon: "calendar", mount: schedules.mount },
  { id: "phone", label: "Phone", icon: "smartphone", mount: phone.mount },
  { id: "system", label: "System", icon: "monitor", mount: mountSystem },
];

function tabLink(tab, current) {
  return el(
    "a",
    { class: "settings-tab", href: `#/settings/${tab.id}`, "aria-current": current ? "page" : null },
    icon(tab.icon),
    el("span", null, tab.label),
  );
}

/** Draw the tab named by the route (`#/settings/<tab>`), or General; an unknown name leads to General. */
export function mount(root, ctx) {
  const wanted = ctx.params[0] === undefined ? DEFAULT_TAB : ctx.params[0];
  const tab = TABS.find((candidate) => candidate.id === wanted);
  if (tab === undefined) {
    location.replace(`${location.pathname}${location.search}#/settings/${DEFAULT_TAB}`);
    return () => undefined;
  }
  ctx.setTitle(`${tab.label} · Settings`);
  const pane = el("div", { class: "settings-pane" });
  const links = TABS.map((candidate) => tabLink(candidate, candidate.id === tab.id));
  const nav = el("nav", { class: "settings-nav", "aria-label": "Settings" }, links);
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
  // The bar names the page; the open tab names the pane.
  setStageTitle(el("h1", { class: "stage-h1" }, "Settings"));
  return () => {
    stopCue();
    dispose();
  };
}
