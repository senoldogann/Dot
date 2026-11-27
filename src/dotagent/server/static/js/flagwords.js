// The flags the policy raises on an action and the kinds it sorts actions into, said in everyday words (item 625), for
// the chips of an approval card and the line that says why a step ran. Pure, so they can be read alone
// (tests/js/flagwords.test.js). The program's own names (`message_send`) stay as the tooltip of a chip.

// What each flag of policy/types.py says of an action, as a phrase that follows "It counts as:".
import { t } from "./i18n.js";

const FLAG_WORDS = {
  destructive: t("flagwords.deletesOrOverwritesSomething"),
  financial: t("flagwords.involvesMoney"),
  credential: t("flagwords.involvesAPasswordOrA"),
  system_settings: t("flagwords.changesASettingOfThe"),
  privilege: t("flagwords.needsAdministratorRights"),
  external_send: t("flagwords.sendsSomethingOutOfThis"),
  message_send: t("flagwords.sendsAMessageForYou"),
  public_post: t("flagwords.postsInPublic"),
  social: t("flagwords.actsOnSocialMedia"),
  install: t("flagwords.installsSoftware"),
  secret_access: t("flagwords.reachesASecret"),
  protected_read: t("flagwords.mayReadProtectedData"),
  exfiltration: t("flagwords.sendsDataOut"),
  outside_workspace: t("flagwords.reachesOutsideDotSOwn"),
  persistence: t("flagwords.leavesSomethingThatStaysAfter"),
  upload: t("flagwords.uploadsAFile"),
  self_change: t("flagwords.changesDotSOwnFiles"),
  vault_fill: t("flagwords.fillsInALoginOf"),
  release: t("flagwords.putsAProjectSChange"),
};

// What each kind of action says of what Dot is about to do, as a chip's words.
const KIND_WORDS = {
  read: t("flagwords.readsAFile"),
  write: t("flagwords.writesAFile"),
  shell: t("flagwords.runsACommand"),
  network: t("flagwords.usesTheWeb"),
  browser_read: t("flagwords.readsAWebPage"),
  browser_navigate: t("flagwords.opensAWebPage"),
  browser_input: t("flagwords.typesInAWebPage"),
  browser_click: t("flagwords.clicksInAWebPage"),
  desktop_observe: t("flagwords.looksAtTheScreen"),
  desktop_input: t("flagwords.typesOrClicksOnThe"),
  app_control: t("flagwords.controlsAnApp"),
  message: t("flagwords.writesAMessage"),
  schedule: t("flagwords.changesASchedule"),
  memory_read: t("flagwords.readsItsMemory"),
  memory_write: t("flagwords.savesToItsMemory"),
  notes_write: t("flagwords.writesItsNotes"),
  delegate: t("flagwords.handsWorkToAHelper"),
  meta: t("flagwords.isPartOfDotS"),
  mcp: t("flagwords.usesAConnectedApp"),
  mcp_read: t("flagwords.readsFromAConnectedApp"),
};

/** A name as words when no phrase is known for it: `new_thing` reads "new thing". */
function spoken(name) {
  return String(name).replace(/_/g, " ");
}

/** Whether the policy's flag has a phrase of its own (not one made from its name). */
export function knownFlag(flag) {
  return Object.hasOwn(FLAG_WORDS, flag);
}

/** What `flag` says of an action: "sends a message for you". */
export function flagWords(flag) {
  return Object.hasOwn(FLAG_WORDS, flag) ? FLAG_WORDS[flag] : spoken(flag);
}

/** What `kind` says Dot is about to do: "runs a command". */
export function kindWords(kind) {
  return Object.hasOwn(KIND_WORDS, kind) ? KIND_WORDS[kind] : spoken(kind);
}

/** A phrase with its first letter capital, for a chip. */
export function capital(text) {
  return text === "" ? text : `${text[0].toUpperCase()}${text.slice(1)}`;
}
