// The flags the policy raises on an action and the kinds it sorts actions into, said in everyday words (item 625), for
// the chips of an approval card and the line that says why a step ran. Pure, so they can be read alone
// (tests/js/flagwords.test.js). The program's own names (`message_send`) stay as the tooltip of a chip.

// What each flag of policy/types.py says of an action, as a phrase that follows "It counts as:".
const FLAG_WORDS = {
  destructive: "deletes or overwrites something",
  financial: "involves money",
  credential: "involves a password or a key",
  system_settings: "changes a setting of the Mac",
  privilege: "needs administrator rights",
  external_send: "sends something out of this Mac",
  message_send: "sends a message for you",
  public_post: "posts in public",
  social: "acts on social media",
  install: "installs software",
  secret_access: "reaches a secret",
  protected_read: "may read protected data",
  exfiltration: "sends data out",
  outside_workspace: "reaches outside Dot's own folder",
  persistence: "leaves something that stays after the task",
  upload: "uploads a file",
  self_change: "changes Dot's own files",
  vault_fill: "fills in a login of your vault or password manager, or makes one",
};

// What each kind of action says of what Dot is about to do, as a chip's words.
const KIND_WORDS = {
  read: "reads a file",
  write: "writes a file",
  shell: "runs a command",
  network: "uses the web",
  browser_read: "reads a web page",
  browser_navigate: "opens a web page",
  browser_input: "types in a web page",
  browser_click: "clicks in a web page",
  desktop_observe: "looks at the screen",
  desktop_input: "types or clicks on the Mac",
  app_control: "controls an app",
  message: "writes a message",
  schedule: "changes a schedule",
  memory_read: "reads its memory",
  memory_write: "saves to its memory",
  notes_write: "writes its notes",
  delegate: "hands work to a helper",
  meta: "is part of Dot's own work",
  mcp: "uses a connected app",
  mcp_read: "reads from a connected app",
};

/** A name as words when no phrase is known for it: `new_thing` reads "new thing". */
function spoken(name) {
  return String(name).replace(/_/g, " ");
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
