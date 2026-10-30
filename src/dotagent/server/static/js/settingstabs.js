// The tabs of the settings and the sections they stand in (items 562 and 845). The settings page draws the open one, the
// settings nav in the sidebar (settingsnav.js) and the phone's list (views/settings.js) name the sections.

export const DEFAULT_TAB = "general";

export const TABS = [
  { id: "general", label: "General", icon: "sliders" },
  { id: "appearance", label: "Appearance", icon: "palette" },
  { id: "models", label: "Models", icon: "layers" },
  { id: "keys", label: "AI services", icon: "key" },
  { id: "websearch", label: "Web search", icon: "search" },
  { id: "browser", label: "Browser", icon: "globe" },
  { id: "vault", label: "Vault", icon: "lock" },
  { id: "trading", label: "Trading", icon: "trendingUp" },
  { id: "rules", label: "Rules", icon: "shield" },
  { id: "memory", label: "Memory", icon: "database" },
  { id: "harness", label: "Self-improvement", icon: "sparkle" },
  { id: "phone", label: "Phone", icon: "smartphone" },
  { id: "channels", label: "Channels", icon: "message" },
  { id: "snapshots", label: "Snapshots", icon: "image" },
  { id: "system", label: "System", icon: "monitor" },
];

// The seven sections the settings are in (item 845, after fifteen tabs in four groups): the nav and the phone's list
// name the sections, and a section of more than one tab shows its tabs in a row under its name. `value` is the tab
// whose count, model or state the section's row shows. Trading is in no section: it is the Trader agent's (item 847),
// and its tab opens from that agent.
export const SECTIONS = [
  { id: "general", label: "General", icon: "sliders", tabs: ["general", "snapshots"], value: null },
  { id: "appearance", label: "Appearance", icon: "palette", tabs: ["appearance"], value: "appearance" },
  { id: "models", label: "Models", icon: "layers", tabs: ["models", "keys", "websearch"], value: null },
  { id: "rules", label: "Rules and memory", icon: "shield", tabs: ["rules", "memory", "harness"], value: "memory" },
  { id: "browser", label: "Browser and Vault", icon: "globe", tabs: ["browser", "vault"], value: "vault" },
  { id: "phone", label: "Phone and channels", icon: "smartphone", tabs: ["phone", "channels"], value: "phone" },
  { id: "system", label: "System", icon: "monitor", tabs: ["system"], value: null },
];

/** The section the tab `id` stands in, or null for a tab in none (Trading). */
export function sectionOf(id) {
  return SECTIONS.find((section) => section.tabs.includes(id)) ?? null;
}

/**
 * The tab an address (`location.hash`) is on: `#/settings/<tab>`, or System for the pages under it (Diagnostics and the
 * open source licences); null for any other address, and for the settings with no tab named.
 */
export function tabAt(hash) {
  const path = hash.replace(/^#/, "").split("?")[0];
  if (path === "/diagnostics" || path === "/licences") {
    return "system";
  }
  const named = /^\/settings\/([a-z]+)$/.exec(path);
  return named !== null && TABS.some((tab) => tab.id === named[1]) ? named[1] : null;
}
