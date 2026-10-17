# Apps, MCP servers, skills and plugins

## Connect apps

**Connect apps**, the sidebar's page under Campaigns (items 278 and 289), lists the applications Dot works with, each with what Dot
does with it, where it stands and the one step that sets it up; **Check again** reads them all anew.

- **On this Mac**: Mail, Calendar, Notes, Reminders, Messages, Contacts, Excel, Word, PowerPoint and Outlook, which Dot
  drives with AppleScript. macOS lets an app send Apple events to another only once you have said yes to its question
  (Privacy & Security › Automation), so a row says **Allowed**, **Not asked yet**, **Refused**, **Not checked** or
  **Not installed**. macOS tells about an application only while it runs: while one is closed, its row keeps the last
  answer macOS gave about it (item 288), kept for the program macOS asks for Dot, and says **Not checked** only when
  there is none. **Allow** asks macOS, which shows its question on the Mac; an application that is not open is opened
  hidden first, without coming to the front. The page waits a minute for your answer, then says that the question waits
  on the Mac. After a no macOS does not ask again: **Open System Settings** opens the Automation pane, and the row says
  what to switch on there, under the program macOS asks for Dot: Dot for the service, else the app Dot was started
  from (Terminal, iTerm, Claude).
- **Your files**: OneDrive, whose folders (`~/Library/CloudStorage/OneDrive-*`) Dot's `onedrive` skill works in. It
  says **Syncing** once a folder of it is there; else **Open** opens OneDrive to sign in.
- **On the web**: Google, Microsoft 365, GitHub, Slack, Notion, LinkedIn, X and Instagram, used in Dot's own browser.
  A row says **Signed in** when that browser holds a cookie the site keeps only for a signed-in visitor, read by host
  and name and never by value: from Chrome while it runs, else from a copy of its profile's cookie file. **Sign in**
  opens the sign-in page in a tab of Dot's browser on the Mac. With My Chrome (Settings › Browser) Dot works in your own
  Chrome, where you are signed in as you always are; a browser without a window (`headless = true` under `[browser]`)
  has none to sign in in, which the section says once.
- **Tools of other services** (item 279): every [MCP server](#mcp-servers) Dot has, from its config, a plugin, another
  agent (Skills › Found elsewhere) or this page, with **Ready** and its tools, **Left out** and why, or **Off**. Below
  it, **Services to add**: Notion, Linear, Sentry, Cloudflare, Stripe and Todoist, which sign in with your account, and
  DeepWiki and Microsoft Learn, open to all; or any server by its name and address, a URL or the command that starts it
  on this Mac. **Add** tries the server first and keeps it only once it answered with its tools, which go to the runs
  that start next, without a restart. A server that asks for a sign-in gets one in the browser you are using: the row
  says **Waiting for your sign-in** with **Open the sign-in page**, the service's own page, which sends you back to
  Dot's `/oauth/callback` on the address you added it from (the Mac's, or your phone's through `[server] reach`), and
  the row turns **Ready**. This is MCP's own authorization: OAuth with PKCE, Dot registering itself with the service as
  a client without a secret. Dot keeps the tokens in `mcp-sign-ins/<server>.json` (mode 0600) and renews them before
  they run out, after a restart too; a run never signs in, so a server whose sign-in is gone is left out with words
  that bring you back here (**Sign in again**). **Remove** takes a server of this page out, with its tools and its
  sign-in; one set up elsewhere is removed there.

A sign-in is told by its cookie alone: a session the site ended stays **Signed in** until its cookie expires. Not in
the services' list, as Dot cannot register with them by itself (checked 2026-10-15): GitHub, which takes a personal
token (an `[[mcp]]` table with an `Authorization` header), Asana, Slack, Google and Box (clients registered by hand),
and Vercel, Figma, Canva and Atlassian (approved clients only, or lists of allowed ones).

## MCP servers

Dot can use the tools of [MCP](https://modelcontextprotocol.io) servers that it starts itself over stdio, or reaches
over streamable HTTP or the older SSE transport. Add one `[[mcp]]` table per server to `config.toml`:

```toml
[[mcp]]
name = "notes"                              # a-z, 0-9 and -, starting with a letter; tools become mcp__notes__<tool>
command = "npx"                             # started with the PATH of your login shell
args = ["-y", "some-mcp-server"]
env = { NOTES_TOKEN = "${NOTES_TOKEN}" }    # ${NAME} is looked up like an API key
allow = ["search", "read_note"]             # offer only these tools; empty = all of them (at most 30)
enabled = true
startup_timeout_s = 60.0
call_timeout_s = 120.0

[[mcp]]
name = "docs"
url = "https://mcp.example.com/mcp"       # a remote server; transport = "http" (default) or "sse"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }  # ${NAME} as above; headers are never logged
```

`dotx mcp list` starts every configured server and shows its tools, or why it does not start. Only tools are used (no
resources, prompts or sampling). A server at a URL may sign in as MCP's authorization asks (OAuth): `oauth = true` has
Dot send the token it keeps for it, which **Sign in again** on [Connect apps](#connect-apps) gets, and the servers added
there sign in by themselves. The tool list is read when Dot starts and when a server is added or removed on Connect
apps, for the runs that start after; a server that dies is started again by the next call. A server that is enabled but cannot start (its command, a secret or its working directory is missing, it
quits or does not answer in time, its `allow` list names a tool it does not offer, two of its tools would share a name,
or the input schema of one of its tools cannot be handed to a model: a reference that leads nowhere, or a tree whose
nodes hold nodes, which `allow` can leave out) is left out, and Dot starts without its tools: Settings › System names it
with the reason, `dotx run`, `dotx resume` and `dotx chat` say so before they begin, and `dotx doctor` warns of a
missing command or secret. It is tried again when Dot starts again; `enabled = false` stops trying it. Plugins add their
servers too (see [Skills and plugins](#skills-and-plugins)); their names become `<plugin>-<server>`. MCP results are
untrusted content, and the stderr of a stdio server goes to `<logs>/mcp-<name>.log`. A tool that its server declares
read-only (`readOnlyHint`) is taken at its word and runs as a read, with no flag; any other counts as one that may
change things (`destructive`) and send to the world (`external_send`), unless its server says it does not.

## Skills and plugins

A skill is a folder of instructions for one kind of work, in the Agent Skills layout: `<name>/SKILL.md` with a YAML
frontmatter (`name`, `description`) and optional `scripts/`, `references/` and `assets/`. Every model is given, in its
instructions, the `name: description` line of each skill its run is offered, and reads the rest with the `skill` tool
when a task matches; instructions longer than one tool result come in parts, each saying where the next one starts, so
none is cut out of the middle. A run offered more than `listed` under `[skills]` (20) is given those that fit its goal
and the goals of the latest earlier runs of its thread best, as its notes are chosen, the best fit first and at most
that many, and is told how many more there are: `find_help` searches them all, and `skill` reads one by its name. A
skill fits by the words it shares with the goal and, with the memory's embeddings on, by how close it is to the goal in
meaning (at least `min_similarity` under `[memory]`), the two rankings fused as the notes' are; bge-m3 reads Turkish as
well as English, so a goal in one language is given the skills described in the other, while by words alone it shares
none with them. Each skill is embedded once and kept while Dot runs; when the skills must be picked and the embedding
service fails, the run fails to start, as it does when its notes cannot be searched. A skill that shares only some of
the goal's words is kept, as a goal can ask for more than one kind of work. Dot always looks in `<home>/skills` and in
the `skills/` folder of every plugin; `[skills] dirs` lists more directories (each holds skill folders; `~` expands).
Skills written for Claude Code load unchanged: Dot reads only `name` and `description` of the frontmatter, so other keys
(a date or a nested map under `metadata`, a list of `allowed-tools`) do not leave a skill out. To use your own, put
`dirs = ["~/.claude/skills"]` under `[skills]` in your `config.toml`; Dot reads nothing there until you do. An entry may
also be one skill folder (`~/.claude/skills/<name>`), to take some skills only: `~/.claude/skills` can hold skills
written for Claude Code's own tools, such as the superpowers bundle or the skills synced from claude.ai. The skills you
wrote for Claude Code are then the playbook a campaign can work from.

Dot comes with three skills of its own, for work that has to know this Mac: `excel` (reading and changing a workbook
with openpyxl, getting the results of its formulas from LibreOffice or Excel, which openpyxl does not compute, and
driving Excel with AppleScript), `word` (changing the text of a Word document with python-docx without losing its
formatting, and driving Word with AppleScript) and `onedrive` (where OneDrive keeps the folders it syncs,
`~/Library/CloudStorage/OneDrive-*`, the files it keeps only online, and what a change there does in the cloud). They
are read after every other skill, so a skill of yours with the same name is used instead of one of them, and
`builtin = false` under `[skills]` leaves them out. A skill's instructions name Dot's own Python, which has openpyxl,
python-docx and python-pptx, as `{{dot_python}}`: the `skill` tool puts the path of that Python there, quoted for the
shell. `dotx doctor` says which of Excel, Word and LibreOffice are installed, at which version, and counts the folders
OneDrive syncs by the kind of account (personal, work or school, shared libraries), never by their names, which hold the
organization's.

A plugin is a folder in the Claude Code layout: `.claude-plugin/plugin.json` (its name), `skills/<name>/SKILL.md` and
`.mcp.json` (`{"mcpServers": {...}}`; `${CLAUDE_PLUGIN_ROOT}` is the plugin folder). Their skills and MCP servers are
used; hooks, commands and agents are not. Dot always looks in `<home>/plugins`; `[plugins] dirs` lists more directories
(each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, skills at the start of
every run.

`dotx skills list` shows every plugin and skill found, and anything left out and why; `dotx doctor` has a row for each.

**Found on this Mac.** Dot also finds, read-only, the skills and MCP servers that the Mac's other agents have set up:
the skills of Claude Code (`~/.claude/skills`), of the plugins installed in Claude Code (each one's `installPath` in
`~/.claude/plugins/installed_plugins.json`, with its `skills/` and `.mcp.json`) and of Codex (`~/.codex/skills`), and
the MCP servers of Claude Code (those for every project, `mcpServers` in `~/.claude.json`; a project's own are not
read), Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`), VS Code (`Code/User/mcp.json`) and
Codex (`mcp_servers` in `~/.codex/config.toml`). Their skills are found as Dot finds its own: the skill folders of a
folder, and those of the folders in it a few levels down. Each is found once by its agent and its name, which is how an
import names it: a plugin installed for you and for a project is read from your install, the one Claude Code uses
outside a project. Only what names a thing is kept: a skill's name, description and folder, and a server's name, its
transport and what it reaches, the base name of the program it starts or the host of its URL, when that is a plain name.
A server's arguments, environment and headers, and the rest of its URL, can hold keys and tokens: they are never kept,
logged or shown, not even in the reason a server is left out. A skill is marked when Dot reads its folder, a server when
Dot has one under the name it would give it (`<plugin>-<server>` for a plugin's). What cannot be read is listed apart
with why: a file, a skill whose `SKILL.md` Dot cannot use, a skill named as one before it in the same agent's folders (a
second copy reached through a link is not counted), a plugin named as one of another marketplace before it, a server
that names no command or URL to use, whose command does not start with the name of a program (a URL, or a variable set
before it), whose URL names no host, or whose entry holds values of the wrong kind (told by their keys).
`dotx skills list` shows them after Dot's own, with `?` for any character a terminal would act on; Settings › Skills
shows them under "Found on this Mac", read apart from the learned skills; and `GET /api/skills/elsewhere` returns them.
Runs do not see them: Dot uses one only once you add it (below), or name its folder in `[skills] dirs` or
`[plugins] dirs`.

**Adding what is found.** Under Found on this Mac, Add to Dot asks first, then adds a skill or an MCP server to Dot;
Remove asks, then takes it back (`PUT /api/skills/elsewhere` with `kind`, `agent`, `name` and `added`;
`dotx skills list` marks what you added). A skill is copied into `<home>/skills/<name>`, without its hidden files and
with a hidden marker of where it came from, and every run is offered it at once. The copy is what you said yes to: it
does not follow the agent's later changes (remove it and add it again to take them), and a run outside `yolo` may read
it but never change it. Remove deletes that copy and nothing else. A server is added as a line of
`<home>/added-servers.json` that names its agent and its name, with a fingerprint of its entry (a SHA-256 hash) and
nothing it is started with; a run outside `yolo` may read that record but never change it. From Dot's next start
(`dotx service install` restarts the daemon), Dot reads the entry from that agent's file each time it starts and starts
the server as the agent does, with its command, arguments and environment, or its URL and headers, under the name Dot
gives it (lower case, `-` for `_` and spaces); its tools are offered and approved as those of an `[[mcp]]` server. An
entry that changed after you added it is not started, and the page says so until you remove the server and add it again:
a change made in the agent's file, by a run or by anyone else, never starts on your earlier yes. One that is gone from
its agent's file, or that Dot cannot start as it is written by then, is left out and logged too. What you added that its
agent no longer has is listed apart, under Added, no longer set up, with Remove; Dot keeps such a skill's copy until you
remove it. What cannot be added says why instead of offering the button: a skill whose name Dot has already, or that
Dot's skills folder holds; a skill folder that holds a link, something that is neither a file nor a folder, or more than
2000 files or 50 MB; a server of a plugin, which comes with its plugin (name the plugin's folder in `[plugins] dirs`); a
server whose name does not fit Dot's rule for server names (a lower-case letter, then lower-case letters, digits and
hyphens, at most 24 characters) or is taken, or that uses a variable its agent fills in: any `${...}` in its command,
arguments or URL, or one in its environment or headers other than `${NAME}`, which Dot looks up like an API key.

**Importing several at once.** Import from other apps, at the head of Settings › Skills, opens a checklist of what is
found that Dot can add: the skills by agent, each with its description, ticked at first, then the MCP servers, each
with its agent and what it starts or reaches, not ticked, as a server starts a program of this Mac; Choose all ticks
or clears every one, and a line says how many others Dot has already or cannot add (Found on this Mac says why). The
button is off until what the other agents set up is read and something of it can be added. Import N adds what is
ticked in one request (`POST /api/skills/elsewhere/import` with `items`, each `kind`, `agent` and `name`, at most 500
and each once), each as Add to Dot would, from one look at the other agents' files: what is added is said in a toast,
and what is not, with its reason, in one toast each, as when two agents have a skill of the same name and the first
is copied. Cancel adds nothing.

**Finding help.** Before it takes a hard way (writing a script or a program for the work, clicking through an
application or a site, trial and error in the shell), a run is told to call `find_help` with its need in a few plain
words, and to call it again once a way has failed twice. It searches the skills the run is offered, the tools of the MCP
servers that the run's mode allows and, when the run can use `apple_script`, the applications that AppleScript can
drive: those whose Info.plist names a scripting dictionary or turns AppleScript on, in `/Applications`,
`/System/Applications` (each with its `Utilities`), `/System/Library/CoreServices` and `~/Applications`, each told by
the commands and classes of its dictionary's own suites (the suites that many applications share are ranked, not
listed). It ranks them by the words they share with the need (BM25; case, accents, common words and plurals do not
matter; one that scores less than half as well as the best one is left out) and, with the memory's embeddings on, by how
close they are to the need in meaning, as the skills a run is given are, and lists the best eight, each with how to use
it: `skill` with its name, the MCP tool by its name, or `apple_script` with `tell application`, and `sdef` with the
application's path, which prints its whole dictionary. With `elsewhere = true` under `[skills]` (the default), it also
searches the skills and MCP servers found on this Mac that you can add, each told with how to ask you to add it: a run
never adds one itself. When the embedding service fails, it says so and lists what the words found. With nothing found,
it says what to try next. It changes nothing.

**Learned skills.** With `playbooks = true` in `[agent]` (the default), a task run of its own that ended done (its
answer checked, or its goal check found the outcome achieved) after at least `skill_min_steps` (4) acting steps (every
tool call but the ones that plan, ask, remember, consult, read a skill, find help or finish) is followed, once it has
ended, by one call of the review model that distils how it got there: from the acting steps (each tool call with its
arguments and the start of its result; failed calls marked) the model names the kind of work in a few words, writes the
steps that worked, one sentence each, with the values that change from goal to goal as parameters in angle brackets, and
a one-sentence description. The skill is a folder `learned-<kind>/SKILL.md` under `<home>/playbooks` with "Kind of
work", "When", "Steps", "Parameters" and "Evidence" (the goal, every run it was learned from with its acting steps, the
date), plus a `playbook.json` that keeps the kind and counts its use; the folder is written whole under a hidden name
and then takes its own. The model is shown the kinds of the skills the run's owner keeps (those of the run's site first)
and names the run's work by one of them, word for word, when it is that work, so a later run of the same kind (the same
kind, or one close in meaning when embeddings are on) updates the skill rather than writing another: the steps are the
newer run's, the evidence keeps every run. A run updates its owner's skills alone (Dot's runs Dot's, an agent's runs
the agent's); another owner's skill of the same name keeps its folder, and the new one takes the next free name
(`learned-<kind>-2`). Later runs see it under Skills like any skill (its source is `learned`) with a note that it is
advice from Dot's own work, to follow with their own parameters and judgement, and read it with the `skill` tool. Every
run that ended done or failed counts the learned skills it read; one read by two runs in a row that then failed (none
ended done between them) is retired, moved under `<home>/playbooks/.retired`, and no longer offered. A review model that
fails, or a folder that cannot be written, leaves a `playbook_failed` event on the run; a written or updated one a
`playbook_written` event and a retired one a `playbook_retired` event. A run that read content third parties can author
(a web page, a message, a result of an MCP server) is distilled from its own steps alone: the model sees each call and
whether it worked, never what it returned nor the run's answer; a skill that names a web address, an account or an
e-mail address the run's own goal and calls never did is not kept (`playbook_failed` says why), and the skill says
where it comes from (`read_outside` in its record). The skill never takes an instruction from a page as a step, and
never holds a secret, a one-time code, an e-mail address or a home path. Settings › Skills lists the learned skills
(kind, where, the runs, how they fared, the retired ones apart, an agent's with its name and Share) with Drop, each list
in a box that keeps its size and scrolls inside past its first rows (item 218); `dotx playbooks list` shows them with
their use, `dotx playbooks show NAME` prints one and `dotx playbooks drop NAME` removes one, active or retired.
Delegated runs, chats and evals (which always run without learned skills) write none. `dotx stats` counts the skills
written, retired and read, and compares, per host, the runs that read a skill with the runs on that host that did not
(act calls and seconds a run).
