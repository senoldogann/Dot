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
order its "Order and dependencies" paragraph gives.

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
- The owner's daemon runs the code it loaded when it started. Python changes take effect after the owner restarts it;
  the static web files are served from disk.

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
  and stable datasets; add the minimum the change needs. Use temp directories (`DOT_HOME`, `DOT_CHROME_DIR`, and
  `DOT_LOG_DIR` for a daemon and for every other `dotx` command, `dotx eval check` included: each one logs to
  `<log dir>/dotx.log`, which otherwise is the owner's `~/Library/Logs/Dot`) and never the owner's data directory
  (`~/Library/Application Support/Dot`): not even to read message text. Counting rows is the most that was ever done
  there.
- Safety: never start, stop or restart the owner's launchd daemon. Nothing that deletes the owner's data may run by
  default: retention and cleanup are explicit commands. Commit locally, one commit per item or coherent step, with the
  agent's `Co-Authored-By` trailer; never push, never rewrite published history. `PLAN.md` (the owner's working notes,
  untracked on purpose, excluded in `.git/info/exclude`) is not committed.
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

| #   | Item                                                                   | Status  | Commits |
| --- | ---------------------------------------------------------------------- | ------- | ------- |
| 9   | Time accounting: where the seconds of a run go                         | planned |         |
| 10  | Yolo asks nobody: secret fields, questions and the end of a run        | planned |         |
| 11  | Open-ended goals in one run: targets, budgets and an honest end        | planned |         |
| 12  | Campaigns: a goal that works until its outcome is verified             | planned |         |
| 13  | Faster browser and desktop steps                                       | planned |         |
| 14  | Rich editors, drag and bulk text in the browser                        | planned |         |
| 15  | Helper models: consult, delegate to another model, standby             | planned |         |
| 16  | Typed judgments (TypeSafe) at the decision points                      | planned |         |
| 17  | Pacing and public actions on social platforms                          | planned |         |
| 18  | Skills written for Claude Code, and the X playbook                     | planned |         |
| 19  | Prompt-cache hygiene for the hosted providers                          | planned |         |
| 20  | An interactive terminal for programs that prompt                       | planned |         |

**Order and dependencies.** 9 first: it is cheap and every later item is judged with it. 10, 11 and 12 are the owner's
main ask and build on each other (12 needs the left questions of 10 and the targets of 11). 13 and 14 are the speed
ask and depend on nothing. 15 before 16: both handle a provider that is not there. 17, 18, 19 and 20 are independent;
18 is an afternoon. The owner may reorder; write the new order here when they do.

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

**Status: planned.** **Goal.** Every model call and every tool call of a run leaves its duration and its tokens in the
store, and `dotx stats` and `dotx eval` show where the time goes, per model and per tool.

**Why.** Items 13, 14 and 19 promise speed; without this item their effect is a guess. Today the store has
`tool_result.seconds` (with the approval wait inside), the timestamps of events, and the run's token totals; the model
call itself, the compaction, the review calls and the cache reads leave nothing (see the audit list above).

**Design.**
- `ChatRequest` gets `purpose: CallPurpose`, with `CallPurpose` the literal of `step`, `compaction`, `review`,
  `goal_check`, `progress`, `lessons`, `vision`, `attachment` and `consult`
  (`llm/types.py`); every constructor names its purpose (`agent/loop.py`, `agent/review.py`, `agent/vision.py`,
  `agent/attachments.py`, `agent/aside.py`, `agent/lessons.py`, `evals/scripted.py`, the smoke tests).
- `ModelRouter.complete` (`llm/router.py:104-106`) measures the call and hands a `ModelCall(purpose, model, session_key,
  seconds, input_tokens, output_tokens, cached_tokens, error)` to a recorder the runtime sets:
  `runtime.calls.record(call)`. The recorder resolves the run from the session key (one active run per thread,
  `agent/manager.py:348-353`; a review question's `session_key` is the thread id too, `agent/review.py:386`) and
  writes a `model_call` event for that run. A call with no active run (`dotx eval check`, `dotx models profile`) is
  logged, not stored. Failures are recorded with `error` set to the error type and `seconds` to the time spent.
- The `assistant` event (`agent/loop.py:882-895`) gets `seconds` and `cached_tokens` too, so the timeline can show them
  per step without joining events.
- `tool_result` gets `waited_s`: the time `gate.authorize` spent waiting for an approval (`agent/executor.py:100`,
  measured around it; `_PausedClock` at `:102` already knows it). `seconds` stays the whole call, as today.
- `store/stats.py`: `ModelStats` gets `model_seconds`, `step_seconds_median`, `step_seconds_p90`, `compaction_seconds`,
  `side_call_seconds` (every purpose but `step`), `cached_tokens`; a new `ToolStats(tool, calls, failed, seconds_total,
  seconds_median, waited_s)` per tool name. Both are read from the events of the runs in the window, read-only, no
  text. `dotx stats` prints a "Time" table per model and a "Tools" table (the 15 tools with the most seconds). A step's
  seconds come from its `model_call` event (`purpose = "step"`); runs older than this item have none and are left out
  of the time columns, with their count printed.
- `dotx eval`: `RunMetrics` (`evals/results.py:22-34`, `evals/metrics.py:18-31`) gets `model_seconds` and
  `tool_seconds`; the scorecard (`evals/scoring.py:88-115`) prints seconds per pass next to steps and tokens, and the
  comparison table (`:160-170`) gets a seconds column. The replay of `dotx eval check` reports zero model seconds, as
  the scripted model makes no call.
- The eval runner stops depending on the live bus for the end of a run: `_supervise` (`evals/runner.py:188-211`)
  checks `Subscription.dropped` and re-reads the store when events were dropped, as `server/stream.py:128-130` does.
- Side-call tokens join the run's totals: `update_run_progress` adds the tokens of every `model_call` of the run, so
  `dotx stats` and the Details panel stop under-counting (the eval token budget at `evals/runner.py:202-206` reads the
  same totals).

**Touchpoints.** `llm/types.py`, `llm/router.py`, `runtime.py` (the recorder), `agent/loop.py`, `agent/executor.py`,
`agent/review.py`, `agent/vision.py`, `agent/attachments.py`, `agent/aside.py`, `agent/lessons.py`, `store/stats.py`,
`store/db.py` (a `model_call` kind needs no schema change: events are JSON), `cli_stats.py`, `evals/results.py`,
`evals/metrics.py`, `evals/scoring.py`, `evals/runner.py`, `evals/scripted.py`, `timeline.js` (seconds on a step),
README "Measuring the agent".

**Done when.** Unit tests of the aggregation on a hand-written event list (`tests/unit/test_run_statistics.py`:
medians, p90, tools table, runs without `model_call` events left out); a smoke test with the scripted provider that
checks one `model_call` event per step and one per compaction; `dotx stats` on a throw-away home prints the tables;
`dotx eval check` clean; the numbers of one scripted eval run (seconds per task) written here as the baseline for
items 13, 14 and 19.

### 10. Yolo asks nobody: secret fields, questions and the end of a run

**Status: planned.** **Goal.** A `yolo` run, a scheduled run and a delegated run never wait for a person. What the
run can read and do itself, it does; a question for the owner is left in the thread and the run goes on.

**Why.** On 2026-10-09 a `yolo` run applying for a job stopped at an e-mail verification code that had arrived in the
owner's webmail, which the run could open, and asked the owner to type the six digits. Three things made it ask: the
browser's tool-level refusal of one-time-code fields in every mode (`browser/driver.py:280-286`), the prompt line "one-
time codes are never typed by you: use ask_user" (`agent/prompt.py:186`), and `ask_user`, which blocks for 900 s in
`yolo` as in `ask` (`tools/meta.py:84-106`, `agent/approvals.py:45-66`). The owner's decision of 2026-10-07 already
makes `yolo` waive the credential hard stop; the tool and the prompt did not follow. Separately, a task run ends on
the third text-only reply even when tool calls came between them (`agent/loop.py:936-946`), so a status line counts
toward the end of a run that is not finished.

**Design.**
- **Secret fields follow the approval mode.** The check moves out of `run_step` (`browser/driver.py:348`, `:351`) to
  the tool: `BrowserSession.secret_fields(thread_id, refs) -> dict[str, str]` runs `_SENSITIVE_FIELD_SCRIPT` for the
  refs of the `fill` and `type` steps and returns the kind of each secret field. `browser_act` calls it before `act`
  unless the run is `yolo` (`ToolContext` gets `approvals: ApprovalMode`, the run's mode, set at `agent/loop.py:787`
  where the context is built; the gate knows it already, `agent/gate.py:101-102`), and refuses as today ("refusing to
  type into a one-time-code field: ask the user to
  enter it themselves") with no browser action run. In `yolo` the steps run; the classification of a typing step keeps
  the `credential` flag from the field's name (`policy/elements.py:30-37`), so the gate records the yolo approval as
  for any waived hard stop. The desktop's secure-field refusal (`desktop/actions.py:409-414`) follows the same rule
  through the same `ToolContext.approvals`; password managers stay refused in every mode (`desktop/session.py:132-137`)
  unless
  the owner decides otherwise (open decision 7). Screenshots keep blacking out secure fields.
- **The prompt says what yolo may do.** The trust rules (`agent/prompt.py:180-190`) become a function of the approval
  mode. In `ask` and `auto`: as today. In `yolo`: "Passwords, one-time codes and payment details: enter them when you
  can read them yourself (a code in a mailbox you can open, a value the user gave you); never guess one, never read a
  secret store the task does not need, and say in the answer which you entered." The `yolo` line of
  `_APPROVALS_TEXT` says that nobody answers questions during the run and that a question is left for later.
- **Questions that do not block.** `[agent] questions = "wait" | "leave"` decides what `ask_user` does in a run nobody
  watches: a run in `yolo`, a scheduled run, an eval run (delegated runs have no `ask_user`, `agent/loop.py:737-738`).
  With `leave` (the default, the owner's ask of 2026-10-09) `ask_user` creates the approval record with severity
  `question` as today, so the card shows in the UI and in the timeline, but does not set the run to `blocked` and does
  not wait: its result is "The question is left for the user, who is not asked to answer now. Continue with what does
  not depend on the answer; if nothing can be done without it, finish and put under *What I need from you* exactly
  what you need." A `wait` run behaves as today. `ask` and `auto` runs started by the owner always wait.
  `ApprovalBroker.leave(run_id, tool, summary, severity, args)` writes the record and emits `approval_requested` with
  `"blocking": false`; the answer path (`POST /api/approvals/{id}`) accepts an answer for a left question after the run
  ended: it stores it, appends "Answer to the agent's question …: …" to the thread's instructions
  (`agent/manager.py:423-433` reads them at the next run) and, when the thread has no active run, offers Continue
  (a new run on the thread whose opening text is the answer, through the resume path of item 6 with `resumed=None`).
  The web UI lists left questions of ended runs under the composer ("Dot asked you") until they are answered or
  dismissed; `dotx run` prints them after the answer.
- **The end of a run counts only consecutive text replies.** `nudges` is reset to 0 whenever a reply carries tool
  calls (`agent/loop.py:900`), so only `finish_nudges + 1` text-only replies in a row end a task run. `finish` stays the
  normal end; `FinishArgs` gets `needs_from_user: str` (may be empty) that the run page shows as "What Dot needs from
  you" and that a campaign (item 12) carries into its ledger.
- **Events and the record.** `approval_requested` with `blocking`; `question_left` is not a new kind: the record's
  `status` stays `pending` until answered; a pending left question of an ended run is shown, never expired by the
  broker's timer (it has none), and `dotx prune` leaves it.

**Touchpoints.** `browser/driver.py`, `browser/session.py`, `tools/browser.py`, `tools/desktop.py`,
`desktop/actions.py`, `tools/context.py` (the new `approvals` field), `agent/prompt.py`, `tools/meta.py`,
`agent/approvals.py`, `agent/loop.py`, `agent/manager.py`, `server/routes_runs.py` (answers after the end, Continue),
`store/db.py` (left questions by thread), `approvals.js`, `views/run.js`, `timeline.js`, `config.py`, `defaults.toml`,
README "Safety" and "Autonomy", `tests/smoke/test_approval_modes.py`, `tests/smoke/test_browser.py`.

**Decisions and limits.**
- Nothing here lets `ask` or `auto` do more: secret fields are refused before any action in those modes, as today,
  and their questions wait.
- A left question is still a question: a run that cannot proceed without the answer finishes honestly (`success =
  false`, the need in `needs_from_user`), and a campaign session (item 12) picks the answer up at its next session.
- The one-time code of the 2026-10-09 run would now be read from the webmail tab and typed, with the `credential` yolo
  approval in the timeline.

**Done when.** Smoke tests with the scripted model: a `yolo` run whose `ask_user` gets the left result at once, the
record pending, the run status never `blocked`, the answer after the end stored as an instruction and Continue
starting a run with it; a scheduled run the same; an `ask` run waits as before (`test_approval_modes.py`). Browser
smoke on the fixture page: an OTP field is refused in `ask` and filled in `yolo`, with the `credential` approval
recorded. Unit test of the nudge reset. The prompt difference between the modes checked byte for byte in
`tests/unit/test_scaffold.py` style. Timeline and the "Dot asked you" list checked in headless Chrome on a throw-away
daemon.

### 11. Open-ended goals in one run: targets, budgets and an honest end

**Status: planned.** **Goal.** A run given an outcome instead of a task ("grow", "earn", "find a way", "as many as
possible") turns it into measurable targets, works toward them, and ends only with the targets reached or with an
honest account of what is missing; the owner can bound such a run in minutes or tokens when they want to.

**Why.** The prompt was written for precise tasks: "take the smallest reasonable reading", "add nothing that was not
asked for" (`agent/prompt.py:81-84`). Nothing tells the model what "done" means for an open goal, so a run can end
after a few clicks and say so; the goal check (off by default) can only judge against the goal text. There is no
budget of any kind (`agent/loop.py:831`, README "Status and known limits"); the owner wants runs that keep going, and
a bound they can set when they do not.

**Design.**
- **Wording.** `_WORK` (`agent/prompt.py:76-108`) gets a section:
  "# Open-ended goals. A precise request is done exactly as asked, with the smallest reasonable reading. A goal that
  names an outcome instead of a task (grow, improve, earn, reach, as many as possible, find a way) asks for the
  outcome: choose the approach yourself; before acting, write the targets you will reach and how you will verify them
  with your own tools into the plan (`update_plan`, `targets`), and the budget you intend to spend; prefer the channel
  that brings the first measurable result soonest; after each round measure, write the numbers, and change approach
  when a round brought nothing; do not call finish before the targets are met, unless the budget is spent or the next
  step needs something only the user can give; then finish with `success = false`, the numbers reached and what is
  missing." The line "When a request can be read in several ways, take the smallest reasonable reading" becomes "When a
  precise request can be read in several ways, …".
- **Targets in the plan.** `PlanPayload` (`agent/plan.py:24-32`) gets `targets: str` (may be empty; at most 600
  characters): the measurable outcome and how it is verified. `update_plan` takes it, the plan render shows it under
  the steps, the live plan of the UI shows it as its first line, and a resumed run inherits it with the plan
  (`agent/loop.py:727-729`).
- **The goal check reads the targets.** `GoalRequest` (`agent/review.py`) gets `targets`; `_goal_prompt` tells the
  checker to judge against the targets when they are set, from the facts and the digest, and against the goal text
  otherwise. `achieved` needs the targets met or the answer to say honestly that they are not, with `success = false`.
- **Budgets, opt-in.** `[agent] run_max_minutes = 0` and `run_max_tokens = 0` (0: none). After each step the loop
  compares the run's wall time and `input_total + output_total` with them; at the first step past a limit it appends a
  note ("The budget of this run is spent: N minutes / N tokens. Finish now with what was reached, the numbers and what
  is missing.") and offers only the core tools, like the re-plan rung of item 2; if the next reply still does not
  finish, the run ends `stopped` with a result built like the watchdog's (`agent/progress.py:321-336`) and a new
  ending `budget` in `store/endings.py`. Event `budget_reached {minutes, tokens, limit}`. A campaign session
  (item 12) uses the same mechanism per session.
- **Delegated and scheduled runs** inherit nothing new: budgets are per run from the configuration; a campaign may set
  its own (item 12).

**Touchpoints.** `agent/prompt.py`, `agent/plan.py`, `tools/meta.py` (`update_plan`), `agent/review.py`,
`agent/loop.py` (`_check_goal`, the budget check), `store/endings.py`, `store/stats.py` (the new ending counts),
`views/run.js` (targets in the live plan), `timeline.js`, `config.py`, `defaults.toml`, README "How a run works" and
"Autonomy".

**Decisions and limits.**
- Budgets stay 0 by default: the owner asked for runs that do not stop (open decision 6 holds the numbers they may
  want for scheduled runs).
- The wording cannot be measured offline; `dotx eval` gets one open-ended task whose check is a file with numbers
  written by the run (`research`-like, local files), so a regression that stops early shows as a failed check.

**Done when.** Unit tests of `PlanPayload.targets` rendering and of the budget decision (pure: time, tokens, limits);
smoke with the scripted model: a run past `run_max_tokens` gets the note, the restricted tools and ends `budget` when
it does not finish; the goal checker's prompt carries the targets (test in `tests/unit/test_verify.py` style); the
eval task added and `dotx eval check` clean; README updated.

### 12. Campaigns: a goal that works until its outcome is verified

**Status: planned.** **Goal.** The owner switches on *Keep going until done* for a goal, and Dot works on it in
sessions, through restarts and days, until the outcome is verified by evidence or the owner stops it. Nothing else
stops it.

**Why.** This is the owner's main ask of 2026-10-09: "a system that does not stop until I see the result; when I say
earn money and give the information, it must not stop until the money is where it belongs". Today a run ends by
`finish`, by text replies, by the watchdog or by the goal check's patience (`agent/loop.py:936-975`); a schedule the
agent creates cannot carry the owner's approval mode (`tools/schedule.py:99-101`) and starts every firing in a new
thread with only the goal text (`scheduler/runner.py:105-107`); the prompt forbids saving progress
(`agent/prompt.py:166-167`); so no run knows what the last one did.

**Design.**
- **The campaign.** A row in a new table `campaigns`: `id`, `title`, `goal`, `targets` (text, set by the agent),
  `status` (`active`, `achieved`, `stopped`), `thread_id` (the owner's conversation, where it was started),
  `schedule_id` (its one schedule), `mode` (`task` only), `options` (`ScheduleOptions`: the model, effort, approvals
  and goal mode of the opening run, so a `yolo` campaign stays `yolo`), `sessions`, `created_at`, `updated_at`,
  `last_progress_at`, `achieved_evidence`. `RunRecord` gets `campaign_id: str | None`. The ledger is a table
  `campaign_entries(id, campaign_id, run_id, ts, kind, text, numbers)` with `kind` in `progress`, `setback`, `note`,
  `strategy`, `question`; `numbers` a JSON object of metric name to number.
- **Starting one.** `StartRunRequest.standing: bool | None` (`server/views.py:74-84`), the composer's third switch
  "Keep going until done" next to Approvals and Goal mode, and `dotx run --standing`. The daemon creates the campaign
  (title: the first 60 characters of the goal), then starts the opening session as a run with `campaign_id` set. A
  campaign is `task` mode only; `chat` and `research` refuse the switch (422).
- **A session.** Every run of a campaign is a session. Its opening text is the brief: the goal, the targets, the newest
  `strategy` entry, the last 12 ledger entries (newest last, with their numbers), the questions left for the owner
  (item 10) with their answers or "unanswered since …", the session number and the time since the last session. Each
  session is a new thread (as schedules do today): bounded context, and the brief is the memory. The system prompt gets
  a `# Campaign` section only in campaign runs: "This is session N of a campaign. Do not start from zero: the brief
  says what was done. Work toward the targets; write what you did, with numbers and evidence, with `campaign_note`
  before you finish; verify the targets with your own reads before you claim them; when a round brought nothing, write
  a new `strategy` entry and follow it; leave the user a question only for what only they can give, and go on with the
  rest. Finish when this session's work is done; the campaign goes on."
- **Tools, group `campaign`,** offered only in campaign runs (`tools/groups.py`): `campaign_targets(targets,
  verification)` sets the targets and how they are verified (the opening session must call it before it finishes: a
  finish without targets is sent back once, like the plan reminder); `campaign_note(kind, text, numbers)` appends a
  ledger entry; `campaign_schedule(at | cron)` sets when the next session runs (`at` a time, or a cron with the
  scheduler's minimum of 5 minutes); `campaign_achieved(evidence)` claims the outcome. All four are kind `meta`,
  risk `safe`, except `campaign_schedule`, which carries `persistence` like `schedule_create`.
- **It does not stop.** When a session ends and the campaign is `active`, the harness makes sure a next session is
  scheduled: if the agent set none, `[agent] campaign_gap_minutes` (60) from now. The campaign's schedule is a normal
  `ScheduleRecord` with `campaign_id` and the campaign's `options`; the scheduler starts the session with
  `campaign_id` and the brief (`scheduler/runner.py:108-120`). A session that the daemon cut off is resumed by the
  next session's brief, which says so. When the last session finished with `needs_from_user` set and every question
  is unanswered, the next session waits for an answer or `[agent] campaign_wait_hours` (24), whichever comes first,
  then runs anyway and tries another way. After `[agent] campaign_patience_sessions` (3) sessions without a `progress`
  entry the brief says so and asks for a new strategy entry first.
- **Verification, not a claim.** `campaign_achieved(evidence)` asks the goal checker (`agent/review.py:443`,
  `goal_checker_for`) whether the targets are reached, from the targets, the verification method, the ledger's
  `progress` entries with their numbers, the session's measured facts (`RunFacts`) and the evidence text. `achieved`:
  status `achieved`, the schedule disabled, a `notify` to the owner, the campaign page shows the evidence. `continue`
  or `blocked`: the call fails with the checker's reason and the session goes on. With item 16 on, the typed judge
  answers first and only a likely claim reaches the review model. The owner can mark a campaign achieved by hand.
- **Only the owner stops it.** `POST /api/campaigns/{id}/stop` and `dotx campaign stop ID` set `stopped` and disable
  the schedule; a running session ends normally; `resume` sets it `active` again and schedules a session now. A stopped
  or achieved campaign's thread stays.
- **Budgets, opt-in.** `[agent] campaign_max_sessions = 0` (none); item 11's run budgets apply per session.
- **UI and CLI.** A "Campaigns" view (`views/campaigns.js`): the list (title, status, sessions, next session, last
  progress), a page per campaign (targets, the ledger with numbers, the sessions with links to their runs, the open
  questions, Stop, Resume, Mark achieved). `dotx campaign list | show ID | stop ID | resume ID`. The run page of a
  session carries a chip with the campaign and its session number.

**Touchpoints.** `store/db.py` (tables `campaigns`, `campaign_entries`, `runs.campaign_id`, `schedules.campaign_id`),
`store/models.py`, new `agent/campaign.py` (pure: the brief, the patience and wait decisions, the next-session rule),
new `tools/campaign.py`, `tools/groups.py`, `agent/prompt.py`, `agent/loop.py` (the brief as the opening, the
send-back for missing targets, the end-of-session hook), `agent/manager.py`, `scheduler/plans.py`, `scheduler/runner.py`,
`server/views.py`, new `server/routes_campaigns.py`, `cli.py` and a new `cli_campaign.py`, `views/campaigns.js`,
`sidebar.js`, `runmodes.js` (the switch), `config.py`, `defaults.toml`, README (a "Campaigns" section).

**Decisions and limits.**
- A campaign has no step limit and no end but the owner's: that is what was asked. The three opt-in numbers above are
  the only brakes, and `dotx stats` shows what a campaign costs (item 9).
- Sessions are new threads on purpose. The owner's conversation thread shows the opening session and is where they
  talk to the campaign: a message sent there becomes an instruction of the next session (through the thread
  instructions the brief carries, read from the campaign's `thread_id`).
- Money and credentials: nothing new. A `yolo` campaign does them itself and records them; an `ask` campaign leaves
  questions and waits for the owner at its sessions.
- Live behavior cannot be tested here: the smoke tests use the scripted model and a scripted goal checker.

**Done when.** Unit tests of `agent/campaign.py` (brief rendering, the next-session rule, patience, the wait on
unanswered questions); smoke with the scripted model: an opening session without `campaign_targets` is sent back,
one with it ends and a schedule exists at `campaign_gap_minutes`; a session fires through the real scheduler on a
throw-away daemon with a short gap, gets the brief, writes an entry, and a `campaign_achieved` with a scripted
`achieved` verdict closes the campaign and disables the schedule, a `continue` verdict fails the call; stop and
resume through the API; the Campaigns view checked in headless Chrome in both themes at 1280 and 390 px; `dotx
campaign` on a throw-away home; README.

### 13. Faster browser and desktop steps

**Status: planned.** **Goal.** Take the fixed waiting out of a browser or desktop action where no signal needs it,
and make a failed wait or an unchanged page cost no extra step.

**Why.** Measured on 2026-10-09 (read-only bench, this Mac; the Playwright and accessibility parts not measured): a
browser click waits about 160 ms by construction, a five-click batch about 0.32 s; a desktop click about 0.56 s before
any accessibility work, a five-click batch about 1.46 s, plus one unconditional closing walk capped at 4 s. On top,
`browser_wait_text` failed 22.4 % of the time in the real history and returns no view when it fails, so a failed wait
costs the wait, a model turn and a snapshot. Item 9 measures the effect of every change here.

**Design, browser** (`browser/session.py`, `browser/driver.py`):
- The settle after a batch (`:837`) runs only when a step may have changed the page: a batch of `hover` and
  `scroll_into_view` steps only skips it; `nav_idle` (`:615`) always runs. The second settle when a batch leaves its
  tab (`_full_view` at `:691`) goes: `act` builds the view of the new tab from the state it already settled. The
  current tab's title is fetched once (`:674` duplicates `:661`).
- The 40 ms grace (`:811-812`, `:820-822`) runs after steps that can open a popup or a dialog (`click`, `double_click`,
  `right_click`, `press`, `check`, `uncheck`, `select`), not after `fill`, `type` and `hover`; the pending-adoption
  wait (`:823-825`) stays.
- The fixed 150 ms sleep after a wheel scroll (`:857`, `_SCROLL_SETTLE_S` at `:84`) goes; the settle at `:858` stays.
- `browser_wait_text` (`tools/browser.py:400-424`, `BrowserSession.wait_text`) returns one line and the delta view:
  "“Order placed” appeared after 1.3 s" with `_changes_view`; on timeout it returns a failed result that still carries
  the delta view: "“Order placed” did not appear within 10 s; the page now:", so the model needs no snapshot to decide.
- `settle_quiet_ms` (120) and `settle_max_ms` (1 500) stay knobs; the fixture timing test runs with 120 and 60 and the
  numbers go to open decision 13.

**Design, desktop** (`desktop/session.py`, `desktop/input.py`, `desktop/actions.py`):
- One `lsappinfo` listing per act: `_preview_job` (`:227`) keeps its listing in the preview result and `act` reuses
  it in `_open_env` (`:547`) when no approval waited in between (the listing is re-read after a wait).
- The settle after a side-effect batch (`:636-637`, 350 ms) becomes a poll: `windows_on_screen` (3 ms measured) and the
  focused element every 40 ms until two reads agree, with a floor of 80 ms and `settle_ms` as the cap. The closing
  walk (`:638`) stays: the model needs the state after the batch.
- Long text is pasted: a `type` step whose text is longer than `[desktop] paste_threshold_chars` (40; 0 never) goes
  through the paste path (`input.py:365-377`) unless the target is a secure field; the clipboard is restored as today.
  Typing stays key by key below the threshold (`input.py:300-316`).
- The step gaps (`:61`, `:622-625`) and the click pacing (`input.py:175-177`) stay: no acknowledgement exists between
  posted events, and the comment at `input.py:175` says why the move pause is there.
- `screenshot_max_edge` (1 920) is a knob for open decision 13: a 1 710 × 1 112 JPEG is about 2 500 tokens on the
  hosted providers; 1 366 would be about 1 600.

**Touchpoints.** `browser/session.py`, `browser/driver.py`, `browser/types.py` (the popup-opening set),
`tools/browser.py`, `desktop/session.py`, `desktop/input.py`, `desktop/actions.py`, `config.py`, `defaults.toml`,
README "Browser use" and "Computer use", `tests/smoke/test_browser.py`, `tests/smoke/test_desktop.py`.

**Done when.** `tests/smoke/test_browser.py` gets a timing test on the fixture pages (20 single-click acts and 5
five-step batches, medians printed and asserted under 1.5 s and 3 s on this Mac) and the wait_text tests (success
returns the line and the delta; timeout returns a failed result with the view); `tests/smoke/test_desktop.py` (under
`DOT_DESKTOP_TESTS=1`) gets the same timing test against the fixture window and a paste test for long text; the
medians before and after each change written here; the offline smoke suite clean; README updated.

### 14. Rich editors, drag and bulk text in the browser

**Status: planned.** **Goal.** Typing works in the editors of X, GitHub, Google Docs, Monaco and CodeMirror; the
browser can drag an element onto another; a long text goes in as one insertion.

**Why.** On X's composer `type` lost the first character and `fill` reported done without a change (follow-up of
2026-10-07). The cause is in `browser/driver.py:349-352`: `type` is `press_sequentially` and `fill` is `locator.fill`;
editors built on contenteditable (Draft.js and Lexical on X, ProseMirror, Monaco, CodeMirror 6) set their selection
asynchronously after focus and ignore a programmatic `fill`. The browser has no `drag` step
(`browser/types.py:9-22`), which web IDEs, boards and file uploads by drop need. This item is also the browser half of
"write code in the browser".

**Design.**
- **Editable targets.** Before a `fill` or `type`, `run_step` asks the element once:
  `el.isContentEditable || el.closest('[contenteditable]:not([contenteditable="false"])') !== null`. For an editable
  target: `locator.focus()`, then poll (every 25 ms, at most 500 ms) until `document.activeElement` is the element or
  inside it; then `type` uses `page.keyboard.insert_text(text)`, and `fill` selects all (`ControlOrMeta+A`) and
  inserts the text the same way. A step whose target never took focus fails with "the editor did not take focus" and
  the element. For `<input>` and `<textarea>` nothing changes: `fill` fills, `type` presses key by key.
- **New steps.** `insert(ref, text)`: focus as above, then `insert_text` for any target: long text without key
  events. `drag(ref, to_ref)`: `locator.drag_to(target)` (Playwright drives the mouse and dispatches the HTML5 drag
  events); `Step` gets `to_ref: str | None`, `StepAction` gets `drag` and `insert`, `_TEXT_ACTIONS` and
  `_TYPING_ACTIONS` include `insert`, the side-effect set includes both, `render.py` renders them, and the `browser_act`
  description lists them with when to use which.
- **Fixture pages** in `tests/smoke/test_browser.py` (`_PAGES`): `/editor`, a contenteditable `div` whose `focus`
  handler moves the caret in a `setTimeout(…, 0)` and whose `beforeinput` and `input` handlers mirror the text into a
  hidden `<textarea id="mirror">`, so a lost first character shows; `/board`, two lists with HTML5 drag and drop
  handlers that move the dragged item.
- **Guidance.** The `browser_act` description: `fill` for form fields, `type` where autocomplete must see the keys,
  `insert` for long text and for editors, `drag` to move an element onto another.

**Touchpoints.** `browser/driver.py`, `browser/types.py`, `browser/safety.py`, `browser/render.py`,
`tools/browser.py`, README "Browser use", `tests/smoke/test_browser.py`.

**Decisions and limits.**
- X itself is not tested here (no form filling on external sites, a rule since part 1); the owner checks the composer
  once (open decision 12).
- Monaco and CodeMirror take `insert_text`; their auto-indent and auto-close can still change what is typed line by
  line. The guidance says to insert whole blocks and read the editor back.

**Done when.** Smoke tests on `/editor` (`type`, `fill` and `insert` leave the mirror equal to the text; `type` into a
plain `<input>` unchanged) and `/board` (`drag` reorders); unit tests of the new step validation; the offline smoke
suite clean; README updated.

### 15. Helper models: consult, delegate to another model, standby

**Status: planned.** **Goal.** A run on one model can ask a model of another provider for advice, hand a sub-task to
it, or move to it when its own provider stops answering; a helper whose key is missing or whose quota is spent is
simply not there, and the run goes on.

**Why.** The owner asked for it on 2026-10-09 ("while DeepSeek works it should be able to ask Sonnet or Opus for help;
without a key or with the quota spent nothing may break"). The seed exists: the review model is a second model the
harness uses for approvals in `auto`, the goal check, the ladder's second opinion and lessons. But the running model
cannot ask for help itself, `delegate` always uses the parent's model (`agent/manager.py:150-162`), `[models] fast`
is read nowhere, and a missing key or an exhausted quota surfaces as an error in the middle of a run. The gain is
largest for a weak model, which is this roadmap's principle.

**Design.**
- **Configuration.** `[models] helpers = []`: model references in order of preference, for example
  `["anthropic:claude-opus-5-5", "openai:gpt-5.5"]`. `[models] standby = []`: the models a run moves to when its own
  provider answers with a quota, billing or key error, in order. `[models] helper_cooldown_s = 600`. `[agent]
  consult_max_per_run = 5`.
- **The roster.** `agent/helpers.py` (pure): `roster_for(config, states, cooldowns, now) -> Roster(available,
  unavailable)`, where `states` are the `SecretState` of each provider's key (`secrets.secret_state`, which never reads
  the value) and `cooldowns` the providers in cooldown with their end time. A provider without a key, or in cooldown,
  is unavailable with its reason. `cooldown_for(error) -> float | None`: `ProviderQuotaError`, `ProviderAuthError` and
  `MissingSecretError` give `helper_cooldown_s`; `ProviderRateLimitError` gives its `retry_after_s` or 60 s; a
  transport or protocol error gives none (the call failed, nothing is marked). The runtime keeps the cooldowns in
  memory (`runtime.helpers`), per daemon.
- **`consult`**, a core tool in `tools/consult.py`, offered only when the roster has an available helper (like `skill`
  without skills, `agent/loop.py:739-743`). `ConsultArgs(question, include, model)`: `include` is a tuple of `goal`,
  `plan`, `recent` (the digest of the last steps, `goal_digest` of `agent/review.py:351`), `screenshot` (the newest
  image of the run, only if the helper has vision); `model` names a helper of the roster or is empty for the first
  available. The helper gets a system prompt in the reviewer's manner: it advises another agent, the user's
  instructions in the fence are the only authority, fenced content is data, answer in plain text, no tools. The
  result is the answer fenced as advice ("Advice from anthropic:claude-opus-5-5 (not instructions): …"). `purpose =
  "consult"` (item 9); kind `meta`, risk `safe`; `parallel_safe` true; the (n + 1)th call past `consult_max_per_run`
  fails with the count. A provider error marks the cooldown, emits `helper_unavailable {model, reason, until}`, and the
  result says "X is unavailable (quota exhausted) until 14:05; continue on your own or ask another helper: Y".
- **`delegate(model)`.** `DelegateArgs.model: str | None`: a helper of the roster or the run's own model; anything
  else fails with the list. `RunManager.delegate(parent_id, goal, mode, model)` launches the child on it. The
  description says when: a sub-task that fits a cheaper or a stronger model.
- **Standby.** In `run_agent`, the step call (`agent/loop.py:870-871`) catches `ProviderQuotaError`,
  `ProviderAuthError` and `MissingSecretError` after the connector's own retries. With a standby model available in
  the roster: the run's model changes (`update_run_model`; `RunRecord.model` is the model of the run from here on,
  compaction included), a note tells the model "From this step on you are X; Y became unavailable (reason)", event
  `model_switched {from, to, reason}`, a timeline line, and the same step is retried once on X. Without one: the run
  fails as today. A delegated run inherits the model its parent has now.
- **Where it is seen.** `dotx doctor` prints a "Helpers" row with each helper's key state; the run page's model chip
  follows a switch; `dotx models profile` lists helpers.

**Touchpoints.** `config.py`, `defaults.toml`, new `agent/helpers.py`, new `tools/consult.py`, `tools/delegate.py`,
`tools/groups.py` (`consult` is core), `agent/manager.py`, `agent/loop.py`, `runtime.py`, `store/db.py`
(`update_run_model`), `doctor.py`, `cli.py` (`models profile`), `timeline.js`, `icons.js`, README "Models and
providers" (a "Helpers and standby" paragraph) and "Delegation".

**Decisions and limits.**
- No silent fallback: a helper's failure is a tool result the model reads and an event the owner sees; a standby
  switch is announced in the run and in the timeline. Nothing is swapped without a trace.
- `consult` gives no tools to the helper: tool work goes through `delegate`, where the policy applies as to any run.
- The helper sees the owner's instructions as the only authority, as the reviewer does; its answer is advice.
- Both lists default empty: the owner fills them (open decision 11). The first candidate is Claude once
  `ANTHROPIC_API_KEY` is saved (decision 5).

**Done when.** Unit tests of the roster and the cooldowns (states, errors, timing); smoke with the scripted provider
and a second scripted provider registered under another name: a `consult` call answered and fenced, the budget
reached, a helper raising `ProviderQuotaError` marked unavailable with the event and the result text, `delegate` on
the second model, a standby switch mid-run with `model_switched` and the run ending `done`; `dotx doctor` on a
throw-away home shows the row; timeline checked in headless Chrome; README.

### 16. Typed judgments (TypeSafe) at the decision points

**Status: planned.** **Goal.** Where the harness decides something today with a regular expression or with a full
review-model call, a small typed judgment can answer instead: fast, with a probability, in any language.

**Why.** The owner asked whether the decision system of TypeSafe (typesafe.ai, "System One" models such as Jev,
which return typed answers and probabilities for Choice, Noul and Score questions) belongs in Dot; their OSJarvis
project routes tasks with `choice`, verifies actions with `noul` and scores confidence with `score`. Dot's decision
points fit that shape: the intent of a browser click is read from label patterns (`policy/elements.py:23-37`; "Repost"
and "Quote" match none, Finnish and Turkish labels only where a pattern lists them), the goal check and the ladder's
second opinion are full review-model calls (`agent/review.py`), and item 12's claim of an achieved target needs a
judge. The recommendation is yes, at two points first, behind an interface, optional, and never as the loop's
decision maker: the harness keeps the workflow, the judge supplies the semantic calls where code has none.

**Design.**
- **Configuration.** `[judge] provider = "none" | "typesafe"`, `api_key_env = "TYPESAFE_API_KEY"`, `base_url`,
  `model`, `timeout_s = 5.0`, `public_action_threshold = 0.7`, `target_threshold = 0.9`. The base URL and the model
  name are read from the live documentation at implementation time (the skill in the owner's OSJarvis worktree,
  `docs/skills/typesafe-ai/SKILL.md`, says the live docs at `https://docs.typesafe.ai/llms.txt` are the source of
  truth; they were not read for this plan). The key follows the secrets lookup (environment, `.env`, Keychain) and
  the Settings › API keys tab lists it.
- **Connector.** `llm/typesafe.py`: `TypeSafeClient` on `httpx`, retry policy as the other connectors, errors
  classified into the `ProviderError` family; `ask(state: JsonObject, questions: tuple[Question, ...]) ->
  tuple[Judgment, ...]` with `Question(id, kind: "choice" | "noul" | "score", instructions, criteria)` and
  `Judgment(id, answer, probability, confidence)` as `FrozenModel`s. Independent questions over one state go in one
  request.
- **Interface.** `agent/judge.py`: `Judge` with `enrich_action(action, context) -> ActionInfo` and `target_reached(
  targets, verification, entries, evidence) -> Verdict`; `NoJudge` returns the action unchanged and `unknown`;
  `TypeSafeJudge` asks. The judge runs inside `RunGate.authorize` (`agent/gate.py:95-111`), the one async place every
  action passes before the policy decides.
- **Use 1, the intent of a browser click.** For a `browser_act` click whose label matched no pattern, one request with
  four Noul questions over the state `{label, role, page_title, url, nearby_text}`: public post, send to someone,
  payment, destructive. A probability at or above `public_action_threshold` adds the flag; the action's summary says
  "(judge: public_post 0.93)". The patterns stay as the floor and decide alone when the judge is off or unavailable.
- **Use 2, a campaign's claim.** `campaign_achieved` (item 12) asks `target_reached` first: below `target_threshold`
  the call fails at once with the probability and the reason; at or above, the review model confirms as designed.
  The goal check of a plain run is not changed in this item.
- **Unavailable.** A key missing, a quota or an error marks the judge unavailable for `helper_cooldown_s` with one
  event `judge_unavailable {reason, until}`, and the regex and review paths decide, with "(judge off)" in the summary.
  No retry storm, nothing hidden.
- **Measuring it.** `tests/unit/test_judge.py` tests the enrichment and the thresholds with a scripted judge;
  `tests/smoke/test_hosted_services.py -m hosted -k typesafe` sends a few labels in English, Turkish and Finnish
  ("Repost", "Yeniden gönder", "Lähetä", "Pay now", "Delete account") and prints the probabilities. Before the judge is
  turned on by default, count on the owner's history (counts only, read-only): how many allowed browser clicks on
  social hosts had labels that matched no pattern.

**Touchpoints.** `config.py`, `defaults.toml`, new `llm/typesafe.py`, new `agent/judge.py`, `agent/gate.py`,
`policy/elements.py`, `tools/campaign.py` (item 12), `runtime.py`, `secrets.py` and `routes_keys.py` (the key),
`doctor.py`, `timeline.js`, README "Safety" (a "Typed judgments" paragraph), `.env.example`.

**Decisions and limits.**
- Off by default (`provider = "none"`): it is a paid external service and its calibration on Dot's labels is unknown
  until the hosted test and the count above exist (open decision 9).
- The judge never decides a step and never grants anything: it adds flags that make the policy ask, or stops a claim
  the review model would have examined. Code owns the workflow, as TypeSafe's own guidance says.
- One request per judged click adds its latency to that click; the threshold and the host list keep it to clicks that
  matter (the patterns answer the rest).

**Done when.** Unit tests with the scripted judge; the connector's offline body and error tests in
`tests/smoke/test_wire_codecs.py` style; the hosted test written and skipped without the key; `dotx doctor` row;
README and `.env.example`; the owner's count recorded in the log when they run it.

### 17. Pacing and public actions on social platforms

**Status: planned.** **Goal.** Reposts, quotes, follows and likes are seen as what they are, and a run keeps to a pace
that a platform tolerates, so an account that Dot grows is not the account Dot gets suspended.

**Why.** `_POST_ACTION` and `_SEND_ACTION` (`policy/elements.py:28-29`) catch Post, Tweet, Retweet, Publish, Share,
Comment, Send and Reply; "Repost" and "Quote" (X's labels since 2023) match nothing, and Follow and Like carry no flag
at all. No cap and no spacing exist for public actions (search of `browser/` and `tools/browser.py` for pacing, rate,
quota, cooldown, throttle). A campaign that "does what it takes" on X would follow and like without limit; platforms
suspend for exactly that.

**Design.**
- **Patterns.** `_POST_ACTION` adds `repost`, `quote`, `yeniden gönder`, `alıntı`; a new `_SOCIAL_ACTION` matches
  `follow`, `unfollow`, `like`, `unlike`, `subscribe`, `connect`, `takip`, `beğen`, `abone` and gives a new flag
  `social` (`policy/types.py:31-45`), risk `low`, in `TAINT_ESCALATION_FLAGS` (outward-facing). Finnish labels
  (`Julkaise`, `Seuraa`, `Tykkää`, `Lähetä`) join the three patterns; item 16 covers the rest of the world's labels.
- **The pace.** `[policy] public_actions_per_hour = 20`, `public_actions_per_day = 120` (flags `public_post` and
  `external_send`), `social_actions_per_hour = 40`, `social_actions_per_day = 300` (flag `social`), and
  `public_action_spacing_s = 20` between two public actions on one host; 0 means no limit. Counted per host, across
  threads: `ActionInfo` gets `host: str` (empty when not a web action), set by `browser_act`'s classification from the
  thread's current page; the gate writes an allowed public or social action to a table `paced_actions(host, flag, ts,
  run_id)` and refuses the one past a limit: "Pace for x.com: 20 of 20 public actions this hour; the next is allowed
  at 14:35. Do other work meanwhile, or schedule a session." A pace is a limit, not a question: `yolo` does not waive
  it; the owner sets 0 to remove it.
- **Prompt.** A `# Platforms` paragraph: "Work on a platform within its rules: no bulk follows or likes, no duplicate
  posts, no automation the platform forbids; the pace limits of this run are enforced and shown when reached; growth
  comes from posts people want to read and replies that add something." The `x-twitter-growth` skill (item 18) carries
  the strategy.
- **Where it is seen.** The timeline shows a paced refusal with the host and the next window; the campaign page
  (item 12) shows the counts of the day per host.

**Touchpoints.** `policy/elements.py`, `policy/types.py`, `policy/engine.py` (the pace check after the decision),
`agent/gate.py`, `tools/browser.py`, `store/db.py` (`paced_actions`), `agent/prompt.py`, `config.py`, `defaults.toml`,
README "Safety", `rules.js` (the flag list), `timeline.js`.

**Decisions and limits.**
- The default numbers are conservative guesses; the implementer verifies them against the platforms' published rules
  before the defaults are set (open decision 8). They apply to every host; a per-host table is not built until the
  owner asks.
- The count sees only browser clicks the policy saw. A post made through an API in the shell is not counted.

**Done when.** Unit tests of the patterns (English, Turkish, Finnish labels; Repost, Quote, Follow, Beğen flagged;
"Reposted by" as text not an action) and of the pace windows (pure); a smoke test on a fixture page with Repost and
Follow buttons that hits `public_actions_per_hour = 2`; the paced refusal text in the timeline; README.

### 18. Skills written for Claude Code, and the X playbook

**Status: planned.** **Goal.** The owner's Claude Code skills load in Dot as they are, so the X playbook they already
own (`x-twitter-growth`, `baoyu-post-to-x`) is what a campaign on X works from.

**Why.** Both skills are refused: `SkillFrontmatter.metadata` is `dict[str, str]` (`skills/types.py`), and their
frontmatter holds `updated: 2026-03-10` (a YAML date) and a nested `openclaw` map. The Agent Skills specification lets
`metadata` hold arbitrary values. Reproduced on 2026-10-09 with `parse_skill`; `find-skills` loads. And `[skills]
dirs` is empty, so `~/.claude/skills` is not read until the owner lists it.

**Design.**
- `SkillFrontmatter.metadata: JsonObject` (`core/types.py`), so any YAML mapping passes; the prompt line of a skill
  shows name and description only, as today.
- `dotx skills list` prints the folders left out and why (the `problems` of `SkillCatalog`); `dotx doctor`'s skills row
  counts them.
- The comment of `[skills] dirs` in `defaults.toml` shows `dirs = ["~/.claude/skills"]` as the example; the owner's
  `config.toml` is theirs to change (open decision 10).
- The skill guidance in the prompt (`agent/prompt.py:221-225`) adds: "A skill written for another agent may name tools
  Dot does not have (a CLI, a browser extension, a script runner): do the same steps with Dot's tools."
- Documentation: README "Skills and plugins" says skills of Claude Code load unchanged, and names the two X skills as
  the playbook a campaign on X works from.

**Touchpoints.** `skills/types.py`, `cli_skills.py`, `doctor.py`, `agent/prompt.py`, `defaults.toml`, README,
`tests/unit/test_skills.py`.

**Done when.** A unit test parses a frontmatter with a date and a nested map; `dotx skills list` on a throw-away home
with copies of the two skill folders lists both (copies in the temp home, never the owner's folder); a scripted run is
offered both; README.

### 19. Prompt-cache hygiene for the hosted providers

**Status: planned.** **Goal.** On Anthropic and OpenAI the prefix of a run's prompt stays byte-identical from step to
step, so the server's cache pays for the system prompt, the tools and the history instead of the owner.

**Why.** Found with the Claude provider (log of 2026-10-08): the system prompt carries the time to the minute
(`agent/prompt.py:338`), so two runs never share a cached prefix; `drop_old_images` edits the history at every step
(`agent/loop.py:368`, `agent/compact.py:30-39`), so a screenshot leaving the window rewrites a message in the middle
of the prefix and the cache starts again from there; `cached_tokens` is parsed and dropped, so nobody can see the hit
rate. Anthropic's automatic top-level `cache_control` is already sent (`llm/codec_messages.py:164-165`). The Ollama
cloud models the owner runs today gain nothing from this; a Claude helper or standby (item 15) does.

**Design.**
- The system prompt names the date (`%A %Y-%m-%d`) and the time zone; the exact time goes into the opening message
  ("It is now 14:02 EEST") and into every note the harness appends (`_stored_note`), which are at the end of the
  prompt anyway.
- Images leave the history: a tool result with a screenshot is stored with a stub ("[screenshot 3, attached below]")
  from the start, and the request appends one trailing user message "Current screenshots" with the newest
  `keep_recent_screenshots` images of the run, kept in memory by the loop (the files are in the store as today). The
  codecs do not change: it is one more user message with images. `drop_old_images` goes.
- `shrink_old_tool_results` (`agent/compact.py:42-51`) shrinks a result to its final size in one pass (measured: a
  result changed twice before it settled), so a message is rewritten once.
- `dotx stats` shows `cached_tokens` per model (item 9 stores them).

**Touchpoints.** `agent/prompt.py`, `agent/loop.py`, `agent/compact.py`, `store/db.py` (loading keeps the stubs),
`llm/codec_messages.py`, `llm/codec_responses.py`, `llm/codec_chat.py` (no change expected; the tests prove it),
README "Models and providers".

**Decisions and limits.**
- The vision relay for a blind main model (`agent/vision.py`) reads the images from the trailing message the same way.
- Whether the trailing message confuses a model is checked on the scripted and the hosted tests; the hosted run spends
  the owner's quota (open decision 1).

**Done when.** Unit tests of the stub and the trailing message; wire tests of all three codecs with images in the
trailing message; the hosted Anthropic test reports `cached_tokens > 0` on the second step of a three-step scripted
run (when the owner runs it); the offline smoke suite clean; README.

### 20. An interactive terminal for programs that prompt

**Status: planned.** **Goal.** The agent can run a program that asks questions, keep a REPL or a dev server alive
while it works, and answer a prompt, as a person at a terminal would.

**Why.** `shell` runs `zsh -c` with stdin closed and in its own process group (`tools/shell.py:128-137`); nothing in
the code base opens a pseudo-terminal (search for `pty`, `openpty`, `pexpect`, `termios`: none). So `npm create`,
`ssh` with a host-key question, `python -i`, `psql`, interactive installers and a dev server that must stay up for a
browser test are out of reach, and that is most of "any job on this computer" and half of "write code in the
browser".

**Design.**
- A tool group `terminal` (`tools/terminal.py`), acting modes only: `terminal_open(command, cwd) -> session id`,
  `terminal_send(session, text)` (the text is sent as typed; a newline is part of the text), `terminal_read(session,
  wait_s)` (what arrived since the last read, after waiting up to `wait_s` for new output, at most 60 s),
  `terminal_close(session)`. At most `[shell] terminal_sessions = 3` per thread; a session idles out after
  `[shell] terminal_idle_s = 1800` and is closed when the daemon stops; a run's end does not close it (a dev server
  outlives the run that started it, until the thread's idle limit).
- `desktop`-independent implementation in `shell/terminal.py`: `pty.openpty()`, `asyncio.create_subprocess_exec` of
  the user's shell with the slave as stdin, stdout and stderr, a reader task that keeps the last 192 KiB in a ring
  buffer with ANSI escape sequences stripped by one regular expression (no terminal emulator), `TERM=dumb`, the same
  environment filtering as `shell` (`[shell] pass_env`).
- Policy: `terminal_open` is classified like `shell` on its command (`policy/shell.py`); `terminal_send` is classified
  like `shell` on its text when the text ends a line (it may be a command), and as `credential` when the session's
  newest output matched a password or passphrase prompt, so the hand-off and the `yolo` waiver work as for the
  browser (item 10). Output is marked untrusted like shell output.
- The prompt's channel order (`agent/prompt.py:90-94`) names the terminal after `shell`: "a program that prompts, a
  REPL, or a server that must stay up".

**Touchpoints.** new `shell/terminal.py`, new `tools/terminal.py`, `tools/groups.py`, `policy/shell.py`,
`agent/prompt.py`, `config.py`, `defaults.toml`, README "Tools", `timeline.js`, `icons.js`.

**Decisions and limits.**
- No terminal emulator: cursor movement and full-screen programs (`vim`, `htop`) show as garbled text, and the tool
  says so. Line-oriented programs are the target.
- The shell guardrail is best-effort here as in `shell`; the README says so for both.

**Done when.** Smoke tests: `python3 -i` answers an expression; `read -s` shows a password prompt that is handed off
in `ask` mode and typed in `yolo`; a `python3 -m http.server` session serves a page to `web_fetch` while the run goes
on and is closed by `terminal_close`; the idle reaper; unit tests of the ANSI stripping and the ring buffer; README.

## Open owner decisions

1. Live evals: `dotx eval run --model REF` spends model quota. Run it for the models in use and keep the JSON; it is the
   basis for flipping the defaults below.
2. Defaults that change how the agent behaves and wait for evidence: `[agent] tool_surface` (item 4),
   `verify_answer_paths` (item 5), `resume` (item 6), `lessons` (item 7), `stall_escalation` (item 2),
   `scaffold = "measured"` (item 3).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model, and
   `dotx doctor` warns about it (a warning, not a failure).
4. From the audit: how long to keep events and screenshots (retention windows), which model compacts (`[models] fast` is
   documented but never read), browser access to localhost and private addresses, CI.
5. The Claude models (`anthropic`): no request to the Anthropic API has run yet. Save `ANTHROPIC_API_KEY` (Settings ›
   API keys once the daemon runs the new code, its **Check** costs nothing; or `dotx auth set ANTHROPIC_API_KEY`) and
   run the hosted test once (`.venv/bin/python -m pytest tests/smoke/test_hosted_services.py -m hosted -k anthropic`:
   a few tiny requests of `claude-haiku-5-5`). A Claude model is then a candidate for `[models] review` while `main` is
   of another family (decision 3).
6. Standing goals (item 12): the composer switch "Keep going until done" is off by default and a campaign stops only
   when the owner stops it; the defaults `campaign_gap_minutes = 60`, `campaign_wait_hours = 24`,
   `campaign_patience_sessions = 3`, `campaign_max_sessions = 0`, and item 11's `run_max_minutes = 0`,
   `run_max_tokens = 0` are proposals: say the numbers you want, or that none should bound a campaign.
7. `yolo` and password managers (item 10): secure fields and one-time codes are typed in `yolo` by design of this
   part; password-manager apps stay refused in every mode unless you say otherwise.
8. Pace limits (item 17): the defaults (20 public actions an hour, 120 a day; 40 social an hour, 300 a day; 20 s
   between public actions) are conservative guesses. Confirm them against the platforms' published rules, or set 0.
9. TypeSafe (item 16): save `TYPESAFE_API_KEY`, read the live docs for the base URL and the model name, run the hosted
   test, and run the read-only count on your history (how many allowed clicks on social hosts had labels no pattern
   matched) before `[judge] provider = "typesafe"` becomes your default.
10. Skills (item 18): put `dirs = ["~/.claude/skills"]` under `[skills]` in your `config.toml` once the loader accepts
    your two X skills; Dot reads nothing there until you do.
11. Helpers (item 15): fill `[models] helpers` and `[models] standby` in your `config.toml`. The natural first entry is
    a Claude model once `ANTHROPIC_API_KEY` is saved (decision 5).
12. Only you can check live: the X composer with item 14's `type` and `insert`, and a `yolo` run that reads a
    one-time code from your webmail and types it (item 10).
13. Speed knobs that wait for item 9's numbers: `[browser] settle_quiet_ms` (120 or 60), `[desktop]
    screenshot_max_edge` (1920 or 1366), `[desktop] paste_threshold_chars`, `[agent] tool_output_chars` and
    `keep_recent_tool_results`, besides `tool_surface = "grouped"` (decision 2) and the compaction model (decision 4,
    and `agent/loop.py:290-298` records the decision that a run compacts with its own model).
14. Research (not an item of part 2): a `research` run can save nothing, not even a note (`write_file` and
    `memory_save` are acting-only, `tools/base.py:16-17`), and DuckDuckGo's bot check throttles long research runs
    (README "Status and known limits"). Say whether a research run may save notes and files under the workspace,
    and whether the hosted Ollama search (`OLLAMA_API_KEY`) should be the default backend; either is a small item.

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
