# Dot

Dot is an always-on autonomous agent for macOS. Give it a goal in plain words and it works on it until it is done, with
what a person at this Mac would use: a shell and the file system, the web, its own Chrome profile (browser use), the
screen, mouse and keyboard (computer use), AppleScript, a memory that lasts between runs, schedules, MCP tool servers
and sub-agents.

It runs on models from **Ollama** (local models, and cloud models through the local Ollama server), the **OpenAI** API,
**OpenCode Go**, the **Anthropic** API (the Claude models), **OpenRouter** (the models of many labs on one key) or
**OpenCode Zen**'s free models. A daemon on the Mac serves a web UI and a REST API and runs the schedules, and
`dotx run` and `dotx chat` work in the terminal. Dot runs on macOS only.

By default Dot acts without asking. It stops for what it must not decide alone (money, credentials, public posts,
messages sent for you, privilege, system settings), everything it does is recorded, and it can be stopped at once. Read
[Safety](docs/safety.md#safety) before you let it work on a machine that holds data you care about, and
[What leaves this Mac](docs/safety.md#what-leaves-this-mac): by default its models run on Ollama's servers.

<p align="center">
  <img src="docs/images/mac.png" alt="A finished task in Dot's web UI on a Mac: the goal, the two tool calls it made and its answer, with the chats in the sidebar" width="70%">
  <img src="docs/images/phone.png" alt="The same task on an iPhone, in the dark scheme" width="22%">
</p>

The pictures show a run of Dot's scripted test model, under the name of a real one.

## Requirements

- macOS 15.2 or later (developed and tested on macOS 26, Apple silicon)
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- [ripgrep](https://github.com/BurntSushi/ripgrep): `brew install ripgrep`
- Google Chrome, which the browser tools drive through Playwright
- Xcode's Command Line Tools (`xcode-select --install`), with which the service builds the small app that runs Dot
- The models of one provider at least:
  - [Ollama](https://ollama.com), running. Cloud models (their tag is `cloud` or ends in `-cloud`) need `ollama signin`
    once.
    Searching the memory by meaning needs an embedding model: `ollama pull bge-m3`
  - an API key for OpenAI, OpenCode Go, Anthropic or OpenRouter
  - [OpenCode](https://opencode.ai), for Zen's free models (no key)

## Install

1. Install the tools Dot uses: `brew install uv ripgrep`, [Google Chrome](https://www.google.com/chrome/), and
   Xcode's Command Line Tools (`xcode-select --install`).
2. Get Dot: `git clone https://github.com/senoldogann/Dot.git`, then `cd Dot`.
3. Install its packages: `uv sync`.
4. Set it up: `uv run dotx setup`.
5. Open it: `uv run dotx open` opens the web UI in your browser, signed in.

`dotx setup` does the following, then installs the service and gives the address of the web UI:

- It checks this Mac: the macOS version, ripgrep, Chrome and Xcode's Command Line Tools.
- It asks which provider's models Dot runs on, and stores the provider's API key in Dot's `.env` file (mode 0600) when
  it needs one.
- It offers the provider's models that can call tools. The one you pick becomes the model new chats start on, and the
  model of Dot's own work: the fast and review models, and the vision model when it sees images.

Without a terminal, flags give each answer: `--model provider:model`, `--key-stdin` (the key on standard input) and
`--no-service` (the service is left as it is).

The web UI says the same on its home. While the default model cannot answer, a **Set up a model** card says why: no
key, a refused key, or a provider that is out of reach or does not offer the model. It leads to Settings › Models,
Settings › API keys or `dotx setup`.

## First steps

Type a goal on the web UI's home, or give one in a terminal:

```bash
uv run dotx run "Find the opening hours of the Helsinki Central Library and save them to hours.md"
uv run dotx chat
```

[The command line](docs/command-line.md) has the rest: `dotx doctor`, which checks the whole setup and says how to fix
what is missing, and the flags of `run` and `chat`.

## Dot on the phone

The web UI works on a phone too, as an app on its home screen that notifies you. The phone reaches the Mac through your
own network: with [Tailscale](https://tailscale.com) on both, `tailscale serve --bg 8765` on the Mac and the Mac's name
on the tailnet in Dot's config file. [Dot on the phone](docs/phone.md) has the steps.

## Safety in short

- **Who answers the policy's questions.** Most actions need no answer. For those that do, `ask` (the default) puts the
  question to you, `auto` lets a review model answer, and `yolo` lets the run go on alone: then only your hand-off rules
  stop it. See [Autonomy](docs/autonomy.md).
- **What leaves this Mac.** There is no telemetry. The models get the run's conversation, and the sites, search engines
  and services a run uses get what it sends them. See [What leaves this Mac](docs/safety.md#what-leaves-this-mac).
- **The daemon** listens on this Mac's loopback address alone. Its API needs its token, except for a health check,
  the daemon's proof that it holds the token, and the pairing of a phone. A phone comes in through your own tailnet.
- **A vulnerability** is reported privately, as [SECURITY.md](SECURITY.md) says.

## Integrations

`integrations/` carries ready-made additions. [REA](integrations/rea/README.md) (Reverse Engineer Anything) adds its MCP
server and workflow skill: a run can inspect a shipped app or binary and explain how a feature works, with the evidence
and its limits. [replica-skill](integrations/replica-skill/DOT.md) adds eleven skills that turn an app into a recon map,
a rebuild, a bug hunt and a launch (MIT, by Jake Schincariol).

## Documentation

- [The command line](docs/command-line.md): `dotx doctor`, `run`, `chat` and their flags
- [Models and providers](docs/models.md)
- [Runs and tools](docs/runs.md): how a run works, its tools, delegation
- [Autonomy](docs/autonomy.md): approvals, goal mode and the end of a run
- [Safety and privacy](docs/safety.md): what leaves this Mac, the policy, the sandbox
- [The daemon, the web UI and the desktop shell](docs/daemon.md)
- [Dot on the phone](docs/phone.md)
- [Browser and computer use](docs/browser-and-computer-use.md)
- [Memory and schedules](docs/memory-and-schedules.md)
- [Agents and campaigns](docs/agents-and-campaigns.md)
- [Apps, MCP servers, skills and plugins](docs/extending.md)
- [Configuration](docs/configuration.md)
- [Development](docs/development.md): the tests, the gate, measuring the agent
- [Status and known limits](docs/limits.md)

[CONTRIBUTING.md](CONTRIBUTING.md) says how to propose a change, and [CHANGELOG.md](CHANGELOG.md) what changed.

## License and security

Dot is released under the MIT License ([LICENSE](LICENSE)). The parts it carries from others keep their own licences,
each beside them: the Inter and Geist Mono fonts (SIL Open Font License, `static/fonts`), the icons, which are Lucide's
(ISC, and MIT for those Lucide took from Feather, `static/js/LICENSE-Lucide.txt`), the providers' marks
(`static/icons/providers/LICENSE`), the parts of the appearance settings ported from T3 Code (MIT,
`static/js/appearance/LICENSE`) and the character, a port of bloub (MIT, `static/js/character/LICENSE`).

Report a security problem privately, as [SECURITY.md](SECURITY.md) says, not in a public issue. Dependabot raises
alerts and fixes for dependencies with a known vulnerability, and each week one pull request per kind of dependency
that brings it up to date (`.github/dependabot.yml`: Python, the desktop app's npm and Cargo packages, the CI's
actions); the weekly audit of the CI (`audit.yml`, item 217) stays.
