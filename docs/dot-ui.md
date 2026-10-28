# The Dot interface, one page at a time

A map for an agent that has to use or change the interface: each view file, what the page is for, and the smoke tests
that hold the three pages an agent is most likely to break. `tests/unit/test_ui_map.py` fails when a view file is added
or removed without a row here.

The shell is `settings.js` (the settings sidebar and tabs); `customize.js` gathers skills, plugins and connectors; the
home page is `runs.js`, and one run is `run.js`.

| Page | View file | What it is for |
|---|---|---|
| Home | `runs.js` | Dot's presence and the composer; one conversation at a time. |
| One run | `run.js` | A run as a conversation: the question, the steps, the answer and the queued message. |
| Agents | `agents.js` | The owner's agent profiles: face, name, role, instructions, tools and model. |
| Team | `team.js` | The day's messages between agents, oldest first. |
| One team | `teams.js` | A team's agents, its name and purpose, and a box to message it. |
| Campaigns | `campaigns.js` | Goals Dot keeps working on in sessions until a check confirms the outcome. |
| Schedules | `schedules.js` | Goals once or on a cron schedule while the daemon runs. |
| Rules | `rules.js` | What Dot may do on its own, what it asks first, and what it leaves to the owner. |
| Settings | `settings.js` | The settings shell and its sidebar; the page is the open tab alone in a Mac window. |
| Appearance | `appearance.js` | Colour schemes as miniatures of Dot's window. |
| Customize | `customize.js` | The hub of what Dot works with and knows how to do. |
| Skills | `skills.js` | Learned and built-in skills, their versions and the owner's changes. |
| Plugins | `plugins.js` | The gallery of plugins Dot can install. |
| Connectors | `connectors.js` | The services Dot works with and where to connect them. |
| Memory | `memory.js` | Notes, semantic and word search, and the lessons handed to runs. |
| Harness | `harness.js` | Self-improvement around the model: the harness and what it changes. |
| Providers | `keys.js` | The model providers and the judge's service. |
| Channels | `channels.js` | The owner's messaging apps as ways to reach Dot. |
| Phone | `phone.js` | Dot on a phone through Tailscale, a step at a time. |
| Vault | `vault.js` | Logins, cards and contact details Dot may type into pages. |
| Trading | `trading.js` | The trading desk: worth, day, limits and open positions. |
| Web search | `websearch.js` | The engines `web_search` asks, in turn. |
| Snapshots | `snapshots.js` | A Command double tap captures the window in front. |
| System | `system.js` | Daemon health, the emergency stop, health checks and provider models. |
| Diagnostics | `diagnostics.js` | The daemon's process and the children it started. |
| Open source licences | `licences.js` | The licences of what Dot uses. |
| Controls | `controls.js` | Every control on one page, for checking a look in light and dark. |
| Theme editor | `themeEditor.js` | A theme of one's own: a name and a palette for each scheme. |

## Smoke tests that anchor the map

These three pages have the most wiring and the clearest failure a person sees; the map test keeps their smoke tests
present.

- `tests/smoke/test_harness_page.py` — the harness page and its controls.
- `tests/smoke/test_memory_page.py` — memory search, notes and the page's state.
- `tests/smoke/test_rules.py` — the rules page and the policy it shows.
