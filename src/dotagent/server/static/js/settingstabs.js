// The tabs of the settings and the groups they stand in (items 562, 995 and 999). The settings page draws the open one;
// the settings nav in the sidebar (settingsnav.js) and the phone's sheet (views/settings.js) list the groups.

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
  { id: "repair", label: "Self-repair", icon: "wrench" },
  { id: "phone", label: "Phone", icon: "smartphone" },
  { id: "channels", label: "Channels", icon: "message" },
  { id: "snapshots", label: "Snapshots", icon: "image" },
  { id: "system", label: "System", icon: "monitor" },
];

// The settings as groups of rows (item 995, the owner's ask of 2026-11-05 for one plain sheet; item 999, a Mac's nav
// too, after seven sections of tabs, item 845): a row one tab (or Connect apps), so that a setting is found without
// knowing its section. On a phone a row has a line under its name that says what it holds; a Mac's nav shows the name
// alone. `value` names the row whose count or state shows at its right. Trading is in no group: it is the Trader
// agent's (item 847), and its tab opens from that agent.
export const SETTINGS_GROUPS = [
  {
    title: null,
    rows: [
      { tab: "general", line: "Names, notifications, voice and messages" },
      { tab: "vault", line: "Logins, cards and contacts Dot can use", value: "vault" },
    ],
  },
  {
    title: null,
    rows: [{ href: "#/customize/connectors", label: "Connect apps", icon: "appsGrid", line: "Apps, plugins and skills" }],
  },
  {
    title: "Dot",
    rows: [
      { tab: "rules", line: "What Dot does without asking you" },
      { tab: "memory", line: "What Dot remembers", value: "memory" },
      { tab: "models", line: "The models Dot thinks with" },
      { tab: "keys", line: "The keys of the AI services" },
      { tab: "websearch", line: "How Dot searches the web" },
      { tab: "browser", line: "Dot's own Chrome" },
      { tab: "harness", line: "What Dot learns from its tasks" },
      { tab: "repair", line: "Dot mends its own problems" },
    ],
  },
  {
    title: "Phones and channels",
    rows: [
      { tab: "phone", line: "Dot on your phones", value: "phone" },
      { tab: "channels", line: "iMessage and the other ways to reach Dot" },
    ],
  },
  {
    title: "App",
    rows: [
      { tab: "appearance", line: "Themes, type and colours", value: "appearance" },
      { tab: "snapshots", line: "Pictures of your screen for Dot" },
      { tab: "system", line: "The daemon, its version and diagnostics" },
    ],
  },
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
