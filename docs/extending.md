# Apps, MCP servers, skills and plugins

## Customize

**Customize** (items 278, 289, 316, 401, 542 and 545), whose Connectors [Connect apps](#connect-apps) opens, has three
tabs: **Plugins**, the gallery of the plugins Dot installs ([Customize › Plugins](#customize--plugins)); **Skills**, the
skills it reads ([Skills and plugins](#skills-and-plugins)); and **Connectors**, the services Dot works with
([Customize › Connectors](#customize--connectors)). Each opens with the same head (item 545): a card lit by the theme's
accent with the tab's title, a line on what it holds, its search and its controls, and a few of its marks scattered
beside them, which a phone leaves out. `#/customize` opens Plugins. The old address `#/connect` leads to
Connectors, and so does a service's page at the address it had before item 542, `#/customize/plugins/<service>`, since
the gallery's plugins took that name; but for the four services whose gallery plugin has the same name (Cloudflare,
Supabase, MongoDB and Hugging Face), where that address opens the plugin's page.

## Customize › Plugins

**Plugins** (item 542) is the gallery of the plugins Dot installs, after Qoder's page: a head that says what a plugin
is, with the search (it reads the names, the publishers, the kinds and the names of the skills); **New & noteworthy**,
the plugins the gallery features; and **All plugins**, with **All**, **Featured** and **Installed** (each with its
count) and an order, **Popular** (the stars its repository had when it was pinned) or **Newest** (when the gallery took
it). A plugin's card has its mark, its name with a check when its publisher makes what its skills are about, who
publishes it, its kind and how many skills it brings, what it does, and **Install**, or **Installed** with a Remove. A
card opens the plugin's own page: the skills it brings, the repository and the commit Dot pins, its licence, its stars
and since when the gallery has it. A plugin with no logo wears a skill's puzzle piece (item 537) on a tile of its
kind's colour: Development blue, Design pink, Product violet, Data teal, Business orange, Writing green. On a phone New
& noteworthy is a row that scrolls sideways. `GET /api/plugin-gallery` lists the plugins with where each stands
(`available`, `installed`, or `taken` when a plugin of Dot's has its name) and the plugins Dot reads that are not the
gallery's, which **Installed** lists under "Not from the gallery", with a Remove for one in Dot's own plugins folder.

The gallery (`plugins/gallery_list.py`) holds 22 plugins of 201 skills, each from a repository under the MIT or Apache
2.0 licence, pinned to a commit and to the hash of the files of its skills:

- featured: Superpowers (Jesse Vincent's: brainstorm, plan, write the tests first, debug systematically), UI/UX Pro
  Max, Frontend Design, Product Design, Product Management and Engineering;
- Anthropic's too: Skill Creator, Data Analysis, Marketing, Sales, Finance, Customer Support, Creative Design, Web App
  Builder, MCP Builder and Internal Comms;
- from the makers of what they are about: Apollo GraphQL, MongoDB, Supabase, Cloudflare and Hugging Face with their
  logos, and Redis.

Left out: Vercel's skills (their repository names no licence), Anthropic's document skills (docx, pptx, xlsx and pdf,
whose licence lets them be used, not passed on) and the plugins of Qoder's page that are Qoder's own (Architecture
Visualization, Cloud Agents, Phone Use): Product Design reviews designs and Engineering does the architecture work.
`uv run pytest tests/smoke/test_gallery_reachable.py -m internet` downloads every plugin again and checks that its
pinned commit still unpacks to the files pinned.

**Install** (`POST /api/plugin-gallery/{id}`, one at a time) downloads the archive of the pinned commit from GitHub
(`codeload.github.com`, at most 100 MB in two minutes) and unpacks the plugin's skill folders alone into a hidden folder
of Dot's plugins, refusing a path that leads out of it, a link, and anything that is neither a file nor a folder. It
checks their files against the hash the gallery keeps, and only then gives the folder the plugin's name, with a
`.claude-plugin/plugin.json` and a `gallery.json` that say what it is. A plugin whose files are not the ones pinned, one
that cannot be downloaded and a name Dot has a plugin of already are refused (409) and leave nothing behind. Dot then
reads its plugins again: the runs that start after are offered the new skills, with no restart. **Remove** moves the
plugin's folder to the Trash (item 497) and Dot reads its plugins again, so its skills leave the runs that start after.
When a newer Dot moves the pin of a plugin you installed, the runs keep reading the skills of the commit installed, and
the card's badge reads **Older version** in place of Installed (the state `outdated`, with `installed_commit`; the
plugin's page says which commit its files are from): Install is refused while the folder is there, so Remove it and
Install it again to take the new pin (item 558).
Two plugins may bring a skill of the same name (UI/UX Pro Max and Product Design a `design-system`, Product Management
and Marketing a `competitive-brief`): the first read is offered, and `dotx skills list` names the other as left out. A
skill is text the run's model reads as instructions: the gallery takes repositories of known publishers and pins them,
so what is installed is what was looked at, and a skill acts only through the run's tools, under the same rules and the
same asks as anything else the run does ([Safety](safety.md)).

## Customize › Connectors

**Connectors** lists 156 services with a search, **Connected** and **All** (each with its count) and **Check again** in
its head, the accounts under it, and the sections as chips (item 545), one showing its section alone: Popular, Password
managers (item 493), Code, Design, Data, Research, Communication, Productivity, Business, Support and This Mac (item
492), each titled with its count. Popular stands up as Plugins' New & noteworthy does, four cards a row (two on a
narrower window); on a phone its cards are rows as the other sections' are, the mark beside the words (item 604). Each
card has the service's mark, what Dot does with it, how it connects (Sign in, Key, Open to all, On this Mac, On this Mac · Key; and by Connected app,
Mac app, Dot's browser, Synced folder or 1Password), where it stands, and its first step
in words on the line of its name (**Add**, **Allow**, **Sign in**, **Connect**), and lifts under the pointer; a card
opens the service's own page: each way it connects, with where it stands and its step, the **Actions** of its connected
app once it is ready, and **Where its data goes**. **Check again** reads them all anew. `GET /api/plugins` returns the listings
(`connections/directory.py`).

Each tile's mark is the service's own logo, in its own colours, on a tile that is white in both colour schemes (a dark
logo such as GitHub's or X's reads in the dark scheme too). The logos are files of `static/icons/plugins`: from
[gilbarbara/logos](https://github.com/gilbarbara/logos) (CC0), and, for the brands it draws as a wordmark alone
(Stripe, Wix, Zapier, Webflow, Mixpanel, HubSpot and Sanity), not at all (Calendly, Semrush and the twelve
more of item 535) or with an element that may reach past the file (Next.js), Simple Icons' symbol in the brand's
colour (version 16.35.0, CC0), DuckDB's in black, as its yellow does not show on the tile;
the folder's LICENSE names the files, the source and the commit they came from, and the brands' guidelines. A mark
identifies its service and nothing more. The applications of this Mac (Mail, Calendar, Notes, Reminders, Messages,
Contacts, Excel, Word, PowerPoint, Outlook and OneDrive) show the icon of the application you have installed, read from
its bundle when the page is drawn (`GET /api/connections/{id}/icon`, a PNG; `connections/app_icons.py`), since Apple's
and Microsoft's artwork is not Dot's to ship; one that is not installed keeps its own mark. A service with no logo
(DeepWiki, Context7, Jina, Outlook, Mobbin, Parallel, Plain, MailerLite, Klaviyo and 30 of item 535's, Honeycomb, Attio
and Granola among them; Cal.com's symbol is its wordmark, too small to read) wears the icon of the interface it was
given, else the plug, in white on a tile of its colour lit from its top left, as an application's icon is (item 542);
one with no colour of its own takes one of six by its id, the same wherever it shows. A server added by hand wears the
mark of the service it is one of, and the composer's menus draw a server and its tools with the same mark (item 477),
on their own light ground.

**Item 535** brought 84 MCP servers more, each tried first as Dot reaches it (`mcp/catalogue.py` says how and what was
left out with why). Code: Vercel (whose server lets Dot register now), Sourcegraph, grep.app, Xcode (Xcode's own
`xcrun mcpbridge`, which needs Xcode open, and XcodeBuildMCP), AWS, Azure, Alibaba Cloud, DigitalOcean, Kubernetes,
Pulumi, Firebase, Convex, Prisma, Expo, Buildkite, Snyk, Socket, Jam, LogRocket, Honeycomb, Axiom, Better Stack,
PagerDuty, incident.io, LaunchDarkly, Statsig, Braintrust, Clerk, WorkOS, Stytch, Globalping, Next.js, Svelte, Astro,
Nuxt UI, Shopify, Apollo GraphOS, OpenAI's docs, Ref and AWS Knowledge (beside AWS's documentation). Design: Framer,
Lucid, Whimsical, Excalidraw, shadcn/ui, Magic UI, LottieFiles, HeyGen, Sanity, Contentful and WordPress.com. Data:
MongoDB, DuckDB, MotherDuck, Pinecone, Supermemory and CoinGecko. Research: Kagi, Consensus, MarkItDown and Kiwi.com.
Communication: Resend, Mailgun and DingTalk. Productivity: Dropbox, Shortcut, Productboard, Cal.com, Granola,
Fireflies, tl;dv, Jotform, Tally and Time. Business: HubSpot (by its own server and a private app's token), Attio,
Close, Customer.io, beehiiv, Mercury, Ramp and Greenhouse. Support: Help Scout. A server started on this Mac uses what
this Mac has: Kubernetes your kubeconfig, Azure `az login`, AWS `~/.aws`, Xcode the Xcode that is open; a DuckDB query
reads and writes the files it names and may attach another database, and MarkItDown reads the files you name, as any
server you add reaches what its process reaches. Redis, ClickHouse, Neo4j and Qdrant are not in it yet: each takes an
address and a user besides its password, and the page asks for secrets alone, which Dot keeps in its `.env` and masks
wherever their words appear (`localhost` too); they are added by hand under `[[mcp]]` until the catalogue has settings
that are not secrets. Square waits too: its tools take payments, and no flag marks a payment made through an MCP tool
yet, so a call of one would stop for you only as far as its server declares that it acts. The try that adds a server
started on this Mac gives it five minutes to start (`FIRST_START_S`), as `npx` and `uvx` fetch its package then, which
took from 52 seconds to more than five minutes on a cold cache; the server kept starts within its own
`startup_timeout_s`, as a start of the daemon waits for every server, and finds the package in the cache. `uv run pytest
tests/smoke/test_catalogue_reachable.py -m internet` checks the whole catalogue again by read-only requests: a server
that signs in answers 401 and its authorization server registers a client without a secret with PKCE, one open to all
answers, and a package is on its registry, not deprecated.

**The version of a server started on this Mac is pinned** (item 695). `npx` and `uvx` run the newest release of a
package at each start, with your rights and the keys the server is given, so a release that is compromised or broken
would reach you the day it is published. Each package the catalogue starts is therefore written with one exact version
in `mcp/catalogue.py` (`name@x.y.z` for npm, `name==x.y.z` for PyPI), and a test fails on one that is not. A pin moves
only when someone moves it: `uv run python scripts/catalogue_versions.py` asks the registries and lists the version
pinned and the newest of each, to read what changed and to try the server before the number is raised. A server you
already added keeps the arguments it was added with (`@latest`, or no version), and is still the service's in the list;
what the package pulls in is not pinned with it: npm and PyPI resolve its own dependencies to the newest each one allows
at its start, which only a lock of the server's own would fix;
to move it to the pinned version, remove it and add it again. A server you write under `[[mcp]]` is yours: pin it the
same way (`args = ["-y", "some-mcp-server@1.2.3"]`).

### Connect apps

**Connect apps** (items 492 and 545), at the foot of the sidebar right of the cog of Settings, on the sidebar's own
ground as the cog is, opens Customize › Connectors, which it stands for while it is open, wearing the marks of three
MCP servers that are ready, another three of them every few seconds when more are (item 509). It opened the services
in a dialog of their own until item 545, which gave the page the dialog's chips and its steps in words, so that the
services have one place, in the look of Plugins. On a phone the button is a round one at the foot of the list of
chats. The services, where they stand and their steps are `static/js/catalogue.js`'s.

### 1Password

**1Password** (item 493), under Password managers, lets a run sign in with your logins without its model ever seeing
a password or a one-time code (see [Secrets](safety.md)). Dot reads 1Password through `op`, its command line (`brew install 1password-cli`),
and through a service account, which reads only the vaults shared with it, as 1Password's own guidance has an agent
work:

- **Connect 1Password** makes the vault "Shared with Dot" and a service account that only reads it, with your own
  `op` signed in through the 1Password app (Settings › Developer › Integrate with 1Password CLI), which asks you first.
- **Use a service account key instead** takes one you made at 1password.com, asked for in a dialog (item 615).

Either way Dot keeps the token in its `.env` (`DOT_ONEPASSWORD_TOKEN`), and the page says how many logins the vaults
hold. Move the logins a run may use into the vault; your other vaults stay out of its reach. **Disconnect** forgets the
token; the vault and the service account stay in 1Password, whose page removes them.

While it is connected, a run that may act gets `browser_logins`, the logins' titles and websites, and
`browser_fill_login`, which fills one into the current page. Dot fills only on the login's own site, asks you before
each fill in every mode but `yolo`, and masks the values in everything the browser and web tools give back.

Dot keeps logins, cards and contact details of its own too, in Settings › Vault (item 621, see [Dot's own
vault](daemon.md#settings--vault)); its logins are listed and filled by the same tools, beside 1Password's.

A service Dot has no way to reach is not listed: Canva (its MCP server lets in approved clients only, and its page told
Dot's "Unsupported client" on 2026-10-22), Asana and Box (clients registered by hand) left Connectors with item 542,
where they said **Not tried yet** and offered nothing to do. Vercel's server, which let in approved clients only, lets
Dot register since item 535. Figma reads through Framelink's server with your access token instead of Figma's own,
which refused Dot's registration, and Atlassian either through its own server, signed in (your organization's admin may
have to let it in), or through `mcp-atlassian` with an API token (item 413). Google and Slack work in Dot's browser;
their MCP servers sign in only clients registered by hand. Any MCP server can still be added by its address (below).

The ways a service connects:

- **Mac app** (This Mac): Mail, Calendar, Notes, Reminders, Messages, Contacts, Excel, Word, PowerPoint and Outlook.
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
- **Connected app** (item 279): Notion, Linear, Sentry, Cloudflare, Stripe and Todoist, which sign in with your account,
  Supabase and monday.com too (item 408), and GitLab, PostHog, Zapier, Webflow, ClickUp, Postman, Netlify, Apify, Miro,
  Heroku and Atlassian (item 413: Dot's own client registered with each and each opened its sign-in page to it on
  2026-10-19), Mobbin, Semrush, Parallel Task, Plain, MailerLite, Airtable, Klaviyo, Calendly, Amplitude, Mixpanel, New
  Relic and PayPal (item 505: each one's metadata, read on 2026-10-21, offers a client that registers itself without a
  secret), DeepWiki, Microsoft Learn, Context7, Exa, Cloudflare Docs, Jina and Parallel Search, open to all, and the
  services that take a key (**Key**): GitHub (a personal access token), Intercom, Neon, Hugging Face, Wix,
  Firecrawl and Tavily; a few run on this Mac (**On this Mac**), started by Dot with `npx` or `uvx` (at the exact
  version the catalogue pins, item 695: see above) and talked to over standard input and output: Memory, Sequential
  Thinking, Fetch, Playwright, Chrome DevTools and AWS Documentation, and with a key (**On this Mac · Key**) Brave
  Search, Figma (Framelink), Perplexity, ElevenLabs, Mapbox, Grafana and Atlassian (with a key); a value the owner
  knows, such as a site's address, is asked for with the keys, without a link. A service that takes a key asks for it in
  a dialog (item 615) that its tile's **Add** and its page's open, with where to get one; a key typed and then dropped
  is gone when the dialog opens again (item 559), and the daemon's refusal is said inside it, with the key still typed;
  Dot keeps each in its `.env` (mode 0600) under `MCP_<SERVICE>_<NAME>`, written once the add is accepted, and the
  server's headers or environment refer to it as `${...}`, so the value is in no record and leaves the daemon only to
  the service; **Remove** takes the key out of `.env` with the service's own server, and nothing else does (`keep` in
  `routes_mcp.py` says so): a try that fails leaves the key for **Try again**, an add after a failed one writes it
  anew, and a daemon's restart forgets a failed try but not its key. The addresses, keys and commands are as the
  services' own docs read on
  2026-10-19, and item 505's on 2026-10-21 (`mcp/catalogue.py`). **Add** tries the server first and keeps it only once
  it answered with its tools, which go to the runs that start next, without a restart. A server that asks for a sign-in
  gets one in the browser you are using: its tile says **Waiting for your sign-in**, and its page has **Open the sign-in
  page**, the service's own page, which sends you back to Dot's `/oauth/callback` on the address you added it from (the
  Mac's, or your phone's through `[server] reach`), to a page that names the service and its mark and says Dot is
  finishing the sign-in, because the service's code is traded for its tokens after that page; then the tile turns
  **Ready**. This is MCP's own authorization: OAuth with PKCE, Dot registering itself with the service as a client
  without a secret. Dot keeps the tokens in `mcp-sign-ins/<server>.json` (mode 0600) and renews them before they run
  out, after a restart too; a run never signs in, so a server whose sign-in is gone is left out with words that bring
  you back here (**Sign in again**). **Remove** takes a server added here out, with its tools and its sign-in, and takes
  one added from another agent of this Mac back from Dot, stopped at once (item 497; the agent keeps it). A server of a
  plugin goes with its plugin: **Remove plugin** stops the plugin's servers now, its skills leave the runs that start
  after, and its folder in Dot's plugins goes to the Trash, from which it can be put back
  (`DELETE /api/installed-plugins/{name}`); a plugin read from a folder of `[plugins] dirs` is refused (409), and is
  yours to take out of `[plugins] dirs` in `config.toml`. A plugin whose folder went without Dot still lists the servers Dot runs for it until Dot starts again, and
  Remove plugin then stops them; a server of the same name added since is the owner's, not the plugin's. A server of
  Dot's config is the owner's to take out of `config.toml` (its `[[mcp]]` block). The server that comes with Dot
  (item 583, [below](#mcp-servers)) is listed with a **Built in** chip and a switch on its tile, and **Try again**
  when it was left out: the switch stops it at once, or starts it and offers its tools to the runs that start
  after, and writes `[builtin_mcp] web_research` to `config.toml` (`PUT /api/mcp/servers/{name}/switch`); it
  has no **Remove**, since the switch is how it goes, and it is listed switched off too.
  The sign-in file is named for the server, so a server added again under the name of one that is connected, at
  another address or by another command, forgets the sign-in of the old one first (item 558): its tokens were issued
  for the old address and are not sent to the new, and an address left as it was keeps them. The address the page and
  the API show for a server, and what `GET /api/status`, the log and the terminal say of why it was left out, leave a
  key out: the user and password of a URL, its query, a segment of its path that reads as a key, and an argument
  that follows an option naming a secret (`--api-key`, `--token`) or has a known form of a key are masked, as is what
  the server printed as it failed (`mcp/masking.py`). A server's tools are not chosen (`PUT .../allow`, 409) while Dot
  tries it, as a sign-in again does: the try would write its own list over the choice when it ends.
  Dot's Playwright server starts with `--no-webmcp` (item 514): the tools a page registers through WebMCP, which the
  server offers by default, are that page's words and code, and stay out of a run's tools; a server added before keeps
  the arguments it was added with.

**Choose actions** (items 426 and 524): a server offers a run every tool it lists, however many. With the default `[agent]
tool_surface = "full"` each step of a run carries the definition of every one of them, so a server of many tools makes
every request longer; `tool_surface = "grouped"` keeps a server's tools out of a run until it turns the server's group
on. A run of more than 128 tools starts grouped whatever the setting, as some providers refuse more in one request
([runs](runs.md)). The groups a grouped run has on (those its chat used before, the servers picked for its message, and
those it turns on) may still bring a request past what its provider takes, and the request then fails with the
provider's error, so cut a big server down here. **Choose actions**, on the row of a server added here that runs, opens
the list of every tool it offers with what each says it does, all of them ticked until you choose, and **All actions**
ticks or unticks them at once. **Save** connects the server with the ticked ones and keeps the choice with it, so a
restart offers the same ones; with every one ticked it keeps no list, and the tools the server adds later are offered
too. A server left out with a list of tools keeps the button, to choose the list again (it may name a tool the server no
longer offers). A server that does not answer leaves the choice unwritten and the button stays. A server you signed in
to (Linear, Postman) lists and connects its tools with that sign-in, so the choice asks for none again (item 499).

**Your own connected app**, at the foot of All: any [MCP server](#mcp-servers) by its name and address, a URL or the
command that starts it on this Mac, added as the services are, from the card's **Add connected app** button, which asks the
name and the address in a dialog (item 615; a name the daemon refuses is said inside it, with what you typed still
there). Connected lists, besides the services, every server Dot
has from its config, a plugin or another agent (Customize › Skills › Found on this Mac), with **Ready** and its tools,
**Left out** and why, or **Off**.

## Accounts: Google's and Microsoft's own sign-in

Customize › Connectors opens on **Accounts** (item 422), above the list and not on a service's own page: Google (Gmail,
Google Calendar) and Microsoft (Outlook, Microsoft 365), each with its own mark, through their official sign-ins, with
the client you register with each, as each asks of an app that runs on a computer. **Sign in** opens the provider's page
in a new tab; you say yes there, the provider sends the browser back to the daemon's `/oauth/callback` on this Mac's own
address with a code, which Dot trades for tokens with PKCE, and the card follows until the account is there, named by
its address; **Sign out** takes it away, and gives Google the refresh token back, which ends the grant there (item
558; Microsoft has no such call for a client like Dot's: take Dot's access away at your Microsoft account, under the
apps that may use it). A refresh under way ends first, so that no token it fetched is written back after the sign-out.
The list below the card is drawn once the card has read the accounts (item 511), so the tiles do not move down under a
finger as its rows come. A sign-in starts only from a page on this Mac (the
provider comes back to the Mac's loopback address, which a phone does not reach). An account's access token is refreshed
with its refresh token once it is about to run out.

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

Google is asked for `gmail.readonly` and `calendar.readonly` (and who you are), Microsoft for `Mail.Read`,
`Calendars.Read`, `User.Read` and `offline_access`: the account tools only read (item 558; before, Google was asked for
`gmail.modify` and `calendar` and Microsoft for `Mail.ReadWrite`, `Mail.Send` and `Calendars.ReadWrite`, and an account
signed in then keeps that grant until you sign in again). A tool that writes will ask for what it needs, and be one that
asks before it sends. The tokens of each account are in the login Keychain under the
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
allow = ["search", "read_note"]             # offer only these tools; empty = all of them, however many
description = "my notes: search them, read one, add to one"  # what it is for: one line, 200 characters at most
enabled = true
startup_timeout_s = 60.0
call_timeout_s = 120.0
# cwd = "/Users/me/notes"                   # the folder it starts in, a whole path; the workspace when left out

[[mcp]]
name = "docs"
url = "https://mcp.example.com/mcp"       # a remote server; transport = "http" (default) or "sse"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }  # ${NAME} as above; headers are never logged
```

**A server comes with Dot** (item 583): `web-research`, the MCP server of the package `dotagent-web-research` (1.4.9,
item 653), which Dot's own package depends on, so that every install has it ready and on. Its search is a rung of Dot's
own `web_search` since item 655, which Dot asks with the query a run gave `web_search` (Settings › Web search orders it
among the others), and its other tools read what a run asks for, each named `mcp__web-research__<tool>`. The search
(Brave or Serper first when their key is in the server's environment, then DuckDuckGo, Google (read in Camoufox), Yahoo
and DuckDuckGo's lite page, a provider that walls (Google: after a bot check that Camoufox or the minimised Chrome saw
and the other did not read past, or that a lesser path, the HTTP request or headless Chrome, saw when neither could ask
and no browser read Google) or fails three times running resting ten minutes, then twice as long for each failure after
a rest, up to an hour, until it answers again, and when the first fails and every other rests, the one nearest its
rest's end is asked once, in each angle of the search, even one resting after a bot check, which a wall then makes rest
twice as long; each result's date with how sure it is, read from the page's head for news), `web_scrape` (one page, or
two to ten in one call, as Markdown; a page behind a wall is read once more in Camoufox, a Firefox made to look like a
person's browser (not on an Intel Mac, which has no Camoufox), and else another way, from the archive or another
outlet's copy, which the answer names; `archive: true` reads the Internet Archive's copy; a page its scripts draw is
waited for, and one that is still an empty shell, or that is what its site answers any address with, says so:
`rendered_shell`, `soft_404`, `looks_missing`), `web_map` (a site that redirects is mapped at the host it lands on only
when that is the same site: a `www.` flip, `http` to `https` or the same registrable domain; else the one page it leads
to, with `redirected_to`), `web_extract`, and a crawl in the background (`web_crawl_start`, `web_crawl_status`,
`web_crawl_results`, `web_crawl_cancel`; an ended crawl says why it stopped, `stopped_by`, and how many links it left,
`not_visited`). A run is offered those and not the search or `web_health` (the state of its search providers, for the
doctor): the server's `allow` list names them, and a tool a later version adds is offered once Dot names it, after its
arguments are judged. Dot starts it with the servers of `[[mcp]]`, as it starts those: on the Python the daemon runs on
(`python -P -m web_scraper.mcp_server`: `-P` keeps the workspace, where a run's commands write, off its import path),
with `WEB_TOOLS_DATA_DIR=<home>/web-research`, where it keeps its crawl jobs, a folder a run's commands cannot change,
as they cannot change the other files Dot loads and runs, and with `PYTHON_DOTENV_DISABLED=1`, so that it reads no
`.env` into its environment: neither Dot's own key file nor one that a program left in the workspace, its working folder
(the browser it starts for a page that needs JavaScript runs with that environment, and a `NODE_OPTIONS` in it is a file
run outside the sandbox). While you have a list of sites set (`[browser] allowed_hosts`, or `blocked_hosts` beyond the
defaults), it starts with `WEB_TOOLS_SEARCH_RESOLVE_DATES=off` and `WEB_TOOLS_SCRAPE_RECOVER=off`: the lists bind the
addresses a call holds, and these two would have the server read pages of its own accord, the heads of a search's
results and another copy of a walled page (item 623). The seven tools a run is offered (item 655) are judged like any
server's: the five that declare themselves read-only run as reads, the other two by their flags (`web_crawl_start` may
reach out, `web_crawl_cancel` stops something), and what they return is untrusted content in its fence.

**Dot's own rules judge its calls.** A call of the server's tools is judged by the rules of the tool of Dot's own that
does the same, in the same functions, so that a rule added to `web_search`, `web_fetch` or `web_crawl` binds these too:

- *The words of a search*: a run searches with Dot's own `web_search` alone since item 655, whose query is judged
  (one with a key in it, an `sk-…`, `AKIA…`, `ghp_…` or `xox…` key or a private key, earns the flag `exfiltration`,
  which the policy refuses in every mode but yolo, and one of more than 400 characters is refused) before its
  web-research rung sends the server that query and the number of results, and nothing else.
- *The addresses*, which are the `url` or `urls` of `web_scrape` and the `url` of `web_extract`, `web_map` and
  `web_crawl_start`, as the address of `web_fetch` and `web_crawl` is: checked against `[browser] allowed_hosts` and
  `blocked_hosts` and the places Dot refuses (this Mac, Dot's own interface), and refused with the same words. The
  server is handed the address as it was checked, with its scheme, and an address of more than 2,000 characters, or
  one with a key in it (in its query string, say), earns `exfiltration`.
- *A search that reads the pages it found* (the server's `read`) cannot be asked: the rung sends no `read`, so the
  pages a search chose are read only by `web_scrape`, whose addresses are judged.
- *A `web_scrape` with `archive: true`* reaches `web.archive.org` whatever address it names: while you have a list set,
  that site is judged as an address is, and the call is refused where your lists leave it out (item 623).

Every other string a tool takes is worked on inside the server and sent nowhere: a filter of the addresses it found, a
CSS selector, the name of a crawl job, a date or a language code.

**What those rules do not reach.** What Dot's own tools do not reach either: a secret that is not in the shape of a key
(a password, a token of another kind) and a key cut in pieces over several arguments or calls; a run marked untrusted is
also refused a step whose words carry a secret that Dot holds (see *Outside content in yolo* in [Safety](safety.md)).
And what the server does that those tools do not: a redirect it follows to another host (since 1.4.5 a map or a crawl
whose address, `robots.txt` or sitemap redirects to another site reads only the one page it leads to and says
`redirected_to`; from 1.4.0 to 1.4.4 it read that site, its sitemap and its pages as the site's own); the links a crawl
follows on the site it starts on (and on its subdomains, with `include_subdomains`); the search engines a search asks,
which it chooses; and, while you have no list set, the Internet Archive (`web.archive.org`), which `archive: true` asks
for the copy of a page. A crawl outlives its run: `web_crawl_start` keeps crawling in the server after the run that
started it has stopped, up to its page limit, until it ends or the daemon stops (the server stops with it);
`web_crawl_cancel` stops it. Where those limits matter, `[builtin_mcp] web_research = false` leaves the server out. A
server of your own that replaces it is not checked: its tools are not known to Dot.

It starts when its package can be imported (a checkout that did not install it has no such server),
`[builtin_mcp] web_research` is on (`true` by default) and no other server has its name. To turn it off, use its
switch on [Customize › Connectors](#customize--connectors), which stops it at once and writes the setting, or write
`[builtin_mcp] web_research = false`: the table is not `[mcp]`, which TOML cannot have beside the `[[mcp]]` tables.
To use your own checkout of the server instead, add a `[[mcp]]` table named `web-research`: a server of that name from
any place (a plugin's, one added from another agent, one added on Connectors before) replaces it, and the built-in
stays out. Connectors refuses a new server of that name while the built-in is there: name yours otherwise and switch
the built-in off. `dotx doctor` has a row for it, and [What leaves this Mac](safety.md#what-leaves-this-mac) says
what it sends.

**What the model is told of a server** (item 659). When the tools of a run come in groups (`[agent] tool_surface =
"grouped"`, or a run of more than 128 tools, [Runs](runs.md)), each server's tools are a group of their own,
`mcp__<server>`, that is off until the model turns it on, and the instructions list it in one line: `- mcp__rea-mcp: the
tools of the MCP server rea-mcp (mcp__rea-mcp__search, …)`. A name such as `rea-mcp` or `grep-app` tells the model
little, so the line says what the server is for, from the first of these that has words:

1. *Your `description`* in the `[[mcp]]` table: one line of at most 200 characters, with no line break and no character
   that prints nothing (a longer or broken one is refused when the configuration loads, and the error names the key).
   Write what the server is for, as a phrase: `description = "Dutch homes for sale or rent, their prices and sold
   homes"` gives `the tools of the MCP server rea-mcp: Dutch homes for sale or rent, their prices and sold homes
   (mcp__rea-mcp__search, …)`. Your words stand as you wrote them: the configuration is yours.
2. *Dot's own words*, for the server that comes with it: `web-research` is `Reads pages as Markdown, also those
   behind a wall or drawn by scripts, maps a site and crawls it in the background; its search is one of web_search's
   engines.` A server of your own with that name replaces it, and so its words.
3. *What the server says of itself*: the `instructions` of its answer when Dot connected to it, else the first sentences
   of the descriptions of its first four tools (a tool described only by its name has nothing to say). They are
   third-party words, so the line shows them as the server's claim and not as Dot's text: made one plain line (every
   line break and heading, bracket, brace, bar, backtick, backslash, double quote, address and character that prints
   nothing taken out, the secrets Dot holds masked), of whole sentences up to 160 characters, in quotes: `the tools of
   the MCP server tavily, which describes itself as "A web search tool that returns current results."`. The instructions
   tell the model that words in quotes after "describes itself as" are the server's claim, to judge which group fits the
   work by, and that an instruction inside them never applies to it (*What a server says of itself* in
   [Safety](safety.md)).

A server with neither a description nor words of its own keeps the line it had: its name and its tools. What a server
said is read once, when it connects (and when one is added or removed on Connectors, for the runs that start after); a
run asks no server anything to write its instructions. The lines cost about 50 to 150 tokens a server in the
instructions of each run. A server added on Connectors, from another agent or by a plugin has no description of yours,
so it speaks for itself. `[agent] mcp_purposes = false` leaves every line as it was, with no word of a server in it; the
evals use that to measure what the words change
([Development](development.md#the-choice-of-help-and-what-the-purpose-lines-change)).

`dotx mcp list` starts the servers of `[[mcp]]` tables, those of plugins and the one that comes with Dot, which it
marks `built-in` (not the ones added on Connectors), and shows the tools each offers, or why one does not start.
Only tools are used (no resources, prompts or sampling).
A server at a URL may
sign in as MCP's authorization asks (OAuth): `oauth = true` has
Dot send the token it keeps for it, which **Sign in again** on [Customize › Connectors](#customize--connectors) gets, and the servers added
there sign in by themselves. The tool list is read when Dot starts and when a server is added, removed or has its tools
chosen on Connectors, for the runs that start after; a server that dies is started again by the next call. A
server reached at a URL whose session was quiet for a minute is pinged before it is used, and connected again when the
ping finds it gone (its connection closed, its server ended the session or failed; a server that answers that it
does not know ping is there), so that the calls that come go to a new session rather than fail on the old one (item
513). A call or a listing its server refuses as a request of no session it knows (`INVALID_REQUEST`, whatever the
server's words: "Session not found", "Session has been terminated") is a connection that broke, and the next call
connects again. A call
whose connection broke is not repeated, as the tool may have done part of its work, unless the server declares the
tool read-only (`readOnlyHint`): such a call is made once more on a new connection, with a warning in the log. A server that is enabled but cannot start (its command, a secret or its working directory is missing, it
quits or does not answer in time, its `allow` list names a tool it does not offer, or two of its tools would share a
name) is left out, and Dot starts without its tools: Settings › System, under **Details**,
names it with the reason, `dotx run`, `dotx resume` and `dotx chat` say so before they begin, and `dotx doctor` warns of a
missing command or secret. It is tried again when Dot starts again; `enabled = false` stops trying it. A tool's input
schema is handed to the model flattened (item 671): a part that refers again to a definition its own path already went
through (a tree whose nodes hold nodes, a filter of and, or and not), that nests deeper than twelve levels (a feature
flag's rules within rules, as Vercel's `update_flag` has) or that refers to a definition the schema does not hold is cut
to a loose schema of its kind (an object, a list of objects or the scalar its definition or its list of types says),
whose description says that its parts nest deeper than shown, and the service checks what the model sends there; the
tool's other parameters stay whole. A schema that grows past 5,000 parts as it is written out, or past 32,000
characters once flattened, is refused, as one whose arguments are no object at all: such a tool is left out alone, with
`mcp_tool_left_out` in the log, and the server's other tools serve. A tool the server's `allow` list names, which the
owner asked for, leaves the server out with the reason instead, and so does a server none of whose tools can be used.
Before, any of these left the whole server out. Plugins add their
servers too (see [Skills and plugins](#skills-and-plugins)); their names become `<plugin>-<server>`. MCP results are
untrusted content, and so is an error that a server answers a call with: the model reads its words inside the fence as
data, and the run is marked untrusted (item 551). An answer longer than one result holds is kept whole in
`<workspace>/mcp-results/<server>-<tool>-<time>-<id>.txt` (item 494): the result shows the lines at its start that fit,
inside the fence, and then says where the file is, how long it is and the line to read on from with `read_file`. The
stderr of a stdio server goes to `<logs>/mcp-<name>.log`. A tool whose name's first verb
sends or posts (`send_email`, `slack_post_message`, `reply_to_thread`, `create_post`) is a message for you whatever its
server declares (`external_send` and `message_send`): it waits for a yes in every mode that asks (yours, the review
model's in `auto`), and no rule of yours turns that off (item 429); a `yolo` run asks nothing and sends it, its record keeping the reason (item 443). Any other tool that its server declares read-only (`readOnlyHint`) is taken at its word and runs
as a read, with no flag; any other counts as one that may change things (`destructive`) and send to the world
(`external_send`), unless its server says it does not.

## Skills and plugins

**Customize › Skills** (items 317 and 545) lists the skills Dot reads as cards, two a row (one on a phone), each with
its mark light on a tile of its colour (the colour its name picks, as a plugin's is on Plugins), what it is for and
that Dot has it, under the head of Customize with Personal and System, **Save from a chat** and **Import from other
apps**, with a search and, past a section's first six cards, See N more. **Personal** holds yours
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
many, and is told how many more there are and what they are called (their names, up to sixty, item 659: a skill whose
description shares no word with the goal is still in sight by its name, and `skill` reads it); under each message stand
up to six more that fit the message and the goals of the latest earlier runs of its thread, as its notes are chosen,
the best fit first: `find_help` searches them all, and `skill` reads one by its name. A skill fits by the words it
shares with the goal and, with the memory's embeddings on,
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
directories (each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, and again
when the gallery installs one or Remove plugin takes one out (item 542); skills are read at the start of every run. The
MCP servers of a plugin that came while Dot runs start when Dot starts again.

`dotx skills list` shows every plugin and skill found, and anything left out and why; `dotx doctor` has a row for each.
A copy of a skill (the same name, description and instructions in its `SKILL.md` in another folder, as a sync of the
same skills leaves in `~/.claude/skills`; the folders' other files are not compared, and the first one's are used)
counts once and is not listed, in Dot's skills and under Found on this Mac (item 707). A skill another one has the name
of is listed, and the doctor's row says how many there are without counting them against it: nothing is wrong with them,
and one is offered.

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
arguments and the start of its result; failed calls marked, the newest of them when a run took many) the model names
the kind of work in a few words, writes the
steps that worked, one sentence each, with the values that change from goal to goal as parameters in angle brackets, and
a one-sentence description. A model that gives the steps or the parameters as one text instead of a list is read as the
list it means (a JSON array in a string, or a line to each step), but all the steps run together in one line are no list
of them: it is asked once more, and no skill is saved as one step for that (item 543). A description left out stands as
the first sentence of "When" (at most 200 characters, cut between words), and an answer the model wrote as its whole
text, one JSON object (in a code fence or not), is read as its call: the roles that write or judge what Dot learns (the
lessons, the skills, the owner section, the merges, the harness's proposer, composer and experience writer) are read so,
while a role that lets an action happen (the review of a step, the goal's verdict, a rule drafted from your words)
answers by its call alone (item 614). The skill is a folder `learned-<kind>/SKILL.md` under `<home>/playbooks` (the
folder is the kind in lower-case letters, digits and hyphens: a letter with a mark is the letter, and the few that have
none are spelled in ASCII, so "Şirket yıllık raporu" is `learned-sirket-yillik-raporu`; a skill written before item 612
keeps the folder it has, and a run of its kind still finds and updates it, since the kinds are compared by this spelling
on both sides) with "Kind of
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
ended done between them, and a run that ended done and rewrote its steps starts the count again) is retired, moved
under `<home>/playbooks/.retired`, and no longer offered.

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
whether it worked, never what it returned nor the run's answer, so nothing a page says reaches the skill as a step. The
skill's record says where it comes from (`tainted`, and the runs in `tainted_runs`, which `dotx playbooks show` and
the API show), and it is offered with some of its lines withheld, as below. The model is told never to put a secret, a
one-time code, an e-mail address or a home path in a skill, but only secrets are checked for when it is written: the
values Dot holds, the value of a key that names a secret and the keys and tokens of known forms are masked before the
skill is written, and an e-mail address or a home path the model writes anyway is kept as written (a skill from a clean
run is offered whole; one from a run that read outside content has such lines withheld, found by the one gate for
learned texts, [The harness](harness.md#one-gate-for-every-text-dot-learns)). Customize › Skills lists the learned skills
under Learned (kind, where, the runs, how they fared, Retired on a retired one, an agent's with its name and Share)
with Drop; `dotx playbooks list` shows them with
their use, `dotx playbooks show NAME` prints one and `dotx playbooks drop NAME` removes one, active or retired. A run is
offered a skill learned from third-party content, or of unknown origin, with its lines that name an address (a step
that names a host bare too; the description of a skill may name a site), hold a character that does not print, run a
command or reach a path in the home folder or the Mac's own folders withheld; `dotx playbooks trust NAME` offers one
with all its steps, until a run that reads third-party content rewrites them. A line with a character that does not
print is withheld in every learned skill, a trusted one, a taught one and one from a clean run too, and no other line
is: the rest of the skill stands as it was written (a line of more than 2,000 characters is withheld unread in a skill
that may carry outside content): what nobody could read is not followed. The names that start with `learned-`,
`taught-` or `playbook-` are Dot's alone: a skill of any other folder (yours, a plugin's, one of `[skills] dirs`) with
such a name is left out and reported, since a run is told that a `taught-` skill is the owner's own instruction (item
557).
Delegated runs, chats and evals (which always run without learned skills) write none. `dotx stats` counts the skills
written, retired and read, and compares, per host, the runs that read a skill with the runs on that host that did not
(act calls and seconds a run).

**A higher bar, versions, rest and merging (item 585).** A skill is written only from a run that was no chat-mode run,
did not change Dot itself and whose steps the review model judges a procedure you will likely ask for again: its answer
to the call that writes the skill has one more field, `recurs`, which is required (an answer without it is asked for
again), and a question answered, a lookup or a job done once gives no skill. The skip is logged (`playbook_skipped`,
with the reason). A run changed Dot itself when a step of it, or of a run it delegated (a helper's change is the run's),
was flagged by the policy as a change of Dot's own files (`self_change`) or, of the steps that are not reads, was
flagged `secret_access` or `protected_read` (the shell guard's verdict on a line, which a `yolo` run is let carry out:
it sees a working folder, a link or capital letters that the words of the line do not show), was an MCP call whose
arguments name a place Dot guards or loads, or named one in its own words (its configuration, keys, database, code,
skills or playbooks, as a path in any letter case, or with `~` or `$HOME` for the home folder), the daemon's address, or
in a command Dot's own program (`dotx`, `dotagent`): such a run gives no skill whatever the model says, and the model is
not asked, so that a skill never teaches a later run to do to Dot what a run did. A command, or another step that acts,
which the guard flagged for reaching a secret or the clipboard gives no skill for the same reason; a read, which only
looks, does not. What none of these reads is not seen: a command that builds a path as it runs, in a way the guard does
not flag, and an MCP server that takes a path relative to a folder of its own; the sandbox holds the commands outside
`yolo`, and no sandbox of Dot's holds an MCP server (see [Safety](safety.md)). Your **Save from a chat** is not asked
whether the steps recur, since you chose, but a chat-mode run and a run that changed Dot itself are refused there too,
with why.

A rewrite by a later run, a restore and a merge keep the file they replace, whole, as a version
(`.versions/<n>.md` in the skill's folder, a hidden one, so a run is not told it comes with the skill), write the
record before the file (a disk that fills between the two leaves the older steps under the newer mark, never new
steps under a record that says they carry less of outside content than they do), and the record notes how each
version came to be, the run that made it and whether its steps carry the mark of outside
content, as that version had it: a restore brings back the mark of the version it puts back, and a merge is marked
when any skill it was made from is. Customize › Skills shows **Versions (N)** on a skill that has more than one:
a dialog, the newest first, with how each was made, its mark and the steps it changed from the version before it,
and **Restore**, which asks first and makes the version a new version of its own, so that it too can be taken
back. `GET /api/skills/learned/{name}/versions`, `POST /api/skills/learned/{name}/restore` (`{"version": 2}`),
`dotx skills versions NAME` and `dotx skills restore NAME N` do the same. A skill written before the versions has
one, with no note of how it came to be, and its first rewrite keeps the file as it was.

A learned or taught skill that no run read for `[agent] skill_rest_days` (60) days rests: no run is offered it (its
prompt, the `skill` tool, `find_help` and the composer's picks leave it out), the page shows it as **Resting**, last in
its section, and nothing of it is deleted nor does it retire for that; the read is dated where it happens: when a run
reads the skill with the `skill` tool, or starts with it picked in the composer, the skill's `last_read_at` is written
then, whatever the run is (a chat, a helper's run, a run that is stopped afterwards) and however it ends, at most once
an hour for a skill, under the lock of the playbooks, and a date that cannot be written is logged and never fails the
read. The counts of uses and failures (two failures in a row retire a skill) are another thing and stay where they were:
a task run of its own that ended done or failed counts them. A skill whose record counts uses but not when a run last
read it (written before the date was kept) is given that date on the first night, so that it rests after the days pass
and not at once. **Wake** (`POST /api/skills/learned/{name}/wake`, `dotx skills wake NAME`) offers it again; **Pin**
(`PUT /api/skills/learned/{name}/pin`, `dotx skills pin NAME`) keeps it from resting, and a skill can be put to rest by
hand (`POST /api/skills/learned/{name}/rest`, `dotx skills rest NAME`). Only you delete a skill: **Drop** stays yours,
on the page and with `dotx playbooks drop`. `dotx playbooks list` says which skills rest, which are pinned and which
were rewritten.

Each night the review model is asked about the pairs of learned skills of one owner that are awake, not pinned and
not taught and whose kinds are at least `[agent] skill_merge_similarity` (0.75) close by their embeddings, the
closest first. When it says a pair is one procedure, a second call writes the merged steps, secrets masked, and
the merge waits as a card under **Waiting for your yes**
(`GET /api/skills/proposals`, `dotx skills proposals`) that shows everything a yes would write to the first skill:
its name, description, kind of work, when it fits, steps and parameters, each as the file will keep it (one line) and
against what the skill says now, a line struck through for what goes and marked for what is written, and what happens
to each skill: at most `skill_proposals_per_night` (3) a night, and ten waiting. The description is a line of every
run's instructions, so it is never left off the card. Your **Merge** (`POST /api/skills/proposals/{id}/accept`,
`dotx skills accept ID`) writes exactly those fields as a new version of the first skill, with the runs and the use
counts of both, and puts the other to rest, `merged_into` naming the first; the merged skill is offered to every
agent only when both skills were (sharing is yours to give, skill by skill). Restore on the first and Wake on the
other undo it. A proposal whose skills changed since is
refused (409) and says why; **No** (`DELETE /api/skills/proposals/{id}`, `dotx skills dismiss ID`) drops it, and
the pair is proposed again only after one of them changes. Nothing is written to a skill before your yes.

A run that reads, with the `skill` tool, a learned skill that may carry outside content (its record says so, or is
older than the mark) is untrusted from the read, as one that read a web page, and its lessons are marked; so is a
run you started with such a skill picked in the composer, whose steps are in its first message (item 585).
