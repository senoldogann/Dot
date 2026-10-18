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
   each decision tied to an observation; or it keeps the current harness. The composed version is a candidate marked
   `probed`, with the decisions and the evidence in its notes.
4. **Adopt.** You adopt it, or not, on the harness page or with `dotx harness adopt`. `[harness] adopt = "auto"`
   (off by default; a later item) adopts on its own only a version the probe showed better on every task.
5. **Reflect.** After the step, a reflector writes what each edit did (helped, hurt, no observed effect, confounded)
   and, when the evidence supports one, an *experience*: the cue (the shape of situation), the lesson, the evidence
   and its confidence. Experiences go to a library the proposer reads first next time; a step whose evidence shows no
   reusable contrast writes none.

The step runs once a night at `[harness] step_time`, under its own health record, and by hand with `dotx harness step`.
Its provenance (version ← probe ← run, experience → version, the decisions of each step) is a graph the loop keeps, so
that what Dot changed about itself, when, and on what evidence, can always be read (the command line and the API read
it; drawing it on the page is left for later).

## What is built so far

Item 389 builds the versions, their components, the prompt's reading of the adopted one, the command line and the API
above. The roles, the probe, the nightly step, the provenance graph and the page follow, one item each; this page
grows with them.
