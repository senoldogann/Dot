# The Dot interface, one page at a time

A map for an agent that has to use or change the interface: each view file, what the page is for, and the smoke tests
that hold the three pages an agent is most likely to break. `tests/unit/test_ui_map.py` fails when a view file is added
or removed without a row here.

The shell is `settings.js` (the settings' breadcrumb, sheet and pages; the nav in the sidebar is `settingsnav.js`); the
home page is `runs.js`, and one run is `run.js`.

## The shell around every page (items 1327 to 1330)

The shell is one design on a Mac and on a phone, after the reference app's: plain, hairlines instead of shadows, no mascot in a list
or a bar. A phone's drawer is the same sidebar component as a Mac's, a finger's room larger; the sizes and the colours are
the tokens of `themes.css` and `app.css`, never a literal (`tests/unit/test_css_tokens.py`).

- **The sidebar** (`sidebar.js`, `shell.css`): a head with the title; the search with three small buttons at its right (the
  bots' menu, a new bot, a message to Dot); the chats as rows of three lines (the bot's two-letter badge and name with
  the age or "Working 3m" / "Waiting 3m" at the right; the title; a last line of the loop and campaign marks, the team, what
  it delegated, its last words and the dot of an unread chat); the pinned chats first and then the days (Today, Yesterday,
  Previous 7 days); in a dock under the list, **Working** (what waits for the owner and what works, with a count and a fold
  that never hides what waits) and **Settled** (the chats resting past a week, folded); Dot's work, the bots' work, the
  code reviews and iMessage stand as sections of the same look; and at the foot four small buttons: Settings, Connect
  apps, Watch the Mac and Models (there is no account usage page, so the fourth leads to Settings › Models). The bots
  are a menu behind the first button: each its badge, name and how many chats it has, to its conversation else its page,
  then All bots, New bot and Campaigns. Routines has no icon here: its page is Settings › Scheduled tasks (`#/settings/schedules`, a row of `settingstabs.js`). The badge's letters
  (`badge.js`) are drawn from `data-code`, a mark of the row and no text. While a row's run works, the badge gives its
  place to the agent's face, living (`actors.js` `liven`): a short bob and now and then a flip (CSS on the compositor,
  each face at its own moment), its eyes glancing to one side and back every few seconds (the character's engine); it
  stands still off screen and while the page is away, glows instead under `prefers-reduced-motion`, and the row goes
  back to its badge once the run rests, waits on the owner or fails. A chat's own face in its page lives the same way
  while its run works. While the search has words, chips stand under it (`searchfinds.js`, `searchscope.js`): **All**,
  **Messages**, **Bots**, **Files**. The chats whose titles hold the words stay the list's rows;
  above them, each kind under its heading, what the chats say (`GET /api/threads/search`, the words found in bold), the
  bots whose name or role holds every word, and the files attached to the chats or written by their runs
  (`GET /api/threads/search/files`);
  All shows three of each with **See all**, a chip every find of its kind, and Bots and Files hide the list. A message
  opens its chat on the run it is of, scrolled to it, with a ring that fades (`chatfind.js`), and one of a chat with no
  run (nothing to open) is left out; a file opens its chat, a bot its conversation.
- **The stage bar** (`shell.js`, `stage.js`, `shell.css`): 52 px, sticky, a hairline under it only once the page has scrolled
  under it (`layout.js`, `data-scrolled`). At the left the sidebar's button (a phone's Back or menu: the edge swipe and the
  rules of Back are those of items 338, 1079 and 1321), then the breadcrumb: the bot's badge, its name, a slash and the
  chat's title. At the right the toggles of the panels. A page that is no chat's names itself alone. The face a page puts
  before a bot's name stays in the badge, out of sight, and its state tints the badge.
- **The composer** (`composer.js`, `composershape.js`, `choices.js`, `run.css`): the iPhone app's capsule on every width
  (item 2015), one rounded box with a hairline edge: one row while the words are short and the box is at least 340 px wide (the "+", the words, the model's mark and chevron, the microphone and the round send), the words on top once they are long, a line break or a file, with
  the model named and its level in grey on the line under them. The model's pill opens the Chat settings sheet (models,
  Reasoning, Mode, Approvals, How Dot works); YOLO's lock alone stays in the bar. There is no resting state and no button
  that hides the choices.
  The queue's sheet over the box is on the box's own ground, a row of it with an up and a down arrow (32 px, 44 on a phone;
  no drag, item 1348); the line over it that says the work's state is a quiet pill.

Smoke tests of the shell: `tests/smoke/test_side_working.py`, `test_working_rows.py`, `test_activity.py`,
`test_side_agents.py` (the bots' menu), `test_chat_menu.py`, `test_connect_apps.py`, `test_phone_layout.py`,
`test_phone_back.py`, `test_desktop_lights.py` and `test_composer.py`.

| Page | View file | What it is for |
|---|---|---|
| Home | `runs.js` | Dot's presence and the composer; one conversation at a time. |
| One run | `run.js` | A run as a conversation: the question, the steps, the answer and the queued message. |
| Bots | `agents.js` | The owner's bot profiles: face, name, role, instructions, tools and model. |
| Team | `team.js` | The day's messages between bots, newest first, with no badge (item 2035). |
| A bot's work | `agentwork.js` | A bot's work orders, objectives or hand-offs, scoped to it (`botwork.js`) as the phone's bot profile scopes them (item 2035). |
| One team | `teams.js` | A team's bots, its name and purpose, and a box to message it. |
| Watch | `watch.js` | The Mac's screen, live, whether a task works or not. |
| Campaigns | `campaigns.js` | Goals Dot keeps working on in sessions until a check confirms the outcome. |
| Scheduled tasks | `schedules.js` | Settings › Scheduled tasks: goals once or on a cron schedule while the daemon runs, a list of rows and a page for the form. |
| Rules | `rules.js` | What Dot may do on its own, what it asks first, and what it leaves to the owner. |
| Settings | `settings.js` | The settings shell: the breadcrumb in the bar, the page of the open tab (the nav is in the sidebar, `settingsnav.js`), and on a phone the sheet of the pages. |
| Appearance | `appearance.js` | Colour schemes as miniatures of Dot's window. |
| Skills | `skills.js` | Learned and built-in skills, their versions and the owner's changes. |
| Plugins | `plugins.js` | The gallery of plugins Dot can install. |
| Connectors | `connectors.js` | The services Dot works with and where to connect them. |
| Memory | `memory.js` | Notes, semantic and word search, and the lessons handed to runs. |
| Harness | `harness.js` | Self-improvement around the model: the harness and what it changes. |
| Self-repair | `repair.js` | The problems Dot found in its own record and the Claude Code sessions that fixed them. |
| Providers | `keys.js` | The model providers and the judge's service. |
| Channels | `channels.js` | The owner's messaging apps as ways to reach Dot. |
| Hooks | `hooks.js` | Addresses a service calls, signed, to start a scheduled task or a bot ([Hooks](hooks.md)). |
| Phone | `phone.js` | Dot on a phone through Tailscale, a step at a time. |
| Vault | `vault.js` | Logins, cards and contact details Dot may type into pages. |
| Web search | `websearch.js` | The engines `web_search` asks, in turn. |
| Snapshots | `snapshots.js` | Both Command keys together capture the window in front (a double tap, once on). |
| System | `system.js` | Daemon health, the emergency stop, health checks and provider models. |
| Diagnostics | `diagnostics.js` | The daemon's process and the children it started. |
| Speed | `speed.js` | Where the seconds of the runs went, stage by stage, with p50, p95, p99 and the count over 3 s. |
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
  dialog, a sheet, the panel of questions that takes the composer's place, a picture in flight: `--shadow-composer`) has one,
  and in dark it is a ring. The composer itself is a box of the page's: a hairline for its edge, no shadow.
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
- **Scrollbars** are 6 px, thin, on no track, where a pointer scrolls; a phone keeps the system's. A screen has one
  scroller: the window on a page, the conversation (edge to edge, its room its own padding) in a chat on a phone, where
  the window does not scroll or stretch; the dock lets a touch through where nothing of it is drawn (item 1349).

To check a look, open `#/controls`, `#/settings/appearance`, a chat, a menu and a dialog at 1280 and
390 px wide, in light and dark (`tests/smoke/test_controls.py` and `test_tour.py` photograph them).

## What the page keeps on the phone (item 1520)

The page keeps the chats it read and the lists of the shell in IndexedDB (`dot-cache`; the rules are `js/cacherules.js`,
the store `js/datacache.js`), so that Dot opens with a chat drawn on a link that is bad or gone: `views/run.js` draws a
chat from its copy at once (`drawFromCopy`) and keeps it as it stands (a moment after its last event, when the page is
put away and when the chat is left), `js/api.js` keeps the lists as they are read and answers a read of one of them from
the copy while Dot cannot be reached, and `js/linkfacts.js` says that what is shown is a copy and since when. The service
worker stays the shell's alone. Its reads are made as the iPhone app makes them (item 1521, `js/retryrules.js`: 10 s a
try, three tries, a wait that grows with chance in it, none spent while there is no link; a read is shared with the same
read on its way and ends when its page is left), and a chat that was away through 500 events or more of a run reads the
newest 400 and says that some steps are not shown (`views/run.js`, `afterTheGap`). Its streams are read by
`js/sseframes.js` (item 1522: a connection that answers nothing in 8 s and a stream that says nothing in 35 s are dropped
and made again, after 0.5, 1, 2, 4, 8 and 15 s, from the last event, with `Last-Event-ID`). What is done with no link is
kept by `js/outbox.js` (the rules are `js/outboxrules.js`) and shown under the chat by `js/outboxrows.js`: a message as
*Waiting to send* with Retry now and Edit, sent once with its key when the link is back (item 1523). Desktop and phone
browsers show *Mac asleep or unreachable since HH:MM* under the bar during a lasting daemon outage. The line floats
without moving the conversation. On phones, *Offline* and a refused key
take their own row in the bar. See [On a bad link: the web app](phone.md).

## The run page's conversation and panel (items 1331 to 1335, 1345 and 1346)

The run page is drawn as a developer tool draws a thread, plain and professional, the same language on a Mac and on a
phone: what you wrote is a raised, rounded box, and a routine's message (item 2021) a compact "Routine" row on that
surface that unfolds its words; what Dot says is plain text with no bubble; the steps of a turn are one
quiet line each. The modules of the conversation and the panel, and what each holds:

| Module | What it draws |
|---|---|
| `timeline.js` | The turn by turn flow; a group of helpers is one card (`buildFlock`): faces, "2 helpers", "2 working" in blue, the time, and a row each (`childScope`): the job, what it does now (`callPhrase` of `workstatus.js`), its state in a word beside the time. |
| `capsule.js` | The bar over the composer, whose status says "Waiting on 2 helpers" only while the run has stopped for them (`waitsOnHelpers` in `waitcounts.js`) and no spinner, and above it the line of what the first helper does now with Details and Stop; once the run was released from its wait (a message of yours, item 1351) the line says "2 helpers keep working in the background" (`waitcounts.js` counts which). A pane in the dock on the bar's own ground (item 1347). A window wider than a phone's (721 px up) draws it; a phone's chat draws `dockbar.js` instead. |
| `dockbar.js`, `dockwords.js` | A phone's one row over the composer (item 1383): a capsule of glass whose segments are the run's status with its Stop, the plan, helpers, loops, the queue and the commands at work, each there only while it has something to say, with the round way down beside it. `dockwords.js` says which segments there are and their words from what the page knows (pure, `tests/js/dockwords.test.js`); `dockbar.js` draws them and says fewer words, step by step, until the row fits (never a scroll). |
| `docksheet.js` | The one sheet of a phone's chat: a segment of the capsule opens its detail from the foot of the screen, one at a time, closed by a tap outside, Escape, its handle or a swipe down. The queue, the loops and the commands at work stand in it as the very panes (`queue.js`, `loops.js`, `background.js`) that stay over the composer on a computer; the helpers and the plan are drawn from their words. |
| `inspector.js` | The side panel, closed by default: one rounded card in two parts, a head that stays (the tabs as a segmented switch, and on a phone the grabber and Close) and a body that scrolls (`.inspector-body`), whose sections each have a small sticky head (`.panel-section-head`: title, count, buttons on one line) over tight rows on the 8 px scale. A sheet from the foot on a phone. |
| `prose.js`, `linkpill.js` | A link in a message (a markdown link or an autolink, in what you wrote and in what Dot says) is plain words: no frame, no fill and no icon, in the text's ink tilted toward the accent (`.msg-link`; a shade brighter in dark, more saturated in light, above 4.5:1 on every ground), underlined only under the pointer and with the focus, which keeps the page's ring. It opens in a new window, and its label is the link's words, `#1001` for a GitHub pull request or issue, or the site's name for a bare address (`data-words` keeps the words as written for a copy). Dot asks no site for an icon. |
| `agentlist.js` | The **Lineage** ("Lineage · 2 running": the task that leads and its helpers) and the fold of the previous bots; the Task tab has it compact, the Bots tab in full. |
| `routinewords.js`, `messages.js` (`routineMessage`) | The row of a message a routine sent (a run of source `schedule` or `watcher`, item 2021): `.routine`, a `details` whose head says "Routine" and the title (the chat's routine's name from `GET /api/threads/{id}/automations`, else the words' first sentence, `routineTitle`) and whose body is the whole message. |
| `automations.js`, `automationwords.js` | The **Automations** of the chat in the Task tab: its routines and loops, each with a pencil (`#/settings/schedules/<id>`, the routine's editor page), a play and a switch, from `GET /api/threads/{id}/automations`. |

Colours and radii of these are tokens (`var(--accent-strong)` for the colour of work, `--ok`, `--bad`, `--border`,
`--surface`, `--radius`); no sheet draws a literal colour. The sizes of the marks, rows and the list's cut are plain
pixel values of their own rules.

## Smoke tests that anchor the map

These three pages have the most wiring and the clearest failure a person sees; the map test keeps their smoke tests
present.

- `tests/smoke/test_harness_page.py` — the harness page and its controls.
- `tests/smoke/test_memory_page.py` — memory search, notes and the page's state.
- `tests/smoke/test_rules.py` — the rules page and the policy it shows.
