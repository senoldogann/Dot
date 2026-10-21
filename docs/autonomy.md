# Autonomy: approvals, goal mode and the end of a run

Two switches decide how much a run does on its own, and a run has no step limit. The switches do not depend on each
other, so every mix works. Each has a default in the configuration, and a single run can override it.

| Switch    | Values                              | Default | Configuration                        | One run                                                                                  |
| --------- | ----------------------------------- | ------- | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| Approvals | `supervised`, `ask`, `auto`, `yolo` | `ask`   | `[policy] approvals`                 | `--approvals`, `--auto-approve`, `--yolo`; `approvals` of `POST /api/runs`; the composer |
| Goal mode | on, off                             | off     | `[agent] goal_mode`, `goal_patience` | `--goal-mode`, `--no-goal-mode`; `goal_mode` of `POST /api/runs`; the composer           |

`GET /api/status` reports the configured defaults. A run keeps the values it was started with; the run page and its
Details panel show them. The composer of the web UI sends nothing for a picker you did not touch, so the daemon applies
its current default. The YOLO choice is remembered by the browser; it is shown with a warning tint and a note, and the
run page carries a chip for it.

**Approvals** decide who answers a question of the [policy](safety.md#safety), the `ask` outcome of its decision order,
strictest first (item 398). The composer's Options menu lists them with their icons, under the names in brackets:

- `supervised` (Supervised, a lock): you do, and you are also asked before every change the preset lets through: each
  file the run writes, each command that does more than read (a command the shell guard reads as only reading runs),
  each entry or click in a page or an app, each message, schedule and tool of an MCP server, and what shapes later runs
  (a lasting teammate, a campaign's targets, ledger and next session). Reads, the run's own notes and memory notes, and
  what a rule of yours allows go on without a question.
- `ask` (Auto-accept edits, a pencil; the default): you do. Edits and the routine work the preset allows run without a
  question; the run waits for your answer to what the policy raises, on a card that shows what the action acts on whole
  (a long command scrolls within it).
- `auto` (Auto, a shield): the review model (`[models] review`) does. It is given your instructions (the goal and every
  message you sent to the run: the only authority it recognises), the action (up to 12,000 characters of what it acts
  on, and of a longer target its start and its end), why the policy stopped at it and whether the run has read untrusted
  content. It answers `approve` when the action is plainly what you asked for; `deny` when it is not, goes further than
  you asked or looks like something other than your instructions caused it; and `escalate` when it cannot tell, and then
  the question comes to you as in `ask`, with the reason of the review model in it. What the agent wrote into the action
  (its summary and target) is data to the review model, never an instruction.
- `yolo` (YOLO, an open lock): nobody does, and nothing is asked (the owner's word of 2026-10-20, item 443). The
  action is allowed and recorded: the question that untrusted content raises (see [Safety](safety.md#safety)), the one
  about Dot's own files, the guard of money and credentials and of a message or a post in your name, a pre-approved
  spend, and the refusal of a read of your secrets (the keychain, `~/.ssh`, the files of a password manager) or of data
  sent out. Each step the policy stopped at is written into the record with the reason it would have asked for, so you
  can read afterwards what went through and why. A yolo run types the password, one-time code or card number it has —
  one you gave it, or one it read from the mail that carried it — into the browser or the desktop itself, and into a
  terminal program that asks for one. Only the desktop tools stay away from password managers and Keychain Access in
  every mode. The rest is the price of the mode: a run that has read a hostile page can change or delete files, run
  commands, send a message and move money without asking you. A refusal is not a question: a `research` run's limit
  stands, and no rule of yours can refuse — its behaviours are allow, preapproved, ask and hand off.

In every mode but `yolo`, a change of Dot's own files (its code, plugins, skills, playbooks and emergency stop; see
[Safety](safety.md#safety)) is a question to you: the review model never answers it, and no rule of yours does either.
Allowed, the run makes the change with the file tools; a command line never makes it, and neither does OpenCode's own
agent, which writes past the journal `dotx undo` reads (item 398).

Every automatic answer is an ordinary approval record (`decided_by` is `reviewer` or `yolo`, the summary is the action
and the note holds the reason), an `approval_auto` event and one line in the timeline of the run ("YOLO, not asked:
click 'Reply' — untrusted content is in context and this action reaches outside the Mac"), so you can see afterwards
what went through and why. A chat leaves those lines out unless Settings › General › Chat › "Show what ran without
asking" is on (items 346 and 362); the records and the events stay, and a refusal always shows. Every step the policy
let through keeps what decided it, a question answered or not (item 468, see [Safety](safety.md#safety)), and its row in
the chat says why it ran when you open it; a step refused or handed to you has its `policy` event instead. The home's
**While you were away** counts those automatic answers for the runs of your schedules and campaigns since you last
looked (item 466; see [The daemon](daemon.md)).

**What only yolo waives.** Every question and every hand-off (item 443): the untrusted content's, Dot's own files',
the preset's and your `ask` rules', money and credentials', a message or a post in your name, a pre-approved spend, and
the refusal of a read of your secrets or of data sent out (items 429 and 433). **What no mode waives** (item 429): a
`research` run changes nothing but its notes file and memory — a refusal is not a question, so yolo does not waive it
either. (A rule of yours cannot refuse: its behaviours are allow, preapproved, ask and hand off, and a yolo run waives
the last two with every other question.) The modes that ask keep their guard: in `supervised`, `ask` and
`auto` money and credentials are still yours (Dot stops and asks you to do that step yourself; the review model is
never asked about them, and a payment under its pre-approved caps asks you, or the review model in `auto`), and a message
sent for you or a post in your name still waits for your yes (the review model answers it in `auto`; a rule that allows
it does not).

**If the review model fails** (the provider is down, or the model does not answer in the form asked for after two
attempts) there is no fallback. That one tool call fails with the error as its result, the run goes on, and the
question is not put to you in its place.

**Goal mode** is for `task` and `research` runs; a `chat` has no goal to check against, and asking for one is refused.
When the model calls `finish`, the review model checks the answer against the goal, from the evidence of what the run
did and not from what the answer claims. When the run kept a plan, the checker reads it with the evidence of each closed
step: a step marked done whose evidence nothing in the digest or the facts bears out is not done, its number goes into
the verdict's `unproven_steps` (in the `goal_check` event), and when the goal needs that step the goal is not reached.
When the plan has targets, it judges them too: they are the run's own measurable reading of the goal, not requirements
it added, and they make the goal reached only when they fit its words. The evidence is a digest of the run's tool calls
with the start of each result, and facts that Dot measured itself: the files its file tools wrote, its failed calls that
no later success of the same call followed, the newest result of each command that tests, builds or lints, and the paths
and addresses the answer names that the run never touched (paths that do not exist, addresses no call opened). Where the
facts and the answer disagree, the checker goes by the facts. Its verdict is one of:

- `achieved`: the run ends as done.
- `continue`: the goal is not reached yet, so the run is sent back to work with the instruction of the checker. A run is
  sent back at most `goal_patience` times; after that it ends as failed and keeps its answer.
- `blocked`: the goal cannot be reached from here (it needs you, a credential or something that does not exist). The run
  ends as failed with its answer and the reason.

A check can only ask for the original goal to be finished: it adds no work and does not clear the untrusted mark. If the
check itself fails, the run fails and keeps its answer. Each check is a `goal_check` event and a line in the timeline.
An answer that ends by offering to do a part of the goal that is not done yet, which the run could do with its own
tools, is `continue`, so that the part is done instead of offered; an offer of work the goal did not ask for (a fix when
only a diagnosis was asked) changes nothing, and what needs you (a decision, a credential) may stay an offer.

**Asked not to stop.** When the message you start a `task` or `research` run with asks it not to stop until the work is
done (whole words such as "don't stop", "keep going", "never stop", "until it is done", "durma", "hiç durma", "bitene
kadar", "tamamlanana kadar"; not "don't stop the server", "wait until it is done" or "bitene kadar bekle"), the run is
in goal mode whatever the switch says, and the panel's Details show "On, asked not to stop". Its goal check is told that
you asked: it accepts a finish only when every part of the goal is done and the evidence shows it, and a stop to report
progress is `continue`. A delegated run and a campaign's session keep their own setting, and a resumed run, one that an
answer starts and a follow-up on the run's page keep the run's. Whatever the model, every task and research run outside
a campaign is told to keep acting while the goal leaves work it can do, not to stop to report progress or to offer what
the goal asks for, and to take an ask to keep going at its word. This is not **Keep going** in the composer's **Options**,
which makes the goal a campaign worked on in sessions (see [Campaigns](agents-and-campaigns.md#campaigns)). Every run, a chat's too, is told
that when it hesitates to do what it was asked, or a part of it, it says so at once, with what holds it back and what
it can do instead, and asks you or goes on with the part it can do, instead of looking into the matter on its own
first (item 327). Every run, a chat's too, in both prompt styles, is also told that there is always a way (item 446): it
does not give up on a step while a way to do it is left untried, but reads the error and takes another tool, channel
or method instead of repeating the call that failed; and it says that something cannot be done, or leaves it for
later, only after trying it, naming each way it tried and what it returned. A chat, whose reply ends its turn, is told
not to end it announcing a next step it could take now, as a task or research run is told to keep acting. No way goes
around a refusal, a denial or a decision that is yours.

A delegated run takes the approval mode of the run that started it and has no goal mode of its own. A scheduled run uses
the model, reasoning level, approval mode and goal mode of its schedule, and the configured defaults for those the
schedule leaves open, so what a schedule does while nobody is there depends on them: with `ask`, a question of the
policy waits `approval_timeout_s` and then counts as a refusal, or, for a step handed to you with `[policy] unanswered =
"park"`, is kept for you (Steps kept for you, below). A no you gave a run holds for the rest of it: the same action is
refused at once until you write to it again (item 456, [Safety](safety.md#safety)); a `yolo` run asks nothing, so none
is kept there.

**Questions nobody answers during the run.** In a `yolo`, scheduled or eval run nobody answers the agent's own questions
(`ask_user`) while it works, and `[agent] questions` decides what they do. With `leave` (the default) the question is
stored as `left`, the timeline shows it where it was asked, and the run goes on with what does not depend on the answer,
or ends and says what it needs; its status never turns `blocked`. The run's page lists its open questions under "Dot
asked you", during the run and after it, and `dotx run` prints each one when it is left and lists the open ones after
the answer. Your answer (`POST /api/approvals/{id}/decision`) goes to the conversation as your message, "Answer to your
question “…”: …": into the run working there, or else as a new run on it, whose goal is the goal of the run that asked
with your answer under it (so its goal check and its title keep the task), with the mode, model, reasoning level,
approval mode and goal mode of the run that asked, and as the agent it ran as, within that agent's bounds (item 482), as
a resumed run is; the page then opens that run once no other question of the run is open there. A run reads an answer
that comes during its last step before it ends; one that reaches a run that is then stopped or fails stays in the
conversation as your message, for the next run there. An empty answer is refused. Skip only closes the question, and a
left question never expires. With `wait` such a question waits as in a run you watch, for `approval_timeout_s` at most.
A run you started in any mode but `yolo` always waits for your answer.

**Steps kept for you.** A step the policy hands to you (money, a credential, a rule of yours that hands it off) waits
`approval_timeout_s` for **I did it**, as every question of the policy does. With `[policy] unanswered = "park"` (item
482; the default, `"refuse"`, counts it as a refusal) one nobody answers in that time is kept for you instead: the run
is told that the step did not happen and waits for you, and goes on with what does not depend on it, or ends and says
what waits for you; its later hand-offs are kept at once, with no new wait, until you write to it or do a step it kept.
A kept step is a `left` record with the fingerprint of its action (tool, kind, summary, target, amount, flags, the files
it changes and the application it opens; none when its card shows less than all of it, a write's text or a cut summary)
and the time it lapses, `[policy] parked_hours` (72) on: the run's page lists it as "Kept for you" with the questions it
left, the timeline says so where it was asked, the Activity list shows it among what waits, `dotx run` prints it, and
your phone is told when it is kept. The same step, by its fingerprint, is kept once in a chat (the run is told it waits
already), and at most 20 wait in a chat or the chats of one schedule and 50 in all; past them a hand-off is asked as
before and refused if nobody answers it. **I did it** tells the conversation that you did the step yourself, "The user
did the step that waited for them (…) themselves; go on from there.", with your note: into the run working there, or
else as a new run on it, which reads those words under the goal of the run that kept it, with that run's mode, model,
reasoning level, approval mode and goal mode, as the agent it ran as, and which starts tainted when that run, or the
chat's last run, ended tainted. These words are Dot's, not yours: they restore no trust, and only the note you wrote
counts among your instructions (the new run's goal is the goal it goes on from, with your note under it). Skip this step
only closes it, and one past its time closes as expired (the daemon looks every half minute) and can no longer be
answered. A kept step gives the run no authority: a hand-off is yours to do. A question that asks your yes is kept as
well (item 483) when its card shows all of the step and the step reaches nobody: a command (with the folder it runs in,
when the call names one), a file read or a folder listing, a page read or a web search, sending, posting or uploading
nothing (not a page's or a screen's step, a terminal session, a message, an MCP call, delegated work, a search's
pattern, an extract, a schedule or a note, whose cards show less than they do); never a question that what the run read
raised (the policy's stop for untrusted content), nor one about Dot's own files. Its card says "Kept for you" with
**Allow once** and **Skip**. Allow once stores your yes, which holds for `[policy] permit_hours` (24), and tells the
conversation, as I did it does, "The user allowed the step that waited for their yes (…): do it now, exactly as it was,
once"; when the run does that exact step in that conversation (the same fingerprint), the policy decides it again first,
and only where it would ask you does your yes let it through, once, recorded as "You allowed it earlier": a rule that
now refuses or hands it to you, or the run's untrusted reading, still stops it. A yes that cannot reach the conversation
(a `dotx` run works there) is taken back and the card stays. Rewinding the chat to before the run that kept it takes an
unused yes back too. Skip on a kept step, either kind, holds in that conversation: the same step is refused at once,
with no card, until you write there again (a message of yours, or a run you start). Any other question still counts as a
refusal when nobody answers, and nothing is kept in `yolo`, which asks nothing, or in an eval. A delegated run keeps its
steps where the run that delegated it does (item 485): the card is on the delegated run's page and in the Activity list,
the step is the chat's of the run at the top, and your answer reaches that run, while it works, or else a run that goes
on from its goal in its chat (tainted as either ended), which may do an allowed step itself or delegate it again. A
session of a campaign keeps its steps for the campaign, shared by its sessions (item 484,
[Campaigns](agents-and-campaigns.md#campaigns)).

**The end of a run.** There is no step limit. A run ends by `finish`; by the progress watchdog; by a model call that
still fails after its retries and the wait for a model out of reach (see [Models and providers](models.md#models-and-providers));
in goal mode by the goal check giving up; at a limit of minutes or tokens you set; or when you stop it. With
`verify_answer_paths = true` in `[agent]`, a successful end whose answer names paths that do not exist is sent back once
before it is accepted, and before goal mode checks it (see [How a run works](runs.md#how-a-run-works)). A goal that should go
on across runs and days until its outcome is verified is a campaign (see [Campaigns](agents-and-campaigns.md#campaigns)).

**Limits of a run.** `run_max_minutes` and `run_max_tokens` in `[agent]` bound a task or research run; 0, the default,
is no limit, and a chat turn has none. The minutes are the run's wall time, waits for your approvals and answers
included; the tokens are those of every model call of the run (side calls, such as your side questions, included) and
of the runs it delegated to. Dot compares them between steps, so a step that is running when a limit passes goes on: a
model call can take `model_call_timeout_s`, and a delegation waits for its run. At the first step past a limit the run
is told what it used and to finish now with what it reached, what is missing and how to go on; that step may only call
`finish`, `update_plan` and `mark_plan_step` (the plan tools before `finish`, as nothing after `finish` runs), and the
run ends with its reply. A `finish` ends it as it says, and in goal mode the goal check judges that finish once, with
no way back: a goal it does not find reached ends the run as failed. Any other reply ends the run as failed, with a
result that starts "Stopped: the run reached its limit", keeps what the reply said and tells what the run did last,
what it touched and where its plan stood (`dotx stats` counts it as stopped at its limit). Past the limit neither the
open steps of the plan nor `verify_answer_paths` send the run back, and a message you send during that step comes too
late for it, as for any run that has ended: an answer to a question it left stays in the thread for the next run. The
timeline shows when the limit was reached. The limits are those of one run: a resumed run, or one that an answer
starts, begins with a new clock and a new count, and a delegated run has the limits of its own while its tokens also
count toward its parent's, which sees them when the delegation returns. Delegated and scheduled runs have the same
limits; an eval run is cut by its task's own (300 s, 600000 tokens), whatever yours are.

**The progress watchdog.** After every step in which a run used tools, Dot compares what it called and what came back
(screenshots included) with the steps before. By default it looks for the same step `stall_limit` times in a row: the
model is told twice to change its approach, and the third time the run stops with a result that says what it did last,
what it touched and where its plan stood, and how to go on in the same thread. With `stall_escalation = true` in
`[agent]` it also notices a cycle of two or three steps, `failure_streak` steps in a row in which every call failed, and
`revisit_window` steps that only repeated calls the run had already made, with the same results. It then answers with a
ladder instead: a nudge; a re-plan, in which the next step may only plan or finish; a second opinion of the review model
(`[models] review`), given to the model as advice (if that model cannot answer, the run stops and says so); a question
to you (not in yolo, scheduled, delegated or eval runs, where nobody would answer); and the stop. Twelve steps without
an alert bring the ladder one rung down again, so a long run is not stopped for hiccups that are hours apart. Every
alert shows in the timeline with what was seen and what was done. The ladder is off by default until evals show that it
helps; without it, a run that keeps changing its steps without getting anywhere goes on using the model until you stop
it. Give such a run a goal that can be checked, and keep an eye on the token count of a long one (`--verbose`, or the
Details panel).

**Resuming a run that was cut off.** A run that a crash, a restart or a shutdown of the daemon cut off, or a run of
`dotx run` whose terminal was closed (or that got SIGTERM) or whose command died, can go on where it stopped: Continue
on the run's page, `dotx resume RUN_ID` (in this terminal, like `dotx run`), or `POST /api/runs/{id}/resume`. A new run
starts in the same chat, with the goal, model, reasoning level, approval mode and goal mode of the run it resumes, as
the same agent when it was one of yours (item 462), and the conversation so far, where a shutdown or a restart of the
daemon, or a closed terminal, keeps the results of the calls of the step it was in that had ended (a crash keeps none).
Its first message tells the model how that run was cut off and how each call of that step without a result ended:
finished (with the start of its result), cut off while it ran, or not started. A call that was cut off may have changed
something unless it only reads, and the model is told to check before it runs it again. The plan of that run is the plan
of the new one. Only the newest run of a chat can be resumed, and never a run that you stopped or that another run
started. After a shutdown, the chat's queue goes on once the new run ends done; after a crash or a restart, it waits
until you resume it.

By default (`resume = "safe"` in `[agent]`, item 248) the daemon also resumes runs on its own when it starts: runs of
the web UI, cut off at most `resume_within_s` seconds before (30 minutes), whose calls that were cut off only read (a
question to you counts: it is asked again). A run of `dotx run`, of a schedule or of an eval, a run that itself resumed
another (so a run that brings the daemon down is not resumed again and again), and a run cut off in a call that may have
changed something (a command, a write, a click, or a delegation, whose run may have done anything) wait for you. `resume
= "off"` leaves every such run to you; **Go on after a restart** in Settings › System › Daemon writes the choice into
the config file (item 231), and the daemon's next start reads it. The new run's turn opens with a line with a bolt
(item 299): "Dot restarted and resumed this turn" when the daemon went on with it by itself, else
"Resumed this turn after" what cut it off; opened, it says how that run was cut off, how each call of the step it was in
ended, and links to it. A run that waits for you wears the same bolt over its composer, with what cut it off ("Dot
restarted while this run worked.") and Continue.

**Lessons.** With `lessons = "auto"` (the default) or `"propose"` in `[agent]`, a task or research run of its own that
reached its goal after something went wrong on the way (a call failed, whether the run found its way around it or not;
the progress watchdog raised an alert; the goal check sent it back; its answer named paths that did not exist) is
followed, once it has ended, by one call of the review model (`[models] review`). It reads a digest of the run, what
went wrong, the
answer and the saved notes that fit the goal, and writes at most three lessons of one sentence: know-how that a later
run could use from the start, such as that `pdftotext` keeps the columns of a table only with `-layout`. A lesson of
more than one line, of fewer than 20 or more than 300 characters (or `[memory] max_chars`), with a secret, an e-mail
address or a path in a home folder, or that says what the goal, another lesson or a saved note already says, is dropped;
one that says not to do what a saved note says to do (or the other way round) is no repeat of it, however alike their
words and meaning are (`not`, `never`, `n't`, the Turkish `değil`, `yok`, `asla`; the suffix `-ma`/`-me` is not
recognised). With `auto` nobody is asked: each lesson gets a second look first, one more call of the review model that
is shown the goal and the lessons and nothing the run read, so that a page the run read cannot speak to it. It keeps
only know-how about how a tool, a command, a site, an application or a file format behaves, and leaves out a lesson
that tells the agent to send, post, buy, sign in, contact someone, change a setting or go to an address, to trust some
content or to skip a check, or that holds private data; when in doubt it leaves it out. After a run that read content
others can write (web pages, mail, downloads), a lesson with a link or an account name in it, which such content may
have planted, is left out before it is asked. The kept lessons are saved as notes tagged `lesson` whose source is the
run, and "Lessons from this run" on the run's page says "Saved on its own" with Undo, which forgets the note (a note
that was there before, or that was rewritten since, stays); a lesson left out says why, with Save anyway. With `propose`
the lessons wait on that card: Save to memory keeps one, Dismiss drops it (`POST /api/runs/{id}/lessons/{index}/save`,
`/dismiss` and `/undo`). A lesson is what its last decision made it, and
every decision waits for the one before it: a second save or dismissal of a lesson decided already is refused, save the
Save anyway of one `auto` left out. `dotx run` and `dotx resume` show the lessons after the answer; Ctrl-C skips the
wait, and the exit status stays the run's. A saved lesson is a note of the agent like the others: a later run gets it
when it fits its goal, and the Memory page edits or forgets it; a lesson of an agent's run is that agent's own (see
Agents), and the notes it is checked against are the ones that agent's runs are handed. The same text saved again is
the same note, also one that rested, which is awake again. Delegated runs, chats, runs that failed or were stopped,
and evals (which always run without lessons) are not asked at their end; the nightly reflection asks about the failed
and the stopped ones, once each (a run whose lessons were drawn and none kept says so). `"off"` learns nothing. Every
note a run is handed (at its start, by `memory_search`, or changed while it ran) is recorded with how the run ended, so
the Memory page says "Handed to 5 runs, 4 done" under a note and `dotx stats` scores the notes.

**Reflection.** Once a day at `[agent] reflection_time` (`"03:30"` in `[scheduler] timezone`; `"off"` never), the
daemon reflects on the runs that ended since the last reflection (the last day, the first time): it asks the review
model for the lessons of the task and research runs the loop did not ask at their end, the failed and the stopped ones
included, with how the run ended as one more signal and in words that say it did not reach its goal, under the
`lessons` setting above (`propose` leaves them on the run's page, `auto` saves the ones its second look keeps, `off`
draws none), and it reads whether such a run read content others can write from all its results; it folds the lesson
notes of one agent that say the same, the same way round, into the newer one, or into the older one when you saved
that one yourself (by the embeddings when both have one, by the words otherwise, compared off the daemon's event loop;
a resting lesson is left alone, so one learnt again does not take its rest, and two you saved stay two; the one
folded away rests and names the one it was folded into, so nothing the reflection does deletes a note); it rests a
lesson that no run was handed, and that was not written again, for `lesson_rest_days` (60), or whose score says it did
not help (handed to `[memory] score_min_uses` runs in the last `score_days`, more of which failed than ended done); a
lesson you saved yourself is never folded away nor retired. It also puts aside a note of Dot's that a newer note,
written or changed since the last reflection, made outdated (item 306): the review model reads each such pair of notes
close in meaning (at most 12 a night; your notes, the lessons you saved and the `core` notes are never the older one)
and keeps the older one when in doubt; the one it calls outdated rests, names the note that replaced it (the Memory page
says "Replaced by another note") and the log line `note_outdated` says which. It rests a lesson by tagging
it `rest`, after which searches skip it while `dotx memory list` and the Memory page still show it, and the log line
`lesson_retired` says which reason with the numbers; and when anything happened it writes a journal note, tagged
`journal`, of the day it read: the day of the middle of that time, so the reflection at 03:30 names the day before ("On
2026-10-11 I worked on 4 runs (2 done, 1 failed, 1 cancelled). Learned: … Tidied the lessons: 1 merged, 1 retired."), or
its first and last day when it read more than a day and a quarter, after a daemon that was off ("From 2026-10-08 to
2026-10-12 I worked on …"). The reflection touches only the notes Dot wrote, never yours. It also keeps what Dot knows
about you (see Memory): after a day with runs, one call of the review model rewrites Dot's lines of that section from
the section as it stands, your own lines, your notes and the day's goals, at most `[memory] owner_chars` (1500)
characters. A night the Mac slept through is caught up once when it wakes. A daemon that was off at that time, or whose
stop cut the reflection (a stop does not wait for one, and it records nothing until its end), reflects at the next
`reflection_time` on everything since the last reflection. The home's Today strip lists the reflection among what ended
today;
`dotx reflect` runs one now. A reflection that fails is kept: the strip shows "The nightly review failed" with why until
one after it is done, and `dotx stats` says when the reflection was last done and the failure since. Learning does not
stop unseen at a run's end either: lessons that could not be drawn leave `lessons_failed` with why on the run's page,
skill work that failed `playbook_failed`, and `dotx stats` counts the runs of each. The card of a run's lessons arrives
folded (item 374); its head says "could not be drawn" in red, or how many lessons wait for your decision in amber,
else how many there are, and opened it shows why, or the lessons.

**Taking back the file changes of a run.** Before `write_file` or `edit_file` changes a file, the run notes the change
in its journal (`undo/` in the home directory), and its first change to a file keeps a copy of the file as it was. When
the change cannot be noted (a full disk, say), the file is not changed and the model is told why; a note the disk cut
short is left out of the journal, so the run's next change is noted after it and `dotx undo` still reads it. The side
panel's **Changes** tab reads the same journals (item 368): each file a chat's runs changed with `write_file` and
`edit_file` (a run's own notes are left out), diffed from the copy the
chat's first change to it kept to what is there now. A file where the panel beside a chat shows no file (a hidden
folder, an application's files, outside the home folder), a binary file, one past 512 KB, one that is gone and one
whose first change kept no copy show why instead of a diff. Each side of a diff is masked whole, before it is diffed,
of the secrets Dot holds, so a private key over many lines is masked whatever part of it changed. A command's own writes
are not in the journals, so they are not there. `dotx undo RUN_ID` shows what taking back the changes of that run and of
the runs it delegated to would do: a file the run changed is put back as it was (`restore`), with its permissions, a
file it created is deleted (`delete`), and the folders it created are removed once they are empty. Nothing is changed
until `dotx undo RUN_ID --apply`, which exits with 1 when it left a file as it is. A file that changed or was deleted
after the run's last change to it is a conflict and is left as it is; `--apply --force` takes it back too, and what
changed in it since is lost. A file below a folder that is a link now cannot be taken back, even with `--force`: the
link came after the run, and the change would be taken back where it points. A file larger than `undo_max_file_bytes`
(10 MiB), or whose copy would bring the copies of the run past `undo_max_run_bytes` (100 MiB), is
changed without a copy and cannot be put back; a file the run created can always be deleted again. A copy that is not
the one the journal noted is not written back, and a folder that is now where a file was is never replaced. Only the
file tools are in the journal (`write_file`, `edit_file` and `write_notes`): what a shell command (`rm`, `mv`,
`sed -i`), the browser or the desktop changed is not. A
run that is still working is refused; a run of `dotx run` that a crash left working ends when the daemon starts again.
`dotx prune --apply` deletes the journals whose last change is older than `undo_keep_days` (30 days).
`GET /api/runs/{id}/actions` lists the same plan, one file at a time, and `POST /api/runs/{id}/undo` takes back a file
that is as the run left it.
