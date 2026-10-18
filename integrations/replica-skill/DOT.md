# Replica skills in Dot

Eleven Claude skills from [replica-skill](https://github.com/Jakeschincariol/replica-skill) (MIT, by Jake Schincariol)
that turn an app into a recon map, a rebuild, a bug hunt and a better launch. In Dot they are ordinary skills:
`<home>/skills/replica-*`, read at the start of every run — no plugin and no MCP server, so nothing has to be
restarted.

## Install (this folder → Dot)

```bash
for folder in integrations/replica-skill/replica-*/; do
  name=$(basename "$folder")
  rsync -a --delete "$folder" "$HOME/Library/Application Support/Dot/skills/$name/"
done
```

`dotx skills list | grep replica-` shows all eleven.

## Update

```bash
git -C /path/to/replica-skill pull
rsync -a --delete --exclude .git/ --exclude DOT.md /path/to/replica-skill/ integrations/replica-skill/
# then the install loop above
```

## Notes

- The skills are written for Claude Code: they name sub-agents and tools (`Task`, `Read`, `Write`, `WebFetch`,
  Playwright MCP). Dot has `delegate`, the file tools, the browser and the shell; the run does the same steps with
  what it has, as Dot's skill instructions say.
- `replica/` in the text is the project folder a run works in; Dot's workspace plays that part.
- Licence: MIT, Copyright (c) Jake Schincariol — [LICENSE](LICENSE) beside this file.
