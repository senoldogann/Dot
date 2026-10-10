# Autonomy roadmap and handoff

The owner asked for Dot's autonomy to be smarter, steadier and sturdier **whatever model runs it**. This file is the
handoff for that work: what is planned, why, in which order, what is done and how it was checked. If you continue the
work, read it first, and keep the status table, the item sections and the log at the bottom true. It is the only record
of where the work stands. Part 1 (items 1 to 8, autonomy) is done; part 2 (items 9 to 20: standing goals, yolo without
questions, faster steps, helper models) was planned on 2026-10-09 and has its own status table below.

## Principle

Do not count on the model being clever. The harness measures, notices, steers and recovers, so a weak model gets more
scaffolding and a strong one needs less. The order follows from that: measure (1), notice (2), adapt (3, 4), check (5),
recover (6), learn (7), undo (8). Every later item is judged with the numbers of item 1.

## Status

| #   | Item                                                        | Status | Commits          |
| --- | ----------------------------------------------------------- | ------ | ---------------- |
| 1   | Eval harness and run statistics (`dotx eval`, `dotx stats`) | done   | a83cdcc          |
| 2   | Progress watchdog and escalation ladder                     | done   | 5f6cb29          |
| 3   | Model-adaptive scaffold (`dotx models profile`)             | done   | de2cc1c          |
| 4   | Smaller, progressive tool surface                           | done   | d89d634          |
| 5   | Verification of the end of a run                            | done   | e2051a5          |
| 6   | Safe resume after a restart                                 | done   | c3688e6, b6dd42f |
| 7   | Learning from runs (lessons)                                | done   | a297764, 4db2539 |
| 8   | Undo journal for file edits                                 | done   | c9bb85d          |

Status values: `planned`, `in progress`, `done` (implemented, tested, verified, committed), `parked` (the reason is in
the item). Take the first item that is not `done`. All eight are done; what waits for the owner is in "Open owner
decisions". Part 2 starts at item 9: take the first item of its table ("Status, part 2") that is not `done`, in the
order its "Order and dependencies" paragraph gives. All of part 2 is done as well (2026-10-10).

## Where things stand before this roadmap

- Baseline `8848d69` (tag `pre-audit-fixes`; the folder had no git before). Audit fixes on top of it, one commit per
  wave: A `9e6eb5a` run integrity (tool calls stored per step, atomic message writes, no double start, a shutdown has
  its
  own result text), C `159c75b` data upkeep (screenshots follow their chats, `dotx prune`, compaction of the database),
  D `a91aa47` Turkish-aware search (one fold), paging of the chat list, delete confirmations focus Cancel.
- Audit leftovers nobody has started, the owner's call: B speed and cost (the model that compacts, the `wait_text`
  view, the 420 s model-call handler, tool schemas; item 4 here overlaps the last one), E hardening of the `ask` and
  `auto` approval modes, F infrastructure (CI, small memory and tool-exception issues, documentation drift).
- The owner runs the daemon in `yolo` on purpose. Do not change, restrict or argue with that.
- The owner's daemon runs the code it loaded when it started. Python changes take effect after a restart (`dotx service
  install`, house rules); the static web files are served from disk.

## How to continue

1. Read this file, then `git log --oneline` and `git status`. A dirty tree means an item was interrupted: read the diff
   and the item's section before touching it.
2. Check the ground before you build on it: `.venv/bin/ruff format --check src tests`, `.venv/bin/ruff check`,
   `.venv/bin/mypy`, `.venv/bin/python -m pytest tests/unit -q` must all be clean.
3. For the item you take: read the code it touches (the "Touchpoints" list is a start, not a limit), refine the design in
   its section if reading proves it wrong, set the status to `in progress`, then implement, test, verify, commit. The
   designs of part 2 were written from a read-only audit, not from building: where the code disagrees with a design,
   the code wins and the section is corrected first.
4. After each commit: set the status and the commit hash in the table, tick what is done in the item, and add a line to
   the log. A reader must be able to resume from this file alone.
5. Do what an item needs yourself, at the owner's word of 2026-10-10: hosted tests with their keys, restarts of their
   daemon, edits of their `config.toml`. Only what the owner alone can do (a key, a check on their own accounts) goes
   into "Open owner decisions"; keep going with the next item meanwhile.

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
  and stable datasets; add the minimum the change needs. Use temp directories (`DOT_HOME`, `DOT_CHROME_DIR`, and
  `DOT_LOG_DIR` for a daemon and for every other `dotx` command, `dotx eval check` included: each one logs to
  `<log dir>/dotx.log`, which otherwise is the owner's `~/Library/Logs/Dot`), so that no test writes into the owner's
  data directory (`~/Library/Application Support/Dot`). Outside tests the agent may read it and edit the owner's
  `config.toml` (the owner's word, 2026-10-10); a key read from its `.env` for a hosted test goes into the test's
  environment and is never printed.
- Safety: the agent restarts the owner's launchd daemon when a change needs it, with `dotx service install` run with no
  `DOT_*` variable set (they would go into the job's plist) and after checking that no run is active. Nothing that
  deletes the owner's data may run by default: retention and
  cleanup are explicit commands. `PLAN.md` (the owner's working notes, untracked on purpose, excluded in
  `.git/info/exclude`) is not committed.
- Git, since 2026-10-09: the repository is `https://github.com/senoldogann/Dot` (`origin`, branch `main`). One branch
  per item (`item-09-time-accounting`), commits with the agent's `Co-Authored-By` trailer, pushed to `origin`; one pull
  request per item against `main`, its description ending with the attribution line the harness gives, carrying the
  item's ROADMAP update (status, section, log). Review the branch with subagents (at most five; check each one's
  evidence) and fix what holds before merging; the Claude GitHub app is not used (decision 15); then
  squash-merge (`gh pr merge --squash --delete-branch`) and write the merge commit's hash into the status table. Never
  force-push, never rewrite `main`, never merge a PR whose checks fail. Linear mirrors the table: project `Dot` in the
  owner's workspace, one issue per item, In Progress when the branch is made, Done when the PR is merged, the PR link in
  the issue. The ROADMAP stays the record.
- New configuration keys go into `src/dotagent/defaults.toml` with a comment, and into the matching model in
  `src/dotagent/config.py` (a key that does not exist is an error). User-visible behavior goes into `README.md`.
- Search with `rg`. Use non-interactive commands. `git --no-pager diff`.

## Items

### 1. Eval harness and run statistics

**Status: done** (`a83cdcc`). **Goal.** A repeatable, model-independent measure of how well Dot finishes tasks, and the
same numbers for the runs that really happened.

**Why.** Nothing in the repo measured whether a task gets finished: the smoke tests prove that features work. Without
numbers every other item is a guess and "works with any model" is a claim. Real data before the audit fixes: 32 runs
(15 done, 14 cancelled, 2 failed, 1 running), about 103 M input tokens over 2,772 steps, so about 37 k input tokens per
step. Why 14 runs were cancelled cannot be told from the old data, because a restart and a user stop wrote the same
text; since wave A they differ.

**What exists now.**
- `src/dotagent/evals/`: `types.py` (`EvalTask`, the `Check` union: file exists / absent / contains / equals, JSON
  equals, command succeeds, answer contains / matches; `ScriptedTurn` for reference solutions), `catalog.py` (25 tasks
  as data, each with a reference solution), `scripted.py` (`ScriptedProvider`, a `ChatProvider` that replays a script),
  `checks.py` (judging a finished workspace), `runner.py` (one attempt = a throw-away `DOT_HOME`, a runtime opened with
  `open_runtime`, `ModelRouter.register` for the scripted model, a supervisor that refuses every approval question and
  cuts the run at `time_limit_s` or `token_budget`), `metrics.py` (`run_metrics` from the events), `results.py`,
  `scoring.py` (`Scorecard`, rendering), `reports.py` (`<home>/evals/<UTC>-<model>.json`), `audit.py` (structure of the
  catalog).
- `dotx eval list | check | run [--model REF] [--task ID]... [--repeat N] | report [FILE...]` (`cli_eval.py`) and
  `dotx stats [--days N]` (`cli_stats.py`, SQL in `store/stats.py`, opened `mode=ro`).
- `store/endings.py`: the result texts a run is closed with (`RESULT_STOPPED`, `RESULT_SHUTDOWN`, `RESULT_STALLED`, ...),
  used by the manager, the loop and the store, and `classify_ending(status, result)`. A later item that adds a new way
  for a run to end adds its text and its `Ending` here, so that `dotx stats` can name it.
- Also changed: `ModelRouter.register(name, provider)`, `runtime.open_runtime(config, login_env)` (`build_runtime` is
  `read_login_env` + it), `config.load_default_config()`, `core.paths.evals_dir()`, `EvalError`, and
  `Store.mark_interrupted_runs` now closes `blocked` runs too (a run waiting for an answer when the daemon died stayed
  `blocked` for ever).
- Tests: `tests/unit/test_evals.py`, `test_endings.py`, `test_run_statistics.py`, `test_store_runs.py`, and
  `tests/smoke/test_evals_runner.py` (the whole catalog through the real loop with the scripted model, the time and
  token
  cuts, the refusal of `ask_user`, the home left untouched). None needs a service.

**How to use it for the next items.** Run `dotx eval check` after any change to a tool, the policy or the loop: it takes
about 4 s. Compare a change with `dotx eval run --repeat 3` before and after, then `dotx eval report`. To add a task:
append it to `_catalog()` in `catalog.py` (seed files, checks, a reference that ends with `finish`) and run
`dotx eval check`; it tells what is wrong. A new kind of check is a new class in `types.py` plus a branch in
`checks.py`.

**Decisions and limits.**
- The attempts run with approvals `ask` and a supervisor that denies every question, with the preset of the user's
  configuration (`aggressive` by default), so a task must be passable inside the workspace without a question.
- The "does nothing and says done" control is judged on the seeded workspace without a run (the result is the same and it
  is fast); the reference is replayed through the real loop.
- No task uses a local HTTP fixture yet: `web.allow_private_hosts` is all or nothing, so serving a page on loopback would
  mean switching the SSRF guard off for the whole attempt. If a research-over-HTTP task is wanted, give `WebClient` an
  allow-list of exact hosts first.
- Check commands run code the model wrote (`python3 main.py`) outside the policy gate of the run. That is no more power
  than the run had (it ran the same file), but it is why the tasks stay small and local.
- A live `dotx eval run` has not been done: it spends the owner's quota. See "Open owner decisions".

**Done when.** `dotx eval check` passes offline; unit tests for the checks and the scoring; an integration test drives the
runner with the scripted provider on a temp home; README documents the commands. All true.

### 2. Progress watchdog and escalation ladder

**Status: done** (`5f6cb29`). **Goal.** Notice a run that is not getting anywhere, in all its disguises, and answer with
steps that can actually help before giving up.

**Why.** The stall guard in `agent/loop.py` fires only when the last `stall_limit` (4) steps have identical calls *and*
results; after three such warnings the run fails. It misses A-B-A-B cycles, a long streak of different failing calls and
the re-reading of results the run already has. The README admits: "A run that varies its steps without getting anywhere
ends only when you stop it." And when it does fire, all it does is repeat one note three times.

**Design.**
- `agent/progress.py`, pure: an observation per acting step (fingerprint, all-failed flag, a digest of the results that
  includes the hashes of images), a window, and four detectors: exact repetition (today's), a cycle of period 2 or 3, a
  failure streak (`failure_streak` consecutive steps in which every call failed), and revisiting (nearly every result
  in the last `revisit_window` steps was already seen earlier in the run). Plan stagnation (open plan, no step closed
  for
  a long time) is a weak signal; add it only if the first four leave a gap.
- A ladder with a cooldown, so one hiccup is not an emergency: (1) a nudge that names the detected pattern; (2) a
  forced re-plan: for one step the model is offered only `update_plan`, `ask_user` and `finish`; (3) a consultation: one
  judgement call to the review model (same mechanism as the goal checker in `agent/review.py`) that gets the goal, the
  plan, the ledger and the pattern and answers with a diagnosis and up to three next steps, injected as a system note;
  (4) the model is made to ask the user one concrete question (`ask_user`), skipped for delegated and scheduled runs;
  (5) stop, with a result that says what was tried, where it stands and how to continue in the same thread. A long
  healthy stretch steps the level down again, so a run that runs for hours is not killed by hiccups that are hours
  apart.
- `stall` events carry `level`, `reason`, `detail`, `action` (keep `warnings` so the UI and `cli_ui.py` keep working;
  update `timeline.js` to show the reason).
- Config in `[agent]`: `stall_escalation` (false restores the old behavior), `failure_streak`, `revisit_window`.

**Touchpoints.** `agent/loop.py` (the stall block around `recent`/`stall_warnings`; `_fingerprint`), `agent/review.py`,
`agent/prompt.py`, `server/static/js/timeline.js:476`, `cli_ui.py:245`, `config.py`, `defaults.toml`, README.

**What exists now.**
- `agent/progress.py`, pure. `observe` turns the calls of one acting step (`CallSeen`: name, arguments, result, failed,
  image hashes) into an `Observation`; `advance(state, calls, limits, ladder)` returns the next `WatchState` and an
  `Alert` or `None`. Detectors: `repeat` (`stall_limit` steps with the same calls and the same results), `cycle` (period
  2 or 3, two whole rounds, more than one distinct step), `failures` (`failure_streak` steps in a row in which every
  call
  failed), `revisiting` (at least `revisit_window - 1` of the last `revisit_window` steps only redo calls already made,
  with the same results). Every alert clears the window, so the next rung needs fresh evidence; 12 steps without an
  alert (`_DECAY_STEPS`) bring the level one rung down.
- `ladder_for(escalation, can_ask)`: with `stall_escalation` nudge, replan, consult, ask, stop (ask only when
  `can_ask_user`: no parent run, not a schedule, delegate or eval source, approvals not `yolo`); without it nudge,
  nudge,
  stop and only `repeat` counts, which is the old stall guard with the decay and a better end.
- `agent/loop.py`: `_calls_seen` feeds the watchdog after every acting step and `_answer_alert` answers the rung. Nudge,
  replan and ask add a system note; replan and ask also limit the next step through `_only` (a registry of the allowed
  tools: a call to any other tool fails as an unknown tool and lists the allowed ones); consult calls `_consult`; stop
  ends the run as failed with `stop_result` (the reason, the newest 8 actions with secrets masked, what it touched, the
  plan, how to go on in the same thread).
- `agent/review.py`: `progress_consultant_for` (forced tool `progress_advice`, session `<thread>:progress`) gives the
  review model the goal, the plan, the ledger and the pattern, and returns a diagnosis and up to three next steps, which
  reach the run as a system note. A `ProviderError` there stops the run and says why.
- `stall` events carry `warnings`, `level`, `action`, `reason` (the pattern), `detail`, and for the consult `diagnosis`
  and `next_steps` or `error`. `cli_ui.py` (`StallPayload`) and `timeline.js` show them; the stop is shown as bad.
- `store/endings.py`: `PREFIX_STALLED`, so the new stop result counts as `stalled` in `dotx stats`.
- Config `[agent]`: `stall_escalation = false`, `failure_streak = 6`, `revisit_window = 8` next to `stall_limit = 4`.
  `EvalSettings.stall_escalation` records the setting in eval reports and the scorecard; `dotx eval run` checks the
  review model too when the ladder is on.
- Tests: `tests/unit/test_progress.py` (detectors, both ladders, decay, the fresh start, the restriction, who can be
  asked, the stop result) and `tests/smoke/test_progress_watchdog.py` (the real loop, with the scripted model playing
  the
  run and the review model: a nudge and recovery, the whole ladder to a stop with a useful result, a healthy 31-step
  run untouched, the plain ladder). None needs a service.

**How to use it for the next items.** To compare the ladder with the plain guard, run `dotx eval run --repeat 3` from a
home whose `config.toml` sets `[agent] stall_escalation = true` and once without; the reports say which was which. The
`stall` events (`kind = 'stall'`, `data.reason`, `data.level`) are the stall numbers item 3's profile wants. Item 5 can
copy `progress_consultant_for`: one forced tool call to the review model with the run's material behind a nonce.

**Decisions and limits.**
- Off by default: it changes how the owner's agent behaves, so it waits for eval evidence (open decision 2). The decay
  and the new stop result apply with it off too.
- A failed consultation stops the run and says why instead of skipping the rung (no silent fallback), like the goal
  check. The review model is `[models] review`, which is the main model unless the owner changes it (open decision 3).
- The ask rung is left out where nobody would answer: yolo (the owner's mode), scheduled, delegated and eval runs. So
  the owner's ladder, once switched on, is nudge, replan, consult, stop.
- Revisiting looks at calls together with their results: reading a file that changed, or rerunning a test between two
  different edits, is work and not a revisit.
- Plan stagnation was not added: the four detectors cover the cases in the tests, and an open plan that does not move
  is weak evidence on its own.
- One `stall` event per alert. Events of the older guard (only `warnings`) still render.
- Found while testing: an eval attempt could run stale bytecode of a file the reference had edited within the second it
  was compiled (`fix-add-bug` failed on `a83cdcc` too); attempts now run with `PYTHONDONTWRITEBYTECODE=1`. Also fixed:
  `timeline.js` threw on every stall event (`warnings.join` on an int) and `cli_ui.py` refused plan steps marked
  `failed`
  or `skipped`.
- No live model run: the ladder was tried only with the scripted model.

**Done when.** Unit tests of the detectors and the ladder on scripted sequences; integration tests on the real loop with
the scripted provider (flailing then recovering is not killed; flailing for good escalates in order and ends with a
useful result; a healthy long run is untouched); the ladder is visible in the UI timeline. All true: the timeline was
checked in headless Chrome on a throw-away daemon (a UI run that climbed all five rungs: four warnings and the stop).

### 3. Model-adaptive scaffold

**Status: done** (`de2cc1c`). **Goal.** Give each model the amount of help it needs, from facts, not from a global
setting.

**Why.** `ModelInfo` knows what a model *offers* (vision, tools, thinking, context length) and nothing about how
*reliably* it works. `context_length` is not used by the loop at all: `[agent] context_tokens = 48000` is global, so a
model with a smaller window overflows before compaction starts. Long runs drift from their goal because the goal sits
at the start of the context.

**Design** (refined after reading the code; the first draft is in git history at `cab742e`).
- The window. `ModelInfo.context_length` is what one request of the model can hold. The Ollama provider reports the
  smaller of the model's window (`/api/show`) and `[providers.ollama] num_ctx`, which it sends with every request and
  which Ollama cuts a longer prompt to (whether Ollama applies `num_ctx` to `:cloud` models is not verified; taking the
  smaller one is the safe side). The run's budget is `min([agent] context_tokens, 3/4 of the window)`; the last quarter
  is
  room for the reply and for the error of the token estimate. Tool output shrinks in the same proportion (never below
  2000 characters): `ToolContext.output_chars` carries it, the tools that page their output (`read_file`, `web_fetch`,
  MCP results, browser and desktop text) size each page to it, and the executor clips the rest. A model whose
  `/api/show` names no window counts as `num_ctx`. When the system prompt and the tool definitions alone take the whole
  budget, the run fails at the start with the numbers and a hint instead of compacting at every step. Measured windows
  (local Ollama `/api/show`): minimax-m2.7 196 608, deepseek-v4.1-flash 1 048 576, gemma4 262 144, gpt-oss:20b
  131 072; with the default `num_ctx = 65536` every budget stays 48 000, so the owner's runs do not change.
- Compaction follows the budget. The summary and the facts the continuity note pins may take a tenth of it each: the
  task, the user's instructions and the plan are always whole; of the actions and the paths touched the newest that fit
  are listed, with the count of the rest; a mechanical summary is the newest part of the transcript that fits. The
  messages kept are the newest that fit next to the fixed part and the note in 85 % of the budget
  (`_COMPACTED_SHARE`), never less than the newest exchange, so the steps after a compaction have room. Before, a
  compaction kept the newest eight messages whatever their size: at an 18 000 budget a run compacted at 31 of 40 steps
  and a prompt reached 18 408 tokens; now it compacts 9 times and the largest prompt is 17 811.
- The profile. `store/stats.py` `read_model_profile`: one read-only SQL aggregate over the model's newest 30 finished
  runs: runs, steps, tool calls, failed calls, calls refused before they ran (`tool_result` with `ok` false and an empty
  `risk`: unknown tool, wrong mode, arguments that were not JSON or did not fit the schema), invalid JSON, empty
  replies, stalls, compactions. Refusals measure the model and not the environment (a missing file or a failing test is
  not the model's fault), so they decide the tier. No-tool replies are not counted: they cannot be told from a final
  answer. Eval reports are not read: an attempt runs in a throw-away home, `RunMetrics` has no refusal count, and
  parsing
  every report at each run start costs more than it tells.
- `agent/scaffold.py`, pure: `scaffold_for(agent_config, info, profile)` returns the `Scaffold` of a run (window, budget,
  tool output, tier, the reason for the tier, the reminder interval). Tiers: `standard` and `weak`. `[agent] tiers`
  sets a model's tier by hand (in evals too); otherwise `[agent] scaffold = "measured"` makes a model with at least
  `profile_min_calls` tool calls and a refused share of at least `weak_refusal_share` weak; fewer calls, or
  `scaffold = "fixed"` (the default, open owner decision 2), mean standard.
- Reminders (weak tier, task and research runs): after every `reminder_every` steps, at an acting step that neither
  finished nor got a progress note, the loop adds one system note with the goal word for word, the user's instructions,
  the plan and the newest results from the ledger. Event `reminder`.
- Event `scaffold` at the start of every run, before the room check, so a refused run shows its numbers too; the
  timeline shows it only when it differs from the configuration (a budget cut to the window, reminders on).
- `dotx models profile [REF]` (REF defaults to `[models] main`) prints the profile and the scaffold with its reasons.
  `dotx models [PROVIDER]` keeps working: the providers became subcommands of `models`, next to `profile`.
- Not done here: `parallel_tools` and `keep_recent_tool_results` stay global (no evidence that they depend on the model),
  and the tool surface is item 4, whose `auto` mode can read the tier and the window from the scaffold.
- `EvalSettings.tier` records the tier forced for the evaluated model (`standard` when none).

**Touchpoints.** `agent/loop.py` (`run_agent`, `_fit_context`, `_compact`, `_model_summary`), `agent/scaffold.py`,
`agent/continuity.py` (the reminder text, the capped pinned facts), `agent/compact.py` (`split_for_summary`),
`agent/executor.py`, `tools/context.py`, the paged tools (`tools/files.py`, `web.py`, `mcp.py`, `browser.py`,
`desktop.py`), `llm/ollama.py`, `llm/types.py`, `store/stats.py`, `store/db.py`, `cli.py`, `cli_profile.py`,
`evals/results.py`, `cli_eval.py`, `evals/scoring.py`, `timeline.js`, `config.py`, `defaults.toml`, README.

**What exists now.**
- `agent/scaffold.py`, pure: `Scaffold` (model, window, budget, tool output, tier, reason, reminder interval),
  `context_budget`, `output_budget`, `choose_tier`, `scaffold_for(config, info, profile)`, `scaffold_event`,
  `reminder_due`, `require_room` (a `ConfigError` with the model, the window, the budget, the fixed tokens and a hint),
  `PROFILE_RUNS = 30`.
- `store/stats.py`: `ModelProfile` and `read_model_profile(database, model, runs)` (SQLite opened `mode=ro`; a missing
  database is a `StoreError`). `Store.model_profile` runs the same read off the event loop at the start of every run.
- `agent/loop.py`: `run_agent` builds the scaffold, emits `scaffold`, refuses a run without room, gives the tools the
  run's `output_chars`, and adds the reminders (`reminder_note` in `agent/continuity.py`; event `reminder` with `step`
  and `open_steps`). `_fit_context` sizes a compaction to the budget (see the design); `_compact` takes the size of the
  note's parts.
- `llm/ollama.py` `parse_model_details`: the window is the smaller of the trained context and `num_ctx`; the Context
  column of the System page shows it.
- `dotx models profile [REF]` (`cli_profile.py`): steps, calls, failed and refused calls with their shares, invalid
  arguments, empty replies, stalls and compactions of the newest 30 finished runs, then the window, the budget, the
  tool output, the tier with its reason and the reminders of the next run. The provider listings are
  `dotx models [ollama|openai|opencode_go]`.
- Config `[agent]`: `scaffold = "fixed"`, `tiers = {}`, `profile_min_calls = 50`, `weak_refusal_share = 0.1`,
  `reminder_every = 6`. `EvalSettings.tier` records a tier forced for the evaluated model.
- `timeline.js`: the `scaffold` event as a note when the budget was cut to the window or reminders are on, and a note
  for each `reminder`.
- Tests: `tests/unit/test_scaffold.py` (budget, tool output, tiers, reminders, the room check, the Ollama window, the
  profile on a temp database and its rendering), `tests/unit/test_continuity.py` (the capped pinned facts, what a
  compaction keeps), `tests/smoke/test_scaffold.py` (the real loop with the scripted model: a 24 k window cuts the
  budget and the pages and compacts every few steps inside the budget where a large window never compacts; weak-tier
  reminders at steps 3 and 6 and none for a standard model; a 4 k window refused before the model is asked). None
  needs a service.

**How to use it for the next items.** The `Scaffold` of a run is where to read what its model needs: item 4's `auto`
tool surface can take the tier and the budget from it. To see what a model would get, run `dotx models profile REF`
(with `DOT_HOME` and `DOT_LOG_DIR` set to temp dirs unless the owner runs it). To try a tier without data, set
`[agent] tiers = { "REF" = "weak" }` in a throw-away home's `config.toml`; to compare, run
`dotx eval run --model REF --repeat 3` with and without it (the reports record the tier).

**Decisions and limits.**
- `scaffold = "fixed"` stays the default: measured tiers change how the owner's agent behaves and wait for evidence
  (open decision 2). The budget cut to the window applies in both modes; with the owner's models and
  `num_ctx = 65536` it changes nothing.
- The tier is measured on refusals only (calls refused before they ran): a call that ran and failed may be the
  environment's fault. A model with fewer than `profile_min_calls` measured calls is standard.
- Whether Ollama applies `num_ctx` to `:cloud` models is not verified; the smaller window is the safe side.
- The room check is literal: a fixed part as large as the budget refuses the run; one just below it runs and compacts
  often.
- Found while testing and fixed: the tools paged their output by the global `tool_output_chars`, and `read_file`'s
  continuation line could push a page over it, so the executor cut the middle out of the page (`_HEADROOM_CHARS =
  200`).
- No live model run: the scaffold was tried with the scripted model only.

**Done when.** Unit tests of `scaffold_for` on stable inputs; the loop uses the scaffold; a small-window model gets a
smaller compaction threshold; `dotx models profile` works on a temp database. All true: also the offline smoke suite
(142 tests) and `dotx eval check` are clean, and the timeline was checked in headless Chrome on a throw-away daemon (a
weak-tier run on a 24 k window: the budget note, the reminder interval, reminders at steps 3, 6 and 9 and a compaction;
a 4 k window: the budget note and the refusal with its numbers), with no console error.

### 4. Smaller, progressive tool surface

**Status: done** (`d89d634`). **Goal.** Offer a model the tools it needs now, and the rest on request.

**Why.** Measured again for this item (`estimate_tokens` on the real registry, task mode, the desktop on, no MCP
servers; the first draft said 8.2 k): 46 tools, 9 816 tokens in every model call. `desktop` 2 923, `browser` 2 543,
`schedule` 610, memory administration 355: two thirds of it belongs to groups most runs never use. The core (meta,
files, web, shell, memory search and save, delegate, skill) is 3 385. MCP servers add their own tools on top. For a
weak model forty tools are forty ways to pick wrongly.

**Design** (refined while building it; the first draft is in git history at `cab742e`).
- Groups by name (`tools/groups.py`): `browser_*`, `desktop_*` (with `apple_script`) and `schedule_*` are their groups,
  `memory_update` and `memory_forget` are `memory_admin`, the tools of an MCP server are `mcp__<server>` (server names
  have no underscore, so the group is what comes between `mcp__` and the next `__`), and every other tool is `core`:
  the files, the shell, the web, `memory_save` and `memory_search`, `delegate`, `skill`, the meta tools and
  `use_tools`. A new tool is core unless its name puts it in a group.
- `[agent] tool_surface`. `full` (the default) offers every tool of the run at every step, as before: `use_tools` is
  left out, the system prompt is the same byte for byte, and no event is written. `grouped` starts with the core tools
  and the groups whose tools the thread's model called before (any call by name, also one that did not run; a
  `use_tools` call does not count). `auto` (`surface_for` in `agent/scaffold.py`) is grouped for a model of the weak
  tier and for one whose window cut the context budget, full otherwise. A run whose tools are all core is not grouped.
- The prompt of a grouped run has a `# Tool groups` section: how groups work, then one line per group of the run with
  what it is for and its tools (16 named at most, then "and N more"). It lists every group, on or off, so the system
  prompt stays the same for the whole run; only the tool list grows.
- A group is turned on by a `use_tools(group)` call that succeeds (the tool checks the name and says what the group
  holds), or by a call of one of its tools while it is off. That call does not run, because the model did not have the
  tool's definition and its arguments are a guess; its result says "`X` did not run: it is in the tool group G, which
  was off. The group is on now: call `X` again", so a guess costs one step. It counts as a failed call (kind `meta`,
  risk `safe`), not as a refusal, so it does not make a model weak (item 3). Groups only grow. Event `tool_groups`
  (`step`, `on`, `off`, `turned_on`, `reason`): at step 0 the groups a run starts with and why, then at each step that
  turned a group on.
- Budget: the room check at the start of a run counts every tool of the run, also the ones that are off, because a
  group turned on later must fit too; `_fit_context` estimates each step with the tools the step offers (before, it
  counted every tool, also on a step that a progress alert limited).
- A step that a progress alert limits (item 2) offers what the alert allows (always core tools) and turns no group on.
- The tools a run can call (`_run_tools`) are the ones it offers, and the ones of other modes, which are answered as
  not available in the run's mode. A tool of the run's mode that the run leaves out is unknown: `use_tools` when the
  tools are not grouped, `delegate` and `ask_user` in a delegated run (before, such a call reached the tool, and the
  manager refused a nested `delegate`), `skill` when no skill is installed.

**Touchpoints.** `tools/groups.py` (new), `tools/meta.py`, `agent/executor.py`, `agent/loop.py`, `agent/scaffold.py`,
`agent/prompt.py`, `config.py`, `defaults.toml`, `evals/results.py`, `cli_eval.py`, `evals/scoring.py`, `timeline.js`,
`icons.js`, README. `tools/registry.py` and `mcp/` did not change: the groups follow the tool names.

**What exists now.**
- `tools/groups.py`, pure: `CORE_GROUP`, `group_of`, `groups_of`, `tools_in`, `group_lines`, `used_groups`,
  `offered_specs`, `hidden_tools`, `groups_event`.
- `tools/meta.py`: `use_tools` (`USE_TOOLS_TOOL`, `UseToolsArgs`: the name is stripped and lower-cased; `core` is
  always on; an unknown group fails and names the run's groups).
- `agent/executor.py`: `execute_calls(calls, registry, hidden, ctx, gate, parallelism)`; `hidden` maps the tools of the
  groups that are off to their group.
- `agent/scaffold.py`: `Surface` and `surface_for(config, scaffold)`, which returns the surface and its reason.
- `agent/prompt.py`: `_groups_section`, only when `use_tools` is among the run's tools.
- `agent/loop.py`: `run_agent` decides the surface, leaves `use_tools` out when the tools are not grouped, keeps the
  groups that are on (`on`), offers and hides the tools of each step, turns groups on (`_turned_on`) and writes the
  `tool_groups` events; `_run_tools`.
- Config `[agent] tool_surface = "full"`. `EvalSettings.tool_surface` (a report without it counts as `full`); the
  scorecard prints it.
- `timeline.js`: a note for `tool_groups` ("Tools offered by group: core on; browser, memory_admin, schedule off until
  the run needs them (reason)", "Turned on the tool group schedule at step 1"); `icons.js`: `use_tools` has the
  `layers` icon.
- Tests: `tests/unit/test_tool_groups.py` (the group of each tool, the core set and its size, offered and hidden tools,
  the prompt section as the only difference between grouped and full, a large MCP group, the groups a thread used,
  `surface_for`, `_turned_on`, the event, the tools a run can call), `tests/smoke/test_tool_groups.py` (the real loop
  with the scripted model: a call of an off tool, its result and the group's tools at the next step with the same
  definitions, `use_tools`, the same system prompt at every step, the events; a weak-tier `auto` thread whose second
  run starts with `schedule` on; `full` offers every tool, writes no event and answers `use_tools` as unknown). None
  needs a service.

**Sizes** (`estimate_tokens`, the desktop on, no MCP servers; the first step is the system prompt and the tools):

| Mode     | Every tool      | Core, with `use_tools` | First step, `full` | First step, `grouped` |
| -------- | --------------- | ---------------------- | ------------------ | --------------------- |
| task     | 46 tools, 9 816 | 19 tools, 3 523        | 13 132             | 7 190 (45 % less)     |
| chat     | 45 tools, 9 517 | 18 tools, 3 224        | 12 853             | 6 911 (46 % less)     |
| research | 32 tools, 5 258 | 15 tools, 2 727        | 8 651              | 6 403 (26 % less)     |

The `# Tool groups` section is 351 tokens of the grouped prompt (283 in research). In research mode the groups are
`browser` (12 tools, 1 547), `desktop` (5, 1 053) and `schedule` (1, 69).

**How to use it for the next items.** To try the grouped surface, set `[agent] tool_surface = "grouped"` (or `"auto"`
with `[agent] tiers = { "REF" = "weak" }`) in a throw-away home's `config.toml`, and compare
`dotx eval run --model REF --repeat 3` with and without it; the reports record `tool_surface` (eval attempts run with
the desktop off, so their groups are `browser`, `memory_admin` and `schedule`). The `tool_groups` events say which
groups a run needed and when. A new tool that most runs do not need belongs in a group: give it a group's prefix or
list it in `_GROUP_OF_TOOL` (a new group also needs its line in `_PURPOSES`).

**Decisions and limits.**
- `full` stays the default (open decision 2): grouping changes what the owner's models see and waits for evals. With
  `full` a run is as before: the same tools in the same order, the same prompt, no event.
- The groups a thread used are read from the tool calls in the messages a run loads for its thread; a group turned on
  with `use_tools` and never used does not carry over to the next run.
- MCP groups are covered by unit tests only: no MCP server runs in the smoke tests.
- `dotx models profile` shows the scaffold, not the surface; a grouped run's surface and its reason are in its first
  `tool_groups` event.
- No live model run: the groups were tried with the scripted model only.

**Done when.** Unit tests of the grouping and enabling; an integration test on the scripted provider (disabled-group
call, error, enabled, next step offers the schema); sizes measured again and noted here. All true: also the offline
smoke suite (145 tests; one model-driven test failed once and passed when rerun, see the log) and `dotx eval check`
are clean, and the timeline was checked in headless Chrome on a throw-away daemon (a grouped run: the start note with
its reason, the failed call, the groups turned on at steps 1 and 3), with no console error.

### 5. Verification of the end of a run

**Status: done** (`e2051a5`). **Goal.** Do not accept "done" on the model's word when the harness can check.

**Why.** Goal mode already asks the review model to compare the end with the goal, and gives it a digest of the steps.
What it lacks is facts the harness measured itself, and without goal mode nothing checks the ending at all. By default
`[models] review` is the same model as `main`, which shares its blind spots.

**Design** (refined while building it; the first draft is in git history at `cab742e`).
- Facts (`RunFacts` in `agent/verify.py`): the loop keeps them from the outcomes of each step's tool calls, never from
  a model. A call that never ran (refused, an unknown tool, arguments that did not fit) and a bookkeeping call (kind
  `meta`: the plan, `finish`, `use_tools`) add nothing. A call's target is its `ActionInfo.target`: the resolved path of
  a file tool, the command of a shell call, the address of a web call.
  - `written`: the files the file tools wrote or edited, each once at its newest place; the newest 30.
  - `failures`: per tool and target, the last failure that no later success of the same tool and target followed, with
    the first line of its result; the newest 10.
  - `checks`: shell commands that test, build or lint (`_CHECK_COMMAND`: pytest, unittest, ruff, mypy, tsc, eslint,
    jest, make, xcodebuild, `npm run test`, `cargo test`, `go vet` and the like, where a command stands, so
    `cat pytest.ini` is not one), with succeeded or failed and the first and last line of the result (the exit status
    and, for most checkers, the tally). A command that runs again replaces its entry; the newest 6.
  - `named`: the paths and addresses that successful calls named (`identifiers()` of `agent/continuity.py` over every
    string of the arguments, plus a file tool's target, whose name may hold spaces); the newest 300.
- Goal mode: `GoalRequest.facts` is a FACTS block between the digest and the final answer, with the four lists above,
  the paths the answer names that do not exist and that no successful call named, and the addresses it names that no
  call opened. An empty section says "none", and secrets are redacted. The system prompt says that the facts come from
  the harness and that, where they and the answer disagree, the facts are right. The facts always go to the goal
  checker; no setting turns them off.
- `[agent] verify_answer_paths` (default `false`): a task or research run that ends with success, and whose answer names
  paths that do not exist and that no successful call of the run named, is sent back once with the list (`paths_note`):
  create what is missing, correct the answer, or finish again with the same answer if it names them on purpose. It
  comes after the plan reminder and before the goal check, makes no model call, and clears the watchdog's window as a
  goal send-back does. Event `answer_paths` (`missing`).
- The paths of an answer (`answer_paths`) are the ones `identifiers()` finds: `~` paths and absolute paths under
  `/Users`, `/tmp`, `/private`, `/var`, `/opt`, `/Applications` and `/Library`. A path that goes on past a space
  ("~/Library/Application Support/Dot") is also read up to four words further; it exists when any reading exists, and
  it is reported by its first reading. A path that a successful call named is never missing (a file the run removed
  or moved); a relative named value is resolved against the workspace (`missing_paths`).
- `dotx doctor` has a `review model` row: a warning, never a failure, while `[models] review` is the main model.

**Touchpoints.** `agent/verify.py` (new), `agent/loop.py`, `agent/review.py`, `doctor.py`, `config.py`, `defaults.toml`,
`evals/results.py`, `cli_eval.py`, `evals/scoring.py`, `cli_ui.py`, `timeline.js`, README. `agent/continuity.py` did not
change (`identifiers()` is used as it is), and the doctor lives in `doctor.py`, not in `cli.py`.

**What exists now.**
- `agent/verify.py`, pure except `missing_paths`, which reads the file system (the loop calls it through
  `asyncio.to_thread`): `CallFact`, `RunFacts`, `NamedPath`, `new_facts`, `with_outcomes`, `answer_paths`,
  `missing_paths`, `unopened_addresses`, `render_facts`, `paths_note`.
- `agent/loop.py`: `run_agent` keeps `facts` (updated after each step's calls, next to the ledger), sends back an answer
  with missing paths (`paths_reminded`, `_missing_in_answer`) and passes the facts to `_check_goal`.
- `agent/review.py`: `GoalRequest.facts`, the FACTS block in `_goal_prompt`, the rule about facts in `_GOAL_SYSTEM`.
- `doctor.py`: `_review_row(models)`.
- Config `[agent] verify_answer_paths = false`. `EvalSettings.verify_answer_paths` (a report without it counts as off);
  the scorecard prints "answer path check on" or "off".
- `cli_ui.py`: `AnswerPathsPayload`, printed as "◎ the answer names paths that do not exist, back to work: …".
  `timeline.js`: a warning note for `answer_paths` ("Dot's answer named 2 paths that do not exist (…) and was asked to
  correct it").
- Tests: `tests/unit/test_verify.py` (the facts over two steps, their limits and order, what counts as a check, the
  readings of an answer's paths, `missing_paths` on a temp directory, the rendered facts, the doctor row) and
  `tests/smoke/test_answer_check.py` (the real loop, with the scripted model as the main and the review model: an answer
  that names a file the run never wrote is sent back once and never twice; nothing happens with the check off or after
  a failed end; the goal check gets the FACTS block with a written file, an unresolved failed read, a failed
  `python -m unittest` with its exit status 5 and "NO TESTS RAN", a missing path and an address no call opened). The
  two live goal-checker tests of `tests/smoke/test_run_modes_live.py` pass facts now.

**How to use it for the next items.** To try the path check, set `[agent] verify_answer_paths = true` in a throw-away
home's `config.toml` and compare `dotx eval run --model REF --repeat 3` with and without it; the reports record the key,
and the `answer_paths` events say which runs were sent back and for which paths. `RunFacts` is where further measured
facts go; they live in the loop for one run, and nothing stores them. Item 7 wants the failures that a later success
resolved (a failure followed by a method that worked), which `with_outcomes` drops from `failures` today: keep them in
`RunFacts` rather than read them again from the messages.

**Decisions and limits.**
- `verify_answer_paths` stays off (open decision 2): it costs a step whenever an answer names a missing path on purpose
  (a report of what is gone), and only evals can say whether it catches more than it costs. The facts of the goal check
  have no setting: goal mode is the owner's choice already, and its checker only reads more.
- Precision over recall: only the roots `identifiers()` knows are checked, so `/etc`, `/usr` and relative paths in an
  answer are never flagged.
- A path that appears in the content a run wrote counts as named, because every string of a call's arguments is
  searched; an answer that names a missing path that the run wrote into a file is not flagged. A failed call's paths
  are not named.
- A check is recognized only where a command stands: `bash -c "pytest"` or a script that runs the tests is not seen.
- No live run of the path check. The FACTS prompt ran live once: the two goal-checker tests of
  `test_run_modes_live.py` with the default review model, `ollama:minimax-m2.7:cloud` (two model calls; both passed).

**Done when.** Unit tests of the facts and of the unverified-path check; an integration test (a run that claims a file it
never wrote is sent back once); doctor shows the review-model note. All true: also the offline smoke suite (148 tests;
one model-driven test failed once and passed when rerun, see the log) and `dotx eval check` are clean, and the timeline
was checked in headless Chrome on a throw-away daemon (a run whose answer named two missing paths: the warning note,
the run done after one send-back), with no console error.

### 6. Safe resume after a restart

**Status: done** (`c3688e6`, `b6dd42f`). **Goal.** A restart or a crash does not have to end a run.

**Why.** A graceful shutdown leaves the run `cancelled` with its own text; a crash is closed by `mark_interrupted_runs` as
`failed` ("daemon stopped while the run was active"). Either way the owner continues by hand, and the thread keeps every
step and the calls that were interrupted (`repair_history` gives them a placeholder result). The risk of automatic
resume is real: a restart is also how the owner clears a stalled run, and a resumed run may repeat a side effect.

**Design** (refined while building it; the first draft is in git history at `cab742e`).
- The calls of the step a run was in: `pending_calls(messages)` in `agent/compact.py` (pure), the calls of the newest
  assistant message that only tool results follow, minus those with a result; none when a user message follows. A run
  stores the results of a step once the whole step is over, so every call of a step that was cut off is pending, also
  the ones that finished.
- How each call ended comes from the run's events after the step's `assistant` event (a call id is unique only within
  its step): `succeeded` or `failed` (a `tool_result`), `cut_off` (a `tool_call` without a result) or `not_started` (no
  `tool_call`). When the newest `assistant` event is not the run's last step, nothing of the step ran: every call is
  `not_started`.
- Each call's policy kind comes from its tool's `prepare`, which classifies a call and runs nothing, with a gate that
  refuses to be asked and no spawner. An unknown tool, `desktop_act` (its check looks at the screen, which can bring an
  application to the front) and a `prepare` that fails give no kind, which counts as a side effect. Repeatable kinds:
  `READ_ONLY_KINDS - {"delegate"}`, since a delegated run may have done anything its mode allows.
- A resume is a new run on the same thread, source `resume`, with the goal (stored as it was), mode, model, effort,
  approval mode and goal mode of the run it resumes. Its first message (`resume_note`) says how that run was cut off,
  its goal, each call of the step with how it ended (a finished call with the start of its result; a call cut off that
  only reads "may run again"; any other "may have changed something: check the state before you run it again"; a call
  that had not started "nothing of it ran"), the plan, "which is your plan now", and to go on without redoing what is
  done. The loop emits `resumed` (`from_run_id`, `cut_off`, `automatic`, and `calls` with `tool`, `end`, `repeatable`)
  before anything that can fail, then the old plan as the run's first `plan` event, so `mark_plan_step`, the plan
  reminder and the plan card carry on. No new run status.
- What may be resumed (`can_resume`): a top-level run that the daemon cut off (ending `daemon_restarted` or
  `daemon_shutdown`, never a run the owner stopped) and that is the newest run of its thread. By hand, whatever its
  source: Continue on the run's page, `dotx resume RUN_ID`, `POST /api/runs/{id}/resume` (201; 409 otherwise). The
  manager checks again under the thread claim, so two requests at once start one run.
- `[agent] resume = "off" | "safe"` (default `off`) and `resume_within_s` (1800). With `safe` the daemon, right after
  `recover()` and before the scheduler and the server start, resumes on its own (`resumes_on_start`) the runs of the web
  UI that may be resumed, ended within the window, and whose calls that were cut off while they ran only read (a
  finished call reports its result and one that had not started did nothing). Runs of `dotx run` (they may still be
  alive in their own process), schedules, evals and delegations wait for the owner, and so do resumed runs: a run that
  brings the daemon down is not resumed again and again.
- Restart-to-clear-a-stalled-run: the owner stops the run first, then restarts; a run the owner stopped is never resumed.
- `mark_interrupted_runs` ends a run that a crashed daemon left active at its newest event (its last sign of life), not
  when the daemon starts again, and stores for it the `run_finished` event that every other end of a run stores; runs
  closed that way before get it too. Without that event the web UI never saw such a run end: its page said "Connection
  lost. Reconnecting…" for good.

**Touchpoints.** `agent/resume.py` (new), `agent/manager.py`, `agent/loop.py`, `agent/compact.py`, `store/db.py`,
`server/daemon.py`, `server/routes_runs.py`, `server/views.py`, `server/__init__.py` (`UI_SOURCE` moved there from
`server/context.py`, so the agent layer names it without an import cycle), `tools/desktop.py` (`DESKTOP_ACT_TOOL`),
`cli.py`, `cli_ui.py`, `config.py`, `defaults.toml`, `history.js`, `timeline.js`, `tools.js`, `views/run.js`, `app.css`,
README. `policy/types.py` did not change.

**What exists now.**
- `agent/resume.py`, pure except `interrupted_calls` (reads the store, classifies the calls): `RESUME_SOURCE`,
  `RESUMED_EVENT`, `REPEATABLE_KINDS`, `InterruptedCall`, `Resumption`, `cut_off_by`, `can_resume`, `repeatable`,
  `resumes_on_start`, `resume_note`, `resumed_event_data`.
- `agent/loop.py`: `RunOpening(text, files, resumed)` is how a run opens (`AgentRun.opening`); with `resumed`,
  `run_agent` starts with the `resumed` and `plan` events. `agent/compact.py`: `pending_calls`.
- `agent/manager.py`: `resumable(run)`, `resume(run_id)` (a `RunConflict` names the run's status, result and parent and
  the thread's newest run), `resume_safe()`; `_launch` takes the `RunOpening` and checks a resume again under the
  thread claim (`_require_newest`).
- `store/db.py`: `runs_finished_since(since)`, and `mark_interrupted_runs` as above.
- Daemon: `resume_safe()` after `recover()` when `resume = "safe"`; the `daemon_started` log line lists `resumed`.
- Server: `RunDetail.resumable`, `POST /api/runs/{id}/resume`. The detail of a run reads whether the run is active
  before it reads the run, so a run that ends in between never shows as neither active nor ended (a smoke test found
  it).
- CLI: `dotx resume RUN_ID [--no-input] [--thinking] [--verbose]` resumes in its own process like `dotx run` and prints
  the result; the terminal shows "↺ resumed by hand: the daemon stopped while run … worked", a line per call
  ("shell: cut off (it may have changed something)") and the plan.
- Web UI: Continue in the run's header while the run is `resumable` and not active; a resumed run has no goal bubble of
  its own; its timeline opens with a note ("Resumed by hand: the daemon stopped while the run before this one worked.
  Its last step: update_plan finished, write_file finished, shell cut off (it may have changed something)", and "Open
  that run"); in the earlier turns of a later run, a resumed run reads "Resumed where the daemon cut the run above off".
  When a run ends, a call of it that never reported back reads "cut off" (an alert and a badge; "Cut off: the run ended
  before this call reported back"), and its step group counts it ("1 cut off"); it used to spin for good.
- Tests: `tests/unit/test_resume.py` (`pending_calls`, which runs may be resumed, which ones the daemon resumes, the
  note with each call's end and the plan, the note of a run cut off between steps); `tests/smoke/test_resume.py` (the
  real loop with the scripted model, and `tests/fixtures/dot_crashing_run.py`, a process killed while its run works:
  a run that a graceful shutdown cut off while it asked the owner goes on by itself with its plan when the daemon starts
  again; a run that a crash cut off in a shell command after a finished `write_file` waits for the owner, is resumed by
  hand and cannot be resumed twice); `test_server.py::test_only_a_run_the_daemon_cut_off_is_offered_to_be_resumed`;
  `test_store_runs.py` (the end and the `run_finished` event of the runs a crashed daemon left).

**How to use it for the next items.** To see a resume on a throw-away home: cut a run off (kill the process that runs
it, as the smoke test kills `tests/fixtures/dot_crashing_run.py`), start the daemon or call `RunManager.recover()`, then
`dotx resume RUN_ID` or Continue. To try the automatic resume, set `[agent] resume = "safe"`; the `daemon_started` line
of the daemon log lists what it resumed. A resumed run is a new run whose `resumed` event names the run it goes on
from: what is kept per run (the facts of item 5, the lessons of item 7, the journal of item 8) is split between the two.

**Decisions and limits.**
- `resume` stays `off` (open decision 2): only the owner's own restarts and crashes can show whether going on by
  itself helps more than it surprises. By hand it is always there.
- In goal mode, the goal check of a resumed run judges from what the resumed run did: its digest starts at the note.
- The conversation shows every call of the step that was cut off as interrupted (`repair_history`'s placeholder), also
  the ones that finished; the note says which finished. `repair_history` did not change.
- A run of `dotx run` works in its own process, but a daemon that starts while it works marks it as cut off all the
  same, and Continue would start a second run beside it. The daemon never resumes such a run on its own and the README
  says not to resume it by hand while `dotx run` still shows it; the thread claim holds in one process only.
- The goal reaches the instructions and the memory query of a resumed run twice (the note repeats it); harmless.
- A plan step that was open when the run was cut off sends the first `finish` back once (the plan reminder), as for
  any run.
- No live run: every check used the scripted model, or a throw-away daemon whose model cannot be reached.

**Done when.** Unit tests of `pending_calls` and the classification; an integration test that interrupts a scripted run
in the middle of a read-only call and of a write call and resumes both by the rules; the button and the CLI work. All
true: the read-only call is `ask_user` (resumed on its own), the call that may change something a shell command after a
finished `write_file` (left to the owner, resumed by hand). Also clean: the unit tests (436), ruff, mypy, the offline
smoke suite (151) and `dotx eval check`. The web UI was checked in headless Chrome on a throw-away daemon after a real
crash (Continue, the note and the plan card of the resumed run, the earlier turns of a later run, the old run no longer
offering Continue; no console error), and `dotx resume` on a throw-away home (the resumed line, the calls and the plan;
a second resume refused with exit status 2).

### 7. Learning from runs

**Status: done** (`a297764`, review fixes `4db2539`). **Goal.** What one run learned is available to the next, whichever
model runs it.

**Why.** Knowledge that lives in the harness (memory notes, skills) survives a change of model. Memory and skills exist;
nothing turned the hard-won discovery of a run (this site needs that click, this tool wants that flag) into a note.
Unverified lessons can poison later prompts, so nothing is saved without a human by default.

**Design** (refined while building it; the first draft is in git history at `cab742e`).
- Which runs (`may_learn`): a top-level task or research run that ended `done` while `[agent] lessons` is not `off`.
  Chats, delegated runs and runs that failed or were stopped are never asked; evals always run with `lessons = "off"`,
  so no note carries over from one attempt to the next.
- What counts as something to learn (`run_signals`, pure): a failed call, whether a later call of the same tool and
  target worked (`RunFacts.resolved`, added to item 5's facts) or not; an alert of the progress watchdog (`stall`); a
  goal check that sent the run back (`continue`, not final); an answer that named paths that did not exist
  (`answer_paths`). A run without a signal is not asked; `plan_reminder` does not count. Each signal is one line of at
  most 300 characters, secrets masked before it is cut.
- When: after the run's end, in a task the manager starts once `_drive` is over, never inside the run, where a steer
  message would be lost, Stop would lose the outcome, the thread would stay claimed and the answer would wait for the
  review call. The loop returns `RunEnd(outcome, lessons)`; `RunManager.learned(run_id)` waits for the task, and
  `shutdown()` cancels it.
- The writer: a third judgement in `review.py` (`lesson_writer_for`, tool `propose_lessons`, fenced like the reviewer
  and the goal checker, session key `<thread>:lessons`). It reads the goal, the run's digest (secrets masked before the
  cut), the signals, the answer and the saved notes that fit the goal, and returns at most three one-sentence lessons.
- The filter (`kept_lessons`, `lesson_problem`, `repeats`, pure): one line of 20 to 300 characters (or `[memory]
  max_chars` when smaller), no secret (`redact_secrets` would change it), no e-mail address, no path in a home folder
  (`/Users/<name>` or `/home/<name>` in any case; `/Users/Shared` is fine), and no repeat of the goal, of another lesson
  or of a saved note (the same folded words, or most search terms shared). Then each lesson is looked up with `recall`
  (5 notes): a note that repeats it, or whose embedding similarity reaches `[memory] similar_threshold`, drops it.
- Events, all in the lifecycle stream (`LESSON_EVENTS`) so the run's page hears them after the run's end:
  `lessons_proposed {lessons, tainted}`, `lesson_saved {index, memory_id, by}` (`owner` or `auto`), `lesson_dismissed
  {index}`, `lessons_failed {error}` (any `DotError` on the way: a review model or an embedding service that fails, a
  missing API key). `RunDetail.lessons` and the terminal derive what became of each lesson from them (`lessons_of`).
- `[agent] lessons = "off" | "propose" | "auto"`, default `off` (the draft said `propose`). `propose` leaves the lessons
  on the run; `auto` saves each as a note tagged `lesson` whose source is the run, except after a tainted run
  (`RunGate.ever_tainted`, new: the run read content third parties can author, which could plant a lesson), whose
  lessons are only proposed.
- A lesson is decided once. Saving and dismissing, by the owner or by `auto`, hold the run manager's lock
  (`RunManager._deciding`) from the look at the lesson to the event; `auto` skips a lesson the owner decided first, and
  a second decision of the owner is a conflict.
- Retrieval did not change: `prompt_memories` injects the notes that fit the goal, saved lessons among them.

**Touchpoints.** `agent/lessons.py` (new), `agent/review.py`, `agent/loop.py`, `agent/manager.py`, `agent/gate.py`,
`agent/verify.py`, `core/errors.py` (the docstring of `RunConflict`), `config.py`, `defaults.toml`, `evals/runner.py`,
`server/routes_runs.py`, `server/views.py`, `server/stream.py`, `cli.py`, `cli_ui.py`, `views/run.js`, `run.css`,
README. `memory/service.py` did not change.

**What exists now.**
- `agent/lessons.py`, pure except `learn`, `run_lessons`, `save_lesson` and `dismiss_lesson`: `LESSON_TAG`, the event
  names and `LESSON_EVENTS`, `LessonCase`, `LessonView`, `RunLessons`, `may_learn`, `run_signals`, `lesson_problem`,
  `repeats`, `kept_lessons`, `lessons_of`, `learn`, `run_lessons`, `save_lesson`, `dismiss_lesson`.
- `agent/review.py`: `LESSONS_TOOL`, `LessonRequest`, `LessonProposal`, `LessonWriter`, `lesson_writer_for`;
  `goal_digest` masks secrets before it cuts, and so does `agent/verify.py` for the facts it keeps.
- `agent/manager.py`: `learning(run_id)`, `learned(run_id)`, `save_lesson(run_id, index)`, `dismiss_lesson(run_id,
  index)`.
- Server: `RunDetail.lessons`; `POST /api/runs/{id}/lessons/{index}/save` and `/dismiss` answer 200 with the run's fresh
  detail (they change a run and create nothing of their own), 409 for a lesson saved or dismissed already, 404 for a
  run or a lesson that does not exist.
- Web UI: "Lessons from this run" on the run's page, hidden while the run works and shown without a reload when lessons
  arrive. Save to memory and Dismiss turn into chips ("Saved to memory", "Dismissed"); the card of a tainted run says
  that the run read untrusted content; an error stays while a lesson is still proposed. One decision per lesson at a
  time; each ends with a refresh of the run, and only the newest refresh paints. The words of both buttons stay at
  phone width (the small-screen rule hides every `.btn .label`).
- CLI: `dotx run` and `dotx resume` print the answer, then "drawing the lessons of this run (Ctrl-C skips it)", then a
  line per lesson ("◆ lesson saved as a note: …" or "◇ lesson proposed: …"), a hint that the run's page saves or
  dismisses a proposed lesson, and the error if any. Ctrl-C skips the wait; the exit status stays the run's.
- Tests: `tests/unit/test_lessons.py` (which runs may learn, the signals, what counts as a lesson, repeats, the filter,
  what became of each lesson, a save error that goes once every lesson is decided); `tests/unit/test_verify.py` (a
  secret masked before a long target or result is cut); `tests/smoke/test_lessons.py` (the real loop with the scripted
  model as main and review model: proposed lessons wait until the owner saves or dismisses each through the API; two
  saves at once save once; `auto` leaves alone a lesson the owner dismissed while it saved; `auto` saves and drops a
  lesson a saved note already says; a run that read a web page only proposes; `off` and a run without a signal ask
  nothing).

**How to use it for the next items.** To see lessons on a throw-away home, set `[agent] lessons = "propose"` and a
review model; a run must end `done` after a failed call. The scripted provider can be main and review model at once
(`tests/smoke/test_lessons.py`): the turn after the run's `finish` answers the lesson writer with a `propose_lessons`
call. A decision on a lesson goes through `RunManager`, never straight to `agent/lessons.py`, or it escapes the lock.

**Decisions and limits.**
- `lessons` stays `off` (open decision 2): `propose` spends one review call per run that qualifies, and only evals can
  show whether saved lessons help more than they mislead.
- The lock of decisions holds within one process. A `dotx run` with `auto` saves in its own process; a dismissal of the
  same lesson on the daemon's page at the same moment is not ordered with it (the page shows that run's lessons only
  after a reload, so the window is practically nil). A save holds the lock while the embedder works, so a stuck
  embedding service makes other decisions wait for its timeout.
- A save cut off between the note and its `lesson_saved` event (the daemon stops just then) leaves the note while the
  lesson still shows as proposed; saving it again keeps one note (the same text is one note).
- A lesson that says what a saved note says in other words is dropped only when `recall` finds that note among its 5
  and the embeddings call them similar (or most search terms match); without embeddings, only the words count.
- When the filter drops every proposed lesson, no event is stored: the run looks like one that was not asked (the
  daemon log has `lessons_drawn` with `kept=0`).
- A review model or an embedding service that fails is covered by unit tests of what the page and the terminal show,
  not by a smoke test.
- No live run: every check used the scripted model.

**Done when.** Unit tests of the filtering (what counts as a lesson, secrets, duplicates); an integration test with the
scripted provider; the run page shows proposals and saving one adds a note. All true; also clean: the unit tests (467),
ruff, mypy, the offline smoke suite (158, no flake) and `dotx eval check`. The web UI was checked in headless Chrome on
a throw-away daemon twice: the card, save and dismiss, the note in memory, a reload, a tainted run, phone width; after
the review fixes, a double click on Save to memory (one request, one note, no error) and a dismissal made through the
API while the page was open (shown without a reload; a later save answers 409); no console error. Ctrl-C while `dotx
run` waited for a lesson writer that never answered: exit status 0.

### 8. Undo journal for file edits

**Status: done** (`c9bb85d`). **Goal.** Let the agent try, and let the owner take it back.

**Why.** The owner runs `yolo`, so a bad edit is not stopped on the way. A journal keeps the approval mode as it is and
makes the damage reversible. It covers the file tools only; a shell `rm` or `sed -i` is not covered, and the README says
so.

**Design** (refined while building it; the first draft is in git history at `4dbba0c`).
- The note (`note_change`): before `write_file` or `edit_file` changes a file, `_atomic_write` appends one line to
  `<home>/undo/<run_id>/journal.jsonl`: `seq`, `ts`, the tool, the path, whether something was there and its permission
  bits, the copy (`backup`; `before` is its SHA-256, `copied` its size) or why none was kept (`skipped`), the folders
  the change had to create (`created_dirs`), and the SHA-256 and size of what it writes (`after`, `size`). Only the
  run's first change to a path keeps a copy (`backups/<seq>`): it holds what was there before the run. Folders are 0700,
  files 0600. When the note fails (a full disk, a file where the journal folder belongs), the file is not changed and
  the tool's error (`UndoError`) says why.
- No step number (the draft had one): `seq` and `ts` order the changes, and the run's steps are in the database. No
  link field: `expand_path` resolves links, so the file tools never write through one; the journal looks at the path
  itself (`lstat`), and what is not a regular file is noted without a copy.
- Caps: `[agent] undo_max_file_bytes` (10 MiB) per file and `undo_max_run_bytes` (100 MiB) per run. A file past one is
  changed without a copy, and its note names the cap. The journal is always on: it changes nothing the agent does (caps
  of 0 keep no copies; a file the run created can still be deleted).
- The plan (`plan_undo`, pure): the changes of the run and of the runs it delegated to, ordered by `ts`, one step per
  path, the path changed last first. The first change to a path tells what was there before, the last one what the runs
  left. States: `ready` (the path holds what the runs left), `conflict` (it changed or was deleted since), `cannot` (no
  copy was kept, or a folder is there now) and `as_before` (nothing to do). Actions: `restore` the copy of the first
  change, or `delete` a file the runs created.
- Carrying it out (`apply_undo`): the `ready` steps, with `--force` the `conflict` ones too, in the plan's order. A
  restore checks the copy against `before` (a forged or damaged copy is refused before anything is written), writes it
  through a temp file and a rename with its permission bits, and creates the folder above it again when it is gone; a
  delete removes the file, or the link that is there now, never its target. Then the folders the runs created are
  removed when they are empty, deepest first. Nothing replaces a folder.
- `dotx undo RUN_ID [--apply] [--force]` refuses a run that is still active, or one of whose delegated runs is
  (`RunConflict`), and a run the database does not have (not found); both exit 2. It exits 0 when nothing was left as
  it is and 1 when a file was (`cannot`, or `conflict` without `--force`), and logs `run_undone {run_id, restored,
  deleted, folders, left}` to `dotx.log`.
- Retention only by explicit command: `dotx prune` reports the journals (how many, how large, and how many have their
  last change, the time of the journal file, older than `[agent] undo_keep_days`, default 30); `--apply` deletes those.

**Touchpoints.** `undo/journal.py`, `undo/restore.py` and `cli_undo.py` (new), `tools/files.py` (`_journal`,
`_atomic_write`, `_edit_blocking`), `cli.py`, `cli_prune.py`, `core/paths.py`, `core/errors.py` (`UndoError`, the
docstring of `RunConflict`), `runtime.py` (`Runtime.undo_dir`, the guarded files), `config.py`, `defaults.toml`, README.

**What exists now.**
- `undo/journal.py`: `JOURNAL_FILE`, `JournalTarget` (where a change is noted and the caps), `JournalEntry`,
  `JournalFolder`, `run_journal`, `backup_path`, `digest`, `read_journal`, `note_change`, `list_journals`,
  `remove_journals`.
- `undo/restore.py`, pure except the reads and writes on disk: `RunChange`, `FileState`, `UndoStep`, `plan_undo`,
  `due_steps`, `forced_steps`, `created_dirs`, `file_state`, `current_states`, `apply_undo`, `remove_empty_dirs`.
- `tools/files.py`: `_journal(ctx, tool)` is the run's `JournalTarget`; `_atomic_write(path, content, journal)` notes
  the change before it writes, and `edit_file` writes through it.
- `cli_undo.py`: `UndoCommand`, `render_plan`, `render_outcome`, `cmd_undo`. `cli_prune.py`: `PruneFindings` counts the
  journals; `dotx prune` reads the owner's config for `undo_keep_days`.
- `core/paths.py`: `undo_dir()` (`<home>/undo`), which is among the guarded files of `runtime.py`.
- Config: `[agent] undo_max_file_bytes`, `undo_max_run_bytes`, `undo_keep_days`.
- Tests: `tests/unit/test_undo.py` (a changed file put back with its permission bits after an edit and a write; a
  created file deleted and the folders made for it removed; a file changed after the run, and one deleted with its
  folder, are conflicts that only `--force` takes back; the caps; a failed note leaves the file as it is; the change of
  a delegated run goes back to the copy its parent kept; a forged copy is not written back); `tests/smoke/test_undo.py`
  (the real loop with the scripted model: a run edits a file, writes one in a new folder and delegates a write;
  `cmd_undo` shows the plan, takes all three back with the folder, logs `run_undone`, and a second time finds nothing to
  do). Unit and smoke files share the basename, so run them in separate pytest invocations (as for `test_lessons` and
  `test_resume`).

**How to use it.**
```
dotx undo RUN_ID                    # what taking back the run's file changes would do; changes nothing
dotx undo RUN_ID --apply            # restore, delete, remove the empty folders; exit 1 when a file is left
dotx undo RUN_ID --apply --force    # also the files changed after the run; what changed in them since is lost
dotx prune [--apply]                # the journals among the rest; --apply deletes those older than undo_keep_days
```
The run id is on the last line of `dotx run` and in the address of the run's page. A new tool that changes files must
note the change with `note_change` (or write through `_atomic_write`) before it makes it, and must not make it when the
note fails. On a throw-away home the scripted provider drives it without a model (`tests/smoke/test_undo.py`; a `dotx
run` whose `ollama` provider is the scripted one works as well).

**Decisions and limits.**
- Only `write_file` and `edit_file` are in the journal. A shell command, an MCP server, the browser or the desktop
  tools change files that `dotx undo` does not know about.
- A copy is kept only at the run's first change to a path and within the caps; a file without one cannot be restored,
  only deleted when the run created it.
- The changes are ordered by when they were noted. A run's file tools never run in parallel, so each journal has one
  writer; delegated runs can work at the same time (several `delegate` calls in one turn), and two of them writing one
  file at the same moment can leave the change noted first in it. The plan then calls the file a conflict: nothing is
  put back silently.
- No button in the web UI: `dotx undo` is the only way.
- The run must be in the database. The journals of a run whose chat was deleted stay until `dotx prune --apply` finds
  them old; nothing deletes them by default.
- A run that is still active is refused. A run of `dotx run` that a crash left active stays so until the daemon starts
  again and ends it (item 6).
- The journals are guarded like the configuration and the database: outside `yolo` the agent can neither read nor
  change them. A `yolo` run can (the refusal of `secret_access` is waived there); the check of a copy against its
  digest catches a copy changed alone, not a journal rewritten along with it.
- No live run: every check used the scripted model.

**Done when.** Unit tests on a temp workspace (modify, create, delete-by-undo, conflict); `dotx undo` documented. All
true; also clean: the unit tests (475), ruff, mypy, `dotx eval check` and the offline smoke suite (159; one
model-driven test, `test_a_live_stream_carries_the_runs_the_run_delegated_to`, failed once as in item 2 and passed when
rerun). The CLI on a throw-away home, a scripted `dotx run` that edited a file, wrote one in a new folder and shortened
a file past a cap of 16 bytes: the dry run listed `cannot`, `delete`, `restore` and the folder; `--apply` took back two
files and the folder, left the third and exited 1; a second `--apply` left only that file; an unknown run exited 2 with
`error: run not found`; `dotx prune` showed the journals.

## Part 2: standing goals, yolo without questions, faster steps, helpers (planned 2026-10-09)

The owner's second ask, after the eight items (the verbatim Turkish is in `PLAN.md`, part 8): browser and computer
use must be faster; a goal such as "earn money with the information I gave you" or "go to X.com and do what it takes
to grow my audience" must be worked on **until the outcome is there**, not until the run feels done; a `yolo` run must
never ask what it can do itself (a run asked the owner to type a one-time code that had arrived in the owner's own
webmail); a run on one provider should be able to ask a model of another provider for help, and a missing key or an
exhausted quota must not break anything; and the agent should be able to do any job on this Mac, write code in a
browser and research. The owner also asked whether TypeSafe's typed judgments (System One, used in their OSJarvis
project) belong in Dot.

**Principle for part 2.** The same as part 1: the harness measures, notices, steers and recovers. What is new is the
unit of work: a *standing goal* outlives a run and is verified by evidence, not by the model's word. Nothing here
widens what a run may do; `yolo` already waives every policy question. What changes is that the harness stops
pretending to ask when nobody will answer, stops ending runs that are not finished, and does not wait on the model for
what it can do itself. Every behavior change is measured with item 9's numbers or with the eval harness, and defaults
that change the owner's agent are their call (see "Open owner decisions", 6 onward).

### Status, part 2

| #   | Item                                                                   | Status      | Commits |
| --- | ---------------------------------------------------------------------- | ----------- | ------- |
| 9   | Time accounting: where the seconds of a run go                         | done        | 4741bed |
| 10  | Yolo asks nobody: secret fields, questions and the end of a run        | done        | eacd180 |
| 11  | Open-ended goals in one run: targets, limits and an honest end         | done        | 12ad395 |
| 12  | Campaigns: a goal that works until its outcome is verified             | done        | c915ab2 |
| 13  | Faster browser and desktop steps                                       | done        | a14f21a |
| 14  | Rich editors, drag and bulk text in the browser                        | done        | 37808d5 |
| 15  | Helper models: consult, delegate to another model, standby             | done        | 530ac62 |
| 16  | Typed judgments (TypeSafe) at the decision points                      | done        | a5516f7 |
| 17  | Pacing and public actions on social platforms                          | done        | d50a41e |
| 18  | Skills written for Claude Code, and the X playbook                     | done        | 6464881 |
| 19  | Prompt-cache hygiene for the hosted providers                          | done        | 0526a60 |
| 20  | An interactive terminal for programs that prompt                       | done        | cdf7ec7 |
| 21  | A plain chat screen: Dot's face, task chips, one composer line         | done        | bb89714 |

**Order and dependencies.** 9 first: it is cheap and every later item is judged with it. 10, 11 and 12 are the owner's
main ask and build on each other (12 needs the left questions of 10 and the targets of 11). 13 and 14 are the speed
ask and depend on nothing. 15 before 16: both handle a provider that is not there. 17, 18, 19 and 20 are independent;
18 is an afternoon. The owner may reorder; write the new order here when they do. On 2026-10-10 the owner asked for
a plain chat screen like the reference they sent; it is item 21, done before 16 to 20.

**What the audit of 2026-10-09 measured and found** (four read-only subagent audits, re-read at the cited lines; the
owner's data was not read, the numbers of 2026-10-08 are the audit's aggregate SQL):
- One streamed model call per step (`agent/loop.py:869-871`); the tools of a turn run after the whole reply, browser
  and desktop tools one at a time (`tools/browser.py:303`, `tools/desktop.py:372`, `agent/executor.py:194-220`). The
  fixed part of every call is about 13.3 k estimated tokens with the `full` surface (3 321 system prompt, 9 954 tool
  schemas); average input per step in the real history was about 37 k (103.1 M over 2 772 steps). Compaction took
  12.7 % of run wall time, median 10.7 s per compaction, with the run's own model (`agent/loop.py:299`; `[models] fast`
  is read nowhere).
- Model-call duration is recorded nowhere; `tool_result.seconds` includes the approval wait (`agent/executor.py:100`,
  `127-166`); the usage of review, goal-check, compaction and vision calls is discarded (`agent/review.py:383-400`,
  `agent/loop.py:266`); `cached_tokens` is parsed and never stored. `browser_wait_text` failed 22.4 % of the time and
  returns a whole page view when it succeeds (`tools/browser.py:400-424`).
- A browser click costs about 160 ms of fixed waiting (40 ms grace per side-effect step, `browser/session.py:820-822`;
  120 ms DOM-quiet window, `browser/driver.py:46-53`) plus Playwright round trips and one full aria snapshot, also when
  nothing changed. A desktop click costs about 0.56 s fixed (two `lsappinfo` listings of about 38 ms each,
  `desktop/session.py:227` and `:547`; 110 ms click pacing, `desktop/input.py:175-177`; 350 ms settle,
  `desktop/session.py:636-637`) plus an unconditional closing accessibility walk (`desktop/session.py:638`, capped at
  `ax_budget_s` 4 s).
- A text reply ends a task run when `nudges >= finish_nudges`, and `nudges` is reset only by a send-back
  (`agent/loop.py:936-946`, `954`, `962`, `973`): a status line written mid-run counts toward the end. `ask_user` goes
  through `approvals.request` in every mode and waits `approval_timeout_s` (900 s) in `yolo` and in scheduled runs
  too (`tools/meta.py:84-106`, `agent/approvals.py:45-66`). The browser refuses password and one-time-code fields at the
  tool level in every mode (`browser/driver.py:280-286`, `348`, `351`), and the prompt says codes are never typed
  (`agent/prompt.py:186`), while `yolo` waives the credential hard stop (`agent/gate.py:101-102`).
- Nothing outlives a run: the prompt forbids saving progress (`agent/prompt.py:166-167`), a schedule the agent creates
  takes no options (`tools/schedule.py:99-101`, `UNSET_OPTIONS`) and starts a new thread per firing
  (`scheduler/runner.py:105-107`), and a delegated run always uses the parent's model (`agent/manager.py:150-162`).
  "Repost" and "Quote" match no pattern (`policy/elements.py:28-29`), and no cap or spacing exists for public actions.
- The owner's Claude Code skills `x-twitter-growth` and `baoyu-post-to-x` are refused by Dot's loader: their
  `metadata` holds a date and a nested map, and `SkillFrontmatter.metadata` is `dict[str, str]` (`skills/types.py`;
  reproduced with `parse_skill` on 2026-10-09; `find-skills` loads).
- The browser has no `drag` step and no handling of `contenteditable` editors: `type` is `press_sequentially`, `fill`
  is `locator.fill` (`browser/driver.py:349-352`). On X's composer the first character of `type` is lost and `fill`
  reports done without a change (`PLAN.md`, follow-up of 2026-10-07).

### 9. Time accounting: where the seconds of a run go

**Status: done** (`4741bed`, PR #1). **Goal.** Every model call and every tool call of a run leaves its duration and
its tokens in the store, and `dotx stats` and `dotx eval` show where the time goes, per model and per tool.

**Why.** Items 13, 14 and 19 promise speed; without this item their effect is a guess. Today the store has
`tool_result.seconds` (with the approval wait inside), the timestamps of events, and the run's token totals; the model
call itself, the compaction, the review calls and the cache reads leave nothing (see the audit list above).

**Design** (corrected from the code before building; the plan put `purpose` on `ChatRequest` and resolved the run
from the session key, but the review questions' keys are `<thread>:review`, `<thread>:goal`, `<thread>:progress` and
`<thread>:lessons`, and the lessons and a side question are asked after the run ended, when the thread may have a newer
run; every call site knows its run, so it says which).
- `ModelRouter.complete(ref, request, sink, origin)` (`llm/router.py:141`) takes `CallOrigin(run_id, purpose)`
  (`llm/calls.py`), with `CallPurpose` the literal of `step`, `compaction`, `review`, `goal_check`, `progress`,
  `lessons`, `vision`, `attachment` and `aside` (item 15 adds `consult`); every call site names both (`agent/loop.py`,
  `agent/review.py` through the four question factories, `agent/vision.py`, `agent/attachments.py`, `agent/aside.py`).
  `ChatRequest`, the providers and their wire tests do not change.
- The router measures the call and hands a `ModelCall(run_id, purpose, model, session_key, seconds, input_tokens,
  output_tokens, cached_tokens, error)` to the recorder it is built with (`ModelRouter(config, recorder)`). The
  runtime's recorder adds the call's tokens to its run's totals and writes a `model_call` event for that run; a call
  without a run is logged, not stored (the routers of `dotx doctor`, `dotx models` and `dotx models profile` make no
  call). A failed call, whatever its error, is recorded with `error` set to the error type and `seconds` to the time
  spent; a call cut off (a stop, or the caller's timeout, which reaches the router as a cancellation) with `error =
  "cancelled"`. A recorder that fails while it records a failure is logged, so the caller still gets the call's own
  error or cancellation; after a reply, its error is raised like that of any write of the run.
- The `assistant` event (`agent/loop.py:894-909`) gets `seconds` and `cached_tokens` too, so the timeline can show them
  per step without joining events.
- `tool_result` gets `waited_s`: the time the call spent at the gate, measured around `gate.authorize` and around the
  questions the running tool asks through `_PausedClock` (one `_GateTime` per call, `agent/executor.py`); it includes
  the gate's own checks, a fraction of a millisecond. `seconds` stays the whole call, as today.
- `store/stats.py`: `ModelStats.time` is a `TimeStats(timed_runs, model_seconds, step_seconds_median,
  step_seconds_p90, compaction_seconds, side_call_seconds, cached_tokens)` (a side call is every purpose but `step`, the
  compactions included); `ToolStats(tool, calls, failed, seconds_total, seconds_median, waited_s)` per tool name, over
  the runs of every model; `collect_statistics` returns `RunStatistics(models, tools)`. Both are read from the events
  of the runs in the window, read-only, no text. `dotx stats` prints a "Time" table per model and a "Tools" table (the
  15 tools with the most seconds, by name when tied). A step's seconds come from its `model_call` event (`purpose =
  "step"`), and its median and 90th percentile from the steps that got a reply; runs older than this item have none and
  are left out of the time columns (`timed_runs` of the runs is printed, and a model none of whose runs has one gets
  no row).
- `dotx eval`: `RunMetrics` (`evals/results.py:22-40`, `evals/metrics.py:33-48`) gets `model_seconds` and
  `tool_seconds`; the scorecard (`evals/scoring.py:99-131`, printed at `:157-185`) prints seconds per pass next to
  steps and tokens, and the comparison table (`:186-196`) gets a seconds column. The replay of `dotx eval check`
  reports zero model seconds, as the scripted model makes no call.
- The eval runner stops counting from the live bus: `_supervise` (`evals/runner.py`) reads the tokens of the run and
  of the runs it delegated to from the store at every `model_call` event (the recorder writes them before the event
  goes out), and, when `Subscription.dropped` grew, the end of the run too.
- Side-call tokens join the run's totals: the loop stores only the step count (`set_run_steps`), and the recorder adds
  the tokens of every call of the run (`add_run_tokens`), the lessons drawn after its end included, so `dotx stats` and
  the Details panel stop under-counting. The eval token budget compares the same totals, read from the store.

**Touchpoints.** `llm/calls.py` (new), `llm/router.py`, `runtime.py` (the recorder), `cli.py`, `cli_profile.py` and
`doctor.py` (their routers log), `agent/loop.py`, `agent/executor.py`, `agent/review.py`, `agent/manager.py`,
`agent/vision.py`, `agent/attachments.py`, `agent/aside.py`, `agent/lessons.py`, `store/stats.py`, `store/db.py` (a
`model_call` kind needs no schema change: events are JSON), `cli_stats.py`, `evals/results.py`, `evals/metrics.py`,
`evals/scoring.py`, `evals/runner.py`, `timeline.js` (model time on a group of calls, side-call tokens), README
"Measuring the agent" and the run page.

**What exists now.**
- `llm/calls.py`: `CallPurpose`, `CALL_EVENT` (`model_call`), `CANCELLED`, `CallOrigin`, `ModelCall`, `CallRecorder`,
  `call_event_data` (the event's data: purpose, model, seconds, tokens, cached tokens, error) and `log_model_call` (the
  recorder of the CLI routers).
- `llm/router.py`: `ModelRouter(config, recorder)`; `complete` measures from before the connector is made to the reply,
  so a missing key is a failed call too, and records before it returns or raises (`_answered`, `_failed`,
  `_record_failure`).
- `runtime.py`: `_call_recorder(store, bus)` adds the call's tokens to its run (`Store.add_run_tokens`) and emits the
  `model_call` event; a call without a run goes to the log.
- `store/db.py`: `set_run_steps` and `add_run_tokens` replace `update_run_progress`.
- `agent/loop.py`: the step's `assistant` event carries `seconds` (the model call, to its reply) and `cached_tokens`;
  `agent/executor.py`: `tool_result.waited_s`, the seconds at the gate (`_GateTime`), which `seconds` includes.
- `store/stats.py`: `CallSample`, `ToolSample`, `TimeStats`, `ToolStats`, `RunStatistics`, `nearest_rank`,
  `time_stats`, `tool_stats`; `cli_stats.py`: `render_time`, `render_tools`.
- `evals/metrics.py` (`model_seconds`, `tool_seconds` from the events), `evals/scoring.py` (`seconds_per_pass`, split
  into model and tool seconds; the comparison's `seconds/pass` column), `evals/runner.py` (`_supervise` reads the
  tokens from the store at every `model_call` event and after dropped events).
- `timeline.js`: a group of tool calls says "Worked for 4.8 s (model 3.6 s)", the bracket holding the model turns
  spent within the group's time (`addModelTime`: the open group, or the group a turn without text starts); the Details
  panel adds the tokens of the side calls (`model_call` events whose purpose is not `step`) to those of the steps, and
  `views/run.js` reads the run again after a side question.
- Tests: `tests/unit/test_run_statistics.py` (the time and tools tables on hand-written events: medians, p90, a step
  cut off left out of them, a run without `model_call` events left out, the gate's seconds, ties by name);
  `tests/unit/test_model_calls.py` (the router with a stand-in connector: a reply, an error of any type, a recorder
  that fails while a failure or a timeout is recorded, and after a reply); `tests/smoke/test_scaffold.py`
  (`test_every_model_call_of_a_run_leaves_its_seconds_and_tokens_and_the_totals_hold_them_all`: the real loop with the
  scripted model and a small window, one `model_call` event per step and per compaction, in order, and the run's
  totals equal to their sum).

**How to use it.**
```
dotx stats [--days N]    # per model: the Time table; then the Tools table (the 15 tools with the most seconds)
dotx eval run ...        # the score adds seconds per pass, split into model calls and tool calls
```
On the run page the title of a group of tool calls holds the model's part of its time, and the Details panel's tokens
count every call of the run. A new call site of `ModelRouter.complete` names its run and purpose (`CallOrigin`); a call
outside any run passes `run_id=None` and is only logged.

**Baseline for items 13, 14 and 19** (2026-10-09, `dotx eval check`'s 25 reference solutions replayed through the real
loop with the scripted model, temp home, three passes): 0.61 to 0.84 s for all 25 (the first pass is the cold one),
median 0.01 s a task; the slowest are `shell-script-hello` 0.18 s (0.17 s in its shell calls), `fix-add-bug` 0.06 s,
`rename-function` and `implement-slugify` 0.04 s; every other task 0.03 s or less (the cold pass's slowest: 0.39 s).
Model seconds are 0.000 throughout, as the scripted model answers at once, so these are the harness's own costs. The
catalog has no browser or desktop task: the timings of browser and desktop steps that items 13 and 14 promise to cut
come from the fixture timing test of item 13. A scripted model of 1.2 s a call (throw-away daemon) gave the expected
split on the run page ("Worked for 4.8 s (model 3.6 s)") and in `dotx stats` (7 calls: 8.4 s of model time, step
median 1.2 s, side calls 1.2 s for the goal check).

**Decisions and limits.**
- The run's token totals hold the side calls now (compactions, reviews, the goal check, the watchdog's second opinion,
  lessons, image descriptions, side questions), so a run shows more tokens than it would have before this item, and
  `dotx stats`' "a step" average divides them by the steps.
- The tool calls of runs made before this item have no `waited_s`: their approval wait counts as tool time.
- A side call counts under the model of its run in `dotx stats`, whichever model answered it (the event names the
  answering model). A call's seconds include the retries its connector made and the waits between them, so a rate
  limit shows as a slow call. `ask_user`'s seconds are the wait for the answer, and `delegate`'s hold the delegated
  run, whose own tool calls are listed too.
- An eval report saved before this item counted the steps' tokens only and has no seconds.
- A call that fails before the connector exists (a missing key, `MissingSecretError`) is recorded as a failed call too;
  a call that a stop or a caller's timeout cuts off is `cancelled`, with the seconds it ran and no tokens (none were
  reported).
- The scripted replay of `dotx eval check` reports model seconds of about zero; only a run with a real model measures
  the model.

**Done when.** Unit tests of the aggregation on a hand-written event list (`tests/unit/test_run_statistics.py`:
medians, p90, tools table, runs without `model_call` events left out); a smoke test with the scripted provider that
checks one `model_call` event per step and one per compaction; `dotx stats` on a throw-away home prints the tables;
`dotx eval check` clean; the numbers of one scripted eval run (seconds per task) written here as the baseline for
items 13, 14 and 19. All true; also clean: ruff format and check, mypy, the unit tests (483), `dotx eval check` (25
tasks) and the offline smoke suite (179, with the tests that call a cloud model through Ollama left out but one: the
attachment test of the web UI had no guard and called `gpt-oss:20b-cloud` in each run, found in item 11); the run page
checked on a scripted throw-away daemon at desktop width and at 375 px.

### 10. Yolo asks nobody: secret fields, questions and the end of a run

**Status: done** (`eacd180`, PR #2; follow-ups `92b7dfa`, PR #3). **Goal.** A `yolo` run types the secret fields it can
fill from what it reads itself, and a run that nobody watches (`yolo`, scheduled, eval) does not stop for its own
questions: a question for the owner is left with the run, the run goes on, and the owner's answer later carries the work
on.

**Why.** On 2026-10-09 a `yolo` run applying for a job stopped at an e-mail verification code that had arrived in the
owner's webmail, which the run could open, and asked the owner to type the six digits. Three things made it ask (the
lines are those of `4741bed`, before this item): the browser refused to type into password, one-time-code and card
fields in every mode (`browser/driver.py:280-286`, called for `fill` and `type` at `:348` and `:351`); the prompt said
one-time codes are never typed, "use ask_user" (`agent/prompt.py:186`); and `ask_user` blocked for up to 900 s in `yolo`
as in `ask` (`tools/meta.py:82-108`, `agent/approvals.py:45-77`). The owner's decision of 2026-10-07 makes `yolo` waive
the hand-offs of money and credentials (`agent/gate.py:101-102`, `policy/engine.py:26`); the tools and the prompt did
not follow, and the prompt's `yolo` line and the comment of `[policy] approvals` still said that secrets, payments and
sign-in details go to the user (`agent/prompt.py:63-67`, `defaults.toml:159-161`). Separately, a task run ended on its
third text-only reply even when tool calls came between them: the count (`nudges`, `agent/loop.py:814`) was reset only
by the plan reminder, the paths reminder and the goal check's send-back (`:968`, `:976`, `:987`), never by a step with
tool calls (`:911-949`), so a status line written mid-run counted toward the end of a run that was not finished
(`:951-960`).

**Design** (corrected from the code before building; the first draft moved the browser's check in front of the batch,
kept left questions `pending`, fed answers to the thread's instructions and continued through the resume path. The code
does not allow the last three: the instructions are the goals of the thread's runs, `agent/manager.py:501-512`; the
resume path takes only a run the daemon cut off, `:359-361`; the daemon expires every pending approval when it starts,
`:105`).
- **The run's approval mode reaches the tools.** `ToolContext` (`tools/context.py:42`) gets `approvals: ApprovalMode`
  and `questions: QuestionMode`, both required and set where a context is built (`agent/loop.py:813-825`,
  `agent/resume.py:242`, five smoke tests).
- **Secret fields follow the approval mode.** The checks stay where they were, at the moment of the step, so a field is
  judged as it is when the text goes in, and they judge the element that takes the text, whatever element the step
  names. The browser's are in `run_step` (`browser/driver.py:352-398`), through `_check_secret_field` (`:296`): `fill`
  reads the element Playwright fills, which follows a label to its control (`_FIELD_MARKING_SCRIPT`, `:60`); `type`,
  and a `press` of a key that puts text in (`key_enters_text`, `browser/safety.py:157`: a character, or a paste with
  Control or Meta), focus their target first and read the element that then has the focus, through open shadow roots
  and frames (`_focused_marking`, `browser/driver.py:304`); a `press` without a target reads the focused element. The
  marking is a password input, or an `autocomplete` token of a password, a one-time code or a card (`secret_kind`,
  `browser/safety.py:143`; a card token wins). A refused step fails and the steps after it are skipped; so does a step
  the gate refuses, with a note not to retry it, and a step handed off to the owner counts as done by them
  (`browser/session.py:806-822`). The desktop's are in the actions that set a value, type, paste or press a key that
  puts text in (`_check_secure`, `desktop/actions.py:438`, called at `:507`, `:525`, `:534` and `:546`; `enters_text`,
  `desktop/safety.py:176`, counts Command-V), and a value set into a secure field is not read back
  (`desktop/actions.py:513`). What happens at a secret field is the caller's: `BrowserSession.act` takes a
  `SecretFieldCheck`, called with the field (`SecretField`: its ref, none for a press at the focus, the kind of secret
  and the page's address) and raising to refuse; `DesktopSession.act` a `SecureFieldCheck` that rides in the step
  environment (`StepEnv.secure_field`; the actions run in a worker thread). `browser_act` and `desktop_act` pass the
  refusal of before in `ask` and `auto`, with the same text. In `yolo` the browser's check asks the gate with a
  `credential` action (`financial` for a card field) unless the batch was authorized with that flag already
  (`browser_act` is a reviewed tool now, so it gets the action it was authorized with); `yolo` waives it and records it
  (`agent/gate.py:101-105`, `_waive` at `:124`), so every secret field a `yolo` run types is in the timeline, whatever
  its label. The desktop's step review already flags typing into a secure field as `credential`
  (`desktop/safety.py:210-211`, `:225`; `tools/desktop.py:353`), so its `yolo` check only logs. Password managers and
  Keychain Access are never controlled, in any mode (decision 7): the desktop refuses such an app when a batch picks it
  (`_refuse_secrets`, `desktop/session.py:132`, at `:551`), before each step's review (`:578`) and in the step itself
  (`refuse_secrets`, `desktop/actions.py:331`, at `:597`). Screenshots do not change: the desktop blacks out
  password-manager windows (`desktop/shots.py:44`), not secure fields, and the browser masks nothing.
- **The owner's hand-off rules hold in yolo.** `yolo` waives a hard stop and a refusal of the review, which the policy
  decides before it matches the owner's rules; a hand-off rule of the owner that matches the action still stops the run
  there (`owner_handoff`, `policy/engine.py:104`, asked at `agent/gate.py:102-104`), at secrets, money and credentials
  too.
- **The prompt says what yolo may do.** The secrets line of the trust rules follows the approval mode
  (`agent/prompt.py:187-195`, chosen at `:330`): in `ask` and `auto` as before; in `yolo`: "Passwords, one-time codes
  and payment details: enter them when you can read them yourself (a code in a mailbox you can open, a value the user
  gave you). Never guess one, never read a secret store the task does not need, and say in the answer which ones you
  entered." The `yolo` approvals line no longer says that secrets, payments and sign-in details go to the user
  (`:64-67`); the `ask_user` bullet says that the system takes care of an approval instead of "asks the user itself"
  (`:106-108`); the browser lines send a log-in and a log-in box to the rule on passwords instead of to the user
  (`:136-141`); the comment of `[policy] approvals` says what `yolo` lets through (`defaults.toml:165-169`).
- **Questions that do not block.** `[agent] questions = "leave" | "wait"` (default `leave`, the owner's ask of
  2026-10-09; `defaults.toml:107-110`) decides what `ask_user` does where `can_ask_user` is false (`question_mode`,
  `agent/progress.py:127`): in a `yolo`, scheduled or eval run (a delegated run has no `ask_user`,
  `agent/loop.py:136-137`). With `leave`, `ApprovalBroker.leave(run_id, tool, summary, severity, args)`
  (`agent/approvals.py:119-129`) stores the question with the new status `left` and emits `question_left`, a new kind,
  so that nothing that reacts to `approval_requested` (the run page's waiting state, the tab title, notifications, the
  CLI prompt, the eval supervisor) takes it for a question that holds the run. `ask_user` returns at once
  (`tools/meta.py:100-102`): the question is left for the user, who does not answer during the run; go on with what does
  not depend on the answer, and if nothing can be done without it, end the run and say exactly what is needed. The run's
  status stays `running`. The prompt says the same in a line of its own (`agent/prompt.py:214-218`, added at `:281-282`
  only when `ask_user` is among the run's tools); a scheduled run whose questions are left loses "Do not ask questions"
  (`:204-212`, `:277`). `wait`, and every `ask` or `auto` run the owner started, behave as before.
- **The answer carries the work on.** A `left` question is decided like any other (`POST /api/approvals/{id}/decision`,
  through `RunManager.decide`, `agent/manager.py:193-211`; `ApprovalBroker.resolve`, `agent/approvals.py:131`, accepts
  `left` as well as `pending` and emits `approval_decided` for it); an empty answer is refused (422). It is never
  expired (the daemon's start expires `pending` only, `store/db.py:979-986`) and `dotx prune` does not touch approvals.
  Skip denies it and nothing else happens. An answer goes to the thread as the owner's message, "Answer to your question
  “…”: …" (`_carry_on`, `agent/manager.py:317-354`): into the thread's active run, which reads it before its next step,
  also when it comes during the step that ends the run (`agent/loop.py:1010-1015`); or, when no run is active on the
  thread, as a new run on it whose goal is the goal of the run that asked with the answer under it, so its goal check,
  the task its continuity note pins and its title keep that task, with the mode, model, effort, approvals and goal mode
  of the run that asked. Once a run's end is being recorded it reads nothing more: the manager shuts its inbox
  (`agent/manager.py:574`), so a message sent then is refused like one to a run that has ended (409, `steer`,
  `:183-191`), and an answer waits for the end and starts the next run; an answer the run never read, because it was
  stopped, failed or cut off first, stays in the thread as the owner's message for the next run (`_keep_answers`,
  `:588`). Either way the answer is among what the reviewer reads as the owner's words (`agent/loop.py:865-872`,
  `agent/manager.py:501-512`). While a run of another process (`dotx run`) works on the thread, the answer is refused
  (409, `RunConflict`) and the question stays `left`: only that process can steer its run. The answer is delivered
  before the question is decided, under one lock, so an answer that cannot be delivered leaves the question open and a
  question is answered once. The run page lists the run's open left questions above the composer ("Dot asked you", from
  the new `questions` of `GET /api/runs/{id}`, which the index `approvals_run` serves, `store/db.py:74`), during the run
  and after it; once the last open one is answered and the answer started a run, the page follows that run, and a
  question answered elsewhere (another tab, `dotx`, the API) leaves the list through the run's `approval_decided`. The
  timeline shows each question as a line where it was asked. `dotx run` prints a left question when it is left and lists
  the open ones after the answer. An eval run counts left questions among its `unanswered_questions`
  (`evals/metrics.py:15`, `:49`).
- **The end of a run counts only consecutive text replies.** A step with tool calls sets `nudges` back to 0, unless all
  its calls left questions (`_acts`, `agent/loop.py:220-223`, at `:935-936`): `finish_nudges + 1` text-only replies in
  a row end a task run (`:977-984`), and a run that only leaves questions between them still ends. `finish` stays the
  normal end; `FinishArgs` (`tools/meta.py:47-64`) gets `needs_from_user` (optional, left out when nothing is needed):
  what the run could not do without the owner. It travels on the run's outcome (`RunOutcome.needs`) into the
  `run_finished` event, with no new column, so a finish that the goal check sends back leaves nothing behind: the run
  page shows it under the answer as "What Dot needs from you", `dotx run` prints it after the answer, and a campaign
  (item 12) reads it from there.

**Touchpoints.** `tools/context.py` and the places that build a context, `policy/types.py`, `policy/engine.py`,
`agent/gate.py`, `browser/types.py`, `browser/safety.py`, `browser/driver.py`, `browser/session.py`, `tools/browser.py`,
`desktop/safety.py`, `desktop/actions.py`, `desktop/session.py`, `tools/desktop.py`, `agent/prompt.py`,
`agent/progress.py`, `tools/meta.py`, `agent/approvals.py`, `agent/attachments.py`, `agent/manager.py`, `agent/loop.py`,
`agent/resume.py`, `store/models.py`, `store/db.py`, `server/views.py`, `server/routes_runs.py`, `cli.py`, `cli_ui.py`,
`evals/metrics.py`, `evals/runner.py`, `config.py`, `defaults.toml`, `approvals.js`, `timeline.js`, `views/run.js`,
`views/rules.js`, `app.css`, README "How a run works", "Safety" and "Autonomy".

**What exists now.**
- `policy/types.py`: `QuestionMode` (`leave`, `wait`); `config.py` and `defaults.toml`: `[agent] questions` (default
  `leave`). `policy/engine.py`: `owner_handoff`, which `agent/gate.py` asks before it waives an action in `yolo`.
- `store/models.py`: the approval status `left`; `store/db.py`: `create_left_approval`, `list_left_questions`,
  `decide_approval` for a `pending` or `left` record, and the index `approvals_run`.
- `agent/approvals.py`: `QUESTION_LEFT_EVENT` (`question_left`) and `ApprovalBroker.leave`; `resolve` takes a `left`
  question and sends its `approval_decided`. `agent/progress.py`: `question_mode`. `tools/context.py`:
  `ToolContext.approvals` and `ToolContext.questions`.
- `tools/meta.py`: `ask_user` leaves the question when the run's `questions` is `leave` and returns at once;
  `FinishArgs.needs_from_user`. `agent/loop.py`: the run's question mode from `can_ask_user` and `[agent] questions`,
  given to the prompt and the tools; `_acts`; `RunOutcome.needs`; a run about to end reads first what was sent during
  its last step. `agent/prompt.py`: `_SECRETS_REFUSED` or `_SECRETS_ENTERED` by approval mode, `_UNATTENDED_LEAVING`,
  `_LEFT_QUESTIONS`, and browser lines that send a log-in to the rule on passwords.
- `browser/types.py`: `SecretKind`, `SecretField` (its `ref` is `None` for a press at the focus), `SecretFieldCheck`;
  `browser/safety.py`: `secret_kind`, `secret_field_label`, `secret_field_flag`, `refuse_secret_field`,
  `key_enters_text`; `browser/driver.py`: `_FIELD_MARKING_SCRIPT`, `_FOCUSED_SCRIPT`, `_focused_marking` and
  `_check_secret_field`; `browser/session.py`: a step the gate refuses or hands off ends the batch; `tools/browser.py`:
  `browser_act` as a reviewed tool with `_secret_field_check` (the refusal in `ask` and `auto`, the gate in `yolo`).
- `desktop/actions.py`: `SecureFieldCheck`, `refuse_secure_field`, `enter_secure_field`, `StepEnv.secure_field` and
  `refuse_secrets`; `desktop/safety.py`: `enters_text` (in place of `types_a_character`); `desktop/session.py` refuses a
  password manager when a batch picks it and before each step's review; `tools/desktop.py` picks the check by the run's
  approval mode.
- `agent/attachments.py`: `UserInput.question_id`. `agent/manager.py`: `RunManager.decide`, `_carry_on` and
  `_keep_answers`; a run's inbox shuts when its end is being recorded. `server/routes_runs.py`: the decision route goes
  through `decide`, and the run's detail has `questions` (`RunDetail.questions`, `server/views.py`).
- `cli.py`: `follow_run` stops the run on SIGHUP and SIGTERM as on Ctrl-C. `cli_ui.py`: the `question_left` line and
  `show_open_ends` (what Dot needs from you, from `run_finished`, and the open questions), printed by `dotx run` and
  `dotx resume` after the answer (`cli.py`). `evals/metrics.py`: `question_left` counts as an unanswered question.
- Web UI: `approvals.js` (a left question's card, "Dot asked you"; an empty answer is not sent; a card leaves out the
  parts it does not have instead of printing "null"), `timeline.js` (the line "Dot asked you “…” and went on without
  the answer" and the "What Dot needs from you" box under the answer, from `run_finished`), `views/run.js` (the run's
  open questions above the composer; the page follows the run that the answer to the last open one started; a question
  answered elsewhere leaves the list), `views/rules.js` (what a `yolo` run goes past), `app.css`.
- Tests: `tests/smoke/test_left_questions.py` (9), `tests/smoke/test_secret_fields.py` (6, a real headless Chrome),
  `tests/smoke/test_approval_modes.py` (a hand-off rule stops a `yolo` run), `tests/smoke/test_cli_run_end.py` (2, a
  real signal to a `dotx run` of the scripted model, `tests/fixtures/dot_cli_run.py`),
  `tests/smoke/test_evals_runner.py` (an eval run's question is left or declined, and counted),
  `tests/unit/test_secret_fields.py` (23), `tests/unit/test_desktop_secrets.py` (7), `tests/unit/test_run_modes.py` (2
  more); the asking test of `tests/smoke/test_resume.py` sets `questions = "wait"`, as a `yolo` run now leaves its
  question.

**How to use it.**
```
[agent]
questions = "leave"   # default: a yolo, scheduled or eval run leaves its questions and goes on; "wait" waits
[policy]
approvals = "yolo"    # secret fields are typed and recorded (credential, financial); ask and auto refuse them
```
A left question shows on its run's page under "Dot asked you", during the run and after it; answer it there (or `POST
/api/approvals/{id}/decision` with `approve` and the answer in `note`, which may not be empty). The answer goes into the
run that works on the thread, or starts a run on the thread with the asking run's settings, and the page follows that
run once no other question of the run is open; Skip only closes the question. `dotx run` prints a left question when it
is left and, after the answer, what Dot needs from you and the questions still open. While a `dotx run` works on the
thread, the answer is refused until it has ended.

**Decisions and limits.**
- Nothing here lets `ask` or `auto` do more: their secret fields are refused as before, and their questions wait.
- A left question is still a question: a run that cannot go on without the answer ends honestly (`success = false`, the
  need in `needs_from_user`), and the answer starts the next run on the thread.
- In `yolo`, the text a run types into a secret field is in its `tool_call` event like any argument; it is what the run
  read in a mailbox or got from the owner, which the store holds already.
- A left question shows only on its run's page and in `dotx run`: it holds nothing, so no notification, tab title or
  approvals list counts it (`question_left` is not a lifecycle kind, `server/stream.py:22-24`).
- An answer delivered to a working run clears its untrusted mark, as every message of the owner does
  (`agent/loop.py:871`).
- A `dotx run` stops its run when its terminal closes (SIGHUP) or it gets SIGTERM, as on Ctrl-C (`follow_run`,
  `cli.py:582`), so the run's end is recorded; one that is killed outright (SIGKILL) or crashes leaves its run `running`
  until the daemon starts again, and until then an answer to a question on its thread is refused (409), as while such a
  run works.
- Messages from the composer that a run did not read before it ended are dropped, as before; only answers are kept.
- The one-time code of the 2026-10-09 run would now be read from the webmail tab and typed, with a `credential` yolo
  approval in the timeline. That flow is not run live here: it is the owner's check (decision 12).
- The desktop's `yolo` path (a secure field typed and logged) and its refusal of password managers are not run by these
  tests: the desktop smoke tests move the real pointer and run only with `DOT_DESKTOP_TESTS=1`.
- Fixed on the way: the README said that desktop screenshots black out secure fields; they black out password-manager
  windows only. The approval card printed "null" for a part it did not have, such as a question without options or a
  decision without a note (older than this item).
- Found, not changed (all older than this item): a paste through a desktop app's Edit menu is not checked against
  secure fields, only Command-V is; `desktop_open_app` opens a password manager before it refuses to go on with it
  (`desktop/session.py:525-526`); the composer's follow-up sends the run's reasoning level, which `start` refuses for a
  model that offers fewer than two (`views/run.js:380`, `agent/manager.py:513-520`); a follow-up message from the web
  UI on a thread that a `dotx run` of another process works on starts a second run there (`_claim_thread` sees only the
  daemon's own runs, `agent/manager.py:425-430`).

**Done when.** Smoke tests with the scripted model (`tests/smoke/test_left_questions.py`): a `yolo` run of the web UI
and a scheduled `ask` run leave their question at once, the record `left`, the run never `blocked`, and the answer after
the end starts one run on the thread whose goal is the asking run's goal with the answer under it, with the asking run's
settings; an empty answer is refused; an answer while the run works is steered into it, and one sent during the run's
last step is read before it ends; an answer that a stopped run did not read stays in its thread; an `ask` run waits as
before; a task run that writes text between tool steps is not ended by it, and one that only leaves questions between
text replies still ends; an answer is refused while a run of another process works on the thread. Browser smoke on
fixture pages (`tests/smoke/test_secret_fields.py`): a one-time-code field whose label names nothing secret is refused
in `ask` and filled in `yolo`, the form sent with the code, with the `credential` approval recorded; in `ask` the code
is refused whatever element the step names (a fill of its label, a type or a press at a heading after focusing the
field, a press without a target). A hand-off rule of the owner stops a `yolo` run
(`tests/smoke/test_approval_modes.py`); an eval run's question is left or declined and counted
(`tests/smoke/test_evals_runner.py`); a `dotx run` told to end by SIGHUP or SIGTERM records its run as cancelled
(`tests/smoke/test_cli_run_end.py`; without the handlers both leave the run `running`). Unit tests: the prompt of `yolo`
differs from that of `ask` only in the secrets line and the approvals line, and the left-questions line comes only with
left questions (`tests/unit/test_run_modes.py`); a field's marking, kind and flag, and the keys that put text in
(`tests/unit/test_secret_fields.py`, `tests/unit/test_desktop_secrets.py`). The tests of the answer during the last
step, of the answer a stopped run did not read, of the run that only leaves questions, of the hand-off rule and the four
`ask` cases each fail without their fix. All true; also clean: ruff format and check, mypy, the unit tests (515), `dotx
eval check` (25 tasks) and the offline smoke suite (198, with the tests that call a cloud model through Ollama left out
but one: the attachment test of the web UI had no guard and called `gpt-oss:20b-cloud` in each run, found in item 11);
the run page checked on a scripted throw-away daemon at desktop width and at 375 px (the left questions, the needs box,
an empty answer kept back, a skip and an answer through the API that leave the page where it is, the last answer steered
into the run the previous one started and the page following it, no "null" in a card).

### 11. Open-ended goals in one run: targets, limits and an honest end

**Status: done** (`12ad395`, PR #4). **Goal.** A run given an outcome instead of a task ("grow", "earn", "find a way",
"as many as possible") turns it into measurable targets, works toward them, and ends only with the targets reached or
with an honest account of what is missing; the owner can bound such a run in minutes or tokens when they want to.

**Why.** The prompt was written for precise tasks (the lines are those of `92b7dfa`, before this item): "Add nothing
that was not asked for", "take the smallest reasonable reading" (`agent/prompt.py:82-84`). Nothing told the model what
"done" means for an open goal, so a run could end after a few clicks and say so; the goal check (off by default) could
only judge against the goal text. The loop had no run-level limit of wall time or tokens: what bounded a run was the
stop button, the watchdog, `goal_patience` and the concurrency limits (`README.md:968-971`); only the eval harness cut a
run (`evals/runner.py:188-218`). The owner wants runs that keep going, and a bound they can set when they do not.

**Design** (corrected on 2026-10-09 against the code before building; the first draft named a `stopped` status that
does not exist, a result that would have been filed as a watchdog stop, a "core tools" set that is not the re-plan set,
and wrong lines).
- **Wording.** The open-goal rules are a section `# Open-ended goals` after `_ENDING_TOOL` (`_OPEN_GOALS`,
  `agent/prompt.py:115-124`, joined by `_ending`, `:334-337`), so task and research runs get them and a chat, which has
  no `finish` (`tools/meta.py:34-35`), does not. A precise request is done exactly as asked; a goal that names an
  outcome instead of a task (grow, improve, earn, reach, as many as possible, find a way) asks for the outcome: the run
  chooses the approach, records with `update_plan` before it acts the targets it will reach and how its own tools will
  measure them, and prefers the channel that brings the first measurable result soonest. After each round it measures,
  writes the numbers down and changes the approach when a round brought nothing, which is a reason to change the
  approach, not to finish. It calls `finish` only once the targets are met, when the next step needs something only the
  user can give (said in `needs_from_user`), or when the system says that the run's limit is reached; short of the
  targets, with `success` false, the numbers reached and what is missing. In `_WORK` (`agent/prompt.py:83-84`) "When a
  request can be read in several ways" became "When a precise request can be read in several ways".
- **Targets in the plan.** `PlanPayload` (`agent/plan.py:30-35`) has `targets: str` with the default `""`, so plan
  events stored before it still read (`FrozenModel` refuses unknown fields and has no other defaults);
  `plan_event_data` writes the steps and the targets and `plan_from_event` reads both (`:42-54`). `update_plan` takes
  `targets` (`UpdatePlanArgs`, `tools/meta.py:186-198`; at most 600 characters, and like `needs_from_user` it may be
  left out): left out or empty, the targets of the latest plan stay, and new targets replace them (`:216`). An empty
  text does not remove them: a provider sees `targets` as an optional text (the schema drops the null branch,
  `tools/schema.py:35-41`), and a model that fills an unused one with "" would wipe them. `mark_plan_step` keeps them
  (`:247-249`). `render_plan` stays the step lines (pinned by `tests/unit/test_continuity.py:167`); `plan_lines`
  (`agent/plan.py:84-87`) adds a `Targets:` line under them, and every text the model reads a plan in uses it: the tool
  result (`tools/meta.py:208`), the continuity note and the reminder (`_task_sections`, `agent/continuity.py:190-202`),
  the resume note (`agent/resume.py:154-155`), a side question's record (`agent/aside.py:65`) and the watchdog's stop
  and second opinion (`agent/loop.py:788`). The live plan of the web UI shows the targets as its first line
  (`planTargets`, `views/run.js:44-45`, painted at `:133-138` from `timeline.js:501-502`), and `dotx run` prints them
  under the plan (`_on_plan`, `cli_ui.py:400-404`). A resumed run inherits them with the plan (`agent/manager.py:368`,
  `agent/loop.py:846-850`).
- **The goal check reads the targets.** `GoalRequest` (`agent/review.py:183-193`) has `targets`, read from the run's
  latest plan when the checker is asked (`_ask_checker`, `agent/loop.py:608-656`, at `:628`); `_goal_prompt`
  (`agent/review.py:319-332`) adds a fenced `TARGETS` section after the goal when they are set. `_GOAL_SYSTEM`
  (`agent/review.py:87-118`) names the targets among the data whose words have no authority (`:105-107`) and has one
  more rule (`:113-115`): targets are the agent's own measurable reading of a goal that names an outcome, not added
  requirements; the checker judges whether they are met from the digest, the facts and the numbers of the answer, and
  whether they fit the goal: targets that ask for less than the goal's words do not make the goal reached. The verdicts
  keep their meaning (`_after_verdict`, `agent/loop.py:562-581`): targets met is `achieved`, not met while the run can
  go on is `continue`, an honest failure after the sensible ways is `blocked`.
- **Limits, opt-in.** `[agent] run_max_minutes` (a float, so a test can use a fraction; neither infinite nor NaN) and
  `run_max_tokens`, both `Field(ge=0)` (`config.py:92-93`) and 0, meaning none, by default (`defaults.toml:86-92`):
  the names of open decision 6. They bound task and research runs (`limits_of`, `agent/limits.py:42-47`); a chat turn
  ends with a reply and has no `finish` to end with. At the top of every step, after the user's messages and the
  memories are read and before the offered tools are chosen (`agent/loop.py:986-993`), the loop compares the seconds
  since `run_agent` started (a monotonic clock, `:840`, so waits for the owner count) and the tokens of the run and of
  the runs it delegated to (`Store.family_tokens`, `store/db.py:790-796`, one query over `input_tokens +
  output_tokens`; the router's recorder adds every call before the call returns, `runtime.py:115-126`) with the limits
  (`limit_reached`, `agent/limits.py:50-57`: a limit is reached once all of it is spent). The eval supervisor's
  spending is the same query (`evals/runner.py:182-185`). At the first step past a limit the run stores a note
  (`limit_note`, `agent/limits.py:71-79`: what it spent; finish now with the numbers reached, what is missing and how
  to go on; the plan tools before `finish`, as the executor skips every call after it) and emits `limit_reached
  {seconds, tokens, max_minutes, max_tokens}`; from then on the step offers only `finish`, `update_plan` and
  `mark_plan_step` (`LIMIT_TOOLS`, `agent/limits.py:22`; a call of any other tool is answered as unknown, `_only`,
  `agent/loop.py:473-476`), and the run ends with that step's reply. A valid `finish` ends it as the finish says
  (`:1067-1068`). Anything else (text, an empty reply, other calls) ends it at once `failed` (`_limit_end`,
  `:832-836`, returned at `:1069-1070` and `:1092-1093`) with a result that starts with `PREFIX_LIMIT`
  (`store/endings.py:19`), then the text of the reply when it had one, then goes on like the watchdog's stop: what it
  did last, what it touched, its plan with the targets and how to go on (`limit_result`, `agent/limits.py:82-88`,
  through `stop_account`, `agent/progress.py:331-346`, the body `stop_result` had). There is no new run status
  (`store/models.py:9`): the ending `limit` is told from the prefix (`store/endings.py:61-62`), before the goal-check
  mark. A finish past the limit is settled in one branch before anything that sends a run back
  (`agent/loop.py:1107-1113`): no plan reminder and no paths check; in goal mode the checker judges it once and its
  verdict is final
  (`_check_goal_at_limit`, `:689-716`; `_after_limit_verdict`, `:584-600`: `achieved` is done, `blocked` and
  `continue` are failed with the check's note, "the goal is not reached, and the run's limit is reached" for
  `continue`), and its `goal_check` event has `limit: true` so the timeline and `dotx run` do not say "after N rounds".
  What the user sends during that step comes too late for the run, as for one that has ended: the manager keeps an
  answer to a left question in the thread for the next run and the run page marks any other message as not read
  (`_keep_answers`, `agent/manager.py:589-601`). The limit wins over a re-plan or a question of the watchdog in the
  step before: both set the one `allowed` (`:1081`), and the limit sets it after them (`:991-993`).
- **Delegated, scheduled and eval runs.** Every task and research run is bounded by the configuration's limits, a
  delegated or scheduled one too. A delegated run's tokens count toward its parent, as the eval counts them (a
  delegated run cannot delegate again, `agent/loop.py:156`, so its children are all there is); the parent sees them
  when the `delegate` call returns, so one step can overshoot by what a child spent (a child is bounded by its own
  limits). The limits are those of one `run_agent` call: a resumed run, or one that an answer to a left question
  starts, begins with a new clock and a new count. `eval_config` (`evals/runner.py:53-67`) sets both to 0: an eval run
  is cut by its task's `time_limit_s` and `token_budget`, so the owner's limits do not change a measurement.

**Touchpoints.** `agent/prompt.py`, `agent/plan.py`, `tools/meta.py`, `agent/continuity.py`, `agent/resume.py`,
`agent/manager.py` (`_resume`), `agent/aside.py`, `agent/review.py`, `agent/progress.py` (`stop_account`), new
`agent/limits.py` (pure: the decision, the note, the result, the event), `agent/loop.py`, `store/endings.py`,
`store/db.py` (`family_tokens`), `evals/runner.py`, `evals/catalog.py`, `config.py`, `defaults.toml`, `cli_ui.py`,
`views/run.js`, `timeline.js`, `app.css`, `run.css`, README ("How a run works", "Autonomy", "Status and known
limits").

**What exists now.**
- `agent/plan.py`: `PlanPayload.targets`, `EMPTY_PLAN` and `plan_lines`; `plan_event_data` and `plan_from_event` carry
  the targets. `tools/meta.py`: `update_plan` takes `targets` (kept when left out or empty, replaced by new ones, at
  most 600 characters) and `mark_plan_step` keeps them; both answer with the plan and its targets.
- `agent/continuity.py`, `agent/resume.py`, `agent/aside.py`, `agent/manager.py` and `agent/loop.py` hold the plan as a
  `PlanPayload` and give it to the model through `plan_lines`: the continuity note and the reminder, the resume note, a
  side question's record, the watchdog's stop and its second opinion.
- `agent/review.py`: `GoalRequest.targets`, the `TARGETS` section of the goal prompt, and the targets among the data
  and the targets rule of `_GOAL_SYSTEM`. `agent/prompt.py`: `_OPEN_GOALS` after `_ENDING_TOOL` in task and research
  runs; "a precise request" in `_WORK`.
- `agent/limits.py` (new, pure): `RunLimits`, `LimitReached`, `limits_of`, `limit_reached`, `limit_spent`,
  `limit_note`, `limit_result`, `limit_event_data`, `LIMIT_EVENT` (`limit_reached`) and `LIMIT_TOOLS`.
  `agent/progress.py`: `stop_account`, shared by the watchdog's stop and the end at a limit. `store/endings.py`:
  `PREFIX_LIMIT` and the ending `limit` ("stopped at its limit of minutes or tokens"). `store/db.py`:
  `Store.family_tokens`. `agent/loop.py`: the run's clock, the check at the top of each step, `_limit_seen`,
  `_limit_end`, the branch that settles a finish past the limit, and the goal check split into `_ask_checker`,
  `_check_goal` and `_check_goal_at_limit` (with `_after_limit_verdict`).
- `config.py` and `defaults.toml`: `[agent] run_max_minutes` and `run_max_tokens` (0). `evals/runner.py`:
  `eval_config` sets both to 0, and the supervisor's spending is `family_tokens`. `evals/catalog.py`: the task
  `collect-contacts`.
- `cli_ui.py`: the targets under the plan (`◎ …`), the line `⏱ limit reached (…): told to finish now`, and "goal
  check: not reached at the limit".
- Web UI: `views/run.js` (the targets as the first line of the plan card, with a target icon), `timeline.js` (the line
  "The run reached its limit (…) and was told to finish now", and "Goal check: not reached, and the run is at its
  limit, so it stops"), `app.css` (`.plan-targets`), `run.css` (the head of a group of steps wraps on a phone).
- Tests: `tests/unit/test_limits.py` (5), `tests/smoke/test_run_limits.py` (6, the scripted model in the real loop and
  as the goal checker), `tests/unit/test_continuity.py` (targets: the round trip, an event stored without them,
  `plan_lines`), `tests/unit/test_run_modes.py` (the open-goal section by mode, a negative, infinite or NaN limit
  refused), `tests/unit/test_endings.py` (the ending `limit`); the resume tests carry targets
  (`tests/unit/test_resume.py`, `tests/smoke/test_resume.py`).

**How to use it.**
```
[agent]
run_max_minutes = 0   # minutes a task or research run may work; 0: no limit
run_max_tokens = 0    # tokens the run and the runs it delegated to may use; 0: no limit
```
A goal that names an outcome ("Collect as many leads as you can into leads.csv") is enough: the run records its targets
with its plan, and the plan card of the run page shows them on its first line (`dotx run` prints them under the plan).
Past a limit the timeline says so and the run's answer says what was reached and what is missing; a run that does not
finish then ends "Failed" with "Stopped: the run reached its limit (…)" and how to go on.

**Decisions and limits.**
- Both limits stay 0 by default: the owner asked for runs that do not stop, and open decision 6 keeps 0 for them.
- A limit is checked between steps, so a step that is running when it passes goes on: a model call can take
  `model_call_timeout_s` (420 s), a `delegate` call waits for its run. Only the eval cuts a run in the middle of a step.
- In goal mode a finish past the limit is checked once, without a way back: the limit says not to go on, and goal mode
  says not to take "done" on the model's word. A check costs one call of the review model past the limit.
- The minutes are wall time, so waits for the owner's approvals and answers count: a run in `ask` that waited long for
  an approval may be told at its next step to finish. The open-ended runs this item is for are unattended (`yolo`,
  scheduled), where nothing waits for the owner.
- The limits are per run: a resume after a daemon restart, or a run that an answer starts, begins again from 0, and a
  delegated run has the full limits of its own. Item 12 keeps these limits per session of a campaign and bounds the
  campaign by its count of sessions (`campaign_max_sessions`).
- The run page shows a limit end as "Failed", as it shows a watchdog stop; `dotx stats` counts the ending `limit`.
- The wording cannot be measured offline; `dotx eval` has one open-ended task (`collect-contacts`, category
  `multi_step`, mode `task`): collect as many contacts as the meeting notes give, checked per contact in the
  `contacts.csv` the run writes, so a run that stops after the first note fails it. How real models do on it is not
  measured here: no eval of this item called a cloud model.
- Fixed on the way (older than this item): on a phone the head of a group of steps did not wrap, so its count and the
  badge of failed calls went out of view (`.steps-head`, `run.css:70-83`).

**Done when.** Unit tests of the targets (event round trip, an old event without them, `plan_lines`), of the limit
decision (pure: seconds, tokens, both, none), of the ending `limit` and the result of a limit end (with and without a
last reply), of the configuration (a negative, endless or NaN limit refused) and of the prompt (the open-goal section in
task and research, not in chat). Smoke with the scripted model (`tests/smoke/test_run_limits.py`): `update_plan` targets
survive a new plan without them, an empty `targets` and `mark_plan_step`, and reach the goal checker's prompt as a
`TARGETS` section with the data rule in its system prompt; a run past `run_max_tokens` gets the note, has a call of
another tool refused and ends `limit` with its last reply when it does not finish; a text reply past the limit ends the
run the same way; a finish past the limit is not sent back by the plan reminder, by a path of the answer that does not
exist or by a message the user sent during the step, and goal mode judges it once (a `continue` ends the run failed with
the check's note). The resume smoke test carries targets; the eval task added and `dotx eval check` clean; README
updated. All true. Taking out the end at the limit after a finish (three smoke tests fail), after a step with calls
(one) or after a text reply (one), or the cut of the tools past the limit (one), makes a smoke test fail (checked one at
a time). Also clean: ruff format and check, mypy, the unit tests (524), `dotx eval check` (26 tasks, none passed by
doing nothing) and the offline smoke suite (203 passed, with the tests marked `live`, `internet`, `hosted` or `desktop`
and the 31 that call a cloud model through Ollama left out; the Ollama log shows no chat request during it); the run
page checked on a scripted throw-away daemon at desktop width in the light and the dark theme and at 375 px (the targets
on the plan card's first line, a plan without targets with no targets line and no "null", the limit line in the
timeline, the goal check at the limit that says the run stops, the run's end and its result, no horizontal overflow, a
clean console), and the lines of `dotx run` replayed from the same run.

### 12. Campaigns: a goal that works until its outcome is verified

**Status: done** (`c915ab2`, PR #5). **Goal.** The owner switches on *Keep going until done* for a goal, and Dot works
on it in sessions, through restarts and days, until the outcome is verified by evidence or the owner stops it. Nothing
else stops it.

**Why.** This is the owner's main ask of 2026-10-09: "a system that does not stop until I see the result; when I say
earn money and give the information, it must not stop until the money is where it belongs". Before this item (the lines
are those of `12ad395`) a run ended by `finish`, by text replies, by the watchdog, by the goal check's patience or at a
limit (`run_agent`, `agent/loop.py:839`); a schedule the agent creates cannot carry the owner's approval mode
(`tools/schedule.py:99-101`) and starts every firing in a new thread with only the goal text
(`scheduler/runner.py:105-107`); the prompt forbids saving progress (`agent/prompt.py:178`); so no run knew what the
last one did.

**Design** (corrected on 2026-10-09 against the code before building; the first draft gave each campaign a
`ScheduleRecord`, which the scheduler skips when overdue and disables after a one-time firing, and which Settings ›
Schedules and the `schedule_*` tools would have shown).
- **The campaign.** Table `campaigns` (`store/db.py:97-107`): `id`, `title` (the goal's first 60 characters), `goal`,
  `targets` and `verification` (set by the agent), `status` (`active`, `achieved`, `stopped`), `thread_id` (the owner's
  conversation), `model`, `effort` and `approvals` of the opening session (a `yolo` campaign stays `yolo`), `files`
  (the paths of the files attached to the goal), `cron` and `next_session_at` (its own timing: no schedule),
  `created_at`, `updated_at`, `evidence` (empty unless achieved). The ledger is
  `campaign_entries(id, campaign_id, run_id, ts, kind, text, numbers)`, `kind` one of `progress`, `setback`, `note`,
  `strategy`, `question`, `numbers` a JSON object of measure to number. `runs.campaign_id` (`_ADDED_RUN_COLUMNS`,
  `store/db.py:456`) makes a run a session; the count of sessions, the last progress and the last session are read from
  the runs and the ledger, never stored twice.
- **Starting one.** `StartRunRequest.standing` (`server/views.py:96`), the composer's switch *Keep going* (a task only;
  goal mode hides while it is on) and `dotx run --standing` call `RunManager.start_campaign` (`agent/manager.py:178`):
  a chat or research run, or goal mode asked for, is refused (422); the campaign is created and the opening session
  starts in the owner's thread with the goal as its first message. A campaign whose opening session cannot start is
  deleted again.
- **A session.** A run with `campaign_id`, goal the campaign's goal, mode `task`, without goal mode, with the campaign's
  model, effort and approvals. Every session after the first starts in a thread of its own, "<title> · session N", as
  an unattended run (`SCHEDULE_SOURCE`), and its first message is the brief (`render_brief`, `agent/campaign.py:119`):
  the goal; the targets and how they are verified, or that they are not set; the paths of the files attached to the
  goal (the first session got their content); the goals of the runs the owner started in the campaign's conversation
  since (its instructions); the newest strategy, and the newest progress when it is older than the entries shown; the
  last 12 ledger entries with their numbers; the last 8 questions left for the owner with their answers, skips,
  "unanswered since …" or that they expired; how the last session ended (or that the daemon cut it off); and, after
  `campaign_patience_sessions` sessions in a row without a progress entry, a call for a new strategy first. What the
  session's reviewer takes as the owner's words are those instructions, the goal and the owner's answers to the
  sessions' questions (`_session_instructions`). Only one session of a campaign works at a time, in any process
  (`start_session`, `agent/manager.py:240`).
- **The prompt.** A session's prompt has a `# Campaign` section instead of the open-ended goals (`_CAMPAIGN`,
  `agent/prompt.py:129`; `_ending`, `:357`): session N, from session 2 on the brief says what was done, record the
  targets while there are none, write progress with numbers and evidence (and setbacks and strategies) with
  `campaign_note` before finishing, not in the notes of the memory, verify before claiming, leave the owner a question
  only for what only they can give, write what the owner says during the session in the ledger, and finish when the
  session's work is done, with success true when it did its part: the campaign goes on.
- **Tools** (`tools/campaign.py`), all `meta` and `safe`, in the core group of a session and left out of every other
  run (`agent/loop.py:871`): `campaign_targets(targets, verification)` (targets it replaces stay in the ledger);
  `campaign_note(kind, text, numbers)`; `campaign_schedule(at | cron)`, which only moves the campaign's own next session
  (a moment keeps a rhythm for the sessions after it; a cron replaces it and holds to the scheduler's minimum interval);
  `campaign_achieved(evidence)`, called alone after the reads that verify, since the facts are those of the steps
  before. The first three shape every later session, so they carry the `persistence` flag: once the session has read
  untrusted content the policy asks first, as for a memory note (yolo records it and goes on). A session that wants to
  end while the campaign has no targets is sent back once (`campaign_targets_reminder`, `agent/loop.py:1149`), after
  the plan reminder.
- **It does not stop.** When a session ends, before its waiters wake (`_drive`, `agent/manager.py:882`;
  `_follow_session`, `:890`), the next one is planned by `next_session_at` (`agent/campaign.py:175`): when a session
  planned it (`campaign_schedule`, or the rhythm of a cron), then; after a session that ended with `needs_from_user` and
  questions it left for the owner, none answered or skipped yet, `campaign_wait_hours` (24) later (a question that
  expired while a session waited for it can no longer be answered, so nothing waits for it); else
  `campaign_gap_minutes` (60) later. The owner's emergency stop of desktop control stops the campaign of the session it
  ends. A question a session left starts no run and goes to no other run (`decide`, `agent/manager.py:355`): the
  answer reaches the session while it runs in the daemon, and is otherwise kept for the next brief; once the session
  has ended, after it planned the next one, an answer or a skip brings that one to now. The scheduler's tick starts
  every active campaign whose next session is due and none of whose sessions works (`due_campaigns`,
  `store/db.py:1501`; `scheduler/runner.py:83`), however late: a campaign's session is never skipped as overdue. When
  the daemon starts, every active campaign with no session planned and none working gets one (`resume_campaigns`,
  `agent/manager.py:287`, called at `server/daemon.py:160`): now, or after `campaign_gap_minutes` when the daemon died
  while the last session worked, so that a session that brings the daemon down does not run again and again. A session
  the daemon cut off is not resumed as a run (`can_resume`, `agent/resume.py:102`): the next session's brief says it was
  cut off.
- **Verification, not a claim.** `campaign_achieved` asks the goal checker (`goal_checker_for`, session key
  "<thread>:campaign") with the goal, the targets and how they are verified, the progress entries of the ledger as the
  digest, the facts the session's own calls measured (`ToolContext.facts`, `tools/context.py:60`) and the evidence as
  the answer (the digest and the reason masked and shortened as in the loop's goal check). `achieved`: the campaign is
  achieved with the evidence, the ledger notes the checker's reason and the owner gets a notification
  (`Runtime.notify`, `core/notify.py:14`). `continue`: the call fails with the checker's reason and the session goes on.
  `blocked`: the call fails and tells the session to note the setback, ask the owner how to go on and finish needing
  them, so the next session waits for the answer.
- **Only the owner stops it.** `server/routes_campaigns.py`: `GET /api/campaigns`, `GET /api/campaigns/{id}`, `POST
  …/stop` (409 when not active), `…/resume` (from stopped or achieved; 409 when active) and `…/achieved` (409 when
  achieved), each noted in the ledger; stop and achieved also stop the campaign's session working in the daemon.
  `dotx campaign list | show ID | stop ID | resume ID | achieved ID` change the store alone (`cli_campaign.py`), so a
  session working then goes on until it ends (Stop on its run page ends it, as the command says) and nothing follows
  it.
- **UI.** Campaigns in the sidebar (`views/campaigns.js`): the list (title, status, sessions, the session working or
  the next one, the newest progress with its numbers, active campaigns first) and a page per campaign (goal, targets,
  how they are verified, the evidence once achieved, model, reasoning, approvals and tokens, the questions waiting for
  the owner, whose cards a reload leaves as they are, the ledger, the sessions with links to their runs, the questions
  answered; Stop, Go on, Mark achieved). A session's run page has a chip "Campaign · session N"; the timeline notes the
  targets reminder. The threads of the sessions after the first are left out of the chat list
  (`list_thread_summaries`, `store/db.py:592`).

**Touchpoints.** `store/db.py`, `store/models.py`, `config.py`, `defaults.toml`, new `agent/campaign.py`, new
`agent/campaign_control.py`, `agent/manager.py`, `agent/loop.py`, `agent/prompt.py`, `agent/resume.py`, new
`tools/campaign.py`, `tools/context.py`, new `core/notify.py`, `tools/meta.py`, `runtime.py`, `scheduler/runner.py`,
`server/daemon.py`, `server/views.py`, `server/routes_runs.py`, new `server/routes_campaigns.py`, `server/app.py`,
`core/errors.py`, `cli.py`, new `cli_campaign.py`, `static/js/runmodes.js`, `views/runs.js`, new `views/campaigns.js`,
`views/run.js`, `approvals.js`, `timeline.js`, `sidebar.js`, `main.js`, README ("Campaigns").

**Decisions and limits.**
- A campaign has no step limit and no end but a verified outcome or the owner: that is what was asked. The brakes are
  opt-in: `[agent] campaign_max_sessions` (0, none) stops a campaign after that many sessions (Go on gives it one
  more), and item 11's `run_max_minutes` and `run_max_tokens` bound each session. The campaign's page shows the tokens
  of its sessions and of the runs they delegated to.
- Sessions are new threads on purpose: a bounded context, and the brief is the memory. The owner's conversation holds
  the opening session and is where they talk to the campaign: what they start there is a run of its own (it does not
  stop the campaign), and its goal is an instruction of the sessions after it. Stopping a session's run does not stop
  the campaign; Stop on the campaign's page does.
- Money and credentials: nothing new. A `yolo` campaign does them itself and records them; an `ask` campaign leaves
  questions and its sessions go on without the answers.
- A question of a session that is still working is answered into that session, as for any run; the page of the
  campaign and of the session say what becomes of the answer.
- Deleting the campaign's conversation deletes its opening session with it: the sessions after it number on from the
  ones left, and `campaign_max_sessions` counts those. Left as it is (found in review): rare, and a fix needs either
  session numbers stored with the runs or a choice of what deleting that chat means for the campaign.
- Live behavior is not tested here: the smoke tests play the sessions and the goal check with the scripted model, and
  no campaign has run with a real model.

**Done when.** Unit tests of `agent/campaign.py` (`tests/unit/test_campaign.py`: the first brief, a later brief with
the files attached to the goal, instructions, strategy, numbers in full, the four states of a question and the last
session, the newest progress shown when newer entries push it out of the ledger listed, a brief after a session the
daemon cut off and too many sessions without progress, the next-session rule, the sessions without progress, the
session number). Smoke with the scripted model (`tests/smoke/test_campaigns.py`): an opening session that wants to end
without targets is sent back once, records them and a progress entry, ends, and the next session is planned at
`campaign_gap_minutes`, while another run of the same daemon is offered no campaign tool and keeps the open-goal
section; the real scheduler starts the second session in its own thread with the brief (targets, the ledger with its
numbers, how session 1 ended), a `continue` verdict fails `campaign_achieved` with the checker's reason, an `achieved`
one closes the campaign, clears its next session, notes the reason and notifies the owner once, and the session's thread
is not a chat; a session the daemon cut off is not resumable, the daemon that died during it plans the next session
after the gap when it starts again (once), and that session's brief says the last one was cut off; the answer to a
question the ended opening session left goes to no other run, though a run of the owner waits on the campaign's
conversation, starts none, and brings the next session from a day ahead to now; a moment a session plans keeps the
rhythm it set; through the API, a standing research run or one with goal mode is refused (422), a standing task starts
a campaign whose run detail carries the chip, the list and the page show it, and stop, resume and achieved work and
refuse a second time (409), an unknown campaign is 404. Each of eight mutations (no targets reminder, a `continue`
accepted, no planning after a session, the campaign tools in every run, a session resumable, an answer handed to the
run on the thread, a moment that clears the rhythm, a session planned at once after the daemon died) makes a smoke test
fail. `dotx campaign` checked on a throw-away home; the list, a campaign's page (with a half-typed answer that a reload
keeps), the run page of two sessions and the composer's switch checked in headless Chrome on a scripted throw-away
daemon at 1280 and 390 px in the light and the dark theme (no horizontal overflow, no "null", a clean console). README
updated.

### 13. Faster browser and desktop steps

**Status: done** (`a14f21a`, PR #6). **Goal.** Take the fixed waiting out of a browser or desktop action where no
signal needs it, and make a failed wait or an unchanged page cost no extra step.

**Why.** Measured on 2026-10-09 (read-only bench, this Mac; the Playwright and accessibility parts not measured): a
browser click waits about 160 ms by construction, a five-click batch about 0.32 s; a desktop click about 0.56 s before
any accessibility work, a five-click batch about 1.46 s, plus one unconditional closing walk capped at 4 s. On top,
`browser_wait_text` failed 22.4 % of the time in the real history and returns no view when it fails, so a failed wait
costs the wait, a model turn and a snapshot. Item 9 measures the effect of every change here.

The first draft of this design was checked against the code and against probes before building (scripts outside the
repository, numbers below); four of its points changed.

**Design, browser** (`browser/session.py`, `browser/driver.py`, `browser/render.py`, `tools/browser.py`):
- The settle after an act stays whole, `nav_idle` and the DOM quiet window: a `hover` opens menus and a
  `scroll_into_view` loads lazy content, so a batch of them does not skip it (the draft skipped it). When a batch
  closes its own tab, the tab that becomes current is settled once (`_settled_view`), not twice. A popup is adopted
  as a new tab but never made current, so that is the only way the batch leaves its tab.
- The popup grace (`side_effect_grace_ms`, 40 ms) already ran only after the steps that can open a popup or a dialog
  (`_SIDE_EFFECT_ACTIONS` in `browser/types.py`: `click`, `double_click`, `right_click`, `select`, `press`, `check`,
  `uncheck`, `click_at`); `fill`, `type` and `hover` never wait. It stays: a batch must stop at a popup.
- The current tab's title is read twice per view (`_assemble`); both reads cost about 1 ms together (probe), so it
  stays.
- `browser_scroll` no longer sleeps 150 ms after the wheel. Playwright's wheel returns before Chrome scrolls, but the
  page lands in one jump within 41 ms of the call (8 runs each of 600 px, 3 000 px and to the bottom, headless and
  headed Chrome on this Mac), inside the DOM quiet window of the settle that follows. The sleep also gave content
  that a page fetches after a scroll more time: what arrives between about 145 and 270 ms after the wheel was shown
  before and now shows in the next view. No signal says that a fetch is coming (the driver never waits for the
  network, which analytics keep busy); a longer `settle_quiet_ms` buys that time back (open decision 13).
- `browser_wait_text` returns a `TextWait` (appeared, waited_s, view) and the tool one line and the view of what
  changed: "The text appeared after 0.6 s · Tab 1 of 1 · …"; when the text does not come in time the call fails and
  still shows the page: "The text did not appear within 10 s; the page now · …". The model's text stays out of that
  line, which carries only what Dot produces (`browser/render.py`). A page that moved on during the wait is checked
  against the URL rules, as after an act. A failed result is not counted as a read by the gate, so the tool marks it as
  carrying content a third party can write. The wait takes the first visible match: a hidden copy of the text earlier
  in the page (a template, a closed menu) held it until the timeout. After a timeout the page is read as it is, with
  no settle that could outlast the wait, and a note when it is still loading. A navigation that has not committed yet
  still holds any reading of the page until it commits (probe: 5.7 s; `action_timeout_s` bounds it), as it did before,
  so the URL rules are checked again once the page is read.
- `settle_quiet_ms` (120) and `settle_max_ms` (1 500) stay knobs; the timing test runs with 120 and 60 for open
  decision 13.

**Design, desktop** (`desktop/ax.py`, `desktop/session.py`, `desktop/actions.py`):
- After a batch that changed something, the fixed `settle_ms` sleep becomes `ax.wait_until_quiet`: an `AXObserver` on
  the application element, on the desktop worker's run loop, for 28 announcements (values, titles, elements and windows
  that come, go, move or resize, focus, sheets, menus, selections, rows, layout, busy state, page loads and live regions
  of web content). The window is read once the application has announced nothing for `[desktop] settle_quiet_ms` (120)
  since it took the registration, at most `settle_ms` (350) after the wait began. Every call to the application gives up
  after `settle_ms`, registering stops at `settle_ms`, and the removals stop at the first that is not answered: an
  application that hangs during the wait adds at most `settle_ms` more. An application that cannot be watched gets the
  whole `settle_ms`, with a warning in the log. One callback serves every wait (a callback wrapped per wait leaked about
  6 KB each). The draft polled the window list and the focused element instead, which sees no change inside a window.
  Probe (an off-screen accessory app whose buttons were pressed through accessibility, no pointer): a label that changes
  at once 133 ms, five changes 60 ms apart 327 ms, a press 30 ms into the wait 171 ms, the 28 registrations and removals
  0.9 ms, an app whose main thread is busy 190 ms of every 200 ms 198-362 ms (it answers the registrations in one gap),
  a stopped (SIGSTOP) app 355 ms, an app stopped during the wait 487 ms (it was about 30 s before the removals stopped
  at the first failure), a pid that is gone 351 ms. A change that comes later than the quiet window without anything
  announced in between (250 ms in the probe) is not waited for, as with the browser's DOM quiet window; the old fixed
  sleep showed such a change when it came within 350 ms. Waiting for a first announcement would not help: a change made
  before the registration is never announced, so every click would wait the whole `settle_ms`.
- A `type` step whose text is longer than `[desktop] paste_threshold_chars` (40; 0 never) and presses no Return or
  Tab (for a line break or a tab) goes through the paste path, into a field that is known, not secure and not one
  for a credential or a payment, and never with a key-like text (the policy's `typing_reasons`: the clipboard is
  shared with every application); the clipboard is put back as for `paste`. Shorter text is typed key by key as
  before.
- One `lsappinfo` listing per act is dropped: the preview (classify) and the act each list the applications, 22 ms
  median (max 98 ms); reusing the preview's listing saves 22 ms of a 0.5 s act and, after an approval wait, could act
  on an application that quit meanwhile.
- The step gaps (`_STEP_GAP_S`, the third of `settle_ms` after a step that opens a menu or a window) and the click
  pacing (`input.py:175-177`) stay: no acknowledgement exists between posted events.
- `screenshot_max_edge` (1 920) is a knob for open decision 13: a 1 710 × 1 112 JPEG is about 2 500 tokens on the
  hosted providers; 1 366 would be about 1 600.

**Measured** (medians; the timing tests print them, `-s`):
- Browser, headless, fixture home page, 20 one-click acts and 5 five-click batches, three runs each: before,
  `settle_quiet_ms` 120: one click 0.220-0.227 s, five clicks 0.551-0.567 s; 60: 0.149-0.150 s and 0.496-0.499 s.
  After: 120: 0.220-0.231 s and 0.549-0.566 s; 60: 0.149-0.150 s and 0.450-0.498 s, the same, since this item does not
  touch the click path. One click act of 222 ms (probe): the click 22 ms, the popup grace 42, the DOM quiet window
  124, the capture 35, the two title reads 1. A 600 px `browser_scroll` (20 on the home page, two runs): 329-331 ms
  before, 169 ms after.
- Desktop, the fixture window, 20 one-click acts and 5 five-click batches, the owner away: before (`00fb523`, three
  runs) one click 0.546-0.573 s, five clicks 1.520-1.549 s; after (three runs) 0.308-0.345 s and 1.278-1.323 s. One
  click act of 343 ms after (probe): the click with its pacing 125 ms, the settle 128, the closing view 32, the
  application listing 32, the preparation 27. Five clicks: the clicks 623 ms and the gaps after steps that open
  something (a third of `settle_ms` each, kept) take most of the 1.33 s. A `type` act (click, select all, type) of 40
  characters typed key by key 1.04 s, 41 pasted 0.92 s, 200 pasted 0.92 s (typing costs 8 ms a character).

**Touchpoints.** `browser/session.py`, `browser/driver.py`, `browser/render.py`, `browser/types.py`,
`tools/browser.py`, `desktop/ax.py`, `desktop/session.py`, `desktop/actions.py`, `desktop/input.py`,
`desktop/types.py`, `tools/desktop.py`, `config.py`, `defaults.toml`, README "Browser use" and "Computer use",
`tests/smoke/test_browser.py`, `tests/smoke/test_desktop.py`, `tests/unit/test_desktop_secrets.py`.

**Done when.** `tests/smoke/test_browser.py` has the timing test (20 one-click acts and 5 five-click batches with
`settle_quiet_ms` 120 and 60, medians printed, asserted under 1.5 s and 3 s), a scroll test (2 000 px down shows
`Item 100` and not `Item 10`) and the wait tests (the line with the time and the changes, past a hidden copy of the
text; a timeout fails, is marked untrusted and still shows the page; one during a slow load returns without waiting
for the rest); `tests/smoke/test_desktop.py` (under `DOT_DESKTOP_TESTS=1`) has the same timing test against the
fixture window and a paste test for long text (it arrives whole, the clipboard is put back); a unit test of the paste
rule; the medians before and after written here; the offline smoke suite clean; README updated.

### 14. Rich editors, drag and bulk text in the browser

**Status: done** (`37808d5`, PR #7). **Goal.** Typing works in the editors of X, GitHub, Google Docs, Monaco and
CodeMirror; the browser can drag an element onto another; a long text goes in as one insertion.

**Why.** On X's composer `type` lost the first character and `fill` reported done without a change (follow-up of
2026-10-07). `type` is `press_sequentially` and `fill` is `locator.fill` (`browser/driver.py:386-392`); editors built
on contenteditable (Draft.js and Lexical on X, ProseMirror, CodeMirror 6) take text only through their own input
handling and get ready asynchronously after they take the focus. The browser has no `drag` step (`StepAction`,
`browser/types.py:10-23`), which web IDEs, boards and file uploads by drop need. This item is also the browser half of
"write code in the browser".

The first draft of this design was checked against probes before building (a local editor that takes text only
through `beforeinput`, as Draft.js and Lexical do, and gets ready a set time after the focus; scripts outside the
repository). Three of its points changed:
- Playwright's `fill` on a contenteditable already focuses, selects all and inserts the text as one `insertText`
  input; that part of the draft was the code as it is.
- With the editor ready 30 ms after the focus, `fill`, `type` and `keyboard.insert_text` all lose the text: the editor
  drops what comes before it is ready, which explains both of X's symptoms. The draft's poll until
  `document.activeElement` is the element does not help, since that holds as soon as `focus()` returns.
- `type` stays key by key (autocomplete and mentions see the keys); `insert` is the step without key events.

**Design.**
- **Editors.** An element is in an editor when it is contenteditable and not a form control; the editor is its editing
  host (the nearest `[contenteditable]` that is not `"false"`). An `<input>` inside an editor and a document in
  designMode (which has no such host) keep Playwright's own steps. `fill`, `type` and `insert` give the editor the focus
  when it does not have it and wait until the editor and the selection have been quiet for 50 ms (at most 500 ms): in
  the probe, editors ready 0 and 30 ms after the focus then take every step, after 57-88 ms of waiting. The wait runs
  also when the editor has the focus already, since a click into it just before gave it the focus too and nothing tells
  that apart from a focus taken long ago. Then the field that has the focus is checked for a secret (a page can move the
  focus during the wait) and the text goes in: `fill` is Playwright's `fill`, which selects the element's content with a
  DOM range (so a paragraph inside an editor works) and waits until the element is visible, enabled and editable; `type`
  presses the keys on the page's keyboard; `insert` inserts the text. An editor that gets ready later without changing
  anything first still drops the text (150 ms in the probe), so the step watches the editor's text: the editor took the
  text when its text differs from before, at once or within 500 ms, or, for `fill`, when the element holds the text
  (filling an editor with the text it holds, the empty text into an empty editor included, changes nothing). Otherwise
  the step fails with "the editor did not take the text", or, when the editor left the page with its text unchanged,
  says that it is not known whether the editor took it. A write after which the page opens another one (Enter in a
  search box) is done.
- **New steps.** `insert(ref, text)` puts the text in as one insertion, without key events: for long text and for
  editors (Monaco's input is a `<textarea>` and takes it; CodeMirror 6 is an editor as above). Outside an editor it goes
  only into a text `<input>` or a `<textarea>` that takes the focus, at the caret; at any other element the step fails,
  since the text would go where the focus or the selection happens to be (a password field whose caret stayed, for one).
  `type` and `insert` need a text that is not empty. `drag(ref, to_ref)` is `locator.drag_to(target, steps=10)`:
  Playwright drives the mouse and the HTML5 drag events, with ten moves on the way for libraries that start a drag only
  after some movement (probe: an HTML5 board moves its item in 114 ms). A drag that fails after pressing the button
  releases it: Playwright leaves it pressed, and later clicks were reported done without reaching the page. `Step` gets
  `to_ref`; `StepAction` gets `insert` and `drag`. `insert` is a typing step: the field that has the focus is checked
  for a secret before the text goes in, and credentials and key-like text are judged as for `type`. The element `drag`
  drops onto is checked for a secret like the field of `fill`. `drag` judges the dragged element as a click on it would
  be, and the element it drops onto by its name whatever its role (a drop zone is more often a list or a region than a
  button) and as a field that takes typed text. The popup grace follows `drag` (a drop can open a dialog), not `insert`
  (as with `fill` and `type`). The batch counts as input when it has an `insert`.
- **Fixture pages** in `tests/smoke/test_browser.py` (`_PAGES`): `/editor`, an editor that takes text only through
  `beforeinput`, gets ready a set time after the focus (`?ready=`, 30 ms by default), shows a change a set time after
  the input (`?apply=`, at once by default) and shows what it holds in a line under it, next to a plain `<input>` and an
  editor without a role that holds two paragraphs and an `<input>`; `/design`, a document in designMode; `/search-box`,
  a contenteditable search box whose Enter opens the results; `/board`, two lists with HTML5 drag and drop handlers that
  move the dragged item. In `tests/smoke/test_secret_fields.py`: `/verify-note`, an editor that hands the focus to the
  code field 20 ms after it takes it.
- **Guidance.** The `browser_act` description: `fill` for form fields, `type` where autocomplete must see the keys,
  `insert` for long text and for editors, `drag` to move an element onto another.

**Touchpoints.** `browser/driver.py`, `browser/types.py`, `browser/safety.py`, `browser/render.py`,
`policy/elements.py`, `tools/browser.py`, README "Browser use", `tests/smoke/test_browser.py`,
`tests/smoke/test_secret_fields.py`, `tests/unit/test_tool_errors.py`, `tests/unit/test_secret_fields.py`.

**Decisions and limits.**
- X itself is not tested here (no form filling on external sites, a rule since part 1); the owner checks the composer
  once (open decision 12).
- An editor that gets ready more than 50 ms after the focus without changing anything meanwhile drops the text; the step
  then fails and says so, and a second try finds it ready.
- An editor that shows the text more than 0.5 s after the write is reported as not having taken it (a second try then
  writes the text twice), and so is a write that changes no text in it and makes the page open another one only after
  more than half a second; a widget inside the editor whose text changes by itself can make a dropped write look taken;
  a `type` that starts before the editor is ready can lose its first keys and still change the editor.
- `press` with a ref is not covered by the wait: a key pressed into an editor that is not ready yet is lost, as before.
- Google Docs takes its text through a hidden editor inside a frame; it is not tested here.
- Monaco and CodeMirror take `insert_text`; their auto-indent and auto-close can still change the text that goes in.
- A drag library that starts a drag only on a long press or from the keyboard is not covered, and neither is an upload
  by drop: `drag` carries no files.

**Done when.** Smoke tests on `/editor` (`type`, `fill` and `insert` leave the editor's line equal to the text with the
editor ready 30 ms after the focus; a `fill` with the text the editor holds, and with the empty text into the empty
editor, is done; a write the editor drops fails the step; an editor that shows the text 150 ms after the write took it;
a click into the editor ready 70 ms after the focus, then a `type`, takes the text; `fill` reaches a paragraph of the
editor without a role and the `<input>` inside it; `insert` at an element that takes no text fails; `type` into the
plain `<input>` unchanged), `/design` (`fill` into a paragraph and an `<input>`), `/search-box` (a key after which the
page opens another one is done) and `/board` (`drag` moves the item); in `tests/smoke/test_secret_fields.py`, an
`insert` at the heading and a drop onto the code field are refused, and so is an editor's text once the editor handed
the focus to the code field; unit tests of the new step validation and of how a drop is judged; the offline smoke suite
clean; README updated.

### 15. Helper models: consult, delegate to another model, standby

**Status: done** (`530ac62`, PR #8). **Goal.** A run on one model can ask a model of another provider for advice, hand a sub-task to it,
or move to it when its own model stops answering; a helper whose key is missing or whose quota is spent is simply not
there, and the run goes on.

**Why.** The owner asked for it on 2026-10-09 ("while DeepSeek works it should be able to ask Sonnet or Opus for help;
without a key or with the quota spent nothing may break"). The seed exists: the review model is a second model the
harness uses for approvals in `auto`, the goal check, the ladder's second opinion and lessons. But the running model
cannot ask for help itself, `delegate` always uses the parent's model (`agent/manager.py:323-326`), `[models] fast` is
read nowhere, and a missing key or an exhausted quota ends the run: the step call (`agent/loop.py:1031-1032`) has no
handler, and the manager fails the run with the error (`agent/manager.py:865-866`). The gain is largest for a weak
model, which is this roadmap's principle.

**Design** (checked against the code on 2026-10-10; what changed from the first draft is listed below).
- **Configuration.** `[models] helpers = []`: model references in order of preference, for example
  `["anthropic:claude-opus-5-5", "openai:gpt-5.5"]`. `[models] standby = []`: the models a run moves to, in order, when
  its own model cannot answer. `[models] helper_cooldown_s = 600` (at most 86400). `[agent] consult_max_per_run = 5`.
  They go into `config.py` and `defaults.toml` together (the config models forbid unknown keys and have no defaults).
  The references are checked where they are used (`parse_model_ref`), as `main` is.
- **Pauses, in the router, per model.** A model that cannot answer for a while is paused: a quota, rate-limit or key
  error, or a missing key (`ProviderQuotaError`, `ProviderRateLimitError`, `ProviderAuthError`, `MissingSecretError`;
  the last is a `ConfigError`, raised when the connector is made). `ModelRouter.complete` already catches every failure
  to record it; it now also pauses the model, for the `retry_after_s` its provider gave (at most a day,
  `MAX_PAUSE_S`, so an endless `Retry-After` cannot break the end-of-pause text), else for `helper_cooldown_s`. A call
  to the model that answers ends its pause, and so does a new key of its provider (`retire`, which the keys page
  calls). Each model has its own pause: a provider's local and cloud models (Ollama) are not held back by one another,
  and a provider whose key or quota fails them all costs one failed call per model. The router never refuses a call
  because of a pause: pauses only steer the choice of a helper or a standby. They live in the daemon's memory.
  Transport and protocol errors pause nothing. By the time an error reaches the router, the connector has retried
  what can be retried (six attempts; a rate limit that asks for more than 120 s is not retried, `llm/http.py:66-73`).
  Anthropic answers a spent prepaid balance with HTTP 400 `invalid_request_error` ("Your credit balance is too low to
  access the Anthropic API", reported in September 2026 in anthropic-sdk-typescript issue 618 and ruby_llm issue
  1006; the documented `billing_error` 402 is kept):
  `classify_anthropic` makes it a quota error.
- **The roster** (new `agent/helpers.py`). `roster_for(models, keyed, paused) -> Roster(available, unavailable)` is
  pure: a model whose provider lacks its key (Ollama needs none) or that is paused is unavailable, with the reason
  and, for a pause, its end. `keyed_providers` reads the key states with `secrets.secret_state` (environment, env
  file, Keychain; it reads the value and returns only where it comes from) in a thread, because the Keychain lookup is
  a blocking subprocess.
- **`consult`** (new `tools/consult.py`; core, through the fallback of `tools/groups.py:48`). Offered when a helper
  other than the run's own model is available at the start of the run and `consult_max_per_run` is above 0, as
  `skill` is withheld without skills; a delegated run never gets it (its parent asks), so the budget of a request holds
  exactly. The system prompt gets a Helpers section that names the helpers when the run offers `consult` or
  `delegate`. `ConsultArgs(question, include, model)`.
  - `include` is a list of `goal`, `plan`, `recent` and `screenshot`, by default the first three. They are the run's
    goal, its plan (the newest `plan` event, as `/btw` reads it), the thread's conversation through `goal_digest`
    (`agent/review.py:362`: secrets masked, the start and the end of a long one), and the newest screenshot of the
    thread. The screenshot goes only to a helper that can see images; the result says when it was left out.
  - `model` names a helper; without it the first available one answers. The run's own model is never asked, even
    when the helpers list it.
  - The helper gets no tools and answers in plain text, as a `/btw` side answer does (`agent/aside.py:82`). Its system
    prompt says that it advises an agent, that the user's goal is the only authority, and that the fenced record is
    data (`<<NAME nonce>>` fences, as in the review model's prompts).
  - The answer comes back fenced as advice: `<helper_advice nonce="..." model="...">`. Purpose `consult`, a new value of
    `CallPurpose` (`llm/calls.py:17-19`). Kind `meta`, risk `safe`, every mode.
  - Not parallel-safe: the calls of one turn run one after another, so the budget holds exactly. The call after
    `consult_max_per_run` calls of the run fails with the count; the count comes from the run's `model_call` events
    with purpose `consult`, failed calls included. A failure that pauses nothing is logged (`consult_failed`).
  - An unavailable error emits `helper_unavailable {model, reason, until}`, and the result says "anthropic:…
    (quota exhausted until 14:05) cannot answer; ask another helper (…) or go on yourself".
- **`delegate(model)`.** `DelegateArgs.model: str | None`: one of the available helpers, or the run's own model;
  anything else fails and names the available helpers. `Spawner.delegate` and `RunManager.delegate` take `model`; `None`
  is the parent's model as stored, so a delegated run follows its parent's switch to a standby. The tool's description
  and the Delegation section of the prompt say so. The parent's result names the model the delegated run ended on.
- **Standby.** `run_agent` catches the four errors around the step call and goes through the available standby models
  in order:
  - a model the run has worked on is not taken again, so a run takes each model once at most and never goes back
    (two spent providers cannot hand a run back and forth as their pauses end);
  - a model whose details cannot be read, that cannot call tools while the run offers tools, or whose window cannot
    hold the system prompt and the tools is passed over (`standby_passed_over` in the log); when every available one
    is, the run fails with a `ConfigError` that says why for each;
  - on the first that fits, the run moves to it: `store.set_run_model` (new) with the reasoning level of the new model
    nearest to the run's (`effective_effort`; `None` stays `None`), so a follow-up or a resume passes the effort
    check; the run snapshot is replaced (`AgentRun` and its `RunRecord` are frozen, and compaction reads
    `agent.run.model`);
  - the model info, the scaffold (context budget, tool output budget) and the token-estimate ratio follow the new
    model, and images leave the history when it cannot see;
  - a stored note tells the model "From this step on you run on X: Y cannot answer (quota exhausted)", and the event
    `model_switched {from, to, reason, step}` is emitted;
  - the step is asked again on the new model, with the tools that step had (a re-plan's restriction holds). Its
    system prompt stays.
  - Without a standby model that can answer, the error ends the run. A transport error or a timeout does not switch.
- **Where it is seen.** The timeline shows `model_switched` and `helper_unavailable` as notes, with existing icons; the
  model chip follows a switch through `onRunStatus`, and the header of a delegated run names its new model. `dotx run`
  prints both events. `dotx doctor` prints a `helpers` row and a `standby` row with each model's state; they never fail
  the check. README.

**Touchpoints.** `config.py`, `defaults.toml`, `llm/router.py`, `llm/calls.py`, `llm/provider_errors.py`, new
`agent/helpers.py`, new `tools/consult.py`, `tools/delegate.py`, `tools/context.py`, `agent/manager.py`,
`agent/loop.py`, `agent/prompt.py`, `runtime.py`, `store/db.py`, `evals/runner.py`, `doctor.py`, `cli_ui.py`,
`server/static/js/timeline.js`, `server/static/js/views/schedules.js`, README "Models and providers" and "Delegation".

**Changed from the first draft.**
- The pauses live in the router, which sees every failed call, instead of in the runtime. A call that answers ends its
  model's pause.
- A rate limit without a time pauses for `helper_cooldown_s` like the other errors, instead of 60 s. A rate limit also
  moves a run to its standby: one that outlasted the connector's retries ends a run as an exhausted quota does.
- `consult` is not parallel-safe, so calls of the same turn cannot overrun its budget.
- `recent` is the thread's conversation through `goal_digest`, not "the last steps".
- `dotx models profile <model>` already profiles any model, a helper too. The doctor's rows list the helpers.
- The smoke's second scripted provider is registered under `anthropic`, because `ModelRouter.register` only takes the
  known provider names. A stand-in key in the test's environment makes the provider count as keyed.
- The answer does not taint the run. The helper sees only the run's own record, which is tainted already when it holds
  third-party content.
- After the review (/code-review): pauses per model instead of per provider, ended by a new key too and at most a day
  long; a standby model is taken once at most and one that cannot take the run over is passed over; the run's level
  follows the switch; a step asked again keeps its tools; `consult` is offered neither to a delegated run nor for the
  run's own model, and the Helpers section comes with `delegate` alone too; evals clear `helpers` and `standby`;
  Anthropic's 400 for a spent balance is a quota error.

**Decisions and limits.**
- No silent fallback: a helper's failure is a tool result the model reads and an event the owner sees; a standby
  switch is announced in the run and in the timeline. Nothing is swapped without a trace.
- `consult` gives no tools to the helper: tool work goes through `delegate`, where the policy applies as to any run.
- The helper sees the owner's instructions as the only authority, as the reviewer does; its answer is advice.
- Both lists default empty: the owner fills them (open decision 11). The first candidate is Claude once
  `ANTHROPIC_API_KEY` is saved (decision 5).
- Pauses are per model and in the daemon's memory: a restart forgets them, and `dotx doctor`, a process of its own,
  shows only the key states.
- The roster is read at the start of a run that offers `consult` or `delegate`, and again at every `consult` and
  `delegate` call. A key saved during a run counts from the next call; a run that started without helpers does not get
  `consult` later.
- The review and vision models have no standby. With `review` on the provider of `main` (the default), a goal check, an
  `auto` approval or the ladder's second opinion fails while that provider cannot answer, also after the run moved to
  its standby (open decision 16).
- A timeout of the step call (`model_call_timeout_s`, which a long rate-limit ladder can outlast) and a transport error
  do not switch. A compaction whose summary call fails is folded mechanically, and the next step switches. A model
  whose details cannot be read when its run starts (no key, a refused key) fails the run with that error: it is the
  run's own setting to fix.
- The stats attribute a run's calls to the model of the run (`store/stats.py:33-56`): after a switch, the run's
  earlier calls count for the standby model. A follow-up on the run page, an answered question and a resume go on
  with the standby and its level; `dotx chat` starts every turn on its own model, which moves to the standby again.
- A standby model that cannot see gets placeholders for the images of the history; the stored thread keeps them.
- Not run live: the Anthropic connector has not answered a request yet (decision 5). With adaptive thinking, a turn of
  another model with tool calls and no thinking block is allowed by Anthropic's documentation ("Adaptive mode relaxes
  this: no assistant turn needs to start with one"), which a live run has not checked.

**Done when.** Unit tests of the roster, the pauses (per model, the cap, a new key) and the eval configuration; the
Anthropic error classes. Smoke with the scripted provider and a second scripted provider under `anthropic`: a
`consult` answered and fenced (the helper's request checked), the budget reached, a helper raising
`ProviderQuotaError` paused with the event and the result text, `delegate` on the helper model (no `consult` for the
delegated run, the Helpers section without `consult`), a run that does not take its own model for a helper, a standby
switch mid-run with `model_switched` and the run ending `done`, a run that goes through two standby models once each
and passes over one that cannot call tools, a run that no standby can take over failing with why, a re-planned step
keeping its tools on the standby, and without a standby the run failing with its error. `dotx doctor`'s rows on a
throw-away home; the timeline checked in headless Chrome; README.

### 16. Typed judgments (TypeSafe) at the decision points

**Status: done** (`a5516f7`, PR #10). **Goal.** Where the harness decides something today with a regular expression or
with a full review-model call, a small typed judgment can answer instead: fast, with a probability, in any language.

**Why.** The owner asked whether the decision system of TypeSafe (typesafe.ai, "System One" models such as Jev,
which return typed answers and probabilities for Choice, Noul and Score questions) belongs in Dot; their OSJarvis
project routes tasks with `choice`, verifies actions with `noul` and scores confidence with `score`. Dot's decision
points fit that shape: the intent of a browser click is read from label patterns (`policy/elements.py:17-29`; "Repost"
and "Quote" match none, Finnish labels none, Turkish ones only where a pattern lists them), the goal check and the
ladder's second opinion are full review-model calls (`agent/review.py`), and item 12's claim of an achieved target
needs a judge. The answer was yes, at two points first, behind an interface, optional, and never as the loop's
decision maker: the harness keeps the workflow, the judge supplies the semantic calls where code has none.

**Design** (checked against the code on 2026-10-10; what changed from the first draft is listed below).
- **Configuration.** `[judge] provider = "none" | "typesafe"` (default `none`), `api_key_env = "TYPESAFE_API_KEY"`,
  `base_url = "https://api.typesafe.ai"`, `model = "jev-1.13.0"`, `timeout_s = 5.0` (at most 60),
  `public_action_threshold = 0.45`, `target_threshold = 0.15`. The address, the model and the wire come from TypeSafe's
  live API reference (`https://docs.typesafe.ai/llms.txt` and its `api.md`, read on 2026-10-10). The key follows the
  secrets lookup (environment, `.env`, Keychain); Settings › API keys lists it last, and a key saved or removed there
  retires the judge's connection and ends a pause, as it does for a provider.
- **Connector** (`llm/typesafe.py`). `TypeSafeClient.nouls(state, questions) -> dict[str, float]`: one
  `POST /v1/systemone` with the key as a bearer token and every question about a state in one request, through
  `post_json` and `classify_generic` like the other connectors, with one retry (429, 529). An answer without a
  probability from 0 to 1 for a question is a `ProviderProtocolError`.
- **The judge** (`agent/judge.py`). `Judge` with `judge_click(ClickFacts(label, role, url)) -> ClickJudgment(reasons,
  note)` and `judge_claim(state) -> ClaimJudgment(probability)`; `NoJudge` adds nothing; `TypeSafeJudge` asks, and
  reads the key at its first judgment. The runtime holds one (`judge_for`). What it sends has its secrets masked
  (`redact_json`), and a page's address goes without its query and fragment.
- **Use 1, the intent of a browser click.** In `browser_act`'s classify (`tools/browser.py`), where the elements of the
  steps are known: each control that a click, a double or right click, a check or an uncheck lands on, whose role is
  clickable and whose name no pattern knows (`unexplained_clicks`, `browser/safety.py`), is judged once, with four Noul
  questions over `{control: {label, role}, page}`: does it publish, send, pay, delete? The controls of one batch are
  asked at once. A probability at or above `public_action_threshold` adds the reason a pattern would have given
  (`post`, `send`, `financial`, `destructive`), so the flags and the risk follow as for "Post" or "Delete", and the
  summary says "(judge: public_post 0.93)". A label a pattern knows is never sent.
- **Use 2, a campaign's claim.** `campaign_achieved` sends what the goal checker reads (the goal, the targets and how
  they are verified, the ledger's digest, the session's checked facts and the claim) with one question,
  `targets_reached`. Below `target_threshold` the call fails at once with the probability, and the campaign goes on;
  at or above, or with the judge off, the goal checker decides as before.
- **Unavailable.** A missing or refused key, a rate limit, an error or `timeout_s` passing makes the judge unavailable
  for `[models] helper_cooldown_s`: a warning in the log with the error, one persisted `judge_unavailable {reason,
  until, error}` event (a note in the timeline, a line in `dotx run`), and "(judge off)" in the summary of every click
  it does not judge meanwhile. Nothing is asked while it is unavailable; the patterns and the goal checker decide alone.
- **Where it is seen.** `dotx doctor` has a `judge` row: off, or the service and whether its key is set (a failure
  when the judge is on without a key). Settings › API keys lists TypeSafe; its **Check** sends one small judgment,
  because the API documents no other call to check a key with. README "Safety" ("Typed judgments"), `.env.example`.

**Touchpoints.** `config.py`, `defaults.toml`, new `llm/typesafe.py`, new `agent/judge.py`, `browser/safety.py`,
`tools/browser.py`, `tools/campaign.py`, `runtime.py`, `server/routes_keys.py`, `doctor.py`, `cli_ui.py`,
`server/static/js/timeline.js`, `providers.js`, `views/keys.js`, README "Safety", `.env.example`.

**Changed from the first draft.**
- A click is judged in `browser_act`'s classify, not in `RunGate.authorize`: the gate sees an `ActionInfo`, while the
  label, the role and the step are known only where the steps are classified. The policy still decides, from the flags.
- Only Noul questions (`nouls`), not a general `ask` with Choice and Score: neither use needs them.
- A click's state is its label, its role and the page's address: the element map the classify reads has no page title
  and no nearby text. No host list: with the judge on, every click whose label no pattern knows is judged.
- `policy/elements.py`, `agent/gate.py` and `secrets.py` are unchanged (the lookup takes any variable name).
- Tests: a stand-in for TypeSafe's API (`tests/smoke/test_judge.py`) that answers as the reference documents takes the
  place of the scripted judge and the offline wire tests, so the connector, the judge, `browser_act` on a real headless
  Chrome and the campaign tool are checked together.

**Decisions and limits.**
- Off by default (`provider = "none"`): it is a paid external service, though a cheap one (input tokens only). The
  thresholds were set on live answers of `jev-1.13.0` (open decision 9, done); the owner keeps it off.
- The judge never decides a step and never grants anything: it adds flags that make the policy ask, or stops a claim
  the goal checker would have examined. Code owns the workflow, as TypeSafe's own guidance says.
- One request per judged control adds its latency to that click, at most `timeout_s`; a claim the judge refuses saves
  the goal checker's call, one it lets through costs both.
- Run live on 2026-10-10 (open decision 9): the hosted tests (`.venv/bin/python -m pytest
  tests/smoke/test_hosted_services.py -m hosted -k typesafe`) check that "Boost", "Teilen" and "Compartir" get
  `public_post`, "Jetzt bezahlen" `financial` and "Konto löschen" `destructive` at `public_action_threshold`, each on
  the page it belongs to, and that a claim the session read reaches `target_threshold` while one resting on a hope does
  not. Keys are made at console.typesafe.ai, which Settings links to. The judge asks about every click whose label no
  pattern knows, on any site and in every mode: in `yolo` its flags ask nothing, but each request adds its time to the
  click, and a flagged click counts toward a batch's one public or social action.

**Done when.** Unit tests of which clicks are judged and of what the probabilities add (`tests/unit/test_judge.py`).
Smoke on the stand-in and headless Chrome: an unknown label flagged with its note and the request checked (path, key,
model, state without the query, the four questions), a known label not sent, a refused key unavailable for the
cooldown with its events until a new key, a campaign claim refused before the goal checker. The hosted test written
and skipped without the key; the `dotx doctor` row; README and `.env.example`. The owner's count goes into the log
when they run it.

### 17. Pacing and public actions on social platforms

**Status: done** (`d50a41e`, PR #11); no limit is set by default since decision 8 (2026-10-10). **Goal.** Reposts,
quotes, follows and likes are seen as what they are, and a run keeps to a pace that a platform tolerates, so an account
that Dot grows is not the account Dot gets suspended.

**Why.** `_POST_ACTION` and `_SEND_ACTION` (`policy/elements.py:28-29`) catch Post, Tweet, Retweet, Publish, Share,
Comment, Send and Reply; "Repost" and "Quote" (X's labels since 2023) match nothing, and Follow and Like carry no flag
at all. No cap and no spacing exist for public actions (search of `browser/` and `tools/browser.py` for pacing, rate,
quota, cooldown, throttle). A campaign that "does what it takes" on X would follow and like without limit; platforms
suspend for exactly that.

**Design** (checked against the code on 2026-10-10; what changed from the first draft is listed below).
- **Patterns** (`policy/elements.py`). `_POST_ACTION` adds `repost`, `yeniden gönder` and the Finnish `julkaise`, and
  `quote` and `alıntı(la)` at the start of a label or of a sentence in it ("Quote", "12 Quotes. Quote"; "Get a quote"
  stays a plain click); `_SEND_ACTION` adds `lähetä`. The new `_SOCIAL_ACTION` matches follow, unfollow, like, unlike,
  subscribe, unsubscribe, connect, `takip et`, `takibi bırak`, `beğen`, `beğenmekten vazgeç`, `abone ol`, `seuraa` and
  `tykkää` in the same place ("Follow @dot", "12 Likes. Like"; "Posts you might like" and "Following @dot" are no
  action). It gives the reason `social`: the new flag `social`, of risk `low` when it is the only one, in
  `TAINT_ESCALATION_FLAGS` (outward-facing).
- **The pace** (new `policy/pace.py`, pure). `[policy.pace]` `public_per_hour`, `public_per_day`, `social_per_hour`,
  `social_per_day`, `public_spacing_s`; 0 removes a limit, and every default is 0 since decision 8 (the first defaults
  were 10, 50, 20, 100 and 20 s). A `browser_click` or `browser_input` action with `public_post` or `external_send` is
  public, one with `social` is social. It counts on its site, the host of the action's target (the page's address)
  without `www.`, across runs and threads, in a sliding hour and day (`paced_actions(host, pace, ts, run_id)` in the
  store).
- **In the gate** (`agent/gate.py`). Before the policy decides, an action past a limit is refused (`PolicyDenied` and a
  `policy` event with source `pace`): "Pace for x.com: 10 of 10 public actions in the last hour; the next is allowed at
  14:35. Do other work meanwhile, or leave the rest to a later session." No approval mode waives it. Once the action is
  let through, a public one waits until `public_spacing_s` have passed since the last public one on its site, and the
  action is recorded.
- **One per batch** (`tools/browser.py`). A `browser_act` call with more than one public or social click (by the
  patterns or the typed judge) is sent back ("A batch takes one public or social action at most…"), so that each one
  counts.
- **Prompt.** A bullet under Browser: one public or social action per `browser_act`. Until decision 8 it also told the
  agent to keep to a platform's rules (no bulk follows or likes, no duplicate posts, nothing its automation rules
  forbid) and what a refusal of the pace means, which a refusal says itself.
- **Where it is seen.** The refusal is a `policy` event, which the timeline and `dotx run` show with its reason. README
  "Safety" ("Pace on social platforms").

**Touchpoints.** `policy/elements.py`, `policy/types.py`, new `policy/pace.py`, `agent/gate.py`, `agent/manager.py`,
`tools/browser.py`, `store/db.py`, `agent/prompt.py`, `core/text.py` (`until_text`, moved from `agent/helpers.py` so
that the policy does not import the agent), `config.py`, `defaults.toml`, README "Safety".

**Changed from the first draft.**
- `ActionInfo` has no `host`: the pace reads the host from the target of a browser action, which is the page's address
  (twenty constructors would have changed for one tool's sake).
- The pace is checked in the gate, before the decision, not in `policy/engine.py`: the engine is pure and the count
  needs the store.
- The spacing waits instead of refusing: a refusal would cost the model a step for a wait of seconds.
- One public or social click per batch: otherwise a batch of twenty likes would count once.
- Lower defaults than the draft's (20 and 120 public, 40 and 300 social): X cut an unverified account's posts to 50 a
  day in May 2026 (decision 8). The keys sit in `[policy.pace]` (`PaceConfig`), not as five `[policy]` keys.
- `quote`, `alıntı` and the social words count only at the start of a label or of a sentence in it, so that ordinary
  links and offers are not taken for them.
- `rules.js` has no list of flags to change. The campaign page shows no counts per host: the refusal says what the run
  needs to know.
- The typed judge's tests use "Boost" and other labels the new patterns do not know.

**Decisions and limits.**
- No limit is set by default (decision 8: the owner wants the agent free); the first defaults were guesses below X's
  published limits. X's automation rules forbid automated likes and bulk or aggressive following, and warn that
  scripting the website can get an account suspended.
- Only the clicks the policy recognises are counted: a post sent with a keyboard shortcut or through an API from the
  shell is not, and an action that was let through counts even when its click then fails.
- Two runs acting on one site at the same moment can both pass a limit before either is recorded.

**Done when.** Unit tests of the patterns (English, Turkish and Finnish labels; Repost, Quote, Follow, Like and Beğen
flagged; "Get a quote", "Posts you might like" and "Reposted by Dot" not) and of the pace (the windows, the spacing,
what counts). A smoke test on a fixture page in headless Chrome with `public_per_hour = 2`: a batch with a repost and
a follow sent back, two reposts spaced, the third refused in `yolo` with a `pace` policy event, a follow counted
apart. README.

### 18. Skills written for Claude Code, and the X playbook

**Status: done** (`6464881`, PR #12). **Goal.** The owner's Claude Code skills load in Dot as they are, so the X
playbook they already own (`x-twitter-growth`, `baoyu-post-to-x`) is what a campaign on X works from.

**Why.** Both skills were refused: `SkillFrontmatter.metadata` was `dict[str, str]` (`skills/types.py`), and their
frontmatter holds `updated: 2026-03-10` (a YAML date) and a nested `openclaw` map. The Agent Skills specification does
ask for strings there ("a map from string keys to string values", agentskills.io/specification, read on 2026-10-10;
the first draft said it allowed any value), but Claude Code loads these skills, and Dot never read `metadata`.
Reproduced again on 2026-10-10 with `parse_skill` on the owner's folders (read only). `[skills] dirs` is empty, so
`~/.claude/skills` is not read until the owner lists it.

**Design** (as built on 2026-10-10).
- `SkillFrontmatter` keeps the two keys Dot reads, `name` and `description`, with the specification's limits. Every
  other key is ignored whatever it holds: `metadata`, and `license`, `compatibility` and `allowed-tools`, which Dot read
  nowhere either.
- The Skills section of the prompt adds: "A skill written for another agent may name tools you do not have (a
  command-line program, a browser extension, a script runner): do the same steps with your own tools."
- README "Skills and plugins": skills of Claude Code load unchanged; `dirs = ["~/.claude/skills"]` is the owner's line
  to add (open decision 10); the two X skills are then the playbook a campaign on X can work from, within item 17's
  pace.

**Touchpoints.** `skills/types.py`, `agent/prompt.py`, README, `tests/unit/test_skills.py`.

**Changed from the first draft.**
- `metadata` is not typed as `JsonObject`: a YAML date is no JSON value, so that would refuse the same skills. The keys
  Dot does not read are not modelled at all.
- `dotx skills list` already printed the folders left out and why, `dotx doctor`'s skills row already counted them, and
  the comment of `[skills] dirs` already named `~/.claude/skills`: nothing changed there.

**Done when.** A unit test parses a frontmatter with a date and a nested map under `metadata` and a list under
`allowed-tools`; the case that refused a number under `metadata` is gone. `dotx skills list` on a throw-away home with
copies of the two skill folders (symlinks resolved; the copies deleted afterwards) lists both, and the prompt's Skills
section built from that home lists both. README.

### 19. Prompt-cache hygiene for the hosted providers

**Status: done** (`0526a60`, PR #13; smaller than the first draft). **Goal.** On Anthropic and OpenAI the prefix of a
run's prompt stays byte-identical from step to step, so the server's cache pays for the system prompt, the tools and the
history instead of the owner.

**Why.** Found with the Claude provider (log of 2026-10-08): the system prompt carries the time to the minute
(`agent/prompt.py:338`), so two runs never share a cached prefix; `drop_old_images` edits the history at every step
(`agent/loop.py:368`, `agent/compact.py:30-39`), so a screenshot leaving the window rewrites a message in the middle
of the prefix and the cache starts again from there; `cached_tokens` is parsed and dropped, so nobody can see the hit
rate. Anthropic's automatic top-level `cache_control` is already sent (`llm/codec_messages.py:164-165`). The Ollama
cloud models the owner runs today gain nothing from this; a Claude helper or standby (item 15) does.

**Design** (as built on 2026-10-10; what was left out of the first draft, and why, is listed below).
- The system prompt names the day and the time zone ("Today: Saturday 2026-10-10, time zone EEST (UTC+0300)"), never
  the time of day. A stored note after each run's goal gives it ("[system note] It is now 14:02 EEST.", `clock_note`).
  A run builds its system prompt once, so nothing changes within a run; the next run of a thread (a chat's next turn,
  a campaign's next session) now starts with the same bytes on the same day when it lists the same notes from memory,
  and the provider can serve the whole earlier conversation from its cache.
- `dotx stats` already shows the cached tokens per model (item 9).

**Left out of the first draft, and why.**
- Moving every screenshot into one trailing "Current screenshots" message. The trailing message moves as the history
  grows, so its images would be sent uncached at every step; today each screenshot is cached from the step after it
  arrives until it leaves the window, and dropping it costs one rewrite of the steps after it. Neither cost is measured,
  because no request has reached Anthropic yet (decision 5): measure the cached tokens of a run with screenshots on
  Claude with `dotx stats` first.
- Shrinking a tool result in one pass. A result with a screenshot changes when its image leaves the window and again
  when it is shortened; one rewrite would mean dropping images earlier or keeping long results longer. Left for the same
  measurement.

**Touchpoints.** `agent/prompt.py`, `agent/loop.py`, README "Models and providers", `tests/unit/test_run_modes.py`.

**Decisions and limits.**
- The notes from memory in the system prompt are chosen by each run's goal: a chat turn whose message brings other
  notes starts the cache again from the system prompt. Moving them out of the system prompt is a larger change.
- The Ollama models the owner runs today gain nothing from this.

**Done when.** A unit test: the day line of the system prompt has no time of day, and the clock note gives it. The
offline smoke suite and `dotx eval check` clean with the note after every goal. README. The hosted Anthropic test of
`cached_tokens > 0` on a run's second step waits for the owner's key (decision 5).

### 20. An interactive terminal for programs that prompt

**Status: done** (`cdf7ec7`, PR #14). **Goal.** The agent can run a program that asks questions, keep a REPL or a dev
server alive while it works, and answer a prompt, as a person at a terminal would.

**Why.** `shell` runs `zsh -c` with stdin closed and in its own process group (`tools/shell.py:128-137`); nothing in
the code base opened a pseudo-terminal (search for `pty`, `openpty`, `pexpect`, `termios`: none). So `npm create`,
`ssh` with a host-key question, `python -i`, `psql`, interactive installers and a dev server that must stay up for a
browser test were out of reach, and that is most of "any job on this computer" and half of "write code in the
browser".

**Design** (as built on 2026-10-10; what changed from the first draft is listed below).
- A tool group `terminal` (`tools/terminal.py`), acting modes only, one call at a time: `terminal_open(command, cwd)`
  returns the session's name (`t1`) and its first output (it waits up to 2 s); `terminal_send(session, text, wait_s)`
  types the text (a `\n` presses Enter) and returns what the program printed back; `terminal_read(session, wait_s)`
  returns what arrived since the last read after waiting up to `wait_s` (at most 60 s) for something new;
  `terminal_close(session)`. A read returns once the program has printed nothing for 0.3 s, so a burst comes back
  whole. When the program has ended, the result says so with its exit status and the session is closed.
- `terminal/session.py`: `pty.openpty()`, `zsh -c <command>` with the slave as stdin, stdout and stderr, in a session
  of its own whose controlling terminal is the pseudo-terminal (so `ssh` and `sudo`, which ask through `/dev/tty`,
  reach it); the environment of `shell` plus `TERM=dumb` and `PYTHON_BASIC_REPL=1` (the REPL of Python 3.13 wants a
  terminal with cursor control). A reader on the event loop keeps the newest 192 KiB of unread output and says how
  much was left out; escape sequences are removed by one regular expression and line ends become `\n` (no terminal
  emulator). Closing sends SIGTERM to the process group and SIGKILL after a second.
- Sessions belong to a thread: at most `[shell] terminal_sessions = 3` (0 removes the tools), each closed after
  `[shell] terminal_idle_s = 1800` without use (at least 60, so the reaper never closes a session during a read) and
  all of them when the daemon stops. The end of a run closes none: a dev server outlives the run that started it.
- Policy: `terminal_open` is classified like `shell` on its command (`secret_access` too when its working directory is
  protected); `terminal_send` like `shell` on every line it completes, the first one with what was typed on that line
  before, so a command typed in parts is judged whole; text that completes no line is low risk. While the program's
  last line asks for a password, a passphrase, a passcode or a PIN, the text is a `credential` (high risk; the summary
  names the session and its command, never the text): a `yolo` run types it and keeps the record, as for a password
  field (item 10), and only the secret, since a line after it would go unjudged; `ask` and `auto` refuse before the
  gate, as the browser refuses a password field, and the model is told to ask the user to run that step in their own
  terminal.
- The prompt's channel order names `terminal_open` with `shell`; the timeline shows the terminal tools with the
  shell's icon (`icons.js`).

**Changed from the first draft.**
- The session code is `terminal/session.py`, not `shell/terminal.py`: there is no `shell` package.
- `terminal_open` and `terminal_send` return the program's answer, so a question and its answer take one call.
- A password prompt in `ask` and `auto` is refused instead of handed off: an approved hand-off would let the agent
  type the secret, since nobody else can type into its pseudo-terminal.
- The `http.server` check reads the page with httpx from the test, not with `web_fetch`, which refuses localhost unless
  `[web] allow_private_hosts` is set; the first draft's check could not pass as written.
- The output does not mark the run untrusted. Shell output does not either (`TAINTING_KINDS`); the first draft's "like
  shell output" was wrong about `shell`.
- `policy/shell.py` and `timeline.js` did not change: the tools reuse `assess_shell`, and the timeline draws a tool
  step from its name.

**Touchpoints.** new `terminal/session.py`, new `tools/terminal.py`, `tools/groups.py`, `runtime.py`,
`agent/prompt.py`, `config.py`, `defaults.toml`, `icons.js`, README "Tools" and "Safety", the tool-group tests.

**Decisions and limits.**
- No terminal emulator: cursor movement and full-screen programs (`vim`, `htop`) show as garbled text, and the tool
  says so. Line-oriented programs are the target.
- The password prompt is recognised from English words on the program's last line. A prompt worded otherwise is not
  seen, and what is typed at it is judged as a command line.
- The shell guardrail is best-effort here as in `shell`: a line typed into a REPL or a remote shell is judged as if
  zsh ran it.
- What is typed is echoed by the terminal as part of the output, as a person sees it; a program that asks for a secret
  without turning the echo off shows the secret.

**Done when.** Smoke tests (`tests/smoke/test_terminal.py`, no model): `python3 -i` answers an expression and its
session closes when it exits; behind a `read -s` password prompt a secret is refused in `ask`, sent back with a
command after it, and typed in `yolo`, and it shows neither in the action, nor in the answer, nor in the yolo record; a
`python3 -m http.server` session serves a page between calls and stops with `terminal_close`; the session limit and
the idle reaper. Unit tests of the escape-sequence stripping, the password prompt and the typed lines. README.

### 21. A plain chat screen

**Status: done** (`bb89714`, PR #9). **Goal.** The web interface reads like a chat with an agent, as in the reference
the owner sent on 2026-10-10 (OpenAI's Codex handing tasks to a dot named Alfred): Dot's face and name at the head of a
conversation, the owner's messages in one accent colour, delegated runs as small task chips with a status ring, one
composer line, no side panel unless asked for, and the same on a phone.

**Why.** The run page opened with the Details and Screen panels beside the conversation and a head line with the
status, the goal (which the first message under it repeats), the model and the approval mode; a delegated run was a
row with a bar, whose mode-and-model chip broke the line on a phone; the composer of a new chat had two rows of controls.

**Design (no change outside the static files).**
- `theme.js`: both looks start with the side panel closed; the button in the run's head opens it, and the choice is kept.
- `app.css`: `--bubble` and `--on-bubble` (a warm yellow with dark ink in Neutral, the accent tint in Paper) colour the
  owner's bubbles and Dot's face (`avatar()`, `icons.js`).
- The run's head (`views/run.js`): Dot's face and name in the middle with the status badge and the start under them,
  the chips and buttons on the right (on a line of their own on a phone); the goal stays the page's heading for a screen
  reader. While the run works, the core of the face blinks. The run page has no "All runs" link: the sidebar (a drawer
  on a phone) lists the chats.
- Delegated runs (`timeline.js`): the panel's summary is a chip with a status ring (`paintRing`: turning while the run
  works, amber while it waits, green with a check when done, red when failed, dashed when cancelled), the goal, and the
  mode and model in quiet text that gives way first on a short line.
- A new chat (`views/runs.js`): Dot's face and "What should I work on?" above the composer, whose bar is one line
  (attach, mode, model, reasoning, **Options**). Options opens the approval mode, goal mode and keep going, and the
  approval mode in use shows on its button (`approvalsPicker(onPaint)`, `runmodes.js`).
- The top bar (`shell.js`): a live connection is its dot alone; the emergency stop is a red square whose words are in
  its tooltip.

**Decisions and limits.** Dot's answers stay text on the page, not bubbles: reports, tables and code read better at the
full width. The reference's pinned chats and projects have no counterpart in Dot and were not built.

**Done when.** Headless Chrome on a scripted daemon (wide light, wide dark, phone): no horizontal overflow, no "null"
text, no console error; the Options panel with YOLO saved; every status ring. README.

## Open owner decisions

1. Live evals: `dotx eval run --model REF` spends model quota. Run it for the models in use and keep the JSON; it is the
   basis for flipping the defaults below.
2. Defaults that change how the agent behaves and wait for evidence: `[agent] tool_surface` (item 4),
   `verify_answer_paths` (item 5), `resume` (item 6), `lessons` (item 7), `stall_escalation` (item 2),
   `scaffold = "measured"` (item 3).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model, and `dotx
   doctor` warns about it (a warning, not a failure). Done for the owner on 2026-10-10 at their word: their
   `config.toml` sets `review = "anthropic:claude-sonnet-5-5"`, and the warning is gone.
4. From the audit: how long to keep events and screenshots (retention windows), which model compacts (`[models] fast` is
   documented but never read), browser access to localhost and private addresses, CI.
5. The Claude models (`anthropic`), done on 2026-10-10: the owner's key is saved, and the hosted test (`.venv/bin/python
   -m pytest tests/smoke/test_hosted_services.py -m hosted -k anthropic`) passed: `claude-haiku-5-5` is listed, a tool
   round trip replays its signed thinking, a screenshot is read.
6. Standing goals (item 12), decided by the owner on 2026-10-09: the composer switch "Keep going until done" is off by
   default, a campaign stops only when the owner stops it, and the proposed numbers stay (`campaign_gap_minutes = 60`,
   `campaign_wait_hours = 24`, `campaign_patience_sessions = 3`, `campaign_max_sessions = 0`, and item 11's
   `run_max_minutes = 0`, `run_max_tokens = 0`).
7. `yolo` and password managers (item 10), decided by the owner on 2026-10-09: `yolo` types secure fields and one-time
   codes; password-manager apps stay refused in every mode.
8. Pace limits (item 17), decided by the owner on 2026-10-10: no limit, the agent is free to post, follow and like as
   its task needs. Every `[policy.pace]` default is 0 and the prompt no longer tells the agent to keep to a platform's
   rules; a limit set in `config.toml` still holds in every mode. What X publishes, as search results quoted its Help
   Center on 2026-10-10 (help.x.com answered HTTP 403 to a direct read): an unverified account may send 50 posts and 200
   replies a day, 500 messages and 400 follows; its automation rules forbid automated likes and bulk or aggressive
   following, and scripting the website can get an account suspended.
9. TypeSafe (item 16), done on 2026-10-10: the owner saved `TYPESAFE_API_KEY`; the thresholds were set on live answers
   (0.45 for clicks, 0.15 for claims, model pinned to `jev-1.13.0`, PR #18). The owner keeps it off: in `yolo` its
   flags ask nothing, while every judged click waits for its request and a wrong flag can split a batch; its one gain
   there is refusing a clearly false campaign claim before the goal checker's call. `[judge] provider = "typesafe"` in
   `config.toml` turns it on.
10. Skills (item 18), done on 2026-10-10 at the owner's word: the owner's `config.toml` lists the six skill folders of
    `~/.claude/skills` that stand alone (the two X skills, `find-skills`, `frontend-design`, `ui-ux-pro-max`,
    `rust-best-practices`), not the whole folder, which would also bring its `superpowers` bundle (process skills that
    tell the model to brainstorm, plan and ask before it acts) and its `synced` skills (claude.ai's, for tools Dot does
    not have). Add `"~/.claude/skills"` itself to take them all.
11. Helpers (item 15), done on 2026-10-10 at the owner's word: their `config.toml` sets `helpers =
    ["anthropic:claude-opus-5-5"]` and `standby = ["anthropic:claude-sonnet-5-5"]`, and `dotx doctor` says both are
    ready. Both spend Anthropic tokens: a helper on a consult or a delegated part, a standby model on the rest of a run
    whose main model hit a quota.
12. Only you can check live: the X composer with item 14's `type` and `insert`, and a `yolo` run that reads a
    one-time code from your webmail and types it (item 10).
13. Speed knobs that wait for item 9's numbers: `[browser] settle_quiet_ms` (120 or 60; item 13 has the timings of
    both; a longer window also shows more of what a page fetches after a scroll), `[desktop] settle_quiet_ms` (120: a
    change later than it is not waited for), `[desktop] screenshot_max_edge` (1920 or 1366), `[desktop]
    paste_threshold_chars` (40: typing costs 8 ms a character, a paste about 0.25 s), `[agent] tool_output_chars` and
    `keep_recent_tool_results`, besides `tool_surface = "grouped"` (decision 2) and the compaction model (decision 4,
    and `agent/loop.py:290-298` records the decision that a run compacts with its own model).
14. Research (not an item of part 2): a `research` run can save nothing, not even a note (`write_file` and `memory_save`
    are acting-only, `tools/base.py:16-17`), and DuckDuckGo's bot check throttles long research runs (README "Status and
    known limits"). Decided by the owner on 2026-10-09: no change for now; note saving and the hosted Ollama search
    (`OLLAMA_API_KEY`) as the default backend come later.
15. The Claude GitHub app, decided by the owner on 2026-10-10: it is not used; subagents in the session review each
    branch (house rules, Git). It had reviewed nothing since PR #1 ("Code review skipped": no extra usage paid for it).
16. The review model has no standby (item 15): with `[models] review` on the provider of `main` (the default), a run in
    goal mode or with `auto` approvals that moved to its standby fails its goal check or its approvals while that
    provider cannot answer. Settled for the owner by decision 3: their review model is on Anthropic, their main model on
    Ollama. The default stays as it is.

## Log

- 2026-10-08: roadmap written from the owner's go-ahead for all eight items, in this order. Nothing implemented yet.
- 2026-10-08: item 1 done (`a83cdcc`). `dotx eval check` replays 25 reference solutions through the real loop in about
  4 s; `dotx stats` ran against the real history (read-only counts). Real history now: 39 runs in 365 days, 35 on
  `ollama:deepseek-v4.1-flash:cloud` and 4 on `ollama:gemma4:cloud`; tool calls fail about 2 to 3 % of the time; 342
  compactions in 35 runs, so compaction is frequent (item 3 and 4 will want that number).
- 2026-10-08: item 2 done (`5f6cb29`). The watchdog with four detectors and the five-rung ladder, off by default
  (`stall_escalation`); with it off, the old guard plus decay and a stop result that says what was tried. Unit tests,
  `dotx eval check` and the offline smoke suite clean except one model-driven test,
  `test_a_live_stream_carries_the_runs_the_run_delegated_to`: `gpt-oss:20b-cloud` called `delegate` three times although
  told once (no stall event in that run); it passed when rerun.
- 2026-10-08: item 3 done (`de2cc1c`). Budget from the window, tiers, reminders, `dotx models profile`, and compaction
  sized to the budget: a 24 k window compacts 9 times in 40 steps, largest prompt 17 811 of 18 000; a 32 k window 3
  times, 24 561 of 24 576; the default budget of 48 000 never compacts in that run, as before. Unit tests (389),
  `dotx eval check`, the offline smoke suite (142) and the timeline in headless Chrome clean; no live model run. Found:
  every `dotx` command logs to the owner's `~/Library/Logs/Dot/dotx.log` unless `DOT_LOG_DIR` is set, so the earlier
  `dotx eval check` runs of this work added lines there (nothing else); the house rule says so now.
- 2026-10-08: item 4 done (`d89d634`). Tool groups, `use_tools` and `[agent] tool_surface` (default `full`, which
  changes nothing). Measured again: a task run offers 46 tools of 9 816 tokens at every step (the first draft said
  8.2 k); grouped, its first step takes 7 190 tokens of system prompt and tools instead of 13 132, a research run's
  6 403 instead of 8 651. Unit tests (412), `dotx eval check`, the offline smoke suite (145) and the timeline in
  headless Chrome clean; no live model run. One model-driven smoke test failed once and passed when rerun:
  `test_a_shell_command_waits_for_the_approval_of_the_user_and_runs_once_it_is_given`, where `gpt-oss:20b-cloud`
  called `ask_user` instead of `shell` (with the default `full` its tools and prompt are as before). Found: a delegated
  run's model that calls `delegate` or `ask_user` by name now gets "unknown tool"; before, the call reached the tool,
  and the manager refused a nested `delegate`.
- 2026-10-08: item 5 done (`e2051a5`). The facts the harness measured go to the goal check in a FACTS block;
  `[agent] verify_answer_paths` (default off) sends back once an answer that names paths that do not exist;
  `dotx doctor` warns while the review model is the main model, as it is by default. Unit tests (431),
  `dotx eval check`, the offline smoke suite (148) and the timeline in headless Chrome clean. Live: only the two
  goal-checker smoke tests (two calls of `ollama:minimax-m2.7:cloud`, both passed). One model-driven smoke test failed
  once and passed when rerun: `test_a_run_reads_a_file_through_a_delegated_run`, where `gpt-oss:20b-cloud` delegated a
  second time ("Same but give me the content precisely") and the test expects one child run; the run itself ended done
  with the right content. Found: the README does not describe the send-back of a plan with open steps at the end of a
  run (`plan_reminder`), which runs before the path check.
- 2026-10-08: item 6 done (`c3688e6`, `b6dd42f`). A run the daemon cut off goes on in a new run of its thread, by hand
  (Continue, `dotx resume`, `POST /api/runs/{id}/resume`) or, with `[agent] resume = "safe"` (default off), by itself
  when the daemon starts, for a run of the web UI cut off in calls that only read. Unit tests (436), `dotx eval check`
  and the offline smoke suite (151, no flake) clean; the web UI checked in headless Chrome on a throw-away daemon after
  a real crash, and `dotx resume` on a throw-away home; no live model run. Found and fixed: a run that a crashed daemon
  left behind never got its `run_finished` event, so its page said "Connection lost. Reconnecting…" for good (old; runs
  closed that way before get the event at the next start); a call that a crash cut off kept its spinner for good (old);
  the detail of a run could show a run that ended in between as neither active nor ended; two requests to resume one
  run at once could start two runs. Found, not changed: the conversation shows the finished calls of an interrupted step
  as interrupted too (`repair_history`, old); a run of `dotx run` that a starting daemon marks as cut off can be
  resumed by hand while it still works in its own process.
- 2026-10-08: item 7 done (`a297764`, review fixes `4db2539`). A task or research run that reached its goal after
  something went wrong is followed by one call of the review model, which proposes at most three one-sentence lessons;
  a filter drops what is not know-how or is known already; `[agent] lessons` (default off) leaves them on the run's
  page (`propose`) or saves them as notes tagged `lesson` (`auto`, which only proposes after a run that read untrusted
  content). Unit tests (467), `dotx eval check` and the offline smoke suite (158, no flake) clean; the web UI checked in
  headless Chrome on a throw-away daemon; no live model run. Found and fixed before the commit: the small-screen rule
  hid the words of both lesson buttons. Found by a review after it and fixed: two decisions on one lesson at once both
  passed (reproduced: two saves, two 200s), and `auto` could save a lesson the owner had just dismissed; an error other
  than a provider's (a missing API key) went unreported; Ctrl-C while `dotx run` waited for the lessons ended it with
  130 although the run was done; an older answer could repaint the lessons on the page; secrets were masked after a
  digest or a fact was cut, so the part of a key left at the cut stayed readable.
- 2026-10-08: item 8 done (`c9bb85d`). Before `write_file` or `edit_file` changes a file, the run notes the change in
  its journal under `<home>/undo/`, with a copy of what was there at its first change to the file; `dotx undo RUN_ID`
  shows, and with `--apply` takes back, the file changes of a run and of the runs it delegated to (`--force` for files
  changed since); `dotx prune --apply` deletes the journals older than `[agent] undo_keep_days`. Unit tests (475),
  `dotx eval check` and the offline smoke suite (159) clean except one model-driven test,
  `test_a_live_stream_carries_the_runs_the_run_delegated_to` (as in item 2: `gpt-oss:20b-cloud` delegated more than
  once, while the Ollama cloud reported errors inside the stream), which passed when rerun; the CLI checked on a
  throw-away home; no live model run. Found, not changed: two delegated runs that work at the same time and write one
  file can leave in it the change noted first (the plan calls that file a conflict); a `yolo` run can rewrite a
  journal, as it can the configuration and the database.
- 2026-10-08: after the eight items, on the owner's request: the Claude models over the Anthropic API (`11dc6a0`).
  Provider `anthropic` (`anthropic:<model>`, key `ANTHROPIC_API_KEY`) on the Messages codec of OpenCode Go's messages
  route, with what only Anthropic takes behind `MessagesOptions` (adaptive thinking at the reasoning level, the prompt
  cache, the model's own output limit); a model's traits are read from the Models API. Unit tests (475), the offline
  wire tests (bodies, traits, errors), `dotx eval check` and the offline smoke suite (161) clean except
  `test_a_live_stream_carries_the_runs_the_run_delegated_to` (model-driven, as in items 2 and 8), which passed when
  rerun; no hosted run, as no `ANTHROPIC_API_KEY` is set here (decision 5). Found, not changed: `drop_old_images` edits
  the history at every step, so on the models that bind thinking to its context the thinking sent before the edit is
  dropped (the API lists it in `input_transformations`, which Dot does not log) and the prompt cache starts again
  there; the system prompt carries the time to the minute, so the runs of a chat never share a cached prompt; the 4.5
  generation runs without thinking (it has no adaptive thinking, and budget thinking is not sent); the documentation
  gives a 429 for a reached spend limit no mark of its own, so Dot retries it like a rate limit before it fails.
- 2026-10-08: on the owner's request: Settings › API keys (`361cb39`). The web UI saves, replaces, checks and removes
  the key of each provider in `<home>/.env` (the file and the function of `dotx auth set`) and shows where the key in
  use comes from and its last four characters, never the key; a saved or removed key reaches a running daemon at the
  provider's next request (`ModelRouter.retire`). Unit tests (475), the offline smoke suite (163) clean except
  `test_a_run_reads_a_file_through_a_delegated_run` (model-driven, as in item 5), which passed when rerun; the web UI
  test in headless Chrome; the page checked on a throw-away daemon in both looks, light and dark, wide and at phone
  width, with keys under variable names no environment or Keychain has (no key of the owner was read; the checks of the
  fake keys got a 401 from api.anthropic.com and opencode.ai). The owner's daemon started before item 1: it serves the
  new page from disk, but the page cannot load its keys until a restart loads the new code. Found and fixed: the 422
  answer of a refused request repeated the refused value, which may be a key. Found, not changed: saving a key rewrites
  the whole `.env` (comments and `export` prefixes in it are lost) and not atomically, as `dotx auth set` always did.
- 2026-10-08: design pass after the keys tab (`42a0d75`). The Models tab names the providers (OpenAI, Anthropic,
  OpenCode Go, Ollama) and says "no key" or "key refused" with a link to Settings › API keys (`ProviderModels.problem`);
  links in notes are underlined; on a phone the composer's attach button no longer sits alone in the middle. Checked on
  a scripted throw-away daemon in both themes and skins at 1280 and 390 px. Python changed: the owner's daemon needs a
  restart for the key states.
- 2026-10-09: part 2 planned (items 9 to 20) from the owner's second ask and four read-only subagent audits (browser
  and desktop latency, the loop and model round trips, autonomy and guardrails, evals and capabilities), their cited
  lines re-read before use. Nothing implemented. Ground checked: `ruff format --check` (254 files), `ruff check`,
  `mypy` (187 files) and 475 unit tests clean in 5.1 s with `DOT_HOME` and `DOT_LOG_DIR` in a temp directory. Found
  while planning, not changed: the owner's two X skills fail to load (`metadata` typing, item 18); `browser_act`
  results do not taint the run although desktop results do (`tools/browser.py:289`, `policy/types.py:73-75`);
  `check_drift` in `desktop/actions.py:145-159` has no caller; the comment at `desktop/screen.py:75-77` says
  `windows_on_screen` takes 0.2 ms, measured 3 ms. Handed to the next agent; the owner's verbatim ask is in
  `PLAN.md`, part 8.
- 2026-10-09: the repository is on GitHub at `senoldogann/Dot` from here on (the folder had git since `8848d69`
  and no remote). The house rules move from local-only commits to one branch and pull request per item, reviews,
  squash merges and a Linear mirror of the status table; nothing else changed.
- 2026-10-09: item 9 done. Every model call of a run leaves a `model_call` event (purpose, model, seconds, tokens,
  cached tokens, error) and its tokens in the run's totals; a step's `assistant` event has its seconds, a tool result
  the seconds it waited at the gate; `dotx stats` prints a Time table per model and a Tools table, the eval score
  seconds per pass; the run page shows the model's part of a group's time. The design was corrected before building:
  the call sites name their run (`CallOrigin`), as the review keys and the lessons after a run's end made a run
  resolved from the session key wrong. Unit tests (483), `dotx eval check` and the offline smoke suite (179) clean,
  with the tests that call a cloud model through Ollama left out. One smoke run left them in by mistake and ran 10 of
  them on the cloud quota before it was stopped: 9 passed, and `test_a_run_reads_a_file_through_a_delegated_run`
  failed because `gpt-oss:20b-cloud` called `delegate` twice where its goal says once; their events showed the new
  accounting right (the run's tokens are the sum of its steps' calls). The review fixed: the eval token ceiling
  counted a call twice after the supervisor's subscription dropped events; a recorder that failed replaced the error
  of the call, and errors that are not `DotError` were not recorded; cut-off steps entered the step median and p90;
  tied tools had no order; the run page put a step's model time into the wrong group; an aside's tokens reached Details
  only after a reload. Found and left, older than this item: an aside answered after its run ended is not shown on the
  run page; the eval supervisor cannot decline a question whose event its subscription dropped. Baseline in the item:
  25 scripted tasks in 0.61 s, median 0.01 s. A run's tokens now count its side calls, so the same run shows more than
  before.
- 2026-10-09: item 9 merged as `4741bed` (PR #1, squash). The Claude GitHub app is installed and answered the PR, but
  skipped the review: the owner's organization has no extra usage to pay for it (see "Open owner decisions"). Item 10
  started on `item-10-yolo-asks-nobody`.
- 2026-10-09: item 10 done. A `yolo` run types the password, one-time-code and card fields it meets, each one a
  `credential` or `financial` record in the timeline; `ask` and `auto` refuse them as before. With `[agent] questions =
  "leave"` (the default) a `yolo`, scheduled or eval run leaves its question and goes on; the run page lists it under
  "Dot asked you", and the answer carries the work on in the thread (a steer, or a new run with the asking run's
  settings). A step with tool calls resets the count of text replies, so a status line mid-run no longer ends a task
  run; `finish` takes `needs_from_user`. Unit tests (495), `dotx eval check`, the offline smoke suite (187) and the run
  page on a throw-away daemon clean; no cloud model called. Fixed on the way: a smoke test of item 6 took a `yolo` run's
  question to wait; the README said desktop screenshots black out secure fields. Found and left: `press` of a character
  key is not checked against secret fields; a follow-up from the web UI on a thread a `dotx run` works on starts a
  second run. Not run: the desktop's `yolo` path (it moves the pointer) and the live webmail code flow (the owner's,
  decision 12).
- 2026-10-09: item 10 review fixes (/code-review at max effort: eleven finders, a verifier per candidate and a sweep).
  An answer sent during the step that ends a run is read before the run ends, and one the run never read stays in its
  thread (it was lost while its question counted as answered); the desktop never controls a password manager or Keychain
  Access (a `yolo` run could drive one and type into its fields); the secret-field check judges the element that takes
  the text (in `ask`, a fill of a label, a type or a press at a focused field and a press without a target typed a
  code); an owner's hand-off rule stops a `yolo` run at secrets, money and credentials too; a secure field's value is
  not read back; the browser lines of the prompt no longer send a log-in to the user in `yolo`; the run page follows a
  new run only after the last open question is answered, never after a refusal, and drops a question answered elsewhere;
  an empty answer is refused; a run that only leaves questions between text replies ends; the needs of a finish that the
  goal check sent back are not shown; the approval card no longer prints "null". Unit tests (515), `dotx eval check`,
  the offline smoke suite (196) and the run page on a throw-away daemon clean; no cloud model called. Left as limits: a
  left question is seen only on its run's page; a killed `dotx run` keeps answers on its thread refused until the daemon
  restarts; an answer that starts a run is its goal, as a follow-up is. Found and left: a paste through a desktop Edit
  menu, `desktop_open_app` opening a password manager before it refuses it, the composer's reasoning level for a model
  without a choice of levels.
- 2026-10-09: item 10 merged as `eacd180` (PR #2, squash); the Claude GitHub app skipped its review again (decision 15).
  Item 11 started on `item-11-open-ended-goals`.
- 2026-10-09: item 10 follow-ups merged as `92b7dfa` (PR #3, squash): two findings of the item's review, applied at the
  owner's request after the merge. `dotx run` (and `dotx chat`, `dotx resume`) stops its run on SIGHUP and SIGTERM as on
  Ctrl-C, so a closed terminal no longer leaves the run `running` and the answers on its thread refused until the daemon
  restarts (a run killed outright still does); an answer that starts a run puts the asking run's goal first, so that
  run's goal check, the task its continuity note pins and its title keep the task. Unit tests (515), `dotx eval check`
  and the offline smoke suite (198, with the new signal test, which fails without the handlers) clean; no cloud model
  called. The paste through a desktop Edit menu stays found and left.
- 2026-10-09: item 11 done. A goal that names an outcome ("as many as", "grow", "find a way") is held to measurable
  targets: task and research runs are told to record them with `update_plan` (`targets`) before they act, to measure
  after each round and change the approach when a round brought nothing, and to finish only at the targets, at a need
  only the owner can meet or at the run's limit, then with the numbers and what is missing. The targets stay with the
  plan, show on the run page and in `dotx run`, and reach every note that carries the plan, a resumed run and the goal
  check, which judges them as the run's own reading of the goal. `[agent] run_max_minutes` and `run_max_tokens` (0, no
  limit, by default) bound a task or research run together with the runs it delegated to: past a limit the run is told
  so, may only finish or close its plan, and ends after that reply; one that does not finish ends with the new ending
  `limit`. Eval runs keep their task's own cut; `dotx eval` gets `collect-contacts`. Unit tests (524), `dotx eval
  check` (26), the offline smoke suite (214) and the run page on a throw-away daemon clean. Fixed on the way: the head
  of a group of steps went out of view on a phone.
- 2026-10-09: item 11 review fixes (/code-review at max effort, fifteen findings verified). Past its limit a run ends
  with the reply it gives: a finish is not sent back by the plan reminder, the check of the answer's paths or a message
  sent during that step (an answer stays in its thread); goal mode checks it once, without a way back, and the timeline
  and `dotx run` say the run stops at its limit; a reply that does not finish is kept in the `limit` result. The limit
  note asks for the plan tools before `finish` in that reply. An `update_plan` with empty `targets` keeps the targets;
  the goal checker's data rule names the targets; a limit of `inf` or `nan` is refused; a plan without targets no longer
  shows "null" on the run page; `collect-contacts` checks each address once and a name in every row; the README and
  `defaults.toml` say the minutes are wall time with the waits for the owner and that each run has limits of its own.
  Unit tests (524), `dotx eval check` (26), the offline smoke suite (203) and the run page on a throw-away daemon clean.
  Left: the refused calls past the limit fill "What it did last"; the tokens of a run and the runs it delegated to are
  summed at each step (about 2 ms at 50,000 events, and only with a limit set). Found while checking: the smoke test of
  a file attached in the web UI had no `requires_model` guard, so the offline smoke runs of items 9 and 10 called
  `gpt-oss:20b-cloud` through it (it has the guard now); the first three smoke runs of this item left out only the
  guarded tests and not those marked `live`, so the eight `live` wire tests called `gpt-oss:20b-cloud` and
  `gemma4:cloud` as well (53 requests through Ollama in all); the review's agents ran the whole suite with its
  cloud-model tests twice.
- 2026-10-09: item 11 merged as `12ad395` (PR #4, squash); the Claude GitHub app skipped its review again (decision 15).
  Item 12 started on `item-12-campaigns`.
- 2026-10-09: item 12 done. *Keep going until done* (the composer's switch for a task, `"standing": true`, `dotx run
  --standing`) makes the goal a campaign: sessions, the first in the owner's conversation and every later one in a
  thread of its own with a brief as its first message, until the goal checker accepts the evidence of
  `campaign_achieved` or the owner stops it. The sessions keep a ledger (`campaign_note`), record targets
  (`campaign_targets`) and may plan the next session (`campaign_schedule`); otherwise it starts after
  `campaign_gap_minutes`, or after `campaign_wait_hours` when a session ended needing answers to questions it left. The
  scheduler starts due sessions however late, the daemon plans one for every active campaign when it starts, a session
  is never resumed as a run, and an achieved campaign notifies the owner. Campaigns in the sidebar, a page per campaign,
  a chip on a session's run page, `GET/POST /api/campaigns…` and `dotx campaign`. The design was checked against the
  code first (the first draft's `ScheduleRecord` per campaign would have skipped overdue sessions).
- 2026-10-09: item 12 review fixes (/code-review at max effort: eleven finders; the skill's verify stage hit the session
  limit, so the 45 findings were checked against the code here). The answer to a question a session left reaches that
  session, or else waits for the next brief: it could go to another run on the campaign's conversation, or start a run
  outside the campaign when the session was just ending. The campaign's page keeps the answer cards across its reloads
  (a half-typed answer was lost every 15 s while a session worked). `campaign_targets`, `campaign_note` and
  `campaign_schedule` carry the `persistence` flag; a moment planned with `campaign_schedule` keeps the rhythm; the
  owner's emergency stop stops the campaign; after the daemon died during a session the next one waits the gap. The
  checker's reason is masked and shortened and the ledger digest capped; a `blocked` verdict tells the session to ask
  the owner. Briefs carry the paths of the files attached to the goal and the newest progress, and keep a number's
  digits; the owner's answers are instructions of later sessions; the prompt says what success means for a session.
  `dotx campaign stop` points at the session's run page. Shared helpers instead of copies, dead code dropped. Unit
  tests (531), the offline smoke suite (209, no cloud request), eight mutations each caught by a smoke test, and the
  pages on a scripted throw-away daemon at four sizes clean. Left: deleting the campaign's conversation deletes its
  first session (see the item's limits); a CLI session across a daemon restart, as for any `dotx run`; query costs,
  indexed and bounded.
- 2026-10-09: item 12 merged as `c915ab2` (PR #5, squash); the Claude GitHub app skipped its review again (decision 15).
  Item 13 started on `item-13-faster-steps`.
- 2026-10-09: item 13 done. `browser_scroll` no longer sleeps 150 ms after the wheel (a 600 px scroll 169 ms instead
  of 330); a batch that closes its tab settles the new current tab once; `browser_wait_text` says after how long the
  text appeared and shows what changed, and when it does not come the call fails and still shows the page. On the
  desktop the fixed 350 ms settle after a batch waits for the application's accessibility announcements to stop
  (`ax.wait_until_quiet`, `[desktop] settle_quiet_ms`): a click act 0.34 s instead of 0.56, five clicks 1.32 s instead
  of 1.53. A `type` text longer than `paste_threshold_chars` goes in through the clipboard (200 characters 0.92 s
  instead of about 2.3 typed). Four points of the first draft changed after probes; the section says why.
- 2026-10-09: item 13 review fixes (/code-review at max effort: ten finders; the verify stage hit the session limit
  again, so the 22 findings were checked against the code here). A wait that timed out is marked untrusted (the gate
  counts no failed result as a read, and the page is in it), takes the first visible match (a hidden copy held it until
  the timeout) and reads the page without a settle that could outlast it. The desktop settle uses one callback for every
  wait (one per wait leaked about 6 KB), gives every call to the application `settle_ms` and stops the removals at the
  first unanswered one (a hung application held the desktop worker about 30 s), opens the quiet window after the
  registration and listens to 28 announcements instead of 15. No paste into a field for a credential or a payment, nor
  of a key-like text; `type` uses the paste path of `paste`; the step schema says when typing pastes; the desktop
  clipboard tests put the whole clipboard back. A second review of these fixes found two more: after a timeout a
  navigation still pending commits while the page is read, so the URL rules are checked again afterwards (a blocked host
  was shown), and registering stops at `settle_ms`. Unit tests (532), the browser smoke file (30), the offline smoke
  suite (216, no cloud request), the desktop smoke file (16, with the owner away) and four mutations each caught by a
  test. Left: what a page fetches after a scroll and shows 145-270 ms later (open decision 13). Found, not changed
  (older than this item): the paste path puts the clipboard back after a fixed 250 ms, which a slow application may
  outlast; the URL rules are checked after loads, `act`, `back` and `browser_wait_text` only, not when `snapshot`,
  `find`, `links`, `read_text`, `screenshot`, `scroll`, `switch_tab`, `close_tab` or `upload` read a page that moved on
  by itself.
- 2026-10-09: item 13 merged as `a14f21a` (PR #6, squash); the Claude GitHub app skipped its review again (decision 15).
  Item 14 started on `item-14-rich-editors`.
- 2026-10-10: item 14 done. In a rich editor (a contenteditable area such as X's composer) `fill`, `type` and `insert`
  wait until the editor has settled before the text goes in, and fail when it did not take the text instead of reporting
  done; probes showed that Playwright's `fill` already inserts the text as one input and that an editor ready 30 ms
  after the focus loses the text whatever the step, which explains both of X's symptoms. New steps: `insert` (the text
  at once, without key events) and `drag` (`ref` onto `to_ref`, `locator.drag_to` with ten moves). Three points of the
  first draft changed after probes; the section says which.
- 2026-10-10: item 14 review fixes (/code-review at max effort: ten finder angles, 26 candidates, 15 verified, all
  confirmed and fixed). The secret check of the field that has the focus runs after the editor's wait, right before the
  text goes in (a page that moved the focus during the wait got the text into a code field), and `insert` outside an
  editor goes only into a text field that has the focus (with the caret left in a password field, an `insert` at a
  button wrote into it). `fill` into an editor is Playwright's `fill` again after the wait, which brings back a
  paragraph inside an editor without a role, the wait until the element is editable, and a select-all that does not go
  through the page's keys. Form controls inside an editor and documents in designMode keep Playwright's steps (the first
  failed although the text went in, the second threw). The check compares the editor's text instead of counting any
  mutation, waits up to 500 ms for a change and returns at once when there is one (an editor step 199-206 ms instead of
  254-262), takes a `fill` that leaves the element holding its text, counts a navigation after the write as done, and
  says when the editor left the page that it is not known whether it took the text. `type` and `insert` need a text that
  is not empty. A drop onto a secret field is refused, a failed or cancelled drag releases the mouse button (later
  clicks were reported done and never arrived), and the element a drag drops onto is judged by its name whatever its
  role. A second review of these fixes found that skipping the wait for an editor that had the focus already let a batch
  that clicks into the editor and then types write too early; the wait runs before every write into an editor. Unit
  tests (539), the browser and secret-field smoke files (48), the offline smoke suite (228, no cloud request), 14
  mutations of the fixes each caught by a test. Left (limits in the section): `press` into an editor that is not ready
  yet still loses the key; an editor that shows the text after more than 0.5 s; a widget whose text changes inside the
  editor; a `type` started before the editor is ready.
- 2026-10-10: item 14 merged as `37808d5` (PR #7, squash); the Claude GitHub app skipped its review again (decision 15).
- 2026-10-10: item 15 done. `[models] helpers` and `[models] standby`: a run asks a helper with `consult` (no tools,
  the answer fenced as advice, at most `consult_max_per_run` questions), runs a part of the work on one with
  `delegate(model)`, and moves to a standby model from the same step when its own model answers with a quota,
  rate-limit or key error or has no key, with a note to the model and the switch in the timeline. The router pauses a
  model after such an error; the roster (keys and pauses) picks the helper or the standby. `dotx doctor` rows,
  timeline notes, `dotx run` lines, README. The section was checked against the code first; seven points changed.
- 2026-10-10: item 15 review fixes (/code-review at max effort: 15 findings verified, all confirmed; 13 fixed, 2 left
  as limits or an open decision). Pauses are per model (a paused Ollama cloud model no longer holds back a local one,
  and another model's answer no longer ends a pause), at most a day long (an endless `Retry-After` broke the
  end-of-pause text), and a new key ends them. A run takes each standby model once at most, so two spent providers
  cannot hand it back and forth; a standby that cannot take the run over is passed over, and the run fails saying why
  when every one is. The run's level follows the switch, so a follow-up on the run page passes the effort check, and a
  step asked again keeps a re-plan's tool restriction. Anthropic's HTTP 400 for a spent balance is a quota error.
  `consult` is offered neither to a delegated run nor for the run's own model, and the Helpers section comes with
  `delegate` alone; evals clear the helper and standby lists. Left: the review model has no standby (open decision
  16), and a timeout does not switch. Unit tests (548), the helper smoke file (11), the offline smoke suite (239, no
  cloud request), 31 mutations each caught by a test (each with a bytecode cache of its own: two mutants of the same
  size written in the same second had shared a stale `.pyc`).
  Item 15 started on `item-15-helper-models`.
- 2026-10-10: item 15 merged as `530ac62` (PR #8, squash); the Claude GitHub app skipped its review again (decision 15).
  The owner asked for a plain chat screen like OpenAI's dot in Codex (a screenshot) and for at most five subagents per
  task; the screen is item 21, done before 16 to 20. Item 21 started on `item-21-chat-screen`.
- 2026-10-10: item 21 done. A run opens under Dot's face and name with its state and start; the goal is the first
  message, in the yellow bubble of the owner's messages; a delegated run is a chip with a status ring; the Details
  panel stays closed until asked for. A new chat is Dot's face and a composer with one line of options (approvals,
  goal mode and keep going behind Options, the approval mode on its button). The connection is a dot and the emergency
  stop a red square. Static files only. Unit tests (548), the offline smoke suite (239, no cloud request), headless
  Chrome on a scripted daemon (wide light and dark, phone; Options with YOLO; every ring): no overflow, no console
  error. The review subagent was stopped before it reported; the diff was reviewed in the main session.
- 2026-10-10: item 21 merged as `bb89714` (PR #9, squash); the Claude GitHub app skipped its review again (decision
  15). The owner said to go on with the remaining items and to build only what each needs. Item 16 started on
  `item-16-typed-judgments`.
- 2026-10-10: item 16 done. With `[judge] provider = "typesafe"` (off by default), TypeSafe's System One API is asked
  about a browser click whose label no pattern knows, which then gets the flag the judge finds ("Repost" as a public
  post), and about a campaign's claim before the goal checker; a judge that cannot answer pauses for
  `helper_cooldown_s` with an event, and the patterns decide alone. The design moved where the code showed it wrong
  (the section lists what changed). Reviewed in the main session: the hosted test now asks the judge's own questions,
  and a page's query and fragment are not sent. Unit tests (550), the offline smoke suite (242, the judge's three on a
  stand-in for the API and headless Chrome; no cloud request). No request reached TypeSafe: the key, the hosted test
  and the count are the owner's (decision 9).
- 2026-10-10: item 16 merged as `a5516f7` (PR #10, squash); the Claude GitHub app skipped its review again (decision
  15). Item 17 started on `item-17-pacing`.
- 2026-10-10: item 17 done. Repost, Quote, Follow, Like and their Turkish and Finnish labels are flagged (the new flag
  `social` for follows and likes); public and social browser actions keep a pace per site in the gate (10 and 50 public
  an hour and a day, 20 and 100 social, public ones 20 s apart), which no approval mode waives, and a batch takes one
  such click at most. The defaults are lower than the draft's after X's published limits (decision 8; help.x.com
  refused a direct read, so they come from search results). Reviewed in the main session. Unit tests (553), the
  offline smoke suite (243, with the pace on a fixture page in headless Chrome; no cloud request).
- 2026-10-10: item 17 merged as `d50a41e` (PR #11, squash). The Claude GitHub app did not answer on it, and the owner
  said its review is gone (the limit is spent): PRs no longer wait for it (decision 15, house rules). Item 18 started on
  `item-18-skills`.
- 2026-10-10: item 18 done. Skills written for Claude Code load: the frontmatter model keeps only `name` and
  `description`, so the owner's two X skills (a date and a nested map under `metadata`) are no longer left out; the
  prompt tells the model to do a skill's steps with its own tools when the skill names tools Dot lacks. Checked on a
  throw-away home with copies of the two folders: `dotx skills list` and the prompt's Skills section list both. Also
  fixed: item 17's smoke test measured the spacing from the end of the first click and failed once in the full suite
  (0.88 s); it now reads the two recorded times. Unit tests (553), the offline smoke suite (243, no cloud request).
- 2026-10-10: item 18 merged as `6464881` (PR #12, squash). Item 19 started on `item-19-prompt-cache`.
- 2026-10-10: item 19 done, smaller than planned. The system prompt names the day and the time zone; the time of day is
  a stored note after each goal, so the next run of a thread can start from the provider's cache. The trailing message
  of screenshots and the one-pass shrink were left out: neither is measured, and the first could cost more than it
  saves (the section says why). Unit tests (554), the offline smoke suite (243, no cloud request), `dotx eval check`.
- 2026-10-10: item 19 merged as `0526a60` (PR #13, squash). Item 20 started on `item-20-terminal`.
- 2026-10-10: item 20 done. A tool group `terminal` runs a program on a pseudo-terminal of its own (`terminal_open`,
  `terminal_send`, `terminal_read`, `terminal_close`): a REPL, an installer that asks questions, a server that stays up
  between calls. Each line typed is judged as a shell command; a password prompt takes a secret only in `yolo` (`ask`
  and `auto` refuse, since nobody else can type into the session). Three sessions per chat, closed after 30 idle
  minutes. Reviewed in the main session. Unit tests (558), the offline smoke suite (247, no cloud request).
- 2026-10-10: item 20 merged as `cdf7ec7` (PR #14, squash). Every item of part 2 is done; what waits for the owner
  is in "Open owner decisions".
- 2026-10-10: decision 8 answered by the owner: no pace limit, the agent is free. Every `[policy.pace]` default is 0,
  and the Browser bullet of the prompt no longer tells the agent to keep to a platform's rules; a limit set in
  `config.toml` still holds in every mode, and a `browser_act` still takes one public or social click. Decisions 6, 7
  and 14, which the owner answered on 2026-10-09, are marked so; decision 10 is done (six skill folders of
  `~/.claude/skills` in the owner's `config.toml`). Found on the way: `dotx auth set TYPESAFE_API_KEY` answered "unknown
  key name", since item 16 added the key to Settings › API keys only; `dotx auth set` and `dotx auth status` now take
  the keys of that page from one list (`config.providers_by_key`), and a smoke test stores the key. Branch
  `decision-08-no-pace-limits`.
- 2026-10-10: decision 8 merged as `43ac113` (PR #16, squash). At the owner's word the agent restarted their daemon
  (`dotx service install` with no `DOT_*` variable in its environment, so the job's plist still names only `PATH`; no
  run was active): it runs `43ac113`, its database gained the tables of items 12 and 17, and `dotx doctor` is all ok,
  the six skills included. Decision 5: the hosted Anthropic test passed. Decisions 3, 11 and 16: the owner's
  `config.toml` sets Claude as `review` (Sonnet 5.5), `helpers` (Opus 5.5) and `standby` (Sonnet 5.5); the daemon was
  restarted once more to read it.
- 2026-10-10: decision 9 merged as `769dc45` (PR #18, squash): the judge's thresholds set on live answers of
  `jev-1.13.0` (0.45 for clicks, 0.15 for claims) and the model pinned to it. Two subagents reviewed it (the owner's way
  now, decision 15); the offline smoke caught a stand-in claim that no longer stopped at the judge and reached the
  configured review model (one cloud call); fixed. At the owner's word the house rules no longer leave paid runs, daemon
  restarts and the data directory to the owner. Their `config.toml` sets `[judge] provider = "typesafe"`; the daemon
  was restarted to read it (no run active, the plist still names only `PATH`), and `dotx doctor` shows the judge on
  `typesafe:jev-1.13.0` with its key.
- 2026-10-10: at the owner's word the judge is off again: `[judge]` left their `config.toml` (in `yolo` it delayed
  clicks and gained only the campaign-claim check, decision 9); the daemon was restarted (no run active, the plist still
  names only `PATH`), and `dotx doctor` shows the judge off.
