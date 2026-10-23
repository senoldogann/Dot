# The harness: what Dot improves around its model

Dot's model is fixed; what stands around it is not. The system prompt, the guidance on the tools, the skills Dot
learned from its own runs and the notes it keeps are the *harness*: the soft parts that decide how well a given model
works on this Mac. This page says which of those parts Dot may improve, how a change is proposed, tried and adopted,
and what never changes without you.

The method is Harness² (Xu et al., arXiv 2610.14224), adapted to one always-on agent whose owner is present: a change
is proposed from what a run did, tried against the same work beside the current harness, composed from what the trial
showed, and adopted by you. Evidence-backed lessons from each step feed the next. Model weights, the policy and the
approval gate stay as they are.

## What the harness holds

A *version* of the harness is a small set of texts, kept in Dot's store with who made it, what it was made from, and
the evidence behind it. Every run's system prompt takes the texts of the one adopted version, after Dot's own text:

| Component | Where it goes in the prompt | What it is for |
|---|---|---|
| **Habits** | After how to work, before the tools | Ways of working that earlier runs taught: planning, when to delegate, how to handle a long output. Always on. |
| **Tool guidance** | After the tools and the rules of communication | What earlier runs learned of the tools on this Mac: a flag a command wants, a site that needs a slower pace. Always on. |

Both are plain text you can read on one screen, at most `[harness] habits_chars` and `tool_guidance_chars` characters
(1,200 and 1,500) in at most `component_lines` lines (16), no line past 300 characters. A text with a secret, a link, an
e-mail address, a path in a home folder or an account name is refused (a page a run read may have put any of them into a
proposal, and a prompt is forever), and so is one with a character that does not print (a control, a zero-width or a
bidi character), which could hide a line from you. The learned skills (`<home>/playbooks`, [Memory and
schedules](memory-and-schedules.md)) and the notes are harness components too; they keep their own paths, which already
validate what is written.

Not in the harness, by design: the rules of trust and safety and the secrets line of the prompt, the policy engine, the
approval gate and your rules, the credentials, `config.toml`, Dot's code, and scripts or hooks the model would run. The
habits stand in the prompt before the rules of trust and safety and the tool guidance after them, and each says that the
rules of the prompt, trust and safety among them, win over it.

## Versions and adoption

A version is one of:

- `candidate`: written, tried by nobody;
- `probed`: tried by the improvement loop beside the current version, with the evidence in its notes;
- `adopted`: the one every run takes; one at most;
- `retired`: put aside, kept for the record and to come back to.

Adopting a version retires the adopted one; adopting an earlier version again is how a change is taken back. A run
records the version it took (`harness_version` on the run), so what a version did for the runs that had it can be
read later. With no adopted version, a run's prompt is Dot's own text alone, as before item 389.

You write a version yourself with `dotx harness set` (a component from a file or standard input, the other kept from the
adopted version, `--adopt` to take it at once) or `POST /api/harness`, and manage them with `dotx harness list`, `show`,
`diff`, `adopt` and `retire`, or `GET /api/harness`, `GET /api/harness/{id}`, `POST /api/harness/{id}/adopt` and
`POST /api/harness/{id}/retire`. The daemon reads the adopted version as each run starts: no restart.

## The scoring set is the owner's

What a candidate is judged on is never shown to the model that writes it. The proposer reads one run; the probe runs the
candidate on a *scoring set* it has not seen: the eval tasks that are safe to repeat, and the runs of yours you mark as
tests. A second, smaller *final set* is never used to choose between candidates: `dotx harness final` runs it once,
after a version is adopted, and reports it as it comes out, so that the score you read was not optimized against. The
owner writes the questions, never the program; that, and adopting, is the human's part (Wei et al., "The End of Harness
Engineering", 2026, make the same cut: an execution report the run writes, candidates a small model writes, a scoring
set the operator writes and the proposer never sees, and a final set used once).

Selection is by score first: a candidate that does better on every task of the scoring set survives, one that does
worse on any is dropped. The composer merges edits only when candidates do not conflict and each earns its place, or
explains why the base is kept.

## How a change comes about

The improvement loop (the items after 387 build it in layers) works one *step* at a time, with a budget `k`:

1. **Propose.** A proposer model reads what a recent run did (its digest, what went wrong on the way, what it ended as),
   the current harness and the index of earlier experience, and writes at most two candidates: one about *how the
   agent works* (planning, tool use, context, delegation), one about *what a practitioner knows* (the knowledge, the
   procedures, the checks of the domain). The second usually becomes a learned skill rather than a habit. Every edit
   names the moment of the run that motivates it; a proposal with no such moment is `KEEP`.
2. **Probe.** Each candidate runs the same work as the current harness: the eval tasks that are safe to repeat, in a
   throwaway home with the exclusions of an eval (no MCP servers, no desktop, a headless browser, no learning), within
   a budget of tasks, model calls and time. What comes back is a scorecard per run (the ending, the steps, the tokens,
   the seconds, the tool errors, the approvals asked, the checks passed) and a *contrast* with the current harness:
   the edit helped, changed nothing, or hurt. A probe says how a candidate behaved, not that it is right: runs vary,
   and the models share their blind spots.
3. **Compose.** A composer reads the candidates, their diffs and the contrasts, and keeps, revises or drops each edit,
   each decision tied to an observation; or it keeps the current harness. The composed version is played on the scoring
   set in its turn and survives, as `probed`, when it did better than the base, with the decisions and the evidence in
   its notes; else the best candidate does.
4. **Adopt.** You adopt it, or not, on the Self-improvement page or with `dotx harness adopt`. `[harness] adopt =
   "auto"` (off by default) adopts on its own only a version its own probe showed better on a task and worse on none,
   with two repeats, and never one written from a run that had read content third parties can author: that decision is
   yours.

5. **Reflect.** After the step, a reflector writes what each edit did (helped, hurt, no observed effect, confounded)
   and, when the evidence supports one, an *experience*: the cue (the shape of situation), the lesson, the evidence
   and its confidence. Experiences go to a library the proposer reads first next time; a step whose evidence shows no
   reusable contrast writes none.

The step runs once a night at `[harness] step_time`, under its own health record, and by hand with `dotx harness step`.
Its provenance (version ← probe ← run, experience → version, the decisions of each step) is a graph the loop keeps, so
that what Dot changed about itself, when, and on what evidence, can always be read (the command line and the API read
it; drawing it on the page is left for later).

## The roles

Four roles play the step, each a model asked one question that it answers with one tool call (as the review model
answers Dot's other questions), each shown digests and texts inside fences it reads as data and never follows, with
the secrets Dot holds masked: the **proposer** (`propose_harness`: a candidate's whole texts within the bounds, the
moments of the run behind each change, or keep), the **composer** (`compose_harness`: one version of the candidates,
a decision with its evidence for each change, or the base kept), the **selector** (`select_answer`: the best of
anonymous answers to one goal, judged on delivery, conformance, fidelity, consistency, fitness and finish, never on
length) and the **experience writer** (`write_experience`: what each edit did, and an experience only when an edit
helped or hurt with evidence). An answer that breaks its rules (a text past the bounds, a change with no moment, an
experience with no effect behind it) is asked once more with what was wrong (the fields and their problems, never a
key the model made up, as the step says them when it fails), then the step fails and says so.
`[harness] proposer`, `composer`, `selector` and `experience` name a model for each; "" is `[models] review`.
`dotx harness propose RUN_ID [--scope invariant|domain]` asks the proposer about one run by hand and writes its
candidate, whose notes keep the scope and the moments; `keep` writes nothing.

## The step, as built

`[harness] step_time` (a time of day; `off` by default) has the daemon run one step a night, and `dotx harness step
[--run ID] [--scripted]` runs one by hand. The step reads the newest run of yours that ended since the last step (a task
or research run of its own, as the reflection reads them), asks the proposer for a candidate in each scope of
`step_candidates`, then plays the scoring set (`scoring_tasks`, eval task ids) `step_repeats` times under the adopted
texts and under each candidate. Each probe is a process of its own (`dotx harness probe`), because an attempt switches
the process's Dot home to its throw-away one, which the daemon and your runs must never see; the step reads the report
back. A candidate that passed more on a task and less on none survives; two that both did are merged by the composer,
and the merge is played in its turn: it survives when it did better than the base, else the best candidate does. The
survivor is written as `probed`, with its contrast and the decisions in its notes, and the others are retired with
theirs. The experience writer then says what each edit did, and an experience is kept when an edit helped or hurt with
evidence. The step stops before the attempt that would pass `step_minutes` or `step_tokens` of the model under test (the
next attempt is taken to cost what the earlier ones did; a probe with nothing left plays nothing, one that outlives its
budget is ended, and a probe cut short leaves its candidate `unclear`, which never survives), records what it did (`dotx
harness step` prints it), and counts in how learning fares with the reflection. `adopt = "auto"` adopts the survivor on
its own when it did better on a task and worse on none with `step_repeats` of at least 2, and never from a run that had
read content third parties can author; `"owner"`, the default, leaves it to you. A role that fails after the probes (the
composer, the experience writer) leaves the step `failed` with the error, the survivor probed all the same, and how
learning fares counts the failure; a scripted check is recorded and marked `scripted`, and does not move the window the
next step learns from. `--scripted` plays the references through the scripted model and has the roles answer from a
script (the proposer proposes one habit and keeps the other scopes, the experience writer saw no effect): the whole
step, at no model's cost, as a check of the loop.

`dotx harness final [ID]` plays the final set (`final_tasks`) once under the adopted version, or the one named, apart
from the daemon, keeps the report in the version's notes (the end of `dotx harness show`), and prints it as it comes
out. What the first step with a real model showed (2026-10-19, Claude Haiku 5.5): the eight scoring tasks are passed 2/2
under any text by a capable model, so a candidate can come out worse on them but never better, and the loop keeps;
before the step can adopt on evidence, the scoring set needs tasks with headroom (ones the main model fails sometimes),
which is left for later, as is a secondary signal (steps, tokens) when pass rates tie.

## Provenance

What Dot changed about itself, when, and on what evidence is a graph the store keeps and the owner can read: a version
is *made from* the one adopted when it was written, and *composed of* the candidates the composer merged; a step
*learned from* a run, *proposed* its candidates and *probed* the survivor; an experience is *about* a version and
*written in* a step. `GET /api/harness/graph` returns the nodes (versions with their state, the newest steps, the
experiences with their confidence, the runs learned from) and the edges; `dotx harness graph` draws the same as a
Mermaid flowchart. A version's *lineage* (`GET /api/harness/{id}/lineage`, the end of `dotx harness show`) is the same
read from that version: its ancestors, the attempts of the probes that played it, the steps that touched it and the
experiences about it. `dotx harness steps` and `experiences` (`GET /api/harness/steps`, `/experiences`) list the newest
of each, and `GET /api/harness` counts them all (`steps_total`, `experiences_total`); the graph carries the newest
steps (`--steps`, 50 by default) and four experiences a step. The library is in use: the proposer is shown the newest
experiences, a line each of the cue, the lesson and the confidence, before it writes a candidate. An experience
written in a step that learned from a run which had read content third parties can author is kept marked
(`tainted`, said on the page) and never shown to the proposer, as a tainted note is never shown to the lesson
writer: its words cannot pass into a clean run's candidate unmarked.

## The page

Settings › *Self-improvement* is the harness's page, in plain words (item 503): it opens by saying that Dot learns
from its own runs how to work better on this Mac, that every run follows two short texts besides Dot's built-in
instructions, that each night Dot can propose a new version and try it on test tasks first, and that you choose the
version it follows and never have to write one. Its words for the states are *new* (`candidate`), *tried* (`probed`),
*in use* (`adopted`) and *set aside* (`retired`); **Use** adopts and **Set aside** retires.

A row of four tiles in one card comes first (items 527 and 566): what is in use (*Built-in*, or *A version* with its
summary), the number of versions with how many are in each state, the steps of learning, nightly and by hand, with the
last one's outcome (or when the next is, or that the learning is off) over a strip of the newest 14 (green a change,
grey none, red a failure, named in a legend and each step on a hover), and the lessons kept. The two counts are of every
step and lesson, past the newest the lists below show. On a phone the tiles go two to a row, and no column of the page
is wider than the screen, the narrowest iPhone's too.

*What Dot follows now* is a card with the adopted version's summary, an *In use* pill and **Set aside** at its head, and
its texts side by side under it where the page is wide enough (*Habits · how to work*, *Tool guidance · what this Mac
wants*), or that Dot's built-in instructions alone are in use. **Write your own**, at the head of *Versions* (item 566),
opens the form, which stays shut until then; it takes habits and tool guidance (prefilled from the adopted version) and
a summary, and says what each takes (item 412): that writing one is optional since the nightly learning proposes
versions, what a habit and a line of tool guidance are, the limits a version keeps and what each button does, with
examples in the empty fields and **Start from an example**, which puts them in; **Save for later** keeps it as a
candidate and **Save and use** adopts it at once; what cannot be a component is refused with why. *Versions* lists every
version, the one in use first and the others the newest first, with its state, who made it (you, or Dot's proposer or
composer) and when, its texts and notes (what a proposal is about and the moments of the run it answers, the trial's
contrast, the decisions of a composition) under *Texts and notes*, **Use** on each version not in use and **Set aside**
on a new or tried one; the version in use is set aside from *What Dot follows now*, and setting a version aside asks
first. *Nightly learning*, under *Versions*, opens with when the step runs (every night at its time, with the next one,
or that it is off and which setting turns it on); it and *Lessons learned* list the newest steps and experiences, a step
with the run it learned from and what it decided. *How it learns*, a fold, says the test tasks (the scoring set) and
the final check (the final set), who chooses, and the limits. On a phone the page is a row of the settings list under
*How Dot works*, which says *built-in* or *changed*.

## What is built so far

Item 389 built the versions, their components, the prompt's reading of the adopted one, the command line and the API.
Item 390 built the roles and `dotx harness propose`. Item 391 built the probe, the step and its schedule. Item 392
built the provenance graph, the lineage and the library's reading. Item 393 built the page. Left for later: a run of
yours kept as a test of the scoring set from its page, the step started from the page, and the graph drawn on it.
