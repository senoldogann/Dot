# Dot comes to you

Dot does not only answer when it is asked. Each morning it writes you a brief and sends you its first line (item 570),
and its watchers tell you when a mail, a calendar event or a GitHub notification you care about comes (item 571). Both
are set on Schedules, and what Dot tells you on its own goes through one place, which the phone's push, the desktop's
notifications and, when it is on, iMessage share (see [Telling you](#telling-you)).

## Morning brief

The **Morning brief** card stands under your schedules on the Schedules page, as one line: its name, when it writes
("Every day at 08:00", or Off) and its switch (item 609); its **Settings** button opens them in a dialog, so that the
page keeps its size (item 615). The settings are the time of day (08:00, read in `[scheduler]
timezone`, which is the zone of this Mac unless the configuration says another), every day or the weekdays, and a box,
**Also tell me about…**, for your own lines, one thing each (at most 20 lines of 300 characters). Switched on, Dot
writes the brief at that time, and your phone gets the push of its first line, which opens the brief in its chat.
**Write one now**, in the dialog's foot beside Save, saves what you changed first and starts one by hand, as Run now of
a schedule does; the dialog says when the next one comes and when the last one was written, and leads to it. Cancel, the
cross and Escape close the dialog, asking first whether to discard what you changed; a refusal of the daemon (a line of
more than 300 characters) is said inside it, with what you typed still there.

**It is a schedule.** The brief is a schedule of the scheduler you already have (see
[Schedules](memory-and-schedules.md#schedules)), marked as the brief, so that it shows in the list under the card with a
**Brief** label, and switches off there too, which the card follows. It fires once at its time; a firing missed because
the daemon was off or the Mac asleep is skipped, not replayed; its failures count as any schedule's (`[scheduler]
stop_after_failures`). Its row has no form of its own: **Edit the brief's settings**, in its **…**, opens them, since
its goal is not text
you edit. A brief that was never switched on makes no schedule, and one switched off keeps its schedule (switched off)
and what you set.

**It only reads.** Its run mode is `research`, whatever its row says (the mode is set again at every firing, so a change
of the schedule through the API does not make a brief that acts): it can read, search, browse and look at the screen,
and it can delegate only to research runs (see [Safety](safety.md#safety)). It runs in the approval mode `[policy]
approvals` says, unattended, as a schedule's run does: a question nobody answers is refused or kept for you as `[policy]
unanswered` says. Reading your mail marks the run untrusted, as it marks any run (see [Safety](safety.md#safety)); a
brief never has the means to send, change or delete anything.

**What it is given.** At its time Dot builds the run's goal, which is its few words and your lines: "Write my morning
brief for Friday 23 October 2026." and, when you wrote any, "Also tell me about:" with them. The reviewer of the run
takes that goal as your instruction, so nothing else is in it. In front of the goal, in the run's first message and not
in what its chat shows, are Dot's instructions for the brief and Dot's record of itself, in a fence of their own, as the
dated answers of a schedule's earlier runs are:

- what waits for you: the questions and approvals that runs left, and the drafts waiting for you to send them;
- how many plugins the nightly look proposed wait on Customize › Plugins (only the numbers, item 711);
- the runs that failed since the last brief, with the first line of what each said;
- the campaigns at work, with when the next session is and the last note of the ledger;
- the schedules that fire today, with their times.

Each line of it that a run or a campaign wrote is quoted, with its secrets masked and cut to 160 characters, and the
fence is one nothing in it can close: it is the record of what happened, data for the run to report and not
instructions. **The brief takes the taint of the runs it quotes** (item 243's rule for a run that reads another's
words): the run of each question, approval or draft it lists, each failed run it names and the run of each campaign's
last note are the digest's runs, and the brief starts untrusted when any of them ended untrusted, since a page or a mail
may have said something to a run that read it. It does so for the first brief of its chat and for one that goes on in
it, which also takes the chat's last run. The names of the schedules and the titles of the campaigns come with no run's
taint: a campaign's title is the owner's, and a schedule's name is the owner's or a run's that asked first when it had
read outside content (saving a schedule asks then). The newest 200 runs are read for the failed ones; a Mac that ran
more since the last brief says in the title of that part that it lists the failures among them only.

**What it reads.** With a Google or Microsoft account signed in (see [Mail and calendars](runs.md#mail-and-calendars)),
the instructions give the run the calls to make for each account: today's calendar with `calendar_events`, and the mail
of the last day with `mail_search` (the days since the last brief for a brief that comes after a weekend, at most
three), and ask it to tell only what needs you: a question for you, a request, a deadline, a bill, a person waiting.
With none signed in, the run says so in one line and writes the rest; that is not a failure. A call that fails is said
in one line and the brief is still written. In the presets that ask for every read (`cautious`, `supervised`) nobody is
there to answer, so the reads of mail and calendar may be refused and the brief says what it could not read.

**What you get.** The answer is short, the most important first: its first line is one sentence of at most 140
characters, which the push shows with the title "Your morning brief" and which opens the run when you tap it (with
iMessage on, the same line goes into your conversation with yourself too, see [Telling you](#telling-you)); then a few
lines for each part that has something. Every brief is a new run in one chat, **Morning brief**, kept for all of them:
the run reads the conversation of the runs before it, as a schedule that goes on in its chat does, and starts untrusted
when one of them ended so. A brief that failed is told as any schedule's failure ("A task failed", at the level its row
says: always).

What goes out of the Mac is what any run's goes (see [What leaves this Mac](safety.md#what-leaves-this-mac)): the record
above, and what the mail and calendar tools return, go to the model the run runs on, and the first line of the answer
goes to your phone's push service and, with iMessage on, to Apple's iMessage servers. Nothing is sent anywhere else.

## Watchers

**Watchers** is the panel under the brief on Schedules (with none, its head says *None yet* and a line says what a
watcher does, item 609); **New watcher** makes one, and a watcher's pencil changes it, in a dialog (item 615) that
asks before it drops what you typed and says inside it why the daemon refused (twenty watchers are the most). A
watcher has:

- a **kind**: **New mail** in a Google or Microsoft account you signed in (see [Mail and
  calendars](runs.md#mail-and-calendars)); **Calendar**, an event about to start in one of those accounts; or
  **GitHub**, your own notifications: a review asked of you, a mention (yours or your team's), an assignment, and a
  check or workflow run of yours that failed;
- **what you want to hear of**, in your own words ("my bank, my landlord, anything about the visa"): needed for mail,
  optional for GitHub (then the notifications above are told as they are) and for the calendar (then every timed event
  is told);
- **minutes**: how often a mail or GitHub watcher looks (5 by default), how many minutes before an event a calendar
  watcher tells (15 by default);
- **what it does**: **Tell me** (the default), or **Tell me and start a task** with your goal text ("draft a reply in my
  voice and keep it as a draft").

**It looks; nothing reaches the daemon.** The daemon looks for each watcher at its own interval, in a loop of its own
beside the scheduler (`[watchers] tick_s` is how often it sees which are due). There is no inbound webhook: the daemon
stays on the loopback address, and a push of GitHub's or of a mail provider's to it is left for later. A watcher keeps
its place in the store, so a restart neither repeats nor misses: mail asks from five minutes before the last look began
(what came while the daemon was off, up to a day, is looked at by the first look after) and leaves out what it handled
by its id, GitHub's request is conditional on `Last-Modified` and waits at least as long as `X-Poll-Interval` says, and
an event is told once for the time it has (one that moves is told again at its new time). **The first look of a watcher
only notes what is there** and tells nothing, so a new watcher does not tell the mail already in the inbox. **Check
now** on a row looks at once: the first look, or what came since. A look can take minutes, so while one goes on the
row's button shows a spinner and takes no second press (each press had queued another whole look behind the first;
item 612). A row on a phone gives the watcher's name the room between the switch and the edge: its kind chip goes under
a long name, an address that is longer than the row ends in an ellipsis (its title has it whole), and the three buttons
stand at the end of the row's last line.

**Mail.** A new unread mail of the inbox (Gmail's or Outlook's) is read as the mail tools read it: sender, recipients,
subject and the first lines. For each, one question to the fast model (`[models] fast`): is it one you want to hear of,
by your words, and one line why. The mail is data written by others, so it goes in the fence of the mail tools, alone in
its question, with your words outside it, and the model answers by calling one tool, a verdict: one mail cannot change
the verdict of another. A mail the model cannot judge (no key, a pause, a failure, a reply with no verdict, or one that
does not come within 30 seconds) is **told, not dropped**, with the words "it could not be checked against your words".
A mail that is read already, or came more than a day ago, is not told. The mails of a look are dealt with one at a
time, the oldest first: each is judged, told and written down as handled before the next is asked about, so a look
that is cut has told and kept what it finished, and the next look takes the rest (a look judged them all before it told
any, so one cut told nothing and the next asked about the same mails again, item 612).

**GitHub.** The notifications API with the personal access token Dot keeps when you add GitHub on Customize › Connectors
(`MCP_GITHUB_GITHUB_PAT` in its `.env`); with none, the watcher says on its row to add it. GitHub's notifications need a
classic token with the notifications scope: a fine-grained one cannot read them, and the refusal says so. Without words,
a review asked of you, a mention, an assignment and a check of yours that failed are told; with words, the fast model
judges each new notification (its repository, kind, reason and title) as it does a mail.

**Calendar.** No model. It tells an event's title, time and place, its minutes before it starts. All-day, cancelled and
declined events are left out, and, when you gave words, those that do not have them in their title, place or notes. It
looks at least every five minutes, and again when the next event comes inside its minutes.

**Limits.** At most `[watchers] tells_per_hour` (20) tells from one watcher in an hour; what comes past it that hour is
said in one line ("and 7 more"). A mail or notification handled is never told twice. One look judges at most the newest
`judged_per_look` (25) of them (oldest first), and reads at most 50 mails: the rest of a flood is left unjudged and not
told, and the row says how many. A look ends at 180 seconds, between two mails or in the question it waits for, and
says so on the row with how far it came ("it had dealt with 3 of the 7 new ones (2 told)"); an item it was telling is
told and written down first. One question of the fast model has 30 seconds of its own, under the look's and not the 420
of `[agent] model_call_timeout_s`, which is for a run's step and which one stalled answer could outlast the look with:
a question that is not answered is a mail the model could not judge. A watcher that fails (no account, a refused token,
a provider that cannot be reached, a look that was cut) says so on its row, with the error, and tries again no sooner
than five minutes later; the others look on.
Nothing looks while the emergency stop is engaged. A watcher switches on and off on its row, and at most 20 can be made.

**What a tell is.** A push to your phone (title: the watcher's name; text: the mail's sender and subject with the
model's line, the notification, the event), the same in your conversation with yourself when iMessage is on (see
[Telling you](#telling-you)), and a line kept in the chat **Watchers**, which the tap opens: the chat shows
in the sidebar, unread, with the newest tell as its last line. The line is a run that no model made (its goal says which
watcher and what kind, its result is the tell), kept by `RunManager.keep_notice` and marked untrusted: what a third
party wrote is in its result only, never in its goal, which a later run in that chat would take as yours. A tell that
could not be kept (a run of yours is at work in that chat) goes out all the same and opens Schedules.

**The run a watcher starts.** With **Tell me and start a task**, each item told starts a run, in a chat of its own named
for the watcher and the item, at most `[watchers] runs_per_hour` (5) an hour from one watcher: a tell past that goes out
with "no run", and a run that cannot start is said in the tell, not lost. The run's goal is your goal text and nothing
else. Its first message holds the item in the fence of the mail tools, with where it came from and how to read more of
it (a message's id, for `mail_read`), and your goal after it. It is a task nobody watches (its questions are left, as a
schedule's are), in the default approval mode (`[policy] approvals`), and **it is marked untrusted from its start**:
what a stranger wrote set it off, so what reaches other people or lasts asks first, whatever the preset. In `yolo`
nothing asks, but a yolo run marked untrusted has the review model check what it does (see [Safety](safety.md#safety));
keep the default approvals for a watcher that starts runs unless you want it to act alone. Its end is told as any run's.

The watchers' settings are in `[watchers]` of `defaults.toml`: `enabled`, `tick_s`, `tells_per_hour`, `runs_per_hour`
and `judged_per_look`.

## On its own

**Dot works for you on its own while the Mac is idle** (item 715, an idea of makecindy/cindy's companions). The **On its
own** card under the watchers on Schedules has its switch (off until you switch it on) and Dot's own list: the things it
means to do for you, each with why it helps you, where it stands (to do, doing now, done, let go) and how many runs
tried it; a row opens the chat of the run that did it. A run puts a thing on the list with the tool `todo_add` (a
follow-up its work calls for, something you asked to be done later, a chore it noticed), and takes back with `todo_drop`
one it put on it itself by mistake, with why (item 821: the look of 2026-10-29 listed an empty "placeholder" it could
not take off, and the run that was then given it changed Dot's database to close it). A run's `todo_add` refuses a title
of one word (words counted by their spaces, so a task in a script written without them is refused too), or the same as
its reason: it says no work; your own box takes what you write. A run given a thing ends as it is done; Dot marks it
done, or to try again when the run failed, and the run is told never to change the list or Dot's database itself. You
put one on it with the box at the card's foot; **Remove** takes a thing to do off it, **Again** puts back one that was
let go.

**When it works.** The scheduler looks at every tick. Switched on, it starts one of its runs when none of the Mac's runs
works, nobody touched the keyboard, the mouse or the trackpad for `idle_minutes` (10), `gap_minutes` (15) have passed
since its last run ended, the emergency stop is not engaged and fewer than `runs_per_day` of its runs started in the
last day (0, the default, sets no limit; counted from the runs the database holds, so a restart of the daemon does not
count from none). One of its runs works at a time. When macOS cannot say how long the Mac has been idle, nothing starts,
and the card says so. In `peak_hours_utc` (two hours of a weekday in UTC, such as `[12, 18]`, a provider's dearer hours;
none by default) nothing starts but an urgent thing of yours, as while you use the Mac, and the card says the hours in
the Mac's time (item 826: Ollama Cloud's price outside 12 to 18 UTC on weekdays, and all weekend, is half); a run under
way goes on, and the schedules keep their times. A thing you put on the list yourself with `[URGENT]` in its title or
why (in any case) goes before the older ones and may start while another run works or you use the Mac (item 809); a
thing a run put on the list, or one that came of content third parties can author (a page, a mail), is never urgent,
however it is marked. It does the oldest thing to do, in a chat of its own named after it (under **Dot's work** at
the foot of the sidebar, folded, items 822 and 855); with nothing to do, it looks for work every `look_every_hours` (6)
with a research run, which reads what it remembers of you, the record the morning brief reads (what waits for you, the
runs
that failed since the last look, the campaigns at work), the titles of your newest chats and the list, and puts at most
three things on it. That run is a research run: it writes nothing but its list, its notes and Dot's memory, as any
research run may. Its record quotes no words a third party wrote unless it starts untrusted: the chats it names are the
ones a person started (a watcher's, whose title carries a mail's subject, and Dot's own are left out), and a thing on
the list that came from such content is counted, not quoted; the runs whose words the record does quote give it their
taint, as the brief's do.

**How it does a thing.** The run is a task in `[initiative] approvals`, `yolo` by default whatever your own mode is
(item 756: in `ask`, a run of its own asked to put a thing on its own list and waited on nobody), nobody watches it (a
question it has is left for you, as a schedule's is), and its goal is Dot's words, "On my own initiative: …": the review
model takes none of it as your instruction, in that run or in a later one of its chat, a resume too (`auto` asks you
what reaches others), and a thing that a run put on the list after reading content third parties can author (a mail, a
page) is done by a run that starts untrusted, so that in `yolo` too the review model refuses a message, a post, a
payment or an upload that no word of yours calls for (item 525). A run that read nothing of the kind (a thing from your
own chats) is checked by nobody in `yolo`, and a payment it decides on goes out as it decides, which is what `yolo`
means (item 443); `[initiative] approvals` set to `auto` or `ask` has such a run ask you first.

But in a chat Dot opened on its own initiative, three steps wait for you in every mode, `yolo` and `auto` too (item
850, your word of 2026-10-30):
- a message sent for you: an email, a chat or text message, a reply;
- a post in your name;
- a project's change put live: a deploy to production, a pull request merged, a push that names the main branch
  (see [Safety](safety.md#safety)).

This holds for every run of that chat, as the owner's answers in it start runs too, and for the runs they delegate.
- The question is yours alone: the review model never answers it, and a rule of yours that allows the step does not
  answer it either.
- Its card shows the step as the policy reads it, and your yes is for that step.
- Nobody watches the run. Its question waits for you while the run waits, and one nobody answers in time is a
  refusal, after which the run goes on with the rest. With `[policy] unanswered = "park"` a release is kept for you in
  its chat (item 482). A message or a post never is, since a yes given later never covers a step that reaches other
  people (item 483).

In `yolo` your own chats ask you before a payment, a message or mail sent for you and a public post (item 860); in a
schedule's run in one of your chats the review model judges a message or a post, and a payment is handed to you; a
schedule's own chat asks nothing of the kind.

A thing is not put on the list twice: not while it is to do or being done, nor for 30 days after it was done or
let go. Done, it is marked so with the first line of the run's answer and you are told ("Dot did on its own", the push,
the desktop, iMessage when it is on). A run that failed leaves it to do, up to `attempts` (3) runs; then it is let go
and you are told. A run you stopped lets it go at once; one the daemon's stop or the emergency stop ended (the stop of
every run, or the STOP file the run saw as it worked, item 790) leaves it to do; one the daemon was doing when it shut
down is to do again when it starts, and one it was doing when it stopped
without shutting down (a crash) counts that try, so a thing that brings Dot down is let go after `attempts` tries. A
thing tried as often as `attempts` allows, after the limit was lowered, is let go, and you are told. A thing whose run the owner resumed
is left to that resume while it works, and done once it ended done; another message in its chat is no sign it was done.
A thing whose chat holds a question or a step left for you, unanswered, or a run at work, is not started again in
another chat (item 868), the urgent ones too; its tries are not used up while a question waits. A run of its chat that
goes on from its last one takes it over: a resume, the run your answer to its question started, or the one your answer
to a step it kept started (each carries the goal of the run it goes on from; a message you write is a goal of its
own). Its end is the thing's, as one of its own runs' is: done, the thing is done and you are told; failed, that is
a try; stopped by you, the thing is let go. A question you skip, or a step whose time ran out, ends the wait, and the
try that left it stays counted: a thing whose last try it was is let go then, and you are told.

The settings are in `[initiative]` of `defaults.toml`: `idle_minutes`, `gap_minutes`, `look_every_hours`, `attempts`,
`runs_per_day`, `approvals` (`yolo`; `ask`, `auto` or `supervised` make its runs ask as yours do) and `peak_hours_utc`;
the switch and the time of the last look are kept in the database.

## Telling you

What Dot tells you on its own, the brief and the watchers, goes through one function, `tell_owner(bus, title, text,
url)` in `dotagent/notify/owner.py`: a title, a line or two of text and the route of Dot's own pages that a tap opens
(`#/runs/<id>`; an address of anything else is refused, so that nobody whose words are in a tell can send you
elsewhere). It is an event of Dot itself, `owner_told`, live only like the other notices (see [The
daemon](daemon.md#the-daemon-the-web-ui-and-the-service)): the web push sends it to every subscribed phone, the page
shows it as a desktop notification where those are on, and the channel router of [Channels](channels.md) (item 582)
sends it into your conversation with yourself while iMessage is on and a number is paired; any way to reach you added
later listens for the same event. It is not stored: what is kept is the run it opens.

**On iMessage** a tell is one message, marked `◆ `, of the title, a colon, the text and the link to the page it opens
(`#/runs/<id>` or `#/schedules`, at the address of the links of the channel, the name `[server] reach` gives and never
one that carries the access token; with no such name there is no link, item 611):
*Your morning brief: Nothing needs you today. https://…/#/runs/…*. It is masked of the secrets Dot holds and sent as
plain text, and it is never read back as a command, since every message of Dot's in that conversation starts with `◆`
(and the link that Messages may split off its end is Dot's own too, see
[Channels](channels.md#what-dot-reads-and-what-it-does-not)). A channel gets at most one such message in twenty
seconds (`ASKS_APART_S`, the pace of its other lines): a watcher that tells several things in a row is told as its
first one, and the rest as one message when the twenty seconds are over, *Bank mail: and 3 more*, which links to the
newest. A tell that waited alone goes as itself. With iMessage off, or no number paired, nothing is sent there, and a
tell that was held when it was switched off is let go; the phone's push and the page's notification are not changed by
it.

## The API

| Route | Purpose |
| --- | --- |
| `GET /api/brief` | the brief as its card shows it: whether it is on (its schedule exists and is switched on), `time` and `days` (read from the schedule's cron; `custom` when that was changed in the list to a time the card cannot show), your `lines`, the `zone`, its `schedule_id`, when it fires next and last fired, how the last firing went, the run to open (`last_run_id`) and how many mail and calendar accounts are signed in |
| `GET /api/watchers` | the watchers, in the order they were made, each with its choices (`kind`, `account`, `words`, `minutes`, `action`, `goal`, `enabled`) and how its looks go: `primed` (its first look is made), `looked_at`, `next_look_at`, `last` (what the last look came to), `error` and `error_at` (why the last one failed), `told` (tells in the last hour); never the keys of what it handled |
| `GET /api/watchers/options` | what the form offers: the `accounts` signed in, whether GitHub's token is kept (`github`, never the token), the `minutes` each kind starts with, `tells_per_hour` and `runs_per_hour`, and whether the watchers look at all (`looking`) |
| `POST /api/watchers` | make one, 201: `{"name", "kind": "mail" \| "calendar" \| "github", "enabled", "account", "words", "minutes", "action": "tell" \| "run", "goal"}`; 422 for what its kind lacks (mail needs an account and words, the calendar an account, GitHub takes none, a run needs its goal), for `minutes` outside 1 to 1440, and past 20 watchers |
| `PATCH /api/watchers/{id}` | change the whole of its choices, as for a new one; what it handled is kept unless its kind or its account changed, and one switched on again looks at once (404 for none) |
| `DELETE /api/watchers/{id}` | delete it; answers what was deleted; the chats of the runs it started stay (404 for none) |
| `POST /api/watchers/{id}/try` | Check now: one look at once, whatever its time: `{"outcome": {"first", "found", "told", "summary", "error"}, "watcher": …}`; a look that fails answers 200 with the failure in `outcome.error`, and `found` and `told` say how far it came (404 for none; 409 for a watcher switched off, while `[watchers] enabled` is false and while the emergency stop is engaged) |
| `GET /api/initiative` | Dot's own initiative (item 715): `enabled`, `looked_at` (its last look for work), `idle_readable` (whether macOS says how long the Mac has been idle; without it nothing starts, and the card says so), `approvals` (the mode its runs start in, `[initiative] approvals`, which the card says: item 756) and `todos`, what is being done and to do, then what was done or let go in the last two weeks, each with `id`, `title`, `why`, `source` (`owner` or `run`), `status`, `attempts`, `run_id`, `note` and `updated_at` |
| `PUT /api/initiative` | switch it: `{"enabled": true}`; a run of it under way goes on until its end or a stop |
| `POST /api/initiative/todos` | put a thing of yours on the list, 201: `{"title", "why"}`; 422 when it is on it, or was done or let go in the last 30 days |
| `POST /api/initiative/todos/{id}/drop`, `.../again` | take a thing to do off the list; put one that was let go back, its tries from none (404 for none) |
| `PUT /api/brief` | set it: `{"enabled": true, "time": "08:00", "days": "every" \| "weekdays", "lines": [...]}`; 422 for a time that is not `HH:MM`, a line over 300 characters, more than 20 lines, and what the scheduler refuses (too many schedules switched on) |
