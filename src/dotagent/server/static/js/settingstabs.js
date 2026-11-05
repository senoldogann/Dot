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
  { id: "repair", label: "Self-repair", icon: "wrench" },
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
  { id: "rules", label: "Rules and memory", icon: "shield", tabs: ["rules", "memory", "harness", "repair"], value: "memory" },
  { id: "browser", label: "Browser and Vault", icon: "globe", tabs: ["browser", "vault"], value: "vault" },
  { id: "phone", label: "Phone and channels", icon: "smartphone", tabs: ["phone", "channels"], value: "phone" },
  { id: "system", label: "System", icon: "monitor", tabs: ["system"], value: null },
];

// The phone's sheet of the settings (item 995, the owner's ask of 2026-11-05 for one plain sheet): groups of rows, a row
// one tab (or Connect apps) with a line under its name that says what it holds, so that a setting is found without
// knowing its section. `value` names the row whose count or state shows at its right.
export const PHONE_GROUPS = [
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
      { tab: "phone", line: "Dot on your phones" },
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
