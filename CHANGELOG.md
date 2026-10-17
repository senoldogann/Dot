# Changelog

Dot has had no release yet: it is built on `main`, and each change is described in its pull request. From the first
release on, this file lists what each one changes, the newest first.

## Unreleased

The first public version of Dot, an always-on autonomous agent for macOS.

- **Runs** work on a goal until it is done. They use a shell and the file system, the web, a Chrome profile of their
  own (browser use), the screen, mouse and keyboard (computer use), AppleScript and sub-agents. See
  [Runs and tools](docs/runs.md) and [Browser and computer use](docs/browser-and-computer-use.md).
- **Models** come from Ollama, OpenAI, OpenCode Go, Anthropic or OpenCode Zen's free models. Dot's own work has fast,
  review and vision models, and a run can ask helper models or move to standby ones. See
  [Models and providers](docs/models.md).
- **A policy** records everything a run does. In the approval modes `ask` (the default) and `auto` it hands money and
  credentials to you and asks before a risky action; in `yolo` only your hand-off rules stop a run. See
  [Safety and privacy](docs/safety.md) and [Autonomy](docs/autonomy.md).
- **A web UI** comes from a daemon on the Mac. It is also an app on a phone's home screen, reached through Tailscale,
  with notifications. See [The daemon, the web UI and the desktop shell](docs/daemon.md) and
  [Dot on the phone](docs/phone.md).
- **A memory** lasts between runs. There are **schedules**, **agents** of your own, and **campaigns** that keep working
  on a goal, through restarts and days, until its outcome is verified. See
  [Memory and schedules](docs/memory-and-schedules.md) and [Agents and campaigns](docs/agents-and-campaigns.md).
- **MCP servers, skills and plugins** add tools, and apps connect with your own accounts. **Customize** lists the 34
  services Dot works with, how each connects and the skills it reads. See
  [Apps, MCP servers, skills and plugins](docs/extending.md).
- **`dotx setup`** checks the Mac, stores a provider's key and chooses the model. The home of the web UI says when the
  default model cannot answer. See the [README](README.md#install).
