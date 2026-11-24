// What Settings › Skills says of a skill Dot could not offer to a task (item 1405): `GET /api/skills/left-out` lists each
// file or folder with the catalog's reason and where Dot looked for it. Pure, so that it runs under Node without a page.

const BUILTIN_SOURCE = "built-in";
const PLUGIN_SOURCE = "plugin ";
const SKILL_FILE = "SKILL.md";

/**
 * The name a left-out skill is listed by: its folder's (`/skills/twilio-agent-connect/SKILL.md` is
 * `twilio-agent-connect`), or the last part of a path that is no skill file (a skills directory that does not exist).
 */
export function leftOutName(path) {
  const parts = path.split("/").filter((part) => part !== "");
  const last = parts.at(-1) ?? path;
  return last === SKILL_FILE && parts.length > 1 ? parts[parts.length - 2] : last;
}

/**
 * Whether a skill left out is listed on System (the skills of a plugin and those that come with Dot) and not on Personal
 * (the owner's folders, the learned skills and a path under none of Dot's folders, `source` null).
 */
export function leftOutIsSystem(source) {
  return source !== null && (source === BUILTIN_SOURCE || source.startsWith(PLUGIN_SOURCE));
}

/** `count` and its noun, "1 skill" or "2 skills". */
function skills(count) {
  return `${count} ${count === 1 ? "skill" : "skills"}`;
}

/**
 * The plain sentence over the list of what was left out, from `entries` (the items of the route): the skills whose file
 * cannot be read are not offered to any task until it is mended, and one only left out because another skill has its name
 * is not wrong at all. Both are said when both are there; "" when there is nothing.
 */
export function leftOutSummary(entries) {
  const aside = entries.filter((entry) => entry.shadowed).length;
  const broken = entries.length - aside;
  const parts = [];
  if (broken > 0) {
    parts.push(`Dot could not read ${skills(broken)}, so no task is offered ${broken === 1 ? "it" : "them"}. Mend the file and the next task reads it again.`);
  }
  if (aside > 0) {
    parts.push(`${skills(aside)} ${aside === 1 ? "is" : "are"} not offered because another skill has the name. Rename one of them.`);
  }
  return parts.join(" ");
}

/** The chip on a left-out skill's row: what is wrong in two or three words. */
export function leftOutChip(entry) {
  return entry.shadowed ? "Another skill has its name" : "Left out";
}
