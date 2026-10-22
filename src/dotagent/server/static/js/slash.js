// The words of the composer's menu, without the page: which word of the composer opens it, what the menu offers for it
// and what a pick puts into the text, or picks for the message (item 318). A slash word opens the commands, the skills
// and the MCP tools; an at word the MCP servers and the plugins, to pick for the message (items 367 and 447). A server
// and each of its tools wear the mark its Customize tile wears, and a skill its Customize card's (item 477).
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
    groups.push({
      title: "Commands",
      kind: "command",
      items: commands.map((command) => ({ ...command, id: `command-${command.label.slice(1)}`, kind: "command" })),
    });
  }
  if (palette !== null && palette.skills.length > 0) {
    groups.push({
      title: "Skills",
      kind: "skill",
      items: palette.skills.map((skill) => ({
        id: `skill-${skill.name}`,
        label: skill.name,
        hint: skill.description,
        icon: skill.icon,
        source: skill.source,
        imported: skill.imported,
        kind: "skill",
        pick: { kind: "skill", name: skill.name },
      })),
    });
  }
  if (palette !== null) {
    for (const server of palette.servers) {
      groups.push({
        title: `MCP · ${server.name}`,
        section: "MCP tools",
        kind: "plugin",
        items: server.tools.map((tool) => ({
          id: `tool-${tool.name}`,
          // The server stands before its tool as the tool's row names them, so that tools of many servers read apart.
          label: tool.name.toLowerCase().startsWith(server.name.toLowerCase()) ? tool.name : `${server.name} / ${tool.name}`,
          hint: tool.description,
          kind: "tool",
          serverName: server.name,
          mark: server.mark ?? null,
          pick: { kind: "plugin", name: server.name },
        })),
      });
    }
  }
  return groups;
}

/** The groups of the at menu (item 367): the MCP servers and the plugins, by their names, each with its service's
 * mark, then the Google and Microsoft accounts signed in, by their addresses, each with its provider's mark (item 496)
 * — and nothing else: the skills and the commands are the slash menu's (the owner's word of 2026-10-21, item 447).
 * A pick is a chip for the message. */
export function pickGroups(palette) {
  if (palette === null) {
    return [];
  }
  const groups = [];
  if (palette.servers.length > 0) {
    groups.push({
      title: "MCP servers",
      kind: "plugin",
      items: palette.servers.map((server) => ({
        id: `server-${server.name}`,
        label: server.name,
        hint: `${server.tools.length} ${server.tools.length === 1 ? "tool" : "tools"}`,
        kind: "plugin",
        mark: server.mark ?? null,
        pick: { kind: "plugin", name: server.name },
      })),
    });
  }
  if (palette.accounts.length > 0) {
    groups.push({
      title: "Accounts",
      kind: "account",
      items: palette.accounts.map((account) => ({
        id: `account-${account.address}`,
        label: account.address,
        hint: account.reads,
        kind: "account",
        mark: account.mark,
        pick: { kind: "account", name: account.address },
      })),
    });
  }
  return groups;
}

/** What the menu draws: its groups in sections, the groups that say they are one section (the MCP tools of every server)
 * drawn as one, each `{ title, items }`; `title` is what a screen reader hears of the section. */
export function menuSections(groups) {
  const sections = [];
  for (const group of groups) {
    const title = group.section ?? group.title;
    const last = sections.at(-1);
    if (last !== undefined && last.title === title) {
      last.items.push(...group.items);
    } else {
      sections.push({ title, items: [...group.items] });
    }
  }
  return sections;
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
