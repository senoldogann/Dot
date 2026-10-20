# Changelog

Each release of Dot lists here what it changes, the newest first. A change not released yet goes under **Unreleased**,
at the top, until a release names it; how a release is made is in [Releasing](docs/development.md#releasing).

## Unreleased

- In `yolo`, Dot no longer asks you before it reads where your secrets are kept or sends data out, which every other
  mode refuses: the step runs and is recorded. Money and credentials are still handed to you, and a message or a post
  in your name still waits for your yes (item 433).
- An approval's card stays in its own chat: the home lists a run that waits under Waits for you without its card,
  and once you allow it the card leaves the chat with the line that asked for it. A refused card stays, saying so
  (item 434).

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
