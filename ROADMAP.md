# Autonomy roadmap and handoff

The owner asked for Dot's autonomy to be smarter, steadier and sturdier **whatever model runs it**. This file is the
handoff for that work: what is planned, why, in which order, what is done and how it was checked. If you continue the
work, read it first, and keep the status table, the item sections and the log at the bottom true. It is the only record
of where the work stands.

## Principle

Do not count on the model being clever. The harness measures, notices, steers and recovers, so a weak model gets more
scaffolding and a strong one needs less. The order follows from that: measure (1), notice (2), adapt (3, 4), check (5),
recover (6), learn (7), undo (8). Every later item is judged with the numbers of item 1.

## Status

| #   | Item                                                        | Status  | Commits |
| --- | ----------------------------------------------------------- | ------- | ------- |
| 1   | Eval harness and run statistics (`dotx eval`, `dotx stats`) | planned |         |
| 2   | Progress watchdog and escalation ladder                     | planned |         |
| 3   | Model-adaptive scaffold (`dotx models profile`)             | planned |         |
| 4   | Smaller, progressive tool surface                           | planned |         |
| 5   | Verification of the end of a run                            | planned |         |
| 6   | Safe resume after a restart                                 | planned |         |
| 7   | Learning from runs (lessons)                                | planned |         |
| 8   | Undo journal for file edits                                 | planned |         |

Status values: `planned`, `in progress`, `done` (implemented, tested, verified, committed), `parked` (the reason is in
the item). Take the first item that is not `done`.

## Where things stand before this roadmap

- Baseline `8848d69` (tag `pre-audit-fixes`; the folder had no git before). Audit fixes on top of it, one commit per
  wave: A `9e6eb5a` run integrity (tool calls stored per step, atomic message writes, no double start, a shutdown has its
  own result text), C `159c75b` data upkeep (screenshots follow their chats, `dotx prune`, compaction of the database),
  D `a91aa47` Turkish-aware search (one fold), paging of the chat list, delete confirmations focus Cancel.
- Audit leftovers nobody has started, the owner's call: B speed and cost (the model that compacts, the `wait_text`
  view, the 420 s model-call handler, tool schemas; item 4 here overlaps the last one), E hardening of the `ask` and
  `auto` approval modes, F infrastructure (CI, small memory and tool-exception issues, documentation drift).
- The owner runs the daemon in `yolo` on purpose. Do not change, restrict or argue with that.
- The owner's daemon runs the code it loaded when it started. Python changes take effect after the owner restarts it;
  the static web files are served from disk.

## How to continue

1. Read this file, then `git log --oneline` and `git status`. A dirty tree means an item was interrupted: read the diff
   and the item's section before touching it.
2. Check the ground before you build on it: `.venv/bin/ruff format --check src tests`, `.venv/bin/ruff check`,
   `.venv/bin/mypy`, `.venv/bin/python -m pytest tests/unit -q` must all be clean.
3. For the item you take: read the code it touches (the "Touchpoints" list is a start, not a limit), refine the design in
   its section if reading proves it wrong, set the status to `in progress`, then implement, test, verify, commit.
4. After each commit: set the status and the commit hash in the table, tick what is done in the item, and add a line to
   the log. A reader must be able to resume from this file alone.
5. Anything that needs the owner (a live paid run, a daemon restart, a default that changes how their agent behaves)
   goes into "Open owner decisions"; keep going with the next item meanwhile.

### House rules (from the owner)

- Python 3.13 in `.venv`. Functional style: pure functions that only return values, never mutate parameters or global
  state; classes only for connectors to external systems. Strict typing everywhere: no `Any`, no `unknown`, no loose
  dicts, pydantic `FrozenModel` for structured data. No default parameter values. No parameter that switches a
  function's behavior. All imports at the top. Comments and docstrings in English, stating the current behavior (never
  a changelog). Match the style of the code around you.
- Errors: raise specific `DotError` subclasses with debug context; never swallow, no catch-all handlers, no silent
  fallbacks, fix root causes. External calls retry with warnings and then raise the last error. Structured log fields,
  not interpolated messages.
- Tests: real calls over mocks; integration or smoke tests before unit tests; unit tests only for pure transformations
  and stable datasets; add the minimum the change needs. Use temp directories (`DOT_HOME`, `DOT_CHROME_DIR`) and never the
  owner's data directory (`~/Library/Application Support/Dot`): not even to read message text. Counting rows is the
  most that was ever done there.
- Safety: never start, stop or restart the owner's launchd daemon. Nothing that deletes the owner's data may run by
  default: retention and cleanup are explicit commands. Commit locally, one commit per item or coherent step, with the
  agent's `Co-Authored-By` trailer; never push, never rewrite published history. `PLAN.md` (the owner's working notes,
  untracked on purpose, excluded in `.git/info/exclude`) is not committed.
- New configuration keys go into `src/dotagent/defaults.toml` with a comment, and into the matching model in
  `src/dotagent/config.py` (a key that does not exist is an error). User-visible behavior goes into `README.md`.
- Search with `rg`. Use non-interactive commands. `git --no-pager diff`.

## Items

### 1. Eval harness and run statistics

**Goal.** A repeatable, model-independent measure of how well Dot finishes tasks, and the same numbers for the runs that
really happened.

**Why.** Nothing in the repo measures whether a task gets finished: the smoke tests prove that features work. Without
numbers every other item is a guess and "works with any model" is a claim. Real data before the audit fixes: 32 runs
(15 done, 14 cancelled, 2 failed, 1 running), about 103 M input tokens over 2,772 steps, so about 37 k input tokens per
step. Why 14 runs were cancelled cannot be told from the old data, because a restart and a user stop wrote the same
text; since wave A they differ.

**Design.**
- `src/dotagent/evals/`: `types.py` (`EvalTask`, a union of `Check` types that are data, not code: file exists, file
  contains, file equals, JSON equals, command succeeds, answer contains, path absent; the expected ending), `catalog.py`
  (about 20 tasks as data: file work, shell work, recovery from an error, multi-step work, research over a local HTTP
  fixture, an impossible task whose right ending is an honest failure, "do not touch X", an injection bait inside a
  file), `scripted.py` (a `ChatProvider` that replays scripted replies: the oracle of the self-check and the stand-in
  model of the tests), `runner.py`, `scoring.py`.
- The runner builds a runtime on a temp `DOT_HOME` (own database, own workspace), seeds the task's files, starts the
  run through `RunManager` with approvals `ask` and a watcher that denies every question (unattended and safe), applies a
  wall-clock limit and a token budget, then evaluates the checks. API keys are looked up (environment, `.env`, Keychain)
  before the process switches `DOT_HOME`, so the real database and workspace are never touched and no secret is copied.
- `dotx eval list | check | run [--model REF] [--task ID]... [--repeat N] | report FILE...`. `check` is offline and free:
  every task's reference solution must pass its checks through the real loop and tools, and a "finish at once" run must
  fail every task, so no task is trivially passable. `run` spends model tokens and prints a scorecard per model (pass
  rate, steps, tokens per pass, tool error rate, stalls, timeouts, false claims of success) and saves it under
  `<home>/evals/`.
- `dotx stats [--days N]`: read-only SQL aggregates over the real runs: outcomes per model, steps, tokens, tool error
  rate, invalid-argument rate, stalls, compactions, why runs stopped.

**Touchpoints.** `cli.py` (command union, parser, dispatch; follow `cli_prune.py`), `store/db.py` (one aggregate query),
`runtime.py` (how a runtime is built), `agent/manager.py`, `secrets.py`, `README.md`.

**Done when.** `dotx eval check` passes offline; unit tests for the checks and the scoring; an integration test drives
the runner with the scripted provider on a temp home; README documents the commands.

### 2. Progress watchdog and escalation ladder

**Goal.** Notice a run that is not getting anywhere, in all its disguises, and answer with steps that can actually help
before giving up.

**Why.** The stall guard in `agent/loop.py` fires only when the last `stall_limit` (4) steps have identical calls *and*
results; after three such warnings the run fails. It misses A-B-A-B cycles, a long streak of different failing calls and
the re-reading of results the run already has. The README admits: "A run that varies its steps without getting anywhere
ends only when you stop it." And when it does fire, all it does is repeat one note three times.

**Design.**
- `agent/progress.py`, pure: an observation per acting step (fingerprint, all-failed flag, a digest of the results that
  includes the hashes of images), a window, and four detectors: exact repetition (today's), a cycle of period 2 or 3, a
  failure streak (`failure_streak` consecutive steps in which every call failed), and revisiting (nearly every result
  in the last `revisit_window` steps was already seen earlier in the run). Plan stagnation (open plan, no step closed for
  a long time) is a weak signal; add it only if the first four leave a gap.
- A ladder with a cooldown, so one hiccup is not an emergency: (1) a nudge that names the detected pattern; (2) a
  forced re-plan: for one step the model is offered only `update_plan`, `ask_user` and `finish`; (3) a consultation: one
  judgement call to the review model (same mechanism as the goal checker in `agent/review.py`) that gets the goal, the
  plan, the ledger and the pattern and answers with a diagnosis and up to three next steps, injected as a system note;
  (4) the model is made to ask the user one concrete question (`ask_user`), skipped for delegated and scheduled runs;
  (5) stop, with a result that says what was tried, where it stands and how to continue in the same thread. A long
  healthy stretch steps the level down again, so a run that runs for hours is not killed by hiccups that are hours apart.
- `stall` events carry `level`, `reason`, `detail`, `action` (keep `warnings` so the UI and `cli_ui.py` keep working;
  update `timeline.js` to show the reason).
- Config in `[agent]`: `stall_escalation` (false restores the old behavior), `failure_streak`, `revisit_window`.

**Touchpoints.** `agent/loop.py` (the stall block around `recent`/`stall_warnings`; `_fingerprint`), `agent/review.py`,
`agent/prompt.py`, `server/static/js/timeline.js:476`, `cli_ui.py:245`, `config.py`, `defaults.toml`, README.

**Done when.** Unit tests of the detectors and the ladder on scripted sequences; integration tests on the real loop with
the scripted provider (flailing then recovering is not killed; flailing for good escalates in order and ends with a
useful result; a healthy long run is untouched); the ladder is visible in the UI timeline.

### 3. Model-adaptive scaffold

**Goal.** Give each model the amount of help it needs, from facts, not from a global setting.

**Why.** `ModelInfo` knows what a model *offers* (vision, tools, thinking, context length) and nothing about how
*reliably* it works. `context_length` is not used by the loop at all: `[agent] context_tokens = 48000` is global, so a
model with a smaller window overflows before compaction starts. Long runs drift from their goal because the goal sits
at the start of the context.

**Design.**
- `agent/scaffold.py`: `ModelProfile` (measured: steps, tool calls, tool error rate, invalid-argument rate, stalls per 100
  steps, empty and no-tool replies), computed by one SQL aggregate over a model's recent runs (from item 1's stats
  function; eval runs saved under `<home>/evals/` count as samples too), and `scaffold_for(agent_config, info, profile)`, a pure
  function that returns the settings of one run: effective `context_tokens` (never above a share of the model's window),
  `tool_output_chars`, `keep_recent_tool_results`, `parallel_tools`, a reminder interval, a tool surface.
- Reminders: every `reminder_every` steps the loop adds one system note with the goal word for word, the open plan steps
  and the last results, the known cure for drift in long runs. Off for models that measure as reliable.
- Too few samples means the config defaults, unchanged. A model with at least a stated number of tool calls and an
  error rate above a threshold gets the weak tier. Optional explicit overrides per model in `config.toml`.
- `dotx models profile [REF]` prints the measured profile and the scaffold chosen, so nothing is hidden.

**Touchpoints.** `agent/loop.py` (reads `config.*` in about ten places at the start of `run_agent` and in
`_fit_context`), `llm/types.py`, `store/db.py`, `cli.py`, `config.py`, `defaults.toml`, README.

**Done when.** Unit tests of `scaffold_for` on stable inputs; the loop uses the scaffold; a small-window model gets a
smaller compaction threshold; `dotx models profile` works on a temp database.

### 4. Smaller, progressive tool surface

**Goal.** Offer a model the tools it needs now, and the rest on request.

**Why.** Measured on the real registry (task mode, no MCP servers): 46 tools, about 8.2 k tokens in every model call.
`desktop` 2.4 k, `browser` 2.1 k, `schedule` 0.5 k, memory administration 0.3 k: two thirds of it belongs to groups
most runs never use. The core (meta, files, web, shell, memory search and save, delegate, skill) is about 2.7 k. MCP
servers add their own tools on top. For a weak model forty tools are forty ways to pick wrongly.

**Design.**
- Tool groups by name: `core`, `browser`, `desktop` (with `apple_script`), `schedule`, `memory_admin`, one group per MCP
  server. A meta tool `use_tools(group)` enables a group for the rest of the run; a call to a known tool of a disabled
  group is answered with an error that enables the group ("`browser_open` is in the group `browser`, now on: call it
  again"), so a guess costs one step. Groups only grow within a run (the prompt prefix stays stable for caching). The
  system prompt lists the groups with one line each. A thread that used a group earlier starts with it on.
- `[agent] tool_surface = "full" | "grouped" | "auto"`; `auto` means grouped only when the scaffold (item 3) says so
  (weak tier or small window). The default stays `full`/`auto` until `dotx eval` shows that grouped does not hurt; the
  owner flips it.

**Touchpoints.** `tools/registry.py`, `tools/meta.py`, `agent/loop.py` (the `specs` of a run), `agent/executor.py` (unknown
or disabled tool), `agent/prompt.py`, `mcp/`, `config.py`, `defaults.toml`.

**Done when.** Unit tests of the grouping and enabling; an integration test on the scripted provider (disabled-group
call, error, enabled, next step offers the schema); sizes measured again and noted here.

### 5. Verification of the end of a run

**Goal.** Do not accept "done" on the model's word when the harness can check.

**Why.** Goal mode already asks the review model to compare the end with the goal, and gives it a digest of the steps.
What it lacks is facts the harness measured itself, and without goal mode nothing checks the ending at all. By default
`[models] review` is the same model as `main`, which shares its blind spots.

**Design.**
- Facts block for the checker (`GoalRequest.facts`): files touched, the failures nobody resolved (the last failed
  call of a tool that no later success of the same tool and target followed), the last test/build/lint-looking shell
  commands with their results, and the paths and addresses that the answer names but the run never touched and that do
  not exist (reuse `identifiers()` of `agent/continuity.py`).
- Every task or research run: when the answer names a path that does not exist, the run is sent back once with the
  list (no extra model call). `[agent] verify_answer_paths`.
- `dotx doctor` reports when `[models] review` is the same model as `main` ("independent checking is weaker"), with the
  README advice to pick another model family. Informational, never a failure.

**Touchpoints.** `agent/review.py` (`GoalRequest`, `_goal_prompt`), `agent/loop.py` (the end of a run), `agent/continuity.py`,
`cli.py` (doctor), README.

**Done when.** Unit tests of the facts and of the unverified-path check; an integration test (a run that claims a file it
never wrote is sent back once); doctor shows the review-model note.

### 6. Safe resume after a restart

**Goal.** A restart or a crash does not have to end a run.

**Why.** A graceful shutdown leaves the run `cancelled` with its own text; a crash is closed by `mark_interrupted_runs` as
`failed` ("daemon stopped while the run was active"). Either way the owner continues by hand, and the thread keeps every
step and the calls that were interrupted (`repair_history` gives them a placeholder result). The risk of automatic
resume is real: a restart is also how the owner clears a stalled run, and a resumed run may repeat a side effect.

**Design.**
- Classify the interrupted calls with the policy vocabulary that exists: `READ_ONLY_KINDS` in `policy/types.py` (classify
  with the tool's `prepare`, never run it); an unknown call counts as a side effect.
- `pending_calls(messages)`, a pure function next to `repair_history`, lists the calls of the last assistant message that
  have no result.
- A resume starts a new run on the same thread (source `resume`) with a note: the original goal, what was interrupted,
  "read-only calls may be repeated; check the state before repeating anything else". No new run status.
- `[agent] resume = "off" | "safe"`, default `off`. `safe` resumes, at startup, only the runs a restart or crash
  interrupted (never a user stop), recent ones, and only when every pending call is read-only. Always available by hand:
  `dotx resume RUN_ID`, and a "Continue" button on the run page of an interrupted run.
- Restart-to-clear-a-stalled-run: the owner stops the run first, then restarts; a run the owner stopped is never resumed.

**Touchpoints.** `agent/manager.py` (`recover`, `shutdown`), `store/db.py` (`mark_interrupted_runs`), `agent/compact.py`,
`policy/types.py`, `server/routes_runs.py`, `server/static/js/views/run.js`, `cli.py`, `config.py`, README.

**Done when.** Unit tests of `pending_calls` and the classification; an integration test that interrupts a scripted run in
the middle of a read-only call and of a write call and resumes both by the rules; the button and the CLI work.

### 7. Learning from runs

**Goal.** What one run learned is available to the next, whichever model runs it.

**Why.** Knowledge that lives in the harness (memory notes, skills) survives a change of model. Memory and skills exist;
nothing turns the hard-won discovery of a run (this site needs that click, this tool wants that flag) into a note.
Unverified lessons can poison later prompts, so nothing is saved without a human by default.

**Design.**
- After a task or research run that had something to learn (a failure followed by a different method that worked, an
  escalation of item 2, a send-back), one judgement call to the review model returns at most three lessons: one
  sentence each, general, about how to do things, never about the user's private data. Secrets are redacted.
- `[agent] lessons = "off" | "propose" | "auto"`, default `propose`: the lessons become `lesson_proposed` events on the
  run; the run page offers "Save to memory" and "Dismiss". `auto` saves them through `save_memory` with the tag `lesson`
  and the run id as source (the similarity check already catches near duplicates).
- Retrieval needs no change: `prompt_memories` already injects the notes that fit the goal.

**Touchpoints.** `agent/loop.py` (after the end of a run), `agent/review.py` (a third judgement), `memory/service.py`,
`server/routes_runs.py`, `views/run.js`, `config.py`, README.

**Done when.** Unit tests of the filtering (what counts as a lesson, secrets, duplicates); an integration test with the
scripted provider; the run page shows proposals and saving one adds a note.

### 8. Undo journal for file edits

**Goal.** Let the agent try, and let the owner take it back.

**Why.** The owner runs `yolo`, so a bad edit is not stopped on the way. A journal keeps the approval mode as it is and
makes the damage reversible. It covers the file tools only; a shell `rm` or `sed -i` is not covered (say so in the docs).

**Design.**
- `tools/files.py` (`write_file`, `edit_file`) records the previous content before it changes a file: a copy under
  `<home>/undo/<run_id>/` and a line in a manifest (step, tool, path, existed, backup name, hash after). Caps per file
  and per run; a file over the cap is recorded as "not backed up".
- `dotx undo RUN_ID` lists what would change; `--apply` restores in reverse order, removes files the run created, and
  skips a file that changed after the run's last write unless `--force`.
- Retention only by explicit command: `dotx prune` reports the journal size; `--apply` removes journals older than
  `[agent] undo_keep_days`.

**Touchpoints.** `tools/files.py` (`_atomic_write`, `_edit_blocking`), a new `undo/` module, `cli.py`, `cli_prune.py`,
`core/paths.py`, `config.py`, README.

**Done when.** Unit tests on a temp workspace (modify, create, delete-by-undo, conflict); `dotx undo` documented.

## Open owner decisions

1. Live evals: `dotx eval run --model REF` spends model quota. Run it for the models in use and keep the JSON; it is the
   basis for flipping the defaults below.
2. Defaults that change how the agent behaves and wait for evidence: `[agent] tool_surface` (item 4), `resume` (item 6),
   `lessons` (item 7), `stall_escalation` (item 2).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model.
4. From the audit: how long to keep events and screenshots (retention windows), which model compacts (`[models] fast` is
   documented but never read), browser access to localhost and private addresses, CI.

## Log

- 2026-10-08: roadmap written from the owner's go-ahead for all eight items, in this order. Nothing implemented yet.
