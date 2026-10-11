// The slash menu's words, without the page: which word of the composer is a slash word, what the menu offers for it
// and what a pick puts into the text. composer.js draws the menu and listens to the keys.

/** The slash word the caret is in: `{ start, word }` with the word after the slash (may be empty), or null. A slash word
 * starts the text or follows white space, so a slash inside a path or an address opens nothing. */
export function slashWord(text, caret) {
  const before = text.slice(0, caret);
  const match = /(^|\s)\/([^\s/]*)$/.exec(before);
  if (match === null) {
    return null;
  }
  return { start: caret - match[2].length - 1, word: match[2] };
}

/** The groups of the menu: the commands of the composer, then the skills, then each MCP server's tools, each item with
 * its label (what is typed to find it), its hint and what a pick inserts. */
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
        insert: `use the skill ${skill.name}: `,
      })),
    });
  }
  if (palette !== null) {
    for (const server of palette.servers) {
      groups.push({
        title: `MCP · ${server.name}`,
        items: server.tools.map((tool) => ({ id: `tool-${tool.name}`, label: tool.name, hint: tool.description, insert: `${tool.name} ` })),
      });
    }
  }
  return groups;
}

/** The groups with only the items whose label holds `word` (case folded), and no empty group. */
export function filterGroups(groups, word) {
  const needle = word.toLowerCase();
  return groups
    .map((group) => ({ ...group, items: group.items.filter((item) => item.label.toLowerCase().includes(needle)) }))
    .filter((group) => group.items.length > 0);
}

/** The text with the slash word at `start` (through `caret`) replaced by `insert`, and where the caret lands. */
export function replaceSlashWord(text, start, caret, insert) {
  return { text: `${text.slice(0, start)}${insert}${text.slice(caret)}`, caret: start + insert.length };
}
