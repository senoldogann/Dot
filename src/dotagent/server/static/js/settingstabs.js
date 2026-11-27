// The pages of the settings and the groups they stand in (items 562, 995, 999 and 1336). The settings page draws the open
// one; the settings nav in the sidebar (settingsnav.js) and the phone's sheet (views/settings.js) list the groups, and the
// nav's search reads each page's `words`. A page is one address, `#/settings/<id>`: the pages that were top-level ones
// of their own (Scheduled tasks, Plugins, Skills, Connectors, Agents, Campaigns, Diagnostics, Open source licences) stand
// here too, and their old addresses lead here (main.js MOVED).

export const DEFAULT_TAB = "general";

export const TABS = [
  { id: "general", label: "General", icon: "sliders", words: "name notifications voice messages language send key" },
  { id: "appearance", label: "Appearance", icon: "palette", words: "theme colour dark light font size density contrast" },
  { id: "rules", label: "Rules", icon: "shield", words: "permissions approvals policy ask safety" },
  { id: "memory", label: "Memory", icon: "database", words: "notes remember lessons search" },
  { id: "vault", label: "Vault", icon: "lock", words: "logins passwords cards contacts" },
  { id: "snapshots", label: "Snapshots", icon: "image", words: "screen capture command key pictures" },
  { id: "agents", label: "Agents", icon: "users", words: "agent team handoffs helpers" },
  { id: "models", label: "Models", icon: "layers", words: "model context reasoning provider fallback" },
  { id: "browser", label: "Browser", icon: "globe", words: "chrome profile headless sign in" },
  { id: "websearch", label: "Web search", icon: "search", words: "engines searxng brave" },
  { id: "schedules", label: "Scheduled tasks", icon: "calendar", words: "schedules loops cron repeat morning brief watchers check-in" },
  { id: "campaigns", label: "Campaigns", icon: "target", words: "goals sessions long running" },
  { id: "harness", label: "Self-improvement", icon: "sparkles", words: "harness learning lessons proposals" },
  { id: "connectors", label: "Connectors", icon: "plug", words: "connect apps services mcp accounts google github slack notion" },
  { id: "plugins", label: "Plugins", icon: "puzzle", words: "gallery install extensions" },
  { id: "skills", label: "Skills", icon: "sparkle", words: "learned built-in versions teach" },
  { id: "keys", label: "AI services", icon: "key", words: "keys api providers openai anthropic" },
  { id: "phone", label: "Phone", icon: "smartphone", words: "tailscale pair devices iphone" },
  { id: "channels", label: "Channels", icon: "message", words: "imessage telegram whatsapp messaging" },
  { id: "hooks", label: "Hooks", icon: "bolt", words: "webhooks signed calls http trigger" },
  { id: "system", label: "System", icon: "monitor", words: "daemon health emergency stop version update restart" },
  { id: "repair", label: "Self-repair", icon: "wrench", words: "problems fixes claude code sessions" },
  { id: "diagnostics", label: "Diagnostics", icon: "terminal", words: "processes logs disk usage" },
  { id: "speed", label: "Speed", icon: "clock", words: "performance latency slow percentiles p95 p99 model tools hiz" },
  { id: "licences", label: "Open source licences", icon: "file", words: "about notices licences software" },
];

// The settings as groups of rows (item 995, the owner's ask of 2026-11-05 for one plain sheet; item 999, a Mac's nav
// too; item 1336, grouped by what they are for): a row one page, so that a setting is found without knowing
// its section. On a phone a row has a line under its name that says what it holds; a Mac's nav shows the name alone.
// `value` names the row whose count or state shows at its right.
export const SETTINGS_GROUPS = [
  {
    title: null,
    rows: [
      { tab: "general", line: "Names, notifications, voice and messages" },
      { tab: "appearance", line: "Themes, type and colours", value: "appearance" },
      { tab: "rules", line: "What Dot does without asking you" },
      { tab: "memory", line: "What Dot remembers", value: "memory" },
      { tab: "vault", line: "Logins, cards and contacts Dot can use", value: "vault" },
      { tab: "snapshots", line: "Pictures of your screen for Dot" },
    ],
  },
  {
    title: "Agents",
    rows: [
      { tab: "agents", line: "Your agents, their teams and handoffs" },
      { tab: "models", line: "The models Dot thinks with" },
      { tab: "browser", line: "Dot's own Chrome" },
      { tab: "websearch", line: "How Dot searches the web" },
      { tab: "schedules", line: "Tasks Dot starts by itself", value: "schedules" },
      { tab: "campaigns", line: "Goals Dot keeps working on" },
      { tab: "harness", line: "What Dot learns from its tasks" },
    ],
  },
  {
    title: "Connections",
    rows: [
      { tab: "connectors", line: "The apps and services Dot works with" },
      { tab: "plugins", line: "Plugins Dot can install" },
      { tab: "skills", line: "What Dot knows how to do" },
      { tab: "keys", line: "The keys of the AI services" },
      { tab: "phone", line: "Dot on your phones", value: "phone" },
      { tab: "channels", line: "iMessage and the other ways to reach Dot" },
      { tab: "hooks", line: "Signed calls from services that start work" },
    ],
  },
  {
    title: "Maintenance",
    rows: [
      { tab: "system", line: "Dot's health, version and emergency stop" },
      { tab: "repair", line: "Dot mends its own problems" },
      { tab: "diagnostics", line: "The programs Dot runs and its logs" },
      { tab: "speed", line: "Where the seconds of Dot's work go" },
    ],
  },
  {
    title: "About",
    rows: [{ tab: "licences", line: "The software Dot is built from" }],
  },
];

/** The page `id` (a tab of the settings), or undefined. */
export function tabOf(id) {
  return TABS.find((tab) => tab.id === id);
}

/** The address of the page `id`, with `rest` (a sub-address such as `/new` or an id) after it. */
export function settingsHref(id, rest = "") {
  return `#/settings/${id}${rest}`;
}

/**
 * The page an address (`location.hash`) is on: `#/settings/<page>` with any sub-address after it (a schedule's editor, a
 * plugin's page); null for any other address, and for the settings with no page named.
 */
export function tabAt(hash) {
  const path = hash.replace(/^#/, "").split("?")[0];
  const named = /^\/settings\/([a-z]+)(?:\/|$)/.exec(path);
  return named !== null && TABS.some((tab) => tab.id === named[1]) ? named[1] : null;
}

/**
 * Whether `tab` matches what the owner typed in the settings search: every word of `query` starts a word of the page's
 * name, its line or its search words.
 */
export function matchesSearch(tab, line, query) {
  const text = `${tab.label} ${line ?? ""} ${tab.words}`.toLowerCase();
  const found = text.split(/[^a-z0-9]+/);
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter((word) => word !== "")
    .every((word) => found.some((candidate) => candidate.startsWith(word)));
}
