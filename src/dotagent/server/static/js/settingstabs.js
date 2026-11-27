// The pages of the settings and the groups they stand in (items 562, 995, 999 and 1336). The settings page draws the open
// one; the settings nav in the sidebar (settingsnav.js) and the phone's sheet (views/settings.js) list the groups, and the
// nav's search reads each page's `words`. A page is one address, `#/settings/<id>`: the pages that were top-level ones
// of their own (Scheduled tasks, Plugins, Skills, Connectors, Agents, Campaigns, Diagnostics, Open source licences) stand
// here too, and their old addresses lead here (main.js MOVED).

import { t } from "./i18n.js";

export const DEFAULT_TAB = "general";

export const TABS = [
  { id: "general", label: t("settingstabs.general"), icon: "sliders", words: t("settingstabs.nameNotificationsVoiceMessagesLang") },
  { id: "appearance", label: t("settingstabs.appearance"), icon: "palette", words: t("settingstabs.themeColourDarkLightFont") },
  { id: "rules", label: t("settingstabs.rules"), icon: "shield", words: t("settingstabs.permissionsApprovalsPolicyAskSafet") },
  { id: "memory", label: t("settingstabs.memory"), icon: "database", words: t("settingstabs.notesRememberLessonsSearch") },
  { id: "vault", label: t("settingstabs.vault"), icon: "lock", words: t("settingstabs.loginsPasswordsCardsContacts") },
  { id: "snapshots", label: t("settingstabs.snapshots"), icon: "image", words: t("settingstabs.screenCaptureCommandKeyPictures") },
  { id: "agents", label: t("settingstabs.agents"), icon: "users", words: t("settingstabs.agentTeamHandoffsHelpers") },
  { id: "models", label: t("settingstabs.models"), icon: "layers", words: t("settingstabs.modelContextReasoningProviderFallb") },
  { id: "browser", label: t("settingstabs.browser"), icon: "globe", words: t("settingstabs.chromeProfileHeadlessSignIn") },
  { id: "websearch", label: t("settingstabs.webSearch"), icon: "search", words: t("settingstabs.enginesSearxngBrave") },
  { id: "schedules", label: t("settingstabs.scheduledTasks"), icon: "calendar", words: t("settingstabs.schedulesLoopsCronRepeatMorning") },
  { id: "campaigns", label: t("settingstabs.campaigns"), icon: "target", words: t("settingstabs.goalsSessionsLongRunning") },
  { id: "harness", label: t("settingstabs.selfImprovement"), icon: "sparkles", words: t("settingstabs.harnessLearningLessonsProposals") },
  { id: "connectors", label: t("settingstabs.connectors"), icon: "plug", words: t("settingstabs.connectAppsServicesMcpAccounts") },
  { id: "plugins", label: t("settingstabs.plugins"), icon: "puzzle", words: t("settingstabs.galleryInstallExtensions") },
  { id: "skills", label: t("settingstabs.skills"), icon: "sparkle", words: t("settingstabs.learnedBuiltInVersionsTeach") },
  { id: "keys", label: t("settingstabs.aiServices"), icon: "key", words: t("settingstabs.keysApiProvidersOpenaiAnthropic") },
  { id: "phone", label: t("settingstabs.phone"), icon: "smartphone", words: t("settingstabs.tailscalePairDevicesIphone") },
  { id: "channels", label: t("settingstabs.channels"), icon: "message", words: t("settingstabs.imessageTelegramWhatsappMessaging") },
  { id: "system", label: t("settingstabs.system"), icon: "monitor", words: t("settingstabs.daemonHealthEmergencyStopVersion") },
  { id: "repair", label: t("settingstabs.selfRepair"), icon: "wrench", words: t("settingstabs.problemsFixesClaudeCodeSessions") },
  { id: "diagnostics", label: t("settingstabs.diagnostics"), icon: "terminal", words: t("settingstabs.processesLogsDiskUsage") },
  { id: "licences", label: t("settingstabs.openSourceLicences"), icon: "file", words: t("settingstabs.aboutNoticesLicencesSoftware") },
];

// The settings as groups of rows (item 995, the owner's ask of 2026-11-05 for one plain sheet; item 999, a Mac's nav
// too; item 1336, grouped by what they are for): a row one page, so that a setting is found without knowing
// its section. On a phone a row has a line under its name that says what it holds; a Mac's nav shows the name alone.
// `value` names the row whose count or state shows at its right.
export const SETTINGS_GROUPS = [
  {
    title: null,
    rows: [
      { tab: "general", line: t("settingstabs.namesNotificationsVoiceAndMessages") },
      { tab: "appearance", line: t("settingstabs.themesTypeAndColours"), value: "appearance" },
      { tab: "rules", line: t("settingstabs.whatDotDoesWithoutAsking") },
      { tab: "memory", line: t("settingstabs.whatDotRemembers"), value: "memory" },
      { tab: "vault", line: t("settingstabs.loginsCardsAndContactsDot"), value: "vault" },
      { tab: "snapshots", line: t("settingstabs.picturesOfYourScreenFor") },
    ],
  },
  {
    title: t("settingstabs.agents"),
    rows: [
      { tab: "agents", line: t("settingstabs.yourAgentsTheirTeamsAnd") },
      { tab: "models", line: t("settingstabs.theModelsDotThinksWith") },
      { tab: "browser", line: t("settingstabs.dotSOwnChrome") },
      { tab: "websearch", line: t("settingstabs.howDotSearchesTheWeb") },
      { tab: "schedules", line: t("settingstabs.tasksDotStartsByItself"), value: "schedules" },
      { tab: "campaigns", line: t("settingstabs.goalsDotKeepsWorkingOn") },
      { tab: "harness", line: t("settingstabs.whatDotLearnsFromIts") },
    ],
  },
  {
    title: t("settingstabs.connections"),
    rows: [
      { tab: "connectors", line: t("settingstabs.theAppsAndServicesDot") },
      { tab: "plugins", line: t("settingstabs.pluginsDotCanInstall") },
      { tab: "skills", line: t("settingstabs.whatDotKnowsHowTo") },
      { tab: "keys", line: t("settingstabs.theKeysOfTheAi") },
      { tab: "phone", line: t("settingstabs.dotOnYourPhones"), value: "phone" },
      { tab: "channels", line: t("settingstabs.imessageAndTheOtherWays") },
    ],
  },
  {
    title: t("settingstabs.maintenance"),
    rows: [
      { tab: "system", line: t("settingstabs.dotSHealthVersionAnd") },
      { tab: "repair", line: t("settingstabs.dotMendsItsOwnProblems") },
      { tab: "diagnostics", line: t("settingstabs.theProgramsDotRunsAnd") },
    ],
  },
  {
    title: t("settingstabs.about"),
    rows: [{ tab: "licences", line: t("settingstabs.theSoftwareDotIsBuilt") }],
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
