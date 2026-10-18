// The words of the composer's menu, without the page: which word of the composer opens it, what the menu offers for it
// and what a pick puts into the text, or picks for the message (item 318). A slash word opens the commands, the skills
// and the MCP tools; an at word the MCP servers, their tools and the skills, to pick for the message (item 367).
// composer.js draws the menu and listens to the keys.

/** The word the caret is in when it opens the menu: `{ start, word, trigger }`, `trigger` "/" or "@" and `word` what
 * follows it (may be empty), or null. Such a word starts the text or follows white space, so a slash inside a path or
 * an at inside an address opens nothing. */
export function menuWord(text, caret) {
  const before = text.slice(0, caret);
  const match = /(^|\s)([/@])([^\s/@]*)$/.exec(before);
  if (match === null) {
    return null;
  }
  return { start: caret - match[3].length - 1, word: match[3], trigger: match[2] };
}

/** The groups of the slash menu: the commands of the composer, then the skills, then each MCP server's tools, each item
 * with its label (what is typed to find it), its hint and what a pick does: a command inserts its words or acts, a skill
 * and a tool are picked for the message as a chip (`pick`: the skill, or the tool's server), and the slash word goes. */
export function paletteGroups(commands, palette) {
  const groups = [];
  if (commands.length > 0) {
    groups.push({ title: "Commands", items: commands.map((command) => ({ ...command, id: `command-${command.label.slice(1)}` })) });
  }
  if (palette !== null && palette.skills.length > 0) {
    groups.push({
      title: "Skills",
      items: palette.skills.map((skill) => ({
        id: `skill-${skill.name}`,
        label: skill.name,
        hint: skill.description,
        pick: { kind: "skill", name: skill.name },
      })),
    });
  }
  if (palette !== null) {
    for (const server of palette.servers) {
      groups.push({
        title: `MCP · ${server.name}`,
        items: server.tools.map((tool) => ({ id: `tool-${tool.name}`, label: tool.name, hint: tool.description, pick: { kind: "plugin", name: server.name } })),
      });
    }
  }
  return groups;
}

/** The groups of the at menu (item 367): the MCP servers by their names, then the skills and each server's tools as the
 * slash menu has them; a pick of any is a chip for the message (a tool picks its server). */
export function pickGroups(palette) {
  if (palette === null) {
    return [];
  }
  const servers =
    palette.servers.length === 0
      ? []
      : [
          {
            title: "MCP servers",
            items: palette.servers.map((server) => ({
              id: `server-${server.name}`,
              label: server.name,
              hint: `${server.tools.length} ${server.tools.length === 1 ? "tool" : "tools"}`,
              pick: { kind: "plugin", name: server.name },
            })),
          },
        ];
  return [...servers, ...paletteGroups([], palette)];
}

/** The groups with only the items whose label holds `word` (case folded), and no empty group. */
export function filterGroups(groups, word) {
  const needle = word.toLowerCase();
  return groups
    .map((group) => ({ ...group, items: group.items.filter((item) => item.label.toLowerCase().includes(needle)) }))
    .filter((group) => group.items.length > 0);
}

/** The text with the menu's word at `start` (through `caret`) replaced by `insert`, and where the caret lands. */
export function replaceMenuWord(text, start, caret, insert) {
  return { text: `${text.slice(0, start)}${insert}${text.slice(caret)}`, caret: start + insert.length };
}
