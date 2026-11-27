// The first steps of a Dot that started with an empty store (item 811), as words and counts, with no page: which steps
// there are, where each is done, how many are, and whether the home shows them.

import { t } from "./i18n.js";

/** Where the card is put away for good. */
export const FIRST_STEPS_KEY = "dot.firstSteps";
export const HIDDEN = "hidden";

/**
 * Each step: its id (the server's), what it asks, where it is done, and the page that does it (`null`: the composer under
 * the greeting).
 * @type {readonly { id: string, title: string, where: string, href: string | null }[]}
 */
export const STEPS = [
  { id: "name", title: t("firststepslist.tellDotYourName"), where: t("firststepslist.settingsGeneral"), href: "#/settings/general" },
  { id: "passwords", title: t("firststepslist.bringYourPasswords"), where: t("firststepslist.settingsVaultImportFromCsv"), href: "#/settings/vault" },
  { id: "imessage", title: t("firststepslist.reachDotFromImessage"), where: t("firststepslist.settingsChannels"), href: "#/settings/channels" },
  { id: "phone", title: t("firststepslist.pairYourPhone"), where: t("firststepslist.settingsPhone"), href: "#/settings/phone" },
  { id: "apps", title: t("firststepslist.connectYourApps"), where: t("firststepslist.settingsConnectors"), href: "#/settings/connectors" },
  { id: "chat", title: t("firststepslist.introduceYourself"), where: t("firststepslist.writeToDotWhoYou"), href: null },
];

/**
 * The steps of `view` in the order of `STEPS`, each with whether it is done, and how many are; a step the server does not
 * name is left out.
 * @param {{ fresh: boolean, steps: { id: string, done: boolean }[] }} view
 */
export function stepsOf(view) {
  const done = new Map(view.steps.map((step) => [step.id, step.done]));
  const steps = STEPS.filter((step) => done.has(step.id)).map((step) => ({ ...step, done: done.get(step.id) === true }));
  return { steps, finished: steps.filter((step) => step.done).length };
}

/**
 * Whether the card is shown for `view`: Dot is fresh, a step is left, and the owner has not hidden it.
 * @param {{ fresh: boolean, steps: { id: string, done: boolean }[] }} view
 * @param {string | null} hidden what `FIRST_STEPS_KEY` holds
 */
export function showsFirstSteps(view, hidden) {
  const { steps, finished } = stepsOf(view);
  return view.fresh && hidden !== HIDDEN && finished < steps.length;
}
