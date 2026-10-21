# Changelog

Each release of Dot lists here what it changes, the newest first. A change not released yet goes under **Unreleased**,
at the top, until a release names it; how a release is made is in [Releasing](docs/development.md#releasing).

## Unreleased

- Schedules: **Notifications** in a schedule's form says how the end of its runs is told on your phone and desktop:
  always (as before, else `[scheduler] notify`), only when it fails or needs you, or never; a run you start with
  **Run now** is always told (item 461).
- The side panel's Run tab is plain: Details show the status, the model and its reasoning, the approvals, the tool calls,
  the tokens and the duration (goal mode and the compactions only when there are some), then the conversation's files;
  the run's mode, start and id, how it was set up and its cost fold under **More**, closed the first time, which counts
  a set-up warning beside its title, and a figure of none has no line (item 452).
- A message sent from a chat that is over no longer jumps the chat up and back down: it stays at its foot until the
  next run's page takes its place. A chat whose run is over opens with every message already there, instead of
  filling in as its stream is read (item 454).
- `[browser] idle_close_s` closes Dot's own Chrome, with its Playwright driver, once nobody used it for that long, so it
  does not keep its memory all day; while it runs macOS takes it for your Google Chrome, and a click on Chrome in the
  Dock brings Dot's forward instead of opening yours. It is off (0) unless you set it; Dot's Chrome starts again signed
  in as before (Incognito keeps nothing), since it keeps its session cookies (item 454).
- Schedules: a repeating schedule whose runs fail so many times in a row (its **Switch off after**, or `[scheduler]
  stop_after_failures`; never by default) is switched off and you are told once; its row says how many failed in a
  row (item 458).
- Schedules: **Run now** starts a schedule's run at once, switched on or off, as its firing would run, without
  changing when it fires next; **Firings** lists its last runs, at its time and by hand (item 455).
- Settings › Rules: a rule's arrows move it up or down the order the rules are checked in, and a rule that an earlier
  rule fits wherever it fits says it never decides, with the number of the rule that decides in its place. The note
  above the rules now says what a yolo run keeps of the protections that are always on: only a research run's limit
  (item 449).
- Dot's own Chrome no longer signs you out of sites that keep their sign-in in a session cookie whenever it starts
  again: Default and the profiles you added keep those cookies across restarts, as a real browser keeps a sign-in;
  Incognito still keeps nothing (item 453).
- In `yolo`, Dot asks you nothing (items 433 and 443): not about what every other mode refuses (a read of where your
  secrets are kept, data sent out, a step in Dot's own window or on Chrome's remote debugging, a change of Dot's own
  files outside the undo journal), nor about money, a credential, a message or a post in your name: the step runs and
  its record keeps the reason it would have asked for. Only a research run's limit stands.
- An approval's card stays in its own chat: the home lists a run that waits under Waits for you without its card,
  and once you allow it the card leaves the chat with the line that asked for it. A refused card stays, saying so
  (item 434).
- Every service of Customize › Plugins wears its own logo in full colour on a tile that reads in both colour schemes,
  and the Mac's applications (Mail, Calendar, Notes, Reminders, Messages, Contacts, Excel, Word, PowerPoint, Outlook,
  OneDrive) show the icon of the application you have installed; OneDrive is found by either of its two bundle ids, the
  Accounts card shows Google's and Microsoft's colours and only heads the list, and the "+" of a tile keeps its width on
  a phone (item 437).
- The page you come back to after signing in to Google, Microsoft or an MCP service now looks like Dot, light or dark as
  your Mac is, names the account or service, says in the provider's own words why a sign-in failed, and has a button
  back to Plugins; the docs say to name the app Dot on Google's consent screen (item 437).
- The end of a command Dot left running in the background no longer shows in the composer's Queued list, with Steer,
  edit and remove: a run at work is told at its next step, Dot starts a run to tell a chat where none works, and a
  stopped chat is not left paused with it (item 437).
- The daemon's own log errors are fixed: the Settings › Phone QR codes no longer crash, an OpenRouter account with few
  credits is asked again with an answer it can pay for (and a 402 says "out of credits"), a daemon that refuses its
  configuration names the setting in `dotx service status` and `dotx service install`, and failed searches, banner
  clicks and request answers keep their context or stop warning for ordinary traffic (item 437).
- Small fixes across the web UI: Settings › System's rows end in a right-pointing chevron, Diagnostics counts "1
  process" and fades its table's edge on a phone, Memory's heads and learning lines read right, Schedules and Rules
  close with a cross, an agent's page has its name as tab title and heading and opens its editor at the top, a phone's
  composer words fit its line, Controls' colour names stay on one line, and the sidebar's section labels read at 4.5:1
  (item 437).
- A run that finished while its page was open comes back at rest, not with the done tick again, and a template card's
  Tools row is a 40 px target on a phone; the phone browser tests no longer flake on a busy Mac (item 437).
- A reply the model service's content filter stops (`content_filter`) no longer fails the run as "cut short" after
  asking twice more: the run moves to its standby model at once, or, with none, ends saying that the filter stopped it
  and that another model would answer (item 437).
- `dotx service restart` restarts the daemon now (it stops as at a stop, then the command waits for the health check),
  and `dotx service restart --when-idle` returns at once and restarts it once no run works, the run that asked included:
  a one-shot launchd job that unloads itself and gives up after 2 hours; asking again while one is on its way changes
  nothing. A run's command may ask only for the latter (the restart now and `service install` refuse in a run), and the
  shell tool tells the model to, never to load a launchd job of its own, which launchd started again every 30 seconds.
  `dotx service status` says whether a restart waits and `dotx service uninstall` cancels it (item 441).
- A chat you go to from the side menu opens at its foot, on its newest message: one whose run ended while you were in
  another chat no longer lands on the top of its answer and writes it in again, and one whose run is at work no
  longer stands the composer's height above its end on a phone (item 446).
- Every run, a chat's too, is told that there is always a way: it does not give up on a step while a way is left
  untried and says that something cannot be done only after trying it, naming each way it tried; a chat does not end
  its turn announcing a step it could take now. No way goes around a refusal, a denial or your decision (item 446).
- A message sent to a chat on the phone shows at once on the desktop, and one sent on the desktop on the phone: every
  page that shows the chat's newest run goes on at the run it started, also once a phone put away is back or loads the
  page again, and what was typed there and not sent goes with it; a page of an earlier run stays (item 451).

## 0.1.0 (2026-10-20)

The first public version of Dot, an always-on autonomous agent for macOS.

- **Runs** work on a goal until it is done. They use a shell and the file system, the web, a Chrome profile of their
  own (browser use), the screen, mouse and keyboard (computer use), AppleScript and sub-agents. See
  [Runs and tools](docs/runs.md) and [Browser and computer use](docs/browser-and-computer-use.md).
- **Models** come from Ollama, OpenAI, OpenCode Go, Anthropic, OpenRouter or OpenCode Zen's free models. Dot's own work
  has fast, review and vision models, and a run can ask helper models or move to standby ones. The default models are
  cloud models of Ollama, which run on Ollama's servers: a run's conversation leaves the Mac unless each role is given a
  local model. See [Models and providers](docs/models.md) and
  [What leaves this Mac](docs/safety.md#what-leaves-this-mac).
- **A policy** records everything a run does. In the approval modes `ask` (the default) and `auto` it asks before a
  risky action. In every mode, `yolo` too, money and credentials are handed to you and a message or a post in your name
  needs a yes (the review model's in `auto`), and no rule of yours turns that off; a `yolo` run also asks you before it
  reads your secrets. See
  [Safety and privacy](docs/safety.md) and [Autonomy](docs/autonomy.md).
- **A web UI** comes from a daemon on the Mac. It is also an app on a phone's home screen, reached through Tailscale,
  with notifications. See [The daemon, the web UI and the desktop shell](docs/daemon.md) and
  [Dot on the phone](docs/phone.md).
- **A memory** lasts between runs. There are **schedules**, **agents** of your own, and **campaigns** that keep working
  on a goal, through restarts and days, until its outcome is verified. See
  [Memory and schedules](docs/memory-and-schedules.md) and [Agents and campaigns](docs/agents-and-campaigns.md).
- **MCP servers, skills and plugins** add tools, and apps connect with your own accounts. **Customize** lists the 65
  services Dot works with, how each connects and the skills it reads, and adds the 45 MCP servers of its catalogue with
  a sign-in, a key or a server run on this Mac. See [Apps, MCP servers, skills and plugins](docs/extending.md).
- **Accounts** of Google and Microsoft sign in through their own pages, their tokens in the Keychain, and runs read
  their mail and calendars (Gmail, Google Calendar, Outlook) through the providers' APIs. See
  [Apps, MCP servers, skills and plugins](docs/extending.md#accounts-googles-and-microsofts-own-sign-in).
- **SnapShots**: both Command keys pressed together in any app capture the window in front into the open chat's box,
  with a flash, and bring Dot forward. See [The daemon, the web UI and the desktop shell](docs/daemon.md).
- **`dotx setup`** checks the Mac, stores a provider's key and chooses the model. The home of the web UI says when the
  default model cannot answer. See the [README](README.md#install).
- **The desktop app**, `Dot_0.1.0_universal.dmg` of the release, for Apple silicon and Intel, signed with a Developer ID
  and notarized by Apple: Dot in a window of its own, with a menu bar item and ⌘⇧Space. It looks for a newer Dot as it
  starts and from **Check for Updates…**, and installs one on your yes, once its signature is checked. See
  [The daemon, the web UI and the desktop shell](docs/daemon.md#the-desktop-shell).
