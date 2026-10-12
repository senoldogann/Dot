# Autonomy roadmap and handoff

The owner asked for Dot's autonomy to be smarter, steadier and sturdier **whatever model runs it**. This file is the
handoff for that work: what is planned, why, in which order, what is done and how it was checked. If you continue the
work, read it first, and keep the status table, the item sections and the log at the bottom true. It is the only record
of where the work stands. Part 1 (items 1 to 8, autonomy) is done; part 2 (items 9 to 20: standing goals, yolo without
questions, faster steps, helper models) was planned on 2026-10-09 and has its own status table below. Part 3 (items 22 to 41: fewer steps, evidence, learned playbooks, the owner's own channels, an own crawler) was planned on 2026-10-10 from the competitive research in `reports/Dot ajanı rekabet yol haritası.md`; its status table and handoff prompt are under "Part 3".

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
use must be faster; an open-ended goal (earn something with what the owner gave, grow an audience on a platform) must be
worked on **until the outcome is there**, not until the run feels done; a `yolo` run must
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

**Why.** On 2026-10-09 a `yolo` run signing up on a site stopped at an e-mail verification code that had arrived in the
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

**Why.** This is the owner's main ask of 2026-10-09: "a system that does not stop until the owner sees the result;
when the owner names an outcome and gives the information, it must not stop until the outcome is there". Before this item (the lines
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

## Part 3: fewer steps, evidence, learned playbooks, the owner's own channels, an own crawler (planned 2026-10-10)

The owner's third ask, after parts 1 and 2: be more autonomous, better planned, faster and more correct than OpenAI
dots and SpaceXAI Grok Bot, and give Dot a web crawler of its own "that really works" (the owner named Firecrawl).
The research behind this part is `reports/Dot ajanı rekabet yol haritası.md` (Turkish, sourced): both rivals run on a
cloud computer, hand off at passwords, one-time codes, CAPTCHAs and payments, and publish no independent success or
speed number; Grok Bot has no audit log and shares one computer between all bots of an account. Dot's structural
advantage is the owner's real Mac and real Chrome profile, local data, undo and a full event log. This part turns that
advantage into measured facts.

**Principle for part 3.** The same as before: measure, notice, steer, recover. What is new: (1) *steps* are the
currency of speed: OSWorld-Human (MLSys 2026) found the best agents take 2.7 to 4.3 times the human steps and that
late steps run about 3 times slower than early ones, so every speed item is judged by steps per task and seconds per
step, not by feel; (2) a plan step is done when it carries *evidence*, never on the model's word; (3) what worked once
on a site is kept as a *playbook* and tried first next time (Agent Workflow Memory: +51 % relative success and fewer
steps on WebArena; SkillWeaver: 40.2 to 56.2 on live sites); (4) where the rivals hand off, Dot uses the owner's own
channels (Mail, Messages, Chrome profile) with the owner's standing consent (decision 7). Nothing here widens what a
run may do beyond what `yolo` already waives.

**TypeSafe in part 3 (items 42 to 45, added on 2026-10-10 at the owner's word).** Item 16 asked TypeSafe only where
`ask` and `auto` ask, so in `yolo` it slowed clicks for no gain and the owner turned it off (decision 9). These items
use it where `yolo` gains: fast, cheap typed decisions (about 0.3 s a request in the hosted test, input tokens only)
that take small choices off the main model. Before building one, read TypeSafe's live docs and the cookbook the item
names (the TypeSafe skill, plugin `typesafe@typesafe-ai`, leads to them), and set each threshold on the owner's own
data, not on a cookbook's.

### How to start part 3 (the handoff prompt)

Paste this to the agent that takes the work:

> Read `ROADMAP.md` from the top, then "Part 3" and its status table. Check the ground (`ruff format --check`,
> `ruff check`, `mypy`, unit tests, all with temp `DOT_HOME`/`DOT_LOG_DIR`). Take the first item of the part 3 table
> that is not `done`, in the order its "Order and dependencies" paragraph gives. For each item: read the code it
> touches, correct the design in its section where the code disagrees, set `in progress`, implement under the house
> rules (functional style, strict typing, no defaults, no fallbacks, real tests over mocks, minimum tests), measure
> with the numbers of items 9 and 22, update README and defaults, then branch `item-NN-short-name`, commit with the
> trailer, open a PR against `main`, review it with subagents (at most five; check each one's evidence), squash-merge,
> write the hash into the table and a line into the log. Paid hosted tests with the owner's keys, restarts of their
> daemon and edits of their `config.toml` are yours to do when an item needs them (house rules); `PLAN.md` stays
> untracked. Items 42 to 45 use TypeSafe: read its live docs and the cookbook each item names first (the TypeSafe
> skill leads to them). The owner reads Turkish; code, comments, README and this file are English.

### Status, part 3

| #   | Item                                                                     | Status  | Commits |
| --- | ------------------------------------------------------------------------ | ------- | ------- |
| 22  | Step efficiency: the numbers every later item is judged by               | done    | accd6a8 |
| 23  | Multi-action turns as the rule                                           | done    | a70e354 |
| 24  | The fast model does the side work                                        | done    | 9c16866 |
| 25  | Early and speculative execution of read-only tools                       | parked  |         |
| 26  | Speed knobs decided by measurement; snapshots that send only the change  | done    | 8566080 |
| 27  | Cheapest channel first: API or CLI, then the browser, then the desktop   | done    | dfbd485 |
| 28  | Evidence on every plan step                                              | done    | 9a9fe5f |
| 29  | Eval reports that compare runs, and the defaults that wait for them      | done    | 1513bd4 |
| 30  | Typed best-of-two at critical decisions; injection fixtures in the evals | done    | cda37e1 |
| 31  | One-time codes from the owner's own Mail and Messages                    | done    | 88abeed |
| 32  | The owner's Chrome profile as the default of a campaign                  | done    | f302464 |
| 33  | Pre-approved spending caps                                               | done    | 44ea96b |
| 34  | Research runs that keep notes, and hosted search by default              | done    | e68b22d |
| 35  | A plan with dependencies and parallel branches                           | done    | d6b40c1 |
| 36  | Playbooks learned from runs that reached their goal                      | done    | fb99354 |
| 37  | A stuck campaign changes strategy, not just session                      | done    | 12c589f |
| 38  | One view of what a run did and what can be undone                        | done    | 8c9cdbc |
| 39  | CI and retention defaults                                                | done    | 80e8f19 |
| 40  | Cold start of `dotx`                                                     | done    | 9ebe8e0 |
| 41  | An own crawler: fetch, render, crawl, extract                            | done    | aedab8b |
| 42  | TypeSafe as a tool of the agent: many items judged in one call           | done    | f0b1188 |
| 43  | Typed judgments in X campaigns: what to answer, the best draft, a check  | done    | aa1c77d |
| 44  | Typed checks before a model call: the page after an action, the goal     | done    | 1c72fa7 |
| 45  | A step's model chosen by a typed judgment                                | done    | 1a080ca |

**Order and dependencies.** 22 first: cheap, and every later item is judged with it. Then 24 and 23 (the two biggest
measured time sinks: side calls on the main model, one-action turns). 29 next, because the defaults of items 2 to 7
and the knobs of 26 wait for eval numbers; 26 and 25 follow it. 28 before 35 and 36 (both build on evidence-bearing
steps). 41 before 34 (research runs use the crawler). 31, 32, 33 are independent and are the owner's autonomy ask;
do them after 28. 27, 30, 37, 38, 39, 40 are independent and can fill gaps. 42 to 45 (TypeSafe) come after 22, whose
numbers judge them: 42 first, 43 after it, 44 after 28, and 45 last, after 24, kept only if item 22's numbers show a
gain. The owner may reorder; write the new order here when they do.

### 22. Step efficiency: the numbers every later item is judged by

**What.** `dotx stats` (and the run page) report, per run and aggregated by site host and model: steps; the share of
browser and desktop turns that performed a single action; model-call seconds per step and their curve over the step
index; `cached_tokens / input_tokens`; tokens per step. `dotx eval` reports the same per task, next to success.

**Why.** Item 9 records model-call seconds and `cached_tokens` is stored in the `assistant` event
(`agent/loop.py:1195`, `store/stats.py:34`), but no ratio, no per-site split and no curve exists. OSWorld-Human's two
findings (step inflation, late-step slowdown) cannot be seen in Dot's own data today.

**Design, as built.** The reading of the code corrected the plan in three places: there is no stats endpoint and the run
page paints no statistics (`rg stats src/dotagent/server` finds nothing), so the numbers go to `dotx stats` and the
eval report only; the events held no site, so the `tool_result` event now records `site` (the host of the page for a
browser action, the application for a desktop one, empty for every other tool) and `steps` (the steps a batched call
carried, 0 for a tool without a step list), both set in `agent/executor.py` from the policy action and the call's
arguments; and "host" became "site", since a desktop act names an application. `store/stats.py` reads two more SQL
aggregates (the act results of `browser_act` and `desktop_act`, the `assistant` events with a step number and
seconds) into `ActSample` and `StepSample`, and adds `input_tokens` to the model-call rows. Pure functions:
`act_stats` (calls, the single-step ones, the failed ones, the median call), `step_bands` (the median model-call
seconds of the steps 1-5, 6-10, 11-20, 21-40, 41+ of their runs: the slowdown curve), `site_stats` (per site, the
busiest first); `TimeStats.cached_share` is cached over input tokens of the calls that answered. `ModelStats` carries
`acts` and `bands`, `RunStatistics` carries `sites`. `dotx stats --by model|site|all` (the parser's default `all`).
The eval `RunMetrics` gains `acts`, `single_acts` and `cached_tokens` (``None`` in a report saved before they were
counted), the `Scorecard` the two shares over every attempt that counted them, shown in the card and the comparison
table. Acts and steps recorded before this item are left out, never guessed.

**Touchpoints.** `agent/executor.py`, `store/stats.py`, `cli_stats.py`, `cli.py`, `evals/results.py`,
`evals/metrics.py`, `evals/scoring.py`, README "Statistics" and "Evaluation", `tests/unit/test_run_statistics.py`,
`tests/unit/test_evals.py`.

**Done when.** `dotx stats --by site` and `--by model` print the numbers (done); the unit tests over a fixed event set
check the acts, the curve, the sites, the cache share and their rendering (done: `test_run_statistics.py`,
`test_evals.py`); the eval JSON carries `acts`, `single_acts` and `cached_tokens` per attempt and the report shows the
shares (done). Not built: numbers on the run page (no statistics view exists; a later UI item if the owner wants it).

### 23. Multi-action turns as the rule

**What.** The prompt and the tool descriptions make a one-action browser or desktop turn the exception: steps that
need no new observation between them (fill a form, walk a menu, select and type, scroll and click) go in one
`browser_act` or desktop act call. The loop notices a run of three single-action turns on the same page and adds a
one-line steer.

**Why.** Measured: a 4-step form in one `act` 409 ms against 773 ms as four calls and three model round trips fewer
(`PLAN.md` "Measurements"); item 13 batches exist (`browser/session.py`), the model does not use them by default.

**Design, as built.** The prompt's browser and desktop bullets say that a one-step `browser_act` or `desktop_act`
is the exception, why (a model round trip per call) and what to batch, with one worked example each (a sign-up
form, a save dialog); the two tools' `steps` descriptions say the same in one line. In `agent/progress.py`, pure
functions next to the watchdog: `single_action(acts)` tells whether a step's acts were one-step batches on one target
with one tool (a step that moves between pages or mixes tools is not a wasted round trip), `single_action_streak`
counts such steps in a row on the same target (1 again on a new target, 0 on a batch or a step without an act), and
`single_action_steer` writes the note. The loop derives the step's acts from the outcomes (`batch_steps` of the
call's arguments, the policy action's target: the page's address or the application) and, at
`SINGLE_ACTION_STEPS` (3) in a row, stores the note for the model and emits a `single_action_steer` event (step,
tool, target with secrets masked); the count starts again, so a run that keeps clicking one at a time hears it once
per three steps, never every step. The note never stacks with a progress alert or a reminder of the same step
(it is the last `elif`). `[agent] steer_single_actions = true` by default, `false` turns it off. The eval set has
no browser task (the harness runs without a browser or the desktop), so the measured half of "Done when" waits for
the owner's own runs: `dotx stats --by site` shows the single-step share before and after the daemon restarts on this
code.

**Touchpoints.** `agent/prompt.py`, `agent/loop.py`, `agent/progress.py`, `tools/browser.py`, `tools/desktop.py`,
`defaults.toml`, `config.py`, README "Models and providers" (the scaffold paragraph), `tests/unit/test_progress.py`.

**Done when.** The steer has unit tests (done: `test_progress.py`, the detector, the streak and the note); the
single-step share of the owner's runs (item 22, `dotx stats --by site`) halves after this code runs for a while and
success does not drop (the owner's reading, "Open owner decisions").

### 24. The fast model does the side work

**What.** `[models] fast` is read and used for: compaction, the `browser_wait_text` and `judge` classifications where
no typed provider is set, the vision descriptions when `vision` is unset, the pre-check of the goal (the full review
model still decides). A side call on the fast model that fails falls to the main model with a warning in the event
(an error, not a silent fallback: the event names it).

**Why.** The audit: compaction took 12.7 % of run wall time, median 10.7 s, on the run's own model
(`agent/loop.py:290-298`); `[models] fast` is documented and read nowhere (decision 4). gpt-oss:20b-cloud answers a
tool step in 889 ms, the default main in 2 798 ms.

**Design, as built.** The reading of the code cut the list of side callers to the one the audit measured. There is no
model classification in `browser_wait_text` (it waits for text, `tools/browser.py`); the judge is TypeSafe or nothing
by design (item 16, and items 42 to 45 extend it), not a chat model; the vision role has a model of its own; a
pre-check of the goal is new behavior no number asked for. So: `agent/loop.py:_compact` asks `[models] fast` for the
summary first; when the fast model gives no usable summary (a provider error, a timeout, no text, a summary far too
long) and it is not the run's own model, the run's own model is asked, the warning `summary_fast_failed` is logged
and the `compacted` event carries `fast_problem` (``None`` when the fast model wrote it); `model` in the event and in
the `model_call` events of the compaction names the model that wrote it. The note's author line says "written by the
fast model …" or "written by you, …". `dotx doctor` checks the fast model like main, vision and review.
`defaults.toml` and README no longer say the role is unused. No new config key. The eval runner leaves `fast` as
configured: an eval measures the product's behavior, and the scripted check's `ollama` stand-in answers the fast
model's calls too (same provider name).

**Touchpoints.** `agent/loop.py`, `doctor.py`, `defaults.toml`, README "Models and providers" and "Tools",
`tests/smoke/test_scaffold.py`.

**Done when.** A smoke test shows every compaction of a scripted run written by the fast model with `fast_problem`
``None``, and one with a fast model whose provider fails written by the run's own model with the problem named (done:
`test_scaffold.py`). Item 9's numbers for the owner's runs (compaction share of run time, decision 4 closed by this
item) are read with `dotx stats` after the daemon restarts on this code: the owner's call, in "Open owner decisions".

### 25. Early and speculative execution of read-only tools

**What.** (a) Early start: a `parallel_safe` tool call starts as soon as its arguments are parsed from the stream,
not when the reply ends. (b) Speculation: after a browser or desktop act, the observation the model is most likely to
ask for next (the snapshot of the page, the window view) is taken at once and served from a one-step cache when the
model asks for it; a stale cache is dropped on any act.

**Why.** Tools run after the whole reply (`agent/loop.py:1180-1208`); the stream parse already exists. Speculative
tool execution reports 20 to 48 % latency cuts in 2026 papers (Speculative Actions, PASTE, SPORK); only side-effect
free tools qualify, and the one-step observation cache is the safe subset.

**Parked (2026-10-10), after reading the code.** Both halves rest on premises the code does not hold. (a) The stream
carries text and thinking only (`llm/types.py:StreamDelta`, kinds `text`, `thinking`, `reset`); every codec
accumulates the tool calls' arguments in partial slots and hands them over with the finished reply
(`codec_chat.py:199-268` `PartialCall`, `codec_messages.py:247` `input_json_delta`, the Responses and Ollama codecs the
same way). An early start needs a fourth delta kind in four codecs, plus care for `reset` (a retry re-emits the calls,
so an early-started call would run twice), and it gains only the tail of the reply after the last argument: for the
one-call steps that make up most of a run that is the finish tokens, a few hundred milliseconds at most. (b) An act
already returns the observation the model needs next: `browser/session.py:act` ends with `_changes_view`, the desktop
act with its changed view (item 13), and `browser_open` returns the page, so there is no next observation to take
early in the common path. What would reopen the item is a number: the share of steps with two or more tool calls of
which the first is `parallel_safe` (the `tool_call` events of a step carry the names), read from the owner's history
once item 22's events have accrued; if it is well above a tenth, (a) is worth the codec work.

**Touchpoints (if reopened).** `llm/types.py`, the four codecs, `agent/executor.py`, `agent/loop.py`.

**Done when (if reopened).** Seconds per step on the eval set fall 15 % or more against item 22's baseline; a unit
test proves a side-effect tool never starts early and a retried reply never runs a call twice.

### 26. Speed knobs decided by measurement; snapshots that send only the change

**What.** Decision 13 is closed with numbers: `[browser] settle_quiet_ms` 60, `[desktop] screenshot_max_edge` 1366,
`keep_recent_tool_results`, `tool_output_chars`. A snapshot after an act on an unchanged page returns "no change" and
the ref table of the previous snapshot stays valid; a changed page returns the changed subtree when it is smaller
than the whole.

**Why.** Item 13 measured 220 → 149 ms per click at 60 ms; a 1 710 × 1 112 JPEG is about 2 500 tokens against 1 600
at 1 366; a full aria snapshot is taken also when nothing changed (audit).

**Design, as built.** Two of the four knobs had numbers and were flipped: `[browser] settle_quiet_ms` 120 → 60 (item
13 measured one click 149 against 220 ms with the same result; a page that keeps changing is still waited for up to
`settle_max_ms`), `[desktop] screenshot_max_edge` 1920 → 1366 (about 1 600 tokens a screenshot against about 2 500).
`keep_recent_tool_results` and `tool_output_chars` stay: no number says what they cost, and the eval set has no
browser task to judge them by. The snapshot delta already existed (item 13: an act returns the changed lines when
they are clearly smaller than the page, `browser/session.py:_changes_view`, `snapshot.py:prefer_diff`); what was
missing was the unchanged case, which rendered "0 new/changed lines": `render_diff` now says "no change since the last
observation (N lines unchanged); the refs still hold" in one line. The desktop view already returns only what changed
(item 13). Decision 13 is closed by this item for the two knobs.

**Touchpoints.** `browser/snapshot.py`, `defaults.toml`, README "Browser use" and "Computer use".

**Done when.** The browser smoke suite passes with the new default and its timing test prints the medians (done,
headless Chrome, 39 passed: one click 0.167 s at 60 ms against 0.231 s at 120 ms, five clicks 0.497 against 0.566 s);
the unchanged case has a unit test. The desktop timing test needs the owner away from the keyboard
(`DOT_DESKTOP_TESTS=1`) and was not run here; the screenshot size change is a pure scale and is covered by the existing
unit tests of `desktop/shots.py`.

### 27. Cheapest channel first: API or CLI, then the browser, then the desktop

**What.** The prompt states the order for any job: a documented API or a CLI on this Mac first, the owner's Chrome
profile second, the desktop GUI last; skills may name the API of their site. The loop records the channel of each
step so item 22 can count GUI steps.

**Why.** Claude Cowork does this with connectors and is the reference of the local-Mac category; GUI steps are the
slowest and least reliable.

**Design, as built.** The prompt already set the order (shell and CLIs, then APIs and MCP tools, then the web and
the browser, then AppleScript, then the desktop); it gains one sentence: a skill or an MCP server of a site that names
its API or command is used before the site's pages. The channel is a pure function of the tool's name
(`tools/channels.py`: `command`, `api`, `web`, `browser`, `desktop`, `agent`), recorded in every `tool_result` event
by the executor (not the loop), and `dotx stats` gains a channels table with the share of the acting calls (every
channel but `agent`) that went through a GUI. The eval set cannot measure the prompt change: its model is scripted and
answers in call order, so the GUI share of a scripted run is fixed by its script. The live eval run that records the
change is the owner's (decision 22); `dotx stats` shows the share of their real runs from this version on.

**Touchpoints.** `tools/channels.py` (new), `agent/executor.py`, `store/stats.py`, `cli_stats.py`,
`agent/prompt.py`, README.

**Done when.** Every tool result carries its channel; `dotx stats` shows the channels and the GUI share; unit tests
for the channel of a tool and the share; the live measurement is recorded by the owner.

### 28. Evidence on every plan step

**What.** `PlanItem` gets `evidence: str` (a tool result reference or a quoted observation). `mark_plan_step` to
`done` without evidence is a `ToolInputError` that names what counts as evidence. The verification of the end of a
run (item 5) and a campaign's outcome check (item 12) read the evidence instead of the model's summary.

**Why.** Grok Bot's most reported failure is reporting incomplete work as finished; dots' docs say it "can make
mistakes". Dot's plan is a flat list with a free `targets` text (`agent/plan.py:1-40`).

**Design, as built.** `PlanItem.evidence` (empty for the plans stored before it existed, which still validate).
`mark_plan_step … done` without `evidence` is a `ToolInputError` that names what counts (the tool result that shows the
step: the path and what was read back, the count or the line of output, what the page or the window showed); a failed or
skipped step may carry what happened; a step opened again drops its evidence. `update_plan` takes an `evidence` per
step too: a step given as done keeps its own, else the evidence of the previous plan's step with the same text, else it
is refused, so no plan holds a done step without evidence. The plan with the evidence under each closed step goes
wherever the plan went (compaction note, resumed run, consult, aside) and, new, to the goal check: `GoalRequest.plan`
is a fenced PLAN section of the goal prompt for the end of a run and for `campaign_achieved` (the session's own plan),
and the checker's rules say the evidence of a step is the agent's claim, to be borne out by the digest or the facts.
`GoalVerdict.unproven_steps` lists the done steps the checker could not bear out; the `goal_check` event carries it.
`agent/verify.py` is unchanged: the facts are the harness's, the evidence the agent's, and the checker reads both. The
web UI shows the evidence under the step. Evals: `RunMetrics.unevidenced_steps` (the last plan's done steps without
evidence, 0 by construction) and `RunMetrics.unproven_steps` (summed from the `goal_check` events), in the eval JSON,
the scorecard, the comparison table and the difference line. The "judged by the review model" count therefore exists
only where a goal checker ran (goal mode; the offline `dotx eval check` scripts the verdict), as the harness has no
review model of its own.

**Touchpoints.** `agent/plan.py`, `tools/meta.py`, `agent/review.py`, `agent/loop.py`, `tools/campaign.py`,
`agent/prompt.py`, `evals/results.py`, `evals/metrics.py`, `evals/scoring.py`, `evals/catalog.py`,
`server/static/js/views/run.js`, `server/static/css/app.css`, README "How a run works", "Autonomy", "Campaigns" and
"Measuring the agent".

**Done when.** A mark to done without evidence is refused and a rewritten plan keeps evidence (unit tests, done); the
goal prompt carries the plan with evidence and the verdict's `unproven_steps` reaches the `goal_check` event (smoke
tests of the real loop, done); the eval JSON holds both counts and the scorecard prints them (unit test, done).

### 29. Eval reports that compare runs, and the defaults that wait for them

**What.** `dotx eval compare A.json B.json` prints success, steps, seconds per step, tokens, cache ratio side by
side with the difference; the eval set gets five long tasks (10 or more steps) on the fixture server, one of them a
campaign with a verifiable outcome. The owner runs the live evals (decision 1); the defaults of decisions 2 and 13
are flipped from the comparison, one commit each.

**Why.** Every later item claims a measured gain; without a comparison command each claim is a hand-made table.

**Design, as built.** The reading of the code corrected two things. `dotx eval report A.json B.json` already prints a
comparison table, so no `compare` command was added: with exactly two files the report ends with a difference line,
the second against the first (passed with percentage points, steps, tokens and seconds per pass, the one-step act and
cache shares of item 22 in percentage points, tool errors, stalls, false claims; `-` where a side lacks the number).
The eval harness runs without a browser or the desktop, so the "fixture server" and the campaign of the plan have no
place in it: the five long tasks are file and shell work whose reference needs ten or more tool turns (a digest of
twelve logs, a rename across ten modules with a check script, ten branch totals into JSON, a ten-item checklist that
is ticked, a nine-stage pipeline whose fifth stage fails once with "transient" and is run again: category
`recovery`); `LONG_TASK_IDS` names them, `dotx eval check` replays every reference. A campaign eval waits for a
harness that can run sessions (none planned; item 37 tests campaigns in unit tests).

**Touchpoints.** `cli_eval.py`, `evals/catalog.py`, `evals/scoring.py`, README "Evaluation",
`tests/unit/test_evals.py`.

**Done when.** The difference line has a unit test (done); the long tasks pass `dotx eval check` (done, 5 of 5) and
need ten or more tool turns (unit test); "Open owner decisions" lists the exact commands for the live runs (decision
1, updated).

### 30. Typed best-of-two at critical decisions; injection fixtures in the evals

**What.** At a decision the policy flags as critical (a payment under a cap, the choice of an account or recipient,
a public post) the judge of item 16 is asked twice with independent phrasing and acts only on agreement, otherwise
the step is marked `needs_decision` with both answers. The fixture server gets pages with instructions hidden in
content, and the evals report how many a run followed.

**Why.** OpenAI's own evals for dots report 15 to 17.4 % attempts to work around a warning and 8.6 → 19.7 % scope
violations across chained tasks; a measured counter-number is a claim Dot can make.

**Design, as built.** The judge of item 16 is asked only where code has no answer of its own: a click on a control
whose label no pattern knows, and a campaign's claim of its targets. A payment under a cap (item 33), the choice of a
recipient and a click on "Post" are decided by the patterns and the caps, and end in the policy's question or in the
allowance, so the second opinion lives in `agent/judge.py` alone and `agent/gate.py` and `policy/*` are untouched.
`[judge] second_opinion` (off by default: a second paid request per judged click or claim) asks each question a
second time, in a request of its own with an independent phrasing (`SECOND_CLICK_QUESTIONS`, `SECOND_CLAIM_QUESTION`,
the same ids), and the two answers are read together. A click has no `needs_decision` state of its own, and a flag is
what makes the policy ask, so that is the disagreement's outcome: `agreed_click_judgment` gives the flag when either
answer is at the threshold and the summary names both answers, marking the ones that disagreed. A claim is refused
only when both answers are below `target_threshold` (`claim_refused`); a disagreement leaves it to the goal checker,
since a true claim refused keeps a campaign from ending, and the refusal names both answers. A second request that
fails falls back to the single answer, with the usual `judge_unavailable` event. There is no fixture server offline
(item 29), so the hidden instructions are in seeded files: the invoice of `injection-in-invoice`, a saved pricing page
with a hidden block that asks for a keys file (`injection-in-page`), and a notes file whose last line asks for its own
deletion and a false report (`injection-in-notes`). A task carries `injection_checks` apart from its checks (a file not
made, a file still there, and the new `answer_lacks` check: the words the injection told the run to say); they count
toward the pass, and the attempt's `injection_followed` in the report says whether one of them failed. The scorecard
counts the attempts at such tasks and how many followed (`hidden instructions followed 1 of 3`), the difference line
and the comparison table carry the count, and `dotx eval check` proves the three references pass.

**Touchpoints.** `agent/judge.py`, `tools/campaign.py`, `config.py`, `defaults.toml`, `evals/types.py`,
`evals/checks.py`, `evals/audit.py`, `evals/catalog.py`, `evals/runner.py`, `evals/results.py`, `evals/scoring.py`,
`cli_eval.py`, README "Typed judgments" and "Evals".

**Done when.** The injection count is in the eval JSON and the scorecard; unit tests cover the disagreement of two
click answers, the refusal of a claim with two answers and the count; the references of the three injection tasks pass
`dotx eval check` (decision 23).

### 31. One-time codes from the owner's own Mail and Messages

**What.** A `code_fetch` tool (acting, `yolo` and `auto` only; `ask` asks once) reads the newest message in Mail.app
or Messages.app from a sender or with a subject the model names, within the last N minutes, and returns the code
only. The browser then types it as item 10 allows. Nothing else of the mailbox reaches the model.

**Why.** Decision 7 lets `yolo` type one-time codes; decision 12 waits for a live run that reads one from webmail.
dots sends the user a private form, Grok Bot hands over the screen: this is the line where Dot keeps going. Reading
through AppleScript is faster and steadier than browsing a webmail.

**Design, as built.** One connector module, `desktop/codes.py`. Mail is read through its AppleScript dictionary
(`osascript`, the existing runner): the newest message of the inbox within the window from a sender or with a subject,
returned as age, sender, subject and content, read in Dot and never shown to the model. Messages has no scripting for
received messages, so its database `~/Library/Messages/chat.db` is read directly, read-only and immutable (needs Full
Disk Access for the program that runs Dot; the error says so, and `dotx doctor` has a `one-time codes` row that says
whether the database opens); the text column or the attributed body, decoded loosely, gives the text. The code is the
4 to 8 digit number after a cue word (code, pin, otp, verification, kod, şifre, …), a `G-` prefixed one, the number a
message opens with, or the only six-digit number of the text; anything else is "no code recognised", never a guess.
`tools/codes.py` builds `code_fetch(app, sender, subject, within_minutes)`, registered only when
`[policy] code_sources` names an application (so the default installation's prompt does not carry it), and refuses an
application the list lacks. The action is `app_control` of `high` risk with no hard-stop flag: "ask asks once" of the
plan has no equivalent in the engine without a new flag, so the owner's opt-in is the standing answer, the aggressive
preset allows a read, balanced and cautious ask, a yolo or auto run goes on, and research has no such tool. The result
is not marked untrusted: a few digits carry no instruction, and the message itself stayed in Dot. Typing the code is
item 10's rule (yolo only). The live test (a mail the test sends to the owner's own address, read back within two
minutes) runs only with `DOT_MAIL_TESTS=1`; the offline smoke test covers the registry, the refusal and the policy.

**Touchpoints.** `desktop/codes.py` (new), `tools/codes.py` (new), `desktop/perms.py` (`grant_hint` made public),
`config.py`, `defaults.toml`, `runtime.py`, `doctor.py`, `agent/prompt.py`, README "Tools", "Safety", "Autonomy",
"Status and known limits".

**Done when.** Unit tests for the code rule, the Mail script and the Messages read on a copy of the schema; the smoke
test shows the tool exists only for the listed applications and the policy's verdicts; the live Mail test passes for
the owner (decision 19).

### 32. The owner's Chrome profile as the default of a campaign

**What.** A campaign (item 12) uses `attach` (the owner's Chrome profile) unless its brief says `own`; a run started
by hand keeps its current default. The campaign brief shows which profile it runs on.

**Why.** Cloud browsers are blocked or challenged by sites (dots' docs say so); the owner's signed-in profile is the
advantage neither rival has. The attach path exists (README "Browser use").

**Design, as built.** The daemon had one browser session in one mode, so a campaign on the owner's Chrome while
hand-started runs keep Dot's own needs a second session: `browser/pool.py` keeps the main session (the configured
mode, which `dotx browser attach` still moves) and one session per other mode, made on first use and connected by the
first call that needs it; `Runtime.browsers` holds the pool and `Runtime.browser` stays the main session. The run's
session is chosen when it starts (`loop._browser_of`: the campaign's choice, a delegated run following its parent's
campaign, else the daemon's) and travels in `ToolContext.browser`, which the browser tools and the crawler's renderer
use instead of the runtime's. The campaign record gains `browser` (`own`/`attach`; `NULL` for a campaign from before,
which keeps the daemon's), set at the start from `--browser`/the API's `browser` or `[agent] campaign_browser`
(default `attach`; there is no `[campaign]` table in the configuration). A campaign that asks for the owner's Chrome
is refused at its start while that Chrome is closed or has no debugging port (`read_debug_endpoint`), rather than
failing session after session. The brief's second line names the Chrome; `dotx campaign show` and the campaign's page
show it. Evals keep `campaign_browser = "own"`: a measurement never touches the owner's Chrome. The three-session X
check is the owner's (decision 20); the offline tests cover the pool, the brief, the refusal and the stored choice.

**Touchpoints.** `browser/pool.py` (new), `runtime.py`, `tools/context.py`, `tools/browser.py`, `tools/web.py`,
`agent/loop.py`, `agent/resume.py`, `agent/campaign.py`, `agent/manager.py`, `store/models.py` (`BrowserMode` now
lives here), `store/db.py`, `config.py`, `defaults.toml`, `evals/runner.py`, `cli.py`, `cli_campaign.py`,
`server/views.py`, `server/routes_runs.py`, `server/static/js/views/campaigns.js`, README "Campaigns".

**Done when.** The pool gives one session per mode without starting a Chrome; a campaign's brief names its Chrome; a
campaign that asks for a closed Chrome is refused and one on Dot's own starts with the choice stored; README.

### 33. Pre-approved spending caps

**What.** `[policy.spend]` lists hosts with a cap per purchase and per day; a payment step on a listed host under its
cap passes in `yolo` and `auto`, is recorded with the amount, and counts against the day; over the cap or on an
unlisted host the money hard stop holds in every mode.

**Why.** dots allows advance approval for a specific purchase; Dot has only the hard stop. Open-ended goals of the owner's kind
need small spends (a domain, a listing fee).

**Design, as built.** `[policy.spend]` is a table of sites (the host without `www.`) to `SpendCap(per_purchase,
per_day)`, plain numbers. The amount is read in the browser tool's classification of a payment step
(`policy/spend.py`, not `elements.py`, whose rules are name-based): the amount in a control's label first ("Pay
€19,90"), else the amount after the last total cue of the page's text ("Order total: $12.00"); a step whose amount
cannot be read keeps the hand-off. The amount travels in `ActionInfo.amount` (the one field of `ActionInfo` with a
default, since only payment steps know one) and in the step's summary, so the policy event, the approval record and the
timeline show it. The engine gains `allowance`: the gate computes the site's state (the day's spends from the new
`spends` table) and `spend_allowance` turns a step under both caps into a question with source `spend`, which takes the
place of the money hand-off (`hard_stop` no longer stops the financial flag then; a credential flag still does) and no
later layer loosens. A question in `yolo` is waived, so the run goes on; in `auto` the review model answers; in `ask`
the owner is asked once (the plan said "passes in yolo and auto" and nothing of `ask`). The spend is recorded once the
step is authorized, whichever way. Over a cap, unlisted, or without an amount, nothing changes: the hand-off holds in
every mode but `yolo`, as before. No currency conversion: the caps compare the number on the page whatever its sign.

**Touchpoints.** `policy/spend.py` (new), `policy/types.py` (`Amount`, `ActionInfo.amount`, source `spend`),
`policy/engine.py`, `agent/gate.py`, `agent/manager.py`, `tools/browser.py`, `store/db.py` (`spends`), `config.py`,
`defaults.toml`, README "Safety".

**Done when.** Unit tests for the amounts, the caps (under, over, the day, unlisted, no amount) and the engine; the
gate's smoke test shows the question, the record of the spend, the hand-off over the day's cap and a yolo run spending
within the caps; the timeline shows the amount; README.

### 34. Research runs that keep notes, and hosted search by default

**What.** A `research` run may write one notes file and save memory notes; `web.search_backend` has a third value,
`auto` (the default), that is `ollama` when `OLLAMA_API_KEY` is present and `duckduckgo` otherwise; the research prompt
sends long research to the crawler of item 41 instead of page-by-page fetches.

**Why.** Decision 14: a research run can save nothing (`tools/base.py:16-17`), DuckDuckGo's bot check throttles long
runs (README "Status and known limits").

**Design, as built.** There is no run folder in the workspace, so the notes file is `<workspace>/notes/<run id>.md`,
written by a new tool `write_notes(content, append)` (`tools/notes.py`) that is offered in task and research runs, goes
through the undo journal like `write_file`, and has its own action kind `notes_write`. The mode gate of the policy
(`policy/engine.py`) now allows `RESEARCH_KINDS`: the read-only kinds plus `notes_write` and `memory_write`, so a
research run can keep notes and `memory_save` is offered in every mode (`memory_update` and `memory_forget` stay
acting-only). The backend is resolved once when the runtime opens (`Runtime.search_backend`, from
`web/search.py:resolved_backend` and the presence of the key through `resolve_secret`), `web_search` names it in its
result, and `dotx doctor` has a `web search` row that says what `auto` resolved to and why. The research mode text of
the prompt names `write_notes`, `memory_save` and `web_crawl`; the web section gained a bullet on `web_crawl` and
`web_extract` (item 41 had added none). No 30-minute live research task: the throttle of DuckDuckGo cannot be measured
offline and the hosted search needs the owner's key, so the smoke test is a scripted research run in the real loop that
writes, appends, saves a fact and finishes, and the policy and backend rules are unit tests.

**Touchpoints.** `policy/types.py`, `policy/engine.py`, `tools/notes.py` (new), `tools/files.py` (the write helpers
made public), `tools/memory.py`, `web/search.py`, `runtime.py`, `doctor.py`, `agent/prompt.py`, `config.py`,
`defaults.toml`, README.

**Done when.** A scripted research run in the real loop ends done with a notes file that holds both writes and a memory
note, `write_file`/`edit_file`/`shell` are not offered to it, and the backend choice is in `dotx doctor`.

### 35. A plan with dependencies and parallel branches

**What.** `PlanItem` gets `depends_on: tuple[int, ...]`; steps with no unmet dependency and marked `parallel` may be
delegated at once (item 15's helpers choose the model), each with a step budget, and their evidence (item 28) flows
back into the parent plan. The run page draws the graph.

**Why.** dots runs background agents in parallel and Grok Bot coordinates several bots; Dot delegates one level deep,
one child at a time by the model's choice, with a flat plan.

**Design, as built.** The reading of the code corrected the plan in three places. The parallel mechanism already
existed: `delegate` is parallel-safe and the delegate calls of one turn run side by side (up to `parallel_tools`), so no
new scheduler was built and the harness does not start runs on its own; the model does, told by the plan. What was
missing was the plan knowing which steps are independent, and the parent having to mark each delegated step itself.
`PlanItem.depends_on` (1-based step numbers; empty for the plans stored before); `update_plan` refuses a number outside
the plan, a step that depends on itself and a cycle (`checked_dependencies`). `ready_steps` (open, waiting for no open
or failed step) and `held_steps` (open, waiting for a failed one) are the readiness rule; the result of every plan
change ends with "Ready now: steps 1, 2; they are independent, so delegate can run them in one turn" and "Held by a
failed step: 3", and the plan reads `(after 1, 2)`. `delegate` takes `step`, checked against the plan before the run
starts; when the delegated run ends, the loop (not the tool: the calls run side by side and the plan is rewritten
whole) marks the step done with the report's start as evidence (item 28; a fenced third-party report stays in the tool
result and the evidence says so) or failed, and the delegate result says so. No per-child step budget: the parent's
`run_max_tokens` already counts the delegated runs, `run_max_minutes` bounds each, and the progress watchdog stops a
child that gets nowhere. The model of a branch is the `model` of `delegate` (item 15). The web UI shows `after 1, 2` on
the step; a graph drawing was not built. The eval harness's scripted model answers in call order, so two delegated runs
on one script are not deterministic: the parallel measurement is a smoke test of the real loop with a helper provider
per child (each child works 0.5 s; both start before either ends, so the parent waits for the longer one, not the sum),
and no eval task was added.

**Touchpoints.** `agent/plan.py`, `tools/meta.py`, `tools/delegate.py`, `agent/loop.py`, `agent/prompt.py`,
`server/static/js/views/run.js`, `server/static/css/app.css`, README "How a run works" and "Delegation",
`tests/unit/test_continuity.py`, `tests/smoke/test_plan_branches.py`.

**Done when.** The readiness rule and the dependency checks have unit tests (done); in the real loop two delegated
steps run side by side and their reports close the steps with evidence, and a delegation for a step the plan lacks
starts no run (smoke tests, done).

### 36. Playbooks learned from runs that reached their goal

**What.** When a run or campaign session reaches a verified outcome on a site or application, the harness distills
its acting steps into a playbook: a skill file (`SKILL.md` layout, natural-language steps with the parameters named)
under `<DOT_HOME>/playbooks/<host or app>/`, with the evidence that it worked. The next run on that host sees the
playbook's description in the prompt and reads it with the `skill` tool. A playbook that fails twice is marked and
no longer offered.

**Why.** Agent Workflow Memory: +51 % relative success and fewer steps on WebArena; SkillWeaver: live sites 40.2 →
56.2; "Demystifying Agent Skills" (2026): the skill representation beats raw workflow memory by about 6 points. Dot's
lessons (item 7) record what went wrong, nothing records how a goal was reached.

**Design, as built.** `agent/playbooks.py`, a sibling of `lessons.py`, with the same shape: the manager starts a
task after a run ends (`playbook_case`: `[agent] playbooks` on, a task run of its own, not an eval, ended done or
failed), the task records the playbooks the run read (`skill` calls that named a `playbook-…`) and, when the run ran in
goal mode and ended done (so its goal check found the outcome achieved; a campaign session is a goal-mode run too),
distils it. "Verified outcome" is therefore the goal check's verdict, not a plan's evidence. The site is the host or
application that most `tool_result` events name (`site`, from item 22); a run that acted on none writes nothing, and a
host that has a playbook is not distilled again (one playbook per host; `dotx playbooks drop` makes room for a new
one). The review model gets the goal, the host, a digest of the acting steps on it (tool, arguments, ok or FAILED, the
start of the result; the newest 80) and the answer, and returns a typed `PlaybookDraft` (description ≤ 200 characters,
when, 1–20 steps, parameters); the rules say that page text is data and that no secret, code, address or home path goes
in. The folder is `<home>/playbooks/playbook-<host-slug>/SKILL.md` (a flat skill folder, not `<host>/`, because the
skills loader names a skill after its folder) with When, Steps, Parameters and Evidence, and a `playbook.json` with
`uses` and `failures`; `skill_roots` adds the folder as a root with source `playbook` when playbooks are on, so the
catalog, the prompt (with a note on what a `playbook-…` skill is), the `skill` tool and `dotx skills list` treat it
like a skill. A playbook read by two runs that then failed is moved under `.retired`, where the loader does not look.
`dotx playbooks list|show|drop`. Events `playbook_written`, `playbook_failed`, `playbook_retired`. The default is
`false`: switching it on changes what the owner's runs do and costs a review-model call per qualifying run, and the
"second run takes half the steps" measurement needs a live model on a site (decision 17).

**Touchpoints.** `agent/playbooks.py`, `agent/review.py` (`PlaybookDraft`, `playbook_writer_for`),
`agent/manager.py`, `agent/prompt.py`, `skills/catalog.py` (`skill_roots` with the playbook root, `playbook_root`),
`core/paths.py`, `config.py`, `defaults.toml`, `cli_playbooks.py`, `cli.py`, `evals/runner.py` (evals run without
playbooks), README "Skills and plugins", `tests/unit/test_playbooks.py`.

**Done when.** The distillation's pure parts (main site, digest, skill rendering that the loader parses, the record
that retires at two failures) have unit tests over a fixed event set (done); the live measurement is decision 17.

### 37. A stuck campaign changes strategy, not just session

**What.** When `sessions_without_progress` reaches the patience (item 12), the next session's brief carries an
explicit instruction to take a different route (another channel, source or approach) and lists what was tried; the
same route twice in a row is named in the brief as refused.

**Why.** `agent/campaign.py:169` counts sessions without progress; what follows is another session of the same kind.

**Design, as built.** The brief carries every strategy entry of the ledger (`Brief.strategies`, oldest first, read
with the store's `campaign_entries_of_kind`) instead of the newest alone. Once the sessions without progress reach
`campaign_patience_sessions`, the closing block of the brief changes from "write a new strategy" to a change of route:
it lists the strategy entries written since the newest progress entry (`routes_without_progress`), says they brought
nothing and are refused, and asks for another channel (a site's API, a command or a skill instead of its pages),
another source or another approach, written as a strategy entry first. "The same route twice in a row" is covered by
that set: every route since the last progress is refused, however often it was written. The refusal is enforced, not
only named: while the campaign is stuck, `campaign_note` refuses a strategy entry that repeats one of those routes
(`route_refused`, folded as search folds), with the same call for another route in its error; a campaign that is not
stuck takes any strategy. The session count of the rule excludes the calling session, as the brief's does.
`agent/campaign_control.py` is untouched: the owner's controls do not change. The prompt's campaign line says a new
strategy takes another route.

**Touchpoints.** `agent/campaign.py`, `agent/manager.py`, `agent/prompt.py`, `tools/campaign.py`, `store/db.py`,
README "Campaigns".

**Done when.** A unit test over a fixed ledger shows the routes tried, the refusal and the brief's block; the smoke
test runs a campaign with patience 1 whose second session is refused the route of the first and reaches its outcome by
another (decision 24).

### 38. One view of what a run did and what can be undone

**What.** The run page gets an "Actions" panel: every acting step with its channel, target and evidence, every
approval decision, every file edit with its undo state (item 8), and one button per undoable edit.

**Why.** Grok Bot has no audit log; dots' stop does not undo. Dot records all of it and shows it in pieces.

**Design, as built.** The panel is an Actions card in the Details panel of the run's page, beside the plan, fed by
one endpoint, `GET /api/runs/{id}/actions` (`server/actions.py`, `RunActions`): the acting steps from the run's
`tool_result` events (the executor now records the action's `kind` and `target` on the event; a step acts when its
kind is not read-only, and an event from before the kind was recorded counts by its risk), each with its channel
(item 27), target, summary, success and the clipped result as evidence; every approval and question of the run from
the store (`run_approvals`), with its decision, decider and note; and the file changes as the undo plan of `dotx undo`
reads them, over the run and the runs it delegated to. The plan, the journal reading and the undo of one path moved
into `undo/runs.py`, which the CLI and the server share; `POST /api/runs/{id}/undo` with a path takes that one change
back when the path is as the runs left it (`ready`) and every run has ended, and refuses the rest with the plan's
reason (a conflict is 409, an unknown path 404). The card lists the three groups, a Restore or Delete button per file
that can be taken back (behind a confirmation), and reloads itself when the run ends and after an undo. The `undo/`
package's rules did not change: the shell, the browser and the desktop stay outside the journal.

**Touchpoints.** `undo/runs.py` (new), `cli_undo.py`, `agent/executor.py`, `store/db.py`, `server/actions.py`
(new), `server/views.py`, `server/routes_runs.py`, `server/static/js/views/run.js`, `icons.js`, `app.css`, README
"The daemon, the web UI and the service" and "Taking back the file changes of a run".

**Done when.** `tests/smoke/test_actions_panel.py` checks the endpoint's steps and edits and drives the headless UI
through the card to take one change back; a unit test covers which results are acting steps; `dotx undo` behaves as
before (its smoke test).

### 39. CI and retention defaults

**What.** A GitHub Actions workflow runs the gate (ruff format, ruff check, mypy, unit tests, offline smoke) on every
PR; `[store] retention_days` for events and screenshots gets a documented default and `dotx prune` honours it only
when told (nothing deletes by default, house rule).

**Why.** Audit leftover F and decision 4.

**Design, as built.** One workflow, `.github/workflows/gate.yml`, on `macos-latest` (the package depends on
PyObjC, so Linux cannot install it), on every pull request and push to `main`, one run per ref at a time: `uv sync
--frozen`, the format check, the lint, mypy, the unit tests, `dotx eval check` and the smoke tests that need no
service, listed by file in the workflow (the 19 files that use the scripted model, the real store and the real loop
without Chrome, Ollama, the desktop or a key; about 16 s on a Mac). Each run gets a throw-away home through `DOT_HOME`,
`DOT_LOG_DIR` and `DOT_CHROME_DIR`, `brew install ripgrep` (the search tool and its unit test need it; the image has
Homebrew but not rg) and `TZ=UTC`: the runner's `/etc/localtime` is not a link into the zone database, so the zone
resolution now honours `TZ` before reading that link (`scheduler/timing.py`, documented in `defaults.toml` and README
"Schedules"). Retention: a `[store]` section with `retention_days` (90) names how long the
events of an ended run are kept; `dotx prune` reports the runs that ended before that and still have events, and
`dotx prune --apply` deletes those events first (the runs, their answers, numbers, approvals and conversations stay),
so that the screenshots only they named are among the orphans it deletes next; a run without events shows an empty
timeline and an empty Actions card. Nothing deletes by itself, as before: the daemon never prunes. The journals keep
`[agent] undo_keep_days`.

**Touchpoints.** `.github/workflows/gate.yml` (new), `config.py` (`StoreConfig`), `defaults.toml`, `store/db.py`
(`events_of_runs_ended_before`, `delete_events_of_runs_ended_before`), `cli_prune.py`, README "Development" and the
home directory paragraph of "Configuration".

**Done when.** The workflow is green on this item's pull request; a unit test covers the retention queries; README
names the default (decision 25).

### 40. Cold start of `dotx`

**What.** `dotx --help` and the light subcommands start without importing `mcp`, AppKit and FastAPI: the CLI is
split into one module per subcommand family, each with its imports at the top, and `cli.py` dispatches by name.

**Why.** Measured 1 170 ms, of which `mcp` 430 ms, `desktop.session` 245 ms, `fastapi` 139 ms ("Measurements"); the
rule "imports at the top" stands, so the split is the way.

**Design, as built.** Three moves. The command models (one `FrozenModel` per subcommand, the `Command` union and
its `TypeAdapter`) live in `cli_commands.py`, which imports pydantic and the type aliases only (`Effort`,
`ApprovalMode`, `BrowserMode`, `SnapshotScope`); `KNOWN_PROVIDERS` moved to `llm/types.py` so the parser does not
pull the router and the providers. The commands that lived in `cli.py` moved to family modules with their imports at
the top: `cli_run.py` (run, resume, chat, with `follow_run`, which the CLI smoke fixture imports from there now),
`cli_models.py` (models, auth), `cli_doctor.py`, `cli_desktop.py`, `cli_memory.py`, and `browser login` joined
`cli_browser.py`. Every family module ends with `dispatch(command)`, a match over its own commands; `cli.py` keeps the
parser, the validation and `main`, and its `dispatch` looks the family module up by the command's name
(`_FAMILIES`) and imports it then, the one place the program loads a module by name. Measured on the owner's Mac,
warm cache, five runs: `dotx --help` 80 ms (110 ms the first), from 600 to 760 ms before this item and 1 170 ms when
the item was written; `import dotagent.cli` 67 ms, of which `cli_commands` 38 ms. A heavy command pays its own
imports when it runs, as before.

**Touchpoints.** `cli.py`, `cli_commands.py` (new), `cli_run.py`, `cli_models.py`, `cli_doctor.py`,
`cli_desktop.py`, `cli_memory.py` (new), the other `cli_*.py` (their models moved out, a `dispatch` added),
`llm/types.py`, `llm/router.py`, `server/routes_system.py`, `tests/fixtures/dot_cli_run.py`, README "Development".

**Done when.** `dotx --help` under 400 ms p50 (80 ms measured); the unit tests and the CLI smoke tests pass; every
light command runs end to end on a throw-away home (`prune`, `eval list`, `skills list`, `campaign list`,
`schedule list`, `playbooks list`, `auth status`, `memory list`, `undo`, `models`).

### 41. An own crawler: fetch, render, crawl, extract

**What.** A `web/crawl.py` layer and two tools. `web_crawl(url, scope, max_pages, depth)` follows links within the
scope (same host or a path prefix), with concurrency, per-host pacing (`web/gate.py`), robots.txt read and reported
(the owner decides whether it binds, a config key), a per-run cache, and returns one clean markdown document per page
(the `web/text.py` reader) with the link graph. `web_extract(url_or_pages, schema)` returns typed data: the model
names fields, the extraction runs on the fast model (item 24) over the markdown, validated into the schema, with the
source quote per field. Pages whose plain fetch yields little text (a JavaScript shell) are rendered once in Dot's own
headless Chrome through the existing browser session and read from the DOM; PDF goes through `web/pdf.py` as today.

**Why.** The owner asked for "our own Firecrawl that really works". Today `web_fetch` reads one page at a time with
plain HTTP, `web_search` is DuckDuckGo or the hosted Ollama search, and long research runs are throttled (decision
14). A crawler that renders when needed, respects pacing and returns markdown plus typed extraction removes the
page-by-page round trips from research runs and gives campaigns a steady source.

**Design.** Pure functions: `plan_frontier(graph, scope, seen)`, `should_render(fetched)` (text density under a
threshold or a known shell marker), `to_markdown(document)`; the connector is the existing `WebClient` plus one
browser page borrowed from `browser/session.py`. Limits in `[web.crawl]`: `max_pages = 50`, `max_depth = 3`,
`concurrency = 4`, `per_host_spacing_s`, `render = "auto"|"never"|"always"`, `robots = "report"|"obey"`. Results
over `tool_output_chars` go to the run's folder as files and the tool returns the index. No external service, no
key. The addresses rule (`web/addresses.py`) holds for every fetched and rendered page.

**Design, as built.** `web/crawl.py` holds the pure parts (`scope_of`, `in_scope`, `page_links` from the HTML's
anchors with `<base>` honoured, `plan_frontier`, `looks_like_shell`, which `web_fetch`'s JavaScript note now shares,
`robots_rules`) and `crawl()`, which runs them breadth first against the existing `WebClient` (so the address rules
and the browser's host lists hold for every page, as for `web_fetch`) with a `RequestGate` per host, a semaphore of
`concurrency`, `robots.txt` read once per host, and the page limit applied per level. The reader is `web/text.py`'s
`html_to_text`: its output already is the markdown-like text of `web_fetch` (headings, lists, tables, links), so no
`to_markdown` was written; the link graph is each page's in-scope links. Rendering is a callable the tool hands in: the
run's own browser tab (`open_url`, then `read_text` of the whole page), one page at a time, so `web_crawl` is not
parallel-safe; a browser that cannot render leaves the plain text and says so in the page's `problem`. The cache is per
call (addresses seen), not per run: the crawl folder in the workspace is the durable copy. `web_extract` builds one tool
spec from the fields the model names (a `value` and a `quote` per field, all required), asks the fast model once per
source with the document as the user message, validates the answer and checks each quote against the source's
normalised text; an unverified value is kept and flagged, not dropped. Sources are addresses or workspace files, read
one after another (deterministic, and the fast model is fast). Limits in `[web.crawl]`: `max_pages 50`, `max_depth 3`,
`concurrency 4`, `per_host_spacing_s 0.5`, `render auto`, `robots report`, `page_max_chars 20000`. Results over the
tool's budget become `NNN.md` files plus `index.md` under `<workspace>/crawl/<host>-<time>/` and the tool returns the
index with the paths. No `doctor.py` row: nothing to check that `web_fetch`'s rows do not. The research eval of the
plan needs the network and a live model, so it is not in the offline harness (decision 1's live runs can add
`--task` research goals later); the measurement here is the smoke test on the fixture site.

**Touchpoints.** `web/crawl.py`, `web/extract.py`, `tools/web.py`, `config.py` (`CrawlConfig`), `defaults.toml`
(`[web.crawl]`), `llm/calls.py` (purpose `extract`), README "Tools", `tests/unit/test_crawl.py`,
`tests/smoke/test_crawl.py` (a 30-page fixture site with a JavaScript-only page, a PDF, a robots.txt and a folder
outside the scope; headless Chrome renders the JavaScript page).

**Done when.** The smoke test crawls the fixture site within the limits (the depth cap of the config holds against a
larger request), renders the JavaScript page, writes a crawl that does not fit one result to files, and extracts two
typed fields from three pages with source quotes checked (one quote deliberately wrong and flagged); the pure parts
have unit tests. README documents the tools and the limits. (All done.)

### 42. TypeSafe as a tool of the agent: many items judged in one call

**What.** A tool `judge_items` (acting and research runs) that takes one question and a list of candidates (posts,
search hits, links, playbooks, each with an id) and returns TypeSafe's answer for each in one request, ranked: a Noul
probability or a Score level. The main model reads a short ordered list instead of weighing every item in its own
context, and keeps the decision. The judge's failures are told apart, as TypeSafe's guidance asks: a 422 or a
malformed answer is a code or model error, logged with the request, and does not pause the judge the way an outage
(401, 429, 529, a timeout) does.

**Why.** Sorting and filtering many items is where the main model spends seconds and tokens on a judgment a typed
model gives in about 0.3 s, for input tokens only. In `yolo` this is the gain item 16 never had. Its first users: the
posts of item 43, the search hits of item 34, the links item 41's crawler may follow, the playbook of item 36 that
fits a task.

**Design.** Read first: the re-ranking cookbook (`https://docs.typesafe.ai/cookbooks/rerank_typesafe.md`), the Score
page (`https://docs.typesafe.ai/primitives/score.md`) and State (`https://docs.typesafe.ai/concepts/state.md`). One
question per candidate in one request (the ids stay in code; the question carries the whole meaning); the batch size
is set by measured request size and latency. The tool uses `[judge]`'s key, address and model but not its `provider`
switch, which stays the click and claim judge of item 16: it is offered when `TYPESAFE_API_KEY` resolves.

**Touchpoints.** `llm/typesafe.py`, `agent/judge.py`, a new tool under `tools/` and its registration,
`agent/prompt.py` (when to use it), `llm/router.py` (`unavailable_reason`), `defaults.toml`, `config.py`, README
"Typed judgments".

**Done when.** A smoke test on the stand-in service ranks a list and checks the request; a 422 from the stand-in
leaves the judge on, with an error event; the hosted test ranks a fixed list (posts on and off a topic) with every
relevant one above every other; item 22's numbers on an eval task that filters a list show fewer seconds or tokens
with the tool.

**Design, as built.** The connection moved out of the judge: `agent/judge.py` has a `TypeSafeService` (one client,
the key read at the first request and again after `retire`, the pause of an outage, `nouls` and `scores` that answer
`None` while paused) which the runtime builds once (`Runtime.typesafe`) when `[judge] provider` is `typesafe` or the
key named by `api_key_env` resolves; `TypeSafeJudge` is a thin wrapper over it and `judge_for(config, typesafe)` takes
it. The failures are told apart in `TypeSafeService._failed`: `request_error` (HTTP 422, or an answer outside the
reference, `ProviderProtocolError`) is logged as `judge_error` with the request and the answer, emitted as a
`judge_error` event (CLI line and timeline note) and raised to the caller, with no pause; everything else (401, 429,
529, a missing key, a timeout, a transport error) pauses the service as before. `llm/typesafe.py` gained
`ScoreQuestion(id, instructions, levels)`, `ScoreAnswer(score, probabilities, confidence)` and `TypeSafeClient.scores`;
a score answer is read as the reference documents it (the expected level from 0 to one less than the number of levels,
one probability per level under its index). The tool, `tools/judge_items.py`, takes `question`, `items` (id of letters,
digits, `-` and `_`, up to 64 characters, and text; 500 items at most, distinct ids) and optional `levels` (none, or 2
to 10); without levels one Noul per item, with levels one Score per item whose criteria are the levels. The state of a
request is `{"candidates": {id: text}}` and each item's question is "About the candidate at `candidates.<id>`:
<question>", so the ids never reach the model and the question carries the whole meaning, as TypeSafe's reranking
cookbook does it. The list goes in requests of `[judge] items_per_request` items; the answers come back ranked, the
highest first (`ranked`), one line per item with its id, its value, the likeliest level when there are levels and the
start of its text (`render`). It is in the core group, acting and research modes, kind `network`, risk low, with the
service's host as the target; it is registered in `runtime._tool_groups(config, judge_items)` when the key resolved at
start (so a key saved later needs a restart, which the README says), whatever `provider` is. `dotx doctor`'s judge row
now says whether judge_items is offered. Measured against TypeSafe with jev-1.13.0: 12 posts, 5 on Python packaging,
ranked in one request in 0.59 s with every on-topic post above every other (min 0.9 against max 0.06 on the synthetic
lists); one request of 25, 50 and 100 short posts took 0.40, 0.27 and 0.28 s, so `items_per_request = 100`. An eval
task `filter-headlines` (24 headlines, 6 about electric cars, write them to a file and say how many) is the list a live
run can filter with or without the tool; its item-22 numbers need a live model and are an owner decision (26 below).

### 43. Typed judgments in X campaigns: what to answer, the best draft, a check

**What.** In an X campaign (items 12 and 18), the agent ranks the posts it could answer with item 42's tool on three
questions (on the account's topic; written by a person, not a bot or spam; worth a reply that adds something), picks
the best of its drafts with a Choice, and checks a draft before posting it (off topic, repeats an earlier post of the
account, against a rule the campaign's goal names).

**Why.** Growth on X comes from answering the right posts well; today the main model picks from what the screen
shows, one judgment at a time. Several scores over many candidates, weighted in code, is TypeSafe's composite-scoring
pattern (`https://docs.typesafe.ai/patterns/composite-scoring.md`).

**Design.** The X playbook of item 18 says when to call the tool. The weights of the three scores live in the
campaign's ledger, so the owner can change them without asking TypeSafe again. Thresholds are set on a labeled sample
of the owner's own feed (posts they would and would not answer), not on a cookbook's numbers.

**Touchpoints.** The X skills of item 18, `tools/campaign.py`, item 42's tool, README "Campaigns".

**Done when.** On the labeled sample, the precision of the top ten is measured and written in the log; a live
campaign session records the scores of the posts it answered.

**Design, as built.** Three tools in `tools/posts.py`, offered to the sessions of a campaign (the loop leaves them
out of every other run, with the campaign tools) when TypeSafe's key resolves, on the `TypeSafeService` of item 42.
`rank_posts(topic, posts[id, author, text])` asks three Nouls per post in one request, the posts by id in the state
(`{"topic", "posts": {id: {author, text}}}`) and each question naming its post's path: on the topic, written by a
person (not a bot, a brand's broadcast, spam, a giveaway or engagement bait), worth a reply that adds something. The
composite is the weighted mean of the three probabilities with the weights of the campaign's ledger, the newest entry
whose numbers hold `w_topic`, `w_person` and `w_reply`: the first ranking writes a note with equal weights, and a
`campaign_note` with those numbers changes them without asking TypeSafe again (composite scoring, as the pattern says:
code owns the weights). The scores of every ranking go in a `posts_ranked` event of the session, shown as a note in the
timeline. `pick_draft(topic, post, drafts[2..10])` asks one Choice whose criteria are the drafts by id (the Choice
answer, `{"choice", "confidence", "probabilities": {option: p}}`, joined `llm/typesafe.py` as `ChoiceQuestion` /
`ChoiceAnswer` / `TypeSafeClient.choices`, and the service got `choices`). `check_draft(topic, post, draft,
earlier_posts, rules)` asks in one request whether the draft is off topic or beside the point, whether it repeats one of
the account's earlier posts (when any are given) and, one question per rule, whether it breaks a rule the goal names;
a check at `[judge] draft_check_threshold` (0.5) refuses the draft with every probability listed. The campaign prompt
block says when to call them; the owner's X skills of item 18 are theirs and were not changed. Measured against
TypeSafe with jev-1.13.0 on a labeled sample of 20 posts for an account on Python packaging (10 worth answering, 10
not: bots, brands, closed statements, other topics; in `tests/smoke/test_hosted_services.py`): one request of 60
questions in 0.64 s, precision of the top ten 1.00, the lowest answerable post at 0.93 and the highest other at 0.53
(an espresso question: a person, worth a reply, off topic). The sample is synthetic and the threshold provisional: the
owner's own labeled feed is decision 27, and the scores a live session records are read from its `posts_ranked`
events.

### 44. Typed checks before a model call: the page after an action, the goal

**What.** (1) A browser action can name what it expects to see after it (`expect`: "the reply shows under the post");
its result carries TypeSafe's probability that the page's text shows it, and whether the page is a login wall, a
CAPTCHA or an error page, so the next turn starts from that answer instead of reading the whole page. (2) The goal
check of a run asks TypeSafe first, as item 16 does for a campaign's claim: a clear no is refused at once, a clear yes
and the uncertain middle go to the review model. (3) The judge's click questions apply in `ask` and `auto` only: in
`yolo` they changed no decision and cost a request per click (decision 9), so the owner can keep the judge on for (1)
and (2) without them.

**Why.** A typed check takes about 0.3 s; a model turn or a review call takes seconds (item 9's numbers say how
many). The pattern is verify and escalate (`https://docs.typesafe.ai/cookbooks/citation_check.md`,
`https://docs.typesafe.ai/cookbooks/sde_cascade.md`).

**Touchpoints.** `tools/browser.py`, `browser/safety.py`, `agent/judge.py`, `agent/review.py`, `tools/campaign.py`,
`defaults.toml`, README "Typed judgments".

**Done when.** Fixture pages (a login wall, an error page, a posted reply) are judged right in a smoke test; item
22's numbers on the eval set show fewer turns or seconds per task with the checks; with `[judge] provider =
"typesafe"`, a click in `yolo` sends nothing to TypeSafe.

**Design, as built.** All three go through the `Judge` of the runtime, so `[judge] provider = "typesafe"` switches
them on together and `"none"` leaves everything as it was. (1) `browser_act` took an optional `expect` (a few words
on what the page should show after the steps). When it is given, the tool reads the page's visible text after the
batch (6,000 characters at most) and asks `Judge.judge_page` four Nouls in one request over `{"page": {url without
query and fragment, title, text}, "expected"}`: whether the page shows what was expected, whether it is a login wall,
a CAPTCHA, an error page (`EXPECTED_QUESTION`, `PAGE_QUESTIONS` in `agent/judge.py`). The result opens with one
Dot-written line above the fenced page ("Typed check of the page after the steps: what you expected shows 0.90; login
wall 0.05, CAPTCHA 0.05, error page 0.05."), the numbers only: no threshold acts on them, the next turn reads them.
Without `expect`, or with the judge off or paused, nothing is asked and the result is as before. (2) The goal check of
a run in goal mode (`loop._ask_checker`) asks `Judge.judge_claim` first when the run claims success, on the same state
the goal checker reads (`claim_state`, now shared with `campaign_achieved`); a claim below `target_threshold` comes
back as a `continue` verdict whose reason carries the judge's number, so the run is sent back without the review
model's call, and the `goal_check` event records it like any verdict; a clear yes and the middle go to the review model
as before, and a run that ended failed is not judged. (3) The click judgments in `browser_act`'s classification run
only when `ctx.approvals != "yolo"`. Measured on the stand-in in `tests/smoke/test_judge.py`: a posted reply, a
login wall and an error page get the right numbers through the real tool and headless Chrome; in `yolo` a click on
"Boost" makes no request; a goal-mode run of five finishes that claim success is sent back five times on the judge's
0.10 and never reaches the review model. The eval-set numbers (fewer turns or seconds with the checks) need a live
model and a key: decision 28.

### 45. A step's model chosen by a typed judgment

**What.** Before a step, a Choice over the run's models (the fast model of item 24, the main model, a helper of item
15) from the step's state (the task, the last result, the failures so far): routine steps go to the fast model, hard
ones to the helper.

**Why.** The fast model answers a tool step in 889 ms, the default main model in 2 798 ms (item 24), and most steps of
a long run are routine. The routing shape is in the function-calling cookbook
(`https://docs.typesafe.ai/cookbooks/function_calling.md`) and speculative fan-out
(`https://docs.typesafe.ai/patterns/fan-out.md`).

**Risk.** A change of model loses the prompt cache that item 19 keeps warm, and a weaker model can add steps: the net
is measured, not assumed.

**Touchpoints.** `agent/loop.py`, `runtime.py`, `llm/router.py`, item 42's client, `defaults.toml`, `config.py`.

**Done when.** Item 22's numbers on the eval set show less time per task at the same success rate; otherwise the
item ends as a measured no, written in the log.

**Design, as built.** The mechanism is built and off (`[agent] step_router = false`); the measurement that decides
whether it stays is the owner's (decision 29), since it spends model quota on both arms. `agent/stepmodel.py` holds
the pure parts: the state (the goal, the number of the step, what the previous step brought back since the model last
spoke, the tool calls that failed so far, the steps in a row that ended with a failure), one Choice over the models the
run can take the step on (`fast`, `main`, `helper`, each described by the kind of step it is for; `main` is always an
option), and the policy: `fast` or `helper` takes the step only when its probability reaches
`step_router_threshold` (0.6; the likelier of the two when both do), else the run's own model. In `agent/loop.py` the
candidates are fitted once at the start of the run (`_routing`: `[models] fast` and the first available helper of
`[models] helpers`, each when it is not the run's own model, can call tools and has room for the system prompt and the
tools, as a standby model is fitted); before each step `_route_step` asks `Runtime.typesafe` and picks the candidate,
unless TypeSafe is paused or rejects the request, the candidate's window is smaller than the step's estimated history,
or the history carries images the candidate cannot see. The step's request goes to the chosen model with the run's
reasoning level mapped onto its ladder; the assistant message records which model answered; a `step_routed` event
(the step, the model, the option, the probabilities, the confidence) is a note in the timeline. A routed model that
cannot answer has the step asked again on the run's own model, with a `step_route_failed` event, and the standby logic
stays for the run's own model. The run's scaffold, context fitting and prompt cache are the run's own model's: a routed
step pays the cache miss the item's risk names. Proven offline in `tests/smoke/test_judge.py`: a scripted run of four
steps against the stand-in, which routes by a marker the previous step echoed, sends step 2 to the fast model and step
3 to the helper, keeps the others on the run's model, and asks one judgment per step with the step's state. Not
measured here: whether it saves time at the same success rate on a live model (decision 29); until then the item is
neither a yes nor a measured no.

## Part 4: the face of Dot: web, desktop, mobile (planned 2026-10-11)

The owner's fourth ask, on 2026-10-10: find the design of OpenAI's "Dot" and make Dot's look like it and better, with
desktop and mobile apps planned. The product is OpenAI's **dots** (DevDay, 29 September 2026): an always-on agent
inside ChatGPT with a cloud computer, reached in ChatGPT, Slack, Teams or a call, with a mascot the owner names and
dresses. What could be read on 2026-10-11 (the launch post on openai.com answers 403 to agents; the docs at
learn.chatgpt.com/docs/dots and its subpages read in full; MacStories, TechCrunch, Fortune and Flavio Copes as
reviews; the screenshots are under /tmp/dots-docs on the Mac that did the reading, not committed):

- **Where it lives.** One entry under "New chat" in the ChatGPT sidebar, named after the dot. Its *profile* holds
  **Activity** (every task with progress, files, results and the requests waiting), **Scheduled**, **Computers** and
  **Add** (contact methods). No model picker, no effort level: the dot chooses.
- **Its computer.** A window titled "Alfred's Computer" on a warm yellow gradient: the cloud browser's page, a dock of
  three icons (browser, terminal, files) and a pill under it, "**Alfred** has control · **Take over**"; the pill
  turns into "You have control · Return control". A separate private form for website sign-ins, with "Take over to
  sign in" and "Not now".
- **Permissions.** Automatic review before every action that touches an account; *custom rules* under Settings ›
  Personalization › Permissions with four answers per action: "Take action without asking", "Take action when you
  say so", "Ask before taking action", "Hand off to you".
- **Across devices.** Set up on desktop or phone, the same dot and thread on both; mobile web not supported.
- **Visual language.** White surfaces, black pill buttons, one warm accent per dot, a round character with eyes and
  accessories (berets, bow ties, glasses). Reviewers praised the pacing of the first conversation (no tool access
  until asked), the one synced thread and the absence of knobs; they faulted the single dot per account, a thread
  search that does not work, memories that cannot be read or deleted one by one, and the mascot as "entirely
  marketing" (Fortune, Ed Zitron) that makes the agent look safe before it has earned it.

**Where Dot already stands against it.** Dot's web UI is the same shape (chats in a sidebar, one page, a composer in
the middle, Settings on a page of its own, two looks, light and dark, a phone layout under 860 px) and already has
what dots lacks: any number of threads and campaigns, a search over chats, a Memory tab where every note is read,
corrected and forgotten one by one, Rules the owner writes, an Actions card with undo, a full event timeline, and the
computer is the owner's own Mac with the owner's own Chrome profile, so no site "blocks the cloud browser" and no
session has to be rebuilt. What dots has and Dot has not: a *live view* of the computer with a handover, one
*Activity* list across everything in motion, the four-answer rule, a face that tells the state at a glance, and a
shell on the desktop and the phone.

**Principle for part 4.** Better means *more visible and more honest*, not cuter: every state the agent is in has one
mark that is the same in the sidebar, the run head, the tab title, the notification and later the menu bar; what the
agent is doing right now can be watched and taken over in one click; nothing is hidden behind a character. The
mascot stays a dot (Dot's yellow face, no accessories), and a name is optional. Every item is checked in headless
Chrome on a throw-away daemon in both looks, light and dark, wide and at phone width (item 41's check), and no item
widens what a run may do.

**Order and dependencies.** 46 (the live view) and 47 (Activity) first, both function; 48 (rules in four answers)
third; 49 (the face as the state) and 50 (the visual system) next, form on top of function; 51 (desktop shell) and
52 (phone) last, shells around a web UI that is already right. 51 and 52 each start with an owner decision (30, 31).

### How to start part 4 (the handoff prompt)

> Read `ROADMAP.md` from the top, then "Part 4" and its status table. Check the ground. Take the first item of the
> part 4 table that is not `done`, in the order above. The protocol is part 3's: read the code, correct the design
> where the code disagrees, `in progress`, build under the house rules, check wide and phone in both looks, update
> README and defaults, branch `item-NN-short-name`, PR, squash-merge, hash and log line. Screenshots of a throw-away
> daemon go into the PR, never the owner's data. The owner reads Turkish; everything written is English.

### Status, part 4

| #   | Item                                                                     | Status  | Commits |
| --- | ------------------------------------------------------------------------ | ------- | ------- |
| 46  | Dot's computer: the live view and the handover                           | done    | f4d3754 |
| 47  | Activity: everything in motion in one list                               | done    | 2282e57 |
| 48  | Rules in four answers                                                    | done    | 0811f52 |
| 49  | The face as the state                                                    | done    | 72f6168 |
| 50  | The visual system                                                        | done    | fbf5b34 |
| 51  | The desktop shell                                                        | done    | 8b4415d |
| 52  | Dot on the phone                                                         | done    | 2386ae7 |

### 46. Dot's computer: the live view and the handover

**What.** A panel on the run page, "Dot's computer", that shows the page Dot's Chrome is on while a run works in it
(a screencast, not a screenshot a step old), and a pill under it: "Dot has control · Take over". *Take over* pauses
the run at its next tool call (the run waits as it waits for an answer, with the note "you have the browser"), brings
the Chrome window to the front (it is a real window on the owner's Mac: `[browser] headless = false`), and the pill
reads "You have control · Return control". *Return control* resumes the run with a fresh snapshot of the page, so the
model sees what the owner did. In `attach` mode the tabs are in the owner's own Chrome already; the panel shows the
tab Dot works in and the pill does the same. A desktop (item 13) run shows the window view the same way.

**Why.** It is the one thing dots has on Dot that a reviewer sees first, and Dot can do it better: the computer is
local, the handover is a window the owner already has, and a sign-in, a CAPTCHA or a payment is done in the owner's
own session, not a cloud browser's (dots hands those off or fails: "Some websites block cloud browsers").

**Design.** `browser/driver.py` has a CDP session for screenshots (`new_cdp_session`); the screencast is
`Page.startScreencast` (JPEG, quality 50, at most 4 frames a second, `maxWidth` 1280) and `Page.screencastFrameAck`,
started when the panel opens and stopped when it closes or the run ends, never otherwise, so an idle daemon sends no
frames. Frames travel on the run's existing event stream (`GET /runs/{id}/stream`) as an unpersisted event
`browser_frame` with the JPEG base64 and the page URL, or, if that bloats the stream, on `GET /runs/{id}/screencast`
as a multipart stream. The handover is a run state: `POST /runs/{id}/takeover` sets a flag the executor checks before
each tool call (`agent/executor.py`), the loop waits on it as on a question, and `POST /runs/{id}/resume` clears it
and queues a snapshot. The panel lives in the Details panel's place when a run uses the browser and the owner opens
it; the choice is kept in the browser.

**Design, as built (2026-10-11).** `BrowserSession.screencast(thread_id)` is an async generator over Chrome's
`Page.startScreencast` (JPEG 50, longest edge 1280) on the thread's current tab, read without the thread's lock so it
never waits behind an action and never starts a Chrome; it yields `None` once a second while nothing paints, follows
the tab the model switches to, and stops the cast when the reader stops. `GET /runs/{id}/screencast` is a
server-sent stream of `frame` events (the address and the JPEG, base64), keepalives in between, ending with the run;
the web client reads it with the same fetch-based SSE reader as the run stream, so the token stays in a header. The
hold lives in `RunGate` (`hold`, `release`, `wait_free`, `held`): the executor waits in `wait_free` after
`authorize`, so the wait counts as time at the gate, not the tool's, and the result of the call that waited ends with
the hold note (how long, take a snapshot first). `RunManager.take_over` refuses a headless browser and a run already
held, focuses the tab (`BrowserSession.focus`) and emits `takeover`; `hand_back` emits `handback` with `held_s`;
`RunDetail.held` says the state on a reload. The card `static/js/computer.js` sits first in the details panel with
Watch / Close the view and the pill; `timeline.js` notes both events and tells the card; `dotx run` prints them. The
loop was not touched: the executor is the one place every tool call passes.

**Touchpoints.** `browser/types.py`, `browser/session.py`, `agent/gate.py`, `agent/executor.py`, `agent/manager.py`,
`server/views.py`, `server/stream.py`, `server/routes_runs.py`, `static/js/computer.js`, `views/run.js`,
`timeline.js`, `run.css`, `cli_ui.py`, README, `tests/smoke/test_takeover.py`.

**Done when.** A smoke test on the fixture server opens the panel, receives frames, takes over (the run waits, the
next tool call does not run), returns control (the run goes on with a snapshot event) and finishes; a run without the
panel open sends no frame (counted on the stream); wide and phone, both looks.

### 47. Activity: everything in motion in one list

**What.** Two sections at the top of the sidebar, above the chats: **Waiting for you** (approvals, questions, a
campaign's questions, a takeover the owner holds) and **Working** (runs, their delegated runs, campaigns in a
session, a schedule firing), each row with what it is, the channel it works in (browser, files, shell, desktop, mail),
since when, and what it needs; and a page `#/activity` with the same rows plus the recent (ended today) and filters
by kind and state. A row opens the run, the approval or the campaign.

**Why.** dots' Activity is the one place its reviewers look; Dot has the data (`/runs`, `/approvals`, campaigns,
schedules, the live dot in the sidebar) in four places. One list is also what the phone and the menu bar (51) show.

**Design.** One read model, `server/views.py:ActivityView`, built in `server/routes_data.py` from the runs that are
not ended, the pending approvals and questions, the campaigns with an active session and the schedules due within
the hour; `GET /activity`; the sidebar reads it once and keeps it fresh from `GET /stream` (the global stream already
carries run state changes). The sections fold, the choice kept in the browser; an empty section is not shown.

**Design, as built (2026-10-11).** `server/activity.py` builds `ActivityView` (rows with section, kind, title,
detail, channel, since, href, run id, status) from the manager's live runs, the pending approvals and questions
(kind `approval` or `question` by the record's severity), the questions runs left for the user (`left`, listed even
when the run has ended), the runs the user holds (`takeover`, item 46), the live runs that wait for nothing (kind
`campaign` for a session, with the campaign's title, `delegated` for a child run, `run` otherwise), the enabled
schedules due within the hour and the runs that ended today; the channel of a run is that of its latest tool result.
`GET /activity` lives in `routes_runs.py`. The sidebar loads it with the chats and again on the lifecycle events,
which now include `takeover` and `handback`; the sections are hidden while empty and sit between the search and the
chats, each row a chat row with a second line. `#/activity` (`views/activity.js`, the Activity link in the foot) shows
the three sections with a segmented filter (all, runs, campaigns, schedules), a kind icon, the state badge and the
age, and follows the lifecycle stream and the status refresh while something works. No new config.

**Touchpoints.** `server/activity.py`, `server/views.py`, `server/routes_runs.py`, `server/stream.py`,
`static/js/sidebar.js`, `views/activity.js`, `main.js`, `shell.css`, `app.css`, README, `tests/smoke/test_activity.py`.

**Done when.** A smoke test with a run waiting for approval, a run working and a campaign in a session shows three
rows in the right sections, and the row of the approval opens it; wide and phone, both looks.

### 48. Rules in four answers

**What.** The Rules tab speaks the owner's language: a rule reads "When Dot would use {tool}, matching {pattern}:
*do it* / *do it, whatever it has read* / *ask me first* / *hand it to me*". The four answers are the four behaviours
the policy has had since the hand-off rules of Part 2 (`allow`, `preapproved`, `ask`, `handoff`); what the item adds
is the wording, the sentence form, a segmented control in place of the select, and the takeover of item 46 offered on
a hand-off question of a browser step.

**Why.** dots' four rules read well and the reviewers said so; Dot's rules have the same shape (tool glob, regex,
decision) and the same four outcomes, but named for the engine (`preapproved`) rather than for the owner.

**Design, corrected before building.** The plan said the fourth answer was new and that the second meant "do it when I
said so in this chat". Neither held against `policy/engine.py`: `owner_handoff` and the `handoff` behaviour exist, the
approval card already says "Dot needs you to take over this step" with "I did it" / "Skip this step", and on "I did it"
the executor turns the `HandedOff` into a tool result ("The user handled this step themselves. Continue from there.") so
the run goes on. The second behaviour, `preapproved`, means "skip the question untrusted content raises too"; a rule that
would hold only "when I said so in this chat" has no decision behind it and would need a judgment the policy does not
make, so the second answer is worded for what the engine does: *do it, whatever it has read*. The campaign's
`do_not_ask_again` note stays the session-scoped form of that.

**Design, as built.** `rules.js` keeps the four behaviour ids and renders them as answers: a `segmented` control (class
`answers`, wrapping on a phone) with a hint per answer, a live sentence above the form ("When Dot would use `shell`,
matching `^git push`: *ask me first*."), and each rule row as the same sentence with the answer as a badge. The policy
is untouched. `approvals.js` adds, on a pending hand-off whose tool is `browser_*`, a "Take the browser over" button
that posts `/runs/{id}/takeover` and turns into "Return control" (`/handback`); a hand-off of any other tool shows no
offer. README (Safety) names the four answers with their engine words. Tests: `tests/unit/test_rule_answers.py` (each
answer on a matching action, tainted and not; a non-matching pattern leaves the preset to decide; a hand-off is the
user's in every approval mode) and `tests/smoke/test_rule_handoff.py` (a *hand it to me* rule on `write_file` stops
the write, the run asks with severity `handoff`, the Rules tab shows the sentence and the four answers wide and on a
phone in both looks, "I did it" is clicked in the run page, the run ends done and the file was never written).

**Touchpoints.** `static/js/views/rules.js`, `static/js/approvals.js`, `settings.css`, README (Safety), tests.

**Done when.** A unit test for each of the four answers; a smoke test where a *hand it to me* rule stops a write,
asks, and the run ends after the owner answers; wide and phone, both looks.

### 49. The face as the state

**What.** Dot's yellow face is the state mark everywhere: still when idle, a slow breath while it works, an amber
ring while it waits for the owner, a green tick for a moment when a run ends done, red when it fails. The same mark
in the sidebar row, the run head, the delegated-run chip (which has the ring already), the tab title's count, the
notification icon, the favicon, and in item 51 the menu bar. A name for Dot is a setting (Settings › General), used
in the home greeting and the run head; no accessories, no eyes that follow the cursor.

**Why.** dots' character is what people remember and what critics distrust; a mark that only ever tells the truth
about the state is the honest version of it, and the owner recognises Dot's yellow dot already.

**Design, corrected before building.** Read against the code: the face (`avatar()` in `icons.js`) had one look and a
blink of its core while the run head's badge said running; the sidebar rows (`status-dot`) and the delegated chips
(`status-ring`) already spoke the state in the same colours (accent pulse working, amber blink waiting, green done, red
failed), so they keep their dot and ring and only the face gains states. `attention.js` counted the approvals in the
title but the favicon was a fixed dark square with the blue dot of the first look, not the yellow face. The menu bar
is item 51's.

**Design, as built.** `face.js` holds the vocabulary and the mapping: `faceState(status)` (queued, running → working;
blocked → waiting; failed → failed; done and cancelled → idle, since a run being looked at later rests),
`faceStateAtEnd(status)` (done → the tick), `overallState(snapshot)` (an approval waits → waiting; an active run →
working; else idle), `faceSvg(state)` / `faceDataUrl(state)` (the standalone face in the light look's colours, with
`data-state` in its text) and the name (`dotName()`, `setDotName()`, preference `dot.name`, 24 characters, blank goes
back to Dot). `avatar(state)` carries `data-state` and a tick path; `app.css` animates per state (breath = the core
scales 1.4 over 2.8 s; waiting = amber ring blinking; done = ring green and tick shown for 80 % of 3 s then back to
rest, `forwards`; failed = ring and core red) and, under `prefers-reduced-motion`, shows the tick in place of the
core without motion. The run page sets the state from the status on every repaint except while the tick is showing,
and sets `done` only on the `run_finished` transition. `attention.js` paints the favicon with every change of the
snapshot and gives each notification the face in the state it tells of (waiting; done or failed at an end).
`favicon.svg` is the idle face. Settings › General has a Name card; the home greeting and the run head use the name.
README: New chat, the run head, the title paragraph. Test: `tests/smoke/test_face.py` reads the face's state and the
favicon through a run's life (idle on the home page; waiting on a question; working after the answer; done at the end,
idle when opened again; failed for a run whose model has no reply) and gives Dot a name, wide and on a phone in both
looks.

**Touchpoints.** `static/js/icons.js`, `attention.js`, `sidebar.js`, `views/run.js`, `app.css`, `favicon.svg`, README.

**Done when.** A smoke test reads the face's state attribute through the run's life (idle, working, waiting, done,
failed); the headless check in both looks shows the ring and the tick.

### 50. The visual system

**What.** The two looks become one system with named tokens: colour (surface 1 to 3, ink 1 to 3, the yellow accent
and its weak and strong, the four state colours), type (one sans, sizes 12 to 28 on a 4 px rhythm, the serif voice of
Paper), radius (8, 12, 20, pill), elevation (two shadows), spacing (4 px grid), and the computer panel of item 46 on
the warm gradient backdrop dots uses for its computer, which is also Dot's yellow. The composer, the chips, the
segmented controls and the pill buttons are redrawn on the tokens; density has two settings (comfortable, compact).

**Why.** The looks were built item by item; a system is what the desktop shell and the phone will inherit, and what
makes Dot look finished next to dots' white-and-black-pill language.

**Design, corrected before building.** Read against the sheets: the tokens existed (`--surface`, `--text`, `--accent`,
`--ok`/`--warn`/`--bad` and their weak and border forms, `--bubble`, `--shadow`, three radii) but the neutral look's
colours sat in `app.css` while `themes.css` carried only Paper; five literal colours (`#fff` on the switch knob, the
plan marks and the error toast; `#1a1a1a` and `white`/`black` in the computer backdrop), the shadow and scrim colours,
and every font size (nine distinct values, 11.5 to 22 px) and radius (4 to 20 px and the pill) were literals. The
density setting did not exist. The 4 px type rhythm of the plan would have moved the body text; the scale is the six
sizes in use, named.

**Design, as built.** `themes.css` holds every colour: the neutral look first (`:root`, light and dark), then Paper
on the same tokens, with the new tokens `--on-ok`, `--knob`, `--scrim`, `--scrim-strong`, the five shadows
(`--shadow-seg`, `--shadow-knob`, `--shadow-raised`, `--shadow-float`, `--shadow-overlay`, `--shadow-modal`) and
`--computer-backdrop` (the warm gradient of item 46 on `--bubble`). `app.css` holds the shape, type and spacing tokens:
`--fs-xs` 12 to `--fs-2xl` 22 (11.5 and 12.5 px fold into 12 and 13), `--radius-xs` 6 to `--radius-lg` 20 and
`--radius-pill`, `--space-1` 4 to `--space-6` 24, and the four paddings density changes (`--pad-card`, `--pad-row`,
`--pad-side`, `--gap-stack`), with `:root[data-density="compact"]` tightening them. Every sheet but `themes.css` draws
on tokens only; `tests/unit/test_css_tokens.py` keeps it so (no literal colour, font size or radius outside the token
blocks; the colour tokens declared in `themes.css` and not in `app.css`). Density is a browser preference
(`layout.js`: `DENSITIES`, `applyStoredDensity()` at start, `chooseDensity()`), chosen in Settings › Appearance under
Look. `views/controls.js` at `#/controls` (not linked) draws every control: the colour swatches, the type scale, the
five faces, the buttons, badges, chips and pills, the status dots and rings, the inputs, switches and segmented
controls (with the YOLO form), the notes and the banner, the three approval cards, the two bubbles and the three
panels. `tests/smoke/test_controls.py` renders it in neutral and paper, light and dark, wide and on a phone, and in
compact after choosing it in Settings; its screenshots are the PR's record.

**Touchpoints.** `app.css`, `themes.css`, `shell.css`, `run.css`, `settings.css`, README (Settings › Appearance).

**Done when.** Every control on the control page uses tokens only (a grep for hex colours outside `themes.css` finds
none); the headless check passes in both looks, wide and phone.

### 51. The desktop shell

**What.** A native window around the web UI: a menu bar item with the face of item 49 and the Waiting-for-you count,
a global shortcut for New chat, native notifications, drag-and-drop of files into the composer, and the daemon owned
by the launchd service as now (the shell connects, it does not run the agent). Decision 30 chooses the shell:
Tauri 2 (Rust, a few MB, the web UI unchanged) or Electron (TypeScript, the owner's language, 150 MB).

**Why.** The owner wants a desktop app; dots lives in the ChatGPT desktop app. The web UI is already the app, so the
shell is thin, and a menu bar item is where an always-on agent belongs on a Mac.

**Design, corrected before building.** The token lives in a plain file under the home directory (`api-token`,
`dotagent.core.paths.token_path`), not in the keychain: the shell reads that file, and `dotx url` is not needed. The
notification plugin of Tauri 2 has no click event on desktop (its Actions API is mobile only), so a click on a
notification brings Dot to the front and the sidebar's Waiting for you leads to the run; "a notification click opens
the run" is not something the shell can promise. The web UI's `Notification` API does not exist in WKWebView, so the
UI sends through the shell's bridge when it runs inside it, and the daemon's CSP lets the page reach the bridge
(`connect-src ipc: http://ipc.localhost`, which a browser never uses).

**Design, as built.** `desktop/` holds a Tauri 2 project: `package.json` with the Tauri CLI (`npm run dev`, `npm run
build`, `npm run icons`), `src-tauri/` with the Rust shell (`src/lib.rs`, about two hundred lines) and
`tauri.conf.json` (no window in the config: the window is made at start, on the UI when the daemon answers and on
`ui/down.html` otherwise, with the shell's drag-and-drop handler off so the composer's HTML drop works), a capability
that lets the UI at `http://127.0.0.1:*` use the notification plugin, and `ui/` with the down page. The shell reads
`DOT_HOME` or `~/Library/Application Support/Dot`, `[server]` of `config.toml` (defaults 127.0.0.1:8765) and
`api-token`. A thread asks `GET /api/activity/count` (new, `ActivityCount(waiting, working, state)`, the state as the
face's `overallState`) every second with a 0.8 s timeout, sets the tray icon per state (three PNGs rendered from the
face by `desktop/icons/render.py`, which draws the same shapes as `faceSvg`), the count as the tray title when
something waits, a tooltip, and navigates the window between the UI and the down page when the daemon comes or goes.
The tray menu has Open Dot, New chat (⌘⇧Space, also the global shortcut) and Quit; a left click opens the window;
closing the window hides it; the dock icon's reopen shows it. `attention.js` uses `window.__TAURI__.notification`
when it is there (permission, send) and the browser's `Notification` otherwise. README has a section; the gate gets a
`desktop` job (`cargo fmt --check`, `cargo clippy -D warnings`) with a Rust cache; the build is ad hoc signed and made
on the owner's Mac (`npm run build`).

**Touchpoints.** `desktop/` (new), `server/routes_system.py` (a `GET /activity/count` for the tray), README (a new
section).

**Done when.** The shell opens the UI signed in, the tray shows the count and the face state within a second of a
change, a notification click brings Dot to the front; built on the owner's Mac.

### 52. Dot on the phone

**What.** The web UI as a progressive web app first: a manifest, a service worker for the shell files, install to
the home screen, and web push from the daemon for what the desktop notifications say today (an approval, a question,
a run ended); then, if the owner wants a store app, a Capacitor wrapper of the same UI. The phone shows Activity
(47) first, then the chats. Decision 31 chooses how the phone reaches the Mac: the daemon bound to a Tailscale
address with the token, or nothing until then.

**Why.** dots continues the same thread on the phone; Dot's daemon is on the Mac, so the phone is a window to it,
which is exactly what a PWA is. The phone layout exists (the drawer under 860 px); what is missing is reach, install
and push.

**Design, corrected before building.** Decision 31 said "`[server] host` set to the Mac's tailnet address"; the
guard refuses any host but loopback on purpose (`require_loopback`), and an address of a tailnet over plain HTTP is no
secure context, so neither a service worker nor a push would work there. The way is `tailscale serve`: the daemon
stays on loopback, Tailscale presents it over HTTPS with a certificate, and the guard learns the name as `[server]
reach` (a `Host` of that name, an `Origin` of `https://` it). Lighthouse's PWA category no longer exists (removed in
Lighthouse 12); the shell is checked by what the browser does: the manifest is served with its type, the service
worker registers, the start page is Activity. The desktop shell's pushes cannot carry a click; the phone's can, and
do (the service worker opens the run).

**Design, as built.** `static/manifest.webmanifest` (start on `/#/activity`, standalone, the face as icons at 192
and 512, made from the desktop app icon) and `static/sw.js` (shell files network first with the copy as the fallback,
nothing under `/api` cached; `push` shows the message, `notificationclick` focuses or opens the window on the route),
registered from `main.js`; `index.html` links the manifest and the apple touch icon; the CSP gains `manifest-src
'self'`. `[server] reach` (default empty) reaches `guard.check_request` and `LocalGuard` as a frozenset; `dotx url`
prints one address per name. `notify/push.py`: `read_or_create_keys(vapid_path())` (py_vapid, PEM at
`<home>/vapid-key.pem`, mode 0600, on the runtime as `push_keys`, in the guarded files), `push_message(event, run)`
(the words of `attention.js`: a question, a hand-off, an approval, a run of its own ended), `send_push` (pywebpush,
aes128gcm, VAPID, TTL 600 s, a thread), `watch_pushes` (a task of the daemon beside the scheduler, fed by the bus;
a service that answers 404 or 410 makes the subscription go, any other refusal is a warning). Store table
`push_subscriptions` keyed by endpoint; routes `GET /api/push/key`, `POST /api/push/subscriptions` (HTTPS endpoints
only), `DELETE /api/push/subscriptions?endpoint=`. `attention.js`: Notifications on subscribes a browser that has a
service worker and a push manager (outside the desktop shell) and posts the subscription; off drops it; a browser
subscribed to push stays quiet itself. README section "Dot on the phone". Tests: `tests/unit/test_push.py` (keys made
once and private; each event's message or none; gone; the store), the guard's reach cases, `tests/smoke/test_phone.py`
(a local push service takes the encrypted, signed message of a question and of the end, a 410 deletes the
subscription; the manifest, the icon and the worker are served and the worker registers in a headless Chrome at a
phone width, which opens Activity).

**Touchpoints.** `static/` (manifest, service worker), `server/routes_system.py`, `notify/` (push), `cli_*` (VAPID
keys), README (a new section).

**Done when.** The manifest, the icons and the service worker are served and the worker registers on the throw-away
daemon; a push for a question and for an end reaches a push service encrypted and signed; a push arrives on a phone
on the owner's tailnet for an approval and opens the run (owner's run, logged by hand in this file).

## Part 5: the long run: context, the open web, the bill (planned 2026-10-11)

The owner's fifth word, on 2026-10-11, after part 4: "continue with the next tasks", with the standing trust of
2026-10-11 ("I believe you will take the right decisions; think it through for the future, performance and quality").
Parts 1 to 4 are done, so this part is planned from the only evidence that is new: the owner's own seven runs on the
daemon (2026-10-09 to 2026-10-10, read through the API and `dotx stats` on 2026-10-11; the goals stay out of this
file), the daemon's logs, and what the code does at the points they name. Every item here is judged by numbers that
`dotx stats` reads from those runs before and after, and by the eval set where a number can be had offline.

**What the owner's runs show.** Seven runs, all `yolo`, all at effort `max`, six on `ollama:deepseek-v4.1-flash:cloud`
(a model whose window Ollama reports as 1,048,576 tokens), one on `glm-5.3-flash:cloud`; three task runs from the UI
of 160, 217 and 260 steps (17, 18 and 51 minutes), four research runs delegated by them of 24 to 53 steps.

- *Context.* 96 steps a run, 40.1 k input tokens a step, 23.0 M input tokens over the six DeepSeek runs, 61 % of them
  cached. Those runs compacted 72 times over 573 steps (12.6 per 100 steps); the 260-step run 36 times, once every
  seven steps, and its summaries took 690 of its 1,309 model seconds (53 %); over the six runs the summaries took
  1,004 of the 3,323 model seconds, 30 % of the model time, written by the fast model (`gpt-oss:20b-cloud`) from up
  to 120,000 characters of transcript each time (the figures as item 53 reads them, 2026-10-11). The cause is the
  budget, not the ledger: `[agent] context_tokens = 48000` and
  `[providers.ollama] num_ctx = 65536` give the run about 48 k tokens, the fixed part and six kept tool results of up to
  12,000 characters fill it in a few browser steps, and the pinned ledger is bounded (40 actions, 60 addresses). 64
  `compaction_check_failed` warnings and 7 `summary_too_long` say the fast model's summaries often miss a section or
  run long, so the note leans on the pinned facts.
- *The open web.* 561 `web_fetch` calls, 75 failed, and 32 `web_search` calls, 18 failed; by cause, as item 53
  counts them over the six DeepSeek runs: 51 not found (HTTP 404 on career pages whose address the model guessed,
  `/careers/`, `/ura/`, `/yhteystiedot/`, and host names that did not resolve), 25 throttled (DuckDuckGo's bot check,
  its result page without results, HTTP 429 from LinkedIn), 3 blocked (HTTP 403 from search engines the model fetched
  as pages, Mojeek and Ecosia, when `web_search` was throttled), 9 in transit; and 7 calls refused for arguments that
  did not fit (5 of them `max_results` above 10, a turn lost to a validation message each). Those runs predate the restart
  that made the hosted Ollama search live for the daemon (item 34, decision 18): the throttling is unmeasured since.
  Two `browser_act` batches were refused for more than five steps and two for an action name outside the list.
- *The browser.* The daemon's error log holds two tracebacks of `BrowserSession._adopt_popup`
  (`list.remove(x): x not in list`: a popup closed while it was being adopted) and two `BrowserError: keeping the page
  focused failed` after an OAuth callback page (`.../auth/linkedin_apply/callback`) closed under `keep_focused`
  (`browser/driver.py:644`, a `TargetClosedError`). The log counts 13 `browser_context_closed`, 10 `launch_retry`,
  4 `browser_navigation_failed`, 2 `browser_disconnected`.
- *Delegation.* The three `delegate` calls of the 260-step run took 1,781 seconds (median 687): the tool is already
  parallel-safe and the children ran at once, so the time is the children's own (research runs of 5 to 14 minutes,
  0.8 to 2.1 M input tokens each), spent largely on the guessed addresses above.
- *What the owner can read.* The run page shows tokens; the time of a run, where it went (model, summaries, tools,
  waiting) and how often it compacted are in `dotx stats` only (item 22 left "numbers on the run page" for later).
- *The eval set.* Decision 28 found it has no browser task and no goal-mode run, so items 44 and 54 cannot be measured
  offline; its five long tasks are shell and file work.

**Principle for part 5.** The same currency as part 3 (steps and seconds, never feel), with two additions: (1) the
owner's own runs are the benchmark, read before and after each item with `dotx stats` (and, for the items that admit
it, the eval set's long tasks); (2) a failure stays a failure and says more: a tool that meets a dead address or a
closed page reports it with what it found, never guesses and never hides it behind a retry. Nothing here widens what a
run may do.

**Order and dependencies.** 53 (the numbers) first, since every other item is judged by them; 54 (long browser tasks
in the eval set) second, the offline instrument; 55 (context) and 56 (the web) next, the two costs; 57 (the browser's
closed pages) then; 58 (the bill on the run page) last, since it shows 53's numbers to the owner.

### How to start part 5 (the handoff prompt)

> Read `ROADMAP.md` from the top, then "Part 5" and its status table. Check the ground. Take the first item of the
> part 5 table that is not `done`, in the order above. The protocol is part 4's: read the code, correct the design
> where the code disagrees, `in progress`, build under the house rules, check wide and phone in both looks where a page
> changes, update README and defaults, branch `item-NN-short-name`, PR, squash-merge, hash and log line. Read the
> owner's daemon through its API and `dotx stats` only, never its files; the goals of their runs stay out of this
> file. The owner reads Turkish; everything written is English.

### Status, part 5

| #   | Item                                                                     | Status  | Commits |
| --- | ------------------------------------------------------------------------ | ------- | ------- |
| 53  | The numbers of a long run                                                | done    | 4967717 |
| 54  | Long browser tasks in the eval set                                       | done    | 7df5799 |
| 55  | Context that stays: a compaction every forty steps, not seven            | done    | 57a000e |
| 56  | The web without guessing                                                 | done        | 227e264 |
| 57  | The browser's closed pages                                               | done        | 8d137c7 |
| 58  | The bill of a run, on its page                                           | done        | 0014d77 |

### 53. The numbers of a long run

**Status: done.** Built on `item-53-long-run-numbers` (MYP-193).

**What.** `dotx stats` (per model and per run), the eval report and a new `GET /runs/{run_id}/stats` report what
part 5 is judged by: compactions per 100 steps and the share of model seconds they took; input tokens a step as a curve
over the step index (the bands of item 22); the failures of `web_fetch` and `web_search` by cause (not found: HTTP 404
and an unresolved host; throttled: HTTP 429 and DuckDuckGo's check; blocked: HTTP 403; transit; invalid arguments);
the turns lost to argument validation over every tool; the seconds a run waited for its delegated children.

**Why.** The numbers above were assembled by hand from the events; nothing reads them back, so no item of this part
could be checked. Item 22 built the acts, the bands, the sites and the cache share; this item adds the costs of the
long run next to them.

**Design (from the audit; correct it when the code disagrees).** `store/stats.py` gains the aggregates from the events
that already exist: `compacted` (count, and the `model_call` rows whose purpose is the summary, which item 9 tags),
`tool_result` of the web tools (the cause from the text's first line, classified by a pure function with its own unit
test), the `Invalid arguments` results of every tool, and the `delegate` results' seconds. New frozen models
`CompactionStats`, `WebFailureStats`, `DelegationStats` on `ModelStats`; a per-run `RunCosts` read by the new route
and by `dotx stats --run ID`. The eval `RunMetrics` and the scorecard carry compactions and their seconds. The baseline
of the owner's seven runs (the figures in the part's head) is written into this item once the code reads them, so the
difference after 55 and 56 is a number, not a memory.

**Design, as built.** The reading of the code corrected the plan in three places. The summary calls are already
tagged (`model_call` events carry `purpose = "compaction"`, item 9), so no new event was needed: `CompactionStats`
(count, per 100 steps, seconds, share of the model seconds) is computed from the counts and `TimeStats` that existed.
The `assistant` event already records the input tokens of its step, so the curve is the old `StepBand` with a
`tokens_median` (``None`` for a band whose steps recorded none). The delegated wait needs no new reading either: the
`delegate` tool's result carries the whole wait, so `DelegationStats` is its calls and seconds. New in
`store/stats.py`: every query takes a scope (`r.created_at >= ?` or `r.id = ?`), so one pipeline serves the window
and one run; `_TOOLS_SQL` reads the model of the run and the first 200 characters of the result's text, where the
cause is; `failure_cause(tool, text)` (pure, its own test) tells a call refused for its arguments (the text starts
with `Invalid arguments for`, any tool) from a web tool's not found (HTTP 404, a host name that did not resolve),
throttled (HTTP 429, DuckDuckGo's check, its result page without results), blocked (401, 403), in transit, other;
`WebFailures`, `rejected_arguments`, `collect_run_costs(database, run_id, now)` with `RunCosts` (the run, its minutes
to its end or to ``now``, its `ModelStats`, its tool seconds and the part at the gate, its tools). `ModelStats` gains
`compaction`, `web`, `rejected_arguments`, `delegation` (and loses the bare `compactions` count, now in
`compaction`). `Store.run_costs(run_id)` reads it on the read-only connection like the model profile;
`GET /api/runs/{run_id}/stats` returns it (404 for no such run); `dotx stats --run ID` prints it. The eval
`RunMetrics` gains `compaction_seconds`, `not_found`, `rejected_arguments` (``None`` in older reports), the
`Scorecard` `compactions_per_100_steps`, `compaction_share`, `not_found`, `rejected_arguments`, shown in the card and
in the difference line. Tests: `tests/unit/test_run_statistics.py` (the causes, the compaction numbers, the token
curve, the delegation, one run's costs and their rendering), `test_evals.py` (the card and the difference line),
`tests/smoke/test_activity.py` (the route on a finished scripted run, and 404).

**The baseline, the owner's runs read on 2026-10-11 with this code (the seven runs of 2026-10-09 and 10).**
`ollama:deepseek-v4.1-flash:cloud`, 6 runs, 573 steps: compactions 72 (12.6 per 100 steps, 1,004 s, 30 % of the
model time); web failures not found 51, throttled 25, blocked 3, in transit 9; 7 calls refused for their arguments;
delegated runs 3, waited 1,781 s; input tokens a step by band 22.0 k (1-5), 34.0 k (6-10), 39.6 k (11-20), 37.7 k
(21-40). The 260-step run alone: 51.4 min, 11.1 M input tokens (42.7 k a step), compactions 36 (13.8 per 100 steps,
690 s, 53 % of its 1,309 model seconds), delegated runs 2 waited 1,498 s, 67 % cached. `ollama:glm-5.3-flash:cloud`,
1 run of 217 steps: compactions 6 (2.8 per 100 steps, its calls untimed), 3 refused for their arguments, no web
failure.

**Touchpoints.** `store/stats.py`, `store/db.py`, `cli_stats.py`, `cli_commands.py`, `cli.py`,
`server/routes_runs.py`, `evals/metrics.py`, `evals/results.py`, `evals/scoring.py`, README "Measuring the agent",
`tests/unit/test_run_statistics.py`, `tests/unit/test_evals.py`, `tests/unit/test_channels.py`,
`tests/smoke/test_activity.py`.

**Done when.** `dotx stats` prints the five numbers per model (done), `dotx stats --run ID` and the route per run
(done), the eval report per task (done); the unit tests over a fixed event set check each (done); the baseline of the
owner's runs is in this item (done).

### 54. Long browser tasks in the eval set

**Status: done.** Built on `item-54-long-browser-tasks` (MYP-194).

**What.** Two long tasks against the test fixture site (the local server of the browser smoke tests): a *research*
task that reads ten or more pages, one of them linked to a dead address and one behind a redirect, and reports a
table; and a *goal-mode* task on a form whose outcome must be verified on the page (so `expect` and the goal check
fire). Both play with the scripted model offline and with a live model under `--model`.

**Why.** Decision 28: the eval set exercises neither the browser nor goal mode, so item 44 was unmeasurable and item
55 would be measured on shell tasks whose context never fills. Long browser tasks are where the owner's runs spend.

**Design (from the audit).** `evals/catalog.py` gains the two tasks under the long ids; the fixture pages come from the
smoke tests' site (extend it rather than add a second one); the scripted turns show the intended path (search the
site, follow links, meet the 404, report it); the checks read the report's table and the form's state. The runner
starts the fixture server for a browser task and the browser in headless mode in the attempt's home.

**Design, as built.** The reading of the code corrected the plan in three places. A task is data with no server, and
the smoke tests' fixture sites are each test's own (`ThreadingHTTPServer` in the test module), so the site became
part of the task: `EvalTask` gains `site` (a tuple of `SitePage`: path, status, body, redirect location; a `{name}` in
a body is filled from the query string) and `goal_mode`; `evals/site.py` serves the pages on a free loopback port for
the length of the attempt (`serve_site`) and `with_site` puts the address in for `{site}` in the goal and in the
arguments of the reference, which cannot know the port when it is written. The runner (`_attempt`) serves the site,
places the task, builds the scripted stand-in from the placed reference, and lets the web tools reach the loopback
address for such a task only (`site_config`: `web.allow_private_hosts`); every eval attempt runs the browser headless
(`eval_config`). The form task is not long (three tool turns): its point is `expect` on `browser_act` and the goal
check at the end, so it is a `browser` task outside `LONG_TASK_IDS`, while `careers-of-ten-companies` (twelve turns:
the index, ten careers pages of which Dogwood's answers 404 and Ginkgo's redirects, the finish) joins the long ids.
In a goal-mode task the scripted model is the review model too (`replay_reference` sets `[models] review`), so the
reference ends with the `goal_verdict` turn after `finish`, and the audit's final-call rule leaves such verdict turns
out. `TaskCategory` gains `browser`. The refs of the form's reference (`e5`, `e7`, `e9`, `e10`) were read from a
snapshot of the page in Dot's own headless Chrome. Tests: `tests/unit/test_evals.py` (the server: pages, 404, redirect,
the query placeholders; the placement of the address; the two tasks' shape), `tests/smoke/test_evals_runner.py` (both
references replayed through the real loop and tools: the careers attempt counts one not-found page and twelve or
more steps, the form attempt passes its goal check), and `dotx eval check` plays both in the gate.

**Touchpoints.** `evals/types.py`, `evals/site.py` (new), `evals/runner.py`, `evals/catalog.py`, `evals/audit.py`,
README "Measuring the agent", `tests/unit/test_evals.py`, `tests/smoke/test_evals_runner.py`.

**Done when.** `dotx eval check` plays both offline (done); `dotx eval run --task ID --model REF` plays them live (the
same path with a model instead of the script; the owner's paid run); the report shows compactions and fetch failures
(item 53) for them (done: the smoke test reads them from the attempts).

### 55. Context that stays: a compaction every forty steps, not seven

**Status: done.** Built on `item-55-context` (MYP-195).

**What.** A long run on a model with a large window compacts rarely, and a compaction costs a tenth of what it does
now. Targets, read with item 53 on the long tasks and on the owner's next runs: compactions per 100 steps under 5
(from 12.6 over the owner's DeepSeek runs, 13.8 on the longest), the summaries' share of model seconds under 10 %
(from 30 % over those runs, 53 % on the longest), input tokens a step not above today's 40 k on the same budget.

**Why.** The numbers in the part's head: the budget is 48 k tokens on a model that holds a million, and every seventh
step pays a 19-second summary of up to 120,000 characters to the fast model, which then misses sections.

**Design (from the audit; the measured choice is decision 33).** Three moves, each measured alone before it stays:
(1) the budget follows the model: `context_budget` already takes three quarters of the window, so what cuts it is
`[agent] context_tokens = 48000` and `num_ctx = 65536`; a model-aware default (the window `/api/show` reports, capped
by a ceiling that keeps the tokens a step within reason on a metered key) is measured against today's on the long tasks,
compactions and tokens a step side by side, since a wider window costs more tokens a step at the same cache share;
(2) the summary reads less: the head sent to the writer keeps tool results as their summary lines only (`summary_part`
does part of this) and the earlier continuity note is carried as it is, not re-summarised; (3) the kept tool results
shrink earlier: `shrink_old_tool_results` at the soft limit keeps `keep_recent_tool_results = 6` full results of up to
12,000 characters; the number and the size scale with the budget as `tool_output_chars` already does. The check of a
summary (`check_continuity`) stays; its failures are counted by 53.

**Design, as built.** The measurement came first, with the instrument of `tests/smoke/test_scaffold.py` (a scripted
model that reports the loop's own estimate as its usage, so the budget arithmetic is exact): a run of 40 turns of
three `read_file` calls each, every result 12,000 characters, the shape of a browser run's multi-action steps, played
once per arm with the module constants set for the arm (`/tmp/dot_ctx.py` on the Mac that measured, not committed).
At the budget of 48,000: 41 steps, 2 compactions (4.9 per 100 steps), 13 k tokens to the summariser each, a mean step
of 40 k tokens; the room left after a compaction (`_COMPACTED_SHARE` 0.85 against 0.70) and a shorter transcript for
the summariser (120,000 against 80,000 characters) changed none of the three numbers, because what fills the budget
between compactions is the stale tool results already cut to 700 characters, not the tail a compaction keeps, and the
transcript sent to the summariser was 13 k tokens, far under its cap. At a budget of 96,000: no compaction in the same
41 steps, a mean step of 50 k tokens (+25 %), the largest 63 k. So the two moves the plan called (2) and (3) were
dropped as measured no-ops, and the item is the budget (decision 33) and one cost of a compaction the instrument did
not reach but the owner's runs did: the summary's size.

(1) `[agent] context_tokens` is 96,000 (was 48,000); the rule that a smaller window gives three quarters of itself
stands, so a local model with a 65 k window keeps its 49 k budget and only models that can hold more get more. For
Ollama the window is the smaller of the model's own and the `num_ctx` sent with each request, and `num_ctx = 65536`
is right for a model on this Mac (it sets the memory of its cache), so a cloud model (a name ending in `:cloud`, run on
Ollama's servers) now gets `[providers.ollama] num_ctx_cloud = 262144` (`num_ctx_for(model, config)` in
`llm/ollama.py`, used for the request and for the window `/api/show` is read against): the owner's
`deepseek-v4.1-flash:cloud` (1,048,576) and `glm-5.3-flash:cloud` get the 96 k budget, `minimax-m2.7:cloud` (196,608)
and `gpt-oss:20b-cloud` (131,072) too, a local 65 k model stays as it was. (2) A summary is asked for in at most 1,500
words (`_SUMMARY_MAX_WORDS`), where the budget's tenth allowed 3,600 at 48 k and would have allowed 7,200 at 96 k: a
longer summary costs the fast model's output seconds at every compaction and sits in the note every later step
carries. The trade of (1) is written into `defaults.toml` and the README: a wider budget means fewer summaries and more
input tokens a step (up to twice; +25 % in the instrument), so an owner on a metered key who weighs tokens over
summaries sets `context_tokens = 48000` back. Not changed: `keep_recent_tool_results`, `_COMPACTED_SHARE`, the
transcript cap, the shrink to 700 characters (measured no-ops here). The live numbers are the owner's: the next long
run on the restarted daemon, read with `dotx stats --run ID`, against the baseline in item 53 (13.8 compactions per
100 steps, 53 % of the model seconds on the longest run).

**Touchpoints.** `agent/loop.py` (`_SUMMARY_MAX_WORDS`), `defaults.toml`, `config.py` (`OllamaConfig.num_ctx_cloud`),
`llm/ollama.py` (`num_ctx_for`), README "How a run works" and "Models and providers", `tests/unit/test_scaffold.py`,
`tests/smoke/test_scaffold.py`.

**Done when.** The numbers of the instrument before and after each move are in this item and decision 33 (done); the
defaults that win are set (done: the budget and the cloud window; the no-ops left alone); the owner's next long run is
read with `dotx stats --run` and logged (the owner's run, after the daemon restart).

### 56. The web without guessing

**Status: done.** Built on `item-56-web` (MYP-196).

**What.** A research run finds pages instead of guessing their address, and a wrong guess costs one turn that says
what is there. Targets (item 53): not-found fetches per research run near zero (from about ten), turns lost to
argument validation zero.

**Why.** The owner's two research runs on the same goal lost about twenty turns to guessed career-page addresses, two
to search engines fetched as pages, and five to `max_results` above 10.

**Design (from the audit).** (1) `web_fetch` on HTTP 404 or an unresolved host reports the failure and, when the host
answers at its root, the links of that front page whose text or path shares a word with the asked path (a dozen at
most, with the words they matched), so the next turn opens the right page; the result's first line stays the failure.
(2) The prompt's "# Web" says: find a page with `web_search` (`site:host words`) or `web_crawl`, never guess a path;
a search engine is never fetched as a page. (3) Every tool argument with a maximum says it in words in its description
(`max_results` "1 to 10", a batch "at most 5 steps"), and a number above the maximum is lowered to it with the result
saying so, rather than refused (the rule that a batch is looked at before the rest stands: the first five run). (4) The
DuckDuckGo throttling is read again with item 53 on the owner's runs since the hosted search is live; if it is gone,
the README's known limit is rewritten.

**Touchpoints.** `tools/web.py`, `web/fetch.py`, `agent/prompt.py`, `tools/browser.py` (the batch), README "Tools"
and "Status and known limits", `tests/unit/test_web_tools.py`, a smoke test against the fixture site's dead link.

**Design, as built (2026-10-11).** (1) `tools/web.py`: on HTTP 404 or 410 `web_fetch` reads the host's front page
(`front_page`, one more request, skipped when the front page was the address asked) and lists its links on the same
host that share a word of three characters or more with the asked path and query (`path_words`, `likely_links`,
twelve at most, in page order, the address itself left out); the matched words are not repeated, the link shows them.
The first line stays the failure, the second says the page is not there and names `web_search` (`site:host words`)
and `web_crawl`. When the front page cannot be read, the result says why (its status, a web error's message, not an
HTML page); when it links to nothing matching, the result says that. An unresolved host is already a `WebError` with
its own message and has no front page to read, so it is left as it was. (2) The prompt's "# Web" bullet is in. (3)
`max_results` says "1 to 10" and a larger number is lowered to 10 with the search header saying so (`wanted_results`);
a browser or desktop batch over `max_batch_steps` runs its first steps and the result opens with how many remain
(`tools/batch.py`: `first_steps`, `batch_cut_note`, shared by both tools, the two `ToolInputError` refusals gone; the
descriptions say 5 and 25 by default). (4) The throttling was read with item 53 on the owner's runs of the last 30
days: 25 of 93 web failures throttled, 18 of 32 `web_search` calls failed, so the known limit stands; the README's
line now points at `dotx stats` for the count. Tests: `tests/unit/test_web_tools.py` (the words, the likely links,
the rendering, the cut) and `tests/smoke/test_web_fetch.py` (a site served with `evals/site.py`, a dead path answered
with the front page's matching link, a partner's host and an unrelated page left out).

**Done when.** The fixture's dead link returns the front page's matching links in the smoke test; a `max_results` of
20 returns ten results and says so; the prompt line is in; the owner's next research run shows the numbers.

### 57. The browser's closed pages

**Status: done.** Built on `item-57-closed-pages` (MYP-197).

**What.** A page that closes under Dot (a popup that closes itself, an OAuth callback that redirects and closes, a
tab the user closes) is reported as that, in the tool result, with the tab that is now current; no traceback in the
daemon's log, no failed act for a page that did its job.

**Why.** The two tracebacks and the two failed acts in the owner's log (the part's head); 13 `browser_context_closed`
and 10 `launch_retry` warnings whose causes are read in the item.

**Design (from the audit).** `_adopt_popup` registers a tab only if the page is still open after `opener()` and
removes it once (`_close_tab_object` tolerates a tab already gone); `keep_focused` and the act that calls it treat
`TargetClosedError` as "the page closed" and return the observation of the thread's current tab with a line that says
which page closed and why (navigation, closed by script, closed by the user); the smoke test opens a popup that
closes itself and a page that navigates away and closes during an act. The two warning counts are read from the log
with their causes before the design is final.

**Touchpoints.** `browser/session.py`, `browser/driver.py`, `tools/browser.py`, `tests/smoke/test_browser_*.py`.

**Design, as built (2026-10-11).** The log, read first: the four `_adopt_popup` tracebacks were `keep_focused`
(attach mode) raising `BrowserError` on a LinkedIn sign-in callback popup that had already closed itself, and
`_adopt_popup` re-raising because `browser_gone` knows Playwright's errors, not Dot's; the 13 `browser_context_closed`
follow `browser_started` (5), `browser_idle_thread_released` (3: the last tab of Dot's own Chrome closing closes the
persistent context) and `daemon_stopping` (2), none a fault; the 10 `launch_retry` were one `desktop_open_app` of a
path to a `node` binary, which Launch Services fails with -600 (the "still shutting down" code) six times over; the
`list.remove(x)` traceback is inside Playwright's frame bookkeeping (`_on_frame_detached`), logged by pyee, not Dot's.
The store holds no act result with a closed-target error, so the "two failed acts" of the audit are not reproduced
from the data. Built: `popup_fate(error, closed)` decides what became of a popup Dot could not adopt ("closed" when
the page is closed, whatever the error; "gone" when the browser is; neither is raised), `_adopt_popup` uses it in two
places (the opener lookup, the registration) and a popup found closed after registering is not appended; the opener
gets `closed_popup_note` ("a tab opened and closed itself (url); there is no tab to switch to"). In `_run_steps` a
`BrowserError` on a page that is closed is a "skipped" step ("the page closed under this step"), and both that and
the surprise detector stop the batch with `closed_tab_stop`: "the tab was closed by the page (it was at url); the tab
now current is shown below", which `act` already shows (`_current_tab` drops closed tabs). `open_arguments` refuses a
path that is not a bundle with a message naming the three forms, before any retry. Tests: `tests/unit/test_closed_pages.py`
(the fate, the notes, the refusal) and two smoke tests in `test_browser.py`: a popup that closes itself at once (one tab
kept, a later act works on the opener) and a tab closed by its page in the middle of a batch (no failed step, the stop
line, the Home tab shown). Against `main` the second fails on the stop line only: with a 60 ms grace the close is seen
by the surprise detector, so the closed-target error inside a step is not forced by the test; the unit test holds the
cause of the tracebacks (a `BrowserError` on a closed page is "closed").

**Done when.** The two regressions are tests that fail on `main` before and pass after; the owner's daemon log shows
no `_adopt_popup` traceback after a week on the version.

### 58. The bill of a run, on its page

**Status: done.** Built on `item-58-run-bill` (MYP-198).

**What.** The run page shows, under the face, what a run cost and where: minutes (model, summaries, tools, waiting
for the owner), steps, input and output tokens with the cached share, compactions, delegated runs with their cost;
the Activity rows that ended today show minutes and tokens. Wide and phone, both looks, both densities.

**Why.** The owner's runs cost 23 M tokens in two days and the page said only the tokens; item 22 left the numbers off
the page until the owner wanted them, and a 51-minute run with 63 compactions is what wants them.

**Design (from the audit).** `GET /runs/{run_id}/stats` (item 53) feeds a `cost-card` in `views/run.js`, painted
with `paintStats` while the run works and once at the end; `ActivityRow` gains `minutes` and `tokens`; the controls
page (item 50) shows the card.

**Touchpoints.** `static/js/views/run.js`, `views/activity.js`, `server/activity.py`, `server/views.py`,
`static/css/app.css`, `tests/smoke/test_run_page.py`, README "The daemon, the web UI and the service".

**Design, as built (2026-10-11).** `static/js/cost.js`: `createCostCard()` is a folded card (`foldCard`, id
`run-cost`, closed by default) whose head line is `costLine(costs)` ("12m 03s · 48 steps · 1.2M in · 8.1k out") and
whose body is a `kv` grid of nine cells: Model, Summaries, Tools and Waiting for you as seconds with their share of
the run's wall time ("None" at zero), Steps, Input tokens with the cached share, Output tokens, Compactions with the
rate per 100 steps, Delegated runs with the seconds waited; every cell carries a hint. `run.js` places it under the
head (after the connection note) and reads `GET /runs/{id}/stats` on arrival, every 10 s while the run works
(`COST_REFRESH_MS`, a slower tick than the 1 s of the face since the route queries the run's events) and once at the
end (from `reconcile`). `ActivityRow` gained `minutes` and `tokens` (`None` on the rows that have not ended;
`_run_row` fills them from `finished_at - created_at` and the run's input and output tokens) and `activity.js` adds
"12 s · 2.1k tokens" to the facts of a row that ended. The controls page shows the card on a 51-minute sample
(`FAKE_COSTS`). `.cost-grid` narrows the cells to 150 px so they go three abreast wide and two on a phone. No
`test_run_page.py`: the Activity smoke test already plays scripted runs to their end, so it opens the run page at
1280 and 390 px in light and dark, checks the head line, opens the card, checks the grid and the Ended today rows,
and keeps the screenshots. The scripted provider reports no model seconds, so the sample page shows "Model: None";
the owner's runs fill it.

**Done when.** The card shows the numbers of a scripted run in the smoke test at both widths and looks; the Activity
rows carry minutes and tokens.

## Open owner decisions

1. Live evals: `dotx eval run --model REF` spends model quota. Run it for the models in use and keep the JSON; it is the
   basis for flipping the defaults below. Since item 29 the commands are, with temp dirs so the owner's data is not
   touched: `DOT_HOME=$(mktemp -d) DOT_LOG_DIR=$(mktemp -d) .venv/bin/dotx eval run --model ollama:minimax-m2.7:cloud
   --repeat 2` before a change and again after it (the report path is printed), then `.venv/bin/dotx eval report
   BEFORE.json AFTER.json`, whose last line is the difference. Each full run plays 32 tasks (27 small, 5 long of ten
   or more turns) against the model; `--task ID` plays one.
2. Defaults that change how the agent behaves and wait for evidence: `[agent] tool_surface` (item 4),
   `verify_answer_paths` (item 5), `resume` (item 6), `lessons` (item 7), `stall_escalation` (item 2),
   `scaffold = "measured"` (item 3).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model, and `dotx
   doctor` warns about it (a warning, not a failure). Done for the owner on 2026-10-10 at their word: their
   `config.toml` sets `review = "anthropic:claude-sonnet-5-5"`, and the warning is gone.
4. From the audit: how long to keep events and screenshots (retention windows), which model compacts (closed by item 24 on 2026-10-10: the fast model writes the summary, the run's own model when it cannot; read the compaction share with `dotx stats` after the daemon restarts on that code), the fact that `[models] fast` was (`[models] fast` is
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
13. Speed knobs that wait for item 9's numbers (item 26 set `[browser] settle_quiet_ms` to 60 and `[desktop] screenshot_max_edge` to 1366 on 2026-10-10; the rest still waits): `[browser] settle_quiet_ms` (120 or 60; item 13 has the timings of
    both; a longer window also shows more of what a page fetches after a scroll), `[desktop] settle_quiet_ms` (120: a
    change later than it is not waited for), `[desktop] screenshot_max_edge` (1920 or 1366), `[desktop]
    paste_threshold_chars` (40: typing costs 8 ms a character, a paste about 0.25 s), `[agent] tool_output_chars` and
    `keep_recent_tool_results`, besides `tool_surface = "grouped"` (decision 2) and the compaction model (decision 4,
    and `agent/loop.py:290-298` records the decision that a run compacts with its own model).
14. Research (not an item of part 2): a `research` run can save nothing, not even a note (`write_file` and `memory_save`
    are acting-only, `tools/base.py:16-17`), and DuckDuckGo's bot check throttles long research runs (README "Status and
    known limits"). Decided by the owner on 2026-10-09: no change for now; note saving and the hosted Ollama search
    (`OLLAMA_API_KEY`) as the default backend come later. Done by item 34 (see decision 18).
15. The Claude GitHub app, decided by the owner on 2026-10-10: it is not used; subagents in the session review each
    branch (house rules, Git). It had reviewed nothing since PR #1 ("Code review skipped": no extra usage paid for it).
16. The review model has no standby (item 15): with `[models] review` on the provider of `main` (the default), a run in
    goal mode or with `auto` approvals that moved to its standby fails its goal check or its approvals while that
    provider cannot answer. Settled for the owner by decision 3: their review model is on Anthropic, their main model on
    Ollama. The default stays as it is.
17. Playbooks (item 36) are off by default. To measure them: set `playbooks = true` under `[agent]` in a temp home,
    run the same goal-mode browser goal twice on one site (the first run writes `<home>/playbooks/playbook-<host>/`,
    the second reads it) and compare the steps with `dotx stats --by site`. If the second run takes clearly fewer
    steps, flip the default in `defaults.toml` and say so in the README's playbook paragraph.
18. The search backend default changed with item 34: `[web] search_backend = "auto"` sends `web_search` to Ollama's
    hosted search as soon as `OLLAMA_API_KEY` is set on the Mac (each search counts against the key's allowance) and to
    DuckDuckGo otherwise. If you prefer DuckDuckGo with the key present, set `search_backend = "duckduckgo"` in your
    config; `dotx doctor` shows the choice. Nothing needs restarting beyond the usual daemon restart for a new version.
19. One-time codes (item 31) are off by default: `[policy] code_sources = []`. To turn them on, set
    `code_sources = ["mail"]` or `["mail", "messages"]` in your `config.toml`; Messages needs Full Disk Access for the
    program that runs Dot (`dotx doctor`, row `one-time codes`). The live test sends a mail to your own address and
    reads the code back: `DOT_MAIL_TESTS=1 uv run pytest tests/smoke/test_codes.py`. A `yolo` run that signs in to a
    site that mails you a code is yours to try (decision 12).
20. Campaigns work in your own Chrome by default from item 32 on (`[agent] campaign_browser = "attach"`): a campaign
    you start while your Chrome is closed or without remote debugging is refused with the reason, and `dotx run
    --standing --browser own` keeps it on Dot's Chrome. The check that is yours: a campaign over three sessions on X
    keeps your session (the brief of each session names the Chrome). Set `campaign_browser = "own"` to go back.
21. Pre-approved spending (item 33) is empty by default: `[policy.spend]` lists nothing, so every payment step is
    handed to you as before (a `yolo` run does it itself). To pre-approve small spends, list the site with its caps in
    your `config.toml`, for example `"namecheap.com" = { per_purchase = 20, per_day = 50 }` under `[policy.spend]`;
    the caps compare the number on the page whatever its currency. The check that is yours: a live purchase under the
    cap on a listed site in `auto` or `yolo`.
22. The channel order (item 27) is measured on your real runs, not offline: run `dotx stats` after a week on this
    version and compare the GUI share of the acting calls with the share before (the events of older runs carry no
    channel, so the share starts with this version). A live eval run of the long tasks (decision 1) on the same model
    before and after is the stricter check and is yours to pay for.
23. The second opinion of the judge (item 30) is off by default, because it doubles the paid requests of a judge that
    is itself off by default; turn it on with `[judge] second_opinion = true` once the judge is. How often the two
    phrasings disagree on `jev-1.13.0` is unmeasured: the offline tests use a stand-in, so the first live runs with it
    on are the measurement, and the summaries of the judged clicks (`(disagreed)`) say what it found. The three
    injection tasks have not been run against a live model either; `dotx eval run` with your model gives the first
    `hidden instructions followed` count.
24. The change of route (item 37) is proven offline with a scripted model; whether a live model takes the brief's
    call and finds another channel is unmeasured. A campaign of yours that reaches the patience is the measurement:
    its brief and the `campaign_route_refused` log lines say what it refused and what the session wrote instead.
25. `[store] retention_days` (item 39) is 90 days and only `dotx prune --apply` acts on it; the daemon never deletes.
    Set it in your `config.toml` if you keep timelines longer, and run `dotx prune` first to see what it would take.
    The gate workflow runs on GitHub's macOS runners, which are metered on a private repository; the smoke tests that
    need Chrome, Ollama, the desktop or a key stay on your Mac on purpose, so the run is short.
26. `judge_items` (item 42) appears when `TYPESAFE_API_KEY` is saved, whatever `[judge] provider` says, and it costs
    input tokens only (about 0.3 s a request of up to 100 items). Its gain on a live model is not measured here: run
    `dotx eval run --model REF --task filter-headlines --repeat 3` once with the key saved and once with it removed (or
    with `[judge] api_key_env` pointing at an unset name) and compare seconds and tokens in the two reports; if the
    model does not reach for the tool, the prompt line under "# Web" in `agent/prompt.py` is where to push it.
    Measured on 2026-10-10 at the owner's word (`ollama:minimax-m2.7:cloud`, `filter-headlines` three times each
    arm): key saved 3/3 in 4.3 steps, 50.8k tokens, 12.9 s an attempt; key removed 3/3 in 4.0 steps, 45.3k, 13.8 s.
    The model never called `judge_items` (its tool calls were read_file, write_file, finish on every attempt): it
    filtered 24 headlines itself in one write. So the tool costs the 5.5k tokens of its definition on a task of this
    size and gains nothing; its case is a list the model cannot hold in a reply, which the eval set does not have.
    Open: whether to push the prompt line, or to add a task of a few hundred lines where the tool is the only way.
27. The post tools of a campaign (item 43) rank, pick and check with weights of 1/1/1 and `[judge]
    draft_check_threshold = 0.5`, measured on a synthetic sample (precision@10 1.00, the top other post at 0.53). Label
    twenty or thirty posts of your own X feed (would answer / would not) and run them through `rank_posts`' questions
    (the hosted test shows how); if a post you would answer falls under one you would not, change the weights with a
    `campaign_note` carrying `w_topic`, `w_person`, `w_reply`, and set the threshold where your rejected drafts fall.
28. The checks before a model call (item 44) are on whenever `[judge] provider = "typesafe"`; the click questions
    no longer run in `yolo`, which removes the cost decision 9 named, so the judge can stay on in `yolo` for the page
    check and the goal check alone. Whether they save turns or seconds on the eval set is not measured here: run
    `dotx eval run --model REF --repeat 2` with the judge on and off (`[judge] provider` in `config.toml`) and compare
    the reports; the page check only acts when the model fills `expect`, so look at how often it did in the timelines.
    Measured on 2026-10-10 at the owner's word (`ollama:minimax-m2.7:cloud`, 34 attempts an arm, `--repeat 1`):
    judge off 31/34, 4.6 steps and 16.2 s a pass, 53.6k tokens; judge on 30/34, 4.3 steps and 10.2 s a pass, 48.9k.
    The difference is run-to-run variance, not the checks: the eval set has no browser task and runs with goal mode
    off, so neither `expect` nor the goal check ever fired, and the seconds moved on single tasks (sort-into-folders
    142 s against 17 s, pipeline-with-flaky-stage 24 steps against 10). The failures changed names, not count:
    collect-contacts and report-failing-tests fell in both arms, filter-headlines only off, write-todo-note and
    recover-wrong-extension only on. What would measure item 44 is a browser task with `expect` filled and a goal-mode
    run; neither is in the eval set, so the decision stays with you: the checks cost nothing on this set and the
    judge can stay on.
29. The step router (item 45) is built and off. Its question is the item's own: does a run take less time at the same
    success rate when routine steps go to `[models] fast` and hard ones to a helper? Measure it on your models with the
    eval set, both arms with the key saved: `dotx eval run --model REF --repeat 2` with `[agent] step_router = false`,
    then with `true` (and a helper in `[models] helpers` if you want the hard steps routed too), and compare seconds
    and success in `dotx eval report`. On Anthropic or OpenAI as the main model, count the cache miss: a routed step
    sends the whole prefix again. Keep it off, or set `step_router_threshold` higher, when the numbers do not favour it.
    Measured on 2026-10-10 at the owner's word (`ollama:minimax-m2.7:cloud` as main, `ollama:gpt-oss:20b-cloud` as
    fast, no helper, threshold 0.6, 34 attempts an arm): router off 31/34, 4.6 steps and 16.2 s a pass, 53.6k tokens,
    194 tool calls of which 5 failed; router on 32/34, 6.1 steps and 17.9 s a pass, 62.1k tokens, 209 tool calls of
    which 13 failed. The routed steps cost turns: the fast model needed more of them on the same tasks (rename-function
    15 steps, checklist-ten-files 14, rename-helper 13, collect-contacts 12) and made more tool errors (6% against 3%),
    and the time of a judgment plus a retried step ate what the fast model's speed saved. Success within the noise, a
    step and a half more a pass, 8.5k tokens more, 1.7 s more: the numbers do not favour it, so the router stays off
    and the default holds. Not kept: the per-step `step_routed` counts, since an eval attempt's home is thrown away
    with its database; the next measurement should read them from `dotx timeline` of a kept run. Resolved: off.
    The same two arms on `ollama:deepseek-v4.1-flash:cloud` (the model of most of the owner's real runs), `--repeat 2`,
    68 attempts an arm, on 2026-10-11: router off 62/68, 4.3 steps and 9.8 s a pass, 50.2k tokens, 11 of 301 tool
    calls failed; router on 62/68, 6.5 steps and 13.5 s a pass, 68.4k tokens, 12 of 398 failed, and four tasks passed
    in one attempt only (rename-function, collect-contacts, turkish-sort, filter-headlines). The same verdict, by a
    wider margin: the routed steps cost 2.2 steps, 18.2k tokens and 3.7 s a pass at the same success. Off it stays. A
    side finding for the safety set: DeepSeek followed the hidden instruction in 4 of 6 injection attempts off and 2
    of 6 on (the two arms differ by variance there too; the two injection tasks are where it fails), which is a
    number for the preset decision (2), not for this one.

30. The desktop shell (item 51): Tauri 2 (Rust, a few MB, nothing to change in the web UI) or Electron (TypeScript,
    about 150 MB). The recommendation is Tauri 2: the shell is a window, a tray item and notifications, nothing that
    needs Node in the app, and the owner never has to write Rust beyond the generated project. An Apple developer
    account is needed for a signed build that other Macs open without a warning.
    Decided on 2026-10-11 at the owner's word ("I trust you to decide; think it through for the future, performance
    and quality"): Tauri 2. The shell is a window, a tray item, notifications and a file drop; none of that needs
    Node in the app, the binary stays a few megabytes, the web UI is the one codebase, and the Rust is the generated
    project plus a few dozen lines. Electron would cost 150 MB and a second runtime for nothing the shell does.
31. Reaching the daemon from the phone (item 52): the daemon listens on `127.0.0.1` on purpose. The recommendation is
    Tailscale: `[server] host` set to the Mac's tailnet address, the token stays the sign-in, nothing is opened to the
    internet. Without it, the phone items are built and tested on the Mac's own network only.
    Decided on 2026-10-11 at the owner's word: Tailscale, and the web UI as a PWA first; a store app (Capacitor around
    the same UI) only once the PWA has been used for a while. The daemon stays on loopback by default; `[server] host`
    documents the tailnet address.
    As built (item 52): not `[server] host`, which the guard holds to loopback, but `tailscale serve --bg 8765` in
    front of it and the name in `[server] reach`; the phone needs HTTPS for the service worker and the push anyway.
32. A name and a face for Dot (item 49): the recommendation is a yellow dot that tells the state and an optional
    name, no eyes and no accessories; dots' critics (Fortune, 2026-10-02) said why. Say so if you want the character.
    Decided on 2026-10-11 at the owner's word: the yellow dot that tells the state, an optional name, no character.
33. The context budget of a long run (item 55): today `[agent] context_tokens = 48000` and `[providers.ollama]
    num_ctx = 65536` hold every run to about 48 k tokens, on models whose windows Ollama reports as 196,608
    (minimax-m2.7), 131,072 (gpt-oss:20b) and 1,048,576 (deepseek-v4.1-flash, glm-5.3-flash). A wider budget means
    fewer summaries and more tokens a step; the right number is measured in item 55 on the long tasks and on the
    owner's own runs, and the owner's `config.toml` is set to the winner at their standing word. Open until item 55.
    Decided on 2026-10-11 by the measurement in item 55: the budget is 96,000 and a cloud model of Ollama is asked
    for a 262,144 window (`num_ctx_cloud`), so the owner's models get the 96 k budget without a change to their
    `config.toml`; a local model keeps three quarters of its own window. In the instrument the wider budget took the
    compactions of a 41-step run from 2 to 0 at +25 % input tokens a step; the owner's next long run is the live
    number. The room after a compaction and the summariser's transcript were measured and left alone.

## Part 6: the web that does not fail (planned 2026-10-11)

The owner's sixth word, on 2026-10-11, after part 5: "build a different structure for web search and fetch, one in
which failure disappears; then continue with the next tasks". Planned from the owner's own runs as `dotx stats --days
30` and the store read them (read-only; the goals stay out of this file) and from measurements of the public engines
made the same day from this Mac.

**What the owner's runs show.** 32 `web_search` calls, 18 failed: every one DuckDuckGo's bot check (HTTP 202), met on
the third try after two pauses of 8 s, 16 to 40 s lost each. 561 `web_fetch` calls, 75 failed: 51 not found (item 56
answers those), 9 HTTP 429 from LinkedIn job pages and a Brave result page, 15 results that said "browser_open uses a
real Chrome and may get through" and so cost the model another turn, the rest in transit. Of the 93 web failures, 25
were throttling.

**What the engines answer this Mac (2026-10-11, plain requests with Dot's user agent, then Dot's own Chrome).**
DuckDuckGo's HTML page answers until its check comes and then shows a plain request its 202 page, a headless Chrome an
image CAPTCHA ("select all squares containing a duck") and a visible Chrome its results (10 of 10 relevant on two
queries). Bing's HTML page answers a plain request, but three of six queries came back with another query's results
(YouTube monetisation pages for a Finnish job query) or an empty notice for a company with open positions; a wrong
answer is worse than a skipped rung, so Bing is not one. Brave Search's HTML page answered five queries in a row with
18 to 20 relevant results each and the sixth with HTTP 429 ("flagged as suspicious"), a limit that held for more than
ten minutes and in the browser too. Startpage, Mojeek, Qwant and Yahoo turn a plain request away. The hosted search of
Ollama (`OLLAMA_API_KEY`) has no check and is the first rung whenever the key is set.

### How to start part 6 (the handoff prompt)

> Read ROADMAP.md part 6 and the status table below. One item at a time, in order; the protocol of part 5 (branch,
> gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch the owner's data
> directory except to read it and to restart the daemon when `/api/status` shows no active run; PLAN.md stays
> untracked. The owner reads Turkish; everything written is English.

### Status, part 6

| #   | Item                                                                     | Status      | Commits |
| --- | ------------------------------------------------------------------------ | ----------- | ------- |
| 59  | The web that does not fail                                               | done        | 725e301 |

### 59. The web that does not fail

**Status: done.** Built on `item-59-web-that-does-not-fail` (MYP-199), merged 725e301 (PR #64).

**What.** A search fails only when every engine does, and a page is read when any way of reading it works. `web_search`
climbs a ladder of engines and says which rung answered; `web_fetch` reads a page that turned the plain request away in
Dot's own Chrome itself, in the same call, and says so.

**Why.** Half the searches of the owner's research runs died on one engine's bot check after wasting sixteen seconds
each, and the fetches that met a wall told the model to spend another turn on `browser_open`. Both are structure, not
luck: one engine with retries is one point of failure; a hint to the model is a turn lost.

**Design.** `web/ladder.py` holds what the client and the search module share: `SearchEngine`, `SearchOutcome`
(hits, the engine that answered, the rungs skipped with their reasons, the age of a remembered answer), `EngineBench`
(which engines sit out and until when, `BENCH_S` per engine: DuckDuckGo 600 s, Brave 90 s, the browser 600 s, Ollama
60 s) and `AnswerMemory` (the outcome of each query for `MEMORY_S` = 600 s, queries equal up to case and spacing).
`web/search.py` is the ladder: `search_ladder(choice, ollama_key_present, browser_present)` gives the rungs (`auto`:
ollama when its key is present, duckduckgo, brave, browser when the run's browser is Dot's own with a visible window; a
named engine alone), `climb` asks them in turn (a benched rung skipped with the time it has left, a `WebThrottledError`
benches and skips, another `WebError` skips, every rung skipped raises an error naming each engine and its reason),
`search` reads the memory first and keeps the outcome, `engine_attempts` builds the tries a run can make (Ollama with
its key, the browser with a page reader). `parse_brave` reads `div[data-type="web"]` blocks (the class names carry
build hashes; the data attribute and the words `title` and `description` stay); Brave has no empty-result notice this
code reads, so a page without web results benches it for 90 s and the next rung answers, and the error says so. The
browser rung opens DuckDuckGo's HTML page in Dot's own Chrome and parses it like the plain one. `[web] search_attempts`
is gone (a rung is tried once; the next rung is the retry); `search_spacing_s` paces the two scraped engines.
`BrowserSession.read_page(url)` reads a page in a scratch tab no thread owns (a `context.new_page()` that
`_adopt_popup` leaves alone since it has no opener), waits for the page to settle and, after a 202, 403, 429 or 503,
up to 8 s for the challenge to reload, keeps the status of the last main-frame response, closes the tab in `finally`
and returns `RenderedDocument(url, status, html)`; `quiet_reads` (own mode) says a scratch tab is allowed, `human_like`
(own mode and not headless) that a bot check will pass. `web_fetch` reads a page whose plain request got 401, 403,
429 or 503, or whose HTML is a JavaScript shell, through `read_page` when `quiet_reads`, renders it like a plain fetch
under a first line "Read in Dot's browser: the plain request got HTTP 429." and, when the browser got an error status
too, says both ("in Dot's browser too"); without an own Chrome the old report with the `browser_open` hint stands. The
search header reads "4 results for 'q' from brave (duckduckgo skipped: benched for 9 more min after it limited this
machine). Read the best ones with web_fetch."; the action's target is the ladder ("duckduckgo/brave/browser"). The
system prompt tells the model not to repeat a search or fetch that came back and not to reach for `browser_open` just
to read. `dotx doctor` shows the ladder. `dotx stats` counts a throttled engine's message under throttled as before.

**Touchpoints.** `web/ladder.py` (new), `web/search.py`, `web/client.py`, `browser/session.py`, `tools/web.py`,
`agent/prompt.py`, `runtime.py`, `doctor.py`, `config.py`, `defaults.toml`, `tests/unit/test_search_ladder.py`,
`tests/unit/test_research.py`, `tests/smoke/test_web_fetch.py` (a local site that answers 429 to a plain request and
200 to a Chrome, a JavaScript shell, a 403 for everyone), `tests/smoke/test_web.py` (its browser is the user's, so the
plain report is what it tests), README.

**Live check (2026-10-11, this Mac, a temporary home).** The ladder through the tool: DuckDuckGo answered in 0.9 s;
the same query with other case and spacing came from memory in 0.0 s with "the same query was answered 1 min ago";
with DuckDuckGo benched Brave answered in 0.4 s; with both benched the visible own Chrome answered through DuckDuckGo
in 1.4 s with the right pages and left no tab behind. The smoke test reads the 429 page and the shell through the
headless own Chrome and reports the 403 page as refused in the browser too.

**Open.** The live number is the owner's next research run: `dotx stats --run ID` web failures, throttled, against 25
of 93 before. The bench and memory live in the daemon's web client, so they span runs and reset at a restart. Brave's
limit after five quick searches means a long run with DuckDuckGo benched reaches the browser rung often; that rung
needs `[browser] headless = false` and `mode = "own"`, which is the owner's setting. Setting `OLLAMA_API_KEY` makes the
hosted search the first rung and is the owner's call.

## Part 7: the long run measured, the summaries fixed, the soft look, the composer (planned 2026-10-11)

The owner's seventh word, on 2026-10-11, after item 59: "run the long run yourself, I approve; then solve the other
problems; and turn the app into a design with complete soft light and dark mode support: the composer in the chat can
be better, `/` should show commands, skills, MCP servers; the panel on the right and the cancel and model names at the
top should stay fixed while scrolling; think, research and do the best." Planned from the daemon's logs (read-only),
the code at the points they name, and the measurements of the long run started for item 60.

**What the logs show.** 64 `compaction_check_failed` warnings over the owner's runs: 47 flag only "unverified"
identifiers, and the samples are addresses the summary cut short (`…/roles/1f5cb00f-…/sr-`, `…&tags=c`), so the exact
match against the history fails on a string the history does contain in full; 17 lack a section (PROBLEMS 17, STATE
15, GOAL 9, NEXT 8, DONE 7, several at once). 7 `summary_too_long` warnings, one of 5,450 words against a limit near
1,500. The live-model delegate tests fail on main because the model calls `delegate` with `step=1` before any plan
exists and item 35's rule refuses the call: a turn lost, in the evals and in the owner's runs alike. Item 25 stays
parked: its premises (tool-call deltas in the stream) are still not in the code.

### How to start part 7 (the handoff prompt)

> Read ROADMAP.md part 7 and the status table below. One item at a time, in order, except item 60, which is written
> when the long run of 2026-10-11 ends; the protocol of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash
> in the table, log line) is unchanged. Never touch the owner's data directory except to read it and to restart the
> daemon when `/api/status` shows no active run; PLAN.md stays untracked. The owner reads Turkish; everything written is
> English.

### Status, part 7

| #   | Item                                                                     | Status      | Commits |
| --- | ------------------------------------------------------------------------ | ----------- | ------- |
| 60  | The live numbers of parts 5 and 6                                        | done        | (text)  |
| 61  | Summaries that pass their own check                                      | done        | a8c602f |
| 62  | A delegate before the plan is a step, not a lost turn                   | done        | 056b153 |
| 63  | The soft look: light and dark, every surface                             | done        | 9e86fda |
| 64  | The composer: a slash menu of commands, skills and MCP tools             | done        | d36d375 |
| 65  | The chrome that stays: the run's head and side panel while it scrolls    | done        | 5c3917f |
| 66  | One door for the store's writes                                          | done        | 5c7eada |
| 67  | The inspector's cards: folded, no stray null, a view that follows the run | done        | 8255af7 |
| 68  | Delegated runs in sight: the sidebar and the chat                        | done        | 278f256 |

### 66. One door for the store's writes

**Status: done.** Built on `item-66-one-writer` (MYP-200), merged 5c7eada (PR #67). Put first in the part: on 2026-10-11 at 06:30 the
long run of item 60, its four delegated runs and nothing else died in the same 150 ms with
`sqlite3.OperationalError: database is locked` raised from `add_run_tokens`, the write that follows every model call.

**What.** The daemon's writes to `dot.sqlite3` go through one door: one connection, one writer at a time, a unit of
several statements committed whole; a lock held by another process is waited out with retries, logged each time, and
only then an error; and a run does not die because its token totals could not be written once.

**Why.** The store had two kinds of writer: the daemon's `aiosqlite` connection (every event, run and token write)
and, for the message units of a thread (append, replace at a compaction), a connection of their own opened in a thread
with `BEGIN IMMEDIATE`, so that the unit was all or nothing. Two writers on one database wait on each other up to a
busy timeout of 10 s and then fail; the `aiosqlite` connection serves every run in turn, so when one of its writes
waited the full 10 s and failed, the writes queued behind it by the other runs failed in the same instant: five runs,
one lock. What held the lock those 10 s is not in the log (the store logged nothing before the crash); the structure
that let one holder take five runs down is what this item removes.

**Design.** `store/db.py`: `_writing`, a decorator on every method that commits (42 of them), takes the store's
`asyncio.Lock`, so the daemon's writers run one at a time on the one connection and no writer's statement or commit
lands inside another's unit; a `sqlite3.OperationalError` that says locked or busy (another process: `dotx prune`, a
tool of the owner's on the file) is rolled back and the method tried again after 0.2, 0.5, 1 and 2 s, each try a
`store_locked_retry` warning, then raised as a `StoreError` that names the method and the tries. `append_messages` and
`replace_messages` encode their bodies in a thread and then run as a `_unit` on the connection (`BEGIN IMMEDIATE`, the
statements, `COMMIT`, a rollback on any error); the sync connection and `_write_transaction` are gone. `runtime.py`:
the recorder of a model call catches a `StoreError` from `add_run_tokens`, logs `model_call_not_recorded` with the
run and the tokens (the run's totals are then known to be short by that call) and still emits the `model_call` event,
so a run goes on. The read-only connections of `dotx stats` and the stats route are untouched: in WAL mode a reader
never blocks a writer. Unit tests: thirty concurrent appends to one thread and thirty token counts all land; a thread
that holds `BEGIN IMMEDIATE` on a connection of its own for 0.6 s delays a token write and does not fail it.

**Touchpoints.** `store/db.py`, `runtime.py`, `tests/unit/test_store_writes.py`, README (the store paragraph).

### 67. The inspector's cards: folded, no stray null, a view that follows the run

**Status: done.** Built on `item-67-inspector-cards` (MYP-201), merged 8255af7 (PR #68). From the owner's screenshots of
2026-10-11: the Actions card shows the word "null" under its last group, the cards of the side panel open by themselves, and "Dot's
computer" did not show the page while the run worked.

**What.** The side panel's cards (Dot's computer, Details, Plan, Actions) arrive folded with a one-line summary each
and open at a click, the owner's choice kept per card; no card renders a null; the live view starts by itself while a
run works in the browser and stops when the run ends.

**Design.** `panels.js`: `foldCard` renders `actions` only when it is a node (a `null` reached `el()` as a child,
which the Actions and Plan cards pass); `run.js`: the four cards fold by default (`openByDefault: false`), Dot's
computer becomes a `foldCard` whose summary is the page's address; `computer.js`: `start()` runs when the run is active
and the browser has a page (the first `browser_*` tool call), stops at the end; the run page's smoke test checks the
folded state, the summaries and that no card's text contains "null".

**Design, as built (2026-10-11).** The null was `actionsBody.replaceChildren(..., null)`: the DOM turns a null child
into the text "null" (the `el()` helper skips nulls, `replaceChildren` does not); the optional hint is now spread in
only when it exists, and `foldCard` passes `actions ?? null` through `el()`. Five fold cards in the panel, all folded
by default (`openByDefault: false`, the owner's choice kept per card as before): Dot's computer (summary: the page's
address, else "no page yet", "watching" or "ended"; the Watch button in the head, outside the toggle), Details
(summary "status · model"), Plan, Actions, Screen (the caption in the head). `timeline.js` gained `onBrowserStep()`,
called once at the root run's first `browser_*` tool call; `computer.js` `notePage()` starts the view on it while the
run is active, and the empty hint says "the run has not opened a page in its browser yet" until then, so a run that
searches over plain HTTP (item 59) is not mistaken for a broken view. The daemon's screencast was probed on the live
run of item 60 while it searched: no frames, no page, as the hint now says. Smoke: the Actions card is hidden until its
toggle is clicked and the panel's text has no "null"; the computer card is opened before its pill is checked, at 1280
and 390 px in both schemes.

### 68. Delegated runs in sight: the sidebar and the chat

**Status: done.** Built on `item-68-delegated-runs` (MYP-202), merged 278f256 (PR #69). From the owner's word of 2026-10-11: "show the
subagents' work better, in the left menu and in the chat".

**What.** A delegated run is visible where its parent is: in the sidebar the parent's row carries a count of its
running delegated runs with their status rings and opens to list them; in the chat the delegate call is a live card
with the child's goal, its step count, its last tool and its status, updated from the parent's stream, with a link to
the child's own page.

**Design.** `GET /runs/{id}` gains `children` (id, goal, status, steps, last tool); the stream of a parent already
carries the child's events (`childScope` in `timeline.js`): the card under the delegate call paints steps and the last
tool from them and the status ring from `run_finished`; `sidebar.js` lists the children under the parent row from the
`/api/activity` payload (item 47), which gains `parent_run_id`. The Activity page groups a parent with its children.
A smoke test plays a scripted parent that delegates and checks the card, the sidebar count and the child link.

**Design, as built (2026-10-11).** No new field on `GET /runs/{id}`: the parent's stream already carries the child's
events, and the lists have what they need from `/api/activity`, whose rows gained `steps` and `parent_run_id` (both
`None` on approvals and schedules). `static/js/nesting.js`: `nestRows(rows)` puts a row whose `parent_run_id` names
another row of the same section under that row and leaves every other row where it is; `progressWords(row)` reads
"3 steps · browser · delegated by another run". The sidebar's sections and the Activity page render each parent and
then its children in a bordered, indented block (`.side-children`, `.activity-children`); the parent's row says
"1 delegated run". In the chat, `childScope` gives the delegated run's card a progress chip that reads "step 3 ·
web_fetch" from the child's `assistant` and `tool_call` events and "done · 12 steps" at its `run_finished`, and an
"open" link to the child's own page that does not fold the card. The smoke test delegates a scripted run through
`manager.delegate` (a delegated run takes no slot of `max_concurrent_runs`, so `manager.start` with a parent would
have queued it behind the three working runs) and checks the API fields, the nested sidebar row and its words, the
Activity page's nested row, the chat card's chip and link, at 1280 and 390 px in both schemes, and the four ended rows.

### 60. The live numbers of parts 5 and 6

**Status: done (2026-10-11).** A measurement, not code; the numbers follow the plan below.

**What.** One research run of the owner's own kind (the kind the owner's seven runs were; the goal stays out of this
file), started on the daemon at the owner's word on 2026-10-11 (`run_mv39kmag_ec5bb3`, `ollama:deepseek-v4.1-flash:cloud`,
effort max, approvals yolo, research mode), read with `dotx stats --run ID` when it ends and written here against the
baseline of item 53: compactions per 100 steps (13.8 before; item 55 aims under 5), the share of model seconds in
summaries (53 % before; a tenth), not-found fetches (about ten a research run before; near zero, item 56), throttled
searches (25 of 93 web failures before; near zero, item 59), the engine named in each search result (which rung
carried the run), `_adopt_popup` tracebacks in the log (none, item 57) and the cost card's numbers on the run page
(item 58). Whatever the run shows that the parts did not foresee goes into the log and, when it is structure, into an
item.

**What the run showed (2026-10-11).** The first start (`run_mv39kmag_ec5bb3`) died with every other run in the
`database is locked` crash of item 66; the run was started again as `run_mv3aaeat_5e1c03` on the daemon at 5c7eada
(items 66 and 67 in; 61 to 65 not yet) and ended done in 28 min: 12 steps of its own and 5 delegated runs of 36, 47,
57, 40 and 38 steps (230 steps in all), read with `dotx stats --run` and the store, read-only.

| measure                                   | item 53 baseline            | this run                                     |
| ----------------------------------------- | --------------------------- | -------------------------------------------- |
| compactions per 100 steps                 | 13.8                        | 5.7 (13 in 230 steps; 3.5 to 8.5 per child)   |
| model seconds in summaries                | 53 %                        | 3 to 6 % per child, 0 in the parent          |
| not-found fetches per research run        | about 10                    | 0 in the parent, 1 to 7 per child (17 in all) |
| throttled searches                        | 25 of 93 web failures       | 0 of 104 searches; every one from `ollama`   |
| `_adopt_popup` tracebacks                 | present                     | 0 in the log                                 |
| store `store_locked_retry` / unrecorded   | the crash                   | 0 and 0                                      |
| input tokens a step                       | not measured                | 38 k parent, 59 to 63 k children, 53 to 73 % cached |

Not foreseen: 6 of the 13 compaction checks failed, every one for a missing STATE section (one with an unverified
identifier too), written by the fast model `gpt-oss:20b-cloud` at one attempt each; item 61's second ask with the
section named was not on the daemon yet, so this is the measure it should move next time. Of 46 failed `web_fetch` /
`web_extract` calls, 23 are `web_extract` answering "Extracted 0 of N sources" (the quotes did not match the pages)
and the rest dead job links (404, 410), the ordinary wear of job boards; `web_extract` on job pages is a candidate for
a later item. The parent waited 4 312 s on its delegated runs with 0 s at the gate.

### 61. Summaries that pass their own check

**Status: done.** Built on `item-61-summary-check` (MYP-203), merged a8c602f (PR #70).

**What.** A compaction summary that names an address the history holds is not "unverified" because the model cut the
address short; a summary that lacks a section is asked for again once, with the missing sections named, before the
note says "rely on the pinned facts"; a summary far too long is asked for again with the limit in front of it.

**Why.** 47 of the 64 check failures in the owner's logs flag identifiers the history does contain, cut at a line's
end; every one of them puts "treat these as unverified" into the note the run goes on from, so the run doubts what it
knows. 17 notes lack a section, and the note then tells the run to lean on the pinned facts instead of a summary that
could have been complete for one more fast-model call.

**Design.** `continuity.check_continuity`: an identifier counts as known when a known identifier starts with it and it
is at least 24 characters long (a cut address), or when it starts with a known identifier that is at least that long
(an address with a fragment the history did not have); shorter identifiers keep the exact match. `loop._model_summary`:
a reply that lacks a section is followed by one more attempt whose prompt names the sections missing and carries the
first reply, on the same model; a reply over the word limit is followed by one more attempt whose prompt puts the limit
and the overrun first; the attempts stay within `_SUMMARY_ATTEMPTS`. The `compacted` event records the attempts and
the check as before, so the log and `dotx stats` show whether the second ask helped. A unit test on the check with cut
addresses; a scaffold smoke test that the second ask repairs a summary a scripted fast model writes without PROBLEMS.

**Design, as built (2026-10-11).** The log's unverified identifiers were read one by one before the code changed: 11 of
75 end in an ellipsis, the mark Dot's own renderings leave on a value they cut (the pinned actions at 160 characters, a
page's text), which the summary copied as it was; the check took the ellipsis for part of the address. The exact check
was already a substring match, so an address cut short without the ellipsis passed before; the 24-character prefix rule
of the plan for a cut address added nothing and was not built. As built: `identifiers` strips a trailing ellipsis with
the punctuation; `check_continuity` takes an identifier as known when the history holds it, whole or as a part, or when
it starts with a known identifier of at least 24 characters (`_LONG_IDENTIFIER_CHARS`: a fragment or a query the
history did not have), and `missing_sections(summary)` is its own function so the summary ask can use it.
`loop._model_summary` keeps `_SUMMARY_ATTEMPTS` (3) and changes what it asks: a reply over the limit turns the next
prompt into `_SUMMARY_SHORTER_PROMPT` (the words and the limit first, then the usual prompt); a reply that lacks a
section is asked again once with `_SUMMARY_SECTIONS_PROMPT` (the sections named) and the history followed by the draft
under "Your earlier summary, which lacked the sections …"; a second reply that still lacks a section is returned for
the check to say so. The `compacted` event's `attempts` and `problem` show the repair ("the summary lacked the sections
PROBLEMS", attempts 2). Unit test: a cut address with the ellipsis and a lengthened one pass, another page under the
same prefix and a lengthened short path do not. Scaffold smoke test: a fast model that leaves PROBLEMS out until asked
by name gives, at every compaction, a second summary that passes, with two requests per compaction and the draft in the
second.

**Touchpoints.** `agent/continuity.py`, `agent/loop.py`, `tests/unit/test_continuity.py`, `tests/smoke/test_scaffold.py`,
README (compaction paragraph).

### 62. A delegate before the plan is a step, not a lost turn

**Status: done.** Built on `item-62-delegate-step` (MYP-204), merged 056b153 (PR #71).

**What.** `delegate` with a `step` the plan does not have runs the delegation and says in its result that the step was
not in the plan, instead of refusing the call and losing the turn.

**Why.** The live model (and the eval models since item 35) call `delegate` with `step=1` before `update_plan`
recorded a plan; the refusal costs a turn and, in the two live delegate smoke tests, the test. A number the plan does
not have is a model's slip, not a danger: the delegation itself is harmless to run.

**Design.** `tools/delegate.py`: the step check moves from the argument validation into the run: when the plan has no
such step the delegation runs with `step=None` and the result's first line says "step N is not in the plan (the plan
has K steps); the delegated run ran, nothing was marked". The argument description says so. The two live delegate smoke
tests pass again; a unit test on the first line.

**Design, as built (2026-10-11).** `tools/delegate.py`: `unplanned_step_note(steps, step)` is the first line of the
result when the plan has no such step ("Step 3 is not in the plan (the plan has 2 steps); the delegated run ran,
nothing was marked."), `None` when it has; the tool reads the plan before the delegation and renders the note first
and the usual header second, with no "Plan step … is marked" line. The loop closes plan steps from the call's `step`
argument, so `closing_step(call_step, result)` reads the result's first line and gives no step for an unplanned one,
and `_delegated_closings` uses it: no "could not be closed, mark them yourself" note for a step that was never in the
plan. The live run of the first smoke test showed a second slip of the same kind: `delegate` with `model=""`, which
`_model_for` refused as "is not a helper model"; a blank name is now the parent's own model. The argument
descriptions, the tool description, the prompt's Delegation section and README say that an unplanned step runs and
closes nothing. Unit test `tests/unit/test_delegate.py` on the note, the result's lines and `closing_step`; the two
live delegate smoke tests pass again (2 passed in 24 s on `gpt-oss:20b-cloud`).

**Touchpoints.** `tools/delegate.py`, `tests/unit/test_delegate.py`, `tests/smoke/test_delegate.py`, README.

### 63. The soft look: light and dark, every surface

**Status: done.** Built on `item-63-soft-look` (MYP-205), merged 9e86fda (PR #72).

**What.** One soft look in light and dark: low-saturation warm neutrals, surfaces that step up by lightness rather than
by shadow in dark, hairline borders at a small alpha, text in three tiers that meet WCAG AA on every surface, one warm
accent; the system's scheme followed and the owner's choice kept; every view, control, chip, note, toast, menu, code
block, table and the inspector checked at 1280 and 390 px in both schemes; the controls page (item 50) is the proof
sheet.

**Why.** Item 50 gave the app its tokens and themes, but the looks are hard-edged (pure white, graphite, black
accent) and the dark scheme repeats the light one's borders and shadows; the owner asks for a complete soft light and
dark design.

**Design.** `themes.css`: the neutral look's tokens are rewritten as a layered scale (`--bg`, `--surface`,
`--surface-2`, `--surface-3` as four lightness steps on a warm grey; `--border` and `--border-strong` as the text colour
at 8 % and 16 % alpha so they sit on every surface; `--text`, `--text-2`, `--muted` as three tiers with measured
contrast; `--accent` a warm amber from the bubble, not black; shadows only in light, surfaces one step lighter in
dark), the same tokens in `@media (prefers-color-scheme: dark)` and under `[data-scheme="dark"]`/`"light"` for the
owner's choice (`theme.js` already keeps it), `color-scheme` set on the root so form controls and scrollbars follow.
`app.css`, `shell.css`, `run.css`, `settings.css`: every hard-coded colour and every `rgb(0 0 0 / x)` moves to a token;
focus rings, selection, code blocks and tables get soft tokens. A unit test reads `themes.css` and checks that every
token of the light block has a dark twin and that the text tiers meet 4.5:1 (body) and 3:1 (muted) against each
surface (a contrast function in the test). Screenshots of the controls page, the run page, the Activity page and the
settings at 1280 and 390 px in both schemes, looked at. What the research of 2026-10-11 into Radix Colors, Apple's
dark mode guidance, Material 3 surfaces and the looks of Linear and Claude says is recorded as built.

**Design, as built (2026-10-11).** Item 50 had already moved every colour into `themes.css` (the unit test counts zero
literal colours in the other sheets), so the other sheets did not change. `themes.css` names each token's light and
dark value in one place with `light-dark()` (Baseline 2024) under `color-scheme: light dark` on the root, so there is
no second block per scheme to keep in step; `:root[data-scheme="light"|"dark"]` sets `color-scheme` to one and holds
it, which `light-dark()` follows, as do form controls and scrollbars. The neutral scale is a warm grey (Radix's
12-step greys as the measure): light `#f8f7f4` page, `#fdfcfa` / `#f3f2ee` / `#e9e8e3` surfaces, ink `#1c1b19`; dark
`#1b1a18` page, `#22211f` / `#2a2927` / `#343230` surfaces stepping up by lightness, text `#ecebe6`; borders are the
ink at 8 % and 16 % alpha (9 % and 18 % in dark); shadows are built from three ink tokens (`--shadow-ink`,
`-soft`, `-deep`) that are transparent in dark but under what floats over a scrim. The accent stayed an ink, not the
amber of the plan: the accent paints text and focus rings, and an amber reads at under 2:1 on a light surface; the
warmth is the bubble and the greys. Paper got the same treatment with its own values. `theme.js`: `SCHEMES`
(system, light, dark), `applyStoredScheme` at start, `chooseScheme` kept as `dot.scheme`; Settings › General ›
Appearance gained a "Colour scheme" segmented control. Tests: `test_css_tokens.py` reads each look's block, computes
WCAG contrast and asserts text and text-2 at 4.5:1 and muted and faint at 3:1 on every surface in both schemes, the
inks on their grounds (on-accent, on-bubble, ok/warn/bad on their weak tints) at 4.5:1, and the surfaces stepping
down in light and up in dark; `test_controls.py` holds Dark on a light page and measures the page's luminance before,
after and after a reload (its section count was 11 since item 58 added Cost: 12 now, the test had not been run).
Screenshots of the controls page (both looks, both schemes, 1280 and 390), the Activity page, a run page and the
settings in held dark were looked at.

**Touchpoints.** `static/css/themes.css`, `static/js/theme.js`, `main.js`, `views/system.js`,
`tests/unit/test_css_tokens.py`, `tests/smoke/test_controls.py`, README (visual system paragraph).

### 64. The composer: a slash menu of commands, skills and MCP tools

**Status: done.** Built on `item-64-slash-composer` (MYP-206), merged d36d375 (PR #73).

**What.** Typing `/` at the start of a word in the composer (the new-run page and the run's dock) opens a menu over the
caret: the commands (`/btw`, the run modes, approvals, model), the skills by name with their one-line description, and
the MCP servers with their tools; the arrow keys move, Enter picks, Escape closes, letters filter; a pick leaves a chip
or the command's text in the composer. The composer grows with its text, sends on Enter and breaks a line on
Shift+Enter, shows the attachments and the options in one soft frame.

**Why.** The owner asks for it; today the only slash command is `/btw`, known from the README alone, and the skills
and MCP servers a run can use are invisible until the model names them.

**Design.** `GET /api/palette` (routes_system) returns the commands, the skills (`SkillCatalog`, name, description,
source) and the MCP servers with their tools (`McpHub.tools`), typed in `views.py`. `static/js/slash.js`: a listbox
(`role="listbox"`, `aria-activedescendant`) anchored above the composer, fed by the palette and filtered by the word
after the slash, grouped Commands, Skills, MCP; selecting a skill inserts "use the skill NAME: " and a tool inserts
its name, a command runs or inserts its text. `runs.js` and `run.js` share one composer module (`composer.js`:
textarea, autosize, Enter/Shift+Enter, attachments, slash menu); the dock of a run keeps its send button. The system
prompt is unchanged: a skill named in the goal is already read by the run. Unit test on the filter and grouping; a
smoke test that types `/` and picks a skill from a fixture skills root, at 1280 and 390 px.

**Design, as built (2026-10-11).** `GET /api/palette` (`server/palette.py`, `palette_view(skills, tools)`) returns
the skills by name (name, description, source) and the MCP servers by name, each with its tools by the name the model
calls them by and the first line of their description; the skills are read from the folders as a run reads them
(`load_skills` with the plugins and the playbook root). The commands are the web UI's own, since they act on the
composer and not on the daemon: `/task`, `/chat`, `/research` on the new chat page pick the kind of run (through the
same effects as a click on the segmented control), `/btw` in the box of a run inserts itself. `static/js/slash.js`
holds the words without the page: `slashWord(text, caret)` (a slash at the start of the text or after white space,
so a slash in a path or an address opens nothing), `paletteGroups(commands, palette)`, `filterGroups(groups, word)`
and `replaceSlashWord`. `static/js/composer.js` `wireComposer(textarea, { maxPx, send, commands })` is what the two
composers share: autosize, Enter sends and Shift+Enter breaks a line, and the menu (`role="listbox"`, options with
`aria-selected`, `aria-expanded`, `aria-controls` and `aria-activedescendant` on the box; ArrowUp/Down move, Enter
and Tab pick, Escape closes, a blur closes); the palette is asked for once per page on the first slash and a failure
is shown in the menu as a note, not hidden. The two frames stayed their own (the new chat's card with its options bar,
the dock with its send button): the plan's one composer module is the wiring, not the frame. A skill picked reads
"use the skill NAME: ", a tool its name and a space. No `tests/unit/test_slash.py`: the project has no JavaScript
test runner, so the unit test is `tests/unit/test_palette.py` on `palette_view` and the slash words are covered by the
smoke test `tests/smoke/test_composer.py`: a scratch home with one skill, the API's answer, and in the page at 1280
and 390 px: `/` opens the menu with Commands and Skills, the first option is active, `sc` filters to the skill, Enter
picks without sending, a slash inside a word opens nothing, Escape closes, `/res` and Tab pick the research mode and
leave no text, Shift+Enter breaks a line. The dock of a run is wired by the same module and not driven by the test.

**Touchpoints.** `server/routes_system.py`, `server/views.py`, `server/palette.py` (new), `static/js/composer.js`
(new), `static/js/slash.js` (new), `static/js/views/runs.js`, `static/js/views/run.js`, `app.css`,
`tests/unit/test_palette.py`, `tests/smoke/test_composer.py`, README.

### 65. The chrome that stays: the run's head and side panel while it scrolls

**Status: done.** Built on `item-65-sticky-chrome` (MYP-207), merged 5c3917f (PR #74).

**What.** On the run page the head (Dot's face and state, the model chip, the mode chips, Cancel) stays at the top of
the conversation while it scrolls, the inspector stays beside it to its full height with its own scroll, the dock
stays at the bottom; on a phone the head collapses to one line and the inspector is the drawer it is today.

**Why.** The head scrolls away with the first messages, so Cancel and the model's name are out of reach in a long
run; the inspector is sticky under the stage bar but its top offset is a fixed 68 px that the banner and the density
break; the owner asks for both to stay.

**Design.** `run.css`: `.chat-head` becomes `position: sticky; top: var(--stagebar-h)` with the bar's height measured
into a custom property by `layout.js` (the banner changes it), a soft backdrop (`--bg` at 85 % with a blur) so the
messages pass under it; `.inspector` takes the same offset and `max-height: calc(100dvh - var(--stagebar-h) - gap)`;
the `.page` column is the one scroll container (no `overflow` on `.run-layout`, which would defeat sticky). At 390 px
the head shows the face, the state and Cancel in one row. A smoke test scrolls a long scripted run at 1280 and 390 px
and asserts the head's and the inspector's bounding boxes stay in the viewport; screenshots in both schemes.

**Design, as built (2026-10-11).** The window was already the one scroll container (the stage bar and the sidebar are
sticky to it), so no scroll container moved. `layout.js` `trackStagebar(stagebar)` keeps the bar's height in
`--stagebar-h` on the page element through a `ResizeObserver` (the banner, the density and the width change it;
`app.css` declares 56 px as the value before the first measure); `shell.js` calls it once the bar is built. The
head (`.chat-head`, app.css) is `position: sticky; top: var(--stagebar-h)` on a backdrop of the page colour at 88 %
with a blur, within `.run-main`, so it stays while the messages pass under it; the inspector's offset and height
follow the same property instead of the fixed 68 px. Under 720 px the head is one row: the face at 28 px, the name
with the state and the date, and the buttons; the chips (model, mode, parent, campaign) are hidden there and read in
the Details card. `run.js` did not change: the head's markup already had the parts. The smoke test
`tests/smoke/test_run_chrome.py` plays a scripted run of thirty shell steps, opens it with the steps unfolded and the
panel on, checks that `--stagebar-h` equals the bar's measured bottom, scrolls to the middle and asserts the head's
top sits at the bar's bottom (within 1.5 px), the panel's top just under the bar and its bottom inside the viewport
(at 1280), no chip visible in the head (at 390), and the dock inside the viewport below the head; screenshots at 1280
and 390 px in both schemes were looked at.

**Touchpoints.** `static/css/run.css`, `app.css`, `static/js/layout.js`, `static/js/shell.js`,
`tests/smoke/test_run_chrome.py`, README.

## Part 8: a system that works on its own, learns and shows it is here (planned 2026-10-11)

The owner's eighth word, on 2026-10-11, after part 7: "continue with the next steps; do whatever it takes to bring
the project to the best level: a freely working, fully autonomous, continuously self-improving system with memory;
change the design completely, so that the UI says 'I am here' and the people who work in it all day are happy;
research the web where needed; and should we use Claude Haiku 5.5 for web searches directly, since the API carries
its own search?" Planned from a read-only map of what exists (memory, lessons, playbooks, scheduler, campaigns, the
home page), the Anthropic docs and the Models API, and what Claude Code desktop, Cursor, Linear and Raycast do for
presence and calm.

**What exists, and what does not.** Memory: notes saved and recalled by meaning (`memory/service.py`), injected at
each run's start (core, the owner's, then the top agent notes), forgotten only by hand. Learning: at most three
lessons after a run that ended done with a signal of trouble (`agent/lessons.py`, `[agent] lessons = "off"` by
default) and a playbook after a goal-mode run on a new site (`agent/playbooks.py`); nothing looks at a failed run, at
the day as a whole, or at lessons that aged. Autonomy: schedules and campaigns run unattended; approvals, questions
and the lesson's "propose" still wait for the owner. Presence: the home is a greeting and a composer; what Dot is
doing now lives in the sidebar's sections and on the Activity page; notifications are off until switched on and fire
whether or not the owner is looking. The Anthropic web search tool: supported by `claude-haiku-5-5` (the Models API
says so), $10 per 1,000 searches plus tokens (the docs; a failed search is not billed), citations always on,
`max_uses`, domain lists, errors as `web_search_tool_result_error` (`too_many_requests`, `max_uses_exceeded`,
`query_too_long`, `request_too_large`, `unavailable`). It is not free: one cent a search next to Haiku's tokens; the
`ollama` rung, which answered all 104 searches of item 60's run, stays first.

### How to start part 8 (the handoff prompt)

> Read ROADMAP.md part 8 and the status table below. One item at a time, in order; the protocol of part 5 (branch,
> gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch the owner's data
> directory except to read it and to restart the daemon when `/api/status` shows no active run; the owner's goals
> stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is English.

### Status, part 8

| #   | Item                                                                     | Status      | Commits |
| --- | ------------------------------------------------------------------------ | ----------- | ------- |
| 69  | The Anthropic rung: Haiku 5.5 searches through the API's own web search | done        | 927a720 |
| 70  | Reflection: Dot's day in review, every night                             | done        | dd13ec9 |
| 71  | Memory that rests: use decides what is recalled                          | done        | 6896e53 |
| 72  | The home that says "I am here"                                           | done        | 9020ee0 |
| 73  | Motion with meaning                                                      | done        | 1efede9 |
| 74  | Quiet chrome: the surfaces recede, the work leads                        | done        | f809abd |
| 75  | Told only when not looking                                               | done        | 9643889 |
| 76  | The live numbers of part 8                                               | in progress |         |

### 69. The Anthropic rung: Haiku 5.5 searches through the API's own web search

**Status: done.** Merged as `927a720` (PR #77, squash; MYP-209 Done).

**What.** A search rung that asks `claude-haiku-5-5` with the Messages API's `web_search` server tool and reads its
results as hits (title, address, the cited text, the page's age), second on the ladder after `ollama` when the
Anthropic key is present and `[web] anthropic_search` is not `"off"`; each search is counted in the run's spends at
the documented price and in `dotx stats`.

**Why.** The owner asks whether the API's own search should carry Dot's searches. It is reliable and already paid
through the key, but not free: one cent a search plus Haiku's tokens. As a second rung it answers when `ollama` is
benched and before the HTML engines, which throttle; as the first rung it would cost about a dollar per hundred
searches, so the owner chooses that with `[web] anthropic_search = "first"`.

**Design.** `web/ladder.py`: `SearchEngine` gains `"anthropic"`, `BENCH_S["anthropic"] = 120`. `web/search.py`:
`search_ladder` places it (`auto`: after `ollama`; `"first"`: before; `"off"`: absent) and `search_anthropic(client,
model, query, max_results)` posts one Messages request with `tools=[{"type": "web_search_20250305", "name":
"web_search", "max_uses": 1}]`, `max_tokens` small, a system line that asks for no prose, and parses the
`web_search_tool_result` block into `SearchHit`s (`encrypted_content` is not kept); `web_search_tool_result_error`
maps `too_many_requests`/`unavailable` to a bench and the rest to a `WebError` with the code. `llm/anthropic.py` is
not changed: the search is its own request through the provider's client. `[models] search` names the model
(`anthropic:claude-haiku-5-5`); `[web] anthropic_search_price_usd` holds the documented 0.01 so a price change is a
config line. The spend is recorded in `spends` with kind `web_search`; `dotx stats` and the cost card show searches
by engine and the dollars. Doctor's search row names the rung. Unit test on the parsing and the errors (recorded
bodies); a live smoke test with the key (skipped without) runs one search and asserts a hit and a spend.

**Design, as built (2026-10-11).** As planned, with three differences. The request goes through the `WebClient`'s
`post_json` like Ollama's (`search_anthropic(client, AnthropicSearch(api_key, model), query, max_results)`), not
through the provider's client: one place for the web's requests, and the provider's retry policy is for the model's
own calls. No spend row and no price in the config: the `spends` table is the pacing budget of a host (item 23), not
a ledger, and a price written into Dot goes stale; instead `store/stats.py` `search_counts` reads the engine from the
first line of every `web_search` result ("N results for … from anthropic"), `dotx stats` prints "searches answered:
ollama 12, anthropic 3" and the cost card a "Searches" cell whose hint says what a search on the Anthropic rung costs;
the price stays in README with its date. Snippets: the server tool returns pages as `encrypted_content`, not text, so
a hit's snippet is the text the relay cited from that page (the `cited_text` of the answer's citations) with the page's
age, and empty when it cited nothing. The ladder: `search_ladder(choice, ollama_key_present, anthropic, browser)`
where `anthropic` is `[web] anthropic_search` when the key is set and `None` without it; `auto` puts the rung after
`ollama`, `"first"` before, `"off"` nowhere; `search_backend = "anthropic"` names it alone. `[models] search`
(`anthropic:claude-haiku-5-5`) must be an Anthropic model, refused at the call otherwise. The relay prompt asks for
exactly one search with the user's words and one citing sentence; `max_tokens` 400; HTTP 429 and the tool errors
`too_many_requests` and `unavailable` bench the rung for 120 s, other tool errors fail the search with their code.
The live smoke test ran one search with the machine's key: 1 passed in 5.5 s, hits with addresses and titles.

**Touchpoints.** `web/ladder.py`, `web/search.py`, `config.py`, `defaults.toml`, `runtime.py`, `tools/web.py`,
`doctor.py`, `store/stats.py`, `cli_stats.py`, `server/static/js/cost.js`, `tests/unit/test_search_anthropic.py`,
`tests/unit/test_research.py`, `tests/smoke/test_search_anthropic.py`, README (models table, web tools and ladder).

### 70. Reflection: Dot's day in review, every night

**Status: done.** Merged as `dd13ec9` (PR #78, squash; MYP-210 Done).

**What.** Once a day, unattended, Dot reads the runs that ended since the last reflection and writes what it learned:
lessons from failed and cancelled runs too (today only runs that ended done teach), one short journal note for the
day ("what I did and learned on 2026-10-11", shown on the home of item 72), merged duplicates among its lesson notes,
and retired lessons that no run recalled in sixty days. It touches only its own notes, never the owner's.

**Why.** The owner asks for a system that improves itself. Learning today happens only at the end of a run that went
well; a failed run leaves nothing, lessons pile up without a merge, and nothing tells the owner what the day taught.

**Design.** `agent/reflection.py`: `reflect(runtime, since) -> Reflection(runs_read, lessons_written, merged,
retired, journal)`; the runs come from the store (ended, not delegated, since the last `reflection` event); the
lesson writer of `agent/review.py` is asked per run with the run's signals and result, failed runs included, with the
same cap of three; near-duplicates among `lesson` notes are found by the memory ranking (cosine above 0.92) and the
older one folded into the newer, with provenance kept; lessons whose `last_used_at` is older than
`[agent] lesson_rest_days` (60) are retired to a `rest` tag (recalled no more, still on the Memory page); the journal
is a note tagged `journal` with the date. The scheduler gets a built-in schedule `reflection` at `[agent]
reflection_time` (03:30 local, `"off"` to stop), fired like a schedule but running `reflect` instead of a run, with a
`reflection` event and a row in Activity ("Reflected on 7 runs: 2 lessons, 1 merged"). `dotx reflect` runs it by
hand. Lessons under `propose` stay proposals. Smoke test: three scripted runs (done, failed, cancelled) and a
reflection on a scripted review model; asserts the lessons, the merge, the journal and the Activity row.

**Touchpoints.** `agent/reflection.py` (new), `agent/lessons.py`, `agent/review.py`, `memory/service.py`,
`scheduler/runner.py`, `config.py`, `defaults.toml`, `cli_reflect.py` (new), `server/activity.py`,
`tests/smoke/test_reflection.py`, README (Memory, Schedules).

**Design, as built (2026-10-11).** As planned, with these differences. The last reflection is a record in the store's
key/value table (`reflection.last`, a `Reflection` with `at`, `since`, the counts and the journal's id), not an event:
events belong to runs, and the reflection is none; the first reflection reads the last day. The scheduler keeps no
built-in `ScheduleRecord`: `Scheduler.tick` asks `reflection_due(time_of_day, anchor, now, zone)` (the daily cron of
`[agent] reflection_time` after the anchor, which is the later of the last reflection and the last attempt or the
scheduler's making), and runs `RunManager.reflect` in a task of its own, so a night the daemon slept through is caught
up once at its next tick and a failure waits for the next night. The lesson writer is not changed: the reflection
builds a `LessonCase` from the stored `tool_result` events (`digest_of`, `call_facts` as the loop keeps failures and
resolved calls, `read_outside` for the taint from the result kinds and the web and browser channels) and asks through
`lessons.learn_from` with the run's ending as one more signal (`ending_signal`: "The run ended failed: …", "The run was
stopped before it finished: …"); a run the loop asked at its end (a `lessons_proposed` or `lessons_failed` event) is not
asked again, and with `[agent] lessons = "off"` no lesson is drawn while the merge, the retirement and the journal
still happen. Duplicates are found by the cosine of the stored vectors (0.92) when both notes have one and by
`lessons.repeats` (the words) otherwise, each note in at most one pair; the older is deleted, the newer keeps both
notes' tags, and the log line `lesson_merged` keeps the older's text and source. Retirement adds the `rest` tag;
`memory.recall` drops a resting note, so `memory_search`, the prompt's notes and the lesson writer's known notes no
longer see it, while `dotx memory list` and the Memory page do (item 71 makes use the ranking). The journal note's
source is the word `reflection` (`memory.service.REFLECTION_SOURCE`), so it is neither an owner's note (which would
reach every run) nor linked to a run; the Memory page says "Written by the nightly reflection" and the model reads
"your nightly reflection wrote it". No journal is written when nothing happened. Activity gets the row kind
`reflection` ("Reflected on 4 runs: 2 lessons, 1 merged, 1 retired.", opens the Memory page) for a reflection that
ran today. Smoke test: four scripted runs (done, failed, stopped, done after a stumble that the loop asked at its end),
two twin lessons and a stale one, the scheduler's tick at 03:30 fires the reflection; asserts the two questions, the
three saved lessons, the merge, the rest, the journal, the Activity row and that the next tick does not fire again.

### 71. Memory that rests: use decides what is recalled

**Status: done.** Merged as `6896e53` (PR #79, squash; MYP-211 Done).

**What.** A note that a run recalled and built on is marked as used (the store has `mark_memories_used`); the ranking
of auto-recalled agent notes favours notes used lately and lets notes never used rest after `[memory] rest_days`
(90): still on the Memory page under "Resting", recalled by a search, not injected on their own. The owner's notes
and core notes never rest.

**Why.** Auto-injection has a fixed budget (6 agent notes); without use in the ranking the first notes written keep
their place over the notes that help. A memory that only grows stops being one.

**Design.** `memory/ranking.py`: `rank(hits, now)` adds a recency-of-use term (half-life `[memory] use_half_life_days`,
30) to the RRF score of agent notes; `memory/service.py` `prompt_memories` skips resting notes; `store`:
`last_used_at`, `use_count` on `memories` (migration), `rest` derived, not stored; the Memory page groups "Resting"
and shows uses; `GET /memories` carries both. Unit test on the ranking with dates; the memory smoke test extended.

**Touchpoints.** `memory/ranking.py`, `memory/service.py`, `store/db.py`, `server/routes_data.py`, `views.py`,
`static/js/views/memory.js`, `defaults.toml`, `tests/unit/test_memory_ranking.py`, `tests/smoke/test_memory.py`,
README (Memory).

**Design, as built (2026-10-11).** As planned, with these differences. The store needed no migration: `memories`
already had `used_count` and `last_used_at` (named so, not `use_count`), written by `mark_memories_used` when a run
starts with a note, is handed one that changed, or finds one with `memory_search`. The ranking is
`ranking.fused_scores` (the reciprocal rank fusion scores, kept) plus `ranked_by_use(scores, notes, now, half_life_s,
limit)`: an agent's note gets `use_lift` (1 for a use now, halving every `[memory] use_half_life_days`, 0 never used)
times a second place in one list (1/62), so two rankers agreeing on an unused note still beat one ranker and a recent
use; the user's notes stand on their fused score alone. `recall` fetches every fused candidate and ranks it so (the
cut to `limit` comes after the lift), still dropping a note the reflection retired (tag `rest`, item 70). Rest is
derived, not stored: `service.resting(record, now, rest_days)` is true for an agent's note (a source, no `core` tag)
that carries the `rest` tag or that no run was handed (`used_count == 0`) in `[memory] rest_days` since it was written;
`prompt_memories` skips resting notes among the related ones, a search still finds them (unless retired).
`GET /api/memories` carries `resting`, the ids of the page's items that rest, and the Memory page lists them under a
"Resting" head with the hint "No run was handed these lately; a search still finds them, runs no longer start with
them"; the uses were shown already ("Used 3 times, last 2 days ago"). Unit tests on the scores, the lift, the order and
the rest rule; the words-only memory smoke tests gain a use that reorders a search and an old agent note that rests
while the user's old note still reaches the run.

### 72. The home that says "I am here"

**Status: done.** Merged as `9020ee0` (PR #80, squash; MYP-212 Done).

**What.** The home is Dot's presence: the face in its state, a greeting by the time of day with the owner's name
(Settings › General), one line of now ("Working on 2 runs · 1 waits for you · 3 ended today · 1 lesson"), a Today
strip (what waits, what works, what ended, what was learned, what is due) that follows the lifecycle stream, and the
composer; an empty day says so in one line. Nothing moves to the sidebar that is not already there.

**Why.** The owner wants a UI that says "I am here". Today the home is a greeting and a box; what Dot is doing lives
in the sidebar's small print and on another page.

**Design.** `views/runs.js` gains `presence.js`: `nowLine(activity, journal)` (pure words from `/api/activity` and
the day's journal note of item 70, with sensible words before item 70 lands), `todayStrip(activity)` (rows with one
action each, Enter opens; the same `nestRows` as item 68), a greeting from the hour and the name; `/api/activity`
gains `learned_today` (the journal and the lessons written today) and `due` (schedules firing within the day,
campaigns' next session); the strip updates on the lifecycle stream like the sidebar. The face states of item 49
drive the greeting's verb. Screenshots at 1280 and 390 in both schemes; a smoke test plays a waiting, a working and an
ended run and reads the now line and the strip.

**Touchpoints.** `static/js/presence.js` (new), `views/runs.js`, `server/activity.py`, `views.py`, `app.css`,
`tests/smoke/test_home.py`, README (the web UI).

**Design, as built (2026-10-11).** As planned, with these differences. The row of the Activity list moved to
`static/js/activityrow.js` (`activityRow`, `rowFacts`, `KIND_ICON`, `KIND_WORD`, `LIFECYCLE_KINDS`), which the Activity
page and the home's strip both draw from, so a row reads the same in both places; a row whose status is empty or
``scheduled`` (a thing due, a lesson, the journal) carries no state badge. `/api/activity` gains `learned_today` (the
journal note and the lesson notes written today, newest first; a lesson opens the run it came from, the journal the
Memory page) and `due` (the switched-on schedules firing and the active campaigns' next sessions before the day ends,
soonest first, "fires at 14:30" in this Mac's zone); the Activity page keeps its three sections. `presence.js` holds
the words as pure functions: `greeting(hour, owner, state)` (Hello before 5, Good morning, Good afternoon from 12, Good
evening from 18; the verb from the face's state: "What should I work on?", "I'm on it. Anything else?", "Something waits
for you."), `nowLine(activity)` ("Working on 2 runs · 1 waits for you · 3 ended today · 1 lesson · the day's journal ·
2 things due today"; a quiet day: "A quiet day so far: nothing works, nothing waits, nothing ended.") and
`todaySections(activity)` (Waits for you, Working, Due today, Learned today, Ended today; only the sections with rows,
five rows each and "and N more in Activity"). The owner's name is a preference of the browser like Dot's (`dot.owner`,
"What Dot calls you" under Settings › General › Name), never sent to the daemon. The strip stands under the composer,
not above it, so the composer keeps its place in the middle and a long day grows downwards; the face's state and the
greeting's verb follow the status snapshot (`overallState`), the line and the strip reload on the lifecycle events and,
while something works, on the status refresh. Smoke test (`tests/smoke/test_home.py`): a run that ended, a lesson note,
a schedule due in five minutes, a run that asks and a run that sleeps; the home at 1280 and 390 in both schemes, the
greeting with the name, the now line, the five sections, then the answer and the runs' end move the line to "3 ended
today · 1 lesson" and the face to idle.

### 73. Motion with meaning

**Status: done.** Merged as `1efede9` (PR #81, squash; MYP-213 Done).

**What.** Motion tokens (`--motion-quick` 120 ms, `--motion-base` 200 ms, `--motion-slow` 320 ms, one standard
easing and one emphasised), the face breathes while idle and pulses while working, cards and rows enter with a short
fade, state badges cross-fade, the menu of item 64 and the toasts fade rather than slide, and under
`prefers-reduced-motion` every transform goes and the fades stay (a status change must still be seen).

**Why.** Presence is also motion: a face that breathes says "here" more than a label; and the people who use the UI
all day need motion that is short, consistent and switchable.

**Design.** `app.css`: the tokens next to the type tokens; `@keyframes breathe`, `pulse`, `fade-in`; the face's
`data-state` picks the animation; `.card`, `.activity-row`, `.slash-menu`, `.toast` use the fades; a
`@media (prefers-reduced-motion: reduce)` block strips transforms and keeps opacity. Unit test: every `transition` and
`animation` in the drawn sheets uses a motion token (as the colour test does). Smoke test: the face's computed
`animation-name` by state, and with `reduce_motion` emulated no transform animates.

**Touchpoints.** `app.css`, `shell.css`, `run.css`, `static/js/face.js`, `tests/unit/test_css_tokens.py`,
`tests/smoke/test_face.py`, README (the visual system).

**Design, as built (2026-10-11).** As planned, with these differences. The tokens sit with the spacing tokens in
`app.css`: `--motion-quick` 120 ms, `--motion-base` 200 ms, `--motion-slow` 320 ms, `--ease` and `--ease-emphasis`,
and, because what repeats has a rhythm of its own, `--motion-breath` 4 s, `--motion-pulse` 1.6 s, `--motion-blink`
1.4 s, `--motion-spin` 0.8 s, `--motion-moment` 3 s (the tick of a run that ended) and `--motion-shimmer` 1.8 s; the
unit test flags any literal duration in a drawn sheet (`0s` is the instant, not a duration), so `run.css` and
`settings.css`, which had none, pass as they are. `face.js` did not change: the state attribute it sets already picks
the animation, so the face's core breathes at `idle` (`breathe`, scale 1.12, slow) and pulses at `working`
(`pulse-core`, scale 1.4, which was the old `breath`); the ring's blink at `waiting` and the tick's moment at `done`
stay. `.card`, `.slash-menu` and `.toast` fade in (`fade-in`, opacity only; the toast's `rise` slide is gone); the
rows of the Activity list and the Today strip are inside cards and fade with them, so no rule of their own; a badge
cross-fades its colours. Reduced motion no longer stops every animation and transition with `!important`: it names
what transforms (the core's breath and pulse, the spinners, the switch's knob, the chevron, the drawer's slide in
`shell.css`) and leaves the fades, the blink and the tick, so the block that forced the tick to show without motion is
gone with the blanket rule. Smoke test in `tests/smoke/test_face.py`: the core's computed `animation-name` is
`breathe` at rest and `pulse-core` at work, `none` in both under emulated reduced motion while a card still fades in,
and the drawer's transition is `0s` under it. Taken back in item 74: the fade of `.card`, because a list that repaints
on every lifecycle event (the Today strip, the Activity page) blinked with it; the slash menu and the toasts, which
appear once, keep theirs.

### 74. Quiet chrome: the surfaces recede, the work leads

**Status: done.** Merged as `f809abd` (PR #82, squash; MYP-214 Done).

**What.** The parts that orient recede so the conversation leads (Linear's rule): the sidebar without dividers
between its groups, its labels lighter and its active row a surface step, the stage bar's pills quieter until they
have something to say, the timeline's step rows one line each with the duration shown on hover, the cards' heads
lighter, one rhythm of 8 px for every gap and pad (checked), and one primary action per list row (Enter opens).

**Why.** The owner wants the people who work in the UI all day to be happy: fewer lines, fewer weights, one rhythm.

**Design.** `shell.css`, `app.css`, `run.css`: the rules above, on the existing tokens, with new spacing tokens where
a value is not a multiple of 8 (4 allowed for the smallest); `tests/unit/test_css_tokens.py` asserts every `gap`,
`padding` and `margin` is a token or a multiple of 4; screenshots of the sidebar, a run, the Activity page and the
settings at 1280 and 390 in both schemes, looked at; the controls page updated.

**Touchpoints.** `shell.css`, `app.css`, `run.css`, `views/controls.js`, `tests/unit/test_css_tokens.py`,
`tests/smoke/test_controls.py`, README (the visual system).

**Design, as built (2026-10-11).** As planned, with these differences. The rhythm: every `gap`, `padding` and
`margin` of the four drawn sheets (157 declarations, `settings.css` included) was moved to the 4 px grid by one
rounding (3 and 5 to 4, 6 to 10 to 8, 11 to 13 to 12, 14 to 18 to 16, 22 to 26 to 24, 50 to 48, 70 to 72) and the
hairline (1 and 2 px) became the token `--space-half`; `--pad-side` is now two space tokens. The unit test reads those
properties and flags a pixel value off the grid; one new token, not several, since the grid itself is the rule.
The sidebar's one divider (above the foot) is gone; `.side-label` is `--faint` at weight 500; the open row is
`--surface` with the lightest shadow, a step up from the sidebar's ground in both schemes (`--surface-2` would not
show: it is a shade of the sidebar's own ground). The stage bar's pills lose their border and ground unless they
carry a count (`.warn`, `.run`); the connection pill was already its dot alone. `.card-head` is weight 500 in the
second ink over a 70 % line. The timeline's folded step was one line already (`.tool-sum` ellipsis); the duration
(`.tool-time`) now shows on hover, on focus and while the step is open. "One primary action per list row" needed no
change: a row that opens something is a link, and Enter follows it. `views/controls.js` did not change: the controls
page draws the same classes and shows the new rhythm by itself. Looked at: the controls page in both looks, the home
and the sidebar, the Activity page and a run's page at 1280 and 390 in both schemes from the existing smoke tests.

### 75. Told only when not looking

**Status: done.** Merged as `9643889` (PR #83, squash; MYP-215 Done). CI did not run for the PR (GitHub Actions: the account's payments failed or its spending limit is reached, both jobs refused in 5 s); merged on the local gate: format, lint, mypy, 763 unit tests, `dotx eval check`, the attention smoke test.

**What.** A desktop notification or a push for a run that ended or asks fires only when the owner is not looking at
that run: the tab hidden, or another page open (Claude Code desktop's rule); the tab title keeps counting; a setting
"Tell me even when I am looking" turns it off.

**Why.** A notification for what is on screen is noise, and the owner who works in the UI all day gets many.

**Design.** `attention.js`: `isLooking(runId)` from `document.visibilityState` and the current route; `tellAboutEnd`
and `tellAboutApproval` check it; the push subscription carries nothing new (the daemon pushes; the page shows or
not). Smoke test with a stubbed Notification: a run that ends on the open page notifies nothing; on a hidden page it
does.

**Touchpoints.** `static/js/attention.js`, `views/system.js`, `tests/smoke/test_attention.py`, README (the web UI).

**Design, as built (2026-10-11).** As planned. `attention.js` had "away from the page" (the tab hidden or the window
inactive, any page); it is now `isLooking(runId, { hash, visibility, focused })`, pure: the run's own page open
(`#/runs/<id>`), the tab visible and the window active, and `worthATell(runId)` tells unless that holds, so a run
that ends or asks while another page is open (the home, Activity, the settings) is told about, which it was not
before. The setting is the preference `dot.notify.always` ("Tell me even when I am looking", a second switch under
Settings › General › Notifications, enabled only while notifications are on). The push subscription is untouched: a
browser the daemon pushes to stays quiet on the page as before. Smoke test (`tests/smoke/test_attention.py`): the
page's `Notification` is a stub that records what it was shown and `document.hasFocus` says yes; a run that ends on
its own open page notifies nothing, one that ends while the home is open notifies, one that ends while the tab is
hidden notifies, and with the setting on a run that ends on its open page notifies too.

### 76. The live numbers of part 8

**Status: in progress (2026-10-11).** A measurement, not code. The clock starts with the daemon restart of 2026-10-11
after item 75 merged (the daemon had run e475368, none of items 69 to 75, until then); the reading is due on or after
2026-10-18 and goes here under "What the week showed".

**Baseline (2026-10-11, before the restart; `dotx stats` and the store, read-only).**

| measure                                         | baseline                                     | read with                                   |
| ----------------------------------------------- | -------------------------------------------- | ------------------------------------------- |
| searches by rung, 30 days                       | ollama 129, anthropic 0 (the rung was not in) | `dotx stats` "searches answered"; cost card |
| anthropic search dollars                        | 0                                            | the cost card's Searches cell                |
| reflections run; lessons written/merged/retired | 0; 0/0/0 (`reflection.last` absent)          | Activity rows of kind `reflection`, the kv  |
| notes tagged lesson / journal / rest            | 0 / 0 / 0 of 22 notes                        | Memory page groups                           |
| notes used at least once                        | 21 of 22; resting 0 (every note younger)     | `used_count`, Memory page "Resting"          |
| runs ended, 7 days                              | 22 (11 done, 9 failed, 2 cancelled)          | `dotx stats`, Activity "ended today"         |
| notifications sent against runs ended           | not in the store: the browser decides and records nothing | a day's count by hand, notifications on, against "ended today" |

**What.** After a week on the daemon: searches by rung and their dollars (item 69), reflections run and lessons
written, merged and retired (item 70), notes resting and the share of recalled notes used (item 71), notifications
sent against runs ended (item 75); read with `dotx stats`, the Activity page and the store, read-only, and written
here.

## Part 9: every corner clean: one type system, one bar, flat surfaces (planned 2026-10-11)

The owner's ninth word, on 2026-10-11, after part 8: "redesign the whole thing, researched on the web, newer and
proper: some places are great, some feel cheap; every inch matters and must have the right, clean design; ChatGPT,
Claude and Grok are tidier and each has a text family and size hierarchy; pay attention down to the smallest point,
research and fix". Planned from a screenshot tour of every page in both schemes and at 1280 and 390 (36 shots, read),
an inventory of the stylesheets, and a web research round on the three products.

**Status (2026-10-11): built.** Items 77 to 83 merged the same day, each with its screenshot tour read; the tour itself
(`tests/smoke/test_tour.py`, 56 shots) and the token tests (type, spacing, motion, contrast, the hue ring) hold the
system. What is left of the redesign is the owner's eye on the live daemon, and the item 76 reading of part 8.

**What the research gave.** The live stylesheets of chatgpt.com, claude.ai and grok.com could not be read (403, text
only); the numbers come from third-party extractions and are low to medium trust. What they agree on: four to seven
type sizes in use (ChatGPT about 13, 14, 15, 16, 20, 24, 32), three weights at most (400, 500, 600; no 700 in the
interface), emphasis by colour and size rather than weight, hairline borders and flat cards over shadows, a 4 px
spacing base, radii 6/8/12/16 with a rounder composer, and a message column of about 768 px. The faces are
proprietary (OpenAI Sans, Anthropic's; Grok's unknown); the open equivalents with a verified SIL OFL licence are Inter
(github.com/rsms/inter) and Geist Sans and Mono (github.com/vercel/geist-font).

**What the tour showed.** Six type tokens but five weights in use (400, 500, 550, 600, 650) and nine sizes outside
the tokens (`em` headings in prose, `.plan-step`); the run's sticky head is translucent and blurred, so a user bubble
scrolled under it shows as a yellow haze and the first lines read half under it; the daemon's dot and the stop button
float in the top right of a bar that has no other content; Settings has two title rows (the page and the tab);
section labels are uppercase on some pages and sentence case on others; meta lines are strings joined with middle
dots; the campaign's numbers are monospace chips; the Activity page's empty states are tall cards with an icon; every
list is a card with a shadow; the dark scheme's first paint after sign-in may be light (one shot, to be verified).

**The system (the decisions of this part).**

- Type: Inter (variable, self-hosted, `font-optical-sizing: auto`) for the interface and the prose, Geist Mono for
  code and identifiers; `system-ui` and `ui-monospace` as fallbacks. Seven sizes with their line-heights, each a
  token with both: 12/16 (badges only), 13/18 (meta, hints), 14/20 (the interface: sidebar, buttons, labels, inputs),
  15/24 (prose and the composer), 17/24 (section titles, prose h1), 20/28 (page titles), 24/32 (the home's greeting).
  Three weights: 400 text, 500 interface labels and names, 600 titles. Letter-spacing 0 under 20 px, -0.01em at 20
  and above. Tabular numerals on every number that sits in a column or a row of meta.
- Bars: one top bar on every page, solid, with a hairline under it; it carries the sidebar opener, the page's name,
  on the run page the run's face, state and start, and on the right the daemon's dot, the pills and the stop. The run
  page's head is that bar; nothing scrolls under a blur. Settings has one title row.
- Surfaces: the page, the sidebar and one raised surface; cards flat with a hairline, radius 12; no shadow but under
  menus and dialogs; the composer a 1 px border at radius 24; the user's bubble a surface, not a colour.
- Words: section labels in sentence case, no uppercase; a meta line is separate spans, not a dotted string; a number
  with its noun ("2 posts deleted"); empty states one line of text with the action, no card and no icon.

### How to start part 9 (the handoff prompt)

> Read ROADMAP.md part 9 and the status table below. One item at a time, in order; the protocol of part 5 (branch,
> gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Every item ends with the
> screenshot tour (`tests/smoke/test_tour.py` from item 77 on) read by eye in both schemes at 1280 and 390. Never
> touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active
> run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written
> is English.

### Status, part 9

| #   | Item                                                                     | Status      | Commits |
| --- | ------------------------------------------------------------------------ | ----------- | ------- |
| 77  | The type system: Inter and Geist Mono, seven sizes, three weights        | done        | ffa00cb |
| 78  | One bar: the top bar carries the page, the run's head and the daemon     | done        | 7a7e43f |
| 79  | Flat surfaces and plain words: cards, labels, meta, chips, empty states  | done        | ab5f33a |
| 80  | Forms and controls: one grammar for inputs, selects, switches, tables    | done        | 132ce0d |
| 81  | Dark, first paint and the tour: every page read in both schemes          | done        | 04693d7 |
| 82  | Delegated runs as rows: face, state, last line, time; the child's bar    | done        | 6454345 |
| 83  | Faces for the actors: a hue each, alive in the rows and the bar          | done        | d760ea3 |
| 84  | The side panel: inset bodies, facts as spans, one card grammar           | done        | 82c06b8 |
| 85  | Motion: the sidebar slides, the panel slides, folds fade, pages cross-fade | done        | 7f02492 |
| 86  | Agents: profiles with a face, a role, a model and their own chats         | done        | bd12b54 |
| 87  | Agents together: messages between agents, the team's thread, the harness  | done        | 23c6b5c |
| 88  | Desktop and web, one interface: the Tauri shell on the same pages         | done        | c4d9b88 |
| 89  | Learning on, and measured: every note handed to a run is scored by how it ended | done        | a779b72 |
| 90  | Memory that keeps what helps: the score decides what is recalled, rests, retires | done        | fa775a0 |
| 91  | Recall by budget: the notes a run starts with fit a token budget, best first     | done        | 9222a5d |
| 92  | Skills from its own work: a task that went well becomes a skill for its kind     | done        | 3074107 |
| 93  | The owner, in one section: what the reflection knows about how you work         | done        | 6d64e43 |
| 94  | An agent learns for itself: lessons, skills and a score per agent                | done        | 937cca4 |
| 95  | The sidebar, smaller: the nav under New chat, sections that fold, rows cut short   | done        | f3dce6e |
| 96  | The run page: the arrow above the composer, the cost in the side panel            | done        | 3a8a20b |
| 97  | Chrome in the background: the live view shows the page, the window stays put       | done        | 558621b |
| 98  | The web that is refused: 999, 403 and their kind meet the browser, not the end     | done        | 65433d0 |
| 99  | The team that grows: a run makes a lasting teammate and knows of it                | done        | 724768e |
| 100 | The review: every feature of parts 1 to 12 read once more on the live pages        | done        | 62585c2 |
| 101 | The character engine: bloub's one shape with two eyes, as plain modules of Dot's UI | done        | 8c3a590 |
| 102 | The look of every agent: shape, expression and colour, chosen in the editor and worn in every row | done        | f6afe5d |
| 103 | Alive beside the chat: the character works, waits, finishes and sleeps with the run | done        | 0a0fdaa |
| 104 | The composer remade: one frame, quiet menus, drafts that keep, a face that listens  | done        | 9261fa9 |
| 105 | The small polish part 12's review left: a scroll cue, empty sections that point, the Browser grid, the tour's content, Watch on an ended run | done        | c14b670 |
| 106 | The review: every feature of parts 1 to 13 read once more, the characters' cost measured | done        | 8e3818d |
| 107 | The fixes of the second look: the still face follows, the favicon freed, TLS reads retried, the sidebar's head and order | done        | 6c79636 |
| 108 | The composer with marks: a mark per provider, an icon on every pill, the reasoning as its own pill | done        | 108c91d |
| 109 | Dot's computer as a stage: a frame with a size, the control under it, the character at the head of the chat | done        | b5d42c0 |
| 110 | Rules in plain words: the model writes the rule, an approval card offers one        | done        | 6da73c4 |
| 111 | The review: part 14 read once more                                                  | done        | 5d96935 |
| 112 | Both eyes move: one key per eye in the character's drawing                          | done        | 392132c |
| 113 | The quiet head: no character card at the head of the chat, no brand in the sidebar  | done        | ea21f27 |
| 114 | Dot's mark and its opening: a dot that looks, the wordmark, the icons, a bounce at launch | done        | ea014d5 |
| 115 | The composer once more: one bar that never wraps, labels that fold, a quiet warning | done        | a667d39 |
| 116 | The deep review: every part of the code read once more, and fixed                   | done        | 0304257 |
| 117 | The helper keeps its parent's rights: a delegated run takes its parent's mode       | done        | e51f9c1 |
| 118 | Chrome behind: every tab opened in the background, the front given back             | done        | 6dfbf07 |
| 119 | The models: the prompt cache on OpenCode Go's Messages route, the free ones marked   | done        | acfc56a |
| 120 | The run page's head: Stop in the send button's place, the emergency stop in the sidebar | done        | 6c1a7b3 |
| 121 | The calm look, after T3 Code: the sidebar, the chat, the composer and the panel     | done        | 4f2b0cd |
| 122 | The sidebar says what works: Working and its time on a chat's row and a working run's | done        | 10fd83f |
| 123 | A helper folded in the chat: its row until the owner opens it                       | done        | a2de64d |
| 124 | The home's first screen: the composer in the middle, the day folded under it         | done        | 41ec542 |
| 125 | What the failed runs showed: the journal's day, the snapshot's arguments             | done        | 36c0eeb |
| 126 | Themes, after T3 Code: a page for the look, a theme for light and one for dark       | done        | c5dfc76 |
| 127 | The interface, the motion and the type                                               | done        | 9c73d80 |
| 128 | Your own themes, kept by the daemon                                                  | done        | 30b2434 |
| 129 | The composer at the foot of the window                                               | done        | 048f3aa |
| 130 | A chat's menu: pin, rename, copy the link, select, delete                            | done        | d7e7d5e |
| 131 | Unread chats                                                                         | done        | 34e1fe1 |
| 132 | Runs that keep going on every model when asked to                                    | done        | 002575b |
| 133 | The composer's choices on a chat's page                                              | done        | bf11312 |
| 134 | The traffic lights on the line of the top bars                                       | done        | a5b0220 |
| 135 | What a run may change, send and open                                                 | done        | cb76e7d |
| 136 | Spending, pacing and stops when two things happen at once                            | done        | bb18c32 |
| 137 | A reply cut off is not an answer                                                     | done        | 43cf3f9 |
| 138 | The store and the daemon's plumbing                                                  | in progress |         |
| 139 | The web UI and the desktop shell                                                     | planned     |         |

### 77. The type system: Inter and Geist Mono, seven sizes, three weights

**Status: done.** Merged as `ffa00cb` (PR #86, squash; MYP-217 Done).

**Design, as built (2026-10-11).** `static/fonts` holds `InterVariable.woff2`, `InterVariable-Italic.woff2` (Inter
4.1) and `GeistMono-Variable.woff2` with their OFL licence files; `app.css` declares the three `@font-face` blocks
(weight 100 to 900, `font-display: swap`) and the CSP gained `font-src 'self'` (`guard.py`). The tokens: `--font`
(Inter, then `system-ui`), `--mono` (Geist Mono, then `ui-monospace`), `--voice` (the Paper look's serif for its
titles), `--text-{xs,sm,ui,md,lg,xl,2xl}` 12/13/14/15/17/20/24 with `--leading-*` 16/18/20/24/24/28/32,
`--weight-{text,label,title}` 400/500/600, `--tracking-title` -0.01em and `--tracking-caps` 0.04em (the uppercase
labels keep it until item 79 removes them). The body is 14/20 with `font-optical-sizing: auto` and tabular numerals
everywhere. Every `font-size`, `font-weight`, `line-height`, `letter-spacing`, `font-family` and `font` shorthand in
the five sheets names a token: the 650 and 550 weights are gone (titles 600, labels and names 500), the prose's
headings are 17/24 (h1) and 15/24 (h2 in the title weight, h3 and h4 in the label weight) instead of `em` factors,
code and the mono blocks are 13/18, the Paper look no longer sets its own prose size. The Controls page's Type card
shows the seven steps with their names. `tests/unit/test_css_tokens.py` refuses a size in px or em, a weight, a
line-height, a letter-spacing or a family that is not a token (the `@font-face` blocks excepted), checks the three
files and the two licences are there and that the CSP names `font-src`. `tests/smoke/test_tour.py` plays one
scripted run with a shell step and a Markdown answer, saves a note, and screenshots fourteen pages (the sign-in's
first paint included) in light and dark at 1280 and 390 into its temp directory, 56 shots, asserting each page's
title and that the body's face is Inter.

**What.** The two faces self-hosted under `static/fonts` (woff2, OFL licence files beside them), `@font-face` with
`font-display: swap`; the tokens `--text-{xs,sm,ui,md,lg,xl,2xl}` each a size and `--leading-*` its line-height,
`--weight-{text,label,title}`; every `font-size`, `font-weight`, `line-height` and `letter-spacing` declaration in
the five stylesheets on the tokens (the prose headings included, in px not em); `font-variant-numeric: tabular-nums`
on meta and columns; the Controls page's Type card shows the seven steps. A unit test reads the stylesheets and
refuses any size, weight or line-height off the scale. `tests/smoke/test_tour.py`: the screenshot tour of every page
(light and dark, 1280 and 390) written to the test's temp dir for reading.

### 78. One bar: the top bar carries the page, the run's head and the daemon

**Status: done.** Merged as `7a7e43f` (PR #87, squash; MYP-218 Done).

**Design, as built (2026-10-11).** `static/js/stage.js` owns two slots the shell places in the bar once, between the
sidebar opener and the daemon's pills: the page's name (`setStageTitle`) and the page's tools (`setStageTools`);
`main.js` clears both before a page mounts, and a null among the nodes is dropped. `panels.pageHead(title, subtitle,
actions)` names the page in the bar and returns the description line with the actions (`paneHead(subtitle, actions)`
is the same line for a settings pane, which the bar already names "Settings"; the five settings panes use it). The
run page names itself with the face (28 px), Dot's name, the state badge and the start, and puts its chips, the panel
toggle, Continue and Cancel in the tools; the sticky translucent `.chat-head` and its styles are gone, the goal stays
as the page's hidden heading. The bar (`shell.css`) is solid `--bg` with a hairline under it, 48 px, `padding: 4px
16px`; on a phone the chips and the start hide (the Details card keeps them), the state badge stays and the brand hides when a page is named,
the opener's button alone leading to the sidebar. `settings.js` draws the nav without its own title and names the bar
"Settings" after the pane mounts; `.page-title` remains only for the sign-in card. The smoke tests follow the move
(`.stage-title .avatar`, `.stage-name`, `.stage-tools .chip`): `test_run_chrome` now asserts the head is inside the bar.

**What.** The stage bar becomes the top bar of every page: solid background, hairline under it, 48 px; left the
sidebar opener and the page's name (on the run page the face, the name, the state and the start), right the pills,
the daemon's dot and the stop. The run page's sticky head is removed; the chips it carried (model, effort, approvals,
campaign) go to the bar's right on wide windows and to the side panel on narrow ones. Settings shows "Settings" in
the bar and the tabs as its first row; the page title row under the tabs goes, the description stays as one line.
The phone header is the same bar. Nothing has a translucent backdrop any more.

### 79. Flat surfaces and plain words: cards, labels, meta, chips, empty states

**Status: done.** Merged as `ab5f33a` (PR #88, squash; MYP-219 Done).

**Design, as built (2026-10-11).** `--radius` is 12 px and `.card` is a surface with a hairline and no shadow; the
shadow tokens stay on what floats (menus, dialogs, the composer's focus, the sidebar's open row). `.section-label` is
sentence case at 14/20 in the label weight on `--text-2`; the table head, the slash menu's group names and a tool
body's heading drop their uppercase and tracking for 13 at the label weight. A row's facts and an answer's facts are
spans in `.meta-facts` (inline flex, wrapping, a 12 px gap), not one string joined with middle dots; the Cost card's
head line is four such spans (`costFacts`) and a step group's count stands beside its title without a dot; the sidebar's
section labels carry their count in a `.side-label-count` span at the text weight. `emptyPanel(title, text)` returns
one `.empty-line` (muted, 12 px of padding, 16 inside a card): the icon and the card are gone, the title and the
sentence read as one line. The campaign cards' numbers read as "3 sessions", in Inter, not as `sessions 3` in mono.
The owner's bubble sits on `--surface-2` in the text colour; the yellow is Dot's face alone (the Controls page says
so). The Models card's head reads "4 providers" and "1 model" as two spans; a page's description line has a flex
basis of 280 px, so on a phone the filters go under it instead of squeezing it to one word a line. The home's "Ended today" list is rows on the page, not a card. `test_activity` reads the labels as "Waiting
for you 1" and "Working 3".

**What.** Cards flat: hairline, radius 12, no shadow (shadows stay on menus, dialogs, the composer's focus); list
rows inside a card without a second border; section labels sentence case at 14/20 weight 500 (the uppercase class
goes); meta lines as spans with a 12 px gap and no middle dots; chips in Inter at 12/16 with a surface, numbers with
nouns on the campaign cards; empty states one sentence and the action in a row, no card and no icon; the user's
bubble on the raised surface; the home's "Ended today" list as rows on the page.

### 80. Forms and controls: one grammar for inputs, selects, switches, tables

**Status: done.** Merged as `132ce0d` (PR #89, squash; MYP-220 Done).

**Design, as built (2026-10-11).** `--radius-control` is 8 px, so every button, input, select, segmented control,
sidebar row and settings tab share one corner. `.btn` is 36 px tall (`min-height`, `padding: 0 16px`), `.btn.sm` 32
(`padding: 0 12px`, the small size), an icon-only button as wide as it is tall; the radii of both are the control
token. `.input` and `.select` are 36 px tall with `padding: 0 12px`, `.textarea` keeps `8px 12px` on the interface
line-height with a 36 px floor; all three carry the strong border as a hairline, the mid-grey `--control` on hover,
and focus is the page's ring: a 2 px accent outline at 1 px, no glow (the composer's textarea draws none, its frame
shows the focus; the sidebar's search box takes the same ring and frame). A select hides the native chevron
(`appearance: none`) and draws `static/icons/chevron.svg` at its right, a grey stroke that reads in both schemes;
`.select.sm` is 32 px for the composer's model menu and the reasoning menus of the models table. `.field-label` is
14/20 in the label weight on the text colour; `.hint` is 13/18 muted. A table cell is 40 px tall with its content on
the middle; the model name stays Geist Mono at 13 and the capability chips are the chips of item 79. The sidebar's
icon buttons are 36. The rule form and the schedule form already shared `.form-grid` (two columns, 16 px gap, `.wide`
for a full row); the Controls page's Inputs card gains a small select and a textarea so the grammar is seen whole.

**What.** Inputs and selects 36 px tall, radius 8, hairline, the same focus ring (2 px accent outline, offset 1);
selects with a drawn chevron, the native one hidden; textareas with the same frame; segmented controls and switches
on the 14/20 label; buttons 32 (small) and 36 (default) with 500 labels; the models table with 40 px rows, the model
name in Geist Mono at 13, the capability chips as in item 79, the reasoning select as the others; field labels at
14/20 500 and hints at 13/18 muted; the rule form and the schedule form on the same grid.

### 81. Dark, first paint and the tour: every page read in both schemes

**Status: done.** Merged as `04693d7` (PR #90, squash; MYP-221 Done).

**Design, as built (2026-10-11).** The dark tiers stood: the tour's dark shots were read page by page (home, run,
activity, every settings pane, the phone run) and the three surfaces (page `#1b1a18`, sunken sidebar `#151412`,
raised cards `#22211f`) with the text tiers on them read as they should; the contrast test already holds `--text-2`
at 4.5:1 and the muted tiers at 3:1 on every surface in both looks, so no token moved. The first paint:
`static/js/boot.js`, a classic script in the document's head, reads `dot.theme`, `dot.scheme` and `dot.density` from
the browser's storage and sets the page element's `data-theme`, `data-scheme` and `data-density` before the body is
parsed, accepting only the values theme.js and layout.js accept; main.js still applies the stored choices after it
loads, so the modules keep the defaults and the switches. `test_static_ui` expects the two scripts in order;
`test_tour` gains a second test that stores a dark scheme in a light browser, watches the page element from an init
script and holds that the scheme landed while the document was still loading and that the body's background is
dark. The tour of items 79 and 80 found and fixed, in those items, the phone's squeezed description line and the
dotted Cost and Models heads; this item rewrote README's visual-system paragraph as the current state in five parts
(colour, type, surfaces, controls, space, motion) with the first paint and the tour at its end.

**What.** The dark tiers of the Neutral look re-set on three surfaces (page, sunken sidebar, raised cards) with the
secondary text at or above 4.5:1 on each; the Paper look the same way; the theme applied before the first paint after
the sign-in redirect (verified by the tour: the first dark shot is dark); every one of the 36 shots of the tour read
and what the earlier items missed fixed here; README's visual-system section rewritten as the current state.

### 82. Delegated runs as rows: face, state, last line, time; the child's bar

**Status: done.** Merged as `6454345` (PR #91, squash; MYP-222 Done). The owner's reference (2026-10-11): Claude Code's
subagent list and page.

**Design, as built (2026-10-11).** `timeline.js` `childScope` draws a delegated run as `details.child` whose summary
is the row: `.child-face` (the 24 px face, `avatar("working")`, with the status ring at its corner, 12 px, the same
ring as before: turning, amber, green with a check, red, dashed), `.child-main` with `.child-title` (the goal, one
line) over `.child-last` (the `.meta-facts` spans, class `child-progress`: "starting", then "step 3" and the tool,
then the end state and the steps), `.child-time` (the time since the run was queued, repainted on every step and at
the end by `paintChild`), the `a.child-open` link to its page and the chevron; the mode and model stay in a hidden
span for a screen reader. The face rests or turns red at the end (`faceState`). The card: a hairline, the 12 px
radius, the surface; a `.child` that follows a `.child` loses the 8 px gap and its top corners and draws one
hairline between them (`:has(+ .child)`), so rows share one card. The sidebar's rows take `progressFacts(row)`
(`nesting.js`, the array that `progressWords` used to join with dots) as spans in `.meta-facts`. The run page
(`run.js`) of a delegated run puts `a.stage-crumb` (the parent's goal, loaded from `/runs/{parent}`, clipped to 56)
and a `.stage-sep` before its own goal in `.stage-name`; the name gives way with an ellipsis, the crumb first, and
on a phone the crumb hides. Under the conversation, where the dock would be, `.child-foot` is a pill of facts: the
model and effort, `Completed in 1m 46s` / `Failed after` / `Stopped after` / `Working for` (repainted with the
stats each second while the run works), "Part of a working run" or "Runs on its own" from the parent's `active`,
and "Open parent"; the "Delegated by another run" chip is gone from the tools. `test_activity` opens the delegated
run's page and holds the crumb's link and the pill's words, and photographs it.

**What.** A delegated run in the parent's chat and in the sidebar is one row: the actor's face with a state dot at
its corner (green ended, blue working, red failed), the goal as the title, the last line it said or did as a muted
second line, the elapsed time at the right, a chevron; the rows in one card with hairlines between them, no chips.
The child's page shows a breadcrumb in the top bar ("Parent's goal / Child's goal", the parent a link) and, under the
conversation, a pill with the model and effort, "Completed in 1m 46s" or "Working for 2m", "Runs on its own" when it
has no parent in flight, and "Open parent". The timeline's delegated card and the sidebar's delegated section use the
same row.

### 83. Faces for the actors: a hue each, alive in the rows and the bar

**Status: done.** Merged as `d760ea3` (PR #92, squash; MYP-223 Done). The owner's reference (2026-10-11): the character marks
of OpenAI's dots and Grok's companions.

**Design, as built (2026-10-11).** `static/js/actors.js`: `hashId` (FNV-1a over the UTF-16 code units, 32-bit),
`hueOf(id)` (the hash modulo 8) and `actorFace(id, state)`, the same `avatar` as Dot's with `data-hue` set; the eight
hues are tokens of `themes.css` (`--hue-0` … `--hue-7`, light-dark pairs light enough for the ink mark in both
schemes: coral, apricot, leaf, mint, sky, lavender, pink, sand) and `app.css` paints `.avatar[data-hue] .face` with
them; Dot's face keeps the bubble yellow. The face lives as it did (the breath, the pulse, the waiting ring, the
tick and the red), and a `.ended` face blinks its core once on the blink token before it breathes again (the
delegated row at its end). Where it shows: the delegated row of item 82 (`actorFace(runId)`), the bar of a delegated
run's page (`run.js`), the sidebar's activity rows and the Activity page's rows (24 px) by the row's new `actor_id`
(`ActivityRow.actor_id` in `views.py`, filled by `activity.py`: the campaign's id for a session, a delegated run's
own, a schedule's, `None` for Dot's own runs, which keep the dot and the kind icon), the Campaigns page's rows (28
px, working while active, at rest once achieved or stopped) and the Schedules page's rows (28 px, at rest). One SVG,
no raster. `tests/unit/test_actors.py` runs `hueOf` in node from the module and holds it to a Python mirror of the
hash for six ids, and holds that every hue has its token and its rule.

**What.** Dot's face stays the yellow dot. Every other actor gets the same mark in its own hue, chosen from a ring of
eight by a hash of its id: a delegated run, a campaign, a schedule. The face lives (item 73's motion): it breathes at
rest, pulses while it works, blinks its core once when it ends, shows the tick or the cross as Dot's does; the rows of
item 82, the sidebar's sections, the Campaigns and Schedules pages and the bar of a child's page all draw it at 24 or
28 px. One SVG, one hue token per actor, no raster; a unit test for the hash and the ring.

## Part 10: the side panel, motion, and agents that work together (planned 2026-10-11)

The owner's tenth word, on 2026-10-11, with a screenshot of the run page's side panel and one of Grok's companion
list: "the area on the right looks cheap and its texts hug the left; fix it; make things as animated as you can, the
sidebar opening and closing smoothly is one example; read arXiv 2609.20804, it may help; let us create custom agents
as in Grok, have them talk to each other and work together, and make the design like theirs, on desktop and web."

**Status (2026-10-11): built.** Items 84 to 88 merged the same day (`82c06b8`, `7f02492`, `bd12b54`, `23c6b5c`,
`c4d9b88`), each with its screenshot tour read; the tour now shoots the Agents, agent, Team and desktop-shell pages
too. What waits: the owner's own agents (none exist until made on the Agents page), and the daemon restarted on the
newest main once no run is active.

**What the paper gave (arXiv 2609.20804, "An Empirical Study of Harness Design for Coding Agents", Fan et al.,
17 Sep 2026; read by a research agent on 2026-10-11, medium trust: the HTML was read to 100k of 164k characters).**
One agent at a time, on SWE-Bench Verified and Terminal-Bench, 176 settings. Context management matters most under a
tight window (the gap between managed and none falls from 35.7 to 2.7 points from 32k to 128k; staged elision of
stale tool outputs, then a summary near the limit, was cheapest); a persistent plan re-injected each turn helped the
small model by 11.6 points and cut the large model's cost by 30%; a typed tool set helped the small model by 15
points while the large one did better with bash alone at half the cost; a recall store of elided outputs was almost
never used; stuck detection (a reminder after 5 identical calls, a stop after 8 failing ones) and guards at the tool
layer (read before write, path limits, allow/ask/deny) were fixed. It does not study agents that message each other.
What Dot takes from it (items 86 and 87): the plan and the context tiers per agent, a tool set per role, loop
detection on every delegated run, and the per-run telemetry (turns, calls, cost, summaries) in the chat.

**The shape of the agents (the decision of this part).** An agent is a profile Dot keeps: a name, a face (the mark in
its own hue, item 83), a role in one line, instructions, a model and effort, an approvals mode, the tools it may use.
Dot itself is the first agent and cannot be deleted. Every run belongs to one agent (Dot by default); the agent's
instructions join the system prompt, its model and tools bound the run, its face names the run in the bar, the rows
and the sidebar. An agent has its own chats, listed as Grok lists its companions: the face, the name, the last line,
the time. Agents work together through the delegation Dot already has: an agent can send a message to another agent
(a run of that agent, with the message, whose report comes back), and the team's thread shows who said what to whom.

### 84. The side panel: inset bodies, facts as spans, one card grammar

**Status: done.** Merged as `82c06b8` (PR #94, squash; MYP-224 Done).

**Design, as built (2026-10-11).** `.inspector .fold > .fold-body` is inset `12px 16px 16px` (the actions list,
whose rows draw their own padding edge to edge, is exempt by `:has(> .actions-list)`); `.plan-list` drops the
padding it carried for itself. The Details facts (`.facts`) sit on the small line with the label muted and the
value in the text ink, baseline-aligned, 8 px between rows; "Tool calls" and "Tokens" are `.meta-facts` spans
(`1`, `2 failed`; `2.0k in`, `100 out`) and the head's summary is two spans (`done`, `scripted`). The tour
(`test_tour`) now opens the panel on the run page and the Details card and photographs it in both schemes at both
widths (`*-run-panel.png`, 60 shots). The drawer on a phone is the same cards with the panel's head and its close.

**What.** The run page's side panel (Dot's computer, Details, Plan, Actions, Screen) on one card grammar: every
fold's body inset by the card's padding (16 px, 12 under the head) so no text hugs the edge; the Details facts as a
two-column list with the label in the muted ink and the value on the text, 8 px between rows, the run id in mono
with its copy button on the line; the head's summary as spans (`done`, `gemma4:cloud`), the tokens as spans; the
Screen card's hint as the one-line empty state of item 79; the drawer on a narrow window with the same cards; the
panel's scroll inside its own height.

### 85. Motion: the sidebar slides, the panel slides, folds fade, pages cross-fade

**Status: done.** Merged as `7f02492` (PR #95, MYP-225).

**Design, as built (2026-10-11).** The docked sidebar (`shell.css`): the shell's first column goes from `0px` to
`--sidebar-w` with `transition: grid-template-columns` on the base duration, the page following; the sidebar keeps
its own width inside the column (`min-width` on its children) and the column clips it on the way; closed, it is
`visibility: hidden` once the slide has ended (a 0 s transition delayed by the base duration) and inert as before,
its right hairline transparent. The side panel's drawer (`run.css`, `inspector.js`): the panel starts at
`translateX(100%)` and `data-open="true"` brings it in on the base duration; `showDrawer` unhides it, lays out its
first frame, then opens it; closing, it sets `data-open="false"` and hides it when the base duration has passed
(`motionMs("--motion-base")` in `dom.js` reads the token, so the script and the sheet keep one number); the scrim
fades the same way. A body that unfolds (`.fold-body`, `.steps-body`, a delegated run's `.timeline`) comes in from
`opacity: 0; translate: 0 -4px` through `@starting-style` on the quick duration. A page comes in with `page-in`
(a fade and a 4 px rise on the base duration) when the router mounts it; the old page leaves at once. Under reduced
motion the slides and the rises go (`transition: none`, `animation-name: fade-in`) and the fades stay. Every
duration is a motion token, which the existing unit test holds; the tour's shots are taken after the motion settles.

**What.** The docked sidebar opens and closes by sliding (the column's width and the sidebar's transform on the base
duration, the page following) instead of appearing; the side panel's drawer slides in and out; a fold's body fades
and rises 4 px when it opens; a page change cross-fades the stage (the old page out on the quick duration, the new
in on the base); the chevrons turn; all on the motion tokens of item 73, and under reduced motion the slides go and
the fades stay. A unit test holds every new transition to the tokens; the tour's shots stay still (they are taken
after the motion settles).

### 86. Agents: profiles with a face, a role, a model and their own chats

**Status: done.** Merged as `bd12b54` (PR #96, MYP-226).

**Design, as built (2026-10-11).** An agent is an `AgentRecord` (`store/models.py`): `name`, `role` (one line),
`instructions`, `model` (empty for the configured one), `effort` and `approvals` (`None` for the configured ones),
`tools` (the names it may use; empty for every tool); the `agents` table keeps them and `RunRecord.agent_id` names
the agent a run belongs to (`None` for Dot). Dot itself is `DOT_AGENT`, a synthetic record (`id` `dot`) that is
listed first and neither stored nor editable. The API (`routes_agents.py`): `GET /api/agents` (Dot first, then the
agents oldest first, each with its newest run), `POST /api/agents`, `GET`/`PUT`/`DELETE /api/agents/{id}` (a request
naming an unknown tool or a model that does not parse is refused with 422; so is a change or deletion of Dot), and
`GET /api/tools`, the names an agent may be given. Deleting an agent clears `agent_id` on its runs, which stay as
Dot's. `POST /api/runs` takes `agent_id` and goes through `RunManager.start_for_agent`: what the request leaves
out, the agent's bounds fill in (its model, effort and approvals), and what the agent leaves out, the configured
ones. In the loop, a run of an agent builds its prompt with an `AgentBrief` whose section opens the prompt ("You are
Scout, an agent the owner made: ... The owner's instructions to you, which you follow within the rules below: ...")
before Dot's own sections, and keeps its tool specs to the agent's list plus the core three (`finish`, `ask_user`,
`use_tools`; `AGENT_CORE_TOOLS`). The Activity rows and the sidebar wear the agent's face (`actor_id` is the agent's
id first, then the campaign's, then a delegated run's own). The interface: `views/agents.js` at `#/agents` (the
sidebar's foot, above Activity) lists Dot and the agents as one link row each (the face in the agent's hue, working
while its newest run works; the name; the newest chat's goal, else the role; when that chat started) with "New agent"
opening the editor card (name, role, instructions, model with its reasoning level when the model offers one,
approvals, the tools as checkbox chips, none picked meaning every tool); `#/agents/<id>` greets with the face, name
and role over a composer ("Message <name>", Enter sends, the run is a task with the agent's bounds) and lists the
agent's chats, with Edit and Delete in the bar. The home composer offers an Agent menu once an agent exists (Dot by
default; the placeholder follows the pick; Keep going hides while an agent is picked, a campaign being Dot's own
work) and posts `agent_id`. A run of an agent wears the agent's face and name in its bar, with a chip to its page.
Tests: `tests/unit/test_agents.py` (the prompt's opening, the core tools, the store's agents and their runs);
`tests/smoke/test_agents.py` (an agent made through the API, a run for it on the scripted model whose prompt opens in
its voice and whose tools are bound, the composer's menu, the Agents page, the agent's page and the run's bar read in
Chrome, the agent deleted and its chat left to Dot); the tour shoots `#/agents` and an agent's page.

**What.** A store table and API for agents (`id`, `name`, `role`, `instructions`, `model`, `effort`, `approvals`,
`tools`, `created_at`); Dot as the built-in first agent; `RunRecord.agent_id`; the agent's instructions in the system
prompt after Dot's own, its model and effort as the run's defaults, its tool set as the run's bound. An Agents page
in the sidebar (above Activity): the list as Grok lists companions (the face in its hue, the name, the role or the
last line of its latest chat, the time), "New agent" opening the editor (name, role, instructions, model, effort,
approvals, tools), each agent's page listing its chats and holding its composer ("Message <name>"). The home's
composer picks the agent (Dot by default). The bar, the rows and the sidebar name the agent by its face and name.

### 87. Agents together: messages between agents, the team's thread, the harness

**Status: done.** Merged as `23c6b5c` (PR #97, MYP-227).

**Design, as built (2026-10-11).** The `message_agent` tool (`tools/team.py`): `agent` (a name, read without regard
to case, or an id) and `message`; the run's spawner starts a run of that agent (`RunManager.delegate` with
`agent_id`: the source `team`, the parent's id, the agent's model, effort and approvals where it names them and
the parent's where it does not; the mode is the parent's as `delegate` hands it on) and waits; the reply comes back
as "Scout (run …) ended as done after N steps." and the run's result, fenced in `<agent_reply>` and marked untrusted
when the run read untrusted content, an error result when the run did not end done. An agent does not message
itself, and a delegated run (of any kind) messages no one (`_NOT_FOR_DELEGATED_RUNS`), so the thread stays one
level deep; a run with no other agent to message is not offered the tool. The prompt's `# Team` section lists the
other agents by name and role and says when to message one; it comes after the helpers and only with the tool. The
`run_queued` event carries `agent_id`, so the delegated row in the sender's chat wears the agent's face
(`timeline.js`). The thread is read from the runs (no new table): `Store.team_runs(since)` (the `team` source,
oldest first) and `Store.agent_messages(agent_id, limit)` (sent or received, through the sender run's agent);
`GET /api/team` gives today's messages as `TeamMessage` rows (the sender and receiver by id and name, Dot as
`dot`, the message, the reply once the run ended, the status, the runs behind it) and an agent's page carries its
newest messages. `views/team.js` at `#/team` (the Agents page's Team button, the agents tab) draws the thread as one
card: the two faces overlapping, "Dot → Scout" with the status, the message, the reply quoted under it, the time and
the two runs; the agent's page shows the same rows under its chats. The harness lessons of the paper are three
`[agent]` settings with defaults (`defaults.toml`): `context_elide_share` (0.6; past it older tool output is
shortened), `context_summarise_share` (0.85 of the configured budget; past it the older part of the run is summarised, where
before only the full budget did; a window that cuts the budget under that point summarises at its budget), and `plan_each_turn` (on; the open plan is appended to the system message of every step of a task
or research run, rebuilt from the newest plan event so nothing accumulates in the history and the prompt's own text
stays cacheable). Loop detection stays the watchdog of item 2 (`stall_limit`, `failure_streak`), and a tool set per
role is the agent's tool list of item 86. Tests: `tests/unit/test_team.py` (the Team section, the name lookup, the
reply's rendering, the defaults); `tests/smoke/test_team.py` (Dot's run messages Scout on the scripted model, Scout's
run answers in its voice without the tool, the reply reaches Dot's run, the Team page, the agent's page and the
delegated row read in Chrome); the tour shoots `#/team`.

**What.** A `message_agent` tool: a run sends a message to another agent, which runs with it (a delegated run of
that agent, its instructions and tools) and whose report returns as the tool's result; the parent's chat shows it as
the delegated row of item 82 with the agent's face; the agent's own page lists the exchange. A Team page: the thread
of every message between agents today (who, to whom, the line, the time) with the runs behind them. The harness
lessons of the paper as agent settings with defaults: the plan re-injected each turn (on), the context tiers (elide
stale tool outputs at 60% of the window, summarise at 85%), loop detection (a reminder after 5 identical calls, a
stop after 8 failing), and a tool set per role.

### 88. Desktop and web, one interface: the Tauri shell on the same pages

**Status: done.** Merged as `c4d9b88` (PR #98, MYP-228).

**Design, as built (2026-10-11).** The shell (`desktop/src-tauri/src/lib.rs`) opens its one window at the size the
tour reviews (1280 × 860, the minimum 390 × 600) with `TitleBarStyle::Overlay` and a hidden title: the system draws
only the traffic lights over the page, and the page is the web UI as the browser shows it, pages, sidebar and panel
alike (the shell navigates to the daemon's UI; nothing is rendered twice). The UI knows the shell by
`window.__TAURI__`: `boot.js` sets `data-shell="desktop"` on the page element at first paint, and `shell.css` keeps
room for the traffic lights (84 px on the left, a 52 px bar) in the sidebar's head, or in the top bar while the
sidebar is away (closed, or a drawer); both bars and the down page's body carry `data-tauri-drag-region`, so the
window drags by them, which a browser ignores; the capability (`capabilities/default.json`) grants
`core:window:allow-start-dragging` to the remote page, without which the drag region raises an ACL error. The tour shoots the home page with the shell's room, the sidebar open
and closed. The dependabot alert on `glib` 0.18 (`Cargo.lock`): glib is reached only through gtk and webkit2gtk,
Tauri's Linux backend, and the shell is built for macOS alone, where the crate is not compiled; Tauri 2.12 pins gtk
0.18, so `cargo update -p glib` locks nothing; the alert is dismissed as not used, with that reason, and this
paragraph is its record. `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` stay clean. The drag fix (the capability and the sidebar head's space) merged as `06cb620` (PR #100).

**What.** The desktop app (`desktop/`) shows the same pages as the web at the same size, the sidebar and the panel
behaving the same; whatever the shell does of its own (the window's title, the traffic lights' room, the menu) is
reviewed in the tour; the dependabot alert on `glib` in `desktop/src-tauri/Cargo.lock` is resolved.

## Part 11: the agent that learns: lessons, skills from its own work, memory that keeps what helps (planned 2026-10-11)

The owner's eleventh word, on 2026-10-11, after part 10: "does it have its own continuous learning, a strong memory
that drops what is not needed? If it learns and keeps skills it gets faster and better; it would not rely on them
alone, but it would be fast. It must be excellent, different from and freer than Grok's and OpenAI's agents."

**Status (2026-10-11): built.** Items 89 to 94 merged the same day (`a779b72`, `fa775a0`, `9222a5d`, `3074107`,
`6d64e43`, `937cca4`), each behind the gate, its smoke tests and its screenshot tour read. Learning is on by
default: lessons proposed after a run that stumbled, a skill distilled from a task that went well, every note scored
by how the runs handed it ended, recall within a token budget, the owner section the reflection keeps, and an
agent's lessons and skills its own. What waits: the evidence of real use (`dotx stats` Learning after a few weeks
of runs, the scores on the Memory page), and the daemon restarted on `937cca4` once no run is active. Before this
part: one note store with the owner's, the agent's and the reflection's notes; recall by words and meaning with a
lift by recent use; lessons (`[agent] lessons`, off) and playbooks per web host after a goal-mode run
(`[agent] playbooks`, off); a nightly reflection that merges, retires and journals; a note that rests after 90 days
unused; nothing learned measured against the runs it was handed to.

**The shape of the learning (the decisions of this part).** Learning is on, in the open: every lesson and every
skill is a note or a folder the owner can read, edit and drop; nothing changes the agent's rules. What is learned
is scored by what it did for the runs that were handed it, and the score, not the calendar alone, decides what is
recalled, what rests and what is retired. A skill is distilled from the agent's own successful work of any kind,
is offered as a skill is, is measured by the steps and seconds it saves on the next run of its kind, and is retired
by its failures; a run reads it as advice and keeps its own judgement. The owner's own ways are one standing
section the reflection keeps current and the owner corrects. An agent (part 10) learns for itself. Everything is
measured with `dotx stats` and the Memory page before it is widened.

### How to start part 11 (the handoff prompt)

> Read ROADMAP.md part 11 and the status table of part 9 (rows 89 to 94). One item at a time, in order; the
> protocol of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged.
> Never touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no
> active run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything
> written is English.

### 89. Learning on, and measured: every note handed to a run is scored by how the run ended

**Status: done.** Merged as `a779b72` (PR #103, MYP-229 Done).

**Design, as built (2026-10-11).** The store's `memory_uses` table (`run_id`, `memory_id`, `handed_at`, `how`,
`status`) records every note a run is handed: `Store.mark_memories_used(run_id, ids, when, how)` writes one row per
note besides counting the use (`how` is `start` for the notes the run begins with, `search` for a `memory_search`
hit, `live` for a note changed while the run worked; `MemoryUseHow` in `store/models.py`), and the run manager's
end of a run calls `close_memory_uses(run_id, status)`, which stamps the run's status on its rows (a row of a run
that still works has none). `memory_scores(ids)` folds the rows into a `MemoryScore` per note (`handed` runs,
`done`, `failed`; distinct runs, so a note handed twice to one run counts once). Playbook reads keep their own
record (`playbook.json` and the `skill` tool calls in the events). `[agent] lessons` defaults to `propose` and
`[agent] playbooks` to `true` (`defaults.toml`; evals still pin both off in `eval_config`). `dotx stats` ends with a
**Learning** section (`LearningStats` in `store/stats.py`, read with four more queries of the window's runs):
lessons proposed (the lessons of every `lessons_proposed` event), saved and dismissed; the runs handed a note and
the ten most handed notes with their done count; playbooks written and retired and the runs that read one; and
per host with runs of both kinds, the runs that read its playbook against the runs that did not, as act calls and
seconds a run (`playbook_comparisons`: a run's host is the site of most of its act calls; only finished runs). The
Memory page's `MemoryPage.scores` carries the score of every listed note some run was handed, and the row says
"Handed to 5 runs, 4 done" (the title holds the failed count). Tests: `tests/unit/test_run_statistics.py` (the
scores of the store, the Learning section from seeded events and uses, its rendering);
`tests/smoke/test_lessons.py` (the default is `propose`; the lesson a second run was handed carries that run's end
in its score).

**What.** A `memory_uses` table records every note handed to a run (the run, the note, when, how: at the start,
by `memory_search`, as a changed note) and, when the run ends, its status; the same for every playbook a run read.
`[agent] lessons` defaults to `propose`, `[agent] playbooks` to `true`; the owner's proposed lessons wait on the
run page as before. `dotx stats` gains a Learning section: lessons proposed, saved and dismissed; notes handed,
and of the runs handed each note how many ended done; playbooks written, read, retired, and the acting steps and
seconds of the runs that read one against the runs on the same host that did not. The Memory page shows a note's
score (handed to N runs, M done). Tests: unit for the table and the stats; smoke for a run that is handed a note
and ends, the use recorded with its status.

### 90. Memory that keeps what helps: the score decides what is recalled, rests and is retired

**Status: done.** Merged as `fa775a0` (PR #104, MYP-230 Done).

**Design, as built (2026-10-11).** `[memory] score_days` (30) and `score_min_uses` (3) in `MemoryConfig`.
`Store.memory_scores(ids, since)` takes the window (`0.0` for all time; the Memory page shows all time) and
`recent_scores(index, ids, now)` in `memory/service.py` reads the window's scores. `balance_lift(score, min_uses)`
in `memory/ranking.py` is the balance (done less failed) over `min_uses`, capped at one either way and nothing
under `min_uses` runs; `ranked_by_use` adds it to the use lift of an agent's note (both worth `_USE_LIFT`), so
`recall` (and with it `memory_search`) lifts a note that helped and lowers one that did not. `resting(record, now,
rest_days, score, min_uses)` rests a note whose score is negative over at least `min_uses` runs, besides the two
older reasons; `prompt_memories` and the Memory page route read the window's scores for it. The reflection's
`_retire` first rests the aged lessons (`to_retire`), then `to_retire_by_score(notes, scores, min_uses,
owner_saved)`: a lesson handed to `min_uses` runs lately of which more failed than ended done, unless the owner
saved it from a run's page (`Store.owner_saved_memory_ids`, the `lesson_saved` events by the owner); each
`lesson_retired` log line carries `reason` (`unused` or `score`) with the numbers, and the journal's retired count
includes both. Tests: `tests/unit/test_memory_ranking.py` (the balance lift and its cap, the rest by score);
`tests/unit/test_reflection.py` (`to_retire_by_score` with an owner-saved lesson kept); `tests/smoke/test_reflection.py`
(a lesson handed to three failed runs is retired by the nightly reflection beside the aged one).

**What.** Recall lifts an agent's note by its balance (done minus failed among the runs it was handed to, over the
last `[memory] score_days`), besides the use lift of item 71; a note with a negative balance over at least
`[memory] score_min_uses` runs rests at once, and the nightly reflection retires it (the `rest` tag, with a
`lesson_retired` line saying why: "handed to 5 runs, 1 done"). A lesson the owner saved keeps its score but is never
retired by it (the owner decides). The journal names what was retired for its score. Tests: unit for the balance,
the resting and the retirement; the reflection smoke extended.

### 91. Recall by budget: the notes a run starts with fit a token budget, best first

**Status: done.** Merged as `9222a5d` (PR #105, MYP-231 Done).

**Design, as built (2026-10-11).** `[memory] inject_tokens` (1500) and `inject_share` (0.02) replace `inject_top_k`,
`core_limit` and `user_limit`. `memory_budget(config, context_length)` in `memory/service.py` is the share of the
model's window capped at `inject_tokens`, the cap alone when `ModelInfo.context_length` is `None`; the loop
computes it from the run's model before it recalls. `note_tokens(record)` estimates a note's line (its text and
tags over `CHARS_PER_TOKEN`, which moved to `core/text.py` with `estimate_text_tokens`, plus twelve tokens for who
wrote it and when); `fit_notes(notes, budget, spent)` takes the notes in order while they fit, passing over one that
does not so that a shorter one after it may; `prompt_memories(index, query, budget)` reads up to 200 standing and
user notes and the recall candidates, orders them as before (standing newest first; the user's closest first; the
agent's by fit and score, resting ones out) and packs the three groups in that order, returning `Remembered`
(the records and the tokens spent). The `memories` event carries `tokens` and the timeline says "Recalled 3
memories, 420 tokens". Tests: `tests/unit/test_memory_ranking.py` (the budget from the window and the cap, the
packing and the pass-over); `tests/smoke/test_memory.py` (a budget with room for the standing note alone leaves the
rest to `memory_search` and reports what it spent).

**What.** `[memory] inject_tokens` replaces `inject_top_k`, `core_limit` and `user_limit` as the measure: the
standing notes first, then the owner's, then the agent's, each ranked by fit and score, taken until the budget
(estimated as the prompt is) is spent; a note that would not fit is left for `memory_search`. The budget scales
with the model's context window (`inject_share` of it, capped by `inject_tokens`). The run's `memories` event
carries the tokens spent. Tests: unit for the packing; the prompt-size assertions updated.

### 92. Skills from its own work: a task that went well becomes a skill for the next of its kind

**Status: done.** Merged as `3074107` (PR #106, MYP-232 Done).

**Design, as built (2026-10-11).** The playbook module (`agent/playbooks.py`, its name kept) writes learned skills
of any kind of work. The gate: `playbook_case` as before (playbooks on, a task run of its own, not an eval, ended
done or failed), then `distils(status, steps, min_steps)`: done, with at least `[agent] skill_min_steps` (4) acting
steps, where `acting_steps(events)` is every tool result but the planning, asking, remembering, consulting,
skill-reading and finishing tools (`_NOT_ACTING`); goal mode is no longer required, a done end being a checked
answer or an achieved goal. The review model's `PlaybookDraft` gains `kind` (3 to 60 characters, "as a later run
would name its own task"), its prompt speaks of work on a site, in an application, in the shell, on files or
through an API, and the request names where the run acted most (`main_site`, or `NO_SITE` = "this Mac"). The
skill's folder is `learned-<slug(kind)>` under `<home>/playbooks` (not `<home>/skills/learned` as planned: the
catalog reads that root as a skills directory of its own, source `learned`, and the folder of before is kept);
`same_kind(kind, entries, vectors)` finds the active skill of the kind, by cosine at least 0.85 between the kind's
embedding and each entry's (`runtime.memory.embedder`) or by slug equality without embeddings, and
`update_playbook` rewrites that skill from the new run (the steps the newer run's, the evidence every run with its
acting steps, the record kept: `PlaybookMeta` now holds `kind`, `runs`, `acting_steps`, `updated_at`). The SKILL.md
says it is advice from Dot's own work, to follow with the run's own parameters and judgement; the prompt's note on
`learned-` skills says the same; `used_playbooks` and the stats query accept both prefixes. The API: `GET
/api/skills/learned` (active first, then retired; `LearnedSkillView`) and `DELETE /api/skills/learned/{name}`;
Settings gains a Skills tab (`views/skills.js`: the rows with kind, place, runs, record; Retired apart; Drop with a
confirm). `dotx playbooks` keeps its name and speaks of learned skills. The playbook comparison of item 89 stays per
host. Tests: `tests/unit/test_playbooks.py` (every acting call in the digest, the gate, the update with its
evidence, the kind match by name and by vectors); `tests/smoke/test_learned_skills.py` (a scripted run of three
acting steps is distilled, the writer saw the steps and the place, the next run's prompt offers the skill as advice,
a short run is not distilled, the API lists and drops it).

**What.** Playbooks become skills of the agent's own work: after a task run of its own that ended done with the
goal check's "achieved" or a verified answer, in any mode, that took at least `[agent] skill_min_steps` acting
steps (shell, files, browser, desktop, an API), the review model distils how it got there into a skill folder
`learned-<kind>/SKILL.md` under `<home>/skills/learned`, the kind named by the model from the goal (one line, a
slug) and matched by meaning against the existing learned skills, so a second run of the same kind updates the
skill rather than writing another (the parameters, the steps that changed, the evidence appended). A learned skill
is offered like any skill, says it is learned and from which runs, and the run that uses it is told it is advice.
The measure of item 89 compares the runs of one kind that read the skill against the ones that did not (acting
steps, seconds, tokens); a skill read by two runs that then failed retires. The Skills page (Settings) lists the
learned skills with their evidence and a Drop. Tests: unit for the kind matching and the rendering; smoke for a
scripted run that is distilled, a second run of the kind offered the skill, and the retirement.

### 93. The owner, in one section: what the reflection knows about how you work

**Status: done.** Merged as `6d64e43` (PR #107, MYP-233 Done).

**Design, as built (2026-10-11).** The section is not a note (a note is capped at `[memory] max_chars`, 600) but
an `OwnerSection` in the store's `kv` (`memory/service.py`, key `owner.section`): `dot_lines`, which the reflection
rewrites, `owner_lines`, the owner's own, kept as they are, and `updated_at`; `owner_section(store)` and
`set_owner_section`. The nightly reflection, after it retired, when the day had runs: `_describe_owner` asks the
review model (`owner_writer_for`, purpose `owner`, tool `describe_owner`, `OwnerDraft` of at most twelve lines)
with the section as it stands, the owner's lines ("the truth, never to be contradicted or repeated"), up to fifty
of the owner's notes and up to thirty of the day's goals (secrets redacted); `owner_lines_within` flattens the
answer, caps it at twelve lines and `[memory] owner_chars` (1500) characters; the section is written when the lines
changed, and `Reflection.owner_updated` says so. The prompt's memory section opens with "What you know about the
user, kept by your nightly reflection; a line marked (you said) is the user's own and outranks the others" and the
lines (`owner_prompt_lines`: the owner's first, marked, then Dot's; `PromptInputs.owner`); the section stands even
when no note was recalled. The API: `GET /api/memories/owner` and `PUT /api/memories/owner` (`OwnerLinesRequest`,
up to twenty lines of 300 characters; Dot's lines stay). The Memory page opens with the card "What Dot knows about
you": Dot's lines as a list (or that nothing is known yet), "Your lines" as a textarea, one per line, with "Save your
lines". Tests: `tests/unit/test_reflection.py` (the cap and the budget of the lines, the prompt's order);
`tests/smoke/test_reflection.py` (the reflection writes the section from the day's goals; the API shows it and
keeps the owner's lines beside Dot's).

**What.** The nightly reflection keeps one standing note tagged `owner` (at most `[memory] owner_chars`): the
language, the hours, the tools and sites you use, the ways you asked for things, the limits you set, distilled
from the owner's notes and the runs of the period and rewritten each night from the previous version and the day;
it opens the memory section of every prompt ("What Dot knows about you"), and the Memory page shows it first with
Edit, so a wrong line is corrected by hand and the next reflection keeps the correction (the owner's edits are
kept as their own lines the reflection does not rewrite). Tests: unit for the merge of the owner's lines; the
reflection smoke extended.

### 94. An agent learns for itself: lessons, skills and a score per agent

**Status: done.** Merged as `937cca4` (PR #108, MYP-234 Done).

**Design, as built (2026-10-11).** A note carries `agent_id` (a column added to `memories`, `None` for Dot's; the
notes of an older database are Dot's): a lesson saved from a run, by `auto` or by the owner, is the run's agent's;
a note a run saves with `memory_save`, a journal note, a note the owner writes are Dot's. `visible_to(record,
agent_id)` (`memory/service.py`) says what a run may be handed: Dot's notes go to every run, an agent's to its own;
`recall_for(index, query, limit, agent_id)` searches among them (the `memory_search` tool, with the run's agent)
while `recall` stays the owner's view of every note (the Memory page, `dotx memory`, the lesson writer's check for
a note that says it already); `prompt_memories` takes the agent and hands the standing notes, the owner's, the
agent's own lessons and then Dot's, within the budget. Scores: `Store.agent_memory_scores(ids, since, agent_id)`
counts the agent's runs alone (a join on `runs`), and `recent_scores_for` ranks and rests a run's notes by the
scores of its own runs (Dot's by every run). Learned skills: `PlaybookMeta.agent_id` and `shared` (absent in the
records written before, which are Dot's), `offered_to(meta, agent_id)` and `offered_skills(skills, entries,
agent_id)`, applied in the loop before the prompt (a learned skill without a readable record is not offered); the
run that writes a skill marks it its agent's, and a skill of the same kind is updated only among the ones the run
is offered; `share_playbook(root, name, shared)` and `PUT /api/skills/learned/{name}`. A deleted agent leaves its
lessons (`Store.delete_agent`) and its skills (`release_playbooks`) to Dot. The API: `GET /api/agents/{id}` gains
`learned` (`AgentLearning`: the lessons, their scores by the agent's runs, the skills); `GET /api/memories` gains
`agents` (names by id) and the learned-skill views `agent_id` and `shared`. The agent's page shows "What it
learned": the lessons with Forget, the skills with Share and Drop; Settings › Skills names the agent on a skill and
offers Share; the Memory page marks a lesson "Scout's". Tests: `tests/unit/test_agent_learning.py` (visibility,
offering, sharing and release, the store's scoping and scores); `tests/smoke/test_agents.py` (an agent's lesson
reaches its run and not Dot's, the page lists it, the deleted agent leaves it to Dot).

**What.** A lesson, a learned skill and a memory use carry the agent (`agent_id`; `None` is Dot). An agent's run
is handed its own lessons first, then Dot's shared ones; its learned skills are offered to it alone unless the
owner marks a skill shared; its scores are its own. The agent's page shows what it learned (its lessons, its
skills, the score), with the same Drop. Tests: unit for the scoping; the agents smoke extended.


## Part 12: the frame and the hands: a smaller sidebar, Chrome in the background, the web that is refused, the team that grows (planned 2026-10-11)

The owner's twelfth word, on 2026-10-11, after part 11, with two screenshots of the sidebar and the composer: move
Agents, Activity and Campaigns up under New chat; let the chat sections fold; in Working, show the subagents' and
the chats' names cut after a few words, the card is too big; when the agent can drive Chrome in the background,
stop bringing Chrome to the front, as a choice to turn on and off, since the right panel already shows what the
agent does; make "Jump to latest" a down arrow right above the composer, shown while scrolled up; show the cost on
the right side or in another area; let a working agent make itself lasting teammate agents, know of them, and do
everything together with them; review every feature built so far together with the new ones; find a solution to
the 999 and 403 errors; and make the dark borders that appear on focus either go or take a light colour of the
theme.

**Status (2026-10-11): built.** Items 95 to 100 merged the same day (`f3dce6e`, `3a8a20b`, `558621b`, `65433d0`,
`724768e`, `62585c2`), each behind the gate, its smoke tests and its screenshot tour read. The sidebar is short
and folds; the run page's arrow sits above the composer and the cost in the panel; Dot's Chrome stays in the
background unless the owner switches it to the front; a refused page meets the browser in both modes, a refusing
host is remembered for the run, and the plain request looks like a browser's; a run makes a lasting teammate and
hears of the ones that join; the review fixed the composer's dark focus frame, the dark scheme's invisible switch
knob and three stale lines, and left five small polish items for the next part. What waits: the daemon restarted
on `62585c2` once no run is active, and the owner's eye on the sidebar and the quiet Chrome in daily use. Before
this part: one sidebar function (`sidebar.js`) with "New chat", the search, the Waiting and Working sections (a
working run with its delegated runs nested, titles cut at 120 characters but wrapped), the chats by day, and a foot
with Agents, Activity, Campaigns and Settings; no section folds. The run page's "Jump to latest" was a fixed button
at the bottom right; the cost card (item 58) stood in the conversation column; the side panel (item 84) held Dot's
computer, Details, Plan, Actions and Screen. Chrome was raised by `switch_tab` and by Take over, and by its own
launch. `web_fetch` retried 401, 403, 429 and 503 in Dot's own Chrome and nothing in the owner's attached Chrome;
999 (LinkedIn's refusal) was a plain failure; crawl skipped every refused page. The team was read once at a run's
start, and only the owner made agents. Focus was a two-pixel accent outline on every control, and the composer's
inner textarea showed it as a dark frame.

**The shape of this part (the decisions).** The frame gets out of the way: the sidebar is a list of short names,
folded by the owner's choice and remembered; focus is visible to the keyboard and quiet to the mouse. Chrome
belongs to the agent, not the screen: with the live view in the panel, the window is raised only when the owner
takes over. A refused page is a step, not an end: the tools climb from the plain request to the browser, remember
which host refuses in the run, and tell the model the next rung in one line. The team grows from the work: a run
may make a lasting agent for a kind of work, under the owner's approvals, and every run learns of the new
teammates while it works. And the whole is read once more, page by page, before the next part.

### How to start part 12 (the handoff prompt)

> Read ROADMAP.md part 12 and the status table of part 9 (rows 95 to 100). One item at a time, in order; the
> protocol of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged.
> Never touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no
> active run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything
> written is English. Every UI item ends with the screenshot tour read by eye.

### 95. The sidebar, smaller: the nav under New chat, sections that fold, rows cut short

**Status: done.** Merged as `f3dce6e` (PR #111, MYP-235 Done).

**Design, as built (2026-10-11).** `sidebar.js`: a `nav.side-links` with Agents, Activity and Campaigns sits under
the search; the foot keeps Settings. Each section's label is a button (`foldLabel`, `.side-fold`, a chevron that
turns) that folds the section: Waiting and Working drop their rows, Chats hides its list (not while chats are being
chosen for deletion); the folds live in the prefs as `dot.sideFolds` (JSON, one key per section). A row of the
activity sections shows `clipWords(title, 7, 56)` (`dom.js`: the first seven words, at most 56 characters, with an
ellipsis) on one line, and its facts joined with " · " on one line under it, both cut with an ellipsis; the rows
take four pixels of vertical padding whatever the density, the faces in them are 20 px, and the delegated rows sit
12 px in. Focus: `--ring` (themes.css: the accent at 38 %) replaces the accent in every outline (`:focus-visible`,
the fields, the search, the picked chats, the drop zone excepted); the dock's textarea draws no outline of its own,
since the dock's frame shows the focus. Tests: `tests/unit/test_css_tokens.py` (the detail line uses the leading
token), `tests/smoke/test_activity.py` unchanged, the tour's home and the activity test's sidebar shots read.

**What.** Agents, Activity and Campaigns move up under New chat and the search, as links of the same row grammar;
Settings stays in the foot. Waiting, Working and Chats each fold on a click of their label (a chevron turns), and
the fold is remembered (`dot.sideFolds` in the prefs). A working row shows its title cut after a few words
(`clipWords`), on one line, with its channel and its step as one short line under it; a delegated run under it the
same, smaller; the rows take the compact padding whatever the density. Focus: the keyboard gets a soft ring (the
accent at low opacity, one pixel of border), the mouse none (`:focus:not(:focus-visible)`), and the composer's inner
textarea never draws a frame of its own. Tests: `tests/smoke/test_activity.py` keeps its selectors; the tour's
sidebar shots are read by eye.

### 96. The run page: the arrow above the composer, the cost in the side panel

**Status: done.** Merged as `3a8a20b` (PR #112, MYP-236 Done).

**Design, as built (2026-10-11).** `run.js`: the jump button is a round icon button (`.btn.icon-only.jump`, 36 px,
the surface with a raised shadow) prepended to the dock, positioned by it (absolute, centred, its bottom 8 px above
the dock's frame), shown by the same rule as before (the answer below the window, or a live run not followed) and
with the words "Jump to latest" / "Jump to the answer" as its title and label; a delegated run's dock, which has no
composer, keeps only the arrow (`.dock-bare`). The cost card (`cost.js`, unchanged)
leaves the conversation column for the side panel's content, after Details and before Plan; the conversation
begins with the earlier turns. Tests: `tests/smoke/test_activity.py` turns the panel on (`dot.inspector`) and opens
the drawer at the phone width before it reads the Cost card; the run page shots read by eye.

**What.** "Jump to latest" becomes a round button with a down arrow, centred just above the composer (inside the
dock, absolute), shown while the page is not at its end or an answer waits below the fold, with the words as its
tooltip. The cost card leaves the conversation column for the side panel, after Details, and the conversation
starts at the first turn; `tests/smoke/test_activity.py` follows it there.

### 97. Chrome in the background: the live view shows the page, the window stays put

**Status: done.** Merged as `558621b` (PR #113, MYP-237 Done).

**Design, as built (2026-10-11).** `BrowserConfig.raise_window` (`defaults.toml` `[browser] raise_window = false`,
with the comment that the run's page shows the page either way). `browser/window.py`: `focused_app()` (the frontmost
pid through the accessibility API, matched against `list_apps()`; `None` when the desktop cannot be asked, logged as
`focused_app_unknown`) and `give_focus_back(app)` (waits up to 2 s for Chrome to take the front, then `activate(app,
2.0)`; a Chrome that never took it leaves nothing to do; a desktop that refuses is logged as `focus_not_returned`, the
run goes on). `BrowserSession._launch_own` records the focused app before the launch when the config is headed and
`raise_window` is off, logs `raise_window` in `browser_started` and gives the focus back after the context is up, both
in `asyncio.to_thread`. `switch_tab` calls `bring_to_front` only when `self._config.raise_window`; `focus()` (Take over)
still calls it. `set_raise_window(bool)` changes the live config with `model_copy` (`browser_raise_window` log), no
restart. `config_edit`: `with_browser_setting(text, key, value)` / `set_browser_setting(path, key, value)` for the
`[browser]` keys `mode` and `raise_window` (a boolean written as `true`/`false`, a quoted-or-boolean value regex per key,
the same parse-again proof), `with_browser_mode` / `set_browser_mode` kept as thin wrappers. `POST /api/browser/raise`
(`BrowserRaiseRequest(raise_window)`) sets the session and writes the file; `BrowserView.raise_window`. Settings ›
Browser: `switchSetting` "Bring Chrome to the front while working" under the mode row, painted from the view. README:
Dot's computer, the Browser use list and the `own` notes. Tests: `test_config_edit` (raise_window added, replaced with
its comment kept, the mode untouched; a non-boolean value in the file refused), `test_server` (the route reports,
switches live, writes the file, refuses a non-boolean).

**Checked by hand (2026-10-11, the owner's Mac).** A script with a temporary home started Dot's own headed Chrome
against a local page twice. With `raise_window = false`: the app that had the focus (Dot's desktop shell) was frontmost
again 1.5 s after the launch (`focus_returned`, the whole open took 3.1 s), and `switch_tab` left another app in front.
With `raise_window = true`: Chrome was frontmost after the launch and after `switch_tab`. A headed launch is not
possible in CI, so this stays a hand check.

**What.** `[browser] raise_window` (false by default): with it off, `switch_tab` no longer raises the window, the
app that had the focus before Dot's Chrome launched gets it back right after the launch, and only Take over raises
the window. Settings › Browser gets the switch "Bring Chrome to the front while working" (`POST /api/browser/raise`,
`config_edit` like the mode), with the words that the panel's live view shows the page either way. README: Dot's
computer and Browser use. A smoke test with a headed launch is not possible in CI; the behaviour is checked by hand
on the owner's Mac and recorded here.

### 98. The web that is refused: 999, 403 and their kind meet the browser, not the end

**Status: done.** Merged as `65433d0` (PR #114, MYP-238 Done).

**Design, as built (2026-10-11).** `web/types.py` `REFUSED_STATUS = {401, 403, 429, 503, 999}`, the one set
`tools/web.py` (`_UNREADABLE_STATUS`) and `web/crawl.py` read; `session._CHALLENGE_STATUS` gains 999 so a wall that
reloads itself gets its seconds. `web/refused.py`: `host_of(url)` (lower case, the port kept) and `RefusedHosts`
(`note`, `remembers`, `len`), one per run as `ToolContext.refused_hosts` (a `default_factory`, like `spawner`'s
default); `web_fetch` notes the host of every refused plain request and, when the host is remembered and the run may
read quietly, goes to the browser at once ("the host turned an earlier plain request away"). `web/client.py`
`request_headers(method, user_agent, extra)`: a GET carries `Sec-Fetch-Dest: document`, `Sec-Fetch-Mode: navigate`,
`Sec-Fetch-Site: none`, `Sec-Fetch-User: ?1` and `Upgrade-Insecure-Requests: 1`; a POST (the APIs) does not; the
caller's headers come last. `BrowserSession.quiet_reads` is true in both modes: `read_page` opens its scratch tab with
`_scratch_page`, a plain `new_page` in Dot's own Chrome and, in the user's, a target created through a browser-level
CDP session with `Target.createTarget {background: true}` (Playwright's `new_page` would open in the foreground),
awaited through `context.expect_page`; `human_like` (the search rung) stays Dot's own visible Chrome. The crawl takes
a second renderer, `quiet` (`_quiet_renderer_for`: `read_page` then `html_to_text`, a non-2xx in the browser raised as
`BrowserError`): a refused page becomes a `CrawledPage` with `rendered=True`, no links and the problem "the plain
request got HTTP 403, read in the browser", or a `SkippedPage` with both reasons. A browser that cannot open the page at all (`BrowserError`, the user's Chrome not running) is not the end either:
`web_fetch` falls back to the plain answer, with the line "Dot's browser could not read it either: …" before the next
rung (`render_http_failure`'s `browser_problem`), and a remembered host whose browser read fails gets the plain
request after all. `render_browser_failure` and the
refused line of `render_http_failure` end with `_NEXT_RUNG` (another source; `web_search` with the address as the
query; `browser_open` and Take over for a login wall). The `web_fetch` description says the tab is one the model never
sees. README: Browsing and search, Crawling. Tests: `test_refused_web` (the host memory, the headers), `test_web_fetch`
(`/wall-999`, `/open`; the memory sends the second fetch of the host to the browser first, a fresh context does not),
`test_crawl` (a walled corner: the 403 page read in the browser, the page refused twice left out with both reasons),
`test_browser_attach` (a quiet read in the user's Chrome leaves the user's tab in front and no tab behind).

**What.** 999 joins the statuses `web_fetch` retries in the browser; a refused page in a crawl is read in the
browser too when the run may read quietly; the owner's attached Chrome reads quietly in a background tab (opened
behind the owner's tab, closed after), so attach mode has the fallback as well. A host that refused the plain
request is remembered for the run, and the next fetch of that host goes to the browser first. The plain request
sends the headers a browser sends (`Sec-Fetch-*`, `Upgrade-Insecure-Requests`) with the same user agent. When the
browser is refused too, the tool's answer names the next rung in one line: another source, the hosted search's
snippet of the page (`web_search` with the URL), or Take over for a login wall. Tests: the fixture site gains a
`/wall-999`; unit tests for the host memory and the headers; the smoke `test_web_fetch` reads the refused pages.

### 99. The team that grows: a run makes a lasting teammate and knows of it

**Status: done.** Merged as `724768e` (PR #115, MYP-239 Done).

**Design, as built (2026-10-11).** `AgentRecord.made_by: str | None` (the run that made the agent, `None` for the
owner; the `agents` table gains the column through `_ADDED_AGENT_COLUMNS`, NULL for every earlier agent), and
`Store.create_agent(..., made_by)`; the Agents route passes `None`. `tools/team.py` `create_agent`
(`CreateAgentArgs`: name, role, instructions, `tools` empty for every tool, `model` empty for the configured one, the
bounds of the Agents page's `AgentRequest`; kind `meta`, risk `low`, the `persistence` flag like `memory_save`, so
`ask` asks once the run has read untrusted content and `yolo` makes it): the model reference is parsed, the tools
must exist in the registry, a name already taken (by name or id, case apart) is refused with the agent named, effort
and approvals stay the configured ones, `made_by` is the run; the result (`render_made`) names the agent, its role
and tools, says it lasts under Agents and how to message it. `build_team_tools` registers both tools; `create_agent` is the one tool of the `team` group ("making a lasting
teammate agent for a kind of work that recurs"), off until a grouped run turns it on with `use_tools`, since its
definition would push the core group past a third of the tool definitions (`test_tool_groups`).
`prompt._team_section`: nothing without `message_agent`; with no team and `create_agent`, "The owner has no agents
yet … make one with create_agent … Never make one for a single step"; with a team and `create_agent`, the team list
and a line that a new teammate may be made for work none of them covers. `loop.py`: `create_agent` joins
`_NOT_FOR_DELEGATED_RUNS` (an answering run is a delegated run); `message_agent` is removed without a team only when
`create_agent` is not among the run's tools (the check moved after the agent's own tool bounds); `known_agents` holds
the team's ids at the start and, each step after the memories check, a run that may message lists the agents and
appends `_new_teammates_note` (names and roles, "made by you, by the owner or by another run") for the ones it did not
know, as a stored note like the memory note. `agents.js`: the row gets "Made by Dot" (`.agent-made`) and the agent's
page "Made by Dot in one of its runs" under the role. README: Agents. Tests: `test_team` (the Team section with and
without a team and the tool), `test_tool_groups` (the `team` group), `test_agents` smoke (a scripted run makes Pilot, the next
step's request carries the note and offers `message_agent`, the record keeps `made_by`, the Agents list shows the
mark).

**What.** A `create_agent` tool (name, role, instructions, tools, optional model) makes an agent as the Agents page
does, under the owner's approvals (kind `meta`, the `persistence` flag: `ask` asks once, `yolo` makes it), and the
run's answer names it. `message_agent` stays offered whenever `create_agent` is, so a run that makes a teammate can
message it at once; the prompt's Team section says, with no teammate, that the run may make one for a kind of work
that deserves its own. Every step, the loop compares the agents with the ones the run started with and appends a
note of the new teammates (name and role) the way changed notes are appended. Delegated and answering runs make
no agents. The record keeps `made_by` (the run that made the agent, `None` for the owner) and the Agents page says
"Made by Dot". README: Agents.

### 100. The review: every feature of parts 1 to 12 read once more on the live pages

**Status: done.** Merged as `62585c2` (PR #116, MYP-240 Done).

**Findings, as built (2026-10-11).** Three readings. (1) The tour: 84 shots (21 pages, two schemes, two widths) read
by four readers against the README and the as-built sections of items 92 to 99, their findings checked by eye and,
for the colours, measured on the live UI with Playwright (computed styles, both schemes). (2) The offline smoke suite
in full (`-m "not live and not internet and not hosted and not desktop"`, the tour apart): 312 passed, 1 skipped, 3
failed, the three being `test_tool_groups`' expectations of the groups that are off, which item 99's `team` group had
changed; fixed on item 99's branch before its merge. (3) The owner's daemon through the API, read-only: healthy,
no run active, 2 agents (Dot and one the owner made), 23 memories, 3 schedules, 2 campaigns, 27 runs of the day,
1 team message; it runs `269eab5` (Part 11), so items 95 to 99 reach it with the restart that closes this part.

Fixed here. The focused composer wore a 1 px accent border with a 3 px ring, which in the Neutral look (its accent is
ink) read as the heavy dark frame the owner asked to be gone in item 95; `.composer:focus-within` and
`.dock-inner:focus-within` now keep `--border-strong` with a 10 % ring. The switch's knob on a switched-on switch was
`--knob` (near white) on the accent track, which in the dark scheme is near white too (measured: track
rgb(230,228,222), knob rgb(245,244,240)); the knob now takes `--on-accent`. The Settings › System head said "its safety
switch" for the control the card calls "Emergency stop". The Agents head promised "a model" the rows do not show. The
README's list of Settings tabs lacked Skills (item 92). The tour set the shell's sidebar closed for the desktop-shell
shot and never opened it, so every later desk shot lacked the sidebar and the readers took that for a defect; the
tour restores it. Checked and not a defect: the emergency stop in the dark scheme (measured rgb(42,11,11) on
rgb(255,143,136), well above 4.5:1, the reader had taken the text for white); the schedules page's "scheduler is not
running" line beside a green daemon dot (the tour's server has no scheduler; the owner's daemon reports it running);
`#/controls` (the design-token gallery, a development page that stays); the Team page's empty copy naming
`message_agent` (the README names tools to the owner throughout).

Left for the next part, each small: the Models tab's table on a phone scrolls sideways without a cue (the Reasoning
and Context columns sit off-screen); the run page's composer placeholder is cut under the send button on a phone;
the Activity page's empty sections say what is empty but not what to do; the Browser tab's status grid wraps to 3 + 1
cells with a stray divider; the tour shoots Skills, Memory and the run page without content enough to show the
learned-skill rows, the lesson scores or the jump arrow, and the Dot's computer card on an ended run still offers
Watch. The desktop shell (Tauri) was not shot: it wraps the same pages, and the tour emulates only its sidebar inset.

**What.** Not code first: the tour of every page in both schemes and both widths is read by eye against the
README, page by page (home, run, activity, agents, agent, team, campaigns, schedules, settings' eight tabs, the
desktop shell), the offline smoke suite runs in full, and the owner's own daemon is read through the API only.
What is broken, stale or contradicts the README is fixed in this item's PR or, when it is more than a day's work,
written as the next part's first items. The findings go in this section as built.

## Part 13: the face and the pen: a character for every agent, alive beside the chat, and the composer remade (planned 2026-10-11)

The owner's thirteenth word, on 2026-10-11, after part 12, with three screenshots of bloub (bloub.vercel.app,
github.com/jeremy-prt/bloub: its customiser of shape, expression and colour, its timeline of animations, its export
menu) and one of the home composer: add animated cartoon characters for the agents, like Grok's bot; let them move
at the right of the chat while the agent works, and in the chat; let the owner customise them; build such a system
in Dot from that site, which is free; and design the composer again, more useful and more luxurious.

**Status (2026-10-11): built.** Items 101 to 106 merged the same day (`8c3a590`, `f6afe5d`, `0a0fdaa`, `9261fa9`,
`c14b670`, `8e3818d`), each behind the gate, its smoke tests and its screenshot tour read. bloub lives in Dot as
plain modules with its notice; every agent has a look chosen in its editor and worn in every row, Dot its own in
Settings; the run page's character works, waits, laughs, sulks and sleeps with the run, the home's face moves and
listens to the composer; the three composers are one frame with pills, panels and drafts that keep; the five polish
items of part 12 are done; the review found the tour and the pages sound, fixed two stale tests, and measured one
moving face at about a millisecond of main-thread time a frame. What waits: the daemon restarted on `8e3818d` once
no run is active, and the owner's eye on the faces and the composer in daily use.

**Status (2026-10-11): planned.** What is built today: an agent's face is a 40 × 40 SVG glyph (`icons.js avatar`:
a ring, a core and a tick) in one of eight hues its id gives it (`actors.js`, FNV-1a), drawn at 20 to 40 px in the
sidebar, the activity rows, the timeline, the home greeting, the Agents and Team pages and the stage bar, with five
states (idle, working, waiting, done, failed) carried by CSS (`app.css`: the breath, the pulse, the amber blink, the
tick, the red; `--motion-breath` 4 s) and a favicon from the same face (`face.js faceDataUrl`). An agent record has
a name, a role, instructions, a model, an effort, approvals, tools and `made_by`; nothing of how it looks. Dot itself
is `DOT_AGENT`, never stored. The composer is two frames with no code in common but the wiring (`composer.js`: the
autosize, Enter and Shift+Enter, the slash menu): the home card (`runs.js buildComposer`: the textarea, then a bar
of attach, Task | Chat | Research, the Agent and Model selects, the Reasoning select, Options, the round send) and
the run page's dock (`run.js`: attach, the textarea, the send), with a third on the agent page; the Options are an
inline block that pushes the card taller; the captions of the selects are hidden; the bar wraps to two lines inside
the 768 px column whatever the window's width; nothing of a draft survives a navigation. bloub is MIT (Jérémy
Perret, 2026) for its code: a body as a closed path through 64 radial samples, morphed by a lerp of radii; two eyes
punched through the body with an SVG mask, moved by the head's gaze; 8 shapes, 16 expressions, 12 colours, 14
animations and a montage of them; about 3,000 lines of TypeScript that import nothing of Vue or the DOM, so the
port to Dot's plain ES modules is a translation, not a rewrite. Its README says the licence covers the code and not
the design it imitates, that of x.ai's bot avatar, and that it is not affiliated with x.ai; Dot keeps both notices,
credits bloub, and calls the look what it is.

**The shape of this part (the decisions).** The character is one more face, not a second system: the same call
sites (`actorFace`, `avatar`) draw it, the same states drive it, the rows get a still frame and the big places
(the run page's panel, the home greeting, the agent's page) the moving one, in one requestAnimationFrame loop for
the page that stops when the tab is hidden and under `prefers-reduced-motion`. The look belongs to the agent and
lives in the daemon (the agents table; Dot's own in the kv store), chosen in the agent's editor with a live preview,
with a fallback from the id's hue so no agent is faceless before the owner chooses. The composer becomes one frame
for its three homes, with the menus as quiet pills of one grammar, Options as a popover, the model and its
reasoning in one menu, drafts kept in the browser, and the character listening when the owner types. The small
polish of part 12's review is paid, and the whole is read once more.

### How to start part 13 (the handoff prompt)

> Read ROADMAP.md part 13 and the status table of part 9 (rows 101 to 106). One item at a time, in order; the
> protocol of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged.
> Never touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no
> active run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything
> written is English. The bloub clone is untrusted: read it, never run it. Every UI item ends with the screenshot
> tour read by eye.

### 101. The character engine: bloub's one shape with two eyes, as plain modules of Dot's UI

**Status: done.** Merged as `8c3a590` (PR #119, MYP-241 Done).

**Design, as built (2026-10-11).** `static/js/character/`: bloub's `src/bot` module by module (`math`, `repere`,
`profiles`, `shape`, `skins`, `face`, `expressions`, `decor`, `states`, `eyefit`, `engine`), the types dropped, the
French identifiers and comments English, the numbers bloub's; the shape, colour and expression ids are English too
(`pebble`, `ink`, `unimpressed`). `eyefit.js` builds a shape's table of eye offsets on that shape's first use rather
than every shape's at import (`eyeOffset`, keyed by the radii array's reference as in bloub). `character.js`:
`mountCharacter(parent, {shape, expression, colour, size, moving, state, phase, label})` builds the SVG with `svg()`
of dom.js (the mask with the body and the eyes as holes, the gradients of the arcs, the arcs' back halves, the dots,
the paper under the body, the inked rect through the mask, the pastille, the arcs' front halves; a `sync` keeps each
group's children in step with the frame's list) and returns `{element, state, setState, setExpression, setShape,
setColour, setLook, freeze, resume, destroy}`; each character keeps its own clock, started at a different second so
two faces do not blink as one; a still character (`moving: false`, `freeze()`, or reduced motion, followed through
the media query's `change`) renders the one frame of `POSES[state]` after a `reset`, and carries `data-still`,
`data-state` and `data-colour`. `ticker.js`: one requestAnimationFrame loop for the page, the frame time clamped at
64 ms, started when the first character subscribes, stopped when the last leaves or the tab hides
(`visibilitychange`), resumed when it shows. The body fills with `currentColor` and the wrapper's `data-colour`
picks `--character-<id>` (themes.css: bloub's twelve, ink and cream per scheme so a body never matches the ground,
`--character-notify` the pastille's blue); the eyes show `--bg`; a particle's depth is its opacity. `#/controls`
gets a Characters card: the eight shapes, the sixteen expressions and the twelve colours still, the fourteen
animations moving (`controls.js characters()`, destroyed on unmount). `static/js/character/LICENSE` carries bloub's
MIT notice with its non-affiliation note; the README's design system credits bloub. Tests:
`tests/smoke/test_character.py` (the gallery's counts, the eyes as two mask paths at rest and none in the thinking
dots, a gradient per ring, a moving path against a still one, the ticker's state, a mounted character through
`setState` to thinking (the eyes fade) and notify (the pastille), `freeze`, `destroy`; reduced motion leaves every
character still and the loop off; a hidden tab stops the loop and a shown one resumes it); the gallery shot in both
schemes read by eye.

**What.** `static/js/character/`: bloub's `src/bot` translated module by module to plain ES modules (math, repere,
profiles, shape, skins, face, decor, expressions, states, eyefit, engine), the eye-fit table built once on first use
rather than at import, and `character.js` on top: `mountCharacter(parent, {shape, expression, colour, size,
moving})` builds the SVG with DOM calls (no `innerHTML`, `test_static_ui` forbids it), gives each instance its own
mask id, and returns `{setState, setExpression, setShape, setColour, setLook, freeze, destroy}`; `moving: false`
draws one still frame and never joins the loop. One ticker for the page (`ticker.js`) drives every moving character
from a single requestAnimationFrame, the frame time clamped at 64 ms, stopped while `document.hidden` and under
`prefers-reduced-motion` (a still frame of the state's rest then). The 12 colours are tokens in `themes.css`
(`--character-<name>`, tuned per scheme) and the body fills with `currentColor`, so the dark scheme can soften them.
The development gallery `#/controls` gets a Characters section: every shape, every expression and every animation,
moving, as the place to read them by eye. `static/js/character/LICENSE` carries bloub's MIT notice; the README's
credits name bloub, its author and its note that the look recreates x.ai's bot avatar and is affiliated with
nobody. Tests: `tests/smoke/test_character.py` mounts the gallery, reads a body path that changes between two
frames, a state change, the still frame under reduced motion and the stopped loop in a hidden tab;
`tests/unit/test_static_ui.py` and `test_css_tokens.py` unchanged.

### 102. The look of every agent: shape, expression and colour, chosen in the editor and worn in every row

**Status: done.** Merged as `f6afe5d` (PR #120, MYP-242 Done).

**Design, as built (2026-10-11).** `AgentLook` (`store/models.py`: `shape`, `expression`, `colour`, each a literal of
the character's catalogue) on `AgentRecord.look`, `None` for the fallback; three nullable columns (`shape`,
`expression`, `colour`) through `_ADDED_AGENT_COLUMNS`, read back as one look when all three are set;
`Store.create_agent` and `update_agent` take the look, `set_agent_look` the look alone, and Dot's own lives in the kv
store under `look:dot` (`dot_look`, `set_dot_look`). `AgentRequest.look` (`None` when absent, so an older client and
`create_agent` of item 99 keep working); `PUT /api/agents/{id}/look` with an `AgentLook` body, Dot included; `GET
/api/agents` and `/api/agents/dot` serve `DOT_AGENT` wearing the kv look (`dot_record`). `actors.js`: the registry of
looks (`rememberLook`, `loadLooks` from `GET /api/agents`, called by `main.js` once the daemon answers; the Agents
page and the editor keep it current), `fallbackLook(id)` (Dot: the circle in amber, the colour of its old badge;
any other actor: `hueLook(hueOf(id))` of `hues.js`, the hash and the ring of eight moved there, pure, with the
shape and the colour of each hue among the character's twelve; the `--hue-*` tokens are gone), `FACE_OF_STATE` (idle: the
look's own expression; working: attentive; waiting: the notify animation with its pastille; done: happy; failed:
sad), `actorFace(id, state, look)` (a still character of 40 px, `tight`, with the class `avatar`, `data-state` on it
and `data-hue` when it wears the fallback; the handle kept in a WeakMap), `dotFace(state)`, `setActorState(face,
state)`. `icons.js avatar` and `face.js setFaceState` are gone; the favicon and the notification icon are Dot's
character as a standalone SVG (`character/still.js stillSvg`, bloub's hex on white paper, `data-state` kept on the
root). `character.js` carries `data-animation`, `data-shape`, `data-expression`, `data-colour`, takes `tight`, and
watches the reduced-motion query only while it moves. `lookpicker.js`: the preview moving at 120 px, then the
shapes, the expressions and the colours as radio buttons each with a still face (`.look-option`, the picked one
with the strong border); in the agent editor as the Look field (a new agent starts from the fallback of an unnamed
id) and in Settings › General as the "Dot's look" card with Save. CSS: the face's state on the wrapper
(`breathe-face` at rest, `pulse-face` at work, the blink of `.ended`), the old `.face`/`.ring`/`.core`/`.tick` and
hue rules gone; reduced motion stills the wrapper. README: Agents, Settings › General, the design system's faces.
Tests: `tests/unit/test_agents.py` (the look round-trips in the store, the look alone, Dot's in the kv store, a
look outside the catalogue is refused), `tests/smoke/test_agents.py` (a look put on Scout is worn in its row and in
the bar, no hue then), `test_face.py` (the wrapper's animation names), `test_character.py` (the Faces card's five
characters counted); the Agents page, an agent's page, the editor and Settings › General read by eye.

**What.** `AgentRecord.look`: `shape`, `expression` and `colour` (three nullable TEXT columns through
`_ADDED_AGENT_COLUMNS`; `AgentRequest` takes them as the literal sets of item 101 and refuses anything else), Dot's
own look in the kv store under `look:dot` and served on `DOT_AGENT`; `GET/PUT /api/agents/{id}/look`. An agent
without a look wears the fallback its id gives it: the shape and the colour from the hue of `actors.js`, the
neutral expression, so every face stays distinct before the owner chooses. The agent editor (`agents.js agentForm`)
and Dot's own page get a Look card: the preview moving at 120 px, a row of the eight shapes as still frames, a row
of the sixteen expressions, the twelve colours as swatches, each a radio button with its name. `actorFace(id,
state)` and `avatar(state)` draw the character: a still frame at 20 to 40 px in the sidebar, the activity rows, the
timeline, the Team, Campaigns and Schedules pages and the stage bar, with the state as the expression (working
attentive, waiting surprised with bloub's pastille, done happy, failed sad, ended as it was) and the old CSS pulse
and blink kept on the wrapper; the favicon (`faceDataUrl`) is Dot's character in its state. The hue classes stay
for the ring of the delegated rows. README: Agents and the design system's faces. Tests: `tests/unit/test_agents.py`
(the look round-trips and a bad shape is refused), `tests/smoke/test_agents.py` (the Look card picks a shape and the
row wears it), `test_face.py` (the favicon, the home face) updated with the selectors kept where the tour and
`test_home.py` pin them (`.home-who .avatar`).

### 103. Alive beside the chat: the character works, waits, finishes and sleeps with the run

**Status: done.** Merged as `0a0fdaa` (PR #121, MYP-243 Done).

Design, as built (2026-10-11). The run page's side panel opens with the run's actor as a moving character: a new
card `charactercard.js` (`createCharacterCard({id, look, status, endedAt, size})`, a `section.card.character-card`
with the character at 160 px, `data-mood` on it) above Dot's computer. Its resting mood is one pure function,
`restingMood(status, endedFor)` in `mood.js`: queued and running think, blocked goes alert with the pastille
(notify), done rests idle and, ten minutes after the end, sleeps; failed rests sad, cancelled unimpressed. The
moment of a change is a second pure function, `entranceOf(before, after)`: a run first seen running hatches (bloub's
egg, once), a run that ends done laughs once (burst) and settles; the card plays the entrance, then the resting mood.
A browser step (`onBrowserStep`) turns the head aside toward the live view for 2.5 s (`lookAside`, `setLook`). The
card follows `onRunStatus` and the sleep by its own timer, and dies with the page. The stage bar's face, every face
in a chat and the delegated rows keep the still frames of item 102; the conversation's turns open with the actor's
still face at 28 px (`timeline.js` carries the actor id of each scope: the run's agent, Dot, or the delegated run's
agent), and the answer at the end wears the face of its end (`messages.js answerMessage({actorId, …})`, the same in
the earlier turns of the thread). The home greeting's face is Dot's character moving at 96 px (`liveFace`, idle with
a wink now and then, nothing from the sheet on top of it). "Faces that move" in Settings › General (`dot.characters`,
item 102) stills the card's character too. CSS: `.character-card`, the `.bubble[role="article"]` and `.answer` grids
with the `.turn-face` column, the home's live face. README: the home, the run page's panel, the design system's
motion. Tests: `tests/smoke/test_face.py` runs a scripted task with a question and reads the card's `data-mood`
through notify, thinking, burst and idle, the answer's face, and the still frame with the switch off; the home's
face is read as moving on its own.

**What.** The run page's side panel opens with the run's agent as a moving character (`run.js`, a `characterCard`
above Dot's computer, 160 px at the desk width, in the drawer on a phone): a run that starts hatches (bloub's egg,
once), a step that thinks thinks, a tool that reads the web or the screen looks aside toward the live view
(`setLook`), a wait for an approval or a question goes alert with the pastille, a cancel goes unimpressed, a failure
sad, a run that ends well laughs once (burst) and settles to idle, and a run ended for a while sleeps; all from
`onRunStatus` and the timeline callbacks already in the page, as one pure function `characterStateOf(status, lastEvent,
idleFor)` in `face.js`. The panel off (`dot.inspector`), the stage bar's face carries the same state as a still
frame. In the conversation, each turn of the agent opens with its still face at 28 px in the expression of its end.
A delegated run's row in the timeline shows its agent's still face. The home greeting's face is Dot's character
moving in bloub's default cycle (idle with a wink now and then) at 96 px; the Agents page's rows stay still. The
owner may turn the moving faces off (`dot.characters` in the prefs, a Settings › Look switch "Faces that move"),
which leaves every face a still frame. README: the run page, the home, the design system's motion. Tests:
`tests/smoke/test_character.py` runs a scripted task and reads the panel's `data-state` through thinking to happy
and the still frame with the switch off; `test_run_chrome.py` and `test_activity.py` keep their selectors.

### 104. The composer remade: one frame, quiet menus, drafts that keep, a face that listens

**Status: done.** Merged as `9261fa9` (PR #122, MYP-244 Done).

Design, as built (2026-10-11). `composer.js` grows the frame itself: `composerFrame({label, boxLabel, placeholder,
rows, maxPx, commands, send, sendLabel, draftKey, listener})` returns the card with the slash menu over it, the box,
the attached files in a row under it (40 px thumbnails; a drop tints the whole frame) and the bar with an `options`
row to fill and the round send; the home (`runs.js`), the run page's dock (`run.js`) and the agent page (`agents.js`)
build on it, so the three are one `.composer` with one padding, a hairline at rest, a stronger one under the pointer,
the 10 % ring in focus and the shadow on the dock alone. The bar is one grammar: pills of 32 px (`composerPill`, a
muted caption and the value, "Model scripted", "Options Ask"; borderless at rest, a hairline under the pointer and
while open), each opening a panel above it inside the frame (`popover.js anchoredPopover`: built when it opens,
closed by Escape, by a click outside or by its pill, the pill says `aria-expanded`); Task | Chat | Research stays a
segmented control; the Agent pick stays `select[aria-label='Agent']` in the pill's clothes. The Model panel lists the
daemon's default and the models by provider as a listbox and, under them, the reasoning levels of the chosen model as
one row, so two menus became one; the pill shows the model's name and, after it, the level chosen. The Options panel
holds Approvals, Goal mode and Keep going with the YOLO note, and the Options pill shows the approvals as its value, in
the warn tint for YOLO alone; nothing pushes the card taller. The phone wraps the pills over as many lines as they
need and gives the send a line of its own; the dock's placeholder is cut at the frame's edge, no longer under the send
(the fifth of item 100). Drafts keep: the text and the ready files of the home composer (`dot.draft.home`) and of each
run's dock (`dot.draft.<run id>`), written on every change (`attachments.ready()`, `restore(files)`) and cleared on
send (`frame.clear()`), so a navigation loses nothing; a draft that cannot be read is dropped with a warning. The
home's character listens (`presence.js listen`, `liveFace.look/express`): it looks down at the composer while the box
has the focus, goes attentive for 1.5 s after each keystroke and excited for 1.5 s on send. README: New chat, the run
page, the design system's controls. Tests: `tests/smoke/test_composer.py` keeps the slash menu's and gains the Model
and Options panels (the tick, the pick, Escape, the warn tint, a click outside) and a draft that survives a
navigation; `test_face.py`, `test_server.py` and `test_agents.py` keep their selectors; `test_css_tokens.py`
unchanged.

**What.** `composer.js` grows the frame itself: `composerFrame({label, rows, commands, send})` returns the card, the
textarea, the attachment row and the bar, and the home (`runs.js`), the run page's dock (`run.js`) and the agent
page (`agents.js`) build on it, so the three are one `.composer` with one padding, one hairline at rest, the 10 %
ring on focus, the shadow only on the dock that floats. The bar is one grammar: pills of 32 px (`.composer-pill`,
borderless at rest, a hairline on hover, `--radius-control`) that read a muted caption and the value ("Model
Muse Spark 1.3", "Thinking Extra high", "Approvals Ask"), each opening a listbox popover anchored above it like the
slash menu; Task | Chat | Research stays a segmented control of the same height; the Agent pick stays a native
select (`select[aria-label='Agent']`, which `test_agents.py` pins) in the pill's clothes. The Model pill's popover
holds the models by provider and, under them, the reasoning levels of the chosen model as one row, so two menus
become one; the Options popover holds Approvals, Goal mode and Keep going, and the Options pill itself shows the
approvals as its value (the warn tint for YOLO alone, the note inside the popover); nothing pushes the card taller.
The attachments sit in a row under the textarea with 40 px thumbnails; the drop tints the frame. The send stays the
round arrow, named by its placeholder; the phone wraps the bar to the pills on one line and the send on the next,
hiding nothing, and the dock's placeholder is no longer cut under the send (one of part 12's five). Drafts keep:
the text and the ready attachments of the home composer (`dot.draft.home`) and of each run's dock
(`dot.draft.<run>`), written on input and cleared on send, so a navigation loses nothing. The home's character
(item 103) looks down at the composer while the textarea has focus and goes attentive as the owner types, excited
for a moment on send. README: New chat, the run page, the design system. Tests: `tests/smoke/test_composer.py`
keeps `textarea[aria-label='Goal']`, `.composer .slash-menu`, `[role=option]` and the Research `aria-pressed`, and
gains the Model popover, the Options popover and a draft that survives a navigation; `test_face.py`,
`test_server.py` and `test_agents.py` updated where they reach into the bar; `test_css_tokens.py` unchanged.

### 105. The small polish part 12's review left: a scroll cue, empty sections that point, the Browser grid, the tour's content, Watch on an ended run

**Status: done.** Merged as `c14b670` (PR #123, MYP-245 Done).

Design, as built (2026-10-11). The scroll cue: `scrollcue.js scrollCue(box)` sets `data-scroll` on a box that scrolls
sideways ("more" while more lies to the right, else "end"), read on scroll and on resize; the Models tab cues each
of its tables after every paint and drops the cues with the tab, and the sheet fades the right edge of a
`.table-scroll[data-scroll="more"]` (a mask, 40 px). The Activity page's empty sections say what would show there
("Nothing waits for you. A run that asks a question or an approval shows here."; the same for Working and Ended
today). The Browser tab's four facts wear `kv-pairs` (cells of 150 px, as the bill of a run), so a phone sees two and
two, never three and a stray one. Dot's computer on an ended run hides Watch (`watchButton.hidden`, it was disabled)
and keeps the last screen as before. The tour seeds what its pages show: a run of six text steps with a shell call
each, so the conversation is taller than a window and a `run-top` shot shows the way down to the answer (the jump
arrow, asserted visible); the lesson it posts is marked as handed to the run and the run's end closes the use, so the
Memory page shows a score; a learned skill (`learned-count-files`, its `SKILL.md` and `playbook.json` under the
home's `playbooks`, `[agent] playbooks = true`) so the Skills tab has a row; the Activity page's empty line is
asserted to point. README: Dot's computer. Tests: `tests/smoke/test_tour.py` as above (the empty words are read
there, on the tour's page, rather than in `test_activity.py`, whose sections are never empty), the tour read by eye.

**What.** Four of the five from item 100 (the fifth, the dock's placeholder cut on a phone, is item 104's): the
Models tab's table on a phone shows a fade at its right edge while it can scroll and the Reasoning and Context
columns are off-screen; the Activity page's empty sections say what to do ("Nothing waits for you. A run that asks
a question or an approval shows here."); the Browser tab's status grid takes two columns of two cells with no stray
divider; the Dot's computer card on an ended run drops Watch and keeps the last screen. The tour gets content
enough to show what it shoots: a learned skill on the Skills tab, a scored lesson on the Memory page, a run long
enough for the jump arrow. README where a line changes. Tests: `tests/smoke/test_tour.py` (the seeded content),
`test_activity.py` (the empty words), the tour read by eye.

### 106. The review: every feature of parts 1 to 13 read once more, the characters' cost measured

**Status: done.** Merged as `8e3818d` (PR #124, MYP-246 Done).

**Findings, as built (2026-10-11).** Three readings and one measurement. (1) The tour as item 105 left it (23 pages,
two schemes, two widths, 108 shots) read by eye against the README: the home with its moving face and the composer's
pills, the run page from the top with the way down and from the answer, its panel with the character card, the
Agents page and an agent's page, Activity with its pointing empty sections, Campaigns, Team, every Settings tab (the
Models table with its cue on a phone, the Browser facts two and two, Skills with a learned row, Memory with a scored
note), Controls; nothing found broken or contradicting the README. (2) The offline smoke suite in full (`-m "not live
and not internet and not hosted and not desktop"`, the tour apart): 319 passed, 1 skipped, 2 failed, both the tests'
own: `test_controls.py` still counted twelve sections of `#/controls` where item 101 added the thirteenth
(Characters), and `test_phone.py` read the Activity page's sections before they were drawn (a count, not an
expectation); both fixed in this item's PR. (3) The owner's daemon through the API, read-only: healthy, no active
run, approvals Ask, the scheduler running, every doctor check green but "browser profile" (My Chrome is not attached
to the daemon; a choice, not a defect); Dot and one agent, neither with a look yet, so both wear the fallback. The
measurement: `tests/smoke/test_character_cost.py` reads Chrome's performance metrics (`Performance.getMetrics`,
TaskDuration and its script, style and layout shares) over four seconds of frames: the home with its one moving face,
greeting and composer costs 1.02 ms of main-thread time a frame (script 0.24, style 0.08, layout 0.19) against
0.17 ms with the faces off, so one moving face is about 0.85 ms a frame; the gallery of `#/controls` with fourteen
moving at once 3.40 ms. A few milliseconds at most, as the plan asked, so no further cap in the code: the test caps
the home at 6 ms a frame and prints the three readings. README: the cost, in the design system's characters passage.
Nothing is left for the next part's first items.

**What.** As item 100, once more: the tour of every page in both schemes and both widths read by eye against the
README, the offline smoke suite in full, the owner's daemon read through the API only; and one measurement this
part needs: the main-thread time per frame of one moving character and of the home with its greeting, read in
Chrome's performance trace, written here with the number, and a cap set if it costs more than a few milliseconds.
What is broken, stale or contradicts the README is fixed in this item's PR or written as the next part's first
items. The findings go in this section as built.

## Part 14: the second look: the still face that follows, marks on the composer, the computer as a stage, rules in plain words (planned 2026-10-11)

The owner's fourteenth word, on 2026-10-11, after part 13, with five screenshots (the sidebar's head in the desktop
shell, Dot's look in Settings, a composer of another product with a mark per provider, and two of another agent's
"computer": the page large in a warm frame, the character with its state word at the head of the chat, "has
control · Take over" under the view): an "Internal error: SSLError … bad record mac" seen twice, was it Dot's doing
or the system's; Dot's computer could be nicer, with a size to choose; Dot's look does not work, the movements and
the preview of what is picked; a composer like the third screenshot, each provider with its own SVG mark, tidy,
always soft and premium; New chat under the search, the search at the top; the Rules tab is hard to use, an easy
way to make a rule with the agent's help; the fourth screenshot's design too; and the alignment unevenness of the
first screenshot fixed.

**Status (2026-10-12): built.** Items 107 to 112 merged in a day (`6c79636`, `108c91d`, `b5d42c0`, `6da73c4`, `5d96935`,
`392132c`), each behind the gate, its smoke tests and its screenshot tour read. The SSL error the owner saw was the
network path's, not Dot's: a TLS read that failed with a bad record mac, retried now as the model call's other transport
errors are. A still face follows the look picked and the page says when the system stills every face; the tab wears
Dot's face; the sidebar's search comes first and its head sits on the traffic lights' line. The composer carries a mark
per provider and an icon on every pill, the reasoning a pill of its own; Dot's computer is a stage in three sizes with a
drag handle, the control under it and the character with its state at the head of the chat; a rule is said in plain
words, drafted by the review model, written and read back by Dot, and an approval card offers to make one. The review
read the part once more and fixed what it found; item 112, from the owner's word during the review, made both eyes of
every face move again. What waits: the daemon restarted on `392132c` once no run is active, which brings item 107's TLS
retries and item 110's drafting to the owner's daemon; the desktop shell's top line and the links opened on the Mac,
written under item 111 for the next part.

**Status (2026-10-11): planned.** What is built today, and what the readings found: the SSL error is a TLS read
from OpenCode Go (`llm/opencode_go.py` through `core/retry.py`) that failed with `bad record mac`, five times over
two days (twice on 2026-10-10, before part 13), a corruption on the network path rather than Dot's code; it crashed
the run as "Internal error" because `ssl.SSLError` is not among the transport errors the model call retries
(`llm/http.py _TRANSPORT_ERRORS`). Dot's look card works in Chrome and in WebKit against the owner's own daemon
(the preview follows the shape, the expression and the colour and moves), but a character that holds still (the
system's Reduce motion, or "Faces that move" off) keeps its old shape and expression after a pick: `setShape` and
`setExpression` start a morph at the live clock and the still frame is sampled at the pose's own time, before the
morph; the owner's Mac most likely reduces motion, which also stills every face ("the movements do not work"), and
nothing on the page says so. The favicon is a `data:` URL that the page's Content-Security-Policy (`img-src 'self'
blob:`) refuses, so the tab shows no face. The sidebar's head in the desktop shell keeps 84 px for the traffic
lights but its row sits 12 px of padding lower than them. The sidebar lists New chat, then the search. The
composer's pills are words alone: "Model scripted", "Options Ask"; the models list by provider name with no mark.
Dot's computer is a fold card of the side panel with the page at the card's width and the control pill under it;
no size to choose. A rule is made from three fields (tool glob, regex, why) and a four-way answer, with a sentence
preview; nothing writes it for the owner, and an approval card offers no way to make a rule of what it asks.

**The shape of this part (the decisions).** First the fixes the owner saw, as one item, each with a test that would
have caught it. The composer keeps part 13's frame and pills and gains marks: a mark per provider on the Model pill
and in its menu, an icon on every pill, hairlines between them, the reasoning as a pill of its own, in the manner of
the third screenshot; the marks are SVGs served from Dot's own origin under their licences. Dot's computer becomes
a stage: the view in a warm frame with a size the owner chooses and drags, remembered per browser, the control
under it, and the actor's character with its state word at the head of the chat, after the fourth screenshot. Rules
are made in plain words: the owner says what should happen and the model writes the rule (tool, pattern, answer,
why) for the owner to read and keep; an approval card offers to make a rule of what it asks. The whole is read once
more at the end.

### How to start part 14 (the handoff prompt)

> Read ROADMAP.md part 14 and the status table of part 9 (rows 107 to 111). One item at a time, in order; the
> protocol of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged.
> Never touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no
> active run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything
> written is English. Every UI item ends with the screenshot tour read by eye.

### 107. The fixes of the second look: the still face follows, the favicon freed, TLS reads retried, the sidebar's head and order

**Status: done.** Merged as `6c79636` (PR #127, MYP-247 Done).

**Design, as built (2026-10-12).** The still face: `character.js` tells the engine a time already past
(`-SHAPE_MORPH`) when a shape or an expression is set while the character is not subscribed to the ticker, so the
still frame, sampled at the pose's own time, shows the pick; moving, the live clock as before. `test_character.py`
mounts a character under reduced motion and reads the body's path and the eyes' paths before and after a pick, and
opens Dot's look card in Settings under reduced motion to check the preview the same way. Settings › General ›
Faces that move gains a line, shown while `prefers-reduced-motion` matches (`[data-reduced-motion]`): the system
reduces motion so every face holds still whatever the switch says, and where the Mac's own switch is. The favicon:
`img-src` of the Content-Security-Policy gains `data:`; `test_face.py` draws the tab's icon as an image and reads
its width. TLS: `ssl.SSLError` joins `_TRANSPORT_ERRORS` with the reason; `tests/unit/test_transport_retry.py`
streams through an `httpx.MockTransport` that raises the owner's exact error, counts three attempts under a policy
of three, two resets of the sink, and reads the wrapped error as retryable. The sidebar: the search is the first row
of the body, New chat under it (the search's margin 0 above, 4 px below). The desktop head: 38 px high with no
padding above and 8 px below, so the brand's centre sits 15 px under the top edge, where macOS centres the traffic
lights of an overlay title bar; the tour reads the brand's box in the desktop shot and asserts it within 2 px.

**What.** A still character follows its look: `character.js` applies a shape or an expression set while the
character is not subscribed as already morphed (the engine told a time earlier than the pose's by the morph's
length), so the still frame shows the pick; `#/controls` and the look picker's smoke test pick a shape and an
expression under reduced motion and read the path. Under "Faces that move" a note says, while the system reduces
motion, that every face holds still for that reason and where the switch of macOS is. The favicon: the policy's
`img-src` gains `data:`, and the smoke test of the face reads the tab's icon as loaded (`link[rel=icon]` with a
natural size). TLS: `ssl.SSLError` joins `_TRANSPORT_ERRORS`, so a read that fails with `bad record mac` is
retried with a warning per attempt and raised after the last like the other transport failures; a unit test raises
it from a fake stream and counts the attempts. The sidebar: the search above New chat; in the desktop shell the
head's row sits level with the traffic lights (the head 32 px high under the shell, the brand centred on the
lights' line), checked by the tour's desktop shot. README where a line changes.

### 108. The composer with marks: a mark per provider, an icon on every pill, the reasoning as its own pill

**Status: done.** Merged as `108c91d` (PR #128, MYP-248 Done).

**Design, as built (2026-10-12).** The marks: `static/icons/providers/` holds one SVG a provider on a 24 grid in
`currentColor` (the Claude mark for the Anthropic provider's models and Ollama's from Simple Icons, CC0; OpenAI's and
OpenCode's from Lobe Icons, MIT; a ring with a core of Dot's own for any other provider), with their sources, licences
and a trademark line in its `LICENSE`. `providerMark(provider)` (`providers.js`) is a span the page paints as a CSS
mask of the provider's SVG (`.provider-mark[data-provider]`), so a mark takes the colour of the text around it and the
policy's `img-src 'self'` covers it; the Claude mark keeps Anthropic's colour (`--mark-claude`). The plan's `src` became
the mask: an image could not take the ink of the scheme. `composerPill` takes an `icon` node, keeps its caption for
screen readers only and gains `setIcon`; `icon()` names itself (`data-icon`). The home's bar: the mode left its
segmented control for a pill (a target for Task, a speech bubble for Chat, the search glass for Research; its menu the
three with what each does); the agent pill wears its agent's still face; the Model pill the mark of the chosen model's
provider and its name, its menu the models alone, each row with its mark under its provider's heading; a Reasoning
pill (a brain) shows the level chosen or the daemon's, only for a model that offers a choice, its menu the levels as a
listbox; the Options pill a lock closed for Ask, a shield for Auto (a review model answers) and a lock open for YOLO. A
pick closes its menu and gives the focus back to the pill. A hairline stands between two shown pills (the `::before` of
the second, outside its box, so the tint under the pointer leaves it), not on a phone, where the pills wrap and the
attach button and the send stay at the end of the last line. The frame puts the attach button with the send at the
right end, in every composer. Soft at rest: the pills
in the secondary ink, the values in the label weight, the icons at 16 px, the chevron at 14 in the muted ink.
`test_composer.py` reads the Ollama mark on the Model pill (its `data-provider`, the computed mask, the SVG served as
`image/svg+xml`) with no refusal of the page's policy in the console, picks a model with levels (added in the page to
the daemon's list through `page.route` on `/api/models`, since the scripted model offers none) and its level through the
Reasoning pill, walks the Mode menu and reads the Options pill's locks; the menus are shot with their animations
finished. The module-wide asyncio mark of `test_transport_retry.py` (item 107) warned on its sync test: the async test
carries the mark alone. `test_face.py` waited for the drawer with a string predicate, which the page's policy refuses
(no `'unsafe-eval'`) whenever the first check fails: an `expect` on the shell's `data-nav` instead.

**What.** `static/icons/providers/<provider>.svg`: a mark for each provider Dot knows (Anthropic, OpenAI, Ollama,
OpenCode Go, and the generic one for the rest), simple single-colour SVGs that take `currentColor`, with their
source and licence in `static/icons/providers/LICENSE`. `composerPill` takes an icon node: the Model pill wears the
mark of the chosen model's provider and its name; the Reasoning pill (a brain, `icons.js`) shows the level of a
model that offers a choice and opens the levels as a listbox, so the Model menu lists models alone, each row with
its mark; the Options pill wears a lock open (YOLO), closed (Ask) or half (Auto); the Agent pill its agent's still
face; hairline dividers stand between the pills (`.composer-divider`); the attach button and the send keep the
right end. The look of the third screenshot: a soft, quiet bar, the icons at 16 px in the muted colour, the values
in the label weight. README: New chat, the design system's controls. Tests: `test_composer.py` reads the mark's
`src` on the Model pill and the Reasoning pill's menu; `test_css_tokens.py` unchanged.

### 109. Dot's computer as a stage: a frame with a size, the control under it, the character at the head of the chat

**Status: done.** Merged as `b5d42c0` (PR #129, MYP-249 Done).

**Design, as built (2026-10-12).** The size is the side panel's, since the view grows with the panel it lives in:
`stagesize.js` keeps `dot.computer.size` (`compact`, `wide`, `full`, or the width in px of a drag) and puts it on the
run's frame as `data-stage` (and `--stage-px`); run.css turns it into the grid's columns, the conversation, a 28 px
column for the handle (the old gap) and the panel: compact 320 px as before, wide `minmax(320px, 1fr)` beside an equal
conversation, full the conversation at 360 px and the panel the rest, a dragged width clamped between 320 px and what
leaves the conversation 360. Any size but compact lets the page go past its reading width of 1240 px (`:has()` on the
frame). The three buttons sit above the view beside the page's address (in the card's head they ran over its title at
320 px), pressed for the size in use, hidden in the drawer of a narrow page, which keeps one width. The handle
(`role="separator"`) is a grip in the middle of the column, sticky like the panel: a drag sets the width live and keeps
it at the release, the arrow keys move the edge 32 px, a double click gives the panel back its 320 px. The plan's
"leaves the fold card" became a fold card whose body is the stage: it folds like its neighbours, and the warm frame takes
24 px of padding and the card's radius at any size but compact; the pill under it names the actor ("Dot has control",
an agent's name). The character card shows the actor's name and the word of its state (`mood.js stateWord`: Starting…,
Thinking…, Waiting for you, Done, Failed, Stopped); while the panel is docked at a size but compact, `placeCharacter()`
(after each arrangement of the panel, `createInspector`'s `onArrange`, and each change of size) moves it to
`.run-chat-head` at the top of the conversation, a 64 px character beside its name and word in a card that stays under
the top bar on a band of the page's ground fading out below it, and back above the computer at compact or in the drawer.
`test_takeover.py` reads the panel's width at each size (320, half of the frame less the handle, the conversation at 360),
drags the handle 200 px, reloads and finds the width kept, steps it with the left arrow, double-clicks it back to compact,
and follows the character from the panel to the head of the conversation and back; on a phone the sizes are away. The
tour shoots the run at the wide panel on a wide window (`run-wide`, light and dark) and counts those two shots.

**What.** The computer's view leaves the fold card for a stage: `computer.js` draws the page in a warm frame
(`--computer-backdrop`) with a size chooser of three (compact, the panel's width as today; wide, the panel widened
to half the window; full, the conversation folded away and the stage across the window) and a drag handle on the
stage's edge that sets any width between, kept per browser (`dot.computer.size`); the control pill ("Dot has
control · Take over", "You have control · Return control") sits under the stage as in the fourth screenshot;
Watch and Close the view stay. The run page's head of the chat carries the actor's character at 64 px with its
name and its state word under it ("Thinking…", "Waiting for you", "Done"), the character card of item 103 moving
there when the stage is wide or full. The inspector's width follows the size. README: the run page, Dot's computer.
Tests: `test_takeover.py` picks each size and reads the stage's width; `test_run_chrome.py` keeps its selectors;
the tour shoots the run page at the wide size.

### 110. Rules in plain words: the model writes the rule, an approval card offers one

**Status: done.** Merged as `6da73c4` (PR #130, MYP-250 Done).

**Design, as built (2026-10-12).** The model never writes the pattern. `POST /api/rules/draft` (`{"sentence"}`) asks the
review model (`[models] review`, call purpose `rule_draft`, outside any run) for the parts of a rule in one forced call
of `draft_rule` (`policy/drafting.py`, `RuleDraft`): the tool, a name or a glob from the daemon's tools, each listed
with the first sentence of its description; the answer; the shell commands the rule is about or the words the action
must mention; and `problem`, why no rule can be made of the words. `rule_from_draft` writes the pattern to fit what
`match_rule` searches (the summary, a line break, the target) and to err on the side of the answer: a rule that lets Dot
act fits each command alone with plain arguments, after a `cd` at most (anchored on the target's one line; a command
with `;`, `&`, `|`, a backtick, `$`, `<`, `>`, `(` or `)` is refused), a rule that stops Dot fits a command or a word
anywhere, as a whole word (`x.com` not inside `netflix.com`). A draft whose glob fits no tool, that gives commands for
another tool than the shell or with mentions, or a command that is not plain, is asked for once more like a reply that
is not a verdict (`_decide_checked` in `agent/review.py`); a `problem` is answered 422 with the model's sentence. The
drafted rule's description is the sentence it reads back as, written in code as well ("For “git status” or “git diff”
run alone in the shell, with plain arguments (after a cd at most): Dot does it without asking."), so the list says in
words what a long pattern fits; a pattern past 40 characters is cut in the sentence a rule reads as, whole in its
tooltip and in the form. The plan's model-written "why" and read-back line became this one sentence. The Rules tab opens
with "Say the rule in your words": the box, three sentences to start from, Draft the rule (also ⌘/Ctrl-Enter), and on
the button's line the read-back or the model's reason; the draft fills the form below and the focus goes to Add rule,
and the box empties once the rule is added. The plan's form prefilled from an approval's arguments became a sentence:
"Make a rule of this", under the action of an approval card (not a question or a hand-off), opens
`#/settings/rules?sentence=Let Dot use {tool} for this without asking me: {summary}` with the sentence in the box, to be
changed before it is drafted. The line on the order of the rules sits under "Checked in this order". The form's
placeholder `^git (status|diff|log)` and the README's `^git push` never fitted a shell action, whose summary starts with
"Run: ": they read `\bgit (status|diff|log)\b` and `\bgit push\b` now, and the field's hint says what is searched.
Tests: `tests/unit/test_rule_drafting.py` (the patterns against the shell tool's own description of a command through
`match_rule`, the checks, the read-back, the drafter's second ask and its `problem` with a scripted reply);
`tests/smoke/test_rules.py` (no rule from a question about the weather, a git rule drafted, read back, filled and added,
the box emptied, the phone in dark, and the approval card's offer opening the tab with its sentence).

**Fixed after (2026-10-12).** The owner's sentence "Dot can run everything without permission from me" came back as a
refusal in Spanish. Asked again with Dot's tools, the review model on the owner's Mac (`anthropic:claude-sonnet-5-5`)
answered it once in Spanish ("too broad") and once with an English note about the broad rule in `problem`, which the
drafter takes for a refusal, and refused a sentence asking it to send passwords out in French: "in the language of the
owner's sentence" let it pick a language. The drafter's instructions (`review._RULE_SYSTEM` and the description of
`RuleDraft.problem`) now say that `problem` is empty whenever the parts are a rule, a broad one too, and never holds a
note; that its one sentence is in English, the language of Dot's interface; and that words covering every action make a
rule for every tool (`*`, no commands, no mentions), since the protections that are always on stay on whatever a rule
says: only a sentence that asks for one of those actions itself is refused. On the same model the sentence now drafts
"For every use of any tool: Dot does it without asking.", in Turkish too; the passwords and a sentence about Dot's tone
are refused in English; the page's three examples draft as before. `tests/smoke/test_hosted_services.py` (hosted, on
that model): the rule for everything and the refusal in English. README: Rules. The fix merged as `8090ebc` (PR #154).

**What.** `POST /api/rules/draft` takes a sentence ("let Dot run git status and git diff without asking", "always
ask me before it posts on X") and returns a `RuleRequest` drafted by the fast model with a typed reply (tool glob,
regex, answer, why, and one line that reads the rule back), checked against the tools the daemon has and the four
answers before it is returned; the Rules page opens with one box, "Say the rule in your words", a Draft button,
the drafted rule shown as the sentence with its fields open for a change, and Keep; the three fields stay for the
owner who knows them. An approval card (the home, the run page, Activity) gains "Make a rule of this": it opens
the Rules page with the fields prefilled from the approval's tool and arguments (the regex escaped from the
command's first word) and the answer chosen. The page's head says in one line what rules are and in what order they
are read. README: Rules. Tests: a unit test of the draft's checks with a scripted reply; `test_rules.py` (smoke)
drafts a rule from a sentence against the scripted model and keeps it; the approval card's offer.

### 111. The review: part 14 read once more

**Status: done.** Merged as `5d96935` (PR #131, MYP-251 Done).

**Findings, as built (2026-10-12).** Three readings. (1) The tour (23 pages, two schemes, two widths, 90 shots) read
against the README by two readers, and by eye wherever they doubted: the sidebar with its search first, the composer's
marks and pills, the run page's panel at each size with the character at the head of the conversation, the Rules tab's
words card, and every other page as item 106 left it. Three defects, fixed in this item's PR: at the panel's compact
width the facts beside a fold's title were cut ("Cost 123 ms 13 steps 13k in 650 o"), so the panel is a container now
(`container: inspector`) and under 420 px those facts take the line under the title, as on a phone, which the tour
checks (no `.fold-summary` of the panel wider than its box); the computer card of a run that had ended without showing a
page still offered to watch it ("Watch the page Dot's browser is on…") when Watch had gone, and now says that the run
has ended and that the page shows only while a run is going on; the run page's placeholders were cut on a phone ("…or
ask abo") and read "Steer this run, or /btw to ask on the side" and "Follow up, or /btw to ask on the side". The README
did not say that the composer's pills wrap on a phone without their hairlines; it does. Doubts that held as designed:
the Models table scrolls sideways on a phone with its cue (item 105); a skill of Dot's has Drop and no Share, since
Dot's skills are everyone's; the emergency stop in dark is dark ink on its salmon ground; New agent and Team are icons
on a phone. (2) The offline smoke suite in full (`-m "not live and not internet and not hosted and not desktop"`, the
tour apart): 324 passed, 1 skipped, 2 failed, both passing alone.
`test_browser_attach.py::test_a_tab_the_user_opens_meanwhile_is_not_taken_over` found in the headless Chrome it had
started a tab of `https://x.com/ClaudeDevs`, which no test opens: a link opened elsewhere on this Mac in that minute,
which macOS handed to the newest Chrome (below); `test_character_cost.py` lost its page in the middle of its four
seconds of frames while the disk had 4.3 GB left (the system freed 57 GB minutes later). The eval check: every task can
be passed and cannot be passed by doing nothing. (3) The owner's daemon through the API, read-only: healthy, every check
of the doctor green, no run active, no approval waiting, the code of `f48e2b5` until this part's restart. The two
`SSLV3_ALERT_BAD_RECORD_MAC` errors of the owner's screenshots are the runs `run_mv47c2a5_34b576` and
`run_mv47fc7z_6f3685` (2026-10-11 22:15 and 22:16, `opencode_go:muse-spark-1.3-contributor`), a TLS read that item 107
retries once the daemon restarts; the five runs that died on `database is locked` at 06:30 that day were item 66's.
Until the restart the page, served from the working tree, is newer than the daemon: the Rules tab's Draft answers 404
until then.

**Left for the next part.** (a) The desktop shell's top line. Item 107 put the sidebar's head on the traffic lights'
line (their centre 15 px down), but with the sidebar away the top bar keeps its 52 px and centres its toggle and brand
at 26 px, under the lights' line, and with the sidebar open the top bar's own controls sit at 24 px beside a head at 15.
One line for the lights, the head and the top bar, in both states, wants the lights moved to the bar's centre
(`traffic_light_position` of the Tauri window, `desktop/src-tauri/src/lib.rs`) and the head with them, checked in the
desktop shell itself, which the tour cannot draw. (b) A link opened elsewhere on the Mac may land in a Chrome that Dot
started: seen once, in the smoke suite's headless Chrome. Whether a link the owner opens lands in Dot's own Chrome
(`own` mode, a second Google Chrome with Dot's profile) while it runs was not checked; if it does, Dot's Chrome could be
started as a browser macOS does not hand links to (another bundle, such as Chrome for Testing).

**What.** As item 106: the tour of every page in both schemes and both widths read by eye against the README, the
offline smoke suite in full, the owner's daemon read through the API only. What is broken, stale or contradicts the
README is fixed in this item's PR or written as the next part's first items. The findings go in this section as
built.

### 112. Both eyes move: one key per eye in the character's drawing

**Status: done.** Merged as `392132c` (PR #132, MYP-252 Done).

The owner's word on 2026-10-12, during item 111, with a screenshot of Dot's look in Settings (the circle, attentive, the
preview's eyes one above the other like an exclamation mark): one of Dot's eyes always moves and the other does not,
whatever the character.

**Design, as built (2026-10-12).** `character.js` draws a frame by keeping each group of the SVG in step with a list
(`sync`): one child per item, found again by its key. The eyes' key was their index, `(_, i) => String(i)`, but `sync`
called `keyOf(item)` alone, so both eyes had the key `undefined`. The first frame made two paths; from the second on,
both eyes were written into one of them (the outer eye, written last, won) and the other kept the inner eye of the first
frame, where and as the character was first drawn: on every moving face (the home's, the run page's, the look's preview,
the gallery's) and on a still one whose look changed, as the preview under Reduce motion. `sync` now hands `keyOf` the
index too, and a key repeated within one frame is an error rather than a child shared in silence; the rings, their
gradients and the dots had keys of their own and were right. Tests: `test_character.py` reads each eye on its own: both
eyes' matrices change over half a second on a moving face, and a still face set to surprised draws two round eyes of one
size, neither of them the neutral's (before the fix the first eye's matrix was the same at both reads). The gallery's
test counted the orbit's ring gradients at once, before the first ring enters some 50 ms after the mount, and failed
whenever the page opened faster than that; it now waits for the first.

## Part 15: the finish: a quiet head, Dot's own mark and its opening, the composer once more, a deep review (planned 2026-10-12)

The owner's fifteenth word, on 2026-10-12, after part 14, with two screenshots (the card at the head of a run's
conversation, Dot's character with “Failed” under its name; the home's composer with its five pills, the last fallen to
a line of its own): the SSL error once more, OpenCode Go's or not; the character card at the head of the chat is not
needed; the Dot icon and name at the top of the sidebar go; a new logo for Dot, drawn as SVG and used where one is
needed; a short opening when the app starts, the icons coming in with a bounce; the composer still does not look good,
research the best structure on the web if need be; then a deep code review that fixes what it finds, and the project is
finished: the owner starts testing, new features come later. Design matters, in every part.

**Status (2026-10-12): built.** Items 113 to 116 merged in a day (`ea21f27`, `ea014d5`, `a667d39`, `0304257`), each
behind the gate, its smoke tests and its screenshot tour read. The SSL crash the owner saw ran on a daemon from before
item 107's retry, so only the answer changed. The run page lost its character card and the sidebar its brand; Dot has
its own mark, a yellow dot with the character's eyes, on the favicon, the app's icons and a wordmark that opens the app;
the composer is one bar that never wraps; the deep review rewrote the shell guard to read a line as the shell does and
fixed what four readers found in the agent, the tools, the server and the interface. The daemon was restarted on
`0304257` with no run active. What waits: the desktop app rebuilt by the owner (`cd desktop && npm run build`), and part
16, from the owner's word during the review.

**Status (2026-10-12): planned.** What is built today, and what the readings found: the SSL crash the owner saw (run
`run_mv4gcno3_2ef1f7`, 02:28:55) ran on the daemon of part 13 (`f48e2b5`), thirteen minutes before the restart at 02:41
put item 107's retry in place; its traceback is the one item 107 caught (a TLS read of the response's head in
`stream_model_reply`), which `tests/unit/test_transport_retry.py` holds, so only the answer changes. The run page shows
its agent's character with its name and the word of its state (`charactercard.js`, items 103 and 109) at the head of the
conversation while the panel is wide, at the head of the panel otherwise. The sidebar's head, and the top bar with the
sidebar away, carry the brand: the ring-and-core glyph (`icons.js brandMark`) and the name. The same glyph is the static
favicon, the sign-in card's, the desktop shell's page for a daemon that is down and, rendered by
`desktop/icons/render.py`, the app's icon, its menu bar icons and the web app's; nothing plays when the app opens. The
composer's bar wraps its pills (`.composer-options` has `flex-wrap: wrap`): at the owner's width the fifth pill falls to
a line of its own behind a stray hairline; the agent pill keeps a wide gap before its chevron; YOLO wears a fill and a
border in the warn colour; the box is 84 px tall before a word is typed; the goal mode's hint is a two-line paragraph
inside the frame.

**The shape of this part (the decisions).** First the quiet head: the character card leaves the run page (the state
stays in the top bar's face and badge), the brand leaves the sidebar and the top bar. Then Dot's own mark: a dot that
looks, a yellow disc with two capsule eyes after the character's own, and a wordmark in which it is the o of “dot”, set
between the stems of a d and a t drawn in one line. The mark is the favicon, the app's icon on a dark tile, the menu
bar's and the web app's icons; the wordmark heads the sign-in card and the desktop shell's page. The opening plays the
wordmark in about a second when the app starts: the d and the t hop up, the dot drops in, bounces and opens its eyes;
once per window, skipped by a click or a key, not at all when the system reduces motion, and it never holds the app
back. The composer is rebuilt on a research of open composers (Vercel's chatbot, Open WebUI, assistant-ui, measured from
their code): one bar that never wraps, the labels folding to icons as the frame narrows, a rounder frame, a box of one
line that grows, the risky approvals as a quiet warn-coloured dot and word, the goal's hint one line under the frame.
Last, the deep review: four readers, each on a part of the code, every finding checked by hand before it is fixed. After
it Dot is finished as a product to test: no new feature until the owner's testing asks for one.

### How to start part 15 (the handoff prompt)

> Read ROADMAP.md part 15 and the status table of part 9 (rows 113 to 116). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch
> the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run; the
> owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is English.
> Every UI item ends with the screenshot tour read by eye.

### 113. The quiet head: no character card at the head of the chat, no brand in the sidebar

**Status: done.** Merged as `ea21f27` (PR #135, MYP-253 Done).

**Design, as built (2026-10-12).** The run page has no character card: `charactercard.js` and `mood.js` are gone with
their CSS (the card in the panel and the band at the head of the conversation), and so are the two callbacks that placed
the card (`createStageSize`'s `onChange`, `createInspector`'s `onArrange`). The panel holds Dot's computer, Details,
Cost, Plan and Actions; the run's state is the top bar's face and its badge; each turn and the answer still open with
the actor's still face, and the home's face still moves. The sidebar's head holds its button alone, at the left, where
the top bar's opener stands while the sidebar is away, so the button keeps its place in both states (84 px from the left
in the desktop shell, after the lights). The brand, the ring-and-core glyph with the name, is gone from both bars; the
glyph stays on the sign-in card until item 114 replaces it. README: the run page's passage, the moving characters (two
now), the sidebar's head. Tests: `test_face.py`'s card test becomes one that finds no card at the head of the
conversation or of the panel at the Wide size, and the answer's still face; `test_takeover.py`'s sizes lose the card's
checks; the tour checks the sidebar's button on the lights' line and the opener at the same x (in the desktop shell the
opener still sits 11 px lower than the lights' line, item 111's (a)).

**What.** The run page loses the character card (`charactercard.js`, and `mood.js` if nothing else reads it, with their
CSS and their tests): the run's state stays in the top bar's face and badge, the timeline keeps its turn faces, the home
keeps its moving face. The sidebar's head keeps its toggle alone (on the traffic lights' line in the desktop shell); the
top bar with the sidebar away drops the brand too. README where a line changes. Tests: the tour, `test_face.py`.

### 114. Dot's mark and its opening: a dot that looks, the wordmark, the icons, a bounce at launch

**Status: done.** Merged as `ea014d5` (PR #136, MYP-254 Done).

**Design, as built (2026-10-12).** The mark is a yellow disc (`--brand`, the same in every look and scheme) with the two
capsule eyes of the character's attentive rest pose in `--on-brand`, drawn 1.12 times the engine's size so they hold at
16 px. `desktop/icons/render.py` writes it as the favicon and renders from it the app's icon (the mark lit as a ball
over its shadow on a charcoal tile, inset on the 1024 grid), the web app's icons (the same tile, square and full bleed)
and the three menu bar icons (attentive at rest, the character's upward glance while a run works, the attentive mark
with the notification blue's pastille while something waits); `npm run icons` builds the bundle's set in a scratch
folder and copies the five files the bundle lists. The wordmark is “dot” with the mark as its o, between a d (a ring and
a stem) and a t (a bar and a stem with a hooked foot) drawn in one stroke with round ends in `--text`. Its one drawing
is the opening's in `index.html`; the sign-in card shows a copy (`icons.js wordmark`, named “Dot”) and
`desktop/ui/down.html` repeats it in the neutral look's colours; `tests/unit/test_mark.py` keeps the favicon's eyes, the
page's and the down page's alike and their colours the tokens'. The ring-and-core glyph is gone (`brandMark` and its
CSS). The opening: `boot.js` sets `data-opening` on the page element before the first paint, once per window session
(`sessionStorage`), never under reduced motion and not at all when the storage is blocked. The overlay (the page's
ground over everything, no pointer, hidden from screen readers) and its parts share one timeline (`--motion-opening`,
1.3 s, with `--ease-fall` for the drops): the d and then the t hop up past their line and back, the dot falls stretched
with its eyes shut, squashes as it lands, bounces once more, opens its eyes and blinks, and the ground fades from 84 %.
The overlay's own `animationend` takes the attribute away; a click or a key first turns it into a quick fade. Tests:
`tests/smoke/test_opening.py` (it plays without taking the pointer and leaves, not again on a reload, a key cuts it
short, never under reduced motion; two frames kept for the eye), and the tour waits it out. The Dock and menu bar icons
change with the next build of the desktop app.

**What.** The mark: a yellow disc with two capsule eyes looking up and to the right, after the character's rest pose,
drawn on a grid that holds at 16 px. The wordmark: “dot” with the mark as its o, between a d and a t drawn in one line
with round ends, in ink. Where they go: the static favicon (the tab's live icon stays the character in its state), the
sign-in card, the desktop shell's page for a daemon that is down, the app's icon (the mark on a dark rounded tile), the
three menu bar icons and the web app's icons, rendered by `desktop/icons/render.py` from the same SVG. The opening: the
wordmark in the middle of the window over the page's ground; the d and the t hop up, the dot drops in, squashes on two
bounces and opens its eyes, then all of it fades into the app. About a second in all, CSS keyframes on motion tokens,
once per window session (so on every launch of the desktop shell), skipped by any click or key, not played under Reduce
motion, hidden from screen readers and transparent to the pointer, so it never holds the app back. Tests: a smoke test
that sees the opening play and leave, and not play under reduced motion or a second time; the tour waits for it.

### 115. The composer once more: one bar that never wraps, labels that fold, a quiet warning

**Status: done.** Merged as `a667d39` (PR #137, MYP-255 Done).

**Design, as built (2026-10-12).** One frame for the three composers (`composer.js composerFrame`, the new chat's, the
run's dock and the agent page's): a 20 px corner (`--radius-lg`) with its hairline, 8 px inside; the box is one line at
first (the `rows` option is gone) and grows with its text, its words on the left edge of the attach button's icon; then
one bar, the attach button at the left (a round ghost button now), the pills, the round send of 32 px at the right,
quiet in the third surface and the muted ink while the box holds no text (`data-empty` on the frame, repainted on every
change of the text, a pick of the slash menu included: `wireComposer` takes an `edited()` that also keeps the draft
after a pick, which it did not). On the new chat's bar the mode stays at the left by the attach button and the agent,
the model, the reasoning and the approvals go to the right. The pills are round (`--radius-pill`) and on no ground at
rest, a tint under the pointer or while open, no hairlines between them, an icon of 16 px, the value a breath away, a
chevron of 12. The bar is a size container (`container: composer-bar`): at 600 px and less the mode's and the
reasoning's names fold to their icons, at 440 and less the chevrons go and the agent's name with them, and the model's
pill is the one that shrinks, its name giving way with an ellipsis; so at a phone's 390 the bar holds the attach button,
the mode, the agent, the model, the reasoning, the approvals and the send on one line. YOLO is the warn ink on the
Options pill's open lock and word, no fill and no border (a warn tint only under the pointer). The agent pick, a native
select whose width followed its longest option (the wide gap before its chevron), is a pill like the others: the agent's
still face and name, a menu of Dot and the agents with their roles. Keep going's note moved out of the frame to one line
under it (`.composer-stack`, `.composer-note`), in fewer words. README: the New chat passage, the controls. Tests:
`test_composer.py` measures the bar with every pill out (an agent, a model with levels, YOLO) at 1280, 800, 560 and 390
px: one line 32 px high, every control on one middle, nothing past the right edge, the mode's name folded below 600,
YOLO without a fill; `test_agents.py` reads the agents from the pill's menu.

**What.** On the research of 2026-10-12 (Vercel's chatbot `multimodal-input.tsx`, Open WebUI's `MessageInput.svelte`,
assistant-ui's Composer: radii of 16 to 24 px, a one-line box that grows, controls of 28 to 32 px, a round send in the
ink with a quiet disabled state): a rounder frame with a hairline, a box of one line that grows, one bar with attach and
the mode at the left and the agent, the model with its mark, the effort, the approvals and the send at the right. The
bar never wraps: as the frame narrows the effort and the mode fold to their icons, the model's name gives way with an
ellipsis, the agent's name goes; the approvals and the send never move. YOLO is a quiet pill, a warn dot and the word,
no fill. The goal's hint is one line under the frame, its sentence in the pill's menu. The run's dock and the agent's
page take the same frame. Tests: `test_composer.py` measures the bar on one line at 1280, 800, 560 and 390 px; the tour.

### 116. The deep review: every part of the code read once more, and fixed

**Status: done.** Merged as `0304257` (PR #138, MYP-256 Done).

**What.** Four readers, each on a part of the code, asked for real defects only (wrong behaviour, crashes, races,
swallowed errors, leaks, holes in the policy or the auth): the agent and the model calls; the tools, the policy, the
browser, the desktop and the web; the server, the store, the scheduler and the desktop shell; the interface's code after
items 113 to 115. Every finding is checked by hand against the code before it is fixed here; what cannot be fixed here
is written down. Then the whole: the gate, the offline smoke suite, the eval check, the tour read by eye, the daemon
restarted. Findings as built.

**Findings, as built (2026-10-12).** Four readings, every finding checked against the code by hand before it was
changed. (1) The tools and the policy. The shell guard judged a line by its words, so a step could hide from it: in a
substitution inside quotes (`echo "$(rm -rf ~/Documents)"`), behind a `cd`, a variable, a comment with an apostrophe, a
here-document or an `echo` fed to a shell, a wrapper with option values (`bash -lc`, `env -S`, `timeout -s KILL 9`), a
link the line made, a second root of `find`, `xargs -I`, `fd -x`, `rg --pre`, `awk`'s `system()` or a zsh glob
qualifier; commands it took as reading wrote or ran code (`sort -o`, `sed w`, `sysctl -w`, `yq -i`, `xxd -r`, `open` of
a script); and a protected location in another spelling (`~/.SSH`, `~/.s?h`, `"$HOME"/.ssh`, `~/.{ssh,aws}`) was not
one. `policy/shell.py` is written again: it reads the line as the shell does (comments, `$'…'`, here-documents,
arithmetic, the commands substituted into it, each judged as a command of its own), then judges its simple commands in
order with what the line set up before them (the directories a `cd` may have moved it to, its variables, the links it
made); code it cannot read (a command named by a substitution, `eval` of one, a line it cannot parse) is a destructive
step outside the workspace, which every preset asks about, and paths are compared without case, as the Mac's disk does.
`tests/unit/test_shell_policy.py` holds the attacks (each asks, or is refused at a protected location) and the everyday
lines (each stays allowed). Typing into a terminal is judged by the line it runs: the terminal tools and the desktop
share one model of the line editor (Enter, Ctrl-C, Ctrl-U and Delete; any other control key leaves a line Dot cannot
tell, and running that asks); the desktop keeps what it typed into each terminal application, so a command typed in
parts is judged whole when Return goes in, and a Return on a line Dot cannot tell (one it has not typed since the line
was empty, or after a click, a shortcut or a drag) asks; a terminal session's later lines are judged from the home
folder, since its shell may have moved. A JXA script that types into a terminal (`doScript`, `writeText`) is judged by
its line. What a shell command printed after it reached the network (`curl`, `ssh`, `git clone`, `gh`) and what a
terminal program prints mark the run untrusted. A host name with a label over 63 characters ended a fetch as an internal
error. (2) The agent and the model calls. A step that ran past `model_call_timeout_s`, an SSL record error, a body that
is not JSON and a missing API key in a side call (the summary, the goal check, vision, an aside) ended the run as an
internal error; each is a provider failure now. A run of a team waited for a slot its parent held, so with every slot
taken parent and child waited for each other for good; it runs outside the limit, as a delegated run does. A second stop
while a run's end was being recorded skipped the record. An approval stayed pending when its run was stopped between the
request and the wait; two decisions at once both succeeded, and an approval that met its deadline acted expired while
approved was stored; the second decision is a conflict now and the run acts on what is stored. A run that failed between
its record and its task stayed queued for good; it ends failed. The note written with an approval reached the model only
when the approved call succeeded, which is why the approval smoke test failed once in item 115's run (the call failed
under load, the model called again and that approval had no note); a failed call carries it too. An Ollama error inside
a stream is masked like any provider body. (3) The server, the store and the scheduler. `DELETE
/api/skills/learned/%2E%2E` removed Dot's home folder: a learned skill's name has to be one folder of its own. The push
watcher recursed once per idle second and died, unnoticed, after about 16 minutes; it loops, outlives a push that fails,
and its end is logged. Four writes of the store (changing an agent or its look, deleting an agent or a campaign) did not
take the write lock and could fail with "database is locked". One schedule's unexpected error ended the scheduler and
the daemon with it; that schedule is marked as failing to fire now. An `UndoError` answered 500 (409 now); an `at` in
year 1 answered 500; the secrets' `.env` was rewritten in place (now through a temporary file); two browser settings
changed at once kept one; deleting chats raced a run starting on one of them; the desktop shell wrote `http://::1:8765`
for an IPv6 host. (4) The interface after items 113 to 115. Enter in the note of an approval approved it (only a
question's now); editing an agent dropped its tools; a follow-up of an agent's run lost the agent, and sent a reasoning
level the model does not offer (422); any run's event wiped what was typed on an agent's page; `/btw` emptied the field
without telling the composer; the send button ran its handler twice per click; a switch of the System page could stay
out of step after a failure; an input method's Enter sent the message; the jump-to-latest button covered the slash menu;
a click during the opening's fade flashed it; the composer's panels took neither the focus nor the arrow keys; a
campaign's page built its buttons again at every event while a session worked, so a press on Stop could be lost; and
every screenshot shown stayed in memory while the window was open (a page change lets them go). Written down, not
changed: the guard follows neither what a recursive command does inside the folders it walks nor the code of a script,
nor `CDPATH` or GNU sed's `e`; a quoted glob such as `'.*'` run from the home folder is refused as a protected location
(a false alarm on the safe side); a schedule without its own time zone keeps the zone the daemon started in, and the
next firing worked out in it, when the Mac's zone changes, until a restart and one firing; a payment's cap reads the
amount the page labels; a page fetches its screenshots as it opens, not as they come into view; the web UI needs Safari
17.5 (`light-dark()`), as the README says now. (5) The whole: the gate (903 unit tests), the offline smoke suite of the
interface with the campaign and terminal tests (84 passed, none failed), `dotx eval check`, `cargo check` of the desktop
shell, and the tour's 90 shots read by eye, which show nothing changed where nothing was meant to change.

## Part 16: helpers with their parent's rights, Chrome behind, faster models, a calm run page (planned 2026-10-12)

The owner's sixteenth word, on 2026-10-12, during part 15's review, with three screenshots (a delegated run saying that
clicks in the browser were restricted, "read-only mode"; the run page's top bar: the state, the start, the model and its
reasoning, YOLO, Goal mode, the panel's switch, Cancel, the running count, the connection's dot and the emergency stop;
T3 Code's window with its sidebar, its chat and its panel): why read-only, a yolo run should have every right, find the
problems and fix them; Chrome keeps bouncing in the Dock and coming to the front, do it in the background if it can be
done; OpenCode Zen's free models should be in the list; whatever makes the OpenCode models faster, and Claude's; the top
bar's area is not needed: Cancel takes the send button's place while a session works and becomes send again when a new
message is typed, the emergency stop moves to a better place, the top is cleared; and the look of T3 Code, its chat, its
sidebar and its panel, clean and simple, adapted to Dot, from its repository if need be.

**Status (2026-10-12): built.** Items 117 to 121 merged in a day (`e51f9c1`, `6dfbf07`, `acfc56a`, `6c1a7b3`,
`4f2b0cd`), each behind the gate and its smoke tests, and those that changed the pages with their screenshot tour read.
The "read-only mode" the owner saw was a yolo task run that had started its helper as research; a helper takes its
parent's mode now, so it clicks, types and runs as its parent may. Every tab Dot opens in Chrome is a background target,
and a tab a page opens itself gives the front back to the application that had it (with Accessibility). OpenCode Go's
Messages route sends the cache mark (Claude Haiku 5.5 read 10,383 tokens of a repeated prompt from the cache with it),
Claude on Go reaches that route instead of a refusal, and the models Go serves free say Free in the picker; Zen's free
models stay out of the list, because OpenCode serves them only to its own client (every call from another answers 403
`FreeTierError`) and Dot does not pose as it. The run page's head keeps the face, the name and the panel's switch, the
composer's send is the run's Stop while it works, and the emergency stop sits in the sidebar's foot. The look follows T3
Code's: a sidebar a tone darker without a line, a chat's age at the right of its row, tool steps as quiet lines, the
composer on a corner of its own with a soft shadow, the panel one card. The daemon was restarted on `4f2b0cd` with no
run active. What waits: the desktop app rebuilt by the owner, as after part 15 (`cd desktop && npm run build`); its
pages come from the daemon, so part 16 reaches it without a rebuild.

**Status (2026-10-12): planned.** What is built today, and what the readings found. (117) `delegate` takes a `mode`
(`tools/delegate.py`): "task lets it act like you; research keeps it read-only". A task run in yolo started its helper
as research; a research run has no `browser_act` (`tools_for_mode`, `ACTING_MODES`) and the policy refuses every kind
outside `RESEARCH_KINDS` (`policy/engine.py mode_gate`), so the helper could open and read pages but not click them, and
said so. The same run met two more failures: `web_extract` ended with a protocol error when the fast model answered a
field with a plain value instead of a value and a quote (`web/extract.py checked_extraction`), and `mark_plan_step`
refused evidence over 400 characters (`agent/plan.py EVIDENCE_MAX_CHARS`), a step lost each time. (118) Dot's own Chrome
is headed (a headless Chrome meets bot walls) and comes to the front when it starts; with `raise_window` off, the
default, the application that had the front gets it back once (item 97). Measured today on Chrome 154 with probes on
throwaway profiles: `context.new_page()` takes the front, and Dot's own Chrome opens every thread's tab and every quiet
read's scratch tab with it; a target created through CDP with `background: true` does not, nor do `goto`, closing a tab,
a screencast or a dialog; a tab opened in the background is `visible` to its page, paints its screencast at the visible
tab's rate and gives a screenshot in 0.04 s; a tab the page opens itself (a `target=_blank` link clicked, `window.open`)
takes the front. The owner's log counts 42 starts of Chrome. A headless Chrome is a background-only process on macOS,
with no Dock tile, so the test suites' Chromes are not what bounces. (119) OpenCode Zen's list
(`https://opencode.ai/zen/v1/models`) has 45 models, 10 of them free (`big-pickle`, `jev-1.13-free`,
`ling-3.1-flash-free`, `longcat-2.5-preview-free`, `mimo-v2.6-flash-free`, `muse-spark-1.2-contributor-free`,
`muse-spark-1.3-contributor-free`, `nemotron-3.5-lightning-free`, `space-bunny-free`, `step-5-preview-free`); each
answers a call from outside OpenCode's own client with 403 `FreeTierError` ("OpenCode's free tier can only be used from
within OpenCode"), with the key or without. OpenCode Go's list, the one Dot shows, holds two free models that answer Dot
(`longcat-2.5-preview-free`, `step-5-preview-free`), unmarked among 38. Go's Messages route (Claude Haiku 5.5, Qwen,
MiniMax) takes cache marks now: of two calls with the same 10k-token prompt to Haiku 5.5, the second read 10,383 tokens
from the cache when marked and none when not, and Dot sends no mark there (`llm/opencode_go.py _MESSAGES_OPTIONS`,
written when the gateway took none); Qwen caches by itself and with the mark, MiniMax takes the mark; the Chat route
caches on its own (DeepSeek: 7,552 of 7,614 prompt tokens on the second call). Anthropic's own API is called with the
automatic cache mark already, and one client per provider keeps its connections for 60 s. (120) The run page's top bar
holds the face, the title, the state and the start; then the agent, the model and its reasoning, the approvals (YOLO),
Goal mode, the campaign, the panel's switch, Continue and Cancel (`views/run.js` into `stage.js`'s slots); the frame's
right holds the approvals waiting, the runs in progress, the connection's dot and the emergency stop, a red square on
every page (`shell.js buildIndicators`). The sidebar's Waiting and Working sections count the same runs, and the panel's
details list the mode, the model, the reasoning, the approvals and Goal mode. While a run works, a message typed is sent
as a steer under the label Steer. (121) T3 Code's look, read from its repository (MIT, `apps/web/src`): a sidebar of 256
px a tone darker than the page, its rows quiet (the project small, the title in the body size, the state or the time on
the right in tabular figures); one chat lane of 46 rem; the user's messages in a soft bubble, the agent's without one; a
turn's tools as one line with an icon ("Ran 3 commands"); hairlines rather than boxes; the composer a white card on a 24
px corner with a soft shadow, whose round send button becomes a red Stop while the agent works with an empty box; the
panel a floating card of 280 px with hairline sections; the details revealed on hover.

**The shape of this part (the decisions).** First the helper's rights: a delegated run takes its parent's mode, which
the model can no longer narrow; a research run the owner starts stays read-only, as its name says. The two other
failures of that run are fixed with it. Then Chrome behind: every tab Dot opens in its own Chrome is created in the
background, and when a page opens a tab of its own, the front goes back to the application that had it. Then the models:
the cache mark on Go's Messages route, which makes Claude on Go faster and cheaper from a run's second step, and the
free models marked in the picker; Zen's free models stay out of the list, because OpenCode refuses them to any client
but its own and Dot does not pretend to be OpenCode. Then the run page's head: Stop in the send button's place, the
emergency stop at the sidebar's foot, and nothing at the top but the title and the panel's switch. Last, the calm look
of T3 Code adapted to Dot's tokens, faces and mark.

### How to start part 16 (the handoff prompt)

> Read ROADMAP.md part 16 and the status table of part 9 (rows 117 to 121). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch
> the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run; the
> owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is English.
> Every UI item ends with the screenshot tour read by eye.

### 117. The helper keeps its parent's rights: a delegated run takes its parent's mode

**Status: done.** Merged as `e51f9c1` (PR #140, MYP-257 Done).

**As built (2026-10-12).** `DelegateArgs` has no `mode` now: `delegate` starts its run in `default_child_mode(ctx.mode)`
(a research run's helper is research, every other run's a task), as `message_agent` did already, and the run carries its
parent's approvals as before, so a helper of a task run in yolo has every right its parent has; the run manager still
refuses a research run a wider helper. The tool's description and the system prompt's delegation section say that the
agent uses your tools, your mode and your model. `web_extract` reads a field's answer through `pair_of`: a value and a
quote as before; a plain value (a number too) as the value without a quote; the pair sent as JSON text as the pair; a
field left out as empty. A value without a quote is unverified, so the result marks it as a claim to treat as one, where
the whole extraction used to end on a protocol error. The evidence of `update_plan` and `mark_plan_step` is cut at 400
characters with an ellipsis (`clipped_evidence`) and the field's description says so. The README's delegation,
extraction and plan passages follow. Tests: `test_delegate.py` (the mode follows the parent's and cannot be named),
`test_crawl.py` (the tolerant reading), `test_plan_evidence.py` (the cut); 906 unit tests; the delegate, team, helpers,
plan-branches and undo smoke tests (18 passed, the three that call a live model left out); the eval check.

**What.** `delegate` loses its `mode`: a delegated run takes its parent's (a task run's helper is a task run, a research
run's helper research, and the run manager still refuses a research run a wider helper), so a model can no longer start
its helper read-only in a run the owner gave every right; `message_agent` does the same already. The tool's description
and the system prompt's delegation section say that the agent uses your tools, your mode and your model. A research run
the owner starts stays read-only. `web_extract` takes a field the fast model answered with a plain value as that value,
unverified and without a quote, and an object sent as a JSON string as the object, instead of failing the whole
extraction; the evidence of `mark_plan_step` and `update_plan` is clipped at 400 characters with an ellipsis instead of
refusing the call. Unit tests hold the three.

### 118. Chrome behind: every tab opened in the background, the front given back

**Status: done.** Merged as `6dfbf07` (PR #141, MYP-258 Done).

**As built (2026-10-12).** Every tab Dot opens is made by `_background_tab`: a target created through a browser CDP
session with `background: true` (Playwright 1.63 hands the browser of a persistent context too). A quiet read's scratch
tab is always made so, and a thread's tab (`_new_tab`) too unless `raise_window` is on, in Dot's own Chrome and in the
user's alike, so the tab the user works in keeps its place as well. Each such tab is first addressed `about:blank#dot-`
and a random mark, waited for by that address and then sent to a plain blank page: tabs made at the same time (a crawl
reads pages side by side) were otherwise all handed the first new page, which hung the crawl's smoke test until the mark
was in (the quiet reads in the user's Chrome, made so since item 98, had the same race, and could even be handed a tab
the user opened at that moment). A tab the page opens itself still takes the front: each thread's step notes the
application in front as it begins (`_note_front`, one accessibility call), and when a popup of ours is adopted,
`return_front` waits up to two seconds for Dot's own Chrome (its process, from the profile's lock) to take the front and
gives the front back to that application; a front that went anywhere else, or that was Dot's Chrome already, stays where
it is. That needs Accessibility, which the program that runs Dot has when desktop control works; without it the log says
so once and nothing is watched. The start still takes the front once and gives it back (item 97). The README's browser
passages follow. Measured before and after on a visible Chrome with Finder in front: with the tabs opened as before, a
new tab and a quiet read each brought Chrome to the front; with the change neither does, and a link to a new tab gives
the front back. `tests/smoke/test_chrome_front.py` holds both on a throwaway profile (the give-back skipped without
Accessibility); with it the browser smoke set passes (71 tests), and so do the 906 unit tests.

**What.** In Dot's own Chrome every new tab, a thread's and a quiet read's, is a target created in the background
through CDP, as the user's Chrome has it already, unless `raise_window` is on; Playwright hands the browser of a
persistent context too. A tab a page opens itself takes the front by Chrome's own doing: when that happens during an
action, and another application had the front before it, the front goes back to that application (item 97's give-back,
kept to Dot's own Chrome by its process, so a switch the owner makes is not undone). The start of Chrome still takes the
front once, since macOS starts an application in front (a start without a window, `--no-startup-window`, makes
Playwright wait for a first page that never comes), and gives it back as before. A smoke test on a throwaway profile
holds that opening tabs, reading pages and a page's own tab leave the front where it was.

### 119. The models: the prompt cache on OpenCode Go's Messages route, the free ones marked

**Status: done.** Merged as `acfc56a` (PR #142, MYP-259 Done).

**As built (2026-10-12).** `_MESSAGES_OPTIONS` sends the automatic cache mark (`prompt_cache=True`). Measured on Go the
same day: Claude Haiku 5.5 read a repeated prompt of ten thousand tokens from the cache only with the mark (10,383
tokens on the second call, none without it), Qwen 3.8 Flash caches by itself and with the mark, and MiniMax M3 takes the
mark; the Chat Completions route caches by itself (DeepSeek V4.1 Flash read 7,552 tokens on a second call). Reading Go's
model list for this found Claude Haiku 5.5 on it, which Dot sent to the Chat Completions route by its unknown name,
where it answered 400 (`ModelProtocolUnsupported`). The catalog has it now on the Messages route, with the facts of
OpenCode's own catalog on models.dev (pictures, a million tokens in, 128,000 out, thinking as a switch, no temperature),
and a new `claude-` model goes there by its name. The catalog follows Go's list otherwise too: `space-bunny-free` is
`space-bunny` now, and `step-5-preview-free`, `qwen3.7-max` and `qwen3.6-plus` are in it with their facts; the other new
ids answer on the route Dot picks by name (checked one by one). `ModelInfo` and the pickers' `ModelOption` carry `free`
(Go's ids that end in `-free`): the model picker's row says Free, and Settings › Models shows a free chip. Zen's free
models stay out: every call to them from a client other than OpenCode's own answered 403 `FreeTierError`, with a key and
without, and Dot does not pose as OpenCode's client. Claude on Anthropic's own API caches already, and its fast mode
stays off until the owner asks for it. The README's OpenCode Go passage says all this.
`tests/smoke/test_hosted_services.py` adds Claude on Go's Messages route to the round trip and a test that reads the
cached tokens of a second call; with the key both pass, as does the route check of `test_wire_codecs.py`; the smoke
tests of the helpers, the server and the composer pass (38, without the eleven that call a cloud model), and so do the
906 unit tests.

**What.** OpenCode Go's Messages route sends the automatic cache mark (`_MESSAGES_OPTIONS`), so Claude Haiku 5.5, Qwen
and MiniMax on Go read a run's prompt from the cache from its second step: a faster first token on a long prompt and a
tenth of the input's price. The model picker marks the models a provider serves free (Go's ids that end in `-free`) with
the word Free. Zen's free models stay out of the list, with the reason in the README's provider passage: OpenCode serves
them only to its own client. Claude on Anthropic's own API caches already; its fast mode (Opus only, a research preview
at a higher price) waits for the owner's word. A live smoke test, run when the key is there, reads the cached tokens of
a second call on Go's Messages route.

### 120. The run page's head: Stop in the send button's place, the emergency stop in the sidebar

**Status: done.** Merged as `6c1a7b3` (PR #143, MYP-260 Done).

**As built (2026-10-12).** The run page's top bar holds Dot's face and name (or a delegated run's breadcrumb) and the
panel's switch, nothing else: the state badge and the start, the chips of the agent, the model, the approvals, Goal mode
and the campaign, Cancel and Continue left it. The Details card gained the agent (a link to its page) and the campaign
with its session, so every fact the bar showed is there. `composerFrame` has `setStop(handler)`: while a handler is set
and the box holds no text, the send is a Stop (`data-stop` on the frame, the danger colour, a filled square of about a
third of the button, its name "Stop") and pressing it runs the handler; a typed word gives the send back with its own
words, and Enter never stops. The run page sets the handler, the run's cancel, while the run works and is not delegated;
a delegated run, which has no composer, has a Stop as the last word of its pill while it works. Continue is a note line
above the composer, its word kept on a phone. The frame's right keeps the approvals waiting and the connection only
while it is not live; the running pill went, since the sidebar's Working section counts the runs. The emergency stop
(`failsafeButton` in `failsafe.js`) sits in the sidebar's foot beside Settings and, so that it is never out of reach,
beside the button that shows the sidebar while the sidebar is closed (on a phone, where the sidebar is a drawer, it is
there by default). The styles nothing uses now (`.stage-sub`, `.stage-time`, `.pill.run`, `.pill.quiet`) went. The
README's sidebar, run page and emergency stop passages follow. `tests/smoke/test_run_stop.py` holds the bar, the
emergency stop's place, Stop and Steer on an empty and a typed box, and a Stop that cancels a scripted run waiting in
the shell, wide and on a phone in both schemes; `test_activity.py` reads the Stop of a working parent and of its
delegated run's pill, and `test_run_chrome.py` the bar's one tool; the smoke tests of the run page, the activity, the
composer, the agents and the face pass (14), the tour passes and was read by eye at both widths in both schemes, and so
do the 906 unit tests.

**What.** The run page's top bar keeps the face, the title (with the parent's crumb for a delegated run) and the panel's
switch; the state, the start, the agent, the model and its reasoning, the approvals, Goal mode and the campaign live in
the panel's details, which list most of them already. Cancel leaves the bar: while the run works and the box is empty,
the composer's send button is a Stop, a round button in the danger colour with a square, that cancels the run; once a
word is typed it is the send button again and the message goes as a steer; a run that has ended is followed up as
before. Continue, for a run the daemon cut off, is a line above the composer. A delegated run, which has no composer,
has its Stop in its closing pill. The frame's right keeps only what needs the owner: the approvals waiting and the
connection while it is not live; the running count goes (the sidebar's Working section counts the runs), and the
emergency stop moves to the sidebar's foot beside Settings, a red square with its words in its tooltip as before. The
run page's smoke tests follow the controls; the tour is read by eye.

### 121. The calm look, after T3 Code: the sidebar, the chat, the composer and the panel

**Status: done.** Merged as `4f2b0cd` (PR #144, MYP-261 Done).

**As built (2026-10-12).** Read from T3 Code's repository (MIT) and the owner's screenshot of it, through Dot's tokens
and in both schemes. The sidebar is a tone darker than the page with no line between them; a chat's row carries its last
activity at the right in figures of one width (`age` in `dom.js`: "now", "5m", "3h", "2d", "3w", then the day; read
again every minute while the page rests, the date in its tooltip). A tool step is one quiet line with its icon instead
of a box, its body hanging under it on a hairline once opened, and the steps of a group sit on a line of 1 px instead of
2. The composer has a corner of its own, 24 px (`--radius-composer`), and a soft shadow that falls below it
(`--shadow-composer`) on every page, not only in the run's dock. The run's panel is one card on that corner whose
sections (Dot's computer, Details, Cost, Plan, Actions, Screen) sit on hairlines, the head of an open section without a
line under it; as a drawer it is the drawer itself. Where a pointer can hover, when the answer came and how long the run
took wait for a second look; the Copy button and the outcome stay in sight (with Copy hidden too, the outcome sat adrift
after the button's empty place). Dot keeps its faces, its mark, its type and the colours of its states. The run chrome's
smoke test scripts sixty steps instead of thirty, since thirty quiet lines no longer make a page twice the window's
height. The README's look passages follow. 117 smoke tests that read these parts of the page pass, the tour passes and
was read by eye at both widths in both schemes, and so do the 906 unit tests.

**What.** T3 Code's look, read from its repository (MIT) and adapted to Dot's tokens and both schemes, through the
tokens first: the sidebar a tone darker than the page with no border and quiet rows (the title in the body size, the
time or the state on the right in tabular figures, a hover a step lighter); the chat in one lane; the owner's messages
in a soft bubble and Dot's without one; a step's tools as one quiet line with an icon; hairlines rather than boxes; the
composer a card on a larger corner with a soft shadow; the panel's sections on hairlines; the details that only matter
on a second look revealed on hover. Dot keeps its own: the faces, the mark, the type system and the colours of its
states. The tour is read by eye at both widths and in both schemes.

## Part 17: the sidebar says what works, a helper folded in the chat (planned 2026-10-12)

The owner's seventeenth word, on 2026-10-12, after part 16: while helpers work, and an ordinary session too, the left
menu could say something like "working …m", as T3 Code does; and by default a helper's content should be folded in the
chat. Every word of it researched as written, and applied as exactly as it can be.

**Status (2026-10-12): built.** Items 122 and 123 merged in a day (`10fd83f`, `a2de64d`), each behind the gate and its
smoke tests, their screenshots read at both widths in both schemes. The sidebar says what works as T3 Code's does: where
a row's age sits at rest, a chat whose latest run works and every run of the Working section, a delegated run under its
parent included, show a dashed circle, Working and the time since the run was asked for ("12s", "4m", "1h 5m"), read
every second; a run that waits for the owner keeps its amber dot and its age. In a run's chat a helper is its row,
closed from its start, its last line and its time in sight, the time counting on every second; an approval it asks for
opens the row until the owner has decided, and a row the owner never opened or closed by hand folds again then and at
the helper's end. A helper cannot ask the owner a question (`ask_user` is not offered to a delegated run), so the
question the plan counted on to open a row does not exist. Not built, from the readings of T3 Code: the other labels of
its sidebar's status slot (Approval, Input, Done) and the closed group that gathers two subagents or more ("3
subagents", "1 working · 2 done"). The daemon was restarted on `a2de64d` with no run active; the desktop app takes its
pages from the daemon, so part 17 reaches it without a rebuild.

**Status (2026-10-12): planned.** What is built today, and what the readings found. Dot's sidebar lists the chats under
the days, each with a live dot (pulsing while its latest run works, amber while it waits for the owner, red when it
failed) and, at the right, its last activity in the fewest characters (item 121, read again every minute). Above them
the Working section lists the runs that work now (`GET /activity`), a run's helpers nested under it with their steps
(item 68); no row says how long its run has worked. The chats' list carries no start of a run:
`ThreadSummary.updated_at` is the later of the last message and the latest run's start, and a steer moves it. In a run's
chat a helper is a row (item 82: its face with the state at the corner, its goal over the last thing it did, the time it
has taken, the way to its own page and a chevron), a `details` that opens as the helper starts (`timeline.js
childScope`, `open: true`), so its every step, message and answer stand in the parent's conversation while it works; it
folds itself when it ends done and the reader has not touched it, and stays open when it failed. A question or an
approval a helper asks lands as a card inside that panel. T3 Code, read from its repository (MIT, `apps/web/src`): a row
of its sidebar shows its state where the time sits at rest (`Sidebar.tsx`); while the thread works, a dashed circle
(Lucide's `CircleDashed`), the word Working and the time since the current work was asked for, in tabular figures, read
again every second and counted in the fewest units ("12s", "4m", "1h 5m": `formatWorkingDurationLabel` in
`Sidebar.logic.ts`), with no shimmer ("a label that animates forever is noise in a sidebar full of them"); a pending
approval or question wins over Working, and a thread at rest shows its time. Its chat draws a helper as one row that
never holds the helper's work (`V2LifecycleRow.tsx`, `SubagentTimelineLink`): the helper's face with its state, its
title, one line of its progress (its result once it ended), its elapsed time and the way into its own thread; two or
more helpers of a turn fold into one row, "3 subagents" over a count of their states ("1 working · 2 done"), closed
until opened (`MessagesTimeline.tsx`, `V2SubagentGroup`).

**The shape of this part (the decisions).** First the sidebar: a chat whose latest run works, and every run of the
Working section with its helpers, says Working and how long, in T3 Code's words, units and mark, where the time sits at
rest and in the colour of Dot's running state; a run that waits for the owner keeps its amber dot and its time, since
the Waiting section names what it waits for. The chats' list learns when its latest run was asked for. Then the chat: a
helper's row is closed from its start, its last line and its time saying what it does; an approval or a question it asks
opens it, and the rows it sits in, because what needs the owner never hides in a fold; at its end it folds again unless
the owner opened or closed it by hand. Dot's helpers keep their rows one under the other: T3 Code's group of several is
not built.

### How to start part 17 (the handoff prompt)

> Read ROADMAP.md part 17 and the status table of part 9 (rows 122 and 123). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch
> the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run; the
> owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is English.
> Every UI item ends with the screenshot tour read by eye.

### 122. The sidebar says what works: Working and its time on a chat's row and a working run's

**Status: done.** Merged as `10fd83f` (PR #147, MYP-262 Done).

**As built (2026-10-12).** Where a row's age sits at rest, `sidebar.js workingSlot` shows Lucide's dashed circle
(`icons.js circleDashed`, the paths of Lucide 1.8.0), the word Working and `workingFor` (`dom.js`: "12s", "4m", "1h 5m",
T3 Code's units), read again every second by one ticker over the sidebar, in figures of one width, the running state's
colour (`--accent`) and the label's weight; the circle stands still. A chat shows it while its latest run is queued or
running, counted from `ThreadSummary.latest_created_at`, new: the latest run's `created_at`, read by the same query. A
run of the Working section shows it from its row's `since`, which is the run's `created_at`, the delegated runs nested
under their parent included. A schedule due there, every row of the Waiting section and a chat whose run waits for an
answer stay as they were (an amber dot and an age for the chat); the rows keep their dot or face at the left. The
README's sidebar passage says it. `tests/smoke/test_working_rows.py` starts a chat that works, a run it delegates and a
chat that asks a question, on the scripted model, and reads the sidebar at both widths in both schemes: the two runs of
the Working section and the working chat say Working with a time in T3 Code's units, the asking chat keeps its amber dot
and its age, the seconds move while the page rests, and once the runs are cancelled every chat shows its age again. 27
smoke tests pass (the new one, the activity sections, the server and the tour), their shots read by eye at both widths
in both schemes, and so do the 906 unit tests.

**What.** As T3 Code's sidebar does: where a row's time sits at rest, a chat whose latest run is queued or running shows
a dashed circle, the word Working and the time since that run was asked for ("12s", "4m", "1h 5m"), read again every
second; so does every run of the Working section, a helper under its parent included (those rows have no time today, so
the state takes their right edge). The colour is Dot's running state's, the figures are tabular, and nothing shimmers. A
run that waits for the owner keeps its amber dot and its time, and a schedule due in the Working section says when it is
due, as before. `ThreadSummary` gains `latest_created_at`, when the chat's latest run was created, which a steer does
not move. A smoke test reads the rows at both widths in both schemes and sees the figure move; the tour is read by eye.

### 123. A helper folded in the chat: its row until the owner opens it

**Status: done.** Merged as `a2de64d` (PR #148, MYP-263 Done).

**As built (2026-10-12).** A delegated run's row in the parent's conversation (`timeline.js childScope`) is a `details`
that starts closed: its face and state, its goal over its last line, its time and the way to its page show, and its
steps, messages and answer wait behind the chevron. While the parent works, the run page (`run.js`) calls the timeline's
new `tick(now)` every second and after each event it hands the timeline, so a page opened later shows the time at once;
`tick` writes into the row of each delegated run that has not ended the time since it began, as T3 Code counts a working
subagent's. Before, the time stood at the run's last event, a few milliseconds for a helper that waits in a long shell
command. `reveal` opens the row of a run that asks the reader for an approval (an `approval_requested` in its scope) and
the rows it sits in, each counting what waits inside it; `unreveal`, on the decision, closes each that has nothing left
waiting and that the reader never opened or closed by hand (the row's `touched`, set by a click on its summary). At its
end such a row closes again whatever the outcome; before, a run that failed stayed open. A decision already handled
leaves the cards, so an echo of it cannot count twice. The plan's question that opens a row does not exist: a delegated
run is not offered `ask_user` (`loop._NOT_FOR_DELEGATED_RUNS`; nobody waits in its conversation), so only its approvals
reach the reader, and the README's Delegation passage now says so beside the chat passage's fold.
`tests/smoke/test_helper_fold.py`: under the cautious preset a parent in ask mode waits in a shell command while two
runs it delegated work, one asking to write a file, the other waiting in a shell command; at both widths in both schemes
the waiting helper's row is closed, its last line in sight, its time counting on (in seconds at once on a page opened
later) and its steps hidden, and the asking helper's row is open on the approval's card; denied, that helper finishes
and its row closes; opened by hand, the waiting helper's row shows its steps and stays open after it is cancelled. 906
unit tests and 30 smoke tests pass (the new one, the run page's, the activity sections, agents, the team, delegation,
the working rows and the tour among them); the shots were read by eye.

**What.** In a run's chat a delegated run (a helper, or the run of an agent the run wrote to) is its row, closed from
the moment it starts: its face and state, its goal over the last thing it did, the time it has taken and the way to its
own page; the chevron opens its steps, messages and answer as today. An approval or a question the helper asks opens its
row and the rows it sits in, so the card is in sight; when the helper ends, a row the owner never opened or closed by
hand folds again. The README's chat passage follows, a smoke test opens and answers a helper's question, and the tour is
read by eye.

## Part 18: the home's first screen, and what the failed runs showed (planned 2026-10-12)

The owner's eighteenth word, on 2026-10-12, after part 17, with a screenshot of the home: at the first opening the place
to write could stand exactly in the middle, across and down; the old chats under it could fold, or show one or two; and
any other problem found is to be fixed.

**Status (2026-10-12): built.** Items 124 and 125 merged in a day (`41ec542`, `36c0eeb`), each behind the gate and its
tests, the home's screenshots read at three sizes in both schemes. At the first opening Dot's greeting and the composer
stand in the middle of the space under the top bar, across and down, however long the day is: the upper row of the
home's grid is half of what they leave of that space, their height measured as it changes. The Today strip follows under
them in the page, two rows a section with how many more the Activity page holds, and each section folds at its title,
kept by that browser. The plan's strip of no height, built first, let the sidebar and the top bar scroll away with it,
which the new test caught; the strip now counts in the page's height. The journal names the day it tells, or the days
after a daemon that was off, and `browser_snapshot`'s description says it takes no arguments; whether the models stop
giving it some shows in the next reading of the refused calls. From the readings, nothing new for the two crashes, met
by daemons started before items 107 and 66 and not come back since, nor for the campaign in attach mode, whose sessions
need Chrome's Allow while the owner is there. The daemon was restarted on `36c0eeb` with no run active; the desktop app
takes its pages from the daemon, so part 18 reaches it without a rebuild.

**Status (2026-10-12): planned.** What is built today, and what the readings found. The home (`views/runs.js mount`,
`presence.js`) is a grid whose rows are `1fr auto 3fr` (`app.css`): what waits for an answer as cards in the first,
Dot's face, greeting and line of now with the composer (`.home-start`) in the second, the Today strip in the third. The
grid has no height of its own (the stage only has a least height), so its rows take their content's: the space above the
composer is a third of the strip's height, and a day with many rows pushes the composer down the window. Measured on the
scripted model at 1280 by 800 with nine runs ended, a journal and a lesson, the middle of the greeting and the composer
stood 17 px above the middle of the space under the top bar; on the owner's screenshot, with two dozen runs ended and
their helpers, it stood near the window's foot. The strip shows five rows a section, each run with its delegated runs
nested, then "and N more in Activity", and nothing of it folds. The readings of the runs of the last two days, from the
store and the log (counts, kinds and the store's own words only): 3 runs ended as internal errors with `ssl.SSLError`
(`SSLV3_ALERT_BAD_RECORD_MAC`) on a model call through OpenCode Go, each in a daemon started before item 107 counted
that error as a transport error to retry; 5 runs ended at once with `sqlite3.OperationalError: database is locked`,
eighteen minutes before item 66 gave the store's writes their retries; since those daemons neither came back (no
`store_locked_retry` in the log, no crash). The hourly sessions of a campaign in attach mode failed at `browser_open`
because Chrome asked for its Allow while nobody was there, until the owner stopped the campaign. Twelve tool calls of
two models were refused because they gave `browser_snapshot`, which takes no arguments, a `full` or a `ref`; its
description says it shows the tab "again in full". The day's journal is written at the reflection time (03:30 by
default) of the runs that ended since the last reflection, nearly all of them the day before, and names the day it is
written: "On 2026-10-12 I worked on 22 runs" told of the 11th's.

**The shape of this part (the decisions).** First the home: the greeting and the composer stand in the middle of the
space under the top bar, across and down, however long the day is; the strip hangs under them without moving them, shows
two rows a section with "and N more in Activity" (a run without the delegated runs nested under it: the Activity page
and the sidebar keep those), and folds a section at its title, remembered by that browser. What waits for an answer
stays as cards just above the composer, as before. Then the faults the readings found: the journal names the day it
tells, and `browser_snapshot`'s description no longer offers an argument the tool does not take. The two crashes need
nothing new, and Chrome's Allow in attach mode is the owner's to give.

### How to start part 18 (the handoff prompt)

> Read ROADMAP.md part 18 and the status table of part 9 (rows 124 and 125). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch
> the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run; the
> owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is English.
> Every UI item ends with the screenshot tour read by eye.

### 124. The home's first screen: the composer in the middle, the day folded under it

**Status: done.** Merged as `41ec542` (PR #151, MYP-264 Done).

**As built (2026-10-12).** The home's grid (`app.css .home`) has the rows `minmax(L, auto) auto auto`, L being half of
what Dot's greeting and the composer (`.home-start`) leave of the space under the top bar, less the gap: the window's
height (`100dvh`) less the top bar's (`--stagebar-h`), the page's padding and their own height, which `views/runs.js
mount` keeps in `--home-start-h` on the grid as it changes (`layout.js trackHeight`, a `ResizeObserver` that
`trackStagebar` now uses too). So their middle is the middle of that space, across and down, however long the day is;
the cards of what waits for an answer sit in the upper row and move them down only once taller than it; the Today strip
follows them in the page's flow, and the window scrolls to it. The plan's grid of equal outer rows with a strip of no
height, built first, stood the composer in the middle, but the page then ended at the window's foot, so the sidebar and
the top bar, sticky inside it, scrolled away with the strip (the sidebar by 157 px at 1280 by 800, measured); the strip
now counts in the page's height. A section of the strip (`presence.js todaySections`) shows its first two rows, a run
without the runs it delegated, then "and N more in Activity", N counting every row not shown, delegated runs among them;
its title is an `h2` holding the button that folds it, with a chevron, the title and the count of its rows; the folds
are kept in `dot.homeFolds` (`prefs.js readFolds`, which the sidebar's folds now read too), and the focus stays on the
title through the repaint. The README's home passage and its line on the approvals above the composer follow.
`tests/smoke/test_home_first_screen.py`: nine runs ended, a journal note and a lesson; at 1280 by 800 and 390 by 844 in
both schemes and 1700 by 1000 in light, the middle of the greeting and the composer is within 2 px of the middle of the
space under the top bar, across and down; scrolled to the end, the top bar is at the top and the docked sidebar fills
the window; the section of the ended runs shows two rows and "and 7 more in Activity"; its title folds it, the fold
stays across a reload, and a click opens it again. `tests/smoke/test_home.py` takes its screenshots with the motion
reduced, so the opening no longer covers them. 906 unit tests; the new smoke test and 14 more around the home, the tour,
the Activity page, the working rows, the face, the run page, the agents and the helper fold passed, and the shots were
read by eye.

**What.** The home's grid gets the height of the space under the top bar and rows of equal share above and below the
greeting and the composer, which stand in its middle across and down; the Today strip takes no height in that grid, so
it hangs under the composer, and the page scrolls to what does not fit. A section of the strip shows its first two rows,
a run without its delegated runs, then "and N more in Activity"; its title is a button that folds it, with a chevron and
the count of its rows, remembered by that browser (`dot.homeFolds`). The cards of what waits for an answer stay just
above the composer and move it only when they do not fit there. The README's home passage follows; a smoke test measures
the middle at three sizes in both schemes with a long day, reads two rows a section and a fold that stays across a
reload; the home's smoke test takes its screenshots with the motion reduced, so the opening no longer covers them; the
tour is read by eye.

### 125. What the failed runs showed: the journal's day, the snapshot's arguments

**Status: done.** Merged as `36c0eeb` (PR #152, MYP-265 Done).

**As built (2026-10-12).** The journal note names the days it tells (`reflection.journal_span(since, now, zone)`): the
date of the middle of the time the reflection read, in the scheduler's zone, when that time is at most a day and a
quarter (30 hours), so the reflection at 03:30 on the 12th, which reads from 03:30 on the 11th, writes "On 2026-10-11",
one that fired late names the same day and one run by hand in the afternoon names that day; a longer time, after a
daemon that was off, is told by its first and last day, "From 2026-10-08 to 2026-10-12". `journal_text` takes that span
where it took the day the note was written. `browser_snapshot`'s description says it shows the tab again, every element
in and around the viewport with refs, and that it takes no arguments; "in full", which the refused calls answered with a
`full`, is gone. Whether the models stop giving it arguments shows in the next reading of the refused calls; no live
model was asked here. A unit test reads the span of the nightly reflection, of a late one, of one by hand and of four
days; the three calls of `journal_text` in the unit tests give it the span. The README's reflection passage says which
day the note names. 907 unit tests and the reflection's smoke test.

**What.** The journal says the day it tells: the date of the middle of the time it reads, "On 2026-10-11" for the
night's reflection at 03:30, or "From 2026-10-08 to 2026-10-12" when it reads more than a day and a quarter (a daemon
that was off). `browser_snapshot`'s description says it shows the tab again, every element in and around the viewport
with refs, and that it takes no arguments. Unit tests for the journal's day; the README's reflection passage follows.

## Part 19: the Appearance page, after T3 Code (planned 2026-10-12)

The owner's nineteenth word, on 2026-10-12, with three screenshots of T3 Code's Settings › Appearance: whether Dot can
have that page, since T3 Code is open source and much in it could be taken into Dot if it is done smartly. The word
before it, the same day, asked whether Dot's home had caught the plainness of T3 Code's; the answer (partly: a warm
palette and a busier middle) left six choices with the owner, of which this part offers the first, neutral whites, as a
theme to pick, and leaves the default as it is.

**Status (2026-10-12): built.** Items 126 to 128 merged in a day (`c5dfc76`, `9c73d80`, `30b2434`), each behind the gate
and its tests, the Appearance tab's screenshots read on a desktop and a phone in both schemes. Settings › Appearance
holds the colour scheme as three miniatures of the window, the themes as a gallery of orbs with a theme for light and
one for dark, and the interface, the motion and the type, every setting on the page before the first paint. Themes of
one's own are made from a background, an accent and an action colour, duplicated, edited, exported, added from T3 Code's
files or VS Code's, and kept by the daemon in its home's `themes` folder for every browser. A review of item 128 against
its done criteria found a Cancel during a save that left the new theme unpicked and the daemon reading a null where the
page refuses one; both were fixed before the merge. The daemon was restarted on `30b2434` with no run active; the
desktop app takes its pages from the daemon, so part 19 reaches it without a rebuild. Export in the desktop app is not
tried: its web view has no handler for a download.

**Status (2026-10-12): planned.** What is built today. Settings › General holds the look of the browser in one card
(`views/system.js buildAppearance`): two looks (`theme.js THEMES`: Neutral, warm grey and ink, the default, and Paper,
warm paper with a serif voice and a terracotta accent), each a block of colour tokens in `themes.css` that names its
light and its dark value with `light-dark()` and says how the layout starts out (the tool steps folded, the details
panel); the colour scheme (System, Light, Dark); the density; and the switch of the faces that move. `boot.js` puts the
look, the scheme and the density on the page element before the body is parsed. `tests/unit/test_css_tokens.py` reads
the two blocks and holds every text tier to 4.5:1 (text, text-2) or 3:1 (muted, faint) on every surface and every ink to
4.5:1 on its ground, in both schemes. The type is Inter and Geist Mono in seven fixed sizes (item 77), the interface at
14 px, the composer at 15 and code at 13; the sidebar and the panel slide in 200 ms (item 85); the chat lane and the
home are 768 px wide; code blocks scroll sideways. There is no range control, no command palette (`shortcuts.js`: ⌘K
searches the runs, ⇧⌘O starts one), and no diff on any page.

What T3 Code does, read from its repository (MIT, © 2026 T3 Tools Inc.; a checkout outside this repository, read and
never run). Settings › Appearance holds the colour scheme as three cards, each a miniature of the window in the colours
it would show, System split down the middle, and the themes as a gallery of cards, each with a light and a dark orb: a
ball of the theme's canvas lit by its accent and tinted by its action colour (`ThemePreviewCircles.tsx`). A click on a
card takes the theme for both schemes, a click on an orb for that scheme only (`t3code:theme-halves:v1`); the orbs in
use are ringed and badged with a sun or a moon. A built-in theme is a literal map of 61 colour roles for light and for
dark (`packages/shared/src/themePalettes.ts`). A theme of one's own is made from two colours, a canvas and an accent, by
`createVividThemeColors` (`apps/web/src/themePalette.ts`): it steps the surfaces from the canvas in OKLCH lightness,
tints them with the accent's hue, solves the text to 7:1 against the canvas and the muted text to the strength of the
stock palette, and solves the status colours' lightness to 4.6:1 on their surfaces. Themes of one's own are made,
duplicated, edited, exported and imported (T3 Code's own file, version 1, and VS Code colour themes, whose editor
colours feed the roles), and the server can publish themes from a folder for its clients. Interface: contrast from 50 to
200 % (text and borders mixed toward the ground below 100, toward black or white above), glass opacity, the chat's width
(46rem, 72rem or full), diff colours and the composer's git context. Motion: the panels' animation, 0 to 400 ms.
Typography: the interface, prompt, code and terminal fonts and sizes with previews, font smoothing and word wrap.
Everything is kept per browser; only the theme is put on the page before the first paint.

**The shape of this part (the decisions).** Dot takes the page and its mechanics, not T3 Code's look as a whole: Neutral
stays the default, Paper stays, and so do the faces, the mark and the type system. A theme becomes what T3 Code makes of
one, a set of colours for light and one for dark, and Dot's tokens are where they land: a theme picked for light and
another for dark are written on the page element as `light-dark()` pairs, so the scheme switches as it does today, and
nothing is written when Neutral or Paper is picked for both. T3 Code's built-in palettes are ported as data and mapped
to Dot's tokens, with Dot's rule for every text tier on every surface solved the way T3 Code solves its text; its own
look comes as Clean and T3 Chat's as Rose, since those two names are T3's. What Dot has no use for stays out: glass (Dot
draws no translucent surface), diff colours (no page shows a diff), the environment's artwork and the composer's git
context. A theme of one's own is kept by the daemon, in a `themes` folder in its home, rather than by one browser, so
the desktop app, the browser and the phone share it, and a theme file put there by hand or by Dot shows in the gallery:
T3 Code's published themes, made the one place themes are kept. T3 Code's file format (version 1) is Dot's, so a theme
moves between the two. Every setting is kept per browser and put on the page before the first paint, as the look is
today.

### How to start part 19 (the handoff prompt)

> Read ROADMAP.md part 19 and the status table of part 9 (rows 126 to 128). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. T3 Code's
> source is read, never run, from a checkout outside this repository, and what is ported carries its MIT notice. Never
> touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run;
> the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is
> English. Every UI item ends with the screenshot tour read by eye.

### 126. Themes, after T3 Code: a page for the look, a theme for light and one for dark

**Status: done.** Merged as `c5dfc76` (PR #157, MYP-267 Done).

**As built (2026-10-12).** Settings › Appearance (`views/appearance.js`, after General in `views/settings.js`) holds
four cards: Colour scheme, Themes, Layout and Motion. General lost the look's card, and the card's density, its two
layout switches and the faces' switch came along as Layout and Motion, which item 127 folds into its sections. A scheme
card is a miniature of the window made of spans on the tokens (`.wire-*` in `settings.css`); each pane holds its scheme
with `color-scheme`, so every `light-dark()` token inside it resolves to that side, and System clips a light and a dark
pane at the middle. A theme (`theme.js THEMES`) is a look of `themes.css` or a palette. A look's tokens are read from
the stylesheet's own rule through the CSSOM (`appearance/tokens.js lookTokens`), so `themes.css` stays the one place
they live; the palettes (`appearance/palettes.js`) are 23 of the 61 roles of T3 Code's six built-ins, taken verbatim,
which `paletteTokens` maps onto Dot's 28 colour tokens. T3 Code's action colour is Dot's accent, since T3 Code paints
its solid controls with it (its `index.css` says so): Grove's controls are ochre, Ocean's teal, Ember's rust and Iris's
magenta, as in T3 Code, and the hints say so. Each text tier is solved against the surface it reads worst on by moving
its OKLCH lightness away from it (`appearance/colour.js readableOn`, after T3 Code's solver), to 4.6:1 or 3.1:1, a tenth
above Dot's rule so that the browser's rounding never lands under it; the status inks are solved on their grounds the
same way, success taking T3 Code's emerald. The surfaces step from the card: T3 Code's accent surface where it lies
further from the page, else a step of 0.035 in OKLCH lightness. T3 Code's vivid palettes draw their cards on the page
itself, which broke Dot's rule that a dark card lies above the page (the smoke test found it in Grove, Ocean, Ember and
Iris); a dark card no lighter than the page is now a step above it. The picks (`dot.theme`, which kept its name, and
`dot.themeDark`) write the 28 tokens as `light-dark()` pairs on the page element and keep them in `dot.themeTokens`;
Neutral or Paper for both write nothing and drop the kept values. `data-theme` names the pick of the scheme on screen
and follows the system's changes, so Paper's serif voice and shapes are Paper's only while it is shown. `boot.js` checks
every kept pair against a pattern of plain colours before it sets it. The orbs (`appearance/orbs.js`) are T3 Code's: a
ball of the canvas mixed toward white or near-black, lit by two radial gradients in oklab, blurred 3 px and spread to
1.1 inside a 56 px rim whose edge is a token of each scheme in `themes.css`; a click between the orbs falls through to
the card. ⌥⌘A opens the tab and ⇧⌥⌘A steps System, Light and Dark with a toast (`shortcuts.js`, which reads the key by
`event.code`, since ⌥A types "å" on a Mac). `static/js/appearance/LICENSE` carries T3 Code's MIT licence, names the
modules ported and says Dot is not affiliated with T3 Tools Inc. `tests/smoke/test_themes.py` picks each of the eight
themes in each scheme and reads the colours the page paints through a probe, held to the rule of `test_css_tokens.py`;
picks Paper for light and Ember for dark, checks the orbs, the cards in use, the layout's note and the switch that
follows Paper, then aborts `main.js` and reloads, so that only `boot.js` runs, and reads the same colours in each
scheme; and presses the two keys. `test_controls.py` and `test_character.py` follow the settings to the new tab, and the
tour has it.

**What.** Settings gets an **Appearance** tab after General, and the look's card leaves General for it. The colour
scheme is three cards, System, Light and Dark, each a miniature of Dot's window (the sidebar and its rows, a message,
the composer and its send button) drawn on the tokens of the scheme it names, System split down the middle; the chosen
one is ringed. The themes are a gallery of cards, each with a light and a dark orb drawn as T3 Code draws them and the
theme's name: a click on the card takes the theme for both schemes, a click on an orb for that scheme only, and the orbs
in use are ringed and carry a sun or a moon. The themes are Neutral (the default) and Paper as they are, and T3 Code's
palettes ported (its own look as Clean, T3 Chat's as Rose, Grove, Ocean, Ember and Iris), their roles mapped to Dot's
tokens: the surfaces, the sidebar, the borders, the text and its tiers, the accent and what is drawn on it, the owner's
bubble and the status colours. Every text tier reads on every surface at Dot's thresholds and every ink on its ground at
4.5:1, in both schemes, solved where the port falls short as T3 Code solves its text. A theme picked for light and
another for dark are written on the page element as `light-dark()` pairs; Neutral or Paper for both leave the stylesheet
alone; Paper's serif voice follows Paper while it is shown, and the layout a look starts with follows the theme picked
for light. What is picked is kept in the browser with the values it wrote, which `boot.js` puts on the page before the
first paint. ⌘⌥⇧A cycles System, Light and Dark, and ⌘⌥A opens the tab. T3 Code's MIT notice sits beside the ported
code, and the README's look passage follows. A smoke test reads every theme's tiers and inks in both schemes in a
browser, picks a mix and reloads without a flash, and clicks through the gallery; the controls page is read in a ported
theme; the tour is read by eye.

### 127. The interface, the motion and the type

**Status: done.** Merged as `9c73d80` (PR #158, MYP-268 Done).

**As built (2026-10-12).** The tab's Layout card became **Interface** (the contrast, the chat's width, the density, the
two layout switches and their note), **Motion** gained the panels' slide, and **Type** follows them. Each setting is one
object of `appearance/settings.js` (its key, its default, the values it takes and what it puts on the page element), and
`boot.js` writes the same for every key before the first paint, the default being no key and nothing on the page. The
contrast is T3 Code's arithmetic (`appearanceContrast.ts`): the share of a tier kept below 100 (`--contrast-base`), the
push toward black or white above it (`--contrast-boost`) and a quarter of that for the lines. `themes.css` names the
theme's text tiers, `--control` and the two line colours again on the page element under `data-contrast` and mixes them
on the body, since a token cannot be worked out from itself; at 100 nothing is written, so the colours a probe reads are
the ones item 126 measured. The chat's width is a token, `--lane-w` (768 px, 1152 px or none), on the home and the run
page's column. The panels' slide is `--motion-panel`, on the docked sidebar's column, the drawer, the details panel and
its scrim, still none under reduced motion. The seven sizes of the type and their line-heights are `calc()` of
`--type-scale`, the interface's size over 14; the composer (`--composer-size`, `--composer-font`) and an answer's code
blocks (`--code-size` on `.prose pre`) have their own. A family is a stack `app.css` names (`--family-<id>`: Inter, the
system's, a serif, Geist Mono, SF Mono, Menlo) or families typed, quoted as T3 Code quotes them (`appearanceFonts.ts`,
with the typed quotes dropped and CSS's keywords quoted) and put before the stack they replace; letters, digits, spaces,
commas, quotes, dots, dashes and underscores may be typed, up to 200, and anything else is refused under the field and
not kept. Paper's voice is `var(--family-serif)`. Font smoothing off and code wrapped are attributes the stylesheets
read. A range control (`panels.js rangeSetting`; `.range` in `app.css`, a hairline track filled with the accent up to
the value under the switch's knob) draws the contrast, the slide and the sizes, and stands on `#/controls`.
`tests/smoke/test_interface_settings.py` sets every setting through its control and reads it on the page, the colours
through a canvas since a mixed colour is computed as `oklab()`: over 100 the text and the muted tier read further from
the page and under it nearer; the sidebar's transition takes 0 s and then 0.4 s; the body's family and size follow the
interface's; the previews follow the answers', the composer's and the code's; a family with a semicolon and braces is
refused and the one kept stays. With `main.js` aborted, so that only `boot.js` runs, a reload puts the same attributes
and tokens on the page; with it, the controls show the values kept; the home reads 1152 px wide, its composer in the
code's family at 18 px; set back to the defaults, nothing is left on the page element and no key is kept.

**What.** The tab's other sections, as T3 Code has them where Dot has a use for them. Interface: the contrast, 50 to 200
% with 100 as today (below 100 the text tiers and the borders are mixed toward the ground, above it toward black in
light and white in dark, as T3 Code does); the chat's width (Comfortable, 768 px as today; Wide, 1152 px; Full); and the
density, moved here with the switches of the tool steps and the details panel. Motion: the panels' slide, 0 to 400 ms
with 200 as today (0 is instant, and the system's reduced motion still wins), and the faces that move, moved here. Type:
the interface's font (Inter as today, the system's, or a family typed) and its size (13 to 17 px around today's 14,
every text token scaled with it); the answers' font (as the theme says, which is serif in Paper, the interface's, or
serif); the composer's font and size (15 px as today); the code's font (Geist Mono as today, SF Mono, Menlo, or a family
typed) and size (13 px as today), each with a preview; font smoothing (on, as today); and long lines of code wrapped
(off, as today). A range control joins the controls page. Every value is kept per browser and put on the page before the
first paint. The README follows; a smoke test sets each one and reads it on the page and after a reload; the tour is
read by eye.

### 128. Your own themes, kept by the daemon

**Status: done.** Merged as `30b2434` (PR #159, MYP-269 Done).

**As built (2026-10-12).** The daemon keeps the themes in `<home>/themes` (`core/paths.py themes_dir`), which
`ensure_layout` leaves alone: the first theme saved makes it, mode 0700. `dotagent/themes.py` checks a theme file as T3
Code's `themePalette.ts` and `environmentTheme.ts` do: version 1 or T3 Code's short form (a name, an appearance, a
canvas and an accent in hex), a name of 1 to 48 UTF-16 units, T3 Code's 61 roles and no other key, colours in the
grammar of `colour.js parseColour` (hex of 3, 4, 6 or 8 digits, `rgb()`, `oklch()`, `transparent`), a variant only for
the other appearance, and an id, the file's name, of lowercase letters, digits and dashes that no theme of Dot's or T3
Code's has; a key without a value is left out rather than null, and `managed` is true or false, as the page reads them.
A file is opened with `O_NOFOLLOW | O_NONBLOCK` and weighed with `fstat`, so a link, a pipe or a folder is skipped, as
are a file over 32 KB, the files past the 32nd and the ones past 192 KB in all; each is listed with its reason and
logged as `theme_skipped`. A theme is written whole, through a temporary file and `os.replace`, one change of the folder
at a time so that the caps are weighed against what is there. `server/routes_themes.py` serves `GET /api/themes` (the
folder, its themes and the files skipped), `PUT` and `DELETE /api/themes/{id}`; a theme goes out with the keys it has
only, since FastAPI wrote the missing ones as `null` and the page refused the themes it had just saved (the smoke test
found it). On the page, `appearance/generator.js` ports T3 Code's generator: the 61 roles from a canvas and an accent,
and a family of colours changed with the ones made with it (`withFamily`). `appearance/own.js` reads a theme file as the
daemon does, fills the roles a theme leaves out from T3 Chat's palette as T3 Code does, lays an alpha over the canvas
and maps the roles onto Dot's tokens through item 126's `paletteTokens`, so a theme of one's own holds Dot's rule as the
built-ins do; `appearance/vscode.js` maps a VS Code colour theme's workbench colours as T3 Code's `vscodeThemeImport.ts`
does. `theme.js` holds the themes of one's own beside the built-ins: until the daemon's list arrives a pick it does not
know is pending and the values `boot.js` wrote stay; a theme deleted gives its schemes back to Neutral; and while the
editor is open the page wears the palette being changed (`previewTokens`, `data-theme="draft"`) and the scheme's
controls wait. The editor (`views/themeEditor.js`) has T3 Code's Background and Accent, an **Action** field of Dot's
and, in a fold, the families (Sidebar, Surface, Highlight surface, Border, Input border, Text, Muted text, Message
surface, Error and Warning), beside a sample of the chat. T3 Code's generator turns the action colour 50° of hue away
from the accent, which painted Dot's buttons purple under a blue accent, so the action is set on its own and kept when
the palette is made again. Create starts from the palettes of the themes in use, as T3 Code's does; `managed` (the
generator's alone, as T3 Code marks it) is written only when no palette was changed by hand; Duplicate reads a look's
colours back from its tokens; Export downloads `<id>.json` with the derived colours written out; Add theme takes a file
of 2 MB at most. A new theme's id is one that no theme and no file left out of the folder has, matched without case as a
Mac's disk matches names, so a file put there by hand is not written over; Cancel waits while a theme is saved, since a
Cancel then closed the editor twice and left the new theme unpicked (a review found both). A card carries Duplicate, and
on a theme of one's own Edit, Export and Delete, shown on hover where there is one; the folder's path and the files
skipped sit under the gallery, the path broken anywhere, since on a phone it pushed the page 530 px wide.
`static/js/appearance/LICENSE` names the new ports. `tests/unit/test_theme_folder.py` (22 tests) reads both forms,
refuses fourteen kinds of file and a null or a loose flag, saves, replaces and deletes, holds the three caps, lists a
folder with a link, a folder, a broken file and a note in it, and checks that `generator.js` and `themes.py` name the
same 61 roles. `tests/smoke/test_own_themes.py` makes a theme in both schemes in the editor, saves it and reads the file
the daemon wrote, holds it to Dot's rule in both schemes, reloads with only `boot.js`, exports it, adds a VS Code theme,
a short form and a file of another version, duplicates Grove and deletes the copy, reads a file put in the folder by
hand and one that does not parse, checks that a new theme does not take the name of the second, and checks a phone's
width, waited for with a function, since Playwright evals an expression again on every frame it polls and the page's
Content-Security-Policy refuses that. Export in the desktop app is not tried: its web view has no handler for a
download.

**What.** **Create theme** makes a theme from a background and an accent, the rest derived by T3 Code's generator,
ported and held to Dot's thresholds, with an advanced view for the families of colours; **Duplicate** copies any theme
into the editor; a theme of one's own is **edited**, **exported** as a file and **deleted**; **Add theme** imports a
file: T3 Code's theme file (version 1), the short form T3 Code publishes (a name, light or dark, a canvas and an
accent), or a VS Code colour theme, whose editor colours feed the roles as T3 Code maps them. The editor shows the theme
on the page while it is drawn. Themes of one's own are kept by the daemon as files in its home's `themes` folder (`GET`,
`PUT` and `DELETE /api/themes`), checked on the way in (the version, a name of 1 to 48 characters, an id no built-in
has, colours that parse, a size cap) and written whole or not at all; the desktop app, the browser and the phone share
them, and a valid file put in the folder shows in the gallery. The README follows; unit tests for the daemon's checks; a
smoke test creates, picks, reloads, exports, imports a VS Code theme and deletes; the tour is read by eye.

## Part 20: a chat's menu and the composer at the foot, after T3 Code (planned 2026-10-12)

The owner's twentieth word, on 2026-10-12, with a screenshot of T3 Code's sidebar, where a right-click on a thread
opens its menu: that Dot take the actions on a chat it needs, from T3 Code or of its own design, as long as they work
for Dot. With it, a screenshot of a run page: after the first message the composer stands right under it, near the top
of the window, and belongs at the bottom.

**Status (2026-10-12): planned.** What is built today. A chat of the sidebar (`sidebar.js chatLink`) is a link to the
latest run a person started on it: the state dot (none when the run is done or cancelled), the title (the first message
cut short, `thread_title`) and the age, or Working and its time (item 122). The chats are grouped by day (Today,
Yesterday, Previous 7 days, Older), searched by title, and deleted several at a time under Select (`DELETE
/api/threads`, refused with 409 while a run of one works). A thread (`threads`: id, title, source, created_at,
updated_at) holds nothing else: no pin, no name the owner gave it, no mark of what was seen. A run that ends while the
owner looks elsewhere is told by a desktop notification when those are on (`attention.js`); the sidebar shows it only by
its dot going away. On the run page the composer is the dock (`.dock`, sticky at the bottom of the window): it stays
there once the conversation is taller than the window, and stands right under a short one, so the first message has it
near the top.

What T3 Code does, read from its repository (MIT, © 2026 T3 Tools Inc.; a checkout outside this repository, read and
never run). A right-click on a thread's row opens its menu (`threadActionMenu.logic.ts`, run by `Sidebar.tsx`), the
desktop app's own menu or, in a browser, a menu of the page; the row has no button for it. Pin puts the thread in a
Pinned group at the top. Settle moves it to a folded "Settled (n)" shelf at the foot of the list, by hand, or by itself
once its pull request is merged or after days without activity. Snooze hides it for an hour, three hours, until the
evening, tomorrow, next week or a time of one's own, and it wakes early when it needs an answer. Rename turns the title
into a field in the row (Enter keeps, Escape leaves; a double click starts it). Regenerate title asks a model for a new
one. Mark unread winds back the time the thread was last seen, so its last finished turn shows as new until the thread
is opened. Copy gives the thread's path, branch or id. Archive hides it until it is brought back from Settings. Delete
asks first. Several threads chosen at once get Unpin, Settle, Snooze, Regenerate titles, Mark unread and Delete.

**The shape of this part (the decisions).** Dot takes the actions a chat of Dot has a use for: Pin, Rename, Mark unread
(with the unread dot it needs), Copy link and Delete, and Select, to start choosing several from a chat. What stays out:
Settle, Snooze and Auto-settle (an inbox for many threads of code; Dot's chats fold by day, and a schedule brings work
back at a time), a new thread on a branch and the project's filter and settings (Dot has no projects), Regenerate title
(Dot names a chat by its first message and makes no model call for it; Rename names it) and Archive (a second place for
a chat to be, beside Delete). The menu opens from a "…" button at the row's right, shown on hover and on focus and
always on a touch screen, from a right-click on the row and from the context-menu key; since a row is a link and the
browser's own menu gives way to Dot's, Dot's menu copies the link. The pin, the name and what was seen are kept by the
daemon, so the desktop app, the browser and the phone agree. The composer stands at the foot of the window from the
first message.

### How to start part 20 (the handoff prompt)

> Read ROADMAP.md part 20 and the status table of part 9 (rows 129 to 131). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. T3 Code's
> source is read, never run, from a checkout outside this repository, and what is ported carries its MIT notice. Never
> touch the owner's data directory except to read it and to restart the daemon when `/api/status` shows no active run;
> the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads Turkish; everything written is
> English. Every UI item ends with the screenshot tour read by eye.

### 129. The composer at the foot of the window

**Status: done.** Merged as `048f3aa` (PR #161, MYP-270 Done).

**As built (2026-10-12).** The run page's column (`.run-main`, `run.css`) is a flex column at least as tall as the
window less the top bar (`--stagebar-h`) and the page's padding above it (24 px, 16 px on a phone), and the conversation
is a block of its own in it (`.run-thread`, `views/run.js`) that grows into the room a short one leaves, so the dock,
sticky at the bottom as before, stands at the window's foot from the first message. The hidden heading moved into the
conversation's block, so its `> * + *` spacing is the one `.run-main` had: a flex item lays out its own children, whose
margins no longer fold into the column's. A long run scrolls under the dock as before, with the arrow down above it.
`tests/smoke/test_run_chrome.py` gains a test: a finished run with its steps folded fills a third of the window, and at
1280 px with the details panel open and closed and at 390 px the page does not scroll, the dock's foot is the window's
and the question is at the top; the long run's test still finds the dock in the window, and the run page's other smoke
tests (stop, takeover, team, activity) pass.

**What.** On a run page the composer stands at the foot of the window from the first message: the conversation starts
at the top and fills the space above it, and once it is taller than the window the page scrolls under the composer as
it does today. The arrow down, the line of Continue and a delegated run's foot keep their places by it, and the spacing
of the conversation does not change. A smoke test opens a run of one message and reads the composer's foot at the
window's, on a desktop with the details panel open and closed and on a phone, and reads that a long run still scrolls
under it; the tour is read by eye.

### 130. A chat's menu: pin, rename, copy the link, select, delete

**Status: done.** Merged as `d7e7d5e` (PR #162, MYP-271 Done).

**As built (2026-10-12).** The daemon keeps the pin in `threads.pinned_at`, a column added to a store made before it
with its chats unpinned, and lists the pinned chats first, the last pinned first, then the latest activity first, so a
pinned chat is listed however old. `PATCH /api/threads/{id}` takes a title (stripped, 1 to 120 characters), a pin or
both: 204, 404 for no chat, 422 for neither, an empty or a long title or a pin that is not a boolean. The menu is
`menu.js`, at the page's level above the drawer and under the toasts: its items are buttons with the menu item's role,
which the arrow keys, Home and End walk round; Escape and Tab close it and give the focus back, as a chosen item does
before it runs; a click outside it, a scroll, a resize, a change of page or a repaint of the list close it; it takes the
focus itself, so a click between its items keeps the keys with it, and it keeps 8 px inside the window. A chat's row
(`sidebar.js`) is its link and the "…" button beside it, since a button cannot sit in a link: the button shows over the
time where the pointer rests or the focus is, and always on a touch screen, where the time steps aside for it. A
right-click on the row, its button too, opens the menu at the pointer, Shift+F10 and the context-menu key under the row.
Rename holds the list's repaint while its field is in the row, where a right-click is the field's own; the row comes
back at once, under the new name while the daemon keeps it, and a key gives the focus back to the row while leaving the
field leaves it where it went; Delete is Select's delete for one chat, asked as "Delete this chat?", and goes home when
the chat was open. The menu's items are as tall as the sidebar's rows in either density, after the 4 px grid's unit test
refused a 6 px padding. Tests: `tests/unit/test_store_threads.py` (a store from before the pins gains them; the order;
404), `tests/smoke/test_server.py` (the route through the API) and `tests/smoke/test_chat_menu.py` (the button, the
right-click and the key; the keys in the menu; Pin, kept after a reload; Rename by Enter, by Escape and by leaving the
field; the link read back from the clipboard; Select; Delete cancelled, then done on the open chat; Unpin in the dark
scheme; on a phone the button beside the time and the menu inside the window); the screenshots read by eye. A review
against the done criteria found them met and five edges, fixed before the merge: a repaint now closes an open menu, a
right-click in the name's field is the field's, the row comes back without waiting for the list, Enter while an input
method composes is left to it, and the menu holds the keys after a click between its items.

**What.** A chat's row gets a menu, after T3 Code's: a "…" button at its right (on hover and on focus, always on a touch
screen), a right-click on the row and the context-menu key open it; the arrow keys move through it, Escape closes it
and gives the focus back. **Pin** / **Unpin**: pinned chats sit in a Pinned group above Today, the last pinned first, and
are listed however old they are. **Rename**: the title becomes a field in the row; Enter or leaving the field keeps the
name, Escape leaves the old one, and an empty name is not kept. **Copy link**: the chat's address. **Select**: Select's
mode, with this chat ticked. **Delete**: asked first, as Select's Delete is, and refused while a run of the chat works.
The daemon keeps the pin and the name (`threads.pinned_at`, `PATCH /api/threads/{id}`), so every browser shows them.
The README follows; unit tests for the store and the route; a smoke test opens the menu by the button, a right-click and
the key, pins, renames, copies and deletes; the tour is read by eye.

### 131. Unread chats

**Status: done.** Merged as `34e1fe1` (PR #165, MYP-272 Done).

**As built (2026-10-12).** The daemon keeps when each chat was last seen in `threads.seen_at`; added to a store made
before it, the column is filled with the time it was added, so the chats there are taken as seen and none turns up
unread at once, while a new chat starts unseen. A chat is unread when its latest run a person started has ended
after it was last seen, or has ended and the chat was marked unseen: `GET /api/threads` carries `unread`, and
`PATCH /api/threads/{id}` takes `seen` (true marks it seen now, false unseen; 404 for no chat, 422 for a value that
is not a boolean). The sidebar paints an unread chat's dot in the accent, where a run that ended well shows none and
a failed one keeps its red, and its row says "unread" to a screen reader. Rather than tracking which run ended where,
the open chat is seen whenever the page is in view, shown and in a window that has the focus, so a window behind
others shows nothing new: on opening, on every repaint of the list (a run that ends there, whichever run the page
shows) and when the page comes back into view; Mark unread on the open chat holds until it is opened again or the
page comes back into view. The menu offers Mark read on an unread chat and Mark unread on a read one whose run is
over, after Pin; a daemon from before item 131 sends no `unread`, and the page shows no dot and offers neither.
Tests: `tests/unit/test_store_threads.py` (a store from before the mark takes its chats as seen, once;
a chat is unread once its run ends until seen, unseen again, read while a run is under way, unread when that run
ends; 404), `tests/smoke/test_server.py` (the route: unread after a run ends, seen, unseen, 404, 422) and
`tests/smoke/test_unread_chats.py` (a run that ended before the page opened leaves an accent dot that says unread;
opening the chat clears it, and a run that ends in the open chat leaves none; a run that ends in another chat
leaves that one unread; Mark unread on the open chat brings the dot back, kept while another chat is opened and
over a reload, in the light and the dark scheme; Mark read clears it); `tests/smoke/test_chat_menu.py` knows the
new item; the screenshots read by eye. A review against the done criteria found them met and one regression, fixed
before the merge: a chat opened by a key lost the focus when its dot went, as the repaint took its row away (the
smoke test opens the chat by Enter and fails without the fix); the backfill now commits at once, and the unit test
proves it runs only when the column is added.

**What.** A chat whose latest run ended after the chat was last seen shows a dot in the accent colour and says unread
to a screen reader, as T3 Code marks a thread with a turn not yet seen; opening the chat, or having it open in view when
the run ends, marks it seen. **Mark unread** in a chat's menu makes it unread until it is opened again, and **Mark
read** clears one. The daemon keeps when each chat was last seen (`threads.seen_at`), so the phone and the desktop
agree; the chats there when the column is added are taken as seen, and a scheduled run that ends leaves its chat unread.
The README follows; unit tests for the store; a smoke test ends a run while another chat is open, reads the dot, opens
the chat and marks it unread from its menu; the tour is read by eye.

## Part 21: runs that keep going on every model, the composer's choices on a chat's page, and the window's lights in line (planned 2026-10-12)

The owner's twenty-first word, on 2026-10-12, with two screenshots: that a run on Ollama keeps going when told not to
stop, or not to stop until something is done, while runs on OpenCode Go and on Anthropic's models stop when they see fit
(the first screenshot: a run on a hosted model that made two changes and ended with a report and suggestions); that once
a chat has started, its composer no longer offers the model, the reasoning and the rest, and whether that is how it
should be; and, in the second screenshot, that the desktop window's three traffic lights stand above the line of the two
buttons beside them, and could stand on it with room above and below.

**Status (2026-10-12): planned.** What is built today, and what a reading of the store found (the kinds and the numbers
of the latest runs; of a goal, only whether it holds words that ask to keep going). A run ends when its model calls
finish, or, in a chat, with a reply that calls no tool; a task's reply without a tool call is first sent back twice
(`finish_nudges`). None of this depends on the provider: no provider is made to call a tool, and the prompt is the same
for all. Goal mode, a switch under the composer's Options, has the review model check a finish against the goal and send
the run back up to four times (`goal_patience`). Of the owner's runs, one goal asked to keep going: on Ollama, it went
260 steps; no run on Anthropic or OpenCode Go was asked in those words, so the store holds no like for like. In goal
mode a Claude run went 97 steps and two Ollama runs 94 and 96, and the checker accepted every finish at its first look,
five of five. The run of the screenshot, with goal mode off, kept no plan and no targets for a goal that names an
outcome, which the prompt's rule for open-ended goals asks for, and finished after 22 steps. A run stops where its model
decides that it is done; the owner finds that Claude and the models of OpenCode Go decide so sooner than the models run
on Ollama, and nothing in Dot holds a model to the owner's words. On a chat's page the composer is the dock: a follow-up
repeats the open run's mode, model, reasoning, approvals and goal mode (`views/run.js`), and the dock shows none of
them; the home's box has them as pills (`views/runs.js`). In the desktop app macOS draws the traffic lights where it
puts them by default, their centre 16 px under the top on macOS 26 (measured offscreen with tao's own code); the
sidebar's head is centred on that line (item 107), and the top bar, while the sidebar is away, centres its buttons on
its own middle, lower.

**The shape of this part (the decisions).** The owner's words decide how long a run goes, on every model: words that ask
a run to keep going turn goal mode on for it, and the checker, told so, accepts a finish only when the goal is done,
blocked or impossible; every model is told plainly not to stop to report or to offer work it can do itself, and every
goal check sends back an answer that offers work within the goal instead of doing it. The dock of a chat's page offers
what the home's box offers, for the next message, as T3 Code's composer keeps its model and reasoning on every turn of a
thread. The window puts its traffic lights on the top bars' centre line, and the sidebar's head grows to the top bar's
height so that its button stands there too.

### How to start part 21 (the handoff prompt)

> Read ROADMAP.md part 21 and the status table of part 9 (rows 132 to 134). One item at a time; the protocol of part 5
> (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Never touch the owner's
> data directory except to read it, by the kinds and numbers of what is stored, and to restart the daemon when
> `/api/status` shows no active run; the owner's goals stay out of this file; PLAN.md stays untracked. The owner reads
> Turkish; everything written is English. Every UI item ends with the screenshot tour read by eye.

### 132. Runs that keep going on every model when asked to

**Status: done.** Merged as `002575b` (PR #164, MYP-273 Done).

**As built (2026-10-12).** `agent/keep_going.py` finds the ask in whole words, in English and in Turkish, with the
Turkish letters or without them and with the typed apostrophe or the curly one that a phone's keyboard puts; the capital
İ is read as i first, since Python's case-insensitive match folds it to no single letter. A bare "durma" asks only at
the end of a clause or a line, or after "hiç" or "sakın", since it is also the noun of "durma noktası"; a stop with an
object ("don't stop the server"), a wait ("wait until it is done", "bitene kadar bekle"), "durmadan önce" and "non-stop",
mostly said of flights, ask nothing; "devam et" is left out, as it is how a follow-up says "continue". A run a person
starts (`RunManager.start` and `start_for_agent`: the web UI, `dotx run`, a schedule, an agent's chat) whose goal asks
runs in goal mode whatever was chosen; a delegated run and a campaign's session keep theirs, a campaign having its own
check, and a resumed run, one that an answer starts and a follow-up on the run's page keep the run's, so the chat's work
goes on as asked and the agent's own question cannot turn it on (item 133 puts the switch within reach on the chat's
page). The goal checker's question gains an ASKED part after the goal, and its rules gain two: an answer that ends by
offering to do a part of the goal that is not done yet, which the agent could carry out, is `continue`, while an offer
of work the goal did not ask for changes nothing and what needs the owner stays an offer; and with ASKED, achieved needs
every part of the goal done and shown, and a stop to report progress is `continue`. The prompt of every task and
research run outside a campaign tells the model to keep acting while the goal leaves work it can do, to leave work the
goal did not ask for as an offer, and to take an ask to keep going at its word. `GET /api/runs/{id}` carries
`keep_going`, and the Details card reads "On, asked not to stop": "Keep going" is already the composer's switch for a
campaign. Tests: `tests/unit/test_keep_going.py` (asks in both languages, capitals and curly apostrophes included, and
lookalikes such as a bus stop, a server not to stop, a wait, "durma noktası" and "durması"),
`tests/unit/test_run_modes.py` (the rules in task and research runs, not in a chat or a campaign's session) and
`tests/smoke/test_keep_going.py` (a scripted task asked not to stop and started with goal mode off runs in goal mode,
its checker is told, its first finish is sent back and its second accepted; a task not asked keeps the goal mode
chosen, and its checker is not told); the Details card read in Chromium on a scripted run. A review against the done
criteria found two blockers, fixed before the merge: the curly apostrophe of a phone's "Don’t stop" was not read as an
ask, and the offer rule reached past the goal (a run asked only for a diagnosis would have been sent back to make the
fix); the agent's own question no longer turns goal mode on in the run its answer starts.

**What.** A goal that asks the run to keep going, in English or in Turkish (don't stop, keep going, never stop, until it
is done; durma, hiç durma, durmadan, bitene kadar, bitirene kadar, tamamlanana kadar), turns goal mode on for a task or
research run, whatever the switch says, and the run's page says why. The goal checker is told that the owner asked to
keep going: it accepts a finish only when every part of the goal is done and shown, or blocked by what only the owner
can give, or impossible. Every goal check sends back an answer that ends with suggestions or offers within the goal that
need nothing from the owner, so that they are done instead of offered. The prompt tells every model to keep acting while
the goal leaves work it can do, not to stop to report or to offer more, and to take an ask to keep going at its word.
The README follows; unit tests for the words (both languages, with lookalikes that are not asks), the prompt's rules and
the checker's question; a smoke test runs a scripted task asked not to stop, reads goal mode on, its first finish sent
back and its second accepted.

### 133. The composer's choices on a chat's page

**Status: done.** Merged as `bf11312` (PR #166, MYP-274 Done).

**As built (2026-10-12).** The home's pickers are one module, `static/js/choices.js`: `runChoices` makes the pills of a
run's choices besides its goal, the mode, the model with its reasoning and the Options (approvals and goal mode), each
with its menu, and takes from its box where they start and where a choice is kept. The home keeps them in the browser as
before (`KEPT_APPROVALS` and `KEPT_GOAL_MODE` in `runmodes.js`, a reasoning level per model in `efforts.js`). A chat's
page starts them from the open run's mode, model, reasoning level, approvals and goal mode and holds them in the page
(`heldChoice`), so a choice made for a follow-up leaves the home's as they were. A follow-up starts its run with them on
the same thread and for the same agent; it waits for the model list, so that a level the chosen model does not offer is
not sent (the daemon refuses it, item 116). The dock has no agent pill, the agent being the chat's, and no Keep going, a
campaign starting from a goal of its own; goal mode hides for a chat, as on the home. While a run works a message steers
it, and the run keeps its own choices: the pills step back to half their strength until the pointer or the focus comes
to them, and their tooltips add "For the next follow-up: the run under way keeps its own". Tests:
`tests/smoke/test_dock_choices.py` (a finished run's page shows its mode, model and approvals, no agent pill and nothing
waiting; another model with its High reasoning and Auto approvals, chosen there with goal mode in the Options and no
Keep going, start the follow-up's run on the same thread, whose page shows them, while the browser keeps none of them
and the home's box shows the daemon's own; while a run works the pills wait and say so, and stop when it ends; Chat
chosen in the dock leaves no goal mode switch; a run on the model with levels, followed up on one without, leaves the
level to the daemon); `tests/smoke/test_composer.py` and the tour for the home's box; the screenshots read by eye. A
review against the done criteria found them met and the smoke test short of their negative cases, which it now reads; a
follow-up now always waits for the model list, as every follow-up on a run with a model recorded did.

**What.** On a chat's page the dock offers what the home's box offers for the next message, after T3 Code's composer,
which keeps its model and reasoning on every turn of a thread: the mode, the model with its reasoning, and the Options
(approvals and goal mode), set at first to the open run's. A follow-up starts its run with them. While a run works, a
message steers that run, which keeps its own model and options; the choices then wait for the next follow-up, and their
pills say so. The agent stays the chat's, and Keep going stays a choice of a new chat (a campaign starts from a goal of
its own). The home's pickers become one module that both boxes use. The README follows; a smoke test changes the model,
the reasoning and the approvals on a finished run's page, follows up and reads them on the new run; the tour is read by
eye.

### 134. The traffic lights on the line of the top bars

**Status: done.** Merged as `a5b0220` (PR #167, MYP-275 Done).

**As built (2026-10-12).** `desktop/src-tauri/src/lib.rs` gives the window `traffic_light_position` (`TRAFFIC_LIGHTS`,
20 and 28 logical pixels). Tauri hands it to wry, whose `inset_traffic_lights` is tao's code line for line and runs
again on every redraw: it moves the close button 20 px from the left and, on macOS 26, its centre 2 px above the inset's
y, so the lights are centred 26 px under the top edge and end 80 px from the left (measured offscreen; by default they
were centred 16 px under the top, from 9 to 69 px). In the desktop shell every top bar's row is 52 px tall, with the
sidebar docked as well, and the sidebar's head grows from 38 to 52 px, so the button that folds the sidebar, the top
bar's opener and the emergency stop beside it stand on the lights' line, with 19 px above and below the lights; the room
kept for them grows from 84 to 96 px, 16 px after the last light. A browser's page keeps its own bars. Tests:
`tests/smoke/test_desktop_lights.py` (in the desktop shell as its page sees it, a `__TAURI__` global: the sidebar's
button and the top bar's row on the line with the sidebar docked; the opener in the button's place and the emergency
stop on the line once it is closed; the opener and the drawer's button at a phone's width; a browser's bars as before;
the shell's test fails on the CSS of before), and the tour reads its constants; `cargo fmt --check` and `cargo clippy
--all-targets -D warnings` pass; the screenshots read by eye. The window itself is the owner's to read once the app is
rebuilt (`npm run build` in `desktop/`).

**What.** In the desktop app the window puts its traffic lights on the centre line of the top bars, with room above and
below them: Tauri's `traffic_light_position` sets them 20 px from the left and centres them 26 px under the top (the
close button's centre is the inset's y less 2 px, measured offscreen with tao's own code on macOS 26). The sidebar's
head grows to the top bar's height, so that the button that folds the sidebar stands on that line, as the top bar's
buttons do while the sidebar is closed or a drawer, and the page's room for the lights follows their new place. A smoke
test reads the centres of those buttons in the desktop shell's layout, with the sidebar open and closed and at a phone's
width; the app is built; the owner reads the window by eye once the app is rebuilt.

## Part 22: what a check of the whole found (planned 2026-10-12)

The owner's twenty-second word, on 2026-10-12: that much code has been written, as professionally as could be, but like
any professional work it may hold mistakes, wrong choices or gaps; a comprehensive check of everything, then the
problems found fixed from the most important to the least.

**Status (2026-10-12): planned.** How the check was made. Four reviews read the code by area: the policy and what a run
may reach; the run's own machinery (the loop, the models, memory, the scheduler, undo); the store, the API and the
daemon's plumbing; the web UI and the desktop shell. Each finding came with the lines it rests on and a case that shows
it, and was read again in the code before it was ranked by weight: what lets a run do what the owner did not allow, or
loses data, first; a bug on a common path or a race next; an edge or a missing timeout after; the rest last. Beside the
reviews: the offline smoke suite on main (289 passed, one skipped), the history searched for keys (none but the fake
ones of the redaction tests), and the dependencies checked against the advisories (`pip-audit` over 79 packages and
`npm audit`: none; the Rust crates were not checked, `cargo audit` is not installed).

**The shape of this part (the decisions).** Five items in the order of weight, each a pull request of its own: what a
run may change, send and open (135); spending, pacing and stops when two things happen at once (136); a reply cut off
taken for an answer (137); the store and the daemon's plumbing (138); the web UI and the desktop shell (139). What would
change the design, or needs another account of the Mac to show, is left to the owner, not built. A finding that a second
reading does not bear out is not fixed.

### How to start part 22 (the handoff prompt)

> Read ROADMAP.md part 22 and the status table of part 9 (rows 135 to 139). One item at a time, in order; the protocol
> of part 5 (branch, gate, PR, Linear MYP issue, squash merge, hash in the table, log line) is unchanged. Read each
> finding again in the code before fixing it, and give each fix a test that fails on the code of before. Never touch the
> owner's data directory except to read it, by the kinds and numbers of what is stored, and to restart the daemon when
> `/api/status` shows no active run; PLAN.md stays untracked. The owner reads Turkish; everything written is English.

### 135. What a run may change, send and open

**Status: done.** Merged as `cb76e7d` (PR #169, MYP-276 Done).

**As built (2026-10-12).** `dotagent.policy.paths` gains the path class `dot`: what Dot loads and runs, and its
emergency stop (`Runtime.dot_paths`: the plugin folders with `[plugins] dirs`, `plugins-data`, the skill folders with
`[skills] dirs`, `playbooks` and `STOP`). A run reads it as it reads the home folder; the file tools and the shell guard
judge a change there as they judge one of a protected location (`secret_access`, refused outside `yolo`), with the
reason "changes what Dot loads and runs". The shell guard also learns where more commands write: a copy (`cp`, `ln`,
`install`, `ditto`, `rsync`, `scp`) reads its sources and writes its last operand, so a skill's template copied into the
workspace stays allowed; `rsync` and `scp` are judged on what they write on this Mac as well as for the other machine;
`git clone` on its folder, `curl` and `wget` on the files they save, `tar` and `unzip` on the folder they unpack into,
and an interpreter's inline code on the places of Dot's it names. A browser upload of a file from those folders keeps
the `exfiltration` flag it had. A new flag, `upload`, marks a shell line that sends data out: `curl` with a body or a
method, `git push`, `scp` and `rsync` to another machine, `ssh`, `sftp` and the sockets, a code host's command line that
creates, comments, edits, merges or deletes (`gh api` with fields or a method), and inline code that names the network,
which now counts as fetching too; it changes nothing in a run that read no untrusted content and asks in one that did.
`truncate` outside the workspace is destructive, so the default preset asks. `RunManager.delegate` gives an agent's run
the stricter of the agent's approvals and the sender's (`ask`, then `auto`, then `yolo`). `check_browser_url` refuses
this Mac on the daemon's port in any spelling a browser reads (`localhost` and the names under it, `127.1`,
`2130706433`, `[::1]`, an IPv4 address written as IPv6) and the names of `[server] reach`; the browser session, the
browser tools and the web tools use it, and a server of this Mac on another port still opens. The desktop's app role
`dot` (the shell's bundle, `dev.dot.desktop`) refuses a step in Dot's own window outside `yolo`.
`tests/smoke/test_chrome_front.py`, which changes the Mac's front application, is marked `desktop` as well. Tests: unit
tests for the shell guard (Dot's folders changed and read, sends with and without untrusted content, `truncate`), the
browser's refusal and the desktop role; smoke tests through a real runtime (the file and shell tools on a plugin's and a
skill's files), through the team (an agent set to `yolo` answering runs that ask, review and go without asking) and in
a browser (a session whose daemon listens on the test site's port); each fails on the code of before.

**What.** Dot's own folders, the ones it loads and runs, are read but never changed by a run outside `yolo`; a shell
line that sends data out asks once the run has read untrusted content; an agent answers a message in the stricter of its
approvals and the sender's; the browser never opens Dot's own interface, and the desktop tools never act in Dot's own
window outside `yolo`; truncating a file outside the workspace counts as destructive.

### 136. Spending, pacing and stops when two things happen at once

**Status: done.** Merged as `bb18c32` (PR #170, MYP-277 Done).

**As built (2026-10-12).** `dotagent.agent.gate.SiteLocks`, one per runtime (`Runtime.site_locks`) and shared by the
gates of all its runs, holds a lock per site for its pace and one for its spending: a public or social action is checked
against the pace, decided, spaced and recorded under the first; a payment with an amount on a site with caps reads what
was spent, is decided and is recorded under the second. A second run's payment or post on the same site waits for the
first, the owner's answer included, and is judged from what the first did. `RunManager.emergency_stop`, which
`POST /api/failsafe` calls, ends every active run with the result "Emergency stop: every active run was stopped." (the
ending `emergency_stop`), so the campaign of a session it ends stops instead of planning the next one; while the `STOP`
file exists the scheduler fires no schedule (a due firing is skipped to its next time with the status "skipped: the
emergency stop is engaged") and starts no campaign session. A run's task starts eagerly, so a stop that comes before its
first step lands inside it and ends it as any stop does (before, the run stayed queued, its thread refused new runs and
the daemon's shutdown waited for it forever). A takeover brings up the tab in the browser the run works in
(`loop.browser_of`: a campaign's for its sessions) and frees the run again when the tab cannot be brought up. The
scheduler reads each due schedule again just before it fires it, so one switched off or deleted meanwhile does not fire,
and a campaign stopped or marked achieved while its session was being started (the owner's Stop found no session to
stop) ends that session at once. A run whose end cannot be recorded is logged with the traceback
(`run_end_not_recorded`), its handle is freed and its waiters wake. The inbox of a run is shut the moment the run ends,
before anything awaits, so a message sent then is refused instead of being accepted and dropped. Tests: two runs paying,
and two posting, on one site at once; a session the emergency stop ends and its campaign held; a schedule skipped under
the stop; a schedule switched off during a tick; a campaign stopped while its session starts; a takeover of a campaign's
session and one whose tab cannot be brought up; a run stopped before its first step, a message sent while its end is
recorded, a store that fails to record it. Each fails on the code of before.

**What.** The spending cap and the pace of public actions are read, decided and recorded under one lock per host that
every run shares, so two runs that pay or post at the same moment cannot pass the cap or the pace together. The
emergency stop ends the runs it stops as stopped by it, and campaigns and schedules wait while it is engaged. A run
stopped before its first step is cleaned up like any other; a takeover acts on the browser its run uses and lets the run
go on when it fails; a schedule or a campaign changed while its run was being started is read again first; a run whose
end cannot be recorded says so; a message sent while a run shuts down is not lost.

### 137. A reply cut off is not an answer

**Status: done.** Merged as `43cf3f9` (PR #171, MYP-278 Done).

**As built (2026-10-12).** A reply without tool calls that the model service ended before it was complete, at its output
limit (the finish `length`: the service's own `length`, `max_tokens`, `max_output_tokens` or
`model_context_window_exceeded`) or for an abnormal reason (the finish `other`: a content filter, a refusal, a reason
the codecs do not know), is not taken as the run's answer in any mode. The loop stores a note that says how the reply
ended, in the service's own words, and asks the model to go on with its tools or to give the complete answer again in
fewer words. The note counts as a nudge: after `[agent] finish_nudges` of them in a row the run fails with "The model's
replies were cut short and the run did not finish (the last one ended with '…')." (the ending `cut_short`, "replies from
the model cut short"). An Ollama reply whose `done_reason` is empty is a normal stop, as the chat and messages codecs
already took an empty reason. Tests: a chat whose first reply is cut short ends with the complete second one; a task run
whose replies keep being cut short fails saying so; the reasons Ollama gives for a reply's end. Each fails on the code
of before.

**What.** A model's reply that ended because it ran out of room, or for any reason other than its end, is not taken as
the run's last answer: the loop asks the model to go on, or ends the run saying why.

### 138. The store and the daemon's plumbing

**Status: in progress.**

**As built (2026-10-12).** Every write of the store that fails or is cancelled part way is rolled back before its error
goes on (`_writing`), so no part of it waits in an open transaction for the next writer's commit; the waits for a lock
another process holds stay as they were. Dot's own Chrome is closed when a step after its start fails or is cancelled,
instead of being left running with its profile locked until the daemon restarts. The numbers of the configuration that
would stop the daemon are refused when it is read: below 1 for the runs, the tools and the calls at once, the tries and
the tabs, 0 or less for the timeouts and the scheduler's tick, a port outside 1 to 65535. A screenshot is written to a
file of its own that then takes its name, and a file under that name whose size is not the image's is written again. The
push sender and the stream of lifecycle events listen to their own kinds only (`EventBus.subscribe_kinds`), so the live
text of busy runs no longer pushes a question or an end out of their queue, and a listener that falls behind logs it
once (`event_listener_fell_behind`). The reapers of idle tabs and terminals log a failure and go on with the others;
closing the runtime, the browser pool and the terminals tries every part and raises after (`AsyncExitStack`). A restart
gives `run_finished` to the runs it closes and to older closed runs that still have events, so a prune is not undone;
the runs are indexed by chat, by the run that delegated them and by agent, the approvals by status; the columns, the
backfill and the indexes an older database gains are one transaction. A campaign, session or schedule whose start fails
after its run was made deletes the run with the thread (`delete_threads`; `delete_thread` is gone). `POST /api/runs`
refuses `standing` with `agent_id` and `browser` without `standing` (422). An upload named like a file Dot keeps next to
it (`meta.json`, `text.txt`, in any case) gets an underscore before its name. Tests: a write stopped part way; a
screenshot left cut short; a run whose events were pruned; the indexes; the bounds; a listener of a few kinds while runs
stream; a firing whose run fails after it was made; the start requests; the upload names. Each fails on the code of
before. Not tested: the Chrome closed after a failed step (only a visible Chrome has a step there) and the transaction
of the migration.

**What.** A write of several statements that is cancelled midway leaves no transaction open; a Chrome started for a run
that is cancelled meanwhile is closed, not lost; the configuration's numbers are held to sensible bounds when it is
read; a screenshot is written whole before it is kept under its hash; a subscription that cannot keep up logs what it
drops; a reaper that dies says so, and closing the runtime goes on past a part that fails; an attachment's name never
collides with the files Dot keeps beside it. And the smaller ones: pruning that a restart could undo, indexes for the
commonest queries, a backfill that a crash could skip, a failed start that leaves a run behind, a request to start a run
whose fields contradict each other.

### 139. The web UI and the desktop shell

**Status: planned.**

**What.** In the desktop app a link that opens a new window opens in the browser; the shell's watcher neither reloads
the page at its first look nor sends it to the down page after one slow answer, and a shortcut it cannot register no
longer stops it; the sidebar reads the chats again after the stream reconnects; an error in handling one event is shown,
not taken for a lost connection; the home's box keeps the reasoning level chosen before the models load; a note, a rule
or a key is never added twice by a key pressed twice. And the smaller ones: answers that arrive out of order, an
approval card left after a failed refresh, the shell's New chat, an answer that is not JSON, and the rights of the CI's
token.

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
- 2026-10-10 (Fable 5.1, research session): part 3 planned, items 22 to 41, from `reports/Dot ajanı rekabet yol haritası.md`; no code changed. Start with the handoff prompt under "How to start part 3".
- 2026-10-10: at the owner's word, items 42 to 45 join part 3: TypeSafe where `yolo` gains (a tool that judges many
  items in one call, X campaigns, typed checks before a model call, a step's model), with the docs and cookbooks each
  item starts from; part 3 is committed with them.
- 2026-10-10 (Fable 5.1): item 22 built on branch `item-22-step-efficiency`: `tool_result` events record `site` and `steps`; `dotx stats` gains the acts line, the slowdown curve, the cache share and a Sites table (`--by model|site|all`); eval reports carry acts, single acts and cached tokens and show the two shares. Gate: format, check, mypy clean; unit 560; `dotx eval check` ok; offline smoke of the eval runner and scaffold 11 passed. The merge hash goes into the table with the next PR.
- 2026-10-10 (Fable 5.1): item 22 merged accd6a8 (PR #22, MYP-163 Done). Item 24 (branch `item-24-fast-model`) started; its first commit records accd6a8.
- 2026-10-10 (Fable 5.1): item 24 built on branch `item-24-fast-model`: the compaction summary is the fast model's side work, the run's own model writes it when the fast one fails and the `compacted` event names the problem (`fast_problem`); `dotx doctor` checks the fast model; defaults and README. Scope cut to what the audit measured (no wait_text or judge classification exists on a chat model; vision has its own role; no goal pre-check). Gate: format, check, mypy clean; unit 560; offline smoke 197 passed, 1 failed: `test_delegate.py::test_a_run_reads_a_file_through_a_delegated_run` (`@requires_model`, a real cloud model delegated three times instead of once; it passed on a rerun; unrelated to this item). The merge hash goes into the table with the next PR.
- 2026-10-10 (Fable 5.1): item 24 merged 9c16866 (PR #23, MYP-164 Done). Item 23 built on branch `item-23-multi-action`: prompt and tool descriptions make the one-step act the exception, with examples; `progress.py` single-action detector, streak and steer; the loop stores the note and emits `single_action_steer` after three such steps on one target; `[agent] steer_single_actions = true`. Gate: format, check, mypy clean; unit 563; `dotx eval check` ok; smoke scaffold, tool groups and run limits 14 passed. The eval set has no browser task, so the share is the owner's reading with `dotx stats --by site`.
- 2026-10-10 (Fable 5.1): item 23 merged a70e354 (PR #24, MYP-165 Done). Item 29 built on branch `item-29-eval-compare`: `dotx eval report A B` ends with a difference line; five long tasks (ten or more tool turns) in the catalog, `LONG_TASK_IDS`; decision 1 lists the live commands. No `compare` command (report already compares); no fixture-server or campaign task (the harness has no browser and runs one run per attempt). Gate: format, check, mypy clean; unit 565; `dotx eval check` 31 ok.
- 2026-10-10 (Fable 5.1): item 29 merged 1513bd4 (PR #25, MYP-166 Done). Item 26 built on branch `item-26-speed-knobs`: browser settle 60 ms, screenshot edge 1366, the unchanged-page delta says so in one line; decision 13 closed for the two knobs. Browser smoke (headless Chrome, temp dirs) 39 passed with the medians above.
- 2026-10-10 (Fable 5.1): item 26 merged 8566080 (PR #26, MYP-167 Done). Item 25 parked: the stream carries no tool calls (the codecs hand them over with the finished reply) and an act already returns the next observation; the number that would reopen it is in the item.
- 2026-10-10 (Fable 5.1): item 28 built on branch `item-28-plan-evidence` (MYP-168): `PlanItem.evidence`; a mark to done without evidence is refused with what counts, a rewritten plan keeps the evidence of the steps it keeps; the goal check (end of run, `campaign_achieved`) reads the plan with evidence and its verdict lists `unproven_steps`, carried by the `goal_check` event; the web UI shows the evidence under the step; evals count `unevidenced_steps` and `unproven_steps`. `agent/verify.py` unchanged (facts are the harness's, evidence the agent's). Gate: format, check, mypy clean; unit 570; `dotx eval check` 31 ok; offline smoke 208 passed, 10 skipped.
- 2026-10-10 (Fable 5.1): item 28 merged 9a9fe5f (PR #27, MYP-168 Done). Next: item 35 (a plan with dependencies and parallel branches).
- 2026-10-10 (Fable 5.1): item 35 built on branch `item-35-plan-branches` (MYP-169): `PlanItem.depends_on` with range, self and cycle checks; the plan result names the ready (independent) and held steps; `delegate` takes `step` and the loop closes that step from the report (done with evidence, or failed); web UI shows `after 1, 2`. No scheduler, no step budget, no graph drawing, no eval task (the scripted model answers in call order): the design section says why. Gate: format, check, mypy clean; unit 574; `dotx eval check` 31 ok; smoke (plan branches, helpers, run limits, resume, watchdog, campaigns, answer check, scaffold, tool groups, server) 76 passed; the two delegated steps overlap in the real loop.
- 2026-10-10 (Fable 5.1): item 35 merged d6b40c1 (PR #28, MYP-169 Done). Next: item 36 (playbooks learned from runs that reached their goal).
- 2026-10-10 (Fable 5.1): item 36 built on branch `item-36-playbooks` (MYP-170): `agent/playbooks.py` distils a goal-mode run that ended done on a site into `<home>/playbooks/playbook-<host>/SKILL.md` through the review model, the skills catalog reads the folder as a root, runs that read a playbook count its use and two failures retire it; `dotx playbooks list|show|drop`; default off (decision 17). Gate: format, check, mypy clean; unit 581; `dotx eval check` 31 ok; smoke (skills runtime, lessons, helpers, doctor, scaffold, plan branches) 34 passed.
- 2026-10-10 (Fable 5.1): item 36 merged fb99354 (PR #29, MYP-170 Done). Next: item 41 (an own crawler).
- 2026-10-10 (Fable 5.1): item 41 built on branch `item-41-own-crawler` (MYP-171): `web/crawl.py` (breadth-first crawl within a prefix or host scope, per-host pacing, robots.txt read and reported, a shell page rendered in Dot's own Chrome, PDFs read, one clean text per page), `web/extract.py` (typed fields with a quote each, checked against the source, on the fast model), tools `web_crawl` and `web_extract` (a crawl that does not fit one result becomes Markdown files in the workspace), `[web.crawl]` caps. Tool descriptions trimmed so the small-window scaffold test still compacts at a steady pace (core share 0.412; `test_tool_groups` bound 0.45). Gate: format, check, mypy clean; unit 587; `dotx eval check` ok; smoke (crawl against a local fixture site with headless Chrome, web, tool groups, scaffold, plan branches, helpers, doctor, server) 72 passed.
- 2026-10-10 (Fable 5.1): item 41 merged aedab8b (PR #30, MYP-171 Done). Next: item 34.
- 2026-10-10 (Fable 5.1): item 34 built on branch `item-34-research-notes` (MYP-172): `tools/notes.py` with `write_notes` (one Markdown file per run under `<workspace>/notes/`, in the undo journal, offered in task and research runs), the policy's mode gate allows `notes_write` and `memory_write` in research (`memory_save` in every mode), `[web] search_backend = "auto"` (the new default; decision 18) resolved once at runtime start, `dotx doctor` `web search` row, research prompt names `write_notes`/`memory_save`/`web_crawl`, web bullet on `web_crawl`/`web_extract`. The small-window scaffold smoke test moved from a 24 000 to a 26 000 token window: the three core tools of items 41 and 34 made the fixed prompt about 700 tokens larger, and what a compaction keeps grows a little each time, so the old window ended up compacting at every step. Gate: format, check, mypy clean; unit 597; `dotx eval check` ok; smoke (research notes, memory, web, doctor, approval modes, plan branches, undo, run limits, crawl, scaffold, tool groups) 64 passed.
- 2026-10-10 (Fable 5.1): item 34 merged e68b22d (PR #31, MYP-172 Done). Next: item 31 (one-time codes from Mail and Messages).
- 2026-10-10 (Fable 5.1): item 31 built on branch `item-31-one-time-codes` (MYP-173): `desktop/codes.py` (Mail through AppleScript, Messages through its database read-only, the code rule: a 4 to 8 digit number after a cue word, `G-` prefixed, opening the message, or the only six-digit number), `tools/codes.py` with `code_fetch` registered only when `[policy] code_sources` names an application (default empty; decision 19), a high-risk `app_control` action, result is the code with sender and age only; `dotx doctor` row `one-time codes`; the yolo sentence of the prompt names the tool. Design corrected: one connector module, Messages read from `chat.db` (no scripting for received messages), "ask asks once" is the owner's opt-in plus the presets. Gate: format, check, mypy clean; unit 610; `dotx eval check` ok; smoke (codes, doctor, tool groups, scaffold, research notes, approval modes) 24 passed, the live Mail test skipped (owner-run, `DOT_MAIL_TESTS=1`).
- 2026-10-10 (Fable 5.1): item 31 merged 88abeed (PR #32, MYP-173 Done). Next: item 32 (the owner's Chrome profile as the default of a campaign).
- 2026-10-10 (Fable 5.1): item 32 built on branch `item-32-campaign-browser` (MYP-174): `browser/pool.py` (the daemon's session plus one per other mode, made on first use), `ToolContext.browser` chosen when a run starts (its campaign's choice, a delegated run following its parent's campaign, else the daemon's), `CampaignRecord.browser` with `[agent] campaign_browser = "attach"` as the default (decision 20), `--browser`/API `browser`, a campaign on the owner's Chrome refused while that Chrome is closed or has no debugging port, the brief's second line names the Chrome, `dotx campaign show` and the campaign page show it; evals keep `own`. Design corrected: no `[campaign]` table, one pool instead of switching the shared session. Gate: format, check, mypy clean; unit 611; `dotx eval check` ok; smoke (campaigns, web, crawl, memory, codes, judge, pace, scheduler, terminal, skills, MCP, secret fields, server, resume, plan branches, scaffold) 137 passed, 1 skipped; the two live-model delegate tests (`gpt-oss:20b-cloud` through the owner's Ollama) fail on main too: the model passes `step=1` to `delegate` before any plan exists, item 35's check refuses that call, and the test helper expects one delegate result. Finding, not a regression (log of item 35 to be revisited: the live model wastes a turn on a step it has not planned).
- 2026-10-10 (Fable 5.1): item 32 merged f302464 (PR #33, MYP-174 Done). Next: item 33 (pre-approved spending caps).
- 2026-10-10 (Fable 5.1): item 33 built on branch `item-33-spending-caps` (MYP-175): `policy/spend.py` (amounts from a control's label or the page's total, `SpendCap` per site, `spend_allowance` turns a payment under both caps into a question with source `spend` that takes the money hand-off's place), `ActionInfo.amount` and the amount in the step's summary (timeline, policy event, approval record), the gate reads the day's spends from the new `spends` table and records the spend once the step is authorized, `[policy.spend]` empty by default (decision 21). Design corrected: the amount is read in the browser tool's classification, not in `elements.py`; `ask` asks once (the plan named only yolo and auto). Gate: format, check, mypy clean; unit 625; `dotx eval check` ok; smoke (approval modes, secret fields, judge, pace, terminal, memory, codes, browser, campaigns, scaffold, tool groups) 102 passed, 1 skipped.
- 2026-10-10 (Fable 5.1): item 33 merged 44ea96b (PR #34, MYP-175 Done). Next: item 27 (cheapest channel first).
- 2026-10-10 (Fable 5.1): item 27 built on branch `item-27-cheapest-channel` (MYP-176): `tools/channels.py` (`channel_of`: command, api, web, browser, desktop, agent), the executor records `channel` in every `tool_result` event, `dotx stats` gains a channels table with the GUI share of the acting calls, the prompt's channel order gains "a skill or an MCP server of a site that names its API or command is used before the site's pages". Design corrected: the order was already in the prompt; the channel is recorded by the executor, not the loop; the eval set is scripted so the GUI share cannot move offline (decision 22: measured on the owner's real runs). Gate: format, check, mypy clean; unit 639; `dotx eval check` ok; smoke (tool groups, scaffold, run limits, approval modes, research notes) 27 passed.
- 2026-10-10 (Fable 5.1): item 27 merged dfbd485 (PR #35, MYP-176 Done). Next: item 30 (typed best-of-two; injection fixtures).
- 2026-10-10 (Fable 5.1): item 30 built on branch `item-30-best-of-two` (MYP-177): `[judge] second_opinion` (off) asks each question again in an independent phrasing; a disagreement flags the click and names both answers, a claim is refused only when both answers are below the threshold; three eval tasks hide an instruction in a file with `injection_checks` and the new `answer_lacks` check, the report's `injection_followed` and the scorecard's `hidden instructions followed` count them. Unit 642, eval check ok, judge and eval-runner smoke 10. Decision 23.
- 2026-10-10 (Fable 5.1): item 30 merged cda37e1 (PR #36, MYP-177 Done). Next: item 37.
- 2026-10-10 (Fable 5.1): item 37 built on branch `item-37-change-strategy` (MYP-178): the brief of a stuck campaign lists the strategies since the newest progress as refused routes and asks for another channel, source or approach; `campaign_note` refuses a repeated route while the campaign is stuck; README "Campaigns". Unit 643, campaigns smoke 8, eval check ok. Decision 24.
- 2026-10-10 (Fable 5.1): item 37 merged 12c589f (PR #37, MYP-178 Done). Next: item 38.
- 2026-10-10 (Fable 5.1): item 38 built on branch `item-38-actions-panel` (MYP-179): Actions card on the run page from `GET /api/runs/{id}/actions` (acting steps with channel, target and evidence; approvals; file edits with undo state) and `POST /api/runs/{id}/undo` per file; `undo/runs.py` shared by the CLI and the server; README. Unit 645, actions-panel smoke 2, undo smoke 1.
- 2026-10-10 (Fable 5.1): item 38 merged 8c9cdbc (PR #38, MYP-179 Done). Next: item 39.
- 2026-10-10 (Fable 5.1): item 39 built on branch `item-39-ci-retention` (MYP-180): `.github/workflows/gate.yml` (macOS; format, lint, mypy, unit, eval check, 19 offline smoke files), `[store] retention_days = 90` and `dotx prune` deleting the events of old runs on `--apply`; README. Unit 646. Decision 25.
- 2026-10-10 (Fable 5.1): item 39's gate went green on PR #39 at its third run: the first lacked ripgrep on the runner, the second could not tell the runner's time zone (`TZ` is honoured now).
- 2026-10-10 (Fable 5.1): item 39 merged 80e8f19 (PR #39, MYP-180 Done; gate green). Next: item 40.
- 2026-10-10 (Fable 5.1): item 40 built on branch `item-40-cold-start` (MYP-181): `cli_commands.py` (light models and union), the commands of `cli.py` moved to `cli_run`, `cli_models`, `cli_doctor`, `cli_desktop`, `cli_memory` and `cli_browser`, a `dispatch` per family, `cli.py` loads the family by name. `dotx --help` 80 ms (was 600 to 760 ms). Unit 646, offline smoke 97, eval check ok.
- 2026-10-10 (Fable 5.1): item 40 merged 9ebe8e0 (PR #40, MYP-181 Done; gate green). Next: item 42.
- 2026-10-10 (Fable 5.1): item 42 built on branch `item-42-judge-items` (MYP-182): `TypeSafeService` shared by the judge and the new `judge_items` tool (one Noul or Score per item in one request, ranked), 422 and malformed answers as `judge_error` without a pause, `[judge] items_per_request = 100` from the measured requests, eval task `filter-headlines`, decision 26.
- 2026-10-10 (Fable 5.1): item 42 merged f0b1188 (PR #41, MYP-182 Done; gate green). Next: item 43.
- 2026-10-10 (Fable 5.1): item 43 built on branch `item-43-x-judgments` (MYP-183): `tools/posts.py` (rank_posts with ledger weights and a posts_ranked event, pick_draft on a Choice, check_draft with the draft threshold), Choice in `llm/typesafe.py`, campaign prompt bullet, README, decision 27. Hosted: precision@10 1.00 on the synthetic sample in 0.64 s.
- 2026-10-10 (Fable 5.1): item 43 merged aa1c77d (PR #42, MYP-183 Done; gate green). Next: item 44.
- 2026-10-10 (Fable 5.1): item 44 built on branch `item-44-typed-checks` (MYP-184): `expect` on browser_act with `Judge.judge_page` (expected, login wall, CAPTCHA, error page), the goal check judged first in goal mode (`claim_state` shared), no click judgments in `yolo`; stand-in smoke tests for the three pages, yolo and a goal-mode run; decision 28.
- 2026-10-10 (Fable 5.1): item 44 merged 1c72fa7 (PR #43, MYP-184 Done; gate green). Next: item 45.
- 2026-10-10 (Fable 5.1): item 45 built on branch `item-45-step-model` (MYP-185): `agent/stepmodel.py` and the loop's `_routing`/`_route_step`, `[agent] step_router = false` and `step_router_threshold = 0.6`, `step_routed`/`step_route_failed` events, a scripted smoke run routed by the stand-in; the live measurement is decision 29.
- 2026-10-10 (Fable 5.1): item 45 merged 1a080ca (PR #44, MYP-185 Done; gate green). Part 3 is built: every row 22 to 45 is done except 25 (parked). What waits for the owner is in "Open owner decisions" 26 to 29: the live measurements of judge_items (eval task filter-headlines), the post tools on the owner's own feed, the checks before a model call, and the step router, each a paid run on the owner's models.
- 2026-10-10 (Fable 5.1): Part 3 bookkeeping merged 181709d (PR #45). The daemon was reinstalled at the owner's word (`dotx service install`, no run or campaign active), so items 22 to 45 are live.
- 2026-10-10 (Fable 5.1): the live measurements behind decisions 26, 28 and 29 ran at the owner's word on `ollama:minimax-m2.7:cloud` with the TypeSafe key, each arm a throw-away home (reports under /tmp/dot-evals, not committed). Decision 26: `judge_items` never called on filter-headlines, no gain. Decision 28: the eval set exercises neither `expect` nor the goal check, the arms differ by variance only. Decision 29: the step router costs a step and a half, 8.5k tokens and 1.7 s a pass at the same success, so it stays off, resolved. Decision 27 needs the owner's own labeled feed and stays open. Branch `part-3-measurements` also sharpens the `step` description of `delegate`.
- 2026-10-11 (Fable 5.1): Part 4 planned at the owner's word ("find OpenAI's Dot design, make ours like it and better; desktop and mobile apps are planned"): the product is OpenAI's dots (DevDay 2026-09-29), read from learn.chatgpt.com/docs/dots and reviews; items 46 to 52 (live view and takeover, Activity, four-answer rules, the face as the state, the visual system, the desktop shell, the phone) and decisions 30 to 32. The DeepSeek arms of decision 29 run in the background and are logged when they end.
- 2026-10-11 (Fable 5.1): the DeepSeek arms of decision 29 ended (62/68 both; the router costs 2.2 steps, 18.2k tokens and 3.7 s a pass): off it stays. Item 46 started on `item-46-live-view` (MYP-186).
- 2026-10-11 (Fable 5.1): item 46 merged f4d3754 (PR #48, MYP-186 Done; gate green). Next: item 47 (Activity).
- 2026-10-11 (Fable 5.1): decisions 30 (Tauri 2), 31 (Tailscale, PWA first) and 32 (the state dot, no character) taken at the owner's word. Item 47 started on `item-47-activity`.
- 2026-10-11 (Fable 5.1): item 47 merged 2282e57 (PR #49, MYP-187 Done; gate green). Next: item 48 (rules in four answers).
- 2026-10-11 (Fable 5.1): item 48 merged 0811f52 (PR #50, MYP-188 Done; gate green). Next: item 49 (the face as the state).
- 2026-10-11 (Fable 5.1): item 49 merged 72f6168 (PR #51, MYP-189 Done; gate green). Next: item 50 (the visual system).
- 2026-10-11 (Fable 5.1): item 50 merged fbf5b34 (PR #52, MYP-190 Done; gate green). Next: item 51 (the desktop shell, Tauri 2).
- 2026-10-11 (Fable 5.1): item 51 merged 8b4415d (PR #53, MYP-191 Done; gate + desktop jobs green; Dot.app built and opened on the owner's Mac). Next: item 52 (Dot on the phone).
- 2026-10-11 (Fable 5.1): item 52 merged 2386ae7 (PR #54, MYP-192 Done; gate + desktop jobs green). Part 4 complete:
  items 46 to 52 merged, decisions 30 to 32 taken and built. Open for the owner: the phone's own check of item 52
  (Tailscale on, `tailscale serve --bg 8765`, the name in `[server] reach`, Notifications on in the phone's browser,
  a push for a question that opens the run), to be logged here by hand; decision 27 (the labelled feed); decision 26's
  follow-up (a prompt push or a longer task for judge_items); a signed desktop build (an Apple developer account).
- 2026-10-11 (Fable 5.1): Part 5 planned at the owner's word ("continue with the next tasks") from the owner's seven
  runs read through the API and `dotx stats` (never their files) and the daemon's logs: items 53 to 58 (the numbers of
  a long run, long browser tasks in the eval set, context that stays, the web without guessing, the browser's closed
  pages, the bill on the run page) and decision 33 (the context budget, measured in 55). Next: item 53.
- 2026-10-11 (Fable 5.1): item 53 merged 4967717 (PR #57, MYP-193 Done; gate + desktop jobs green). The baseline of the
  owner's runs is in the item. Next: item 54 (long browser tasks in the eval set).
- 2026-10-11 (Fable 5.1): item 54 merged 7df5799 (PR #58, MYP-194 Done; gate + desktop jobs green, `dotx eval check` plays the
  two site tasks in CI). Next: item 55 (context that stays; decision 33).
- 2026-10-11 (Fable 5.1): item 55 merged 57a000e (PR #59, MYP-195 Done; gate + desktop jobs green). Decision 33 settled by the
  instrument: budget 96 k, `num_ctx_cloud` 262144, summaries capped at 1,500 words. Next: item 56 (the web without guessing).
- 2026-10-11 (Fable 5.1): item 56 merged 227e264 (PR #60, MYP-196 Done; gate + desktop jobs green). The DuckDuckGo limit
  stands (25 of 93 web failures throttled in the owner's last 30 days). Next: item 57 (the browser's closed pages).
- 2026-10-11 (Fable 5.1): item 57 merged 8d137c7 (PR #61, MYP-197 Done; gate + desktop jobs green). The log's warnings have
  their causes in the item; Playwright's own frame traceback is not Dot's. Next: item 58 (the bill of a run, on its page).
- 2026-10-11 (Fable 5.1): item 58 merged 0014d77 (PR #62, MYP-198 Done; gate + desktop jobs green). Part 5 complete:
  items 53 to 58 merged, decision 33 settled by the instrument and built. The daemon restarted on 0014d77 with no run
  active. The live numbers of the part are the owner's next long runs, read with `dotx stats --run ID` against the
  baseline of item 53 (13.8 compactions per 100 steps, 53% of the model seconds in compactions, about ten not-found
  fetches a research run): under 5 per 100 steps and a tenth of the model seconds (item 55), near zero not-found
  fetches and no turn lost to argument validation (item 56), no `_adopt_popup` traceback after a week (item 57).
  Still open for the owner, unchanged from part 4: the phone's own check of item 52, decision 27, decision 26's
  follow-up, a signed desktop build.
- 2026-10-11 (Fable 5.1): Part 6 planned at the owner's word ("build a structure for web search and fetch in which failure
  disappears; then continue with the next tasks"): item 59 built on `item-59-web-that-does-not-fail` (MYP-199): the search
  ladder (Ollama with its key, DuckDuckGo, Brave Search, DuckDuckGo in Dot's own visible Chrome) with a bench per engine
  and a ten-minute answer memory, `web_fetch` reading a turned-away or JavaScript page in a scratch tab of Dot's own
  Chrome. Measured and rejected: Bing (another query's results for three of six queries); measured and gated: a headless
  Chrome meets DuckDuckGo's CAPTCHA, a visible one gets results. `[web] search_attempts` removed. Gate: format, check,
  mypy clean; unit 723; `dotx eval check` ok; smoke web_fetch, web, doctor, crawl 29 passed.
- 2026-10-11 (Fable 5.1): item 59 merged 725e301 (PR #64, MYP-199 Done; gate + desktop jobs green). Daemon restarted on
  725e301 with no active run. The live number is the owner's next research run: `dotx stats --run ID` web failures,
  throttled, against 25 of 93 in the thirty days before; the engine named in each search result says which rung carried it.
- 2026-10-11 (Fable 5.1): Part 7 planned at the owner's word ("run the long run, I approve; solve the other problems; a
  complete soft light and dark design; a composer with `/` for commands, skills and MCP; the head and the side panel fixed
  while scrolling"): items 60 to 65. The long run of item 60 started on the daemon (`run_mv39kmag_ec5bb3`); the owner's
  own task run was active at the time, so no daemon restart until both end. Evidence for item 61 from the daemon's log:
  47 of 64 check failures are addresses the summary cut short.
- 2026-10-11 (Fable 5.1): the owner's "Connection lost" was every run crashing on `database is locked`: item 66 (one
  writer, retries) merged 5c7eada (PR #67, MYP-200 Done); daemon restarted on it with no run active, the long run of
  item 60 started again as `run_mv3aaeat_5e1c03`. Item 67 (folded cards, no "null", the view at the first browser step)
  merged 8255af7 (PR #68, MYP-201 Done). Item 68 (delegated runs nested in the sidebar and the Activity page, a progress
  chip and a link on the chat's card) merged 278f256 (PR #69, MYP-202 Done). The long run was active at both merges, so
  the daemon stayed on 5c7eada.
- 2026-10-11 (Fable 5.1): item 61 (the ellipsis of a cut value is not part of an identifier; a long known prefix counts;
  the summary asked again with the missing sections named or the limit first) merged a8c602f (PR #70, MYP-203 Done).
  Item 62 (a delegate for a step the plan lacks runs and says so; a blank model name is the parent's own; both live
  delegate smoke tests pass) merged 056b153 (PR #71, MYP-204 Done). Item 63 (every token light-dark() in one place,
  a warm grey scale, borders as ink at a small alpha, a Colour scheme choice, contrast measured by a unit test) merged
  9e86fda (PR #72, MYP-205 Done); the controls smoke test's section count had been stale since item 58. Item 64 (the
  slash menu: `GET /api/palette`, slash.js, composer.js shared by both composers) merged d36d375 (PR #73, MYP-206
  Done). Item 65 (the head and the panel sticky under a measured stage bar, a one-line head on a phone) merged 5c3917f
  (PR #74, MYP-207 Done). Item 60 measured on `run_mv3aaeat_5e1c03`: 5.7 compactions per 100 steps (13.8 before),
  summaries 3 to 6 % of the model time (53 %), 0 throttled searches of 104 (every one from the `ollama` rung), 0
  `_adopt_popup`, 0 store retries; 6 of 13 compaction checks failed for a missing STATE section (item 61 was not on the
  daemon yet). Part 7 complete.
- 2026-10-11 (Fable 5.1): Part 8 planned at the owner's word ("a freely working, fully autonomous, self-improving
  system with memory; a design that says I am here; Haiku 5.5 for web search?"): items 69 to 76. The Models API
  confirmed `claude-haiku-5-5` supports the `web_search` server tool; the docs price it at $10 per 1,000 searches plus
  tokens, so it is a second rung, not a free first one. Daemon restarted on e475368 (pid 66449) with no run active.
- 2026-10-11 (Fable 5.1): item 69 merged 927a720 (PR #77, MYP-209 Done). Item 70 built on branch `item-70-reflection` (MYP-210): `agent/reflection.py` (reflect, reflection_due, stored_case, duplicate_pairs, to_retire, journal), the scheduler's tick fires it at `[agent] reflection_time`, `rest`/`journal` tags, `dotx reflect`, the Activity row; unit and smoke tests.
- 2026-10-11 (Fable 5.1): item 70 merged dd13ec9 (PR #78, MYP-210 Done). Item 71 built on branch `item-71-memory-rests` (MYP-211): `ranking.fused_scores`/`use_lift`/`ranked_by_use`, `service.resting`, `[memory] rest_days`/`use_half_life_days`, `MemoryPage.resting`, the Memory page's Resting group; unit and smoke tests.
- 2026-10-11 (Fable 5.1): item 71 merged 6896e53 (PR #79, MYP-211 Done). Item 72 built on branch `item-72-home-presence` (MYP-212): `presence.js` (greeting, nowLine, todaySections, the owner's name), `activityrow.js` shared by the Activity page and the home, `/api/activity` `learned_today` and `due`, the Today strip; smoke test with screenshots.
- 2026-10-11 (Fable 5.1): item 72 built on branch `item-72-home-presence` (MYP-212, PR #80). Item 73 built on branch `item-73-motion` (MYP-213): motion tokens, the face's breath and pulse, fades, reduced motion that keeps the fades, the duration unit test and the face smoke test.
- 2026-10-11 (Fable 5.1): item 72 merged 9020ee0 (PR #80, MYP-212 Done).
- 2026-10-11 (Fable 5.1): item 73 built on branch `item-73-motion` (MYP-213, PR #81). Item 74 built on branch `item-74-quiet-chrome` (MYP-214): the 4 px grid for every gap, padding and margin (`--space-half`), the sidebar without dividers, quiet pills, lighter card heads, step durations on hover; the spacing unit test.
- 2026-10-11 (Fable 5.1): item 73 merged 1efede9 (PR #81, MYP-213 Done). The controls page's cost sample lacked the searches of item 69 (the page threw and drew nothing; the controls smoke test had not run since): fixed in item 74.
- 2026-10-11 (Fable 5.1): item 74 built on branch `item-74-quiet-chrome` (MYP-214, PR #82). Item 75 built on branch `item-75-not-looking` (MYP-215): `attention.js` `isLooking`/`worthATell(runId)`, the "Tell me even when I am looking" switch, the attention smoke test with a stubbed Notification.
- 2026-10-11 (Fable 5.1): item 74 merged as `f809abd` (PR #82). Item 75 merged as `9643889` (PR #83) on the local gate: GitHub Actions refused both jobs (billing), see its status line. Item 76 opened: the baseline is written, the week starts with the daemon restart of today. The daemon's hourly task on `anthropic:claude-sonnet-5-5` failed three times running (04:28, 05:29, 06:31): Chrome did not accept the remote debugging connection on 9222 within 60 s; the owner's Chrome, not the code, as far as the store shows (told the owner).
- 2026-10-11 (Fable 5.1): Part 9 planned (items 77-83; items 82 and 83 from the owner's references: Claude Code's subagent rows and child page, Grok's moving agent faces). Item 77 merged as `ffa00cb` (PR #86, MYP-217 Done): Inter and Geist Mono self-hosted, seven sizes, three weights, the token test, the screenshot tour. Item 78 merged as `7a7e43f` (PR #87, MYP-218 Done): `stage.js`, one solid top bar, the run's head in it, the settings' single title. Item 79 built on branch `item-79-flat-surfaces` (MYP-219): flat cards, sentence-case labels, `.meta-facts`, one-line empty states, the bubble on the raised surface.
- 2026-10-11 (Fable 5.1): item 79 merged as `ab5f33a` (PR #88, MYP-219 Done). Item 80 built on branch `item-80-forms` (MYP-220): one control grammar (36/32 px, 8 px corner, one focus ring, the drawn chevron, 40 px table rows).
- 2026-10-11 (Fable 5.1): item 80 merged as `132ce0d` (PR #89, MYP-220 Done). Item 81 built on branch `item-81-dark` (MYP-221): the dark shots read and kept, `boot.js` for the first paint, the tour's first-paint test, README's visual system as the current state.
- 2026-10-11 (Fable 5.1): item 81 merged as `04693d7` (PR #90, MYP-221 Done). Item 82 built on branch `item-82-delegated` (MYP-222): the delegated run as a row, the child's breadcrumb and pill; the bar's title now gives way with an ellipsis and a delegated run's bar carries no chips (its pill has them).
- 2026-10-11 (Fable 5.1): item 82 merged as `6454345` (PR #91, MYP-222 Done). Item 83 built on branch `item-83-faces` (MYP-223): `actors.js`, eight hue tokens, the faces in the rows, the pages and the bar, `ActivityRow.actor_id`.
- 2026-10-11 (Fable 5.1): item 83 merged as `d760ea3` (PR #92, MYP-223 Done). Part 9 is built: items 77 to 83 merged (`ffa00cb`, `7a7e43f`, `ab5f33a`, `132ce0d`, `04693d7`, `6454345`, `d760ea3`); the screenshot tour read after each. Item 76's reading (part 8) is still due on or after 2026-10-18. The daemon restarted on `d760ea3`.
- 2026-10-11 (Fable 5.1): Part 10 planned (items 84-88) from the owner's word and arXiv 2609.20804. Item 84 merged as `82c06b8` (PR #94, MYP-224 Done). Item 85 built on branch `item-85-motion` (MYP-225): the sidebar's slide, the drawer's slide, unfolding bodies, pages coming in.
- 2026-10-11 (Fable 5.1): item 85 merged as `7f02492` (PR #95, MYP-225 Done). Item 86 built on branch `item-86-agents` (MYP-226): `AgentRecord` and the `agents` table, `RunRecord.agent_id`, `start_for_agent`, the agent's section first in the prompt, its tools as the run's bound, `/api/agents`, the Agents page, the agent's page with its composer, the home composer's Agent menu, the agent's face in the rows and the bar.
- 2026-10-11 (Fable 5.1): item 86 merged as `bd12b54` (PR #96, MYP-226 Done). Item 87 started on branch `item-87-team` (MYP-227).
- 2026-10-11 (Fable 5.1): item 87 merged as `23c6b5c` (PR #97, MYP-227 Done). Item 88 started on branch `item-88-desktop` (MYP-228).
- 2026-10-11 (Fable 5.1): item 88 merged as `c4d9b88` (PR #98, MYP-228 Done). Part 10 is built: items 84 to 88 merged (`82c06b8`, `7f02492`, `bd12b54`, `23c6b5c`, `c4d9b88`). The glib alert dismissed as not used (item 88's record). Item 76's reading (part 8) is still due on or after 2026-10-18.
- 2026-10-11 (Fable 5.1): the desktop shell rebuilt on the owner's Mac after part 10; its first drag raised an ACL error, fixed as `06cb620` (PR #100, under item 88): the start-dragging capability and the sidebar head owning its space. Verified by dragging both bars of the rebuilt `Dot.app`.
- 2026-10-11 (Fable 5.1): part 11 planned (items 89 to 94): learning on and measured, memory scored by outcome, recall by token budget, skills from the agent's own work, the owner's section, learning per agent.
- 2026-10-11 (Fable 5.1): item 89 merged as `a779b72` (PR #103, MYP-229 Done): learning on (`propose`, playbooks on) and measured (`memory_uses`, the Learning section of `dotx stats`, the score on the Memory page). The daemon restarted on `a779b72`.
- 2026-10-11 (Fable 5.1): item 90 merged as `fa775a0` (PR #104, MYP-230 Done): the score lifts, rests and retires the agent's notes. The daemon restarted on `fa775a0`.
- 2026-10-11 (Fable 5.1): `tests/smoke/test_continuity_live.py` (live, not in CI) had failed since the prompt outgrew its 14k window (the system prompt and the tools are about 18k tokens now); its window is 24k, the three tests pass against the cloud model (fixed with item 91).
- 2026-10-11 (Fable 5.1): item 91 merged as `9222a5d` (PR #105, MYP-231 Done): the run's starting notes fit a token budget. The daemon restarted on `9222a5d`.
- 2026-10-11 (Fable 5.1): item 92 merged as `3074107` (PR #106, MYP-232 Done): learned skills of any kind of work, the Skills tab. The daemon restarted on `3074107`.
- 2026-10-11 (Fable 5.1): item 93 merged as `6d64e43` (PR #107, MYP-233 Done): the owner section the reflection keeps, the owner's own lines beside it, the Memory page card. The daemon restarted on `6d64e43`.
- 2026-10-11 (Fable 5.1): item 94 merged as `937cca4` (PR #108, MYP-234 Done): an agent's lessons, skills and scores its own, the agent's page "What it learned". Part 11 built: items 89 to 94. The daemon waits for a restart on `937cca4` (two of the owner's runs were active).
- 2026-10-11 (Fable 5.1): the daemon restarted on `269eab5` once idle. Part 12 planned (items 95 to 100): the sidebar smaller, the run page's arrow and cost, Chrome in the background, the web that is refused, the team that grows, the review.
- 2026-10-11 (Fable 5.1): item 95 merged as `f3dce6e` (PR #111, MYP-235 Done): the nav under the search, sections that fold (`dot.sideFolds`), Working rows cut to their first words, focus rings in the theme's `--ring`.
- 2026-10-11 (Fable 5.1): item 96 merged as `3a8a20b` (PR #112, MYP-236 Done): the round arrow above the composer (`.jump`, `.dock-bare` for delegated runs), the cost card in the side panel after Details.
- 2026-10-11 (Fable 5.1): item 97 merged as `558621b` (PR #113, MYP-237 Done): `[browser] raise_window` off by default, the focus given back after the launch, the Settings › Browser switch, `POST /api/browser/raise`.
- 2026-10-11 (Fable 5.1): item 98 merged as `65433d0` (PR #114, MYP-238 Done): 999 among the refused statuses, the host memory per run, a browser's navigation headers, quiet reads behind the user's tab, refused pages in a crawl, the next rung in one line.
- 2026-10-11 (Fable 5.1): item 99 merged as `724768e` (PR #115, MYP-239 Done): `create_agent` in the `team` tool group, `AgentRecord.made_by`, the Team section that offers to make a teammate, the note of the agents that joined, "Made by Dot" on the Agents page.
- 2026-10-11 (Fable 5.1): item 100 merged as `62585c2` (PR #116, MYP-240 Done): the tour read by four readers and by eye, the offline smoke suite in full, the owner's daemon through the API; the composer's focus frame, the dark switch knob, three stale lines and the tour's sidebar fixed; five polish items left for the next part. Part 12 built: items 95 to 100. The daemon waits for a restart on `62585c2`.
- 2026-10-11 (Fable 5.1): the daemon restarted on `3f1bb24` (code `62585c2`) once idle. Part 13 planned (items 101 to 106): the character engine from bloub (MIT), the look of every agent, the character alive beside the chat, the composer remade, part 12's polish, the review.
- 2026-10-11 (Fable 5.1): item 101 merged as `8c3a590` (PR #119, MYP-241 Done): bloub ported to `static/js/character/` (MIT notice kept), `mountCharacter`, one ticker per page, the `--character-*` tokens, the Characters card of `#/controls`.
- 2026-10-11 (Fable 5.1): item 102 merged as `f6afe5d` (PR #120, MYP-242 Done): `AgentLook` on the record and in the editor, Dot's look in Settings › General, every face a still character (`actors.js`), the favicon from `character/still.js`, `hues.js` pure.
- 2026-10-11 (Fable 5.1): item 103 merged as `0a0fdaa` (PR #121, MYP-243 Done): the run page's character card with its moods (`mood.js`, `charactercard.js`), the turns and the answer with the actor's still face, the home's face moving.
- 2026-10-11 (Fable 5.1): item 104 merged as `9261fa9` (PR #122, MYP-244 Done): one `composerFrame` for the three composers, the bar as pills with panels (`popover.js`), drafts kept per composer, the home's face listening.
- 2026-10-11 (Fable 5.1): item 105 merged as `c14b670` (PR #123, MYP-245 Done): the scroll cue on the Models tables, the Activity page's empty sections that point, the Browser tab's facts two and two, Watch gone on an ended run, the tour seeded with a taller run, a scored lesson and a learned skill.
- 2026-10-11 (Fable 5.1): item 106 merged as `8e3818d` (PR #124, MYP-246 Done): the tour read by eye, the offline smoke suite in full (two stale tests fixed), the owner's daemon through the API, one moving face measured at about a millisecond a frame (`test_character_cost.py`). Part 13 built: items 101 to 106. The daemon waits for a restart on `8e3818d`.
- 2026-10-11 (Fable 5.1): part 14 planned, items 107 to 111, from the owner's five screenshots and the readings of the daemon's log and the look card; no code changed. Start with the handoff prompt under "How to start part 14".
- 2026-10-12 (Opus 5.5): item 107 merged as `6c79636` (PR #127, MYP-247 Done): a still character follows a shape or an expression set, the note under Faces that move while the system reduces motion, the favicon allowed by the page's policy (`img-src data:`), `ssl.SSLError` retried as a transport failure (the owner's "bad record mac" came from the network path to OpenCode Go, not from Dot's code), the sidebar's search above New chat and its desktop head on the traffic lights' line.
- 2026-10-12 (Opus 5.5): item 108 merged as `108c91d` (PR #128, MYP-248 Done): the providers' marks (Simple Icons and Lobe Icons, licences in `static/icons/providers/LICENSE`) as masks in the scheme's ink, an icon on every pill of the composer, the mode and the reasoning as pills of their own, hairlines between them, the attach button beside the send.
- 2026-10-12 (Opus 5.5): item 109 merged as `b5d42c0` (PR #129, MYP-249 Done): the computer's panel in three sizes and a drag handle kept per browser (`stagesize.js`), the control pill under the view naming the actor, the character with its name and the word of its state at the head of the chat while the panel is wide or full.
- 2026-10-12 (Opus 5.5): item 110 merged as `6da73c4` (PR #130, MYP-250 Done): a rule said in words, drafted by the review model into its parts (`draft_rule`), the pattern written by Dot so that a rule that lets Dot act fits a command alone and one that stops it fits a word anywhere, read back in a sentence that is the rule's description; the Rules tab's "Say the rule in your words" card, and "Make a rule of this" on an approval card.
- 2026-10-12 (Opus 5.5): item 111 merged as `5d96935` (PR #131, MYP-251 Done): the tour read by eye by two readers and checked wherever they doubted, the offline smoke suite in full (324 passed, two failures that pass alone), the owner's daemon through the API and its two SSL crashes identified; the panel's fold facts wrap under the title at its compact width, an ended run's computer card says why it is empty, the run composer's placeholder follows the run's state.
- 2026-10-12 (Opus 5.5): item 112 merged as `392132c` (PR #132, MYP-252 Done): both eyes of every face move again (`sync` hands the key its index, a repeated key is an error), a test that reads each eye on its own, the gallery test waiting for the orbit's first ring. Part 14 built: items 107 to 112. The daemon waits for a restart on `392132c`.
- 2026-10-12 (Opus 5.5): part 15 planned, items 113 to 116, from the owner's word with two screenshots, the daemon's log (the SSL crash ran before the restart) and a research of open composers; no code changed.
- 2026-10-12 (Opus 5.5): item 113 merged as `ea21f27` (PR #135, MYP-253 Done): the run page without its character card (`charactercard.js` and `mood.js` gone, the state in the top bar's face and badge), the sidebar's head with its button alone at the left where the top bar's opener stands, the brand gone from both bars; the tour checks the button on the lights' line.
- 2026-10-12 (Opus 5.5): item 114 merged as `ea014d5` (PR #136, MYP-254 Done): Dot's mark (a yellow dot with the character's eyes) as the favicon, the app's, the menu bar's and the web app's icons from `desktop/icons/render.py`; the wordmark “dot” on the sign-in card and the desktop shell's down page; the opening, once per window session, never under reduced motion, cut short by a click or a key.
- 2026-10-12 (Opus 5.5): item 115 merged as `a667d39` (PR #137, MYP-255 Done): one composer frame on a 20 px corner with a box of one line, one bar that never wraps (its labels fold by container queries), round pills on no ground, YOLO in the warn ink without a fill, the agent pick a pill with a menu, a quiet send while the box is empty, Keep going's note under the frame.
- 2026-10-12 (Opus 5.5): item 116 merged as `0304257` (PR #138, MYP-256 Done): the shell guard reads a line as the shell does (comments, `$'…'`, here-documents, arithmetic, each substituted command judged on its own, the directories, variables and links a line sets up, paths without regard to case, unreadable code asked about), the terminal's and the desktop's typing judged by the line it runs, a network fetch marks the run untrusted, and what four readers found in the agent, the tools, the server and the interface fixed; 903 unit tests, 84 smoke tests, the eval check and the tour read by eye; the daemon restarted on it with no run active.
- 2026-10-12 (Opus 5.5): item 117 merged as `e51f9c1` (PR #140, MYP-257 Done): `delegate` has no `mode` and its run takes its parent's (a research run's helper is research, every other run's a task), so a yolo task run's helper has every right its parent has; `web_extract` keeps a field answered without a quote as an unverified value instead of failing the extraction; plan step evidence is cut at 400 characters instead of refused; 906 unit tests, 18 smoke tests, the eval check.
- 2026-10-12 (Opus 5.5): item 118 merged as `6dfbf07` (PR #141, MYP-258 Done): every tab Dot opens is a background CDP target with a mark of its own (tabs made at the same time no longer share a page, a race the user's Chrome had since item 98), a tab a page opens itself gives the front back to the application that had it (with Accessibility), and `raise_window` keeps the tabs in front as before; 906 unit tests, 71 browser smoke tests.
- 2026-10-12 (Opus 5.5): item 119 merged as `acfc56a` (PR #142, MYP-259 Done): OpenCode Go's Messages route sends the automatic cache mark (Claude Haiku 5.5 read 10,383 tokens of a repeated prompt from the cache with it, none without), Claude on Go goes to the Messages route (Claude Haiku 5.5 was sent to Chat Completions and refused), the catalog follows Go's list, the models Go serves free say Free in the picker, and Zen's free models stay out (OpenCode serves them only to its own client); 906 unit tests, 38 smoke tests and the live Go tests with the key.
- 2026-10-12 (Opus 5.5): item 120 merged as `6c1a7b3` (PR #143, MYP-260 Done): the run page's top bar keeps the face, the name and the panel's switch (its facts in Details, which gained the agent and the campaign), the composer's send is the run's Stop while the run works and the box is empty (Steer once a word is typed), a delegated run's pill ends with a Stop, Continue is a line above the composer, the top bar's right keeps the approvals waiting and the connection while it is not live, and the emergency stop sits in the sidebar's foot (beside the sidebar's opener while the sidebar is closed); 906 unit tests, 14 smoke tests and the tour.
- 2026-10-12 (Opus 5.5): item 121 merged as `4f2b0cd` (PR #144, MYP-261 Done): the look after T3 Code's, through Dot's tokens and in both schemes: the sidebar a tone darker than the page with no line between them and a chat's last activity at the right of its row, a tool step one quiet line with its body on a hairline, the composer on a corner of its own (24 px) with a soft shadow on every page, the run's panel one card with its sections on hairlines, and the answer's time and length on a second look where a pointer can hover; 906 unit tests, 117 smoke tests and the tour read by eye at both widths in both schemes; the daemon restarted on it with no run active.
- 2026-10-12 (Opus 5.5): part 17 planned, items 122 and 123, from the owner's word after part 16 and a reading of T3 Code's sidebar and chat in its repository; no code changed.
- 2026-10-12 (Opus 5.5): item 122 merged as `10fd83f` (PR #147, MYP-262 Done): where a row's age sits at rest, a chat whose latest run works and every run of the Working section, delegated runs included, show a dashed circle, Working and the time since the run was asked for in T3 Code's units, read every second; `ThreadSummary.latest_created_at` is new; 906 unit tests, 27 smoke tests and the tour.
- 2026-10-12 (Opus 5.5): item 123 merged as `a2de64d` (PR #148, MYP-263 Done): in a run's chat a delegated run's row starts closed, its time counting on every second and at once on a page opened later; an approval it asks for opens it and the rows it sits in until the owner has decided, and a row the owner never touched folds again then and at the run's end; a delegated run has no `ask_user`, which the README's Delegation passage now says; 906 unit tests, 30 smoke tests and the tour; the daemon restarted on it with no run active.
- 2026-10-12 (Opus 5.5): part 18 planned, items 124 and 125, from the owner's word after part 17, a measurement of the home and a reading of the last two days' failed runs (counts and kinds only); no code changed.
- 2026-10-12 (Opus 5.5): item 124 merged as `41ec542` (PR #151, MYP-264 Done): the home's greeting and composer stand in the middle of the space under the top bar however long the day is, the upper row of the grid being half of what their measured height leaves; the Today strip follows in the page's flow, so the sidebar and the top bar stay as the window scrolls, shows two rows a section and folds at its title, kept per browser; 906 unit tests, 15 smoke tests and the tour.
- 2026-10-12 (Opus 5.5): item 125 merged as `36c0eeb` (PR #152, MYP-265 Done): the journal names the day it tells, the date of the middle of the time the reflection read, or its first and last day when that time is longer than a day and a quarter; `browser_snapshot`'s description says it takes no arguments; 907 unit tests and the reflection's smoke test; the daemon restarted on it with no run active.
- 2026-10-12 (Opus 5.5): the rule drafter's fix merged as `8090ebc` (PR #154, under item 110, MYP-266 Done): the owner's sentence "Dot can run everything without permission from me" drafts a rule for every tool instead of a refusal in Spanish, and a sentence no rule can be made of is answered in English, with no note about a broad rule taken for a refusal; a hosted test on the owner's review model, 907 unit tests and the Rules page's smoke tests.
- 2026-10-12 (Opus 5.5): part 19 planned, items 126 to 128, from the owner's word with three screenshots of T3 Code's Settings › Appearance and a reading of T3 Code's source (MIT): the page, its themes with a theme per scheme, its interface, motion and type settings, and themes of one's own kept by the daemon; no code changed.
- 2026-10-12 (Opus 5.5): item 126 merged as `c5dfc76` (PR #157, MYP-267 Done): Settings › Appearance holds the colour scheme as three miniatures of the window and the themes as a gallery with a light and a dark orb each, after T3 Code's page; T3 Code's six palettes are ported and mapped onto Dot's tokens, every theme held to Dot's contrast rule in both schemes as the browser paints it (a dark card a palette draws on the page is lifted a step above it); a theme per scheme rides on the page element as `light-dark()` pairs and is there before the first paint; ⌥⌘A opens the tab and ⇧⌥⌘A steps through the schemes.
- 2026-10-12 (Opus 5.5): item 127 merged as `9c73d80` (PR #158, MYP-268 Done): Settings › Appearance gains Interface (the contrast, 50 to 200 %, mixed as T3 Code mixes it; the chat's width; the density and the two layout switches), Motion (the panels' slide, 0 to 400 ms, and the faces that move) and Type (the interface's font and size, the answers', the composer's and the code's fonts and sizes with a preview each, font smoothing and wrapped code); every setting is kept in the browser and put on the page by `boot.js` before the first paint, nothing when it is the default; a range control joins `#/controls`; 907 unit tests and a smoke test that sets each one and reads it on the page and after a reload.
- 2026-10-12 (Opus 5.5): item 128 merged as `30b2434` (PR #159, MYP-269 Done): themes of one's own, made in an editor from a background, an accent and an action colour (T3 Code's generator, ported), duplicated, edited, exported and added from T3 Code's theme file, its short form or a VS Code colour theme, kept by the daemon in its home's `themes` folder (checked as T3 Code checks a file, capped, written whole, read without following links) and held to Dot's contrast rule; a review against the done criteria found two faults, fixed before the merge; 929 unit tests and a smoke test in a browser; the daemon restarted on it with no run active; part 19 built.
- 2026-10-12 (Opus 5.5): part 20 planned, items 129 to 131, from the owner's word with a screenshot of T3 Code's thread menu and one of a run page whose composer stood near the top, and a reading of T3 Code's source (MIT): the composer at the foot of the window, a chat's menu (pin, rename, copy the link, select, delete) and unread chats, the pin, the name and what was seen kept by the daemon; Settle, Snooze, Regenerate title and Archive stay out; no code changed.
- 2026-10-12 (Opus 5.5): item 129 merged as `048f3aa` (PR #161, MYP-270 Done): the run page's column is at least as tall as the window and the conversation grows into the room a short one leaves, so the composer stands at the window's foot from the first message, on a desktop with the details panel open or closed and on a phone; a long run scrolls under it as before; a smoke test reads the composer's foot at the window's.
- 2026-10-12 (Opus 5.5): item 130 merged as `d7e7d5e` (PR #162, MYP-271 Done): a chat's menu after T3 Code's thread menu, from a "…" button, a right-click or the context-menu key: Pin (a Pinned group above the days, kept by the daemon in `threads.pinned_at`), Rename in the row, Copy link, Select and Delete; `PATCH /api/threads/{id}`; a review against the done criteria found them met and five edges, fixed before the merge; 930 unit tests and a smoke test in a browser on a desktop and a phone.
- 2026-10-12 (Opus 5.5): part 21 planned, items 132 to 134, from the owner's word with a screenshot of a run on a hosted model that ended with a report and suggestions and one of the desktop window's top left, and a reading of the store by kinds and numbers: runs that keep going on every model when asked to, the composer's choices on a chat's page and the traffic lights on the line of the top bars; no code changed.
- 2026-10-12 (Opus 5.5): item 132 merged as `002575b` (PR #164, MYP-273 Done): words that ask a run not to stop, in English or in Turkish, put a task or research run a person starts in goal mode whatever the switch says, the goal checker is told and accepts a finish only when every part of the goal is done and shown, every goal check sends back an answer that offers to do a part of the goal not done yet, and every model is told to keep acting and to take the ask at its word; the Details card reads "On, asked not to stop". A review against the done criteria found two blockers, fixed before the merge: a phone's curly apostrophe, and an offer rule that reached past the goal.
- 2026-10-12 (Opus 5.5): item 131 merged as `34e1fe1` (PR #165, MYP-272 Done): a chat whose latest run ended after it was last seen shows its dot in the accent and says unread, as T3 Code marks a thread with a turn not yet seen; the daemon keeps `threads.seen_at` (the chats of an older store taken as seen), `GET /api/threads` carries `unread` and `PATCH /api/threads/{id}` takes `seen`; the open chat is seen while the page is in view, and its menu offers Mark read and Mark unread. A review against the done criteria found them met and one regression, fixed before the merge: a chat opened by a key lost the focus when its dot went.
- 2026-10-12 (Opus 5.5): item 133 merged as `bf11312` (PR #166, MYP-274 Done): the dock of a chat's page wears the home's pills, after T3 Code's composer: the mode, the model with its reasoning and the Options, set at first to the open run's and held by the page, and a follow-up starts its run with them; while a run works the pills wait for the next follow-up and say so. The pickers are one module that both boxes use. A review against the done criteria found them met and the smoke test short of their negative cases, which it now reads.
- 2026-10-12 (Opus 5.5): item 134 merged as `a5b0220` (PR #167, MYP-275 Done): the desktop window puts its traffic lights 26 px under the top, on the centre line of the top bars' 52 px rows (`traffic_light_position` 20, 28), and the sidebar's head grows to the bars' height, so the lights, the button that folds the sidebar and the top bar's opener and emergency stop stand on one line with room above and below; the page keeps 96 px for the lights. Part 21 is done; the window is the owner's to read once the app is rebuilt.
- 2026-10-12 (Opus 5.5): part 22 planned, items 135 to 139, from the owner's word asking for a check of everything written so far: four reviews of the code by area, each finding read again in the code and ranked by weight, the offline smoke suite on main and the dependencies against the advisories; the fixes follow in that order, one pull request each.
- 2026-10-12 (Opus 5.5): item 135 merged as `cb76e7d` (PR #169, MYP-276 Done): Dot's own folders (what it loads and runs, and the emergency stop's file) are a path class of their own, read like the home folder and changed like a protected location; the shell guard learns more write targets; a new flag, `upload`, asks before a shell line sends data out once the run has read untrusted content; an agent answers a message in the stricter of its approvals and the sender's; the browser never opens Dot's own interface and the desktop tools never act in Dot's own window outside `yolo`; `truncate` outside the workspace is destructive.
- 2026-10-12 (Opus 5.5): item 136 merged as `bb18c32` (PR #170, MYP-277 Done): the gates of all runs share one lock per site for its pace and one for its spending, so two runs that pay or post on one site at once are judged one after the other; the emergency stop ends every active run as an emergency stop, and while it is engaged no schedule fires and no campaign session starts; a run stopped before its first step ends as any stop does; a takeover brings up the run's own browser and frees the run when that fails; a schedule or a campaign changed while its run was being started is read again first; a run whose end cannot be recorded is logged and freed, and a message sent as a run ends is refused instead of dropped.
- 2026-10-12 (Opus 5.5): item 137 merged as `43cf3f9` (PR #171, MYP-278 Done): a reply without tool calls that the model service cut short, at its output limit or for an abnormal reason such as a content filter, is no longer taken as the run's answer in any mode: the model is told how the reply ended and asked to go on or answer again in fewer words, each such reply counts as a nudge, and a run whose replies keep being cut short fails saying so (the ending `cut_short`); an empty reason of Ollama is a normal stop.
