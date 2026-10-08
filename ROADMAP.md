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
| 4   | Smaller, progressive tool surface                           | done    | d89d634 |
| 5   | Verification of the end of a run                            | done    | e2051a5 |
| 6   | Safe resume after a restart                                 | done    | c3688e6, b6dd42f |
| 7   | Learning from runs (lessons)                                | done    | a297764, 4db2539 |
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
  before it reads the run, so a run that ends in between never shows as neither active nor ended (a smoke test found it).
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
2. Defaults that change how the agent behaves and wait for evidence: `[agent] tool_surface` (item 4),
   `verify_answer_paths` (item 5), `resume` (item 6), `lessons` (item 7), `stall_escalation` (item 2),
   `scaffold = "measured"` (item 3).
3. `[models] review`: use a model of another family than `main` (item 5). The default is the same model, and
   `dotx doctor` warns about it (a warning, not a failure).
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
