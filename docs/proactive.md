# Dot comes to you

Dot does not only answer when it is asked. Each morning it writes you a brief and sends you its first line (item 570).
The card that sets it is on Schedules, and what Dot tells you on its own goes through one place, which the phone's push
and the desktop's notifications share (see [Telling you](#telling-you)).

## Morning brief

The **Morning brief** card is at the head of Schedules. It has a switch, the time of day (08:00, read in `[scheduler]
timezone`, which is the zone of this Mac unless the configuration says another), every day or the weekdays, and a box,
**Also tell me about…**, for your own lines, one thing each (at most 20 lines of 300 characters). Switched on, Dot
writes the brief at that time, and your phone gets the push of its first line, which opens the brief in its chat.
**Write one now** starts one by hand, as Run now of a schedule does; the card says when the next one comes and when the
last one was written, and leads to it.

**It is a schedule.** The brief is a schedule of the scheduler you already have (see
[Schedules](memory-and-schedules.md#schedules)), marked as the brief, so that it shows in the list under the card with a
**Brief** label, and switches off there too, which the card follows. It fires once at its time; a firing missed because
the daemon was off or the Mac asleep is skipped, not replayed; its failures count as any schedule's (`[scheduler]
stop_after_failures`). Its row has no form of its own: its pencil leads to the card, since its goal is not text you
edit. A brief that was never switched on makes no schedule, and one switched off keeps its schedule (switched off) and
what you set.

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
characters, which the push shows with the title "Your morning brief" and which opens the run when you tap it; then a few
lines for each part that has something. Every brief is a new run in one chat, **Morning brief**, kept for all of them:
the run reads the conversation of the runs before it, as a schedule that goes on in its chat does, and starts untrusted
when one of them ended so. A brief that failed is told as any schedule's failure ("A run failed", at the level its row
says: always).

What goes out of the Mac is what any run's goes (see [What leaves this Mac](safety.md#what-leaves-this-mac)): the record
above, and what the mail and calendar tools return, go to the model the run runs on, and the first line of the answer
goes to your phone's push service. Nothing is sent anywhere else.

## Telling you

What Dot tells you on its own goes through one function, `tell_owner(bus, title, text, url)` in
`dotagent/notify/owner.py`: a title, a line or two of text and the route of Dot's own pages that a tap opens
(`#/runs/<id>`; an address of anything else is refused, so that nobody whose words are in a tell can send you
elsewhere). It is an event of Dot itself, `owner_told`, live only like the other notices (see [The
daemon](daemon.md#the-daemon-the-web-ui-and-the-service)): the web push sends it to every subscribed phone, the page
shows it as a desktop notification where those are on, and any way to reach you added later listens for the same event.
It is not stored: what is kept is the run it opens.

## The API

| Route | Purpose |
| --- | --- |
| `GET /api/brief` | the brief as its card shows it: whether it is on (its schedule exists and is switched on), `time` and `days` (read from the schedule's cron; `custom` when that was changed in the list to a time the card cannot show), your `lines`, the `zone`, its `schedule_id`, when it fires next and last fired, how the last firing went, the run to open (`last_run_id`) and how many mail and calendar accounts are signed in |
| `PUT /api/brief` | set it: `{"enabled": true, "time": "08:00", "days": "every" \| "weekdays", "lines": [...]}`; 422 for a time that is not `HH:MM`, a line over 300 characters, more than 20 lines, and what the scheduler refuses (too many schedules switched on) |
