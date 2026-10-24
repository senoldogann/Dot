# The daemon, the web UI and the desktop shell

## The daemon, the web UI and the service

```bash
dotx daemon     # serve the API and the web UI, run the agent and fire the schedules, in this terminal
dotx open       # open the web UI of the running daemon in your default browser, signed in
dotx url        # print its address with the sign-in token
```

The web UI is laid out like a chat app: the chats in a sidebar on the left, one page on the right, and the settings on a
page of their own.

The sidebar lists your chats (the conversation threads) grouped by day, newest activity first, the pinned ones above the
days, and a chat opens at its latest run. Each chat wears a creature of Dot's character engine, as a messenger's list
shows faces (item 277): a chat with an agent the agent's look, a chat with a team (item 610) the faces of two of its
agents while its latest run is Dot's (an agent's run there wears the agent's face), with the team's name ahead of the
last line, a chat with Dot a shape and a colour its own id picks;
the creature works while the chat's run goes on, waits while the run waits for you and is sad once it failed. Its name
stands over its last line: the answer of its latest run in plain words, or your message, after "You:", while the run
works. The creatures rest; only those at work move, and the mark of a chat's loop (item 580): a chat that asks again
by itself, with a loop of `/loop` or a schedule a run of it made that goes on in it, wears at the foot of its creature
a disc of violet and blue whose arrows turn slowly in a glow that breathes (still when the system reduces motion), its
row saying how often and when next ("Loops every 30 minutes, next 15:30", "Loops on Mon, Wed and Fri at 18:30, next Fri
18:30": a rhythm of days and times in words too, item 590, days together by their short names, item 592); stopped, the
loop takes its mark with it.
Each chat stands once (item 202):
while its latest run works, a chat leaves its day for the **Working** section above the list, and goes back once the run
ends; a pinned chat stays under Pinned. At the right of a chat at work the time gives way to a dashed circle,
**Working** and how long since the run was asked for ("12s", "4m", "1h 5m", read again every second).
Under its name, in the line its last words would take, stand the faces of the runs it delegated (item 507), those at
work and those that ended: three at most, laid over one another as the marks of the connected apps stand in the foot, the
rest counted, and its name for a screen reader says how many there are. Nothing hangs under the row: a click on it opens the chat,
whose conversation and Agents tab hold those runs. Working lists too what is no chat's: the session of a campaign
(which works in a thread of its own). A chat whose run waits for you keeps its waiting creature and
its time among the days, and what it asks stands under **Waiting for you**. Working counts what it lists. Folded, Working hides its rows,
the open chat's too, and keeps its count (item 513), and the search finds the chats at work, folded or not. Above the list: a search over the titles (⌘K or `/`), **New chat** (⇧⌘O) and the ways to **Agents**, **Campaigns**
and **Schedules** (a page of its own since item 401); in the foot, the cog of **Settings**, **Connect apps** beside it
([Connect apps](extending.md#connect-apps), items 492 and 545), which opens
[Customize › Connectors](extending.md#customize--connectors), and
the red square of the **Emergency stop**, which stays there while the sidebar is closed (item 273): the top bar then holds the
sidebar's opener alone, the size of the sidebar's own buttons and of the side panel's switch and close. The top bar
shows only what needs you: the bell of the approvals waiting — a click opens the chat of the one that has waited
longest, its card and its buttons there, and the home when nothing waits (item 444) — and the connection to the daemon
while it is being made or lost; what works stands under Working. The sections (Waiting for you, Working, Chats) fold on
a click of their label, and the folds are kept between visits. The sidebar's head holds only the button that folds it
away. **Select** turns the list into
checkboxes: tick chats (or Select all) and **Delete** asks once, then removes them with every run, step, event and
approval of theirs. A chat with a run under way is refused, the run of a `dotx` command too: wait for the run to end, or
cancel it (a `dotx` command's with Ctrl-C). Escape leaves the selection. Under 860 px the sidebar becomes a drawer. The
list stays where you scrolled it while it is painted again (a chat deleted, pinned, renamed or marked, chats deleted
under Select, a run that starts or ends, a follow-up in the open chat): it brings the open chat into view only when the
page comes to another chat (item 204).

A chat's menu opens from the **…** button at the right of its row (shown where the pointer
rests or the focus is, over the time, and always on a touch screen), from a right-click on the row, or from the
context-menu key (Shift+F10); the arrow keys walk it and Escape closes it. **Pin** keeps the chat under **Pinned**,
above the days, the last pinned first, however old it grows; **Unpin** puts it back among the days. **Rename** makes its
name a field in the row: Enter or leaving the field keeps a new name, Escape leaves the old one. **Copy link** puts the
chat's address (its latest run) on the clipboard, for this browser or another one signed in to this Dot. **Select**
starts choosing chats with this one ticked, and **Delete** asks first, as Select's does. The daemon keeps the pin and
the name, so every browser shows them.

A chat whose latest run ended after you last saw it is **unread**: a dot in the accent colour at the right of its row,
under its time, and "unread" for a screen reader. Opening the chat marks it seen, and so does
having it open while the page is in view (shown, in a window that has the focus), so a run that ends in front of you
leaves no dot. **Mark unread** in a read chat's menu (once its run is over) brings the dot back until the chat is
opened again or the page comes back into view, and **Mark read** clears it. The daemon keeps when each chat was last
seen, so the phone and the desktop agree; the chats there when Dot learnt this are taken as seen, and a run that ends
while nobody watches, a scheduled one too, leaves its chat unread.

Over the home's **Today** strip, **While you were away** (item 466, `GET /api/away?since=`) says what your schedules and
campaigns did since this browser last marked it read: how many of their runs ended done, failed or stopped, how many
work and how many were campaign sessions; their newest runs, two then **Show N more**, each with its schedule's or
campaign's name, how it ended, when, and the first line of its answer with its secrets masked; the approvals the review
model and YOLO answered for you in those runs and their helpers, by who and how many were denied, with the runs that had
the most, two then **Show N more** too; and the drafts they wrote that wait for you. It carries no summary and no
arguments of an approval. **Mark as read** moves the moment on, kept in this browser (each device has its own); the
first visit marks it and shows nothing, so the card never pours out the past, and the server reads a week back at most.
A run started with **Run now** is one of them.

Above the chats, two sections appear while they have rows: **Waiting for you** (the approvals and questions runs wait
for, and a run whose browser you took over) and **Working** (the runs going on, a session of a campaign, and a run
delegated by another; a schedule about to fire is no work yet, and stands under the home's **Due today**, item 608),
each row with its state dot, the first words of its title on one
line, and under it the channel it last worked in (browser, files, shell, desktop, mail) and what it needs; a row opens
the run. They are `GET /api/activity`, which carries the runs that ended today too, for the home's **Today** strip; the
Activity page that showed it all is gone (item 364), and its address leads to the home. A run that ended shows how long
it took and what it cost in tokens. The sections and the strip follow the lifecycle stream, so a run moves between the
sections the moment it starts, waits, is taken over or
ends. The stream replays nothing after a break of the connection (the network dropped, the daemon restarted), so once it
is back every page that follows it, the run's page and its queue among them, reads again what it shows; so does every
page when the stream fell so far behind that events were lost (`resync`). A page that cannot read again keeps what it
showed and says so. A stream from which nothing came for 45 seconds, not even the keepalive the daemon writes every 15,
has stopped without closing (a network that changed, a Mac that slept): the page cuts it and makes it again (item 200).
Between tries the page waits half a second, then longer, up to 8 seconds, and from half a second again once a stream is
made; a page that comes back into view, or a browser back on the network, tries at once. The rules page reads its rules
again when one is added, switched or deleted on another page. A page loaded before Dot was updated says so in a banner,
with **Load again**: the daemon's status tells what the pages it serves are (`pages`, a digest of their files), and the
page compares it with the build the daemon wrote in the page as it served it (its `dot-pages` meta, item 328). The
banner floats under the bar, so nothing on the page moves, and a page left out of date loads again by itself once it is
out of sight. The daemon serves the pages as it read them when it started, hidden files
left out (item 309): files changed on disk while it runs (a merge, a batch of work ported in) wait for its next start,
so that a page never asks the daemon's API for a field or a route only a newer daemon has. A run delegated by another
run stands in the sidebar as a small face in the row of the run that delegated it, three at most with the rest counted
(item 507), and the row's name says how many there are. In the chat, the delegated run's card carries a chip with its
progress ("step 3 · web_fetch", then "done · 12 steps"), its steps so far, the time it has worked and an **open** link to
the run's own page.

**New chat** is Dot's presence and one composer, standing in the middle of the space under the top bar, across and down,
however long the day under them is. The face is Dot's character, moving (idle with a wink now and then; a still frame
with "Faces that move" off), and shows the state of the whole of Dot (waiting when something waits for you, working
while a run is active, else idle) under the name you call Dot by, and a greeting by the hour says your name ("Good
morning, Ada. What should I work on?"; "Something waits for you." when it does). Both names are yours to give in
Settings › General, and the daemon keeps them for every browser and phone (`[assistant] name` and `owner`, `GET` and
`PUT /api/assistant`, item 420); your name is the first name of your account on this Mac until you give one, and the
model is told both. An open home takes a new name as soon as it is saved, also one saved on another page. One line says what is going on ("Working on 2 runs · 1 waits for
you · 3 ended today · 1 lesson · 2 things due today"; a quiet day says so). Under the composer, the **Today** strip
lists it: what waits for you, what works, what is due later today (the schedules that fire and the campaigns whose next
session starts), what was learned today (the lessons and the day's journal) and what ended, each row opening what it is
about; two rows a section (a run without the runs it delegated), then **Show N more**, which shows the section's others
in place (item 364), and the page scrolls to what does not fit. A section folds on a click of
its title, which counts its rows, and the folds are kept in that browser. The line and the strip follow the lifecycle
stream, so an answer or a run's end moves them at once; `GET /api/activity` carries the two new lists as `learned_today`
and `due`. The composer is one frame on a 24 px corner with a soft shadow under it: the goal in a box of one line that
grows with it, the files attached and the picks under it, then one bar that never wraps: the **+** and the mode at the
left. A message is on **Auto** (item 495), which shows no mode pill: its words settle its mode, research, which only
reads, when they ask only to look ("read-only", "don't change anything", "just research", "salt okunur", "hiçbir şeyi
değiştirme", "sadece araştır"), and a task otherwise, which ends at once when it answers without a tool, as a chat does
(`agent/mode_words.py`). A mode named with `/task`, `/chat` or `/research` brings the pill (Task, Chat or Research; its
menu says what each does and leads back to Auto); `POST /api/runs` takes `mode: "auto"`; at the right the agent with its face and name once an agent
exists (its menu lists the agents with their roles), **Model** (the mark of its provider and its name: the daemon's
default or one picked), **Reasoning** (a brain and the level, only for a model that offers a choice; kept in the browser
per model) and **Options** (the approval mode, which the pill always shows by its icon and a short name: a lock for
Supervised, a pencil and "Edits" for Auto-accept edits, a shield for Auto, an open lock for YOLO, on no fill; its menu
lists the four modes with what each does, YOLO's in the warning ink while it is chosen, item 398, see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)) and the round send, quiet while the box is
empty. The files under the box stand in one row that wraps only when the frame runs out of width: a photo shows itself
at a thumbnail's size, its name its tooltip and its × at its corner, where another file keeps its icon, its name and its
size. Each pill is an icon and its value, on no ground until the
pointer is over it; as the frame narrows the mode's name folds to its icon, then the chevrons and the agent's name go
and the model's name gives way with an ellipsis, while the reasoning keeps its level's word (item 273), so on a phone
the bar is still one line. On a phone the bar's buttons are 36 px, and the box's text is the size chosen for it, at
least 16 px on a touch screen other than an iPhone's or an iPad's, which would zoom the page into a smaller one (item
311). While Keep going is on, one line under the frame says so. Each pill opens its menu above it, and a pick or Escape
or a click outside closes it. The model's menu (item 208) has a rail on its left, a star for the
favourites and the mark of each provider, that shows the favourites or one provider's models; a search over the models
of the view shown (item 395), as its placeholder and its label say, which keeps its words when the rail shows another
view, has the keys as the menu opens and reads names, providers and "free"; and on each model a star
that keeps it among the favourites in that browser, where the menu opens once there are any. The arrows and Enter pick a
model, ⌘1 to ⌘9 the rows that show those keys, and ⌘D stars the row the keys are on. The default's row, the first of
every view, has **Change** (item 209): the list then chooses the model new chats start on, a bar under the search says
so and the default has the tick, and the model picked (by a click, Enter or ⌘1 to ⌘9) becomes the default, as in
Settings › Models; the menu goes back to the chat's model and stays open. A chat that has a model of its own keeps it,
and one that follows the default moves with it. Escape or Cancel goes back without a change. What you typed and attached
and did not send is kept in this browser and is there again when you come back (so is the box of each run), and so are
the mode, the model, the agent, Keep going and a choice for one message picked for it (item 397), until a message goes:
a new chat after that starts on Auto, the default model, Dot and Keep going off. Dot's face
looks down at the box while you write and brightens when you send. Enter starts the run, Shift+Enter adds a line, or
⌘Enter (Ctrl+Enter) starts it and Enter adds a line, as Settings › General › Composer chooses for every composer of this
browser (item 230); on a touch screen Return always adds a line and the round send button sends (item 440). The markdown
in the box shows as it reads (item 207):
its marks (a heading's hashes, a list's mark, the stars of bold, the backticks of code, a link's brackets, a
table's pipes) quieter than its words, bold words, headings and a table's head heavier, code tinted, quotes softer and
links in the colour of links; the box is still a text field, with its caret, selection, spelling and undo, and what is
sent is the text as typed. A **microphone** by the send (item 574; where the browser has speech recognition,
`SpeechRecognition` or Safari's `webkitSpeechRecognition`: Safari, Chrome and the desktop app's web view, not Firefox,
which shows none) turns the controls under the box into a recorder while it listens (item 605), as a phone's apps do:

- a cross that drops the dictation;
- a wave that moves while the engine hears a sound (drawn from the engine's own word that it hears one: Dot opens no
  microphone of its own to measure it);
- the time since the browser began to listen;
- a tick that puts the words in the box, after what it holds, once the engine has settled them.

Until the tick the box keeps what it held. The dictation also ends:

- after six seconds with no word, saying nothing was heard;
- after a pause of fifteen seconds once a word was, the words going into the box;
- when you type (it is dropped), when you send, and when the page goes out of sight.

It listens in the page's language, given its region ("en" listens as "en-US", item 593), or the one chosen in Settings ›
General › Voice (English, Türkçe, Suomi, Svenska, Deutsch, Français or Español). The silence is counted from the moment
the browser begins to listen, not while it asks for the microphone and its speech recognition the first time (an iPhone
asks twice). A press while it still asks drops the dictation, and one the browser starts anyway once you allow it is
stopped at once, so the microphone is not left on (item 603). A
microphone that is refused says so and where to allow it: in a Mac's browser, the browser for the address and in System
Settings › Privacy & Security › Microphone; in the desktop app, which macOS gives the microphone and speech recognition
only once the app asks for them (a Dot.app built before item 594 cannot), Dot in System Settings, the toast naming macOS
and Dot, not a browser (item 593); and on a phone, the browser's and the device's settings, with no word of a Mac's. On
a Mac they name macOS Dictation (the Dictation key, or Fn twice), on a phone the keyboard's own microphone, which type
into the box meanwhile. A dictation that ends by itself having heard nothing says so and what to check (the microphone's
permission, on a Mac the input in System Settings › Sound, the language), where it ended without a word before; one you
stop says nothing. Safari's way of saying it, "aborted" where Chrome says "no-speech", counts the same (item 616). On an
iPhone or an iPad, Safari hears the first dictation after the page loads, and at times a later one opens the microphone
and hears nothing (WebKit bugs 317741 and 321436): the wave stays flat. Such a dictation says that Safari heard nothing,
whoever ended it, and its toast's **Reload** brings the microphone back, the box's words kept as its draft; the
keyboard's own microphone types meanwhile. A dictation stops a reading aloud first, as Safari does not hear while it
speaks. Nothing is sent by voice alone: you read the box and send as usual. A message whose answer was lost
on its way back (the page says the sending failed and keeps the message) is done once when it is sent again, here and in
the box of a run: the page sends each new message with a key of its own, and the daemon answers the same message sent
again with what its first sending made. `/` at the start of a word opens the slash menu over the box: the commands of
the composer (`/auto`, `/task`, `/chat`, `/research` and `/loop` here; `/btw`, the modes and `/loop` in the box of a
run), the skills by name with their description, and each MCP server with its tools, read from the daemon as a run reads
them (`GET /api/palette`); letters filter, the arrows move, Enter or Tab picks and Escape closes. The menu stands in
sections with a hairline between them and no title on the page (a screen reader hears each by name: Commands, Skills,
MCP tools): a row is a line icon, the name and under it its words, cut by an ellipsis rather than wrapped (item 537,
after Qoder's menu: a terminal in a box for a command, the arrows of a loop for `/loop`, a puzzle piece for a skill),
and under the list stands what the keys do (a touch screen has none, so it has no such line). An MCP tool is named in
words, its server's first, as the row of a call to it is ("Web Research · Web crawl results", not `web-research /
mcp__web-research__web_crawl_results`; item 581), and the letters find it by those words or by the names the model calls
it by. A skill picked is a chip under the box, a tool the chip of its server, as from the **+** (below), and the slash
word goes; a command does its work. `@` at the start of a word opens the menu of the servers and plugins alone (item
367, and the owner's word of 2026-10-21, item 447) — the group the menu titles **MCP servers**, the plugins: each one by
its name and the number of its tools, filtered by the letters after it — the skills and the servers' tools, which the
slash menu carries, are not there; a pick is a chip and the at word goes. In both menus a server, and each of its tools,
wears the mark its service's Customize tile wears, on that tile's light ground: the service's logo, else its icon or its
letters (DeepWiki's magnifier, Context7's C7), the plug for a service with no mark of its own, and Dot's own icon, its
own tile as an application's is, for a server of no service (web-research, a server added by hand; item 581, on its
Customize tile and its chip too); and a skill wears the puzzle piece, here and as a chip and as the row of a call to the
`skill` tool, plain in the ink of the text (item 537; the Customize cards keep the marks of item 477: the download arrow
of an imported skill, the brain of a learned one; the daemon's palette says which server is of which service). An `@`
inside a word, as in an address, opens nothing. On a phone either menu takes the room the keyboard leaves above the
composer and scrolls inside it. A run that waits for your approval is listed under Waits for you, below the composer,
and leads to its chat: the card stands there alone (item 434). Once you allow it, the card leaves the chat with the line
that asked for it, as the page opens again too; refused, it stays and says so, and a question keeps its answer.

The **+** at the left of the bar (item 318, after Codex's composer) opens the file picker at once (documents, photos and
screenshots alike; a phone opens its own sheet). The slash menu picks **Plugins** (the MCP servers with tools) and
**Skills**, as `GET /api/palette` lists them; the at menu picks the plugins and the Google and Microsoft accounts signed
in (items 447 and 496: an account picked starts the run with the mail and calendar tools and names the account to its
model; one that is no longer signed in is refused). A plugin, an account or a skill picked is a chip under the box in a
colour of its name, which its × takes away; the chip of a server wears its service's mark as the menus do (item 530: the
logo, the icon or the letters, on the tile's light ground), Dot's own icon for a server of no service, and for any
server until the daemon's palette has named it (a draft kept, a message sent; the plug before item 581); Plan first is a
chip too, and a chat has neither it nor goal mode nor Keep going; all three switches stand in the **Options** menu's
Execution group, under the approvals (item 353). The picks go with the message (`plugins`, `skills`, `accounts` and
`plan_first` of `POST /api/runs`, and of a message the queue holds), and once it is sent they stand under it as chips
(the run's `picks` event); a message that steers the run under way leaves them for the next one, and the page says so,
and a queued message with picks is not steered into the run at work: it stays queued to start a run of its own. A run
sent with picks starts with each picked server's tool group on from its first step when its tools are grouped (with
every tool offered they are on anyway), and its first message is your words, then a line that names the servers to use,
Plan first's (write the plan with `update_plan`, then ask with `ask_user` and act once you agree: the run's questions
wait for your answer, item 508, and the prompt asks the rest; nothing holds the run's tools back), and each picked
skill's instructions as the `skill` tool gives them; the run's goal stays your words. Picks a run cannot start with are
refused, with why, and nothing starts: a server that is not connected, or none of whose tools a run of its mode may use
(a chat's, when the server's tools all act), each in its own words, a skill it is not offered, Plan first in a chat or
under YOLO (a YOLO run does not ask), any pick with Keep going, and any pick on one of OpenCode's models, whose run is
OpenCode's own agent. A server picked for a message is open to its run whatever the agent's tools are (item 415): the
owner's word for the message widens the agent's bounds, and a server picked for an earlier message of the chat stays
open to the agent's later runs in it (the runs' picks events say which; a server the model only called opens nothing);
before, a pick for an agent whose tools named no MCP tool was refused as if the server were not connected. A run that
goes on from another (Resume, or an answer that came after its run ended) starts without the picks: the first message,
with their lines and skills, stays in the chat, and with grouped tools `use_tools` turns a picked server's group on.
**Save from a chat**, on the Skills page (`POST /api/runs/{id}/skill`), has the review model write the skill of a chat's
last run as a learned skill, as one is written when a run ends, also after fewer acting steps than `[agent]
skill_min_steps`; it is refused while learned skills are off, and for a run that did not end done or took no acting
step, a delegated run, an eval's, one of OpenCode's agent, a chat-mode run and a run that changed Dot itself (item
585; the review model is not asked whether the steps will recur: you chose). The box of a run held it as **Save
skill**, under a **More** button, until item 406.

A run opens as a conversation under Dot's face and name. The face is the state: still when the run rests, a slow breath
while it works, an amber ring while it waits for you, a green tick for a moment when the run ends done, red when it
failed (with reduced motion the tick simply shows). The panel button sits on the right; the run's facts (its state,
agent, model, approval mode, goal mode, campaign, work, time, mode, start and id) are under More on the panel's Run tab,
closed the first time (items 452 and 544). Your goal is the
first message, in the bubble of your messages on the raised surface. What you write reads there as markdown, as an
answer does: its headings, lists (nested as far as their items are indented, with a block of code or a quote under an
item, numbered on from the number they begin at, and task lists with their boxes), tables (their columns aligned as
the rule line says), code (whatever follows the fence's language, `title="x.py"` or `python:x.py`, is read, so the
blocks after it stand as blocks), quotes, strikethrough, line breaks and emphasis (items 206 and 537). Under each of your messages, once its
turn has its answer, stand the time it was sent (the day too when it was not today, the full date where the pointer
rests) and a copy of it as you wrote it (item 205); the files a message came with stand between it and that line, in the
earlier turns of the chat as under the latest message (item 403), the pictures among them as small squares side by side
over the message, each the picture itself with its name where the pointer rests (item 431; a delegated run's goal has no
bubble, so its files stand where the run starts, pictures first). A picture that cannot be read shows the mark of a
fault and why. A command or a script of a call's row shows as itself, cut in the middle past 4000 characters; only a
blob without words, a picture's base64, is shown as its length, and so is a long text with next to no spaces (one line
of minified code). Under a message that opened a turn stands **Edit from
here** too (item 282), which asks whether to "rewind chat to before this message". **Revert and keep changes** takes
that turn and the ones after it out of the chat (they still count in what Dot spent, and a question they left for you
waits no more) and ends the history the model reads where it ended before the message, a summary made since included;
the message comes back into the box of the turn before it with its files, to be changed and sent again, or into a new
chat's box when it was the first. **Revert files too** also takes back what those turns, and their helpers, changed with
the file tools, each file that is as they left it; a file changed since is left and named, and what a command changed
stays. A message sent before Dot kept the place of each message in its chat has no Edit from here, nor has one that
steered a turn at work, and a chat is not rewound while a run works on it or messages wait in its queue. A delegated run
is a row: the face with its state at the corner (turning while it works, amber while it waits for you, green with a
check when it is done, red when it failed), its goal over the last thing it did and, once it ended, the start of its
answer (item 245), the time it has taken (counting on every second while it works), the way to its own page and a
chevron that opens its steps. Rows that follow each other gather in one group: its head shows their faces, how many they
are and how they stand ("3 helpers", "1 working · 2 done"), the time from the first one's start to the last one's end
and a chevron that folds the rows away; an approval a helper asks for opens the group. The row is closed from the start;
an approval the run asks for opens it until you have decided, and a row you never opened or closed yourself closes
again. Resting the pointer on a row, or moving the focus to it, shows its card (item 226): the whole goal, the model
with its reasoning level, how the run stands and for how long, counting on while it works, and what it does now or did
last; the pointer leaving, a click or Escape puts it away, and on a touch screen a tap opens the row as before. Its own
page names the parent in the top bar as a breadcrumb (the parent's goal, a link, then its own) and has a pill where the
box would be, which stays at the window's foot as the conversation scrolls (item 244): the model, how long it took
("Completed in 1m 46s", "Working for 2m"), whether its parent still works ("Runs on its own" once not), "Open parent"
and, while it works, **Stop**. The tool calls of one stretch of work fold into a single line that says what they did,
"Ran 2 commands and read 1 file", with the time it took beside it ("Working…" and what is behind it while it runs,
"Worked for 12 s" for a stretch with no call; item 374). The line is one line at any width (item 512): what it did
gives way with an ellipsis, and its time, the badge of failed calls and the chevron keep their place. A folded group at
work names the command that runs right
after "Working…", in place of how much it did so far, which the open group keeps (item 430). Open, it shows every call, result and screenshot, each call one
line in line with the group's head (item 402): its mark (a spinner while it runs, then the mark of its kind, in the
failure's colour when it failed; item 403), what it did (a command is the command itself in the monospace type, the
policy's words its tooltip; a read or a write says "Read notes.txt", "Write notes.txt", from the start, its mark saying
which it is, keeps no tool's name, and reads the policy's words once the call is done; another call keeps its tool's name
beside its words, item 506; the name reads in words, item 513: one of Dot's as what it does, "Edit file", "Update
plan", "Web search", an MCP tool as its server and what it does, "Tavily · Search", the tool's own name its tooltip)
and a chevron, a failure's first line its tooltip and its result on a hairline under it once opened, with **Why it
ran** for a step that acted, or that more than the preset decided: the rule of yours, the preset, or who answered and
why the policy asked (item 468); a group that is
over wears the mark of the kind its words start with (the terminal's for "Ran 2 commands"), and the model's thinking is
a line of the same column with the brain. Approvals, questions and policy notes stay in the open. A click on that line
opens or folds it while the run still works and after it ends, and the reader's choice stands for the life of the page;
folded, it keeps the call under way in sight (on a phone, only the count). The lines of how a run was set up (the notes
and skills it was handed and how they were found, its context budget, its tool groups, a step sent to another model) are
not in the conversation (item 365): the side panel's Run tab lists them under **Set up**, in its More (item 452),
which counts a line that warns beside its title, the head of Set up naming them in short
and a kind a long run repeats (the goal repeated, a step sent to another model) once with its count, a line that warns
in amber (the embedding service failed, so the notes were found by their words alone); a delegated run's lines are on
its own page. What the model thought in a step is a line of its own there, before the step's calls (item 246), or out of
the fold before the message the step writes or the answer it ends the run with: a brain where a call has its icon,
"Thinking…" while the model thinks and "Thought" once it is done, the start of the thought beside it; opened, the whole
thought: up to 12,000 characters of it, of a longer one its start and its end, its secrets masked; it is there when the
page is loaded again. A thought and a message are masked as they stream too (item 551): a secret split between two
pieces of the stream is not shown, so the text trails a few words behind and is whole when the step ends. A step asked again from its start drops what its failed try streamed. A message streams as it will
stay (item 274): formatted as it comes, drawn at most once a frame with the parts already written left as they are, its
caret at the end of its text, so that its end moves nothing. An answer that ends a run while its page is open is written
in the same way (item 366), a few words a frame for two seconds at most, into the room it will take, so the page lands
on it as it will stand and the words come in sight, its foot once it is all in; a run opened after it ended, a reply
that streamed already and reduced motion show it at once. A chat whose run is over opens drawn whole (item 454): its
events, those of the runs it delegated to, and the work of the turn before it are read before the page is drawn, so
nothing fills in as the stream comes. A message sent from a chat that is over starts the next run in the same chat
without a jump: the chat stands where it was, at its foot, inert, until the next run's page is drawn in its place,
never lifted to its top and dropped back. The answer of the run is the last message, with a copy button
(its icon turns to a check once the answer is on the clipboard) and, where the browser can speak, a speaker (item 574),
when it came and how the run ended under it; how long the run took shows where the pointer rests on it (on a touch
screen it always shows). Over that line, once the run of the page has ended, one line says what its file tools created
and changed on this Mac (item 597: "Created a file and changed 1", from the undo journal, the run's own notes left
out), the files by name, each a button to the file beside the conversation, with **Review**, which opens the side
panel's Changes tab and their diffs, and **Undo**, which asks first, then deletes the files the run created and puts
back the ones it changed that are as it left them; the line then says its changes were taken back. A run that changed
no file shows nothing there: not an editor's list, which the Changes tab is, and a command's own writes, which no
journal holds, are not in it.
The speaker reads the answer aloud with the browser's own voice, in the language of Settings ›
General › Voice (with a voice kept on this Mac when the browser has one for the language, a network voice only when it
has not: [what leaves this Mac](safety.md#what-leaves-this-mac)), leaving out its code, its addresses and the marks of
its Markdown, and stops the reading at a second press. With **Read answers aloud** on in Settings › General › Voice
(off until you turn it on, in this browser alone), the answer of a run that ends while its page is open is read as it
appears, and one shown again by a chat opened later is not; one answer is read at a time, and the reading ends when you
leave the chat or send a message in it (item 612: it went on over the page you opened next, with no button on the screen
to stop it). Beside the speaker a
smiley with a plus (item 586) opens the palette of reactions (👍 ❤️ 👎 🔁 📌 ❓ ✋), and what Dot said in the middle of a
run has a quiet foot of its own with it; the chips of the reactions that stand follow the button, a press of one takes
it back, and they are there after a reload, in the turns before the run and on every other screen of the chat. What
each does while a run works and after it ended is told in
[Reactions on Dot's messages](runs.md#reactions-on-dots-messages); a ✋ holds the run, and while it does the box sends
what you write to the run at once, saying so in its words. Settings › General › Reactions says what each reaction does
once a run has ended. A message that only says the
answer again is not shown beside it, wherever it
stands in the turn (item 460): a task run that answers in a plain reply is told to call finish, thinks once more
and finishes with the same words, or nearly: its first line reworded
(item 567), a path cut short written out whole, a comma made a semicolon, a line added before or after (item 569). Such
a message goes when every line of it is held in a line of the answer, in order: its words are words of that line in
their order, the marks of a sentence at a word's ends aside (a sign, a unit or a currency is part of the word), and a
path cut short (`.../plan.md`) stands for a word of the line that ends as it does; its first line may instead be the
answer's first line reworded (at least half its words in it). It must hold twenty words and more after its first line
and most of the answer, so a note that is a small part of the answer stays. A message with a line the answer lacks, a
word changed or moved on one of its lines, a number or a "not" the answer lost, or a first line that only adds words to
the answer's or has a number of its own, stays; a first line that swaps a word for another ("No tests
pass:" for "All tests pass:") is taken for a rewording, and the answer, the run's last word, stands for it. A copy out
of the conversation
keeps its markdown (item 206): a selection that starts in a message or an answer goes on the clipboard as the markdown
it was written in, a list as a list, a table as a table and code in its fence, with the formatted copy alongside for the
applications that read it; the line under a message or an answer stays out, and a copy within a block of code is its
code. A block of code, in an answer, in a message of yours, in a step's row or on an approval card, is a dark rounded box with a head
(item 537): at its left the language it was written for (the title its fence gave it, ` ```python title="x.py" `, in
its place; `text` when it named none; a step's boxes say Command, Arguments, Result or Error, an approval's Command or Target), at its right a switch
that wraps the long lines of this block (Settings › Appearance › Type › long lines of code wrapped says how every block
begins; a step's boxes begin wrapped) and the copy of its text as it was written (item 439), which, for a step's result
the page shows only the first part of, is all of it; Show all puts all of it in the box. A table has its head too, with
its name and a copy that takes the table as markdown (item 403). Code shows its letters as they are: no font turns `->`
or `!=` into one glyph.
The box stands at the foot of the window from the first message, the conversation filling the space above it from
the top, and is the same frame as the home's, with the attach button and the send in its bar; what you typed there and
did not send waits for you. While the end of the conversation, or its answer, is below the window, a round arrow just
above the composer leads there; it stays away while the page follows a working run down, as the page does until you
scroll up. At the left of the conversation a column of short marks stands for your messages, one mark for each, top to
bottom (item 201): resting the pointer on it shows the message nearest to it and the start of its answer, a click goes
to that message, and from the keyboard the arrows walk the marks and Enter goes; the marks of the messages in sight are
darker. The column needs a pointer that rests, two messages or more, and a window wider than a phone's; there, the
conversation leaves it its room at the left, beside the side panel too (item 228). In the side panel (Run details),
the Run tab opens with the **Recap** (item 536): a few short lines on what the run has done, what it is doing now and
what waits for you, written by the fast model (`[models] fast`; the run's own model when the fast one fails) from the
run's record (its task, status, plan, the questions that wait for you and its conversation), with no tools and the
record taken for data, never for instructions. The run never sees it, the secrets Dot holds are masked in it, and it
is kept as a `recap` event of the run, its model call counted with the purpose `recap`. Under it stand the step it is
as of, its model and the time it was written (`Updated 17:27`); its button writes it again. One is due once the run
took `[recap] first_after_steps` steps (4), then every `every_steps` steps more (8), and once more when the run ended
after the last; a page asks for one only
while the section is in sight, and as the run takes steps at most every 20 seconds, never two at once for one run, and
after a write that failed it shows why and asks no more until its button is pressed or a recap arrives;
`[recap] enabled = false` writes none and hides the section. Under the recap stand what the run is made of, each
section only when it has a row (item 539): **Skills & MCP** (the skills and MCP servers the run used, with how many
times it used each, opening Customize; a skill is the `skill` tool's name, a server the one in `mcp__server__tool`;
the section stays out of sight until the run uses one, item 546), **Artifacts** (the files the chat wrote with their
added and removed lines, each opening the Changes tab where the diff is), **Web reading** (the pages it fetched and
the searches it made, a page opening in a new tab; a fetch that failed is not a page it read) and **Memory updated**
(the notes it saved, corrected or forgot, each opening its note in Settings › Memory). Each row wears its mark on a
tile: a skill or a server its icon, a file its language's own logo (Simple Icons, version 16.35.0, CC0, under
`icons/languages/`, item 547), or the letters of its kind where the language has no mark. In a tab of
several sections each scrolls on its own once it holds more than two fifths of the screen, its head staying over it,
and a tab of one section has the panel to itself (item 544). The Run tab lists no files of the conversation and no
Details card since item 544: the files open from their chips and paths in the conversation, and the run's facts stand
under More. In the side panel (Run details), under More on the Run tab, **Cost** lists the bill of the run, each value
beside its label as the facts are: where the minutes went (the model, the summaries, the tools, the time spent waiting
for you), the steps, the input tokens with the share the provider served from its cache, the output tokens, the
compactions (with their rate per
100 steps), the runs
it delegated and the web searches by engine, and a figure of none has no line (item 452). It is
`GET /api/runs/{id}/stats` (the same numbers as `dotx stats --run
ID`), read again every ten seconds while the run works and once at its end. A strip under the head links the other runs
of the same thread, and the box at the bottom queues a message for after a run that is going on, or follows up on a
finished one. While the run works and the box is empty, the box's send is the run's **Stop** (a round button in the
danger colour with a square) and cancels it; a word typed turns it back into the send, **Queue**, and the message waits
in the chat's queue (see [How a run works](runs.md#how-a-run-works)), shown above the box: a sheet whose head (Queued
and the count) folds it, with a row per message: a handle to drag it to another place or move it with the arrow keys,
its text on one line (all of it in the tooltip), a pencil that edits it in the box (the send says Save; Escape or Cancel
gives the box its draft back; item 376: the message's files come into the box with its text, small, a photo as its own
picture and each file with its ×, its name the tooltip, and the files the box holds when it is saved are the message's),
**Steer**, which sends it to the run now, and × to take it out. ⌘⇧Enter steers the run with what the box holds, past the
queue, or with the queue's next message when the box is empty. Settings › General › Composer can have a message steer
the run instead (item 230): the send then says **Steer**, and ⌘⇧Enter puts what the box holds in the queue. A held queue
says it is paused, with **Resume**, and a queue the page could not read says why, with **Read again**. A line over the
box (items 352 and 360) says in a few quiet words how long the run has worked, or that it waits for you, how its helpers
stand and how many messages are queued; each part opens what it tells of, and the line comes while one of its parts has
something to say and goes when none has. It does not say what the run does now, which the conversation shows, nor count
the commands at work in the chat, which stand right under it. When the run ends done, the next message starts and the
page follows it; every open page shows the queue as it changes, and only your messages wait in it: the end of a command
in the background is never queued (see [Runs and tools](runs.md#tools)). A message sent to the chat on another screen
(the phone, another window) starts its run there, and every page that shows the chat's newest run goes on at it at
once, as the screen it was sent from does: when the run is queued, once the page's stream is back after a break, and as
a phone's page that the browser loads again opens; what was typed there and not sent goes with it. A page of an earlier
run of the chat (opened from the sidebar's Activity, a link or its address) stays where it is, and so does a page while
a question its run left is open on it, until none is (item 451). In a campaign's chat the message steers the
session. The box wears the home's pills too: the mode (none on Auto, which a chat keeps for its next messages,
each settled by its own words), the model with its reasoning and the Options (approvals and goal mode) of the chat,
and a follow-up or a queued message starts its run with them on the same thread, for the same agent. The chat keeps them (item 199): those its first run started with, then each one chosen in the box, at once and
for every browser, so a reload, a restart of the daemon or a run moved to a standby model leaves them as they are (a
chat from before has the open run's until its next run starts; a Task kept before Auto, item 495, reads as Auto, which
settles a task unless a message's words ask only to look). They are not kept in the browser as the home's are, and
Keep going stays a new chat's choice. The run under way keeps its own: while it works, the pills step back and say that they wait for the
next follow-up. Beside the send, a ring shows how much of the run's context budget its last step filled (item 222): the
tokens that step sent the model against the run's budget (the model's own context size or Dot's default, the most of the
window, item 396); it turns to the warning colour past 80 % and to the danger colour past the budget, and a click shows
the figures, the point past which Dot summarizes the older part of the run, the model's window, and why the budget is
smaller than the window (each step sends the model the whole run again, so a run kept within it is answered sooner and
costs less) and where it is set (items 239 and 250): `[agent] context_tokens`, or the size chosen for the model in
Settings › Models, which the timeline notes too; **Change context size** under them opens that setting at the run's
model (item 396). It reads the run's `scaffold` and `assistant` events and asks the model nothing. A run the daemon cut
off offers **Continue** on a line above the box. The run's facts stand under More on the panel's Run tab (items 452
and 544), closed the first time and as the browser last left it after: the status, the agent of a chat with one, the
model and its reasoning level, the approval mode, goal mode when it is on, the campaign of a session, the tool calls
(with the failed ones), the tokens (of every model call of the run: its steps and its side calls, such as compactions,
reviews and the goal check), the compactions once there is one, the duration, and the run's mode, its start and its id
(with a copy button); its other tabs hold the files the chat changed (Changes), the
plan, the agents of the chat and the screen (Dot's computer and the latest screenshots). Each tab wears its icon, and where the strip has no room for
every name, the tabs not chosen show their icon and count alone (item 227). The Agents tab lists, newest first, the runs
the chat's runs handed work to: five at first and the rest on **Show N more**, each with its face and goal, its
provider's mark, model and state, what it does now (or did last, from the conversation on the page) and how long it
took, and a row opens its page (item 296); the pointer at rest on a row, or the focus on it, shows the helper's card as
the conversation's rows do, with its whole goal, and a phone has none (item 426). It stays closed until the panel button
in the head of the run opens it (the choice is kept in the browser); open, it sits beside the conversation from 960 px,
is a drawer below that, and on a phone a sheet that rises from the foot (item 296). The run's head is the top bar
itself: Dot's face and name on the left, the panel button on the right; the bar is solid with a hairline under it, so
nothing scrolls under a blur, the panel stays in place under it, and the box stays at the bottom. Every other page names
itself in the same bar (Agents, Campaigns, Settings) and opens with one line of description. A run's page, live or over, opens at
its end, where its next words come or its newest message stands, and stays there as the earlier turns fill in above (items
403 and 440). The six runs opened
last are kept while the page lives, so that coming back to one (from a delegated run's page, say) draws it at once as it
stood and follows its stream on from the last message read, not from the first; what the lessons, the questions and the
cost show is read again (item 426). It goes on after the last stored event read, not after a live piece of a thought
or a message, which has no id (item 568). A run that ended while its page was open is kept as ended: coming back to it
shows its face at rest, not the tick again. One that was still at work when its page was left has its state read again
before it is drawn, from the side menu or any other way back: if it ended meanwhile, its chat opens at its foot with
its answer drawn at once and its face at rest, as a run that was over when its page opened does, not on the top of its
answer written in again (item 447).

In the conversation each turn of the actor (Dot, or the agent the run belongs to, in its look), and the answer at the
end, opens with its still face; the run's state is the top bar's face and the first fact under More, with no card for
it at the head of the conversation. The panel is one surface on the composer's corner, in five tabs
(item 203): **Run** (the Recap and the four sections under it (item 539), and under More the run's facts, how it was
Set up and its Cost; items 452 and 544), **Changes** (the files the
chat's runs wrote or created with `write_file` and `edit_file`, up to this run, which the tab counts: each with what
became of it and the lines added and removed, opening on its unified diff from before the chat's first change to it;
`GET /api/runs/{id}/changes`, item 368), **Plan** (its steps; the tab says how many are done), **Agents** (the agents
of the chat) and **Screen** (Dot's computer and the screenshots, which the tab counts). A tab shows its sections open,
except Screenshots on the Screen tab, which folds away from its title and stays folded in this browser
(`dot.fold.panel.run-screen`, item 426); the arrow keys, Home and End move between the tabs, and the tab chosen last is kept in the browser
(`dot.panelTab`). Beside the conversation the panel slides in as it is shown and out before it hides, the conversation
taking its room back once the slide is over, at once under reduced motion (item 220). It stands at the window's right
edge, as far from it as from the top bar, and the conversation keeps its reading width in the room the panel leaves it
(item 238). It has a size: three buttons on Dot's computer make it **Compact** (its own 320 px), **Wide** (half of the
page) or **Full** (the conversation at its narrowest, 360 px, the rest for the panel), and the handle between the
conversation and the panel drags it to any width between (the arrow keys move it a step, a double click gives it back
its own width); the size is kept in the browser (`dot.computer.size`). While the panel is wider than its own width, the
computer's view grows with the panel in its warm frame. The drawer of a narrow page has one width. **Dot's computer**,
on the panel's Screen tab, shows the page of the run's browser as Chrome paints it (`GET /api/runs/{id}/screencast`,
Chrome's own screencast, sent only while the view is open and the page in sight; a tab that does not change sends
nothing). A view whose stream breaks or goes silent is made again by itself after a short pause, and one put out of
sight (a phone that put the app away) starts again once it is back (item 276). The view starts by itself at the run's
first browser step, and a run at work that first uses its browser opens the panel on the Screen tab beside the chat,
unless Settings › Browser turned that off (item 354); until a frame comes it keeps the shape of a screen and says why,
with a mark and a title (no page yet, waiting for the page, the view closed, the run ended, or a run in a `dotx`
command, item 220). A Screen tab that has had no page at all says instead, in the empty panel the other tabs use, that
Dot's computer is quiet and that the screenshots it takes while it works will appear there, with the setting under
Settings › Browser that opens it when a run starts its browser (item 401). **Watch** and **Close the view** open and
close it by hand; once the run has ended it keeps the last screen and Watch goes. A pill under it says who has the
browser ("Dot has control", or the agent's name), on one line, beside Take over and Use it here; a panel too narrow for
the three puts the words on top and the buttons under them (item 512). Since that view shows the page, Dot's own Chrome stays in the
background while a run works (`[browser] raise_window`, off by default, items 97 and 118): the tabs Dot opens are opened
behind, the app you were in gets the focus back right after Chrome starts and after a page opens a tab of its own, and
switching tabs does not raise the window; Settings › Browser has the switch "Bring Chrome to the front while working"
for the other way round. **Take over** holds the run at its next tool call, brings its tab to the front either way, in
the Chrome the run works in (a campaign's session, in the campaign's), and the pill reads "You have control" (when the
tab cannot be brought up, the run is not held): the Chrome window is on your Mac, so sign in, pass a CAPTCHA or do a
step yourself; **Return control** lets the run go on, and the result of the tool call that waited tells the model how
long you had the browser and that the page may have changed, so it takes a snapshot first, which shows `[hidden]` for
what you typed in a password, one-time-code or card field outside `yolo` (item 467). The timeline keeps both moments
(`takeover`, `handback`), and `dotx run` prints them. A takeover is refused while `[browser] headless = true` (there is
no window to use), and the run page keeps the last frame once the run has ended. The page can also be used from the
screen that shows it, a phone's too (`POST /api/runs/{id}/takeover/screen`, then `POST /api/runs/{id}/input`, item 470):
the run is held the same way, but nothing comes to the front of the Mac, so a browser without a window is held as well.
Your taps, scrolls and keys reach the page as fractions of its picture, which the page's own size turns into points, so
Chrome's zoom and the scaling of the picture do not move them; they wait a moment for a step that was under way and land
only while the run is held, so input sent before **Return control** never comes after it. The picture and the input come
from the browser the run works in, a campaign's Chrome too. The `takeover` event then says `by: "screen"` and whether
the request came by a name of `[server] reach` rather than this Mac's own address (`via_reach_name`, a hint, never a
decision), and the `handback` event counts the clicks and scrolls and says whether anything was typed, never what, a
batch still being done included. Neither **Return control** nor the run's end waits on the page (item 479): the hold
ends, no batch begins after, and a button your input left down is let go before the run's next step has the page, by
the last batch under way as it ends, or else at once; the page is given a moment to take a batch (3 s, its measure
included) and to let go (1 s), and one that does not answer fails the batch and is logged, holding nothing up. A run
held on the Mac can be used from the screen as well. A tab a tap opens (a link to a new tab, a sign-in popup) is not the one the picture
shows: the run finds it at its next look at the page. `[browser] remote_input` switches it (Settings › Browser, on by
default). On the run page, **Use it here** does it (item 475): it sits beside Take over in the pill, first on a touch
screen, and on the card of a browser step handed to you; the view opens over the whole page, a tap clicks, two quick
taps double-click, a finger dragged scrolls the page, a finger held still a moment and then dragged drags on it (a
slider, a selection), two fingers zoom the picture itself, a mouse and a trackpad go as they are, and **Keyboard**
brings up the screen's keyboard for the page, a key with Control or Meta, or a named key with Alt, going as a chord and
a character the layout types with Option or AltGr as text. A step the run was taking when you held it keeps the page a
moment, which the view says, and **Return control** ends it all and closes the view, back to the run's page, as the
run's end does (item 491).

What a run did and what can be undone is `GET /api/runs/{id}/actions`, which the run's page no longer shows (item 364):
every acting step (a tool call that changed something: a file written, a command run, a click, a message sent; reads are
left out)
with the channel it went through, its target, the result the model got as its evidence, the flags its action raised
and what the policy decided of it, who answered and whether the run was tainted then (`decision`, items 468 and
474; none for a step recorded before item 468, no taint for one before item 474); every
approval and question
of the run with its decision, who decided and the note; and every file the run, or a run it delegated to, changed with
`write_file`, `edit_file` or `write_notes`, with its undo state as `dotx undo` reads it (can be taken back, changed
since, cannot be taken back, as before the run). `POST /api/runs/{id}/undo` takes one change back, a file as the run
left it, once the run has ended.

**Settings** (`#/settings`) is one page with tabs: **General** (Dot's name and yours, Dot's own look, desktop
notifications, the composer's keys, the Chat card's "Show what ran without asking", which is off until turned on: off, a
chat leaves out the lines of the decisions the review model made, while a refusal still shows; a step yolo let through
draws no line either way (items 346, 362 and 500, kept in this browser), and the token this browser is signed in with),
**Appearance** (the look
of this browser, below), **Models** (the **Default model** first, a menu of the models that can call tools: the runs
that name no model, a new chat's among them, start on the one chosen at once, and `config.toml` keeps it as `[models]
main`, as when it is changed from the composer's model menu; the **Roles** card holds the fast, review and vision
models too, each with its provider in its box, and **Fallbacks** the models a run moves to; then **Every model, by
provider**, a search over one fold per provider, with the reasoning levels of each model and a menu to choose one, and
what the providers hold in the page's head with a button that asks them again; a provider without a key, or whose key
was refused (HTTP 401), says so and links to Providers, and one that refused access (HTTP 403) says that, with its
words), **Providers** (the providers Dot signs in with, one card: each on its tile with the tail of its key and a dot
that says whether it is signed in, and the one chosen with its key, the page where it is made and the models it brings
behind a filter; see [Models and providers](models.md#models-and-providers)), **Browser** (which Chrome Dot browses with
and what Chrome has to allow first; see [Browser
use](browser-and-computer-use.md#browser-use)), **Rules**, **Memory**, **Skills**, **Self-improvement** (the harness,
what Dot improves around its model, in plain words: what Dot follows now, a form for a version of your own, the
versions, fifty at a time, with Use and Set aside, the nightly learning, the lessons learned and how it learns; see [The
harness](harness.md)), **Phone** (the addresses a phone reaches Dot at and a pairing code for its home-screen app; see
[Dot on the phone](phone.md#dot-on-the-phone)) and
**System** (item 564: three tiles say how the daemon, the permissions and the health checks stand; the **Emergency
stop** is a card with **Stop everything**, or **Release** once it is engaged; the **Health checks** are what needs a
look first, then the checks that passed in their groups (setup, desktop control, providers and models, browser, memory,
tools and plugins), with a filter and **Run again**, a card that folds and says how many checks passed at its head
(item 576), a finding of more than 180 characters shown in its first two lines with **Show all**; below them the
daemon's facts in four groups each named over its rows of a label and a value (item 378: this Dot, how it acts, what
it works with and its models), the permissions and the ways to **Diagnostics** and **Open source licences**, pages of
their own). Which folds are open is kept in the browser. A card that folds keeps its head on its ground, so that
folded it is a row of the page, as a group of the health checks is (item 576).
In a Mac's window the sidebar is the settings' nav while they are open (item 562): **Back to chats** (to the chat last
open; in the desktop app under the traffic lights, whose row holds the button that hides the sidebar where the chats'
head holds it, item 577) and the tabs in four groups (Interface, Models, How Dot works, This Mac), each with what it
holds at its right (the main model, how many providers have a key, how many notes Dot keeps, a green dot when a phone
reaches Dot); whether the daemon answers and the emergency stop are the page's, as on every page. The page is the open
tab alone, in a column of 920 px under its own name and the line that says what it is for, each section a name over a
card of rows; in a window too narrow for the docked sidebar the nav is in the drawer. On a phone they are a list in
groups whose rows open each tab as a page of its own (item 330, under [Dot on the phone](phone.md#dot-on-the-phone)).
The earlier addresses of the memory, rules and system pages lead to their tabs, and the schedules' tab
(`#/settings/schedules`) to the Schedules page. **SnapShots** (item 405) is the window captured into the box by both
Command keys pressed together: see below.

**Dot's first conversation** (item 420). On a first open, while Dot has not introduced itself (`[assistant] introduced`)
and there is no chat yet, New chat shows Dot's first conversation in the place of the greeting, over the composer, with
no model called: Dot greets you by name and asks what to call it (a few names to pick, or one to type; "Name changed to
Miro" notes a new one), says in a few lines what it does (and, in Ask and Supervised, what it asks you about; in Auto,
that a review model answers for you and money and passwords stay yours), and offers to
connect Gmail and Google Calendar: Connect takes the Google sign-in step in Dot's browser and the card follows it until
you are signed in (a browser without a window says so instead). Then it offers to look through your inbox for something
to take off your hands: that look is a run of its own whose goal tells it to only read and to ask you which to do
(the goal asks it, as a goal does; the approval mode decides what is asked of you on the way). The look, Not now, Skip
the introduction and the first message you send end it, and the daemon keeps that it was had.

**Diagnostics** (`#/diagnostics`, item 370) reads this Mac as it is: the daemon's process and the ones it started (MCP
servers, Dot's Chrome and its helpers, commands), each under the one that started it, with its share of the CPU, its
memory and how long it has run, read again every five seconds while the page is seen; the room each kind of file Dot
keeps takes on disk (the store, the workspace, the profiles of Dot's own Chrome, screenshots, downloads, the undo
journals, the eval reports, skills and plugins, the logs and the home in all); and the logs, the last 300 lines of the
one written last, or of another, with a filter. A process is named by its executable alone, as its arguments may carry a
key, a log's lines come with their secrets masked, and the owner's own Chrome is never measured
(`GET /api/diagnostics/processes`, `/disk`, `/logs` and `/logs/{name}?lines=`). **Open source licences** (`#/licences`,
item 371) shows the notices that come with the pages (the fonts, the icons, and the code taken from other projects,
whose copyright Dot keeps), the Python packages installed beside the daemon with the licence each one's metadata names
and a search, and the tools Dot uses (Google Chrome, Ollama, Node.js, Tailscale, OpenCode), each with where it is on
this Mac or that it is not, looked for on the PATH of the owner's shell (`GET /api/licences`).
Both pages, like the other pages under another (an agent's, a team's or a campaign's page, Handoffs, a service's page), have
their link back at their top in a wide window, with the sidebar beside them too (item 380): the sidebar names the list
they are under, not the page itself. On a phone the table of processes scrolls sideways in its box, its edge fading
while more lies to the right.

Settings › Appearance (⌥⌘A) holds the look of this browser. **Colour scheme** is three cards, each a miniature of Dot's
window drawn in the scheme it names: System (the default, split down the middle), Light and Dark; ⇧⌥⌘A steps through
them. **Themes** is one strip of cards that slides sideways (a mouse drags it, a touch and a trackpad scroll it, the
arrow keys, Home and End move from card to card, and it opens on the theme in use), each with a light and a dark orb:
the card takes its theme for both schemes, an orb for its own scheme only (a sun or a moon marks the picks), so light
can wear one theme and dark another. Dot's two
looks are **Neutral** (warm grey and ink, round and quiet; the default) and **Paper** (warm paper tones with a serif
heading and a terracotta accent), whose serif voice follows the scheme it is picked for; Dot's twelve palettes are
**Clean** (near-white, grey and a blue accent), **Rose** (blush whites and a raspberry accent), **Grove**, **Ocean**,
**Ember**, **Iris**, **Midnight**, **Nord**, **Solarized**, **Sepia**, **Forest** and **Contrast** (item 426; the
names of all fourteen themes are taken, so a theme of your own cannot be saved under one). A palette's colours are mapped onto Dot's tokens (the colour a palette paints its solid controls
with is Dot's accent) and held to the rule of the looks below: a text tier or an ink that would read under its ratio is
moved in lightness until it reads, and in dark a card lies a step above the page; a browser test measures every theme in
both schemes as the page paints it. The two picks are written on the page as `light-dark()` pairs (nothing when one look
is picked for both) and kept with the picks, so the first paint after a reload already wears them. Themes of your own
join the gallery: **Create theme** starts from the themes in use for light and dark, **Duplicate** (on a card, as Edit,
Export and Delete are on one of your own) copies any theme, **Export** saves one in the theme file format, and
**Delete** asks first and gives a scheme it was picked for back to Neutral. The editor takes a name and, for each scheme
the theme has, the **Background** and the **Accent** its palette is made from, as the theme generator makes one, and the
**Action** colour Dot paints its buttons, switches and send button with (the generator turns it away from the accent, so
Dot lets you set it); **Colour families** change one colour, and the ones made with it, at a time. The page wears the
palette while you change it. **Add theme** reads a file: a theme file (version 1, or the short form of a name, an
appearance, a canvas and an accent) or a VS Code colour theme, mapped onto a palette. The daemon keeps them, one theme
file each, in its home's `themes` folder (made with the first theme saved; 32 themes, 32 KB a file and 192 KB in all at
most), so every browser has them; a file put there by hand shows after a reload, one Dot cannot read is listed under the
gallery with the reason, and a colour a theme leaves out is Rose's. **Interface**: **Contrast**, 50 to 200 % (100, the
theme's own, by default): under 100 the text tiers and the lines fade toward the page, over it they move toward black in
light and white in dark, the lines a quarter as far; **Chat width**, how wide the home and a conversation read
(Comfortable, 768 px, the default; Wide, 1152 px; Full); **Density**, how close the rows, cards and lists sit
(comfortable or compact); and two switches that follow the theme picked for light until you set them yourself: fold the
tool steps (on in all but Paper) and the Details panel (off in all). **Motion**: **Panel animation**, how long the
sidebar and the details panel take to slide (0 to 400 ms, 200 by default; while the system reduces motion they never
slide), and "Faces that move" (below). **Text** (item 331): one **Text size** under a line of an answer, the size of the
body on this device, every text size scaled with it: on a phone 12 to 20 px around 16 (item 347), as iOS's own, and
elsewhere 13 to 17 px around 14; each device keeps its own. **Type**: the interface's font (Inter, the system's, or
families typed, which go before Inter so that no glyph goes missing); the answers' font (the theme's, which is a serif
in Paper, the interface's, or a serif); the composer's font (the interface's, a serif, the code's, or families typed)
and size (12 to 20 px, 15 by default); the code's font (Geist Mono, SF Mono, Menlo, or families typed) and the size of
an answer's code blocks (10 to 18 px, 13 by default), each with a line of preview; font smoothing (on) and long lines of
code wrapped (off: how a block of code begins, its own switch wraps just that block). The choices are kept in the
browser only, and `boot.js` puts each of them on the page before the
first paint. The palettes, the colour arithmetic, the orbs, the contrast's mixing, the families typed, the theme
generator, the theme file with its checks and the VS Code import are a port of an MIT-licensed project to plain ES
modules; its notice is `static/js/appearance/LICENSE`, and Dot is not affiliated with that project. The looks are one
visual system. Colour: every colour is a token of `themes.css` naming its light and its dark value in one place;
surfaces step up by lightness (page, sunken sidebar, raised cards) rather than by shadow in dark; borders are the text
colour at a small alpha so they sit on every surface; the text tiers are measured against every surface by a unit test
(4.5:1 for text and its second tier, 3:1 for the muted and faint ones; the neutral and the paper look hold the muted
one to 4.5:1 too, the sidebar's group labels being set in it); the yellow is the owner's bubbles. Type: Inter
for the interface and the answers, Geist Mono for code and identifiers, both served from Dot's own origin
(`static/fonts`, SIL Open Font License); seven sizes each with its line-height (12/16 badges and chips, 13/18 meta and
hints, 14/20 the interface, 15/24 prose and the composer, 17/24 section titles, 20/28 page titles, 24/32 the greeting),
three weights (400 text, 500 labels and names, 600 titles), tabular numerals throughout; a unit test refuses any size,
weight, line-height or family in the sheets that is not one of these tokens. Surfaces: cards are flat, a surface with a
hairline and a 12 px radius and no shadow (shadows stay on what floats: menus, dialogs, the composer in focus); section
labels are sentence case; the facts of a row are spans a gap apart rather than one dotted string; an empty page or list
stands a mark in a quiet ring, a title and one line that says what brings something there, in the middle of its place
(item 407); your messages sit on the raised surface. Controls: buttons, inputs and selects 36 px tall on an 8 px corner
with a hairline (32 for the small ones), the same 2 px focus ring on each, a select with its own drawn chevron, a range
on a hairline track filled with the accent up to its value under the switch's knob, field labels on the interface line
and hints on the small one, the composer's menus as round pills of 32 px (an icon of 16 px, the value, a chevron of 12;
no ground at rest, a tint under the pointer) that open a panel above them, the providers' marks served from
`static/icons/providers/` with their licences, table rows 40 px with their content on the middle; `#/controls` shows
every control on one page for checking a look. Faces: every actor is a character (below); an agent wears the look you
choose, Dot its own (Settings › General), and an actor without one (a delegated run, a campaign, a schedule) the shape
and colour its id's hue gives it, one of eight, so it keeps its face in the sidebar, the lists, the rows of a chat and
the bar of its own page. Characters: the animated faces (`static/js/character/`) are a port of bloub
(github.com/jeremy-prt/bloub, MIT, Jérémy Perret) to plain ES modules, one filled shape with two eyes that are holes in
it, in eight shapes, sixteen expressions, twelve colours (the theme's `--character-*` tokens) and fourteen animations;
bloub recreates the behaviour of x.ai's bot avatar as an exercise and is affiliated with nobody, and neither is Dot: the
licence covers the code, not the design it imitates (`static/js/character/LICENSE`). One loop drives every moving
character on the page and stops while the tab is hidden; under reduced motion, or with "Faces that move" off in Settings
› Appearance, each shows one still frame (while the system reduces motion the switch says so, and where that is turned
off). A still character follows a change of its shape or expression at once. Two characters move: the home's face and
the look editor's preview; every other face is a still frame. One moving face costs about a millisecond of main-thread
time a frame (`tests/smoke/test_character_cost.py` reads it from Chrome's performance metrics and caps it). `#/controls`
shows every shape, expression, colour and animation. Space: every gap, padding and margin sits on one rhythm of 8 px (4
for the smallest, a 2 px hairline token for what sits a breath apart; a unit test checks it); the sidebar is a tone
darker than the page with no line between them, has no dividers between its groups, its labels are the lightest ink that
reads, a chat's last activity sits at the right of its row in figures of one width ("5m", "3h", "2d") and its open row
is a surface step rather than a tint; the top bar's pills show a border only when they count something; a card's head is
lighter than its body; a folded step of the timeline is one line whose duration shows on hover. Motion: three durations
(120, 200 and 320 ms) and two easings for what changes, and the rhythms of what repeats (the face's breath and pulse, a
blink, a spin); a unit test holds every transition and animation to them. The face breathes slowly at rest and pulses
while a run works; cards, rows, the slash menu and the toasts fade in; a state badge cross-fades. Under the system's
reduced-motion setting every transform goes (the breath, the pulse, the spinners, the drawer's slide) and the fades
stay, so a state change is still seen. Dot's mark is a yellow dot with the character's eyes, the o of the wordmark "dot"
between a d and a t drawn in one line (`static/index.html`); the favicon, the app's icon, the menu bar's and the web
app's icons are drawn from it (`desktop/icons/render.py`), and the wordmark heads the sign-in card. A window that opens
the UI plays the opening once: the d and the t hop up, the dot drops in with its eyes shut, bounces twice, opens its
eyes and blinks, and the page shows through, 1.3 s in all; a click or a key cuts it short, it takes no pointer, and it
plays neither on a reload nor under reduced motion. The look, the scheme and the density you chose are put on the page
by a small script in the document's head before the first paint, so a held dark scheme never flashes light on the way
in; a screenshot tour (`tests/smoke/test_tour.py`) photographs every page in both schemes at 1280 and 390 for reading by
eye.

While you are elsewhere, the tab title counts the approvals that wait: `(2) New chat · Dot`. Settings › General ›
Notifications adds
desktop notifications (off until you switch them on; the browser asks for its permission on that click): Dot says
when it asks for an approval, a question or your help, and when it leaves you a question that a run could not wait for
(a `yolo`, scheduled or campaign run goes on without the answer); when a run ends (done, failed or stopped, with the
first line of its answer; the sub-runs of a run stay quiet, and a schedule's run is told as its notification level
says, item 461, see [Memory and schedules](memory-and-schedules.md)); when a campaign ends by itself (achieved, or
stopped by the emergency stop or by `campaign_max_sessions`); when a schedule misses a firing that nobody saw (overdue,
or its run could not start; told once while the reason stays the same, and not at its level "never"); when a
repeating schedule is switched off because its runs failed so many times in a row (item 458, once); and when Dot has
something to tell you on its own: the first line of the morning brief (item 570) and what a watcher tells (item 571; see
[Dot comes to you](proactive.md)). It says nothing while you are looking at that run: its page open, the tab visible and
the window active. On another page, in a background tab or in an inactive window you are told; "Tell me even when
I am looking" under the same setting turns the rule off. Clicking a notification opens what it tells of. The tab's
icon is the same face in the state of the whole of Dot (amber ring while something waits for you, working while a run
is active, still otherwise), and a notification carries the face in the state it tells of.

Ctrl-C stops the daemon: the runs that are going on are cancelled, and the browser and the other services are closed.

The server listens on `127.0.0.1:8765` (`[server]`) and refuses to start on any other address: `[server] host` is
`127.0.0.1`, `localhost` or `::1`, written so (another spelling of a loopback address, such as `127.0.0.2` or `::1` in
full, would start a server that refuses every request, its own client's too: item 561). Every request must carry
a path without a `.` or `..` segment (`..%2F`, as a browser sends it, reads as one; item 419), a `Host` of the loopback
address and, when it has one, an `Origin` of the server itself; every call under `/api` except
`/api/health`, `/api/proof` and `/api/phone/pair` needs the bearer token in `<home>/api-token` (mode 0600). A page a run showed
(`/pages/<id>`) needs none: its id cannot be guessed, and it is served sandboxed (see [Tools](runs.md#tools)). `dotx url` puts
the token in the fragment of the address (`#token=…`), so it never reaches the server or a log; the page keeps it in the
browser's local storage and removes it from the address bar. A page that cannot sign in says why: a token the daemon
does not take is asked for again, a daemon that does not answer is to be started, and one that answered with an error is
running, and the page shows that error. Pages are served with a strict content security policy and no CORS headers.

The token goes only to the daemon. Before `dotx open`, `dotx url` and `dotx service permissions` hand it over, and
before each request of the page and each poll of the desktop shell that carries it, the client sends a fresh nonce to
`GET /api/proof` and checks the answer: an HMAC-SHA256 of the nonce keyed with the token, which only a holder of the
token can give. A program that took the port while the daemon was down cannot, so it is sent nothing: the command stops
and names the one that shows what listens on the port, the page asks for the token again, and the menu bar item says
that something else answers at the daemon's address. What the check cannot cover is a page that such a program served at
that address: it reads what the browser keeps for the address, the token among it.

```bash
dotx service install     # a launchd agent: starts the daemon at every login and after a crash, never after a clean stop
dotx service status      # whether the job is loaded and the daemon answers, and whether a restart waits for the runs
dotx service restart     # restart the daemon now, cutting off the runs at work
dotx service restart --when-idle   # return at once; restart the daemon once no run works (what a run asks for)
dotx service permissions # make macOS ask, in the name of the service, for the desktop permissions it lacks
dotx service uninstall   # stop the daemon and remove the job
```

- Run `service install` again after you changed the job itself (`service restart` takes up a change of the
  configuration). It stops the running job before it writes the new job's file; when launchd does not load the new job,
  the file of before is put back and its job
  started again, and the install fails with launchd's words. When the job loads and the daemon does not answer within a
  minute, the error says why when the daemon's own start failed since the install began (`dotx.log`'s `command_failed`
  of `daemon`: a setting this version of Dot does not know, a daemon already running, an address taken), and
  `service status`, when the daemon does not answer, shows the failure that ended the latest `dotx daemon` unless a
  start that went well came after it. Without such a failure, read `daemon.err.log`.
- `dotx service restart` restarts the daemon without changing the job (item 441): the job is unloaded (`bootout` sends
  the daemon SIGTERM, so it stops as it does at the end of a session, and launchctl waits until it has exited) and
  loaded again from its property list, and the command waits up to 60 seconds for a new process to answer `GET
  /api/health`, then prints its version. It is the way to take up a change of Dot's code, of `config.toml` or of a key;
  `service install` is for a change of the job itself (another Python, another `DOT_HOME`). The runs at work are cut
  off, and the daemon's next start resumes them as `[agent] resume` says. When launchd has no job of the service, the
  command says so and names `service install`. In a command a run's tool started (the shell and terminal tools mark
  them, `DOT_RUN_SHELL`), `service restart`, `service install` and `service uninstall` refuse: the daemon would stop
  under the run, and the run's end would kill the command before it loads the daemon again (or, for the uninstall,
  before it removes the job's file, which would bring the daemon back at the next login).
- **A run restarts the daemon with `dotx service restart --when-idle`, never with a launchd job of its own** (item 441;
  the shell tool's description tells the model so). A run that merged a change of Dot's own code wants the daemon
  restarted once it has ended, and restarting at once would cut the run itself off. A run reaches launchctl only in
  `yolo` or with `[shell] sandbox` off: the sandbox of the other modes keeps a run's commands from launchctl (see
  [Safety](safety.md#safety)), and the run then says in its answer that the daemon needs `dotx service restart`. The
  option loads a one-shot launchd job, `dev.dotagent.restart-when-idle` (its property list is in Dot's home:
  `RunAtLoad`, no `KeepAlive`, `AbandonProcessGroup`), and returns at once. The job runs `python -P -m
  dotagent.restart_when_idle`, which reads the database read-only every 5 seconds until no run is queued, running or
  blocked (the run that asked included, so it has to end first, and a run that waits for an answer works too), then
  restarts the daemon as `service restart` does, waits for the health check, writes what it did to `dotx.log`
  (`restart_when_idle_waiting`, `_idle`, `_restarted`, `_gave_up` after 2 hours without a restart, `_failed`, `_crashed`
  with a traceback), unloads itself and deletes its property list. The list is written to a new file made in Dot's home
  and put in place in one step, and the one that was there is read without following a link or waiting for a FIFO (a
  command of a run may leave either in Dot's home, and the upgrade of the desktop app, item 575, asks for this restart
  at every update). Asking while one is on its way changes nothing
  (replacing it could stop it between unloading the daemon's job and loading it again), so there is never more than one;
  `service status` shows a `restart` line that says whether one waits, and `service uninstall` cancels it. A job that a
  run made with `launchctl submit` is started again by launchd each time its script exits, which restarted the daemon
  every 30 seconds for as long as no run worked (312 starts in one log).
- The job runs **Dot.app** (item 288), a small app in Dot's home (`~/Library/Application Support/Dot/Dot.app`) whose
  program starts the daemon's Python and waits for it. macOS holds the program launchd starts responsible for all it
  starts, so its questions and System Settings name **Dot**, and Accessibility (it covers posting input events),
  Screen Recording, Automation and the folders you allow go to Dot. Before, the job ran Python itself: macOS named
  `python3.13`, and a grant went to that Python binary alone, so the next one (a uv update, another version) had none.
  `service install` builds the app with clang (Xcode's Command Line Tools; `xcode-select --install` when it says they
  are missing) and signs it ad hoc; given a program built and signed already (`--launcher PATH`, or `DOT_APP_LAUNCHER`:
  the desktop app carries one, item 575) it copies that in place of the clang build, so that the Tools are not needed,
  and signs the app ad hoc the same, and `DOT_APP_TOOLS` names a folder (the app's `rg` and `uv`) that the job's `PATH`
  begins with. Only the desktop app's own steps set those two variables, so `dotx service install` (and `dotx setup`,
  which installs the service) typed in a terminal on an install of the app, as the README tells, finds both in Dot's
  home: the folder is `runtime/app-tools` when it is there, and the program is the one the app was made from, which
  Dot.app keeps (`Contents/Resources/launcher.program`, with its hash) and makes the app again from when its Info.plist
  has changed; so nothing asks for Homebrew's ripgrep or for Xcode's tools. A program that changed is the desktop app's
  to bring, with `--launcher`; an app that keeps none (one built with clang) is built with clang as before, and when
  clang is missing the error names the desktop app's program as the way. macOS knows such an app by the hash of its
  code, so the app is built again only when its source or its Info.plist change (an app made from a prebuilt program
  is not made from this package's source, so a change of it alone leaves that app as it is); otherwise it stays as it
  is, with what you granted it. A run's commands cannot
  change it: the sandbox closes `Dot.app` and the folder it is built in to their writes (item 561, see
  [Safety](safety.md#safety)). It is not the desktop
  shell's window app (`desktop/`, `dev.dot.desktop`), which also bears the name. Moving from the Python job, grant the
  permissions to Dot once; the `python3.13` rows in System Settings can go. Settings › System in the
  web UI reports what the daemon itself has, and `dotx desktop check` what your terminal has.

  `dotx service permissions` leads you through it, one permission at a time. It asks the running daemon (so the dialog
  names Dot and not your terminal), copies Dot.app's path to your clipboard, opens System Settings at the first
  permission that is missing and says what to do there:

  - **Accessibility** (the input events row follows this switch): macOS shows a dialog; choose **Open System Settings**
    and switch Dot on (Touch ID or password).
  - **Screen & System Audio Recording**: switch Dot on in the pane that opened. When it is not in the list (macOS showed
    no dialog for this to a program that is not an app), press `+`, then Cmd+Shift+G, paste the path with Cmd+V, press
    Return, choose **Open** and switch it on.

  A running process keeps what it had when it started, so after you switched a permission on run `dotx service restart`
  (or `dotx service install`) and then `dotx service permissions` again for the next one. The command ends with "The
  service has everything the desktop needs." and exit code 0 once the daemon has accessibility, input events and screen
  recording. Settings › System › **Permissions** in the web UI does the same from the page (item 409): the three
  permissions as macOS reports them for the daemon (`GET /api/desktop/permissions`, no dialog), **Ask macOS** (`POST`,
  which also puts the program in the panes' lists), **Open** for the pane of each permission missing (`POST
  /api/desktop/permissions/open`), and, when the daemon is the service's own process (launchd's job runs Dot.app's
  launcher, the daemon's parent), **Restart Dot** (`POST /api/system/restart`): the daemon stops with an exit launchd
  does not count as clean, so the job starts it again, with what was granted meanwhile; a daemon run by hand is refused
  the restart, and so is one when launchd cannot be asked. The SnapShots page offers the Accessibility pane the same
  way. Each section of Settings › System folds (item 411), as the reader leaves it in this browser; the Permissions fold
  says what is missing beside its title. Measured through the service with all three granted: the model opened the
  fixture app, typed a name, pressed a button twice and ticked a checkbox in 4 steps and 13 seconds.
- The job does not see the environment of your shell. Give it its API keys through `dotx auth set` or the Keychain.
- When Dot's files are under Desktop, Documents or Downloads, the first start of the job waits for you: macOS asks once
  whether Dot may use that folder, and `service install` waits up to 60 seconds for the answer. Measured on an
  Apple-silicon Mac, with the Python job: 42 seconds with a person answering the dialog, then about 1.2 seconds for
  every later start.

The REST API (all under `/api`; bodies and replies are JSON, event streams are server-sent events):

| Route                                                                           | Purpose                                                      |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `GET /health` (public), `GET /status`, `GET /models`, `GET /doctor`             | liveness, state of the daemon, models per provider (with their reasoning levels), checks |
| `POST /models/main` (`{"model": "provider:model"}`)                            | make the default model, kept in `config.toml` (422 for one that cannot call tools or of no known provider; the provider's error, 502, for one it does not know) |
| `POST /models/roles` (`{"role": "review", "model": "provider:model"}`)         | make the fast, review or vision model, kept in `config.toml` (422 for one that cannot do that work) |
| `PUT /models/standby` (`{"models": ["provider:model", …]}`)                    | the standby models in their order (item 478), kept in `config.toml` as `[models] standby`; `[]` for none (422 for one named twice, one that cannot call tools or one of Zen's free models) |
| `PUT /agent/resume` (`{"resume": "safe"}`)                                     | whether the daemon's next start resumes the runs it cut off (`"safe"` or `"off"`), kept in `config.toml` |
| `GET\|PUT /shell/sandbox` (`{"sandbox": false}`)                                | whether a run's commands outside yolo start in the macOS sandbox, from the next one on, kept in `config.toml` (422 when the sandbox does not start) |
| `PUT /models/context` (`{"model": "provider:model", "tokens": 192000}`)        | the context size of the model's runs from its next one, kept in `config.toml`; `null` for Dot's default, `[agent] context_tokens` (422 below 48 000 or past three quarters of its window, of its own window for an Ollama model whose own is larger than the one Dot asks for, item 520) |
| `GET /models/context?model=provider:model`                                    | what the model's runs of the last 30 days measured about its context size |
| `GET /runs`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`            | list, start and read runs; `POST /runs` with `loop` (`"30m"`) makes the goal a loop of the run's chat too (item 400), with `team_id` opens a chat with one of your teams (item 610) |
| `GET /runs/{id}/stream?after=N`, `GET /stream`                                  | live events of a run with its sub-runs; lifecycle of all runs |
| `POST /runs/{id}/steer`, `POST /runs/{id}/cancel`                               | message a running run; stop it (a `dotx` command's run: the command is asked to stop it) |
| `POST /runs/{id}/btw` (`{"text": …}`)                                           | a side question, answered without the run seeing it          |
| `GET\|POST /runs/{id}/messages/{message}/reactions`, `DELETE /runs/{id}/messages/{message}/reactions/{reaction}` | the reactions on a message of Dot's (item 586; `message` is `answer` or `step-<n>`, `n` written as the number it is, from 1: `step-01` and `step-0` are 422, so that a message has one key; item 612): list them; put one on (`{"reaction": "good"}`, one of `good`, `love`, `wrong`, `again`, `remember`, `explain`, `hold`) and do what it says, 201, or 200 when it was there (the reply has `reactions`, `effect`, `said` and `run_id`; 404 for no run or a step it did not say, 422 for a message with no words, for the answer of a run that works, or for a 📌 on a message that holds a secret); take one back |
| `GET /threads/{id}/reactions`                                                   | the reactions that stand on the messages of a chat's runs (404 for no chat) |
| `GET\|POST /reactions/settings` (`{"key": "wrong", "value": "feedback"}`)        | the switch of the follow-ups and what each reaction does after a run, as they stand; change one, live and kept in `config.toml` under `[reactions]` (`follow_ups` takes true or false, the others `follow_up` or `feedback`: 422 for a pair that does not fit) |
| `GET /runs/{id}/recap`, `POST /runs/{id}/recap` (`{"force": …}`)                | the run's recap and whether one is due; one written when it is due, or whether or not with `force` (item 536) |
| `POST /teach`, `GET\|DELETE /teach/{thread_id}`, `POST /teach/{thread_id}/stop`, `/draft`, `/save`, `GET /teach/{thread_id}/screencast` | a task the owner shows Dot once: start the recording of the chat's tab, the steps as they come, stop it, the review model's draft of the skill, the saving of it (`{"skill": …}`), the discarding, and the tab's frames while it goes on (item 423) |
| `POST /runs/{id}/loop` (`{"every": "30m", "text": …}`)                         | a loop of the run's chat (item 281): a schedule of the chat, returned |
| `GET\|POST\|PUT /threads/{id}/queue`, `POST /threads/{id}/queue/resume`         | a chat's queue (`held`, `messages`); queue a message (`text` and the choices of `POST /runs`; 409 for a chat with no run yet, a campaign's, or the thread of a delegated run); reorder it (`{"ids": […]}`, each once); resume a held one |
| `PATCH\|DELETE /queue/{id}` (`{"text": …}`), `POST /queue/{id}/steer`            | edit or take out a queued message, its files too when `attachments` names them (item 376; left out, they stay); send it to the run at work now (409 with none); 409 while its run is starting; each answers with the queue, each message's `files` named |
| `POST /attachments` (`{"name", "media_type", "data_b64"}`)                      | keep a file for a message; `POST /runs` and `steer` name it in `attachments` |
| `GET /attachments/{id}/image`                                                  | the picture of an image kept for a message (item 376), for its small chip in the box that edits a queued message |
| `GET /threads?limit=N`, `DELETE /threads` (`{"ids": […]}`)                      | the chats, the pinned first, then newest activity first, each with the face it wears (`actor_id`), the team it is with (`team`, item 610), its last line (`last_message`, `last_role`) and its loop (`loop_cron`, `loop_next_at`, item 580); delete some with their runs (409 with the ids while one has a run under way) |
| `PATCH /threads/{id}` (`{"title": …, "pinned": …, "seen": …, "choices": …}`)   | name a chat, pin it, mark it seen or unseen, or keep the choices its next follow-up starts with (`mode`, `model`, `effort`, `approvals`, `goal_mode`; `GET /runs/{id}` has them as `choices`); one at least (204; 404 for no chat); a chat's `unread` is in the list |
| `GET /threads/{id}/helpers?limit=N`                                            | the runs a chat's runs delegated, newest first (the side panel's Agents tab; 404 for no chat) |
| `GET /threads/{id}/terminals`                                                  | the chat's terminal sessions: command, background or not, start, end and exit status (404 for no chat) |
| `DELETE /threads/{id}/terminals/{session}`                                     | ends a session's program and what it started; the chat is told nothing (404 for no such session) |
| `GET /approvals`, `POST /approvals/{id}/decision`                               | pending approvals; answer one                                |
| `GET\|POST /rules`, `PATCH\|DELETE /rules/{id}`                                 | custom policy rules; each of `GET`'s names in `never_decided_by` the earlier rule that fits every action it fits, or null (item 449) |
| `POST /rules/{id}/move` (`{"direction": "up"\|"down"}`)                        | moves a rule one place in the order and numbers the rules 1 to n; answers the list (422 past either end, 404 for no rule) |
| `POST /rules/draft` (`{"sentence": …}`)                                         | a rule drafted from your words, not kept (422 with the model's reason when the words make no rule) |
| `GET /rules/usage?days=` (1–90)                                                  | how each enabled rule was used on the steps of the last days, with today's rules: the steps it fits and decides, those in yolo runs or settled before any rule, the earlier rules that decide first; writes nothing (item 473; 422 without days or past 90) |
| `POST /rules/preview` (`{"behavior", "tool_glob", "match_regex", "days": 1–90}`) | what the rule would have done on the steps of the last days, before it is added; writes nothing (item 469; 422 for a pattern that is not one) |
| `GET /memories` (`order`: `changed`, `used`, `most_used` or `least_used`), `GET /memories/search`, `POST /memories`, `GET\|PATCH\|DELETE /memories/{id}` | notes; a page of them says since when each that rests does (`rested`); `DELETE` moves a note to the trash (item 584) |
| `PUT /memories/{id}/pin` (`{"pinned": true}`), `POST /memories/{id}/unmerge` | pin a note, so that it never rests (a resting one wakes), or take the pin off; take back the merge that made a note: its sources wake apart, tagged `keep-separate`, and it goes to the trash (422 for a note no merge made) (item 584) |
| `GET /memories/proposals`, `POST /memories/proposals/forget` and `/keep` (`{"ids": […]}`) | the notes that rested for `[memory] forget_after_days`, the longest first; forget or keep the notes of the ids the page showed, the ones not proposed any more left as they are and named in `skipped` (item 584) |
| `GET /memories/trash`, `POST /memories/trash/{id}/restore`, `DELETE /memories/trash/{id}` | the notes you forgot, the newest 200 first (`total` says how many there are), each with when it is deleted for good; bring one back awake; delete one for good (404 for one that is not in the trash) (item 584) |
| `GET /skills/learned`, `DELETE /skills/learned/{name}`, `PUT /skills/learned/{name}` (`{"shared": true}`) | the skills Dot learned, active first, each saying whether it rests and why, is pinned, taught or marked, its version and when a run last read it, with `rest_days`; drop one (the owner's alone); offer an agent's to every run (item 94) |
| `GET /skills/learned/{name}/versions`, `POST /skills/learned/{name}/restore` (`{"version": 2}`) | the versions of a skill, the oldest first, each with how it was made and what its file says; put one back as a new version of its own (404 for one with no file, 422 for the one the skill has now) (item 585) |
| `PUT /skills/learned/{name}/pin` (`{"pinned": true}`), `POST /skills/learned/{name}/rest`, `POST /skills/learned/{name}/wake` | pin a skill so that it never rests (one that rests wakes) or take the pin off; put one to rest by hand; wake one (item 585) |
| `GET /skills/proposals`, `POST /skills/proposals/{id}/accept`, `DELETE /skills/proposals/{id}` | the merges of learned skills that wait for the owner's yes, each with why it cannot be applied now (`stale`), what its skills say now and every field the yes would write to the first (`fields`: name, description, kind, when, steps and parameters, each line marked kept, dropped or written against the skill it replaces); the yes writes exactly those fields as a new version (409 when a skill changed since); the no drops it (item 585) |
| `GET /drafts/{id}`, `POST /drafts/{id}/send\|discard`                          | a draft a run wrote; send it with its words, or discard it (item 314) |
| `GET\|POST /schedules`, `GET\|PATCH /schedules/{id}`, `POST /schedules/{id}/enable\|disable`, `DELETE /schedules/{id}` | schedules (`PATCH` takes the whole schedule; `POST` with `"enabled": false` adds one switched off, its time checked but not the room for one more switched on, which switching it on checks, item 464) |
| `POST /schedules/{id}/run`, `GET /schedules/{id}/firings?limit=` | a run of the schedule started by hand, 201 (409 while the emergency stop is engaged, while one of its runs is at work, or in a daemon that runs no scheduler); its last runs, at its time and by hand, newest first (item 455) |
| `GET\|PUT /brief` (`{"enabled": true, "time": "08:00", "days": "every"\|"weekdays", "lines": […]}`) | the morning brief (item 570, [Dot comes to you](proactive.md)): its settings and when it fires next and last fired; set it, which makes or changes its schedule (`research`, read-only), shown in `GET /schedules` with `brief: true` (422 for a time that is not `HH:MM`, a line over 300 characters, more than 20 lines, or what the scheduler refuses) |
| `GET\|POST /watchers`, `PATCH\|DELETE /watchers/{id}`, `POST /watchers/{id}/try`, `GET /watchers/options` | the watchers (item 571, [Dot comes to you](proactive.md#watchers)): each with its choices (`kind` `mail`, `calendar` or `github`, `account`, `words`, `minutes`, `action` `tell` or `run`, `goal`) and how its looks go; make one (201; 422 for what its kind lacks, `minutes` outside 1 to 1440 and past 20 watchers), change the whole of one, delete one; Check now, one look at once (409 for one switched off, while `[watchers] enabled` is false and while the emergency stop is engaged); what the form offers (the accounts signed in, whether GitHub's token is kept, the limits) |
| `GET\|POST\|DELETE /failsafe`, `GET /images/{name}`                             | emergency stop; screenshots of runs                          |
| `POST /runs/{id}/takeover/screen`, `POST /runs/{id}/input` (`{"events": [...]}`) | hold the run for its page to be used from the screen, which needs no window; your taps, scrolls and keys as fractions of the picture (409 when the run is not held, has no page, a step of it is still going, or `remote_input` is off); nothing typed is kept (item 470) |
| `POST /browser/remote-input` (`{"remote_input": true\|false}`)                | whether a held run's page can be used from the screen (item 470), kept in the config file |
| `GET /browser`, `POST /browser/mode` (`{"mode": "own"\|"attach"}`)              | which Chrome Dot browses with and what Chrome allows; switch it (409 while runs are active, 422 when Chrome refuses) |
| `POST /browser/debug-port` (`{"debug_port": 9222}`, 0 for none)                 | the DevTools port of Dot's own Chrome (409 while runs are active, 422 below 1024 or when another program holds the port) |
| `GET /connections`, `GET /connections/{id}/icon`, `POST /connections/{id}`      | the applications of Customize › Connectors, where each stands (`state`), its step (`step`) and whether the installed application has an icon (`icon`); the icon of an application of the Mac or of OneDrive as a PNG read from its bundle (404 for one of the web, one not installed and one whose bundle holds no icon file); take the step: ask macOS (a minute for your answer), show the Automation pane, open OneDrive, or open the sign-in page in Dot's browser (404 for no such application, 409 for a sign-in in My Chrome or in a browser without a window) |
| `GET\|POST /mcp/servers` (`secrets` by name for a service that takes a key, item 408), `POST /mcp/servers/{name}/connect`, `DELETE /mcp/servers/{name}` | the MCP servers Dot has (`origin`, `state`, `tools`, `error`, `link`, `mark`, `picks_tools`) and the services to add (`kind`, `secrets`, `logo`); add one (`{"service": "notion"}`, or `{"name", "address"}`, with the page's `origin`), tried first and answered once its sign-in page is known or within 20 s; connect one again (signing in again); remove one added there, or take back one added from another agent of this Mac and stop it now (item 497; 409 for one of a plugin, which goes with its plugin, for one of the config, and for one that comes with Dot, which is switched) |
| `PUT /mcp/servers/{name}/switch` (`{"on": true}`) (item 583) | switch a server that comes with Dot (`origin` `builtin`: `web-research`) on or off: on starts it and offers its tools to the runs that start after, off stops it now; the config file keeps it as `[builtin_mcp]`, written once the server answered when it is switched on and before it stops when it is switched off, so a server that cannot start (an error that says why) or a config file that cannot be changed (422) leaves the switch as it was; 409 for a server that does not come with Dot or is being tried, 404 for no such server. A server that comes with Dot is listed switched off too, and `POST /mcp/servers/{name}/connect` tries one that is on and was left out again (409 for one switched off) |
| `GET /mcp/servers/{name}/offered`, `PUT /mcp/servers/{name}/allow` (`{"tools": [...]}`) (items 426 and 524) | for a server added on the page (409 for one set up elsewhere, 404 for one that is not there): every tool it lists with what it says it does, and its allow list (`allow`, empty when it offers them all); then the tools to offer, at least one of the names it lists (422 otherwise, nothing changed), all of them keeping no list, connected first and kept with the server after, so a server that does not answer leaves the choice unwritten and a restart offers the same ones. The page offers the choice when `picks_tools` is true: the server runs, or it was left out with an allow list |
| `GET /plugin-gallery`, `POST /plugin-gallery/{id}` | the plugins of Customize › Plugins' gallery (item 542), each with where it stands (`available`, `installed`, `taken`), its skills, its repository and pinned commit, and the plugins Dot reads that are not the gallery's (`others`); install one: its pinned commit downloaded from GitHub, its skills checked against the hash the gallery keeps and put in Dot's plugins, offered to the runs that start after (404 for no such plugin, 409 for a name taken, a download that fails or files that are not the ones pinned, which leave nothing behind) |
| `DELETE /installed-plugins/{name}` | take a plugin out of Dot (item 497): its MCP servers stop, its skills leave the runs that start after (Dot reads its plugins again, item 542), and its folder in Dot's plugins goes to the Trash (409 for one of `[plugins] dirs`; of a plugin whose folder went without Dot, only the servers are stopped) |
| `GET /accounts`, `POST /accounts/{provider}/start`, `DELETE /accounts/{provider}/{address}` | the accounts of Google and Microsoft signed in through their own sign-ins and whether the client you registered with each is saved (item 422); start a sign-in, only from this Mac's own address (the provider's page to open; 422 when the client is not saved); sign an account out |
| `GET /vault`, `POST /vault/connect`, `POST /vault/token` (`{"token": "ops_…"}`), `DELETE /vault` | the owner's 1Password (item 493): whether `op` is installed and Dot holds a service account's token, and the vaults it reads with how many logins each; Connect, which makes the vault "Shared with Dot" and a service account that only reads it through the owner's `op` and the 1Password app, which asks first; a token of the owner's, kept once it reads a vault (422 for one that is no `ops_` token); both only from this Mac's own address; Disconnect, which forgets the token. No token and no value is ever answered |
| `GET /oauth/callback` (public, outside `/api`)                                  | where a service's sign-in page sends the browser back; hands the code to the sign-in that waits with its `state`, an account's of Google or Microsoft first, else an MCP server's, and answers a page of Dot's own look that names the account or service and says how it came out (item 437; 400 for a state nobody waits for, for an account's that failed, and for one that waited more than 10 minutes; what the address carries is shown only for a state that waits) |
| `GET /assistant`, `PUT /assistant` (`{"name": "Miro", "owner": "Ada", "introduced": true}`, each optional) | the name the owner calls Dot by, the owner's name (the first name of their account on this Mac until they give one) and whether Dot has had its first conversation (item 420); a change, live and in the config file (422 for a blank name, a control character or a name too long) |
| `GET /phone`, `POST /phone/pairing`                                             | the addresses a phone reaches Dot at (`[server] reach`), what Tailscale says of the Mac and whether the daemon keeps it awake (item 271); a new pairing code (item 267) |
| `POST /phone/tailnet`, `POST /phone/awake` (`{"on": true}`)                     | Tailscale set up for this daemon and its name written into `[server] reach` (422 with why when Tailscale is missing or not signed in); the Mac kept awake while it charges, or not (`[server] keep_awake`) (item 271) |
| `POST /phone/tailscale`, `GET /phone/qr/tailscale`, `GET /phone/qr/dot` | Tailscale's page in the Mac App Store while it is missing, else its app to sign in (item 421); a QR code (PNG) of Tailscale's download page, and of Dot's address on the tailnet once the daemon answers to it (422 before) |
| `POST /phone/pair` (public; `{"code": "7KQ2-M9XD"}`)                            | the access token for a good pairing code, once (403 when it is wrong, used or out of date) |
| `GET /channels`, `POST /channels/imessage/switch` (`{"on": true}`)              | the channels of Settings › Channels (item 572, [Channels](channels.md)): iMessage's switch, paired numbers and addresses, a code that waits, what macOS granted (`database`, `automation`), what keeps it from working (`problem`, `needs`) and what the channel sees of Messages' database (`seen`, item 595: when it looked, the cursor, the newest row, the sizes and times of `chat.db` and `chat.db-wal`, the last command and error, a `warning` when the log changed for two minutes and nothing Dot reads did, and `cursor_reset`, item 611, when the cursor had to come down to the newest row of a database rebuilt or restored: `{"from_row", "to_row", "at"}`), and the address its links open at (`links`: the name `[server] reach` gives, `null` when it names none and no link is sent); the switch, at once and in `[channels] imessage` (422 when the config file cannot be changed, and the switch stays as it was) |
| `POST /channels/imessage/code` (`{"handle": "+358401234567"}`), `POST /channels/imessage/confirm` (`{"code": "482 913"}`), `POST /channels/imessage/remove` (`{"handle": …}`), `POST /channels/imessage/check`, `POST /channels/imessage/poll` | send a pairing code to a number (with its country code) or an address through Messages; pair it when the code is typed back (422 for a wrong one, with the tries left, and for one out of date or used up); forget a paired one; show macOS's question whether Dot may control Messages, a minute for the answer; look at Messages' database now (Check now, item 595): a poll like the loop's, never reading a row twice with it, when the channel is on and a number paired, else a look that reads no message |
| `GET /snapshots`, `POST /snapshots/settings` (`{"key": "sound", "value": "pop"}`), `POST /snapshots/take`, `POST /snapshots/claim` | SnapShots (item 405): the settings as they stand, whether the Command key is watched and the captures waiting; a setting changed, live and in the config file; a capture taken by hand; the captures a composer takes into its draft |
| `GET /desktop/permissions`, `POST /desktop/permissions`, `POST /desktop/permissions/open` (`{"pane": "screen_recording"}`), `POST /system/restart` | the desktop permissions as macOS reports them, make macOS ask for the missing ones, show a pane, start the service's daemon again (item 409) |

`POST /runs`, `POST /runs/{id}/steer` and `POST /threads/{id}/queue` take a `key` of the caller's own (16 to 64 letters,
digits and dashes), which the pages send with each new message. The same message sent again with its key is not done
twice: it is answered with the run its first sending started or steered, or with the chat's queue; 409 while its first
sending is under way, for two minutes at most. A key is kept for a day.

Errors come as `{"message": …, "context": {…}}`, those of the web framework itself (a path that does not exist, a method
a path does not take) included; an error Dot did not expect is a 500 whose context names its kind and its words, and the
log keeps its traceback once (item 558: the answer is made inside the guard, so it carries the security headers and
`Cache-Control: no-store` of every other answer, and is not raised again for uvicorn to log a second time). Invalid
input is a 422 with the validation errors in the context.

**SnapShots** (item 405, after another coding agent's). In any application, both Command keys pressed together (⌘ ⌘,
item 414, as another coding agent's shortcut is), or the Command key tapped twice, captures the window in front and puts
it in the box of the open chat (or of the new chat) as a picture, without switching to Dot; a short sound says it was
taken, a soft white flash goes over the window (`flash`), and a card with the picture lifts off it and flies to Dot's
window as it comes to the front (`open_dot`): the desktop app, or the tab of a browser that shows Dot's pages, chosen
with AppleScript (Safari, Chrome and the browsers built on it, Arc; macOS asks once whether Dot may drive the browser,
and one it may not is brought to the front as it is), else Dot opened in the default browser; the browser of the page
that took the last capture is asked first. A helper process draws the flash and the flight (`python -m
dotagent.desktop.flight`, an application without a Dock icon that reads nothing of the screen), as the daemon has no
window of its own. The page takes the capture once it is seen, so the picture flies into its chip and the chip springs
into the box (`animations`); a page of this Mac with no box that came to the front goes to the new chat, whose box takes
it. With **Include app text** the window's text and controls, as the application shows them to accessibility and as the
model reads a window, come as a Markdown file beside the picture. The keys are watched by the daemon through a
listen-only event tap once macOS has granted Accessibility to the app that runs Dot; it reads only whether a Command key
went down or up (the left and the right apart) and whether another key was pressed meanwhile (⌘C ⌘V is two shortcuts,
not a capture), never which key. Only the pages of this Mac take captures: a phone's page, reached through `[server]
reach`, does not take one away from the Mac's box. Dot's own window (the desktop app, or a browser showing Dot) and a
password manager's are never captured; what lies over the window shows in the picture, as on the screen. A box on screen
takes the capture as it comes; with none on screen (another page open) the daemon keeps it until a box is made. Settings
› SnapShots switches it (`[snapshots] enabled`), the text (`app_text`), the sound (`sound`: click, pop, glass or off),
the flight and the spring (`animations`), the flash (`flash`), Dot coming to the front (`open_dot`), and says whether
the key is watched and, when it is not, why; while the permission is missing the daemon tries the watch again every few
seconds, so granting it needs no restart. A capture past the ten files of a message is left out, and the box says so.
Left for later: a shortcut of your own.

## The desktop shell

`desktop/` is a native Mac window around the web UI, built with Tauri 2: a few megabytes, the one web UI, and a
menu bar item. The daemon stays owned by launchd (`dotx service install`); the shell connects to it and never runs
the agent itself. On a Mac that has no Dot, the shell installs one first (**Installing Dot**, below).

- **Installing Dot** (item 575). With no Dot on the Mac, the app has no daemon to connect to, and it makes one. Its
  window is the page **Set up Dot** (`desktop/ui/installer.html`, bundled with the app, in the look of the down page),
  in a window of its own, and it is shown only when nothing of Dot is here: no launchd job of Dot's, no daemon that
  answers, no `config.toml` and no token in Dot's home (but for an install of the app's own that stopped, items 4 and
  5). Where any is (the owner's checkout, a Dot installed by hand), the app shows what it always showed, the UI or
  the page that says Dot is down, and never the installer; each step asks again before it runs, so a Dot that
  appears while the page is open stops the install. The steps, from the shell
  (`desktop/src-tauri/src/installer.rs`), with the output of each kept in `installer.log` of Dot's log folder (the key
  masked) and listed on the page:
  1. **What the Mac has**: macOS 15.2 or later (older only warns: the desktop tools need it, nothing else does),
     Google Chrome (missing only warns, with a button to Chrome's download page: the browser tools need it) and that
     the app runs from where an app stays (not from its disk image, nor the place macOS runs a downloaded app from
     before it is moved).
  2. **The models**: what `dotx setup` offers, less OpenCode's own agent (the list is `providers.json`, which a test
     keeps equal to `CHOICES` of `cli_setup.py`): Ollama, when it answers on 127.0.0.1:11434 (its models are listed; a
     cloud model needs `ollama signin` once), or an API key of OpenAI, Anthropic, OpenRouter or OpenCode Go, in a
     password field.
  3. **Install**, one button, four steps. `uv --no-config tool install --python 3.13 dotagent-macos==<the app's
     version>`, with the `uv` the app carries and `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `UV_PYTHON_INSTALL_DIR` and
     `UV_CACHE_DIR` under `runtime/` of Dot's home, only Pythons that uv downloads (`UV_PYTHON_PREFERENCE =
     only-managed`) and no `uv.toml` read; then `dotx setup --model provider:model --no-service` with the key on
     standard input (`--key-stdin`), never in an argument, an environment variable or a log; then `dotx service install
     --launcher <the launcher in the app's Resources>`, which copies the program of Dot.app, built and signed in the
     release, in place of the clang build (so Xcode's Command Line Tools are not needed); then the window waits for
     the daemon to prove itself and opens the UI as it always does. Dot's commands run with the Python of the package's
     environment (`python -P -m dotagent`), as the job runs the daemon. Each program is started with a short list of
     the app's environment (home, temporary folder, language, Dot's folders, and the proxy and certificate settings
     uv needs behind a company's network) and what the step sets, and nothing else. The job's `PATH` begins with
     `runtime/app-tools`, a folder of two links, to `rg` and `uv` of the tools in the app's Resources and to nothing
     else, which the app makes again at every start where the app is: Dot's search finds ripgrep with no Homebrew,
     moving the app does not lose it, and nothing else that lies in the app's tools folder is on the `PATH` of a
     daemon that holds the keys of your accounts.
  4. **A step that fails** says which, what it printed (the key masked) and what to do, and **Try again** goes on from
     it; the steps that are done are not run again, whichever step failed (the service step too, when launchd loaded the
     job and the daemon was slow to answer). Before the package is installed nothing is made, and a failed install
     leaves no marker. A package that PyPI does not have yet (a version reaches PyPI some minutes before its app is
     published, see [Releasing](development.md#releasing)) is said as that.
  5. **The marker, and what makes the install complete.** `runtime/app-install.json` records each of the first three
     steps as it finishes: the package installed (written once `uv` succeeded, with its version), the models set up, and
     the service installed. The install is **complete when its service is installed**. The fourth step, the wait for the
     daemon's first answer, is shown to you and decides nothing: when it runs out (the first start can wait for macOS to
     ask whether Dot may use a folder) and the app is quit, the install is complete all the same, and nothing depends on
     the app having seen the daemon answer. At every start of the app, a complete install shows what the app always
     showed, the UI, or the page that says Dot is down where the daemon does not answer, and its package is kept in step
     (see Updates below). An install with a step missing shows the installer, which goes on from the first step that is
     not done, as long as no daemon answers and the job is not another program's; with the models done it asks for none
     and goes on at the service, and `dotx setup` is never run again over a config of yours (a choice of models that
     reaches it then is refused in words, and **Change the models** is not offered: change them in Settings once Dot is
     open). What is on the disk adds to what the marker records: a `config.toml` is the models done, a launchd job that
     is the app's own is the service installed, and the package counts only while its Python is there. A marker that an
     older build wrote recorded no steps, and what it said instead (that the daemon had answered) is not believed: it is
     read by the disk alone, each step done only where the disk shows it, so that a marker that claims everything over a
     package alone leaves the installer to go on and the package alone as it is.
     **The job is the app's own** when its property list, read as launchd reads it and not as text, is a dictionary with
     the label of Dot's service and no `Program` of its own, whose `ProgramArguments` begin with Dot.app's program in
     Dot's home and, exactly, the Python of the package's environment in the runtime folder, as `dotx service install`
     writes them when the app runs it. A job of a checkout on the same home, whose `PATH` names `runtime/app-tools`, a
     list that mentions the runtime folder in a comment or in another key, and a job of another program that has that
     Python for an argument are not it: they are another Dot's, which the installer never runs over and the upgrade
     never restarts. **A file that a command of a run may have put something else at is not trusted**: the marker, the
     job's property list and the installer's log are opened without following a link and without waiting for a FIFO (the
     open does not block, and what was opened is checked to be a plain file), and read only up to the few kilobytes they
     hold. What is not a plain file is read as no marker, as another program's job and as no log (the app says so of the
     log on its standard error); it holds nothing up.
  The page's commands are the shell's own (`installer_inspect`, `installer_status`, `installer_start`,
  `installer_retry`, `installer_open`), which only the page of that window may call (below). The runtime folder is Dot's
  own code to the sandbox, like Dot.app: a run's commands cannot change it.
- **One interface.** The window is the web UI at the size the screenshot tour reviews (1280 × 860, down to a
  phone's 390 wide). The system draws only the traffic lights over the page, on the centre line of the top bars with
  room above and below them; the page keeps room for them in the sidebar's head, as tall as the top bar so that its
  button stands on the same line (the settings' nav has the same head, with Back to chats under it, item 577), or in
  the top bar while the sidebar is away, and the window drags by those bars.
  The themes, the colour scheme and the density are the web UI's own settings.
- **Signed in by itself.** The shell reads the daemon's address from `config.toml` (`[server]`, or the defaults without
  that file) and the token from the token file under the home directory, and opens the UI with the token in the
  fragment, never typed, once the daemon has proven that it holds that token (see the server above; each poll checks it
  again before it sends the token). While the daemon is down, the window shows Dot's wordmark and the one command that
  starts it (`dotx service install`, written `uv run dotx service install` in a clone), and opens the UI by itself when
  the daemon answers. When the shell could not read one of those files, that page and the menu bar item's tip say which
  and why instead, and the shell guesses no address: a `config.toml` that is not valid TOML, with the line and column
  of the fault; a file it may not read, in the system's words; no token file yet, which the daemon makes the first time
  it starts. When something answers at the address without the proof (another program holds the port, or a daemon of
  another Dot home or of an older version), the page says "Something else answers at" that address and gives the `lsof`
  line that shows what (item 558), not the command that starts the daemon, which would be refused. The window leaves
  the UI for that page only after ten polls in a row go unanswered (item 200): a restart of the daemon, a few seconds,
  and one slow answer of a busy daemon leave the open page, which says that it reconnects and reads again what changed
  once the daemon is back.
- **The menu bar item** is Dot's mark in the state of the whole of Dot (looking at you at rest, glancing up while a
run works, a blue pastille while something waits for you) with the count of what waits for you beside it, read from
`GET /api/activity/count` every second. A click opens the window; the menu has Open Dot, New chat, Check for Updates…
and Quit. Closing the window keeps the shell in the menu bar.
- **⌘⇧Space** anywhere brings the window up on New chat, with the cursor in the box. When another app holds the
  shortcut, the shell starts without it and its menu says so; New chat stays in the menu. While the window shows the
  page of a daemon that is down, New chat brings it up as it is, and what the page says stays.
- **Links** that a page opens in a new window, such as the links of a model's answer, open in the default browser;
  the shell opens no window of its own for them, and only web addresses (`http`, `https`).
- **Notifications** are the system's: the web UI sends them through the shell (Settings › General › Notifications,
  the same switch). A click brings Dot to the front, where Waiting for you leads to the run.
- **Only Dot's pages call the shell.** The shell's own pages and the pages of the daemon's address may call the shell,
  to send a notification or drag the window (by its bars, and zoom it with a double click there): the address it reads
  from `config.toml`, and any it read there before since it started. They get no other command of Tauri's core (item
  561): not the image commands, which read a file from disk, nor the menu bar item's and the app menu's, nor events. A
  page of any other address, another port of the Mac among them, gets nothing from it. The commands of the installer
  (item 575) are named in the app's manifest (`build.rs`), so each needs a permission of its own, and only
  `capabilities/installer.json` grants them: to the window `installer` and to local pages, with no remote entry. The
  window `main` (the UI, the down page), the daemon's pages and any other address are refused, which a test asks of
  the app built with its real capabilities. The installer window shows its own page and nothing else: it refuses any
  other address and any new window, and gives its page nothing it asks of the web view (the microphone, the camera,
  where the Mac is), since wry grants every request no one answers (item 594).
- **Files** drop into the composer as in the browser.
- **The microphone** (item 594). The composer's dictation (item 574) is WebKit's speech recognition, which macOS gives
  an app only once it says why it asks (its `Info.plist`: the microphone and speech recognition) and, under the hardened
  runtime, is entitled to the microphone (`Dot.entitlements`, the runtime's one exception the app asks for). macOS asks
  you the first time the microphone is pressed, and System Settings › Privacy & Security › Microphone and Speech
  Recognition list Dot after. The shell gives the microphone to a page of the daemon's own address alone, and nothing
  else a page asks of the web view, to any page. It is told what is asked, not by which frame, and judges the window's
  page; the UI's frames (a page a run showed, a drawing of a file) are sandboxed, which WebKit refuses before the shell
  is asked. The page given the microphone could record sound with it too (`getUserMedia`), as a site given a browser's
  microphone can; Dot's takes only the words. What you say may go to Apple to be recognized, as in Safari
  ([Safety](safety.md)). A Dot.app built before it can do neither: its composer says so and names macOS Dictation (item
  593).
- **Updates** (item 432). As it starts, and from **Check for Updates…**, the shell reads `latest.json` of the latest
  release on GitHub (`plugins.updater` in `desktop/src-tauri/tauri.conf.json`). A newer version is offered in a dialog:
  **Install and Restart** downloads it, checks that it is signed with the updater's key and that the signature names the
  version offered, puts it in place of the app and starts the shell again; **Later** leaves it for the next look. The
  look at start says something only when it finds a newer Dot; the one from the menu also says when this Dot is the
  newest, or why it could not look, and that Dot is looking already while a look is under way, so that an update is
  never installed twice. The dialogs are the system's, shown by the shell itself: no page can show one. The daemon stays
  as it was installed, from its checkout or from PyPI, with one exception (item 575): in an install the app made, its
  package follows the app. At its start, when the package installed (read from its metadata folder) is older than the
  app, which has just updated itself, the shell runs `uv --no-config tool install --reinstall
  dotagent-macos==<the app's version>` and then `dotx service restart --when-idle`, so that the daemon starts again
  with it once no run works. Only an install the app made and finished (its marker, with the service installed: item 5
  above) is ever upgraded, whether or not the daemon answers at that start, never a checkout or a Dot installed by
  hand, and a package newer than the app is left alone. Nothing is done, and `installer.log` says why, while the app
  runs from where it does not stay (the disk image it came on, or the copy macOS makes of a downloaded app): the links
  to its tools would point into it, and into nothing once it is ejected, and the daemon would lose `rg`. Nothing is
  done either while the service's job is another Dot's (a checkout's `dotx service install` took its place: its
  daemon is not the app's to restart). A failure is told in a dialog with the log's place (`installer.log`), and the
  package stays as it was.

Build it on your Mac with the Rust toolchain and Node installed:

```sh
cd desktop
npm install
npm run build      # desktop/src-tauri/target/release/bundle/macos/Dot.app (and a .dmg)
npm run dev        # a debug window with live reload of the shell
```

That build is signed ad hoc, for this Mac, and carries none of the tools the installer needs (`uv`, `rg` and the launcher
of Dot.app, which `desktop/fetch-tools.sh` puts in `desktop/src-tauri/resources/tools`): it never shows the installer,
and shows what it always showed. The app other Macs install, signed with a Developer ID, notarized and
updating itself, with those tools in its Resources, signed too, is a release's, which `desktop/release.sh` builds (see
[Releasing](development.md#releasing)). The
icons come from Dot's mark: `uv run python desktop/icons/render.py` draws the favicon, the app icon (the mark lit as a
ball on a dark tile), the three menu bar icons and the web app's icons, and `npm run icons` makes the bundle's set from
the app icon.
