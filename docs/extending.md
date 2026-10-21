# Apps, MCP servers, skills and plugins

## Customize › Plugins

**Customize** (items 278, 289, 316 and 401), which [Connect apps](#connect-apps) leads to since item 492, has two tabs: **Plugins**, the services Dot
works with, and **Skills**, the skills it reads ([Skills and plugins](#skills-and-plugins)). The old address `#/connect`
leads to Plugins.

**Plugins** lists 65 services with a search, **Connected** and **All** (each with its count), in sections: Popular,
Code, Design, Data, Research, Communication, Productivity, Business, Support and This Mac (item 492). Each tile has the service's mark, what Dot does with
it, how it connects (MCP · Sign in, MCP · Key, MCP · Open to all, MCP · On this Mac, MCP · On this Mac · Key,
AppleScript, Dot's browser or Synced folder), where it stands, and a **+** that takes its first step; a tile opens the
service's own page: each way it connects, with where it stands and its step, the tools of its MCP server once it is
ready, and where its data goes. **Check again** reads them all anew. `GET /api/plugins` returns the listings
(`connections/directory.py`).

Each tile's mark is the service's own logo, in its own colours, on a tile that is white in both colour schemes (a dark
logo such as GitHub's or X's reads in the dark scheme too). The logos are files of `static/icons/plugins`: from
[gilbarbara/logos](https://github.com/gilbarbara/logos) (CC0), and, for the five brands it draws as a wordmark alone
(Stripe, Box, Wix, Zapier and Webflow), Simple Icons' symbol in the brand's colour (version 16.35.0, CC0); the
folder's LICENSE names the files, the source and the commit they came from, and the brands' guidelines. A mark
identifies its service and nothing more. The applications of this Mac (Mail, Calendar, Notes, Reminders, Messages,
Contacts, Excel, Word, PowerPoint, Outlook and OneDrive) show the icon of the application you have installed, read from
its bundle when the page is drawn (`GET /api/connections/{id}/icon`, a PNG; `connections/app_icons.py`), since Apple's
and Microsoft's artwork is not Dot's to ship; one that is not installed keeps its own mark, a logo, an icon of the
interface or letters (DeepWiki, Context7, Jina, Canva and Outlook have no logo to show). A server added by hand wears
the mark of the service it is one of, and the composer's menus draw a server and its tools with the same mark (item
477).

### Connect apps

**Connect apps** (item 492), at the foot of the sidebar right of the cog of Settings, opens the same services in a dialog over the
page, wearing the marks of the first three MCP servers that are ready. It has a search, a chip for each section,
**Suggested for you** (the popular services), then each section's first six tiles with **See all**, and **N connected**,
which lists what Dot is connected to. A tile shows one thing: its first step as a button (**Add**, **Sign in**,
**Allow**, **Open System Settings**), or where the service stands once it is connected, or **Not tried yet**. A tile
opens the service's own page in the dialog, as on Customize, with a way back. A service that takes a key asks for it
there, beside where to get one. **Customize** in the dialog leads to the page, which keeps what the dialog leaves out:
the accounts, your own MCP servers and the skills. On a phone the dialog takes the screen, and the button is a round
one at the foot of the list of chats. The services, where they stand and their steps are the same code on both
(`static/js/catalogue.js`).

A service none of whose ways was tried says **Not tried yet** and offers no step: Vercel and Canva (their MCP servers
let in approved clients only; Canva refused Dot's on 2026-10-19), and Asana and Box (clients registered by hand). Figma
reads through Framelink's server with your access token instead of Figma's own, which refused Dot's registration, and
Atlassian either through its own server, signed in (your organization's admin may have to let it in), or through
`mcp-atlassian` with an API token (item 413). Google and Slack work in Dot's browser; their pages name their MCP servers
as not tried, since they sign in only clients registered by hand. Any MCP server can still be added by its address
(below).

The ways a service connects:

- **AppleScript** (This Mac): Mail, Calendar, Notes, Reminders, Messages, Contacts, Excel, Word, PowerPoint and Outlook.
  macOS lets an app send Apple events to another only once you have said yes to its question (Privacy & Security ›
  Automation), so a tile says **Allowed**, **Not asked yet**, **Refused**, **Not checked** or **Not installed**. macOS
  tells about an application only while it runs: while one is closed, its tile keeps the last answer macOS gave about
  it (item 288), kept for the program macOS asks for Dot, and says **Not checked** only when there is none. **Allow**
  asks macOS, which shows its question on the Mac; an application that is not open is opened hidden first, without
  coming to the front. The page waits a minute for your answer, then says that the question waits on the Mac. After a
  no macOS does not ask again: **Open System Settings** opens the Automation pane, and the service's page says what to
  switch on there, under the program macOS asks for Dot: Dot for the service, else the app Dot was started from
  (Terminal, iTerm, Claude).
- **Synced folder**: OneDrive, whose folders (`~/Library/CloudStorage/OneDrive-*`) Dot's `onedrive` skill works in. It
  says **Syncing** once a folder of it is there; else **Open** opens OneDrive to sign in.
- **Dot's browser**: Google, Microsoft 365, GitHub, Slack, Notion, LinkedIn, X and Instagram. A tile says **Signed in**
  when that browser holds a cookie the site keeps only for a signed-in visitor, read by host and name and never by
  value: from Chrome while it runs, else from a copy of its profile's cookie file. **Sign in** opens the sign-in page in
  a tab of Dot's browser on the Mac. With My Chrome (Settings › Browser) Dot works in your own Chrome, where you are
  signed in as you always are; a browser without a window (`headless = true` under `[browser]`) has none to sign in
  in, which the service's page says. A sign-in is told by its cookie alone: a session the site ended stays **Signed
  in** until its cookie expires.
- **MCP server** (item 279): Notion, Linear, Sentry, Cloudflare, Stripe and Todoist, which sign in with your account,
  Supabase and monday.com too (item 408), and GitLab, PostHog, Zapier, Webflow, ClickUp, Postman, Netlify, Apify, Miro,
  Heroku and Atlassian (item 413: Dot's own client registered with each and each opened its sign-in page to it on
  2026-10-19), DeepWiki, Microsoft Learn, Context7, Exa, Cloudflare Docs and Jina, open to all, and the services that
  take a key (**MCP · Key**): GitHub (a personal access token), Intercom, Neon, Hugging Face, Wix, Firecrawl and Tavily;
  a few run on this Mac (**MCP · On this Mac**), started by Dot with `npx` or `uvx` and talked to over standard input
  and output: Memory, Sequential Thinking, Fetch, Playwright, Chrome DevTools and AWS Documentation, and with a key
  (**MCP · On this Mac · Key**) Brave Search, Figma (Framelink), Perplexity, ElevenLabs, Mapbox, Grafana and Atlassian
  (API token); a value the owner knows, such as a site's address, is asked for with the keys, without a link. A service
  that takes a key asks for it under its tile and on its page, with where to get one; Dot keeps each in its `.env` (mode
  0600) under `MCP_<SERVICE>_<NAME>`, written once the add is accepted, and the server's headers or environment refer to
  it as `${...}`, so the value is in no record and leaves the daemon only to the service; **Remove** takes the key out
  of `.env` with the service's own server, and nothing else does (`keep` in `routes_mcp.py` says so): a try that fails
  leaves the key for **Try again**, an add after a failed one writes it anew, and a daemon's restart forgets a failed
  try but not its key. The addresses, keys and commands are as the services' own docs read on 2026-10-19
  (`mcp/catalogue.py`). **Add** tries the server first and keeps it only once it answered with its tools, which go to
  the runs that start next, without a restart. A server that asks for a sign-in gets one in the browser you are using:
  its tile says **Waiting for your sign-in**, and its page has **Open the sign-in page**, the service's own page, which
  sends you back to Dot's `/oauth/callback` on the address you added it from (the Mac's, or your phone's through
  `[server] reach`), to a page that names the service and its mark and says Dot is finishing the sign-in, because the
  service's code is traded for its tokens after that page; then the tile turns **Ready**. This is MCP's own authorization: OAuth with PKCE, Dot registering itself
  with the service as a client without a secret. Dot keeps the tokens in `mcp-sign-ins/<server>.json` (mode 0600) and
  renews them before they run out, after a restart too; a run never signs in, so a server whose sign-in is gone is left
  out with words that bring you back here (**Sign in again**). **Remove** takes a server added here out, with its tools
  and its sign-in, and takes one added from another agent of this Mac back from Dot, stopped at once (item 497; the
  agent keeps it). A server of a plugin goes with its plugin: **Remove plugin** stops the plugin's servers now, its
  skills leave the runs that start after, and its folder in Dot's plugins goes to the Trash, from which it can be put
  back (`DELETE /api/installed-plugins/{name}`); a plugin read from a folder of `[plugins] dirs` is taken out of the
  config instead. A plugin whose folder went without Dot still lists the servers Dot runs for it until Dot starts
  again, and Remove plugin then stops them; a server of the same name added since is the owner's, not the plugin's. A
  server of Dot's config is the owner's to take out of `config.toml` (its `[[mcp]]` block).

**Choose tools** (item 426): a server added here that offers more tools than a model gets at once (30) is left out, and
its row says so; its **Choose tools** opens the list of every tool it offers with what each says it does, of which you
tick 1 to 30. **Save** connects the server with those and keeps the choice with it, so a restart offers the same ones; a
server that does not answer leaves the choice unwritten and the button stays. A server you signed in to (Linear,
Postman) lists and connects its tools with that sign-in, so the choice asks for none again (item 499).

**Your own MCP server**, at the foot of All: any [MCP server](#mcp-servers) by its name and address, a URL or the
command that starts it on this Mac, added as the services are. Connected lists, besides the services, every server Dot
has from its config, a plugin or another agent (Customize › Skills › Found on this Mac), with **Ready** and its tools,
**Left out** and why, or **Off**.

## Accounts: Google's and Microsoft's own sign-in

Customize › Plugins opens on **Accounts** (item 422), above the list and not on a service's own page: Google (Gmail,
Google Calendar) and Microsoft (Outlook, Microsoft 365), each with its own mark, through their official sign-ins, with
the client you register with each, as each asks of an app that runs on a computer. **Sign in** opens the provider's page in a new tab; you say yes there, the provider sends the browser back to
the daemon's `/oauth/callback` on this Mac's own address with a code, which Dot trades for tokens with PKCE, and the
card follows until the account is there, named by its address; **Sign out** takes it away. A sign-in starts only from a
page on this Mac (the provider comes back to the Mac's loopback address, which a phone does not reach). An account's
access token is refreshed with its refresh token once it is about to run out.

The tab the provider sends you back to is a page of Dot's own look (item 437), light or dark as the Mac is: it names the
account with its address and says how the sign-in came out, signed in, or not signed in because you said no, the
provider refused (with the provider's own words, such as `invalid_client`), or the browser came back after more than 10
minutes. The tab opens where the link opens: in the browser you use, or, from the desktop app, in the browser macOS
hands it to, which may be Dot's own Chrome when that is the one running; the page carries its look (the interface's
sheets in its one style, under a policy that names their hash, and its pictures as `data:` addresses) and asks the
daemon for nothing but its faces, so it is drawn there as anywhere (item 448). It has no link into the interface, which
Dot's own Chrome never opens; Dot's own window on this Mac already shows the account. A page that is loaded again after
its sign-in was used says that the sign-in has ended, and shows nothing the address carried.

The clients, once: at Google, enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
and the [Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com), name the app **Dot**
under [Google Auth Platform › Branding](https://console.cloud.google.com/auth/branding) (App name, and a logo if you
like: Google's page shows the name of the project's app when you say yes, so a project made for something else asks in
that name), create a **Desktop app** client at [Google Auth Platform ›
Clients](https://console.cloud.google.com/auth/clients), and add your address as a test user under
[Audience](https://console.cloud.google.com/auth/audience); at Microsoft, register an app named **Dot** (the
registration's name is what Microsoft's page shows) at [Entra ›
App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) for any
organisation's and personal accounts, with `http://localhost/oauth/callback` as a redirect URI of **Mobile and desktop
applications** (Microsoft compares the path, not the port) and public client flows allowed. Then save the three values
where Dot reads its keys, the Keychain's `dot-agent` service, each command asking for its value twice without showing
it:

```bash
security add-generic-password -U -s dot-agent -a DOT_GOOGLE_CLIENT_ID -w
security add-generic-password -U -s dot-agent -a DOT_GOOGLE_CLIENT_SECRET -w
security add-generic-password -U -s dot-agent -a DOT_MS_CLIENT_ID -w
```

Google is asked for `gmail.modify` and `calendar` (and who you are), Microsoft for `Mail.ReadWrite`, `Mail.Send`,
`Calendars.ReadWrite`, `User.Read` and `offline_access`. The tokens of each account are in the login Keychain under the
service `dot-oauth`, in pieces short enough for a line of `security`'s own prompt (Microsoft's run to some kilobytes):
the item `google:<address>` says which of two slots holds them and how many there are (`a2`), the items
`google:<address>#a0`, `#a1`… hold the pieces, and new tokens go to the other slot before that item names it, so that
tokens cut off half-written leave the ones before them whole. They are written through that prompt so that no token is
on a command line, and the list of accounts, with no secret in it, is in the store. A sign-in the browser comes back to
after more than 10 minutes is turned away. Nothing logs a token, a code or an address (`GET /api/accounts`,
`POST /api/accounts/<provider>/start`, `DELETE /api/accounts/<provider>/<address>`).

Runs read the mail and the calendar of each account with `mail_search`, `mail_read` and `calendar_events` (item 424,
[Runs and tools](runs.md#mail-and-calendars)); the prompt of a run that has one of these tools names the accounts
signed in (an agent of yours may have none of them, or only some). They only read: an email a run writes for you is a
draft you send from the conversation.

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

`dotx mcp list` starts the servers of `[[mcp]]` tables and those of plugins (not the ones added on the Plugins page),
and shows the tools each offers, or why one does not start. Only tools are used (no resources, prompts or sampling).
A server at a URL may
sign in as MCP's authorization asks (OAuth): `oauth = true` has
Dot send the token it keeps for it, which **Sign in again** on [Customize › Plugins](#customize--plugins) gets, and the servers added
there sign in by themselves. The tool list is read when Dot starts and when a server is added, removed or has its tools
chosen on the Plugins page, for the runs that start after; a server that dies is started again by the next call. A server that is enabled but cannot start (its command, a secret or its working directory is missing, it
quits or does not answer in time, its `allow` list names a tool it does not offer, two of its tools would share a name,
or the input schema of one of its tools cannot be handed to a model: a reference that leads nowhere, or a tree whose
nodes hold nodes, which `allow` can leave out) is left out, and Dot starts without its tools: Settings › System names it
with the reason, `dotx run`, `dotx resume` and `dotx chat` say so before they begin, and `dotx doctor` warns of a
missing command or secret. It is tried again when Dot starts again; `enabled = false` stops trying it. Plugins add their
servers too (see [Skills and plugins](#skills-and-plugins)); their names become `<plugin>-<server>`. MCP results are
untrusted content, and the stderr of a stdio server goes to `<logs>/mcp-<name>.log`. A tool whose name's first verb
sends or posts (`send_email`, `slack_post_message`, `reply_to_thread`, `create_post`) is a message for you whatever its
server declares (`external_send` and `message_send`): it waits for your yes in every mode that asks, and no rule of
yours turns that off (item 429); a `yolo` run asks nothing and sends it, its record keeping the reason (item 443). Any other tool that its server declares read-only (`readOnlyHint`) is taken at its word and runs
as a read, with no flag; any other counts as one that may change things (`destructive`) and send to the world
(`external_send`), unless its server says it does not.

## Skills and plugins

**Customize › Skills** (item 317) lists the skills Dot reads as cards, two a row (one on a phone), each with what it is
for and that Dot has it, with a search and, past a section's first six cards, See N more. **Personal** holds yours
(Dot's skills folder and the `[skills] dirs` folders), those imported from the Mac's other agents (with Remove) and the
learned ones (below, with Share and Drop); **System** holds those that come with Dot and those of its plugins. **Save
from a chat** writes the skill of a chat that ended done (`POST /api/runs/{id}/skill`). Under Personal, Found on this
Mac and Import from other apps (below). The old address `#/settings/skills` leads here.

A skill is a folder of instructions for one kind of work, in the Agent Skills layout: `<name>/SKILL.md` with a YAML
frontmatter (`name`, `description`, and Dot's own `icon`, the name of one of the interface's icons, item 408: a skill
without one, or with one the interface lacks, shows the icon its words choose, else a spark) and optional `scripts/`,
`references/` and `assets/`. Every model is given, in its instructions, the `name: description` line of each skill its
run is offered, and reads the rest with the `skill` tool when a task matches; instructions longer than one tool result
come in parts, each saying where the next one starts, so none is cut out of the middle. A run offered more than `listed`
under `[skills]` (20) is given, in its instructions, those that fit the first words of its chat (the chat's title) best,
the same at every message of the chat so that the provider's cache of the instructions holds (item 404), at most that
many, and is told how many more there are; under each message stand up to six more that fit the message and the goals of
the latest earlier runs of its thread, as its notes are chosen, the best fit first: `find_help` searches them all, and
`skill` reads one by its name. A skill fits by the words it shares with the goal and, with the memory's embeddings on,
by how close it is to the goal in meaning (at least `min_similarity` under `[memory]`), the two rankings fused as the
notes' are; bge-m3 reads Turkish as well as English, so a goal in one language is given the skills described in the
other, while by words alone it shares none with them. The embedding of each skill is made once, kept in the store across
restarts and made as the daemon starts (item 404; one no search asked for in thirty days goes); when the skills must be
picked and the embedding service fails, or the embeddings are still being made, the run lists them by their words and
goes on, as it recalls its notes by words then. A skill that shares only some of the goal's words is kept, as a goal can
ask for more than one kind of work. Dot always looks in `<home>/skills` and in the `skills/` folder of every plugin;
`[skills] dirs` lists more directories (each holds skill folders; `~` expands). Skills written for Claude Code load
unchanged: Dot reads only `name`, `description` and its own `icon` of the frontmatter, so other keys (a date or a nested
map under `metadata`, a list of `allowed-tools`) do not leave a skill out. To use your own, put `dirs =
["~/.claude/skills"]` under `[skills]` in your `config.toml`; Dot reads nothing there until you do. An entry may also be
one skill folder (`~/.claude/skills/<name>`), to take some skills only: `~/.claude/skills` can hold skills written for
Claude Code's own tools, such as the superpowers bundle or the skills synced from claude.ai. The skills you wrote for
Claude Code are then the playbook a campaign can work from.

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
used; hooks, commands and agents are not. A server of `.mcp.json` may also carry Dot's own `allow`, `startup_timeout_s`
and `call_timeout_s`, as an `[[mcp]]` table does, so a plugin whose server offers many tools names the ones the model
gets and how long it has to start and to answer. Dot always looks in `<home>/plugins`; `[plugins] dirs` lists more
directories (each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, skills at the
start of every run.

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
`dotx skills list` shows them after Dot's own, with `?` for any character a terminal would act on; Customize › Skills
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
(`dotx service restart` restarts the daemon; a run in `yolo` asks for `dotx service restart --when-idle`), Dot reads the
entry from that agent's file each time it starts and starts the server as the agent does, with its command, arguments
and environment, or its URL and headers, under the name Dot gives it (lower case, `-` for `_` and spaces); its tools are
offered and approved as those of an `[[mcp]]` server. An
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

**Importing several at once.** Import from other apps, at the head of Customize › Skills, opens a checklist of what is
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
ended done between them) is retired, moved under `<home>/playbooks/.retired`, and no longer offered.

A skill whose name starts with `taught-` is one the owner showed Dot once (item 423, Teach a task in the composer):
they did the task in a tab, the review model wrote its steps from what they did and from their words, and they read
the draft and saved it or threw it away. It stands behind no run, so its record says `taught` and no failure retires
it: whether it still fits is the owner's to say, and a run of its kind writes a `learned-` skill of its own rather
than rewriting theirs. Its trust is the owner's reading of that draft and nothing else: the steps a page put in front
of them (a link's text, a label, a page's own call of the recorder's binding) are in the draft they approved, so a page
that lies reads as a strange step in the panel, not as something withheld later. It holds no value typed into a field,
and no secret field's name, which the recorder leaves out before the draft is written. A review model that
fails, or a folder that cannot be written, leaves a `playbook_failed` event on the run; a written or updated one a
`playbook_written` event and a retired one a `playbook_retired` event. A run that read content third parties can author
(a web page, a message, a result of an MCP server) is distilled from its own steps alone: the model sees each call and
whether it worked, never what it returned nor the run's answer; a skill that names a web address, an account or an
e-mail address the run's own goal and calls never did is not kept (`playbook_failed` says why), and the skill says
where it comes from (`read_outside` in its record). The skill never takes an instruction from a page as a step, and
never holds a secret, a one-time code, an e-mail address or a home path. Customize › Skills lists the learned skills
under Learned (kind, where, the runs, how they fared, Retired on a retired one, an agent's with its name and Share)
with Drop; `dotx playbooks list` shows them with
their use, `dotx playbooks show NAME` prints one and `dotx playbooks drop NAME` removes one, active or retired. A run is
offered a skill learned from third-party content, or of unknown origin, with its lines that name an address, run a
command or reach a path in the home folder or the Mac's own folders withheld; `dotx playbooks trust NAME` offers one
with all its steps, until a run that reads third-party content rewrites them.
Delegated runs, chats and evals (which always run without learned skills) write none. `dotx stats` counts the skills
written, retired and read, and compares, per host, the runs that read a skill with the runs on that host that did not
(act calls and seconds a run).
