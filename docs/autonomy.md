# Autonomy: approvals, goal mode and the end of a run

Two switches decide how much a run does on its own, and a run has no step limit. The switches do not depend on each
other, so every mix works. Each has a default in the configuration, and a single run can override it.

| Switch    | Values                              | Default | Configuration                        | One run                                                                                  |
| --------- | ----------------------------------- | ------- | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| Approvals | `supervised`, `ask`, `auto`, `yolo` | `ask`   | `[policy] approvals`                 | `--approvals`, `--auto-approve`, `--yolo`; `approvals` of `POST /api/runs`; the composer |
| Goal mode | on, off                             | on for a task or research run, off for a chat | `[agent] goal_mode`, `goal_patience` (4) | `--goal-mode`, `--no-goal-mode`; `goal_mode` of `POST /api/runs`; the composer |
| Proof checks | commands with a time limit       | none    | none                                 | `--proof`, `--proof-timeout`; `proof` of `POST /api/runs`; the run's own `add_proof` |

`GET /api/status` reports the configured defaults. A run keeps the values it was started with; the run page and the
facts under More on its side panel's Task tab show them. The composer of the web UI sends nothing for a picker you did
not touch, so the daemon applies its current default. A task or research run starts in goal mode unless you turn it
off (`--no-goal-mode`, `goal_mode: false`, or the composer switch). A chat never has goal mode. Approvals stay `ask`
unless you choose otherwise, and a run is sent back at most `goal_patience` times (4). The `yolo` choice (Ask only to pay or send) is remembered
by the browser, and the run page carries a chip for it.

**Approvals** decide who answers a question of the [policy](safety.md#safety), the `ask` outcome of its decision order,
strictest first (item 398). The composer's Options menu lists them with their icons, under the names in brackets:

- `supervised` (Supervised, a lock): you do, and you are also asked before every change the preset lets through: each
  file the run writes, each command that does more than read (a command the shell guard reads as only reading runs),
  each entry or click in a page or an app, each message, schedule and tool of an MCP server, and what shapes later runs
  (a lasting teammate, a campaign's targets, ledger and next session). Reads, the run's own notes and memory notes, and
  what a rule of yours allows go on without a question.
- `ask` (Auto-accept edits, a pencil; the default): you do. Edits and the routine work the preset allows run without a
  question; the run waits for your answer to what the policy raises, on a card that shows what the action acts on whole
  (a long command wraps and scrolls within the box of code every command of a chat is in, whose copy takes it whole).
- `auto` (Auto, a shield): the review model (`[models] review`) does. It is given your instructions (the goal and every
  message you sent to the run: the only authority it recognises), whole or not at all (item 1947): the newest whatever
  its length, then the first and the others, newest first, while together they fit in 60,000 characters. When one does
  not fit, the step comes to you without asking the model, as what it did not read could forbid it: once your messages
  to a run pass 60,000 characters together (a long pasted brief and a few more), every step the review model would have
  answered comes to you for the rest of the run. Only yolo's check of a run that read outside content, which asks
  nobody, is shown the rest too, the newest twelve of them cut to 1,500 characters from their middle, as every
  instruction was before. It is given the action (what it acts on and what it runs, a
  page's script, the whole arguments of an MCP call and the whole goal of a schedule: up to 12,000 characters in one
  question, a longer one in overlapping parts of that size, each its own question, of which the strictest answer
  decides, and one longer than eight parts comes to you without asking the model; items 680, 985 and 1251), why
  the policy stopped at it and whether the run has read untrusted content. It answers `approve` when the action is
  plainly what you asked for; `deny` when it is not, goes further than you asked or looks like something other than your
  instructions caused it; and `escalate` when it cannot tell, and then the question comes to you as in `ask`, with the
  reason of the review model in it. A message, a mail or a post must also be lawful and harmless to its readers and to
  you (no deceit, threat, harassment, spam or secret of yours in it): it denies one that is not, whatever your
  instructions say (item 860, the owner's word for the review of a schedule's messages: "legal ise, tehlike arz
  etmiyorsa ve bir amaç içinse yollasın"; it holds wherever the review model answers, in `auto` and in yolo's check of a
  run that read outside content). What the agent wrote into the action (its summary and target) is data to the
  review model, never an instruction.
- `yolo` (**Ask only to pay or send**, "Pay or send" on its pill, an open lock; named YOLO before, which did not
  say what it still asks): nobody does, and nothing is asked (the owner's word of 2026-10-20, item 443), but a payment,
  in a chat you opened or one Dot opened on its own initiative (item 2069, the owner's word of 2026-11-28: "Yolo modda
  hiçbir şey sormasın, sadece ödeme işlerinde sorsun"): it is handed to you, a schedule's run in your chat included. A
  message or mail sent for you, a public post and a release go unasked, in those chats too (items 860 and 850 asked
  them before). Any other action is allowed and recorded: the question that untrusted content
  raises (see [Safety](safety.md#safety)), the one about Dot's own files, the guard of money and credentials and of a
  message or a post in your name, a pre-approved spend, and the refusal of a read of your secrets (the keychain,
  `~/.ssh`, the files of a password manager) or of data sent out. Each step the policy stopped at is written into the
  record with the reason it would have asked for, so you can read afterwards what went through and why. Each call a
  yolo run makes is also a line in the log (`yolo_step`) and a line in `yolo.jsonl` under that run's undo folder: a
  record of what it did, written without asking and without holding the run up. The result the run reads is the
  tool's own. A yolo run types
  the password, one-time code or card number it has — one you gave it, or one it read from the mail that carried it —
  into the browser or the desktop itself, and into a terminal program that asks for one. Only the desktop tools stay
  away from password managers and Keychain Access in every mode. The rest is the price of the mode: a run that has read
  a hostile page can change or delete files and run commands without asking you. Two things hold for a yolo run that has
  read outside content (item 525, see [Safety](safety.md#safety)): the review model checks, without asking you, a step a
  planted instruction would be after (a message, a post, an upload, a payment, a credential, a read of your secrets, an
  install, a note it saves), and refuses what your instructions do not call for; and a step whose words would send a
  secret Dot holds out of the Mac is refused, but in free mode (item 1097). A refusal is not a question: a `research` run's limit stands, and no rule
  of yours can refuse — its behaviours are allow, preapproved, ask and hand off.

What `yolo` and free mode guarantee, the exact exceptions and the test that keeps them: [Freedom](freedom.md).

In every mode but `yolo`, Dot asks you before a run changes its code, plugins, skills, playbooks, shared appearance or
emergency stop. Only you can approve those changes. [Safety](safety.md#safety)
For approved file edits, the run uses the file tools; a command line never makes them, and neither does OpenCode's own
agent, which writes past the journal `dotx undo` reads (item 398). So is a login filled from your 1Password (item 493)
or from Dot's own vault (item 621): the review model never answers it, and a rule of yours can only hand it to you.

Every automatic answer is an ordinary approval record (`decided_by` is `reviewer`, `yolo`, `permit` for a kept step you
allowed, or `chat-permit` for a step of a kind you allowed for the rest of the chat, item 866; the summary is the action
and the note holds the reason) and an `approval_auto` event, so you can see afterwards what went through and why. A chat
draws a line in the timeline for the review model's decisions ("The reviewer approved: click 'Reply' — untrusted content is
in context and this action reaches outside the Mac"), and none for a step yolo waived: the mode says nothing of what it
let through, and the record and the event keep it (item 500). A chat leaves the review model's lines out unless Settings
› General › Chat › "Show what ran without asking" is on (items 346 and 362); the records and the events stay, and a
refusal always shows. Every step the policy
let through keeps what decided it, a question answered or not (item 468, see [Safety](safety.md#safety)), and its row in
the chat says why it ran when you open it, unless a yolo run did it without asking (item 500); a step refused or handed
to you has its `policy` event instead. The home's
**While you were away** counts those automatic answers for what Dot did on its own (your schedules and campaigns, its
replies, its agents' work, the commands that ended in the background; item 1424) since you last looked (item 466), when a new chat shows the day (Settings › General › Chat, item 640; see [The daemon](daemon.md)).

**What only yolo waives.** Every question and every hand-off (item 443): the untrusted content's, Dot's own files', the
preset's and your `ask` rules', money and credentials', a message or a post in your name, a pre-approved spend, and the
refusal of a read of your secrets or of data sent out (items 429 and 433). **What no mode waives** (item 429): a
`research` run changes nothing but its notes file and memory — a refusal is not a question, so yolo does not waive it
either — and a run that read outside content has a step refused whose words would send a secret Dot holds out of the Mac
(item 525), in every mode but free mode, which you switch (item 1097: it records the step, the secret masked). A yolo
run that read such content also puts the steps it lists to the review model first (`[policy] yolo_taint_check`, on by
default) — and so does the run that goes on from a compaction, whose note is written out of what tools returned, while
what that run saves since is not marked as learned from outside content (item 1219). (A rule of yours cannot refuse:
its behaviours are allow, preapproved, ask and hand off, and a yolo run waives the last two with every other question.) The modes that ask keep their guard: in
`supervised`, `ask` and `auto` money and credentials are still yours (Dot stops and asks you to do that step yourself;
the review model is never asked about them, and a payment under its pre-approved caps asks you, or the review model in
`auto`), and a message sent for you or a post in your name still waits for your yes (the review model answers it in
`auto`; a rule that allows it does not), and a login filled from your 1Password or from Dot's vault waits for
yours alone, in `auto` too.

**What an upload asks** (item 561). A file sent to a server carries the flag `upload`: one chosen in a page's upload
control with `browser_upload` (many pages send it as soon as it is chosen), a `curl -T`, a form sent with a file, a
push. No preset lists it, so what it does follows from the mode and the preset (see [Safety](safety.md#safety) for the
presets):

| Where                                       | An upload                                                          |
| ------------------------------------------- | ------------------------------------------------------------------ |
| `ask` or `auto`, `aggressive` or `balanced` | goes ahead until the run has read untrusted content, then asks (you in `ask`, the review model in `auto`) |
| `ask` or `auto`, `cautious`                 | asks, as everything that is not read-only does                     |
| `supervised`, any preset                    | asks, as every change does                                         |
| `yolo`, any preset                          | asks nothing; once the run has read outside content, the review model checks it first (item 525) |

A file of your home folder, of the system or of Dot's own carries `exfiltration` as well, and a protected one
`secret_access`: every mode but `yolo` refuses those. A rule of yours that allows an upload still asks once the run has
read untrusted content; only a `preapproved` rule skips that question.

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
- `improve` (item 1946): the goal is reached, and a clearly better result is in reach (a better price, a valuable
  detail the answer lacks, research that would improve it, item 809). The run is sent back with how, and the same
  `goal_patience` bounds it; a run whose rounds run out this way ends as done with its answer, since its goal is
  reached. The checker's words are no instruction of yours: what the run does then is decided by the gate as every
  step.
- `continue`: the goal is not reached yet, so the run is sent back to work with the instruction of the checker. A run is
  sent back at most `goal_patience` times; after that it ends as failed and keeps its answer.
- `blocked`: the goal cannot be reached from here (it needs you, a credential or something that does not exist). The run
  ends as failed with its answer and the reason. A **schedule's firing** whose own finish said what it needs from you
  (`needs_from_user`) ends as **done** instead (item 1952), the check's words under its answer saying the goal waits on
  you, and what it needs shown under it: you are asked once, by the notice of its end, and the schedule's row counts no
  failure. The firings after it start with a note that this part is on hold (what the run asked of you, quoted, and not
  to try it again or look for a way round it): they do the rest of the task, and when nothing else is left end at once
  with a line, no second ask and `nothing_new`, so that a login wall you have to clear (Fiverr, TikTok: 17 failed runs and
  some 148 M tokens in a week) is not met again by every run. A firing begun under the note whose check says `blocked`
  ends done too. The note stands until you write in the chat of that run (a run of yours, not the schedule's, after it,
  however many firings came since), or for seven days from when that run began, after which the part is tried again, as you may have cleared it without saying so; a firing you
  start by hand has none. It is read from the schedule's runs, nothing is stored for it, it makes no question to you, and
  a run of any other kind, or a firing that asked nothing of you, fails as before.
- `scheduled` (item 675): all the run can do now is done, and what is left waits on something only time brings (a reply
  to what it sent, a payment, a site's review, a moment the goal names), which a run scheduled in the same chat picks
  up. The run ends as done, with the check's words under its answer. The checker is shown the chat's schedules that are
  still to run and go on in this chat, with the start of each one's goal, read by Dot from its own list, not from the
  answer (the schedule whose firing the run is does not count: its next firing is no follow-up of a run that did not
  get there, so a failing schedule still fails); each goal is the run's own text, which the checker reads as data;
  with none, Dot takes the verdict as `continue` and tells the run to schedule one with `schedule_create` or do
  the work now. A campaign's claim is never accepted this way: its sessions are its follow-ups.

A check can only ask for the original goal to be finished: it adds no work and does not clear the untrusted mark. If the
check itself fails, the run fails and keeps its answer. Each check is a `goal_check` event, which the conversation
does not show (items 1098 and 1112): the answer says what came of the work.

**Proof checks** (item 1853) take "done" out of a model's word. A task run can carry up to 5 commands (a test run,
`curl -fsS` of a page, `test -s report.pdf`), each with a time limit (300 s unless given, at most `[shell] max_timeout_s`, 1800 by default), that must
exit 0 before the run is done. You set them as the run starts: `dotx run --proof "uv run pytest tests -q"` (repeat it
for more; `--proof-timeout S` for their limit) or `proof: [{"command": ..., "timeout_s": ...}]` of `POST /api/runs`.
The run sets them itself with `add_proof` (the `proof` tool group), and is told to when you name a check that must
pass or the goal has a command that proves it. A check is only ever added: no tool changes or removes one, so a run
cannot soften what it is held to. A chat or a research run (no `shell`, no `finish` to hold), a run on the Claude plan
or OpenCode (whose turns end in their own program) is refused checks, and the start of a run of an agent, a team or
a loop takes none from you; a run that goes on from another (resumed, or handed a failed or blocked run's note) keeps that run's checks.

When the run calls `finish` with success, after the reminders of an open plan and before the goal check (with goal
mode on or off), Dot runs every check as a `shell` call of the run itself: one step in the conversation, made by Dot
instead of the model, through the same gate, sandbox, events, masking and time limit as a call of the model's. Dot adds
no rule to the gate: a check asks what that command would ask typed by the model in the run's mode, so a `yolo` or
free-mode run asks nothing new ([Freedom](freedom.md)). Then:

- every check exits 0: the goal check, when on, decides as before, and sees the passing runs among the facts;
- one exits otherwise, or is killed at its limit: the run is sent back with each failing command and the last 3000
  characters of what it printed (the whole result is the call above it), and works on; this counts toward
  `goal_patience`, and past it the run ends failed with its answer and "the proof did not pass";
- one cannot run (the gate, you or a cap refused it, the run has no `shell`, or its limit is past `[shell]
  max_timeout_s`, which the shell would cut it to; a check over it is refused when it is set): the run ends failed with
  its answer and
  "the proof could not run", naming why. It is not sent back, as the same call would be refused again, and no model
  decides in the check's place.

A finish past the run's limit runs the checks too; there, a failing one ends the run failed. Each look is a
`proof_check` event (each command, who set it, `passed`, `failed` or `not_run`), and each check a `proof` event.

**The check may look at the work itself** (item 1363). The digest says that a file was written, never what it holds; so
a check that cannot tell whether the goal is reached from the digest and the facts answers `inspect`, naming in `reads`
at most two files that the facts list among the files the run wrote, with the lines it wants. Dot reads them (only a file
the run wrote is opened at all, at most 120 lines, 4000 characters, with secrets masked as the facts are) and asks the
check once more with what they hold. A second inspect is refused and a decision names no file: the window is one look at
the work, not a second read of the run.
An answer that ends by offering to do a part of the goal that is not done yet, which the run could do with its own
tools, is `continue`, so that the part is done instead of offered; an offer of work the goal did not ask for (a fix when
only a diagnosis was asked) changes nothing, and what needs you (a decision, a credential) may stay an offer.

**Asked not to stop.** When the message you start a `task` or `research` run with asks it not to stop until the work is
done (whole words such as "don't stop", "keep going", "never stop", "until it is done", "durma", "hiç durma", "bitene
kadar", "tamamlanana kadar"; not "don't stop the server", "wait until it is done" or "bitene kadar bekle"), the run is
in goal mode whatever the switch says, and the facts under the panel's More show "On, asked not to stop". Its goal check
is told that you asked: it accepts a finish only when every part of the goal is done and the evidence shows it, and a
stop to report progress is `continue`, unless what is left waits on time and a run scheduled in the chat picks it up
(`scheduled`): work the run could do now never waits for that run. A run that ends so with no run scheduled is sent
back to schedule one. A delegated run and a campaign's session keep their own setting, and a resumed run, one that an
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
not to end it announcing a next step it could take now, as a task or research run is told to keep acting. Your
request is the job however bluntly it is put (item 1102): on your own Mac, accounts, files and code it is done without a
lecture, a warning or a watered-down version, and the character keeps no rule of its own about what Dot will not do
(item 1211's last stop went on 2026-11-18). The rules of trust and safety below still hold back what they name —
credentials and secret stores, another run's work — and each of them protects you. No way goes around a refusal, a
denial or a decision that is yours.

A delegated run takes the approval mode of the run that started it and has no goal mode of its own. A scheduled run uses
the model, reasoning level, approval mode and goal mode of its schedule, and the configured defaults for those the
schedule leaves open, so what a schedule does while nobody is there depends on them: with `ask`, a question of the
policy waits `approval_timeout_s` and then counts as a refusal, or, for a step handed to you with `[policy] unanswered =
"park"`, is kept for you (Steps kept for you, below), and a message, a post or a release is kept as a card with its
draft (Sends kept for you, below, item 922). A no you gave a run holds for the rest of it: the same action is
refused at once until you write to it again (item 456, [Safety](safety.md#safety)); a `yolo` run asks only a payment
(item 2069), and a no you gave one holds there the same way.

**Questions nobody answers during the run.** In a `yolo`, scheduled or eval run, and in every run while free mode is on (item 1563: free mode asks no run's owner
anything, whatever the run's own mode), nobody answers the agent's own questions
(`ask_user`) while it works, and `[agent] questions` decides what they do. With `leave` (the default) the question is
stored as `left`, the timeline shows it where it was asked, and the run goes on with what does not depend on the answer,
or ends and says what it needs; its status never turns `blocked`. The run's page shows its open questions in the panel at the
composer's place ([The daemon](daemon.md), item 973), during the run and after it, and `dotx run` prints each one when it is left and lists the open ones after
the answer. Your answer (`POST /api/approvals/{id}/decision`) goes to the conversation as your message, "Answer to your
question “…”: …": into the run working there, or else as a new run on it, whose goal is the goal of the run that asked
with your answer under it (so its goal check and its title keep the task), with the mode, model, reasoning level,
approval mode and goal mode of the run that asked, and as the agent it ran as, within that agent's bounds (item 482), as
a resumed run is; the page then opens that run once no other question of the run is open there. A run reads an answer
that comes during its last step before it ends; one that reaches a run that is then stopped or fails stays in the
conversation as your message, for the next run there. An empty answer is refused. Skip only closes the question, and a
left question never expires. With `wait` such a question waits as in a run you watch, for `approval_timeout_s` at most.

**Questions in a run you watch.** A run you started in any mode but `yolo` (and with free mode off), in the daemon (from the web page, the phone
or the menu bar), chooses for each question whether it waits for your answer (item 508). One asked with `wait`, which it
uses only when nothing useful can be done until you answer, waits as before, for `approval_timeout_s` at most. Any other
is left on the chat as above and the run goes on with what does not depend on the answer: its status stays `running`,
the card waits in the panel at the composer's place (item 973), and your answer comes to the run as your instruction at once (a step whose model
is still answering is cut short and asked again with it, item 509), clearing no mark of untrusted content, as a waited
answer does not ([Safety](safety.md#safety)), or, once it has ended, carries the work on as above, in a run that starts
as untrusted as the run that asked ended. A run of a message sent with Plan first (your own word for that run, so free mode does not change it, item 1563), and a run of a `dotx` command that works in its own process, which
no other process can reach while it works, wait for every answer; a `dotx run` handed to the daemon (item 1203) is the
daemon's run and chooses as the daemon's do.

**Sends kept for you** (item 922). A message, a post or a release whose question nobody answers in
`approval_timeout_s` is not refused but kept as a card with its draft, in every chat and whatever `unanswered` says:
the run is told to keep the draft as it is to go out and to say where it is, and goes on. In a chat Dot opened on its
own initiative a run nobody watches keeps it at once, with no wait. Your yes starts a run in that chat, or reaches the
one at work there: a command of the shell or an application's script is done exactly as its card showed it, once, and
any other step (a Send button of a page) asks you once more from the draft. A send of a run that read content third
parties can author, or whose question was about Dot's own files, is kept too, but never done on the yes alone: the run
the yes reaches asks you for the step itself (item 483's line: a later yes never answers such a question by itself).
So is a send a campaign's session kept: its yes is told to the campaign's next session, which asks you for the step. The yes binds the
command as the card showed it, not the files it reads. [Proactive](proactive.md) says more.

**Steps kept for you.** A step the policy hands to you (money, a credential, a rule of yours that hands it off) waits
`approval_timeout_s` for **I did it**, as every question of the policy does. With `[policy] unanswered = "park"` (item
482; the default, `"refuse"`, counts it as a refusal) one nobody answers in that time is kept for you instead: the run
is told that the step did not happen and waits for you, and goes on with what does not depend on it, or ends and says
what waits for you; its later hand-offs are kept at once, with no new wait, until you write to it or do a step it kept.
A kept step is a `left` record with the fingerprint of its action (tool, kind, summary, target, amount, flags, the files
it changes, the application it opens and, for a page's script, the whole script it runs, item 985; none when its card
shows less than all of it, a write's text or a cut summary)
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
with no card, until you write there again (a message of yours, or a run you start). Allow once is for one step; a
question open now has **Don't ask again in this chat** too, whose yes holds for the step's kind for the rest of the
chat (item 866, [Safety](safety.md#safety) says which kinds and what never). Any other question still counts as a
refusal when nobody answers, and nothing of the kind is kept in `yolo` or in an eval: the payment a `yolo` run of your
chat asks (item 2069) is refused when nobody answers. A message, a post or a release nobody answered is kept as a card
with its draft in every chat but an eval's (Sends kept for you, above, item 922), and in a chat Dot opened on its own
initiative a run nobody watches keeps it at once. A delegated run keeps its
steps where the run that delegated it does (item 485): the card is on the delegated run's page and in the Activity list,
the step is the chat's of the run at the top, and your answer reaches that run, while it works, or else a run that goes
on from its goal in its chat (tainted as any of the runs it goes on from ended), which may do an allowed step itself or
delegate it again. A session of a campaign keeps its steps for the campaign, shared by its sessions (item 484,
[Campaigns](agents-and-campaigns.md#campaigns)).

**An open plan step is not a finish.** Outside a chat, `finish` does not end the run while the plan still has a step
that is not done, failed or skipped. Each such finish is sent back, up to `stall_limit` times, telling the run to
close the steps with `mark_plan_step` (done needs evidence; failed or skipped is how a step is abandoned, with the
reason in `evidence`). After that many reminders the goal check takes the finish when goal mode is on, and otherwise
the progress stop ends the run, so the reminders cannot spin. Closing the steps, then calling `finish`, ends the run
as it asks. One reminder is not enough to let an open plan through, including when goal mode is off.

**The end of a run.** There is no step limit. A run ends by `finish`; by the progress watchdog; by a model call that
still fails after its retries and the wait for a model out of reach (see [Models and providers](models.md#models-and-providers));
in goal mode by the goal check giving up; at a limit of minutes or tokens you set; or when you stop it. With
`verify_answer_paths = true` in `[agent]`, a successful end whose answer names paths that do not exist is sent back once
before it is accepted, and before goal mode checks it (see [How a run works](runs.md#how-a-run-works)). A goal that should go
on across runs and days until its outcome is verified is a campaign (see [Campaigns](agents-and-campaigns.md#campaigns)).

**Limits of a run.** `run_max_minutes` and `run_max_tokens` in `[agent]` bound a task or research run; the defaults are
120 minutes and 10,000,000 tokens, and 0 turns a bound off; a chat turn has none. The minutes are the run's wall time,
waits for your approvals and answers
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
limits; an eval run is cut by its task's own (300 s, 600000 tokens), whatever yours are. A run on a Zen model of
OpenCode (item 552) has them too, read between the agent's steps: the first step that ends past a limit while the agent
goes on tells it to stop and answer, and an agent that still goes on two steps after that is stopped and the run ends
failed, with the same words and the timeline's line. The time limit is kept by the clock too (item 1940): a session
that hangs mid-step is told when the limit passes and stopped five minutes later, the run failed the same way; and a
session that says nothing for `[providers.opencode] silence_s` (600 s) while no tool works for it and no permission
waits on you fails the run, as Claude Code's does.

**The progress watchdog.** After every step in which a run used tools, Dot compares what it called and what came back
(screenshots included) with the steps before. By default it looks for the same step `stall_limit` times in a row: the
model is told twice to change its approach, and the third time the run stops with a result that says what it did last,
what it touched and where its plan stood, and how to go on in the same thread. With `stall_escalation = true` in
`[agent]` it also notices a cycle of two or three steps, `failure_streak` steps in a row in which every call failed, and
`revisit_window` steps that only repeated calls the run had already made, with the same results. It then answers with a
ladder instead: a nudge; a re-plan, in which the next step may only plan or finish; a second opinion of the review model
(`[models] review`), given to the model as advice (if that model cannot answer, the run stops and says so); a question
to you (not in yolo, scheduled, delegated or eval runs, where nobody would answer); and the stop. Twelve steps without
an alert bring the ladder one rung down again, so a long run is not stopped for hiccups that are hours apart. An alert
the run answers on its own (the nudge, the re-plan, the second opinion) is listed in the side panel's Set up, not in the
conversation; the question to you and the stop show in the conversation with what was seen and what was done. The
ladder is off by default until evals show that it helps.

**Checkpoints of a long run** (item 909). A run that keeps changing its steps without getting closer, or that works on
something its goal does not ask for, shows none of the patterns above. So every `checkpoint_steps` acting steps (`[agent]`,
40; 0 never) the review model (`[models] review`) is shown the goal, your words to the run since (apart: a line of a
page that says it is yours extends nothing), the plan and the newest steps (every call and the start of its result,
as many as fit 28,000 characters, item 1132), and says whether
the run is still on track: on track, or when it cannot tell, nothing happens (a wrong alarm costs a run that was going
well); off track, it says why and gives one to three steps back to the goal, which the run is given as a note, and the
watchdog climbs a rung of its ladder as for a pattern, so a run judged off track again and again is stopped with what it
tried. The side panel's Set up lists the check as a progress alert (`drift`) with the diagnosis and the steps. A
checkpoint the review model cannot answer, or that cannot call it (its key is missing, item 1939), is skipped and logged
(`checkpoint_failed`): a run is never stopped for a judge that failed; the consult rung in that case stops the run as
stalled and says why.
A run that ever read content others can author is marked again when the judge's note (or the consult rung's) comes,
as the judge read what the run read, even after your steer cleared the mark.
This is the Watchdog of DeepSeek Harness's Super-agent design (2026-11-01), which judges whether each worker makes
substantive progress or drifts from its goal: in the owner's Dot no progress alert had fired in four days, while runs
of 130 to 230 tool calls ran on, one for an hour until it was stopped by hand. Give a run a goal that can be checked,
and keep an eye on the token count of a long one (`--verbose`, or the facts under More on the side panel's Task tab).

**Resuming a run that was cut off.** A run that a crash, a restart or a shutdown of the daemon cut off, or a run of
`dotx run` in its own process (no daemon ran) whose terminal was closed (or that got SIGTERM) or whose command died, and a run that is blocked or that
failed, can go on where it stopped: Continue
on the run's page, `dotx resume RUN_ID` (in this terminal, like `dotx run`), or `POST /api/runs/{id}/resume`. The next
message you send in that chat, after a blocked, failed or cut-off run, is handed the same note. A new run
starts in the same chat, with the goal, model, reasoning level, approval mode and goal mode of the run it resumes, as
the same agent when it was one of yours (item 462), and the conversation so far, where a shutdown or a restart of the
daemon, or a closed terminal, keeps the results of the calls of the step it was in that had ended (a crash keeps none),
also when it came as their images were being described for a model that cannot see (item 879).
Its first message tells the model how that run stopped and how each call of that step without a result ended:
finished (with the start of its result), cut off while it ran, or not started, and the goal and the plan. A call that was cut off may have changed
something unless it only reads, and the model is told to check before it runs it again. The plan of that run is the plan
of the new one. After a compaction, the note also keeps the open plan and the ids of Dot's records the ledger named,
in that message, not in the system prompt. A session of a campaign is not given that note: its next session
already carries what it needs. A scheduled run is not given a second note on the next message in its chat, because
the schedule's own firing already carries the goal; one that was cut off can still be resumed by hand, and a blocked
or failed firing is left to the schedule's next session. Only the newest run of a chat can be resumed, and never a run that you stopped or that another run
started. After a shutdown, the chat's queue goes on once the new run ends done; after a crash or a restart, it waits
until you resume it. The daemon still resumes on its own only a run that was cut off, not one that failed or is blocked.

By default (`resume = "safe"` in `[agent]`, item 248) the daemon also resumes runs on its own when it starts: runs of
the web UI and the phone, cut off at most `resume_within_s` seconds before (30 minutes) by a restart, a shutdown or a
crash of the daemon. Since item 1854 a call that was cut while it ran and may have changed something (a command, a
write, an agent it made, a delegation) no longer holds the run back: the note names it, and the run is told, in the
words OpenClaw's runs follow, that Dot restarted while it worked and to check the current state of what was in progress
(files, processes, helpers, outside actions) before it repeats any of it, then to go on. What still waits for you: a
run cut in a call that reaches beyond the Mac (a payment, a message or mail, a post, an upload or data sent out, a
release: the set a schedule's task is never tried again for; done or not, a second try could do it twice), or in a
call whose kind Dot cannot tell (a click the screen classifies, a browser click, whose element only the tab's snapshot
of the daemon that ended knew, an unknown tool); a run of `dotx run`, of a
channel, of an eval or a code review; a schedule's run, which the scheduler starts once more instead (item 1206); and a
run that itself resumed another, so a run that brings the daemon down is not resumed again and again. A run you
stopped is never resumed, by Stop, Ctrl-C or the emergency stop. The resumed run keeps the approval mode, the taint,
the plan, the ledger and the proof checks (below) of the run it goes on from, and asks what that mode asks. So a
restart (`dotx service restart`, an update) no longer loses a run's work, and an update need not wait for the runs to
end; `--when-idle` is still there for a restart that should not cut a step in the middle. `resume
= "off"` leaves every such run to you; **Go on after a restart** in Settings › System › Details writes the choice into
the config file (item 231), and the daemon's next start reads it. The new run's turn opens with a line with a bolt
(item 299): "Dot restarted and resumed this turn" when the daemon went on with it by itself, else
"Resumed this turn after" what cut it off; opened, it says how that run was cut off, how each call of the step it was in
ended, and links to it. A run that waits for you wears the same bolt over its composer, with what cut it off ("Dot
restarted while this task worked.") and Continue.

**Lessons.** With `lessons = "auto"` (the default) or `"propose"` in `[agent]`, a task or research run of its own that
reached its goal after something went wrong on the way (a call failed, whether the run found its way around it or not;
the progress watchdog raised an alert; the goal check sent it back as not reached, not to better a reached goal; its answer named paths that did not exist; or
it is the follow-up of your 👎 or 🔁 on an earlier run of the chat, whose work you disliked, item 654: such a
follow-up is asked for its lessons even when it is a chat) is
followed, once it has ended, by one call of the review model (`[models] review`). It reads a digest of the run, what
went wrong, the
answer and the saved notes that fit the goal, and writes at most three lessons of one sentence: know-how that a later
run could use from the start, such as that `pdftotext` keeps the columns of a table only with `-layout`. A lesson of
more than one line, of fewer than 20 or more than 300 characters (or `[memory] max_chars`), with a secret, an e-mail
address or a path in a home folder (found in the forms a page may write them in: fullwidth, look-alike letters,
`ada[at]example[.]org`, `~ada/`, doubled slashes; item 587), a character that does not print, the mark `(you said)`
that is yours alone, or a line that starts like a heading of the prompt or a note of the system, or that says what the
goal, another lesson or a saved note already says, is dropped;
one that says not to do what a saved note says to do (or the other way round) is no repeat of it, however alike their
words and meaning are (`not`, `never`, `n't`, the Turkish `değil`, `yok`, `asla`; the suffix `-ma`/`-me` is not
recognised). With `auto` nobody is asked: each lesson gets a second look first, one more call of the review model that
is shown the goal and the lessons and nothing the run read, so that a page the run read cannot speak to it. It keeps
only know-how about how a tool, a command, a site, an application or a file format behaves, and leaves out a lesson
that tells the agent to send, post, buy, sign in, contact someone, change a setting or go to an address, to trust some
content or to skip a check, or that holds private data; when in doubt it leaves it out. After a run that read content
others can write (web pages, mail, downloads), a lesson with an address to go to (a scheme, a path, `//host`, `data:`,
`javascript:`; a site named bare is know-how and stays) or an account name in it, which such content may have planted,
is left out before it is asked. `auto` saves at most `lessons_per_day` (40) lessons in 24 hours, counting only its own saves (one kind of task takes at most half of them, and a run that read outside content none of the last quarter, item 1713); past it a kept
lesson is left out with that reason, and its run's page still offers Save anyway, which is not capped (item 1247: the owner's runs made 3 to 97 a
day). A scheduled task's, a watcher's task's or a helper's lessons are never decided by `auto`: they wait under
**Notes tasks proposed** on Settings › Memory, and count toward no cap (item 1865). The kept lessons are saved as notes
tagged `lesson` whose source is the run, and
"Lessons from this task", at the foot of the side panel's Task tab of the run's page (item 856), which counts them (item
842: they stood in the conversation; there they stand now only while a lesson waits for your decision, notes wait for
your look after a 👎 or they could not be drawn), says "Saved on its own" with Undo, which forgets the note (a note that
was there before, or that was rewritten since, stays); a lesson left out says why, with Save anyway. With `propose` the
lessons wait on that card, in the conversation: Save to memory keeps one, Dismiss drops it (`POST
/api/runs/{id}/lessons/{index}/save`, `/dismiss` and `/undo`). A lesson is what its last decision made it, and every
decision waits for the one before it: a second save or dismissal of a lesson decided already is refused, save the Save
anyway of one `auto` left out. `dotx run` and `dotx resume` show the lessons after the answer when the run worked in
their own process; Ctrl-C skips the wait, and the exit status stays the run's. The skill work the run's end started
there and that is still going when the command ends is cut, as a stop of the daemon cuts it, while the store is open,
so the run's record keeps its cut-off model call (item 1284). A `dotx run` handed to the daemon (item
1203) does not wait for them: the daemon draws them, and the run's page shows them. A saved lesson is a note of the agent like the others: a later run gets it when it
fits its goal, and the Memory page edits or forgets it; a lesson of an agent's run is that agent's own (see Agents), and
the notes it is checked against are the ones that agent's runs are handed. The same text saved again is the same note,
also one that rested, which is awake again. Delegated runs, chats, runs that failed or were stopped, and evals (which
always run without lessons) are not asked at their end; the nightly reflection asks about the failed and the stopped
ones, once each (a run whose lessons were drawn and none kept says so), and asks again about a run whose lessons could
not be drawn, until the draw has failed three times (item 687). `"off"` learns nothing. Every note a run is handed (at
its start, by `memory_search`, or changed while it ran) is recorded with how the run ended, so the Memory page says
"Handed to 5 runs, 4 done" under a note and `dotx stats` scores the notes. The lessons are also compared with the
adopted harness (item 584): the model is shown its habits and tool guidance, which every run's prompt has, and a lesson
that says what one of those rules says is dropped like a repeat of a saved note.

**Reflection.** Once a day at `[agent] reflection_time` (`"03:30"` in `[scheduler] timezone`; `"off"` never), the
daemon reflects on the runs that ended since the last reflection (the last day, the first time): it asks the review
model for the lessons of the task and research runs the loop did not ask at their end, the failed and the stopped ones
included, with how the run ended as one more signal and in words that say it did not reach its goal, under the
`lessons` setting above (`propose` leaves them on the run's page, `auto` saves the ones its second look keeps while the day has room, `off`
draws none), and it reads whether such a run read content others can write from all its results; it folds the lesson
notes of one agent that say the same, the same way round, into the newer one, or into the older one when you saved
that one yourself (by the embeddings when both have one, by the words otherwise, compared off the daemon's event loop;
a resting lesson is left alone, so one learnt again does not take its rest, and two you saved stay two; the one
folded away rests and names the one it was folded into, so nothing the reflection does deletes a note, but for the
trash below); it rests a
lesson that no run was handed, and that was not written again, for `lesson_rest_days` (60), one that no run was ever
handed after `lesson_unused_rest_days` (21; item 1247), or whose score says it did
not help (handed to `[memory] score_min_uses` runs in the last `score_days`, more of which failed than ended done); a
lesson you saved yourself is never folded away nor retired, and a note saved while reading outside content never folds
away nor puts aside a clean older one (item 557). It also puts aside a note of Dot's that a newer note,
written or changed since the last reflection, made outdated (item 306): the review model reads each such pair of notes
close in meaning (at most 12 a night, the closest pairs first; your notes, the lessons you saved and the pinned notes,
the standing ones among them (a `core` note you wrote or pinned), are never the older one)
and keeps the older one when in doubt; the one it calls outdated rests, names the note that replaced it (the Memory page
says "Replaced by another note") and the log line `note_outdated` says which. It rests a lesson by tagging
it `rest`, after which searches skip it while `dotx memory list` and the Memory page still show it, and the log line
`lesson_retired` says which reason with the numbers; and when anything happened it writes a journal note, tagged
`journal`, of the day it read: the day of the middle of that time, so the reflection at 03:30 names the day before ("On
2026-10-11 I worked on 4 runs (2 done, 1 failed, 1 cancelled). Learned: … Tidied the lessons: 1 merged, 1 retired."), or
its first and last day when it read more than a day and a quarter, after a daemon that was off ("From 2026-10-08 to
2026-10-12 I worked on …"). The journal names the lessons that were saved, not the ones the second look left out nor
the proposals that wait for you, which later runs would otherwise be handed through the note (item 557). The
reflection touches only the notes Dot wrote, never yours. It also keeps what Dot knows about you (see Memory): after a
day with runs, one call of the review model rewrites Dot's lines of that section from the section as it stands, your own
lines, your notes and the day's goals, at most `[memory] owner_chars` (1500) characters. A line of them that the one
gate for learned texts refuses (a link, which a site named by its domain is, an address, an account name, a secret, a
character that does not print, the mark `(you said)`) is dropped and logged, and a night in which every line is refused
keeps the lines of the nights before (see Memory). A night the Mac slept through is caught up once when it wakes. A
daemon that was off at that time, or whose stop cut the reflection (a stop does not wait for one, and it records nothing
until its end), reflects at the next `reflection_time` on everything since the last reflection. The home's Today strip,
when a new chat shows the day (Settings › General › Chat, item 640), lists the reflection among what ended today;
`dotx reflect` runs one now. A reflection that fails is kept: the strip shows "The nightly review failed" with why until
one after it is done (a reflection that could not draw the lessons of some runs is done and failed at once: it says
"lessons of 2 runs failed", is kept as a failure, and counts in the record of how learning fares, which tells you when
the reflection failed three nights in a row), and `dotx stats` says when the reflection was last done and the failure since; with the day not
shown, `dotx stats` is where to look. Learning does not stop unseen at a run's end either: lessons that could not be drawn
leave `lessons_failed` with why on the run's page, skill work that failed `playbook_failed`, and `dotx stats` counts the
runs of each. The card of a run's lessons arrives folded (item 374); its head says "could not be drawn" in red, or how
many lessons wait for your decision in amber, else how many there are, and opened it shows why, or the lessons.

**The reflection tidies the notes and forgets none (item 584).** In the same night it rests the lessons that say what a
rule of the adopted habits or tool guidance says already (log line `lesson_retired`, reason `habit`); it shows the
review model the notes that are at least `[memory] merge_similarity` (0.80) close in meaning and merges the ones it says
are the same, at most `merges_per_night` (20) a night (the merged note keeps one source's words, the sources rest whole,
and **Undo the merge**, in the note's **…** on the Memory page, brings them back); it counts the notes that rested for
`forget_after_days` (90) as proposals for you to forget or keep, and forgets none; and it deletes from the trash the
notes you forgot more than `trash_days` (30) ago, the only thing it deletes. A note you wrote, a lesson you saved, a
pinned note (the standing notes, a `core` note you wrote or pinned, among them) and a journal are never merged, rested
or proposed by it; a `core` note a run wrote is never merged either, but rests and is proposed as any of a run's until
you pin it (item 613). None of it writes What Dot knows about you. `dotx reflect` says what it did, among it how many
notes wait for you to forget or keep them.

**The reflection and the learned skills (item 585).** In the same night a skill whose record counts uses and has no
date of its last read is given one, and the review model is asked which pairs of skills whose kinds are close in
meaning (`[agent] skill_merge_similarity`, 0.75) are one procedure: each yes is a merge that waits as a card on
Settings › Skills, at most `skill_proposals_per_night` (3) a night, for your yes. No skill is written, rested or
deleted by it: see Learned skills in [Extending Dot](extending.md).

**Taking back the file changes of a run.** At the head of the side panel's Changes tab of a chat's run (item 841;
under its answer until then), its line of what it changed has an **Undo**, which does what follows for the files it
lists (item 597).
Before `write_file` or `edit_file` changes a file, the run notes the change
in its journal (`undo/` in the home directory), and its first change to a file keeps a copy of the file as it was. When
the change cannot be noted (a full disk, say), the file is not changed and the model is told why; a note the disk cut
short is left out of the journal, so the run's next change is noted after it and `dotx undo` still reads it. The side
panel's **Changes** tab reads the same journals (item 368): each file a chat's runs changed with `write_file` and
`edit_file` (a run's own notes are left out), diffed from the copy the
chat's first change to it kept to what is there now. A file where the panel beside a chat shows no file (a hidden
folder, an application's files, outside the home folder), a binary file, one past 512 KB, one that is gone and one
whose first change kept no copy show why instead of a diff. Each side of a diff is masked whole, before it is diffed,
of the secrets Dot holds, so a private key over many lines is masked whatever part of it changed. A command's own writes
are not in the journals, so they are not there; the run's line says instead how many steps it ran with a command, the
browser, the screen, a message or a connected app, "Dot also ran 3 commands and 2 browser actions. Undo takes back files
only, so it cannot take these back" (item 1404), by what the gate let run and the steps' own records, with no claim of
what they did. Under it stands the run's account (item 1746), built from its tool results and its plan, never from what
the model said: how many browser and desktop steps were seen to hold and how many were not (not checked, uncertain,
contradicted) or failed, what it sent for the owner or in public, the plan steps marked done that no tool result shows,
and those left open. `GET /api/runs/{id}` and `GET /api/runs/{id}/actions` carry it as `account`, so the phone has it
with the run's detail. `dotx undo RUN_ID` shows what taking back the changes of that run and of
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
A run ends once (item 1045): the first end stored stands, with its `run_finished` event and the scores of the notes it
used, and a later one (the process of a `dotx run` that was taken for dead ending it after all) is not stored over it;
a queued run that was closed before it started does none of its work.
`dotx prune --apply` deletes the journals whose last change is older than `undo_keep_days` (30 days).
`GET /api/runs/{id}/actions` lists the same plan, one file at a time, and `POST /api/runs/{id}/undo` takes back a file
that is as the run left it.
