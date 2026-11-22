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
| Watch | `watch.js` | The Mac's screen, live, whether a task works or not. |
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
| Self-repair | `repair.js` | The problems Dot found in its own record and the Claude Code sessions that fixed them. |
| Providers | `keys.js` | The model providers and the judge's service. |
| Channels | `channels.js` | The owner's messaging apps as ways to reach Dot. |
| Phone | `phone.js` | Dot on a phone through Tailscale, a step at a time. |
| Vault | `vault.js` | Logins, cards and contact details Dot may type into pages. |
| Web search | `websearch.js` | The engines `web_search` asks, in turn. |
| Snapshots | `snapshots.js` | Both Command keys together capture the window in front (a double tap, once on). |
| System | `system.js` | Daemon health, the emergency stop, health checks and provider models. |
| Diagnostics | `diagnostics.js` | The daemon's process and the children it started. |
| Open source licences | `licences.js` | The licences of what Dot uses. |
| Controls | `controls.js` | Every control on one page, for checking a look in light and dark. |
| Theme editor | `themeEditor.js` | A theme of one's own: a name and a palette for each scheme. |

## The design language

One design language on a phone and on a Mac, plain and professional as a developer tool's (items
1324 to 1326). It is made of tokens, so a page inherits it without drawing any of it: **never write a colour, a font
size, a radius or a duration in a view's sheet, nor a gap off the 4 px grid; take a token**
(`tests/unit/test_css_tokens.py` refuses those literals; a width or a height it leaves to the reviewer).

- **Colour** (`css/themes.css`, the neutral look; the other looks keep their palettes and take the shape below). Dark:
  the page `--bg` is true black, the raised surfaces (`--surface`, `--sidebar`: a card, a popover, the composer's
  ground) `#111111`, `--surface-2` and `--surface-3` the steps a hover and a selected row take; borders are white at
  8%. Light: `--bg` zinc-25, `--sidebar` zinc-50, `--surface` white; borders are black at 9%. Text tiers `--text`,
  `--text-2`, `--muted`, `--faint` are zinc and all read at 4.5:1 on every surface. One accent, a calm blue: `--accent`
  fills the primary button, the switch and the focus ring (white on it), `--accent-strong` is the blue that reads as
  text (links, an active label), `--accent-weak` its tint. `--ok`, `--warn`, `--bad` are emerald, amber and red, each
  with a `-weak` tint; colour is for what waits, what failed and what is on, never for decoration. A user's message
  sits on `--msg-me` (one surface step off the page: lighter than it in dark, a shade darker in light), Dot's answers on
  the page. The amber `--brand` is Dot's face and the warm backdrop of Dot's computer, and `--brand-ink` is the ink that
  reads on it (the page's own ink is light in dark); nothing generic takes either.
- **Shape**: a 0.5 rem scale of tokens, one for each kind of box and named for it (`--radius-control`, `--radius`,
  `--radius-menu`, `--radius-bubble`, `--radius-composer`, `--radius-lg`, `--radius-xs`; their values are in `css/app.css`).
  What is fully round takes `--radius-pill`, and its rules are the only list of that: no doc or comment keeps one.
  The same on a phone. A box is drawn by a hairline ring (`--box-shadow`), not a shadow; only what floats (a menu, a
  dialog, a sheet, the composer) has one, and in dark it is a ring.
- **Type**: the system face (`-apple-system`, Inter after it). Sizes `--text-3xs` 10, `--text-2xs` 11 (metadata: an age,
  a count), `--text-xs` 12, `--text-sm` 13, `--text-ui` and `--text-md` 14, `--text-lg` 16, `--text-xl` 18,
  `--text-2xl` 24, each with its `--leading-*`; weights `--weight-text` 400, `--weight-label` 500, `--weight-title` 600.
  A phone has a scale of its own, a step or two above the Mac's (the touch-screen block of `css/app.css`):
  `--text-ui` and `--text-md` are 16 (the body: a field smaller than that zooms iOS), and no text is drawn under 13 px
  there, the two metadata sizes included. The shapes and colours are the Mac's, and what is tapped takes `--touch-target`.
- **Controls**: height `--control-h` 32 px (`--control-h-sm` 28, `--control-h-lg` 36) on a Mac; on a phone what is tapped
  takes `--touch-target` (36 px) and an icon button a ring that makes the 44 a finger hits. Buttons: `.btn` (a hairline
  on a tint of the ink), `.btn.primary` (the accent), `.btn.ghost` (no frame, no fill), `.btn.danger` (red outline,
  `.solid` filled), `.btn.icon-only`; fields: `.input`, `.select`, `.textarea` (an accent border and a 3 px ring in
  focus); `.switch` (a plain toggle, 32 by 18), `.segmented`, `.badge`, `.chip`, `.card`, `.menu` and `.menu-item` (28 px
  rows), `dialog.modal`, `.toast`, `.empty-state`, `.spinner`. `#/controls` shows every one in light and dark.
- **Motion**: `--motion-quick` 120 ms, `--motion-base` 160, `--motion-slow` 240 on `--ease`
  (`cubic-bezier(0.4, 0, 0.2, 1)`): a fade, a short rise, never a bounce or an overshoot.
- **Scrollbars** are 6 px, thin, on no track, where a pointer scrolls; a phone keeps the system's.

To check a look, open `#/controls`, `#/settings/appearance`, a chat, a menu and a dialog at 1280 and
390 px wide, in light and dark (`tests/smoke/test_controls.py` and `test_tour.py` photograph them).

## Smoke tests that anchor the map

These three pages have the most wiring and the clearest failure a person sees; the map test keeps their smoke tests
present.

- `tests/smoke/test_harness_page.py` — the harness page and its controls.
- `tests/smoke/test_memory_page.py` — memory search, notes and the page's state.
- `tests/smoke/test_rules.py` — the rules page and the policy it shows.
