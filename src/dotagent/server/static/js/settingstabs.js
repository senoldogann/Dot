// The tabs of the settings and the groups they stand in (item 562). The settings page draws the open one, the settings nav
// in the sidebar (settingsnav.js) and the phone's list of tabs (views/settings.js) name them in these groups.

export const DEFAULT_TAB = "general";

export const TABS = [
  { id: "general", label: "General", icon: "sliders" },
  { id: "appearance", label: "Appearance", icon: "palette" },
  { id: "models", label: "Models", icon: "layers" },
  { id: "keys", label: "AI services", icon: "key" },
  { id: "websearch", label: "Web search", icon: "search" },
  { id: "browser", label: "Browser", icon: "globe" },
  { id: "vault", label: "Vault", icon: "lock" },
  { id: "rules", label: "Rules", icon: "shield" },
  { id: "memory", label: "Memory", icon: "database" },
  { id: "harness", label: "Self-improvement", icon: "sparkle" },
  { id: "phone", label: "Phone", icon: "smartphone" },
  { id: "channels", label: "Channels", icon: "message" },
  { id: "snapshots", label: "SnapShots", icon: "image" },
  { id: "system", label: "System", icon: "monitor" },
];

export const GROUPS = [
  { title: "Interface", tabs: ["general", "appearance"] },
  { title: "Models", tabs: ["models", "keys", "websearch"] },
  { title: "How Dot works", tabs: ["rules", "memory", "harness"] },
  { title: "This Mac", tabs: ["browser", "vault", "phone", "channels", "snapshots", "system"] },
];

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
