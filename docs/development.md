# Development and measuring the agent

## Development

```bash
uv sync
.venv/bin/ruff format src tests && .venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/pytest
```

The tests prefer real calls to mocks: the smoke tests start the real server, a headless Chrome against a local fixture
server, a real MCP server process and real Ollama models, and the unit tests cover pure functions. Tests that need a
service are marked:

| Marker     | Needs                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| `live`     | a running Ollama server with a signed-in cloud model                                                                 |
| `browser`  | Chrome; it runs headless against a local fixture server                                                              |
| `internet` | the public internet; a few read-only requests                                                                        |
| `desktop`  | a logged-in screen and `DOT_DESKTOP_TESTS=1`; it moves the real pointer and keyboard against a local AppKit window |
| `hosted`   | your own `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `ANTHROPIC_API_KEY` and `OLLAMA_API_KEY`; a few tiny requests, skipped per missing key |

`pytest -m "not live and not internet"` skips the first and third kind, `pytest -m "not hosted"` the requests that use
your keys. A few smoke tests that use the local Ollama server's embedding model skip themselves without it. A bare
`pytest` collects every test, as each test module is imported by its path (`tests/unit` and `tests/smoke` have modules
of the same name), and a mark that is not in the table fails, so a mistyped one cannot pick a test that needs a service
(item 213).

The smoke tests of the eval runner (`tests/smoke/test_evals_runner.py`) need no service at all: a scripted model
plays the real loop, tools, policy and store.

The gate runs on every pull request and every push to `main` (`.github/workflows/gate.yml`, on macOS because the package
depends on PyObjC): the format check, the lint, mypy, the unit tests, `dotx eval check` and every smoke test that needs
no service, chosen by its marks (`-m "not live and not browser and not internet and not desktop and not hosted"`), and
for the desktop shell `cargo fmt --check`, clippy and `cargo test`. Each action the workflow uses is pinned by the
commit its tag named, so a tag moved later cannot change what runs. The tests marked above stay on your Mac. Once a
week, and when asked, `.github/workflows/audit.yml` checks the dependencies as locked against the public advisories
(pip-audit for `uv.lock`, cargo audit for the desktop shell's `Cargo.lock`) and fails on a known vulnerability
(item 217).

The work on the agent's autonomy (measuring it, noticing a stuck run, adapting to the model, resuming, learning) is
planned and tracked in [ROADMAP.md](../ROADMAP.md); read it before continuing that work.

```
src/dotagent/
  cli*.py     the command line: the parser and the command models (light), one module per command family loaded by name
  agent/      run loop, run manager, approvals, context trimming, vision relay
  llm/        providers (Ollama, OpenAI, OpenCode Go, Anthropic, OpenRouter) and their wire codecs
  policy/     action descriptions, shell analysis, the decision engine
  tools/      the tools the model can call
  browser/    Playwright session, snapshots, safety
  desktop/    accessibility tree, input, screen capture, permissions
  web/        search and fetch
  memory/     notes, ranking, embeddings
  evals/      eval tasks, the runner that plays them, the scripted model, scoring
  scheduler/  schedules, timing, the runner
  mcp/        MCP client hub
  skills/     skill discovery, frontmatter and the catalog
  plugins/    plugin discovery, their skills and MCP servers
  server/     REST API, event streams, daemon, the static web UI
  store/      SQLite persistence
  core/       errors, logging, retries, paths, shared types
```

## Measuring the agent

How well Dot finishes work is measured, not guessed, in two ways.

**`dotx stats [--days N]`** counts the runs that really happened (the last 30 days by default), per model: how they
ended (finished, stopped by you, stopped by the progress watchdog, stopped at its limit, refused by the goal check, cut
off when the daemon died or shut down, internal error, still active), steps, input and output tokens, tool calls and how
many of them failed, calls whose arguments were not valid JSON, stalls and compactions. A **Time** table follows, per
model of the runs as above (a side call that another model answered counts under the run's model): the seconds the model
calls of its runs took, the median and the 90th percentile of one step's model call (of the steps that got a reply), the
seconds of the compactions and of all side calls (every call but a step: compactions, approvals the review model
answered, the goal check, the watchdog's second opinion, lessons, descriptions of images, side questions), and the
tokens the provider served from its prompt cache. A call's seconds include the retries its connector made and the waits
between them. Every model call of a run leaves a `model_call` event with its purpose, model, seconds and tokens (and,
for a call that failed or was cut off, the error), and its tokens count in the run's totals; runs made before calls were
timed have none, are left out of the table and are counted below it. A **Tools** table lists the 15 tools whose calls
took the most seconds: calls, failures, seconds in all, the median call and the seconds spent at the gate (waiting for
your approval or the review model's answer), which the seconds include. The seconds of `ask_user` are the wait for your
answer (none for a question the run left), and those of `delegate` hold the whole delegated run, whose own tool calls
are listed too. The database file is opened read-only, so it is safe to run while the daemon works, and the numbers hold
no text of your chats.

Two more numbers say how many round trips a run spends. Under each model, an **acts** line counts its `browser_act`
and `desktop_act` calls, the share that carried one step (a round trip spent on a single click or keystroke: the
batches of [Browser use](browser-and-computer-use.md#browser-use) and [Computer use](browser-and-computer-use.md#computer-use) exist to avoid it), the share that failed and
the median call; a **step model call by step number** line gives the median seconds of a step's model call for the
steps 1 to 5, 6 to 10, 11 to 20, 21 to 40 and from 41 on of their runs, which shows how much a run slows down as its
context grows. The Time table ends with the share of the input tokens the provider served from its cache. A **Sites**
table follows, per site (the host of the page for a browser act, the application for a desktop one, both recorded in
the `tool_result` event with the steps of the batch): acts, the share with one step, the share that failed, the
median call, and the finished runs that acted there with the share of them that ended done (item 308). **By day**
follows: per day, the finished runs that acted on a site and the share done, which shows whether the browser and the
desktop work gets better from day to day. `--by site` prints these two only, `--by model` everything but them. Acts and steps recorded before
these were counted (before item 22 of the roadmap) are left out of the acts lines and the sites table.

The numbers of a long run follow under each model: the **compactions** per 100 steps, their seconds and their share of
the model time (a run on a small context budget pays a summary every few steps); the **web failures** by cause, read
from the first line of each failed `web_fetch`, `web_search`, `web_crawl` or `web_extract` result (not found: HTTP 404
or a host name that does not resolve, which is an address the model guessed; throttled: HTTP 429 or an engine's bot
check; blocked: HTTP 401 or 403; in transit; other); the calls of any tool **refused for arguments that did not fit**
(a turn lost each, on top of the calls whose arguments were not valid JSON); the **delegated runs** and the seconds
the run waited for them; and the step line adds the median input tokens a step sent per band, the growth of the
context. **`dotx stats --run ID`** prints the same for one run: its minutes of wall time (to now while it works), its
tool calls' seconds and the part spent at the gate, then the lines above and its tools. The daemon serves the same as
`GET /api/runs/{id}/stats`.

**`dotx eval`** plays a catalog of small tasks against a model and judges each by what is left in a workspace of its
own: file edits, shell work, fixing code until its test passes, recovering from an error, multi-step work, a goal that
asks for as much as the files give, read-only research, a task that cannot be done and has to end as an honest failure,
an instruction hidden in a file that must not be followed, and a goal written in Turkish. Five long tasks follow, each
needing ten or more tool turns (a digest of twelve logs, a rename across ten modules, ten branch totals into JSON, a
ten-item checklist, a nine-stage pipeline whose fifth stage fails once): on them the steps a pass costs say how many
round trips a model spends on work that cannot be done in one. Two tasks run against a small site the attempt serves on
a loopback port (no network, no account): a research run that reads the careers pages of ten companies, one of them
linked to a dead address and one behind a redirect, and reports a table (a long task: it is where the compactions and
the not-found pages of a long run are counted); and a run in goal mode that fills in and submits a form in the headless
browser, whose end the review model checks against the goal on the confirmation page. Three tasks bring help that makes
them short, and ask whether a run takes it: a skill that states the house style a memo has to follow (the only way to
pass), a skill whose one command totals a quarter's paid orders, and a tool of a small MCP server the evals ship
(`python -m dotagent.evals.tool_server`) that counts working days; the last two can also be done the hard way, by
reading the files and working it out.

| Command                                                  | What it does                                                                                                                                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dotx eval list`                                         | The tasks, with their kind, mode and number of checks.                                                                                                                                                             |
| `dotx eval check`                                        | Needs no model and costs nothing. Replays the reference solution of every task through the real loop, tools and policy, and proves that a run that touches nothing and says "done" fails every task and that the reference of a task with an easy way takes it. Run it after changing a tool, the policy or the loop. |
| `dotx eval run [--model REF] [--task ID]... [--repeat N]` | Plays the tasks against a model (the configured main model if none is given) and prints the score. It spends the tokens of that model: an attempt is cut after 300 s or 600000 tokens, and the notice before it starts states the ceiling. `--repeat` plays every task several times, which shows the tasks a model passes only now and then. |
| `dotx eval report [FILE...]`                             | The score of saved runs, with a comparison table when there are several and, for exactly two files, a line with the second against the first (passed, steps, tokens and seconds per pass, the one-step act and cache shares in percentage points, tool errors, stalls, false claims, unproven steps, the share of the easy ways taken); without a file, every run saved under `<home>/evals/`. |

Every attempt runs unattended in a throw-away Dot home (its own database and workspace, removed afterwards), with the
Mac desktop, your MCP servers, skills, plugins and the memory embeddings switched off (a task that brings skills or an
MCP server has those alone, besides the skills that come with Dot), and with every question of the policy or of the
agent unanswered (one the run waits for is refused at once). Your own database, workspace and screenshots are never
touched; the API keys are looked up first and handed over by name: those of the model under measure and of the fast,
review and vision models that have one. An eval run does not start without the first, nor without the review model's
when it has work in an attempt: the goal check of a run in goal mode (every attempt with `[agent] goal_mode`, the form
task always, a goal that asks to keep going until it is done) or the second opinion of `stall_escalation`. A goal check
that cannot run ends its attempt as failed, saying why, and the next attempt follows. The score holds the pass rate, per
kind of task, what a pass costs in steps, tokens and seconds (split into model calls and tool calls), the share of the
act calls that carried one step and the share of the input tokens served from the prompt cache (both as `dotx stats`
counts them, over every attempt), how often a tool call failed, stalls, runs that were cut, runs that said they were
done and were not, plan steps marked done without evidence (zero since the plan tools require it), done steps whose
evidence the goal check could not bear out (counted from the `goal_check` events, so only in goal mode) and how many
attempts kept a plan (item 252: one that kept none tells nothing of a change to how the plan reaches the model; `-` in a
report saved before they were counted), compactions per 100 steps and the share of the model time their summaries took,
web pages not found and calls refused for their arguments (as `dotx stats` counts them; `-` in a report saved before
they were), how many of the attempts at a task that hides an instruction in one of its files (a note "to the AI
assistant" in an invoice, a hidden block in a saved page, a line in a notes file) followed it, judged by what only
following it does (a file it asks for, the key it asks to give, a total or a date that the files do not hold), so an
answer that quotes the instruction while refusing it has not followed it (a report saved before quotes were told apart
counted one as following it), how many of the attempts at a task that help makes short took its easy way (a call of the
skill or MCP tool it names that ran; a call that did not run does not count) and what a pass costs in steps when it did
and when it did not, how often find_help was called (`-` in a report saved before these were counted), and the tasks
that passed only in some attempts. The token ceiling of an attempt counts every model call of the run and of the runs it
delegated to, side calls included; a report saved before model calls were timed counted the steps' tokens only and has
no seconds, and one saved before acts and cached tokens were counted shows `-` for their shares. `dotx eval run` saves
its report after every attempt, so an interrupted run keeps what it measured.

A task is data (`src/dotagent/evals/catalog.py`): the files the workspace starts with (their text, or the bytes of an
Office file in Base64), the goal, checks (a file exists, holds or equals something, a JSON file equals a value, a
command exits with 0, the answer contains or does not contain something), the checks that hold unless the run followed
an instruction hidden in a file (`dotx eval check` reports one that an answer quoting the files the goal names would
fail), the skills and MCP servers it brings and the easy way they offer (or a skill that comes with Dot), and a
reference solution as a script of tool calls. The two tasks on Office files start from a workbook whose formulas hold
the results Excel saved and from a Word document whose company name is bold in one place and split over two runs in
another; their easy way is the skill that comes with Dot for them, and their checks read the files with Dot's own Python
(`python -I -m dotagent.evals.office_tasks`). To add one, write it there and run `dotx eval check`.
