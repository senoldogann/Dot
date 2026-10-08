# Dot

Dot is an always-on autonomous agent for macOS. Give it a goal in plain words and it works on it until it is done, with
what a person at this Mac would use: a shell and the file system, the web, its own Chrome profile (browser use), the
screen, mouse and keyboard (computer use), AppleScript, a memory that lasts between runs, schedules, MCP tool servers
and sub-agents.

It runs on models from **Ollama** (local models, and cloud models through the local Ollama server), the **OpenAI** API
or **OpenCode Go**. One local process serves the command line, a web UI and a REST API.

By default Dot acts without asking. It stops for what it must not decide alone (money, credentials, public posts,
messages sent for you, privilege, system settings), everything it does is recorded, and it can be stopped at once. Read
[Safety](#safety) before you let it work on a machine that holds data you care about.

## Requirements

- macOS 15.2 or later (developed and tested on macOS 26, Apple silicon)
- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- [ripgrep](https://github.com/BurntSushi/ripgrep): `brew install ripgrep`
- Google Chrome, which the browser tools drive through Playwright
- [Ollama](https://ollama.com), running. Cloud models (names that end in `:cloud`) need `ollama signin` once. Searching
  the memory by meaning needs an embedding model: `ollama pull bge-m3`
- An API key for OpenAI or OpenCode Go, only if you use those providers

## Quick start

```bash
cd Dot
uv sync
uv run dotx doctor
```

`dotx doctor` checks the home directory, ripgrep, Chrome (with a throwaway start of it), the macOS version and the
permissions the desktop tools need, the commands and secrets of the configured MCP servers (none is started), Ollama,
the keys of OpenAI and OpenCode Go, the embedding model and the three configured models, and says how to fix what is
missing. The examples below write `dotx`: run it as `uv run dotx`, or put `.venv/bin` on your `PATH`.

```bash
dotx run "Find the opening hours of the Helsinki Central Library and save them to hours.md"
dotx run --mode research "Compare the free tiers of three hosted Postgres services"
dotx chat
```

`run` carries out one goal and prints the result; `chat` keeps a conversation going (`/new` starts a fresh one, `/exit`
leaves). Ctrl-C stops the run that is going on; a second Ctrl-C quits at once.

- `--mode research` makes the run read-only
- `--model provider:model` picks the model for this run, for example `ollama:gpt-oss:20b-cloud`
- `--effort LEVEL` picks the reasoning level for this run (see [Models and providers](#models-and-providers))
- `--approvals ask|auto|yolo` picks who answers the questions of the policy in this run: you (`ask`, the default), a
  review model (`auto`) or nobody (`yolo`); `--auto-approve` and `--yolo` are short for the last two. `chat` takes the
  same flags (see [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run))
- `--goal-mode` / `--no-goal-mode` turns the check of the answer against the goal on or off for this run (`run` only)
- `--thread ID` continues an earlier conversation; the line printed at the end of a run names its thread
- `--no-input` never prompts: every approval that would wait for you is denied and questions go unanswered. The same
  holds when standard input is not a terminal
- `--thinking` shows the model's reasoning stream, `--verbose` the tool output and the token use
- a goal of `-` is read from standard input

## Models and providers

A model is written `provider:model`. The `[models]` table of the configuration sets five roles:

| Key      | Used for                                                                                  | Default                     |
| -------- | ----------------------------------------------------------------------------------------- | --------------------------- |
| `main`   | the agent loop                                                                            | `ollama:minimax-m2.7:cloud` |
| `vision` | describing the images a tool returns, when `main` cannot see them                         | `ollama:gemma4:cloud`       |
| `fast`   | summarising the older part of a long run                                                  | `ollama:gpt-oss:20b-cloud`  |
| `review` | answering the policy's questions in `auto` approvals, and checking a run against its goal | `ollama:minimax-m2.7:cloud` |
| `effort` | default reasoning level of a run on `main` (see below)                                    | `medium`                    |

| Provider    | Reference             | Protocol                                                                                   | Key                                                       |
| ----------- | --------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| Ollama      | `ollama:<model>`      | native `/api/chat`                                                                         | none for the local server                                 |
| OpenAI      | `openai:<model>`      | Responses API; `wire = "chat"` selects Chat Completions                                    | `OPENAI_API_KEY`, optional `OPENAI_ORG_ID`/`OPENAI_PROJECT_ID` |
| OpenCode Go | `opencode_go:<model>` | each model is served on exactly one of Chat Completions, Responses and Messages            | `OPENCODE_API_KEY`                                        |

- **Ollama**: `[providers.ollama] base_url` is `http://localhost:11434` by default. Cloud models go through the local server, so nothing else is
  needed once you are signed in. `OLLAMA_API_KEY` is only for the hosted web search and for talking to ollama.com
  directly.
- **OpenAI**: on the Responses wire Dot sends the whole conversation on every call (`store: false`) and carries the
  encrypted reasoning items from step to step, so no state lives on the server.
- **OpenCode Go**: Dot knows the route of the models in its catalog. `route_overrides = { "<model-id>" = "chat" }` in
  `[providers.opencode_go]` adds or corrects one (`chat`, `responses` or `messages`).

`dotx models [ollama|openai|opencode_go]` lists what a provider offers, including the reasoning levels of each model.
`dotx models profile [provider:model]` shows how reliably a model worked in its newest runs and the help its next run
gets (see [How a run works](#how-a-run-works)); without a model it shows `[models] main`.

**Reasoning level.** A model that can think offers a ladder of levels, lowest first, in the words of its provider mapped to
one neutral scale: `off`, `on` (a plain thinking switch), `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. A model that
offers fewer than two has no choice to make, and its picker stays hidden. `models.effort` is the default; one run picks
another with `dotx run --effort high`, with the Reasoning menu of the composer in the web UI, or with `effort` in
`POST /api/runs`. The run keeps the level it was started with (the Details panel shows it). A level the model does not offer
is refused (a 422 over HTTP); the configured default, which is not tied to one model, is rounded to the nearest level the
model has (`off` to its lowest, `max` to its highest).

**Keys** are read from the environment of the process, then from `<home>/.env`, then from the macOS Keychain (service
`dot-agent`, account = the variable name). `dotx auth set OPENAI_API_KEY` reads the value from the terminal without
echoing it and writes the file with mode 0600; `dotx auth status` shows which keys are present. `.env.example` is a
template for the file. An OpenAI key is created on platform.openai.com under API keys (`OPENAI_API_KEY`); the OpenCode
Go key (`OPENCODE_API_KEY`) is the one the OpenCode Console at opencode.ai/auth shows once you have subscribed to Go.
The Ollama models, local or `:cloud` through the signed-in local server, need no key. A running daemon picks up a key
added later when a run first uses that provider, without a restart.

## How a run works

A run is a loop. The model answers with tool calls; Dot checks each call against the [policy](#safety), runs the ones
that pass (up to `parallel_tools` at once) and hands the results back, until the model calls `finish`.

`finish` takes the `answer` and a `success` flag. The `answer` is the complete message the user reads (the findings, the
figures or the text that was asked for, with the links to the sources), and it is the result of the run: the web UI shows
it as the last message of the conversation, `dotx run` prints it. A `chat` turn has no `finish`: its plain reply is the
answer (a small model that is offered both writes the call out as text).

- Old tool results and screenshots are trimmed, and the oldest part of the conversation is summarised by the run's own
  model, so a run stays inside its context budget: `context_tokens`, or three quarters of the model's window when that
  is less. The summary may use a tenth of the budget, so a long run's summary does not grow with each compaction until
  every step compacts. A compaction keeps the newest messages that fit next to the system prompt, the tool definitions
  and its note in 85 % of the budget, and never less than the model's last reply with its results, so the steps after
  it have room. It is tried three times; when the model gives
  no text, or a summary far over that limit, a mechanical summary takes its place and the timeline says so with the reason. Next to the summary a continuity note
  keeps the goal, the plan with the state of each step and your messages, and of the work already done and the paths
  touched the newest that fit in another tenth of the budget (it says how many earlier ones it leaves out), and
  a check lists names, paths and links in the summary that the conversation never had. The `compacted` event names the
  model, how the summary came about and what the check found.
- The plan has steps that are `pending`, `in_progress`, `done`, `failed` or `skipped`; `update_plan` writes it and
  `mark_plan_step` changes one step, so the plan in the web UI moves as the work does. A run that calls `finish` with
  steps still open is asked once to close them.
- How much help a run gets depends on its model. The window is what one request of the model can hold; for Ollama it is
  the smaller of the model's own window and `[providers.ollama] num_ctx`, which Dot sends with every request (the Models
  table of the System page shows it). When three quarters of the window is less than `context_tokens`, the run's budget
  is that, and `tool_output_chars`, the most one tool result may carry, shrinks in the same proportion (never below 2000
  characters). A model whose window cannot hold the system prompt and the tool definitions is refused at the start, with
  a hint. A model of the `weak` tier is reminded in task and research runs, every `reminder_every` steps, of the goal,
  your messages, the plan and its newest actions. `tiers` in `[agent]` sets a model's tier by hand; with
  `scaffold = "measured"` a model is weak when at least `profile_min_calls` tool calls of its newest 30 finished runs
  were measured and at least `weak_refusal_share` of them were refused before they ran (an unknown tool, or arguments
  that were not valid JSON or did not fit the tool). `scaffold = "fixed"`, the default, keeps every model standard.
  The timeline shows a budget cut to the window, why a model is weak, and each reminder.
- A run has no step limit. It ends when the model calls `finish`, when the progress watchdog stops it (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)), when a model call still fails after its retries, or
  when you stop it. In goal mode the goal check can also end it (see [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A reply without a tool call ends the turn in `chat` mode and when the run has not used a tool yet. Otherwise it is
  nudged towards `finish`; after `finish_nudges` nudges the reply itself becomes the result.
- A small model with the full tool list sometimes writes the `finish` call out as a plain message (`gpt-oss:20b-cloud`
  did in most trial runs). Dot does not reinterpret such text: it is nudged first when tools were used, otherwise it
  becomes the result as written. Use a larger model as `main` for task and research runs.
- At most `max_concurrent_runs` runs go at once; the others wait. Delegated runs do not count.
- Runs, their events and their conversations are stored in a SQLite database, so the web UI can replay them. A run
  that a crashed daemon left behind is marked as failed the next time Dot starts.
- A message sent to a running run (steering) reaches the model at its next step. It also clears the run's untrusted
  mark (see [Safety](#safety)).
- A run belongs to the daemon, not to the page that started it: leave the chat and it goes on, its status stays live in
  the sidebar, and you can steer it when you come back.
- `/btw <question>` in the box of a run asks on the side: the run's model answers from what the run has done so far,
  without tools, and the run neither sees the question nor stops for it. Several can be asked at once. The question and
  the answer are `aside_question` and `aside_answer` events, never messages of the conversation.
- Files can go with a goal or a steering message (attach, drop or paste them in the web UI; at most 10 of 20 MB each).
  Text, code and PDF files are put into the message as text (a long one shortened, with its path to read the rest),
  images go to the model as images or, for a model that cannot see, as the `vision` model's description. Anything else
  is refused at upload. The files are kept in `workspace/attachments/`, where the agent's tools can open them.

## Tools

| Area            | Tools                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------ |
| Shell and files | `shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `search_files`                          |
| Web             | `web_search`, `web_fetch`                                                                                          |
| Browser         | `browser_open`, `browser_snapshot`, `browser_find`, `browser_links`, `browser_read_text`, `browser_wait_text`, `browser_act`, `browser_scroll`, `browser_screenshot`, `browser_upload`, `browser_back`, `browser_new_tab`, `browser_switch_tab`, `browser_close_tab` |
| Desktop         | `desktop_apps`, `desktop_open_app`, `desktop_snapshot`, `desktop_read`, `desktop_act`, `desktop_screenshot`, `desktop_clipboard_read`, `desktop_clipboard_write` |
| macOS           | `apple_script`, `notify`                                                                                           |
| Memory, time    | `memory_save`, `memory_search`, `memory_forget`, `schedule_create`, `schedule_list`, `schedule_delete`             |
| Orchestration   | `delegate`, `update_plan`, `ask_user`, `finish`                                                                    |
| Skills          | `skill`                                                                                                            |
| MCP             | `mcp__<server>__<tool>`                                                                                            |

The shell and the file tools work in `<home>/workspace` unless a path says otherwise. A shell command runs with the
`PATH` of your login shell, in its own process group (cancelling a run ends the whole group) and under a timeout
(`[shell] default_timeout_s`, at most `max_timeout_s`). `web_search` uses DuckDuckGo, which needs no key; `web_fetch`
reads HTML and PDF and refuses localhost, LAN and cloud-metadata addresses unless `[web] allow_private_hosts` is set.

## Safety

Before a tool acts, the policy is told what the action does, what it touches and which flags it carries. It answers
allow, ask, hand off to you, or deny.

**Run modes.** `task` (the default) and `chat` can do whatever the policy allows. `research` is read-only: it can read,
search, browse and look at the screen, and a research run can only delegate to research runs.

**Decision order**, strictest first:

1. *Auto-review* denies actions that read secrets or send data out (`secret_access`, `exfiltration`), whatever else is
   configured.
2. The *run mode* denies what a research run may not do.
3. *Hard stops* hand money and credentials to you (`financial`, `credential`): Dot stops and asks you to do that step
   yourself.
4. *Your rules* come next. The first enabled rule, in order, whose tool pattern and regular expression match the action
   decides: `allow`, `preapproved`, `ask` or `handoff`. Rules are managed in the web UI or through `/api/rules`.
5. *The preset* decides everything that is left:

| Preset                 | Asks before                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `aggressive` (default) | privilege, system settings, public posts, messages sent for you, and destructive actions outside the workspace              |
| `balanced`             | all of that, plus anything destructive or installing, anything of high risk, and writing or shell commands outside the workspace |
| `cautious`             | everything that is not read-only                                                                                            |

Who answers what the policy asks depends on the approval mode of the run: you, a review model or nobody (see
[Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). The first three steps above hold in every mode.

**Untrusted content.** Web pages, screens and MCP results can be written by third parties. Dot wraps them in a fence
with a random marker per call (`<web_content …>`, `<screen_content …>`) and tells the model that what is inside is data,
never instructions. A run that has seen such content is marked untrusted, and from then on outward-facing or lasting
actions (messages, posts, privilege, system settings, saving a schedule or a note) ask even when the preset or an
`allow` rule would let them through; only a `preapproved` rule skips the question. A message you send to the run
clears the mark. A delegated run starts as untrusted as its parent.

**Secrets.**

- The browser and desktop tools never fill or read password, one-time-code and card fields, and screenshots black out
  secure fields.
- Environment variables whose names look like secrets are removed from the processes the shell tool starts
  (`[shell] pass_env` lists exceptions).
- The agent can neither read nor change `~/.ssh`, `~/.gnupg`, `~/Library/Keychains`, `~/.aws`, `~/.config/gcloud` and the
  data directory of your Chrome (`[policy] protected_paths`), nor Dot's own key file, token, browser profile,
  configuration and database. The configuration holds the policy and the MCP servers, the database the rules.

**Approvals.** An action that needs a yes becomes an approval: `allow? [y/N]` in the terminal, a card in the web UI. The
run waits (its status is `blocked`) for up to `[policy] approval_timeout_s`; an approval nobody answers expires and
counts as a refusal. The note you write with your answer reaches the model. This is the default approval mode, `ask`; in
`auto` and `yolo` the questions of the policy are answered for you.

**Emergency stop.** Any of these stops Dot from controlling the desktop:

- the **Emergency stop** button of the web UI (or `POST /api/failsafe`) creates the `STOP` file in the home directory
  and cancels every run; the page shows a banner with a button that releases it
- `touch ~/Library/Application\ Support/Dot/STOP` blocks desktop control the same way; delete the file to allow it again
- with `[desktop] failsafe_corner = true` (the default), moving the pointer into the top-left corner of the screen
  aborts the desktop step in progress, and Dot never clicks that corner
- before each step of a desktop batch Dot checks that the pointer is where the previous step left it; if someone else is
  moving the mouse, the rest of the batch is not run

> **The shell guardrail is best-effort, not a sandbox.** Dot reads a shell command before it runs it and judges what it
> would touch, but a shell can do more than any analysis can foresee. Treat Dot as a user of your account with the
> rights of your account: keep backups, keep the workspace for scratch work, and use `balanced` or `cautious` on a
> machine where a mistake would be expensive.

## Autonomy: approvals, goal mode and the end of a run

Two switches decide how much a run does on its own, and a run has no step limit. The switches do not depend on each
other, so every mix works. Each has a default in the configuration, and a single run can override it.

| Switch    | Values                | Default | Configuration                        | One run                                                                                  |
| --------- | --------------------- | ------- | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| Approvals | `ask`, `auto`, `yolo` | `ask`   | `[policy] approvals`                 | `--approvals`, `--auto-approve`, `--yolo`; `approvals` of `POST /api/runs`; the composer |
| Goal mode | on, off               | off     | `[agent] goal_mode`, `goal_patience` | `--goal-mode`, `--no-goal-mode`; `goal_mode` of `POST /api/runs`; the composer           |

`GET /api/status` reports the configured defaults. A run keeps the values it was started with; the run page and its
Details panel show them. The composer of the web UI sends nothing for a picker you did not touch, so the daemon applies
its current default. The YOLO choice is remembered by the browser; it is shown with a warning tint and a note, and the
run page carries a chip for it.

**Approvals** decide who answers a question of the [policy](#safety), the `ask` outcome of its decision order:

- `ask` (the default): you do. The run waits for your answer.
- `auto`: the review model (`[models] review`) does. It is given your instructions (the goal and every message you sent
  to the run: the only authority it recognises), the action, why the policy stopped at it and whether the run has read
  untrusted content. It answers `approve` when the action is plainly what you asked for; `deny` when it is not, goes
  further than you asked or looks like something other than your instructions caused it; and `escalate` when it cannot
  tell, and then the question comes to you as in `ask`, with the reason of the review model in it. What the agent wrote
  into the action (its summary and target) is data to the review model, never an instruction.
- `yolo`: nobody does. The action is allowed and recorded. The question that untrusted content raises (see
  [Safety](#safety)) is waived as well, and so are the refusals and hand-offs below: a yolo run reads secrets, uploads
  or sends files from anywhere, and does money and credential steps itself. That is the price of the mode: a run that
  has read a hostile page can send your files out, pay or sign in without asking you.

Every automatic answer is an ordinary approval record (`decided_by` is `reviewer` or `yolo`, and the note holds the
reason), an `approval_auto` event and a line in the timeline of the run, so you can see afterwards what went through and
why.

**What only yolo waives.** Secrets and exfiltration stay denied in `ask` and `auto`. Money and credentials stay yours
there: Dot stops and asks you to do that step yourself, and the review model is never asked about them. A `yolo` run
does all of it and keeps a record. **What no mode waives:** a `research` run stays read-only, and a rule of yours with
the hand-off behaviour still stops a yolo run.

**If the review model fails** (the provider is down, or the model does not answer in the form asked for after two
attempts) there is no fallback. That one tool call fails with the error as its result, the run goes on, and the
question is not put to you in its place.

**Goal mode** is for `task` and `research` runs; a `chat` has no goal to check against, and asking for one is refused.
When the model calls `finish`, the review model checks the answer against the goal, from the evidence of what the run
did (a digest of its tool calls and the start of each result) and not from what the answer claims:

- `achieved`: the run ends as done.
- `continue`: the goal is not reached yet, so the run is sent back to work with the instruction of the checker. A run is
  sent back at most `goal_patience` times; after that it ends as failed and keeps its answer.
- `blocked`: the goal cannot be reached from here (it needs you, a credential or something that does not exist). The run
  ends as failed with its answer and the reason.

A check can only ask for the original goal to be finished: it adds no work and does not clear the untrusted mark. If the
check itself fails, the run fails and keeps its answer. Each check is a `goal_check` event and a line in the timeline.

A delegated run takes the approval mode of the run that started it and has no goal mode of its own. A scheduled run uses
the model, reasoning level, approval mode and goal mode of its schedule, and the configured defaults for those the
schedule leaves open, so what a schedule does while nobody is there depends on them: with `ask`, a question waits
`approval_timeout_s` and then counts as a refusal.

**The end of a run.** There is no step limit. A run ends by `finish`; by the progress watchdog; by a model call that
still fails after its retries; in goal mode by the goal check giving up; or when you stop it.

**The progress watchdog.** After every step in which a run used tools, Dot compares what it called and what came back
(screenshots included) with the steps before. By default it looks for the same step `stall_limit` times in a row: the
model is told twice to change its approach, and the third time the run stops with a result that says what it did last,
what it touched and where its plan stood, and how to go on in the same thread. With `stall_escalation = true` in
`[agent]` it also notices a cycle of two or three steps, `failure_streak` steps in a row in which every call failed, and
`revisit_window` steps that only repeated calls the run had already made, with the same results. It then answers with a
ladder instead: a nudge; a re-plan, in which the next step may only plan or finish; a second opinion of the review model
(`[models] review`), given to the model as advice (if that model cannot answer, the run stops and says so); a question
to you (not in yolo, scheduled, delegated or eval runs, where nobody would answer); and the stop. Twelve steps without an
alert bring the ladder one rung down again, so a long run is not stopped for hiccups that are hours apart. Every alert
shows in the timeline with what was seen and what was done. The ladder is off by default until evals show that it
helps; without it, a run that keeps changing its steps without getting anywhere goes on using the model until you stop
it. Give such a run a goal that can be checked, and keep an eye on the token count of a long one (`--verbose`, or the
Details panel).

## Measuring the agent

How well Dot finishes work is measured, not guessed, in two ways.

**`dotx stats [--days N]`** counts the runs that really happened (the last 30 days by default), per model: how they
ended (finished, stopped by you, stopped by the progress watchdog, refused by the goal check, cut off when the daemon died or
shut down, internal error, still active), steps, input and output tokens, tool calls and how many of them failed, calls
whose arguments were not valid JSON, stalls and compactions. The database file is opened read-only, so it is safe to run
while the daemon works, and the numbers hold no text of your chats.

**`dotx eval`** plays a catalog of small tasks against a model and judges each by what is left in a workspace of its own:
file edits, shell work, fixing code until its test passes, recovering from an error, multi-step work, read-only
research, a task that cannot be done and has to end as an honest failure, an instruction hidden in a file that must not
be followed, and a goal written in Turkish.

| Command                                                  | What it does                                                                                                                                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dotx eval list`                                         | The tasks, with their kind, mode and number of checks.                                                                                                                                                             |
| `dotx eval check`                                        | Needs no model and costs nothing. Replays the reference solution of every task through the real loop, tools and policy, and proves that a run that touches nothing and says "done" fails every task. Run it after changing a tool, the policy or the loop. |
| `dotx eval run [--model REF] [--task ID]... [--repeat N]` | Plays the tasks against a model (the configured main model if none is given) and prints the score. It spends the tokens of that model: an attempt is cut after 300 s or 600000 tokens, and the notice before it starts states the ceiling. `--repeat` plays every task several times, which shows the tasks a model passes only now and then. |
| `dotx eval report [FILE...]`                             | The score of saved runs, with a comparison table when there are several; without a file, every run saved under `<home>/evals/`.                                                                                    |

Every attempt runs unattended in a throw-away Dot home (its own database and workspace, removed afterwards), with the
Mac desktop, your MCP servers, skills, plugins and the memory embeddings switched off, and with every question of the
policy or of the agent refused. Your own database, workspace and screenshots are never touched; the API keys the model
needs are looked up first and handed over by name. The score holds the pass rate, per kind of task, what a pass costs in
steps and tokens, how often a tool call failed, stalls, runs that were cut, runs that said they were done and were not,
and the tasks that passed only in some attempts. `dotx eval run` saves its report after every attempt, so an
interrupted run keeps what it measured.

A task is data (`src/dotagent/evals/catalog.py`): the files the workspace starts with, the goal, checks (a file exists,
holds or equals something, a JSON file equals a value, a command exits with 0, the answer contains something) and a
reference solution as a script of tool calls. To add one, write it there and run `dotx eval check`.

## The daemon, the web UI and the service

```bash
dotx daemon     # serve the API and the web UI, run the agent and fire the schedules, in this terminal
dotx open       # open the web UI of the running daemon in your default browser, signed in
dotx url        # print its address with the sign-in token
```

The web UI is laid out like a chat app: the chats in a sidebar on the left, one page on the right, and the settings on a
page of their own.

The sidebar lists your chats (the conversation threads) grouped by day, newest activity first; a live dot marks those
with a run going on, waiting for you or failed, and a chat opens at its latest run. Above the list: **New chat** (⇧⌘O)
and a search over the titles (⌘K or `/`); below it, **Settings**. **Select** turns the list into checkboxes: tick chats
(or Select all) and **Delete** asks once, then removes them with every run, step, event and approval of theirs. A chat
with a run under way is refused: wait for the run to end, or cancel it. Escape leaves the selection. Under 860 px the
sidebar becomes a drawer.

**New chat** is the composer and nothing else, centred in the window: the goal, the mode (Task, Chat or Research),
the model and, for a model that offers a choice, its reasoning level (kept in the browser per model), the approval mode
(Ask, Auto or YOLO) and the Goal mode switch (see [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Enter
starts the run, Shift+Enter adds a line. A run that waits for your approval shows above the composer.

A run opens as a conversation. Your goal is the first message. The tool calls of one stretch of work fold into a single
"Worked for 12 s · 5 tool calls" line (open it for every call, result and screenshot), while approvals, questions and
policy notes stay in the open. The answer of the run is the last message, with a Copy button and when it finished and how
long the run took. A strip under the title links the other runs of the same thread, and the box at the bottom steers a
run that is going on or follows up on a finished one. A Details panel shows the status, the model and its reasoning
level, the approval mode and goal mode, the model turns, the tool calls (with the failed ones), the tokens, the duration and the run id, then the plan and
the latest screenshots; it sits beside the conversation from 960 px and opens as a drawer below that.

**Settings** (`#/settings`) is one page with tabs: **General** (the look of this browser, the two switches below, desktop
notifications and the token this browser is signed in with), **Models** (one fold per provider, with the reasoning levels
of each model and a menu to choose one), **Browser** (which Chrome Dot browses with and what Chrome has to allow first;
see [Browser use](#browser-use)), **Rules**, **Memory**, **Schedules** and **System** (the daemon, the emergency stop and
the health checks in a fold, closed, with the number of failures on its line). Which folds are open is kept in the
browser. The tabs sit beside their content when the window has the room and above it otherwise; the earlier addresses of
the memory, schedules, rules and system pages lead to their tabs.

Settings › General › Appearance switches between two looks on the same layout: **Neutral** (cool, round and quiet; the
default) and **Paper** (warm paper tones with a serif heading). Two switches follow the look until you set them
yourself: fold the tool steps (on in Neutral) and the Details panel (on in both). Light and dark follow the system. The
choices are kept in the browser only.

While you are elsewhere, the tab title counts the approvals that wait: `(2) New chat · Dot`. Settings › General ›
Notifications adds
desktop notifications (off until you switch them on; the browser asks for its permission on that click): while the page
is in the background or its window is not the active one, Dot says when it asks for an approval, a question or your help,
and when a run ends (done, failed or stopped; the sub-runs of a run stay quiet). Clicking one opens the run.

Ctrl-C stops the daemon: the runs that are going on are cancelled, and the browser and the other services are closed.

The server listens on `127.0.0.1:8765` (`[server]`) and refuses to start on any other address. Every request must carry
a `Host` of the loopback address and, when it has one, an `Origin` of the server itself; every call under `/api` except
`/api/health` needs the bearer token in `<home>/api-token` (mode 0600). `dotx url` puts the token in the fragment of the
address (`#token=…`), so it never reaches the server or a log; the page keeps it in the browser's local storage and
removes it from the address bar. Pages are served with a strict content security policy and no CORS headers.

```bash
dotx service install     # a launchd agent: starts the daemon at every login and after a crash, never after a clean stop
dotx service status      # whether the job is loaded and the daemon answers
dotx service permissions # make macOS ask, in the name of the service, for the desktop permissions it lacks
dotx service uninstall   # stop the daemon and remove the job
```

- Run `service install` again to restart the daemon after you changed the configuration.
- A launchd job has no terminal, so macOS attaches Accessibility (it covers posting input events) and Screen Recording to
  the Python binary that runs the daemon, not to your terminal. Grant them to that binary; Settings › System in the web
  UI reports what the daemon itself has, and `dotx desktop check` what your terminal has. This prints the binary:

  ```bash
  .venv/bin/python -c "import os, sys; print(os.path.realpath(sys.executable))"
  ```

  `dotx service permissions` leads you through it, one permission at a time. It asks the running daemon (so the dialog
  names that Python binary and not your terminal), copies the binary's path to your clipboard, opens System Settings at
  the first permission that is missing and says what to do there:

  - **Accessibility** (the input events row follows this switch): macOS shows a dialog; choose **Open System Settings**
    and switch the binary on (Touch ID or password).
  - **Screen & System Audio Recording**: macOS shows no dialog for a program that is not an app, so add the binary by
    hand in the pane that opened: press `+`, then Cmd+Shift+G, paste the path with Cmd+V, press Return, choose **Open**
    and switch it on.

  A running process keeps what it had when it started, so after you switched a permission on run `dotx service install`
  (it restarts the daemon) and then `dotx service permissions` again for the next one. The command ends with
  "The service has everything the desktop needs." and exit code 0 once the daemon has accessibility, input events and
  screen recording. Measured through the service with all three granted: the model opened the fixture app, typed a
  name, pressed a button twice and ticked a checkbox in 4 steps and 13 seconds.
- The job does not see the environment of your shell. Give it its API keys through `dotx auth set` or the Keychain.
- When Dot's files are under Desktop, Documents or Downloads, the first start of the job waits for you: macOS asks once
  whether the Python binary may use that folder, and `service install` waits up to 60 seconds for the answer. Measured
  on an Apple-silicon Mac: 42 seconds with a person answering the dialog, then about 1.2 seconds for every later start.

The REST API (all under `/api`; bodies and replies are JSON, event streams are server-sent events):

| Route                                                                           | Purpose                                                      |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `GET /health` (public), `GET /status`, `GET /models`, `GET /doctor`             | liveness, state of the daemon, models per provider (with their reasoning levels), checks |
| `GET /runs`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`            | list, start and read runs                                    |
| `GET /runs/{id}/stream?after=N`, `GET /stream`                                  | live events of a run with its sub-runs; lifecycle of all runs |
| `POST /runs/{id}/steer`, `POST /runs/{id}/cancel`                               | message a running run; stop it                               |
| `POST /runs/{id}/btw` (`{"text": …}`)                                           | a side question, answered without the run seeing it          |
| `POST /attachments` (`{"name", "media_type", "data_b64"}`)                      | keep a file for a message; `POST /runs` and `steer` name it in `attachments` |
| `GET /threads?limit=N`, `DELETE /threads` (`{"ids": […]}`)                      | the chats, newest activity first; delete some with their runs (409 with the ids while one has a run under way) |
| `GET /approvals`, `POST /approvals/{id}/decision`                               | pending approvals; answer one                                |
| `GET\|POST /rules`, `PATCH\|DELETE /rules/{id}`                                 | custom policy rules                                          |
| `GET /memories`, `GET /memories/search`, `POST /memories`, `PATCH\|DELETE /memories/{id}` | notes                                             |
| `GET\|POST /schedules`, `GET\|PATCH /schedules/{id}`, `POST /schedules/{id}/enable\|disable`, `DELETE /schedules/{id}` | schedules (`PATCH` takes the whole schedule) |
| `GET\|POST\|DELETE /failsafe`, `GET /images/{name}`                             | emergency stop; screenshots of runs                          |
| `GET /browser`, `POST /browser/mode` (`{"mode": "own"\|"attach"}`)              | which Chrome Dot browses with and what Chrome allows; switch it (409 while runs are active, 422 when Chrome refuses) |
| `POST /desktop/permissions`                                                     | make macOS ask for the desktop permissions the daemon lacks  |

Errors come as `{"message": …, "context": {…}}`; invalid input is a 422 with the validation errors in the context.

## Browser use

Dot works in Chrome in one of two ways, set by `[browser] mode`: in a Chrome of its own (`own`, the default) or in new
tabs of the Chrome you already have open (`attach`, see below).

**`own`** starts a Chrome of Dot's own on its own profile (`<home>/browser-profile`). It cannot drive your daily profile
that way: since version 136 Chrome refuses remote debugging on its default data directory, and it locks a profile while
it runs. There are two ways to open the sites Dot should use with your accounts:

- `dotx browser login [url]` opens Dot's profile as a normal Chrome window. Sign in, then quit Chrome (Cmd+Q); the
  cookies stay.
- `dotx browser import-chrome [--profile "Profile 1"] [--replace]` copies the cookies and the local storage of a profile
  of your Chrome into Dot's profile, so the accounts that are open in your Chrome are open for Dot too. Quit Chrome
  first. If the Dot service has its browser open, stop it for the import (`dotx service uninstall`, import,
  `dotx service install`). Your Chrome profile is only read and nothing is decrypted; the cookie values stay encrypted
  with the key that Chrome keeps in the Keychain, which Chrome itself reads when Dot's browser starts (macOS may ask
  once; choose Always Allow). The command ends by counting the cookies Chrome can read. The copy is a snapshot: a
  sign-in that expires or rotates needs a new import, and a site that keeps its sign-in in IndexedDB asks again.

Cookies are encrypted with a key, and a profile cannot be read with another key than the one it was written with. A profile
that Dot made uses Playwright's mock keychain in the sign-in window and in the automated sessions alike, so no Keychain
item is involved; the profile of an import uses the system keychain in both (`dotx doctor` shows which). A sign-in made
with an older version of `dotx browser login`, which used the real key, has to be made again.

**`attach`** works in new tabs of the Chrome you already have open, signed in as you are, and starts no Chrome of its own.
It needs Chrome 144 or later and its switch "Allow remote debugging for this browser instance" at
`chrome://inspect/#remote-debugging`, which only you can turn on.

```bash
dotx browser attach     # check Chrome, work in one blank test tab, save mode = "attach"
dotx service install    # restart the service, so that it uses your Chrome
dotx browser detach     # go back to Dot's own Chrome (restart the service again)
```

While `dotx browser attach` runs, Chrome asks whether to allow the connection: click Allow within `attach_timeout_s` (60
seconds). The check opens one blank tab, works in it and closes it again; your other tabs are not touched. The sign-ins
Dot works with are the ones of your Chrome, so nothing is copied and nothing goes stale. What to expect:

- Chrome may ask for the click again whenever Dot makes a new connection, after a restart of the service for example. A
  schedule that fires with nobody there waits `attach_timeout_s` for the click and then fails.
- A tab that Dot opens becomes the active tab of your window, and Chrome may throttle a tab in the background.
- Downloads go to the download folder of Chrome, not to Dot's `downloads/`.
- Dot closes the tabs it opened when they have been idle for `tab_idle_s`. If Dot itself crashes they stay open.
- Calls that arrive while Dot waits for the click queue behind it, and each may in turn wait up to `attach_timeout_s` if
  you do not click.
- Only the stable Google Chrome on its default data directory is supported. `channel` and `headless` apply to `own` only.

The mode can also switch without a restart: Settings › Browser in the web UI, or `POST /api/browser/mode` with
`{"mode": "own"}` or `{"mode": "attach"}`. The live switch runs the same one-blank-tab proof; it is refused with 409
while runs are active, and a failed proof leaves the session in the Chrome it had (422). `dotx browser attach` and
`dotx browser detach` only write the mode, which the service takes up on its next start.

Both modes share what follows.

- The page is observed as an accessibility snapshot in which every element has a ref (`e12`). It covers the viewport plus
  `viewport_margin_px`, and `browser_find`, `browser_links` and `browser_read_text` reach what is outside.
- A collapsed dropdown lists its options on its own line, for example
  `combobox "Shipping" [ref=e3]: options "Standard (3-5 days)" [selected], "Express (next day)"`. `select` takes the
  label or the value of one of them, written as listed; a choice that is not there is refused at once with the options
  that are, the closest first, instead of waiting for the timeout. An option that a script adds within a second of the
  call is still found. A very long list is cut with the number of options left out and the current choice.
- `browser_act` runs up to `max_batch_steps` steps in one call: `click`, `double_click`, `right_click`, `hover`, `fill`,
  `type`, `select`, `press`, `check`, `uncheck`, `scroll_into_view` and `click_at`. After a step Dot waits until the page
  has settled and reports popups, dialogs and downloads that appeared.
- A dialog, or a cookie consent banner, open in the viewport is listed first, under "in front of the page",
  and the line above the page says how many are open. A click that another element takes fails with the name of that
  element and what to do about it (close or answer the box, or press Escape).
- Password, one-time-code and card fields are never filled or read. Cookie banners are declined (`decline_cookie_banners`).
- A conversation thread keeps at most `max_tabs` tabs open and closes idle ones after `tab_idle_s`.
- `[browser] allowed_hosts` and `blocked_hosts` limit the sites of the browser tools and of `web_fetch`; a host also
  matches its subdomains, and an empty `allowed_hosts` means every site. The blocked list starts with the
  cloud-metadata hosts.
- `headless = true` hides the window. Starting Chrome and loading the first page took about 2 seconds with it on an
  Apple-silicon Mac; later calls take well under a second. When Chrome crashes or the connection to it is lost, the
  next call starts it again.
- Chrome runs with its sandbox on. Playwright would start it with `--no-sandbox` unless told otherwise, which makes
  Chrome show a warning bar and weakens the browser that opens untrusted pages.
- Dot's Chrome is the same application as yours, only with another profile. To give it an application of its own, run
  `uv run playwright install chromium` and set `channel = "chromium"`.

## Computer use

The desktop tools see and drive any application through the accessibility tree and, where an application has none, through
screenshots and coordinates. macOS must grant the app that runs Dot three permissions: **Accessibility**, **Input
events** and **Screen Recording**. `dotx desktop check` shows which it has and `dotx desktop setup` asks macOS for the
missing ones and opens their Settings panes.

- `desktop_snapshot` lists an application's window as elements with a role, a name, a value, a state, a position and a ref
  (`a12`). A second view lists only what changed; `query` finds elements anywhere in the application and `scope =
  menu_bar` lists the menu bar. `desktop_read` reads one element's full text.
- `desktop_act` runs up to `max_batch_steps` steps in one call: `click`, `double_click`, `right_click`, `hover`, `press`,
  `focus`, `set_value`, `type`, `paste`, `key`, `scroll`, `drag`, `menu`, `wait` and `activate`. A step names an element
  by its ref or a point by its coordinates. `press`, `focus` and `set_value` work through accessibility without moving
  the pointer; `click`, `hover`, `scroll` and `drag` are real pointer events; `type`, `paste` and `key` are keyboard
  events.
- `desktop_screenshot` captures the screen with ScreenCaptureKit as a JPEG of at most `screenshot_max_edge` pixels.
- `desktop.keep_awake` keeps the display awake while Dot works. Secure fields and password managers are refused.
- `dotx desktop apps`, `dotx desktop snapshot [app] [--scope …] [--query …]` and `dotx desktop screenshot PATH` show what
  the agent would see.

## Memory

Dot keeps short notes between runs: facts about you, your machine and how you like things done. `memory_save`,
`memory_search` and `memory_forget` are tools of the agent; you manage the same notes with `dotx memory` and in the web UI.

```bash
dotx memory add --tag core "I prefer answers in Turkish, with code and commands in English"
dotx memory search --limit 5 how do I like my answers
dotx memory list
dotx memory forget NOTE_ID
dotx memory reindex      # embed every note again, for example after changing [memory] embed_model
```

- A note tagged `core` goes into every run (at most `core_limit` of them), and so does a note you wrote yourself (at
  most `user_limit`), together with up to `inject_top_k` other notes that are close enough to the goal and the earlier
  goals of the conversation (`inject_min_similarity`).
- A note saved or changed while a run works reaches that run at its next step.
- `memory_update` lets the agent correct a note in place; you edit one on the Memory page or with
  `PATCH /api/memories/{id}`. The page also shows who wrote each note and how often runs used it.
- Search combines SQLite full-text search (BM25) with cosine similarity of embeddings and fuses both rankings.
  `embed_enabled = false` searches by words only. The word search does not mind case, accents or the Turkish dotted and
  dotless i (`toplanti` finds `Toplantı`), and a word of five letters or more also finds the words that start with its
  first five letters, so Turkish endings meet the root (`toplantıda`, `toplantılar`).
- Saving a note close to an existing one (`similar_threshold`) tells the model, which decides whether the old note is
  outdated.
- The default embedding model, `bge-m3`, is multilingual: English and Turkish notes are found by meaning (on a small
  hand-written set, the right note came first for 15 of 17 Turkish questions, against 10 with the English-centric
  `mxbai-embed-large`). `min_similarity` and `inject_min_similarity` are set for its scores. For another Ollama model
  set `[memory] embed_model`, run `dotx memory reindex` and adjust the thresholds to the scores of that model.
- To embed the notes with the OpenAI API instead, for example when the local model gives you trouble, add the key once
  and choose the provider. It is a choice, never a fallback: a failing provider is an error and the other one is not
  tried, and the text of every note and search then leaves this Mac.

  ```bash
  dotx auth set OPENAI_API_KEY
  ```

  ```toml
  [memory]
  embed_provider = "openai"
  embed_model = "text-embedding-3-small"
  min_similarity = 0.40
  ```

  Then run `dotx memory reindex` (vectors of two models cannot be compared, so Dot also drops the old ones by itself).
  `dotx doctor` shows whether the key and the model work. The lower `min_similarity` is needed: the scores of
  `text-embedding-3-small` sit lower than those of `bge-m3` (a note's own question scores 0.38-0.79, other notes mostly
  below 0.3), so the shipped `0.50` lets only part of the right notes through. Measured against the real OpenAI API on the
  same small hand-written set (17 Turkish and 10 English questions over 37 notes; indicative, not a benchmark claim): at
  `0.50` the right note came first for 9 of 17 Turkish questions, at `0.40` for 14 of 17 (16 of 17 within the first three,
  English 10 of 10), where `bge-m3` gives 15 of 17. `inject_min_similarity = 0.55` and `similar_threshold = 0.85` held on
  the measured pairs (no wrong note at 0.55; the same fact scored 0.85-0.94, different facts at most 0.66). A recall costs
  about 0.19 s with OpenAI against 0.04 s with the local model, because every search is a network round trip.

## Schedules

```bash
dotx schedule add --name "Morning digest" --cron "0 9 * * 1-5" "Summarise what is new on the pages I follow and notify me"
dotx schedule add --name "Renew reminder" --at 2026-10-06T09:30 --mode research "Check whether the invoice portal lists a new bill"
dotx schedule list
dotx schedule disable|enable|delete ID
```

A schedule is a complete goal that Dot runs later, once (`--at`) or on a cron rhythm (`--cron`, five fields, read in
`[scheduler] timezone`, which defaults to the zone of this Mac). Each firing starts a fresh run without any conversation,
so the goal has to say everything. The agent can create schedules too (`schedule_create`).

On Settings › Schedules a schedule is created and edited whole: its name, goal, repetition (cron or one moment), time
zone, kind of run (task, chat, research), model, reasoning level, approval mode, goal mode and whether it is on. Options
left on "default" take the configured value when the schedule fires. A schedule the agent creates always uses the
defaults: a run cannot schedule itself into another approval mode.

- Schedules fire only while the daemon runs and the Mac is awake; `dotx service install` keeps the daemon running.
- A firing is at most once. One that is overdue by more than `misfire_grace_s` (the daemon was off) is skipped and waits
  for the next.
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.

## MCP servers

Dot can use the tools of [MCP](https://modelcontextprotocol.io) servers that it starts itself over stdio, or reaches
over streamable HTTP or the older SSE transport. Add one `[[mcp]]` table per server to `config.toml`:

```toml
[[mcp]]
name = "notes"                              # a-z, 0-9 and -, starting with a letter; tools become mcp__notes__<tool>
command = "npx"                             # started with the PATH of your login shell
args = ["-y", "some-mcp-server"]
env = { NOTES_TOKEN = "${NOTES_TOKEN}" }    # ${NAME} is looked up like an API key
allow = ["search", "read_note"]             # offer only these tools; empty = all of them (at most 30)
enabled = true
startup_timeout_s = 60.0
call_timeout_s = 120.0

[[mcp]]
name = "docs"
url = "https://mcp.example.com/mcp"       # a remote server; transport = "http" (default) or "sse"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }  # ${NAME} as above; headers are never logged
```

`dotx mcp list` starts every configured server and shows its tools, or why it does not start. Only tools are used (no
resources, prompts, sampling or OAuth). The tool list is read when Dot starts; a server that dies is started again by
the next call. A server that is enabled but fails to start stops Dot from starting: set `enabled = false` to skip it.
Plugins add their servers too (see [Skills and plugins](#skills-and-plugins)); their names become `<plugin>-<server>`.
MCP results are untrusted content, and the stderr of a stdio server goes to `<logs>/mcp-<name>.log`.

## Skills and plugins

A skill is a folder of instructions for one kind of work, in the Agent Skills layout: `<name>/SKILL.md` with a YAML
frontmatter (`name`, `description`) and optional `scripts/`, `references/` and `assets/`. Every model sees the
`name: description` line of each skill and reads the rest with the `skill` tool when a task matches. Dot always looks
in `<home>/skills` and in the `skills/` folder of every plugin; `[skills] dirs` lists more directories (each holds
skill folders; `~` expands).

A plugin is a folder in the Claude Code layout: `.claude-plugin/plugin.json` (its name), `skills/<name>/SKILL.md` and
`.mcp.json` (`{"mcpServers": {...}}`; `${CLAUDE_PLUGIN_ROOT}` is the plugin folder). Their skills and MCP servers are
used; hooks, commands and agents are not. Dot always looks in `<home>/plugins`; `[plugins] dirs` lists more directories
(each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, skills at the start of
every run.

`dotx skills list` shows every plugin and skill found, and anything left out and why; `dotx doctor` has a row for each.

## Delegation

`delegate(goal, mode?)` hands a sub-goal to a separate run with a conversation of its own and returns its result, so a
long investigation does not fill the parent's context. A delegated run cannot delegate again (the depth is one), does not
take one of the `max_concurrent_runs` slots, starts as untrusted as its parent, and ends when the parent is cancelled. A
research run can only delegate to research.

## Configuration

`config.toml` in the home directory is merged over the packaged `src/dotagent/defaults.toml`, which lists every key with
a comment and is the reference. A key that does not exist is an error. Two environment variables of the process move
things: `DOT_HOME` (the home directory) and `DOT_LOG_DIR` (the logs).

| Path (under `~/Library/Application Support/Dot`) | Content                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `config.toml`                                    | your settings                                                        |
| `.env`                                           | API keys (mode 0600)                                                 |
| `dot.sqlite3`                                    | runs, events, conversations, notes, schedules, rules, approvals      |
| `api-token`                                      | the bearer token of the web UI and the API (mode 0600)               |
| `workspace/`                                     | the working directory of the shell and the file tools                |
| `skills/`, `plugins/`                            | skills and plugins Dot always reads (more via `[skills]`/`[plugins]` dirs) |
| `browser-profile/`                               | the Chrome profile of the browser tools                              |
| `screenshots/`, `downloads/`                     | what the tools capture and download                                  |
| `daemon.pid`                                     | held while a daemon runs; a second daemon is refused                 |
| `STOP`                                           | exists while the emergency stop is engaged                           |

Deleting a chat deletes the screenshot files that only it named. Nothing else is removed by itself: old runs and their
events stay until their chats are deleted. `dotx prune` lists the screenshot files that no chat names any more (what
chats deleted earlier left behind) and the free space inside the database file; `dotx prune --apply` deletes those files
and gives the space back to the disk, after which a delete returns its space by itself. The database is locked for a
moment while it compacts, so run it when no run is active.

Logs are structured and go to `~/Library/Logs/Dot/dotx.log`. The output of the launchd job goes to `daemon.out.log`
and `daemon.err.log` in the same directory, and the stderr of each MCP server to `mcp-<name>.log`.

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
| `hosted`   | your own `OPENAI_API_KEY`, `OPENCODE_API_KEY` and `OLLAMA_API_KEY`; a few tiny requests, skipped per missing key     |

`pytest -m "not live and not internet"` skips the first and third kind, `pytest -m "not hosted"` the requests that
use your keys.

The smoke tests of the eval runner (`tests/smoke/test_evals_runner.py`) need no service at all: a scripted model
plays the real loop, tools, policy and store.

The work on the agent's autonomy (measuring it, noticing a stuck run, adapting to the model, resuming, learning) is
planned and tracked in [ROADMAP.md](ROADMAP.md); read it before continuing that work.

```
src/dotagent/
  agent/      run loop, run manager, approvals, context trimming, vision relay
  llm/        providers (Ollama, OpenAI, OpenCode Go) and their wire codecs
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

## Status and known limits

- **Verified live, through the whole stack:** Ollama with cloud models through the local server; OpenAI on both wires
  (Responses and Chat Completions), with screenshots and with the embeddings; OpenCode Go on its three routes (chat,
  responses, messages), with screenshots that a tool returns; the hosted Ollama web search. The smoke tests of the wire
  formats use Ollama's compatible endpoints as a stand-in; `pytest -m hosted` repeats the calls against the real
  services with your own keys (tiny requests, skipped when a key is missing).
- **Verified with real models:** the approval modes and goal mode. `tests/smoke/test_run_modes_live.py` puts real
  questions to the review model and the goal check, and runs whole runs in `yolo`, `auto` and goal mode on
  `ollama:minimax-m2.7:cloud` against a temporary home. What the gate does with each answer of the review model, and
  what each mode waives, is tested exactly in `tests/smoke/test_approval_modes.py` with the real store and broker. Judging
  is model work, so another review model may judge differently.
- **Not verified live:** MCP against third-party servers (the HTTP and SSE transports ran against a local fixture
  server); macOS older than 15.2 (the error messages for it were run with a faked answer, the Mac they ran on has macOS
  26); the notification banner of the web UI, which needs your permission in the browser; `dotx browser import-chrome`
  against a real Chrome profile and the real Keychain, which only you can
  run (its logic ran on a synthetic profile with the real file layout, and the shared key between the sign-in window and
  the automated sessions ran with an ordinary Chrome on the mock keychain); `attach` mode against your own Chrome. Its
  logic and its tab handling ran against a hidden scratch Chrome with a debugging port, which plays your Chrome but has
  no Allow dialog, so what only your Chrome shows is open: the dialog itself, opening a tab in the window you work in,
  how long a connection takes with very many tabs open, and what Chrome does to a tab in the background. The web UI
  (chats, their selection and deletion, the settings tabs including the Browser one) ran in a headless Chromium against
  the static files and a mocked API, and the API itself in the server smoke tests; the two were not run together against a
  live daemon. No model worked from a real skill or plugin folder (discovery, `dotx skills list` and the `skill` tool ran
  on real folders).
- The shell guardrail is best-effort and not a sandbox (see [Safety](#safety)).
- DuckDuckGo answers a burst of searches with its bot check (HTTP 202). Dot spaces its searches (`search_spacing_s`) and
  retries a blocked one (`search_attempts`), but a long research run can still meet the check; the hosted Ollama search
  (`search_backend = "ollama"`, needs `OLLAMA_API_KEY`) has no such limit and is the better choice for that work.
- The thresholds of the memory search were measured on a small hand-written set of English and Turkish notes
  (indicative, not a benchmark); another language, another embedding model or a much larger memory may need other
  `min_similarity` values.
- Schedules fire only while the daemon runs and the Mac is awake. `dotx service install` writes, lints and loads the
  launchd job that keeps the daemon running in your login session.
- MCP: tools only, over stdio or the streamable HTTP and SSE transports (no resources, prompts, sampling or OAuth).
- All conversation threads share one Chrome window.
- Every model call carries the schemas of over forty tools, several thousand tokens. A model with a small context
  window or a metered key feels it; `allow` lists keep the MCP part short.
- A run costs what its model costs: the loop makes many calls, and screenshots are expensive. A run has no step limit, so
  what bounds the cost is the stop button, the progress watchdog, `goal_patience` for the send-backs of goal mode, and
  the concurrency limits (`max_concurrent_runs`). Without `stall_escalation`, a run that varies its steps without getting
  anywhere ends only when you stop it.
