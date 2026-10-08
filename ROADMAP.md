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
| 1   | Eval harness and run statistics (`dotx eval`, `dotx stats`) | done    | a83cdcc |
| 2   | Progress watchdog and escalation ladder                     | done    | 5f6cb29 |
| 3   | Model-adaptive scaffold (`dotx models profile`)             | done    | de2cc1c |
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
  `tests/smoke/test_evals_runner.py` (the whole catalog through the real loop with the scripted model, the time and token
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

**What exists now.**
- `agent/progress.py`, pure. `observe` turns the calls of one acting step (`CallSeen`: name, arguments, result, failed,
  image hashes) into an `Observation`; `advance(state, calls, limits, ladder)` returns the next `WatchState` and an
  `Alert` or `None`. Detectors: `repeat` (`stall_limit` steps with the same calls and the same results), `cycle` (period
  2 or 3, two whole rounds, more than one distinct step), `failures` (`failure_streak` steps in a row in which every call
  failed), `revisiting` (at least `revisit_window - 1` of the last `revisit_window` steps only redo calls already made,
  with the same results). Every alert clears the window, so the next rung needs fresh evidence; 12 steps without an
  alert (`_DECAY_STEPS`) bring the level one rung down.
- `ladder_for(escalation, can_ask)`: with `stall_escalation` nudge, replan, consult, ask, stop (ask only when
  `can_ask_user`: no parent run, not a schedule, delegate or eval source, approvals not `yolo`); without it nudge, nudge,
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
  asked, the stop result) and `tests/smoke/test_progress_watchdog.py` (the real loop, with the scripted model playing the
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
  `timeline.js` threw on every stall event (`warnings.join` on an int) and `cli_ui.py` refused plan steps marked `failed`
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
  smaller one is the safe side). The run's budget is `min([agent] context_tokens, 3/4 of the window)`; the last quarter is
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
  answer. Eval reports are not read: an attempt runs in a throw-away home, `RunMetrics` has no refusal count, and parsing
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
   `lessons` (item 7), `stall_escalation` (item 2), `scaffold = "measured"` (item 3).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model.
4. From the audit: how long to keep events and screenshots (retention windows), which model compacts (`[models] fast` is
   documented but never read), browser access to localhost and private addresses, CI.

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
