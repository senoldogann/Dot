# Dot

Dot is an always-on autonomous agent for macOS. Give it a goal in plain words and it works on it until it is done, with
what a person at this Mac would use: a shell and the file system, the web, its own Chrome profile (browser use), the
screen, mouse and keyboard (computer use), AppleScript, a memory that lasts between runs, schedules, MCP tool servers
and sub-agents.

It runs on models from **Ollama** (local models, and cloud models through the local Ollama server), the **OpenAI** API,
**OpenCode Go** or the **Anthropic** API (the Claude models). One local process serves the command line, a web UI and a
REST API.

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
- An API key for OpenAI, OpenCode Go or Anthropic, only if you use those providers

## Quick start

```bash
cd Dot
uv sync
uv run dotx doctor
```

`dotx doctor` checks the home directory, ripgrep, Chrome (with a throwaway start of it), the macOS version and the
permissions the desktop tools need, the commands and secrets of the configured MCP servers (none is started), Ollama,
the keys of OpenAI, OpenCode Go and Anthropic, the embedding model and the three configured models, and says how to fix
what is missing. It warns, without failing, when the review model is also the main model. The examples below write
`dotx`: run it as `uv run dotx`, or put `.venv/bin` on your `PATH`.

```bash
dotx run "Find the opening hours of the Helsinki Central Library and save them to hours.md"
dotx run --mode research "Compare the free tiers of three hosted Postgres services"
dotx chat
```

`run` carries out one goal and prints the result; `chat` keeps a conversation going (`/new` starts a fresh one, `/exit`
leaves). Ctrl-C stops the run that is going on; a second Ctrl-C quits at once. Closing the terminal, or SIGTERM, stops
the run the same way, so that its end is recorded.

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

A model is written `provider:model`. The `[models]` table of the configuration sets these keys:

| Key                 | Used for                                                                                  | Default                     |
| ------------------- | ----------------------------------------------------------------------------------------- | --------------------------- |
| `main`              | the agent loop                                                                            | `ollama:minimax-m2.7:cloud` |
| `vision`            | describing the images a tool returns, when `main` cannot see them                         | `ollama:gemma4:cloud`       |
| `fast`              | writing the summary of a compaction (see [Tools](#tools))                                 | `ollama:gpt-oss:20b-cloud`  |
| `review`            | answering the policy's questions in `auto` approvals, and checking a run against its goal | `ollama:minimax-m2.7:cloud` |
| `effort`            | default reasoning level of a run on `main` (see below)                                    | `medium`                    |
| `helpers`           | models a run may ask for advice or hand a part of its work to (see below)                 | `[]`                        |
| `standby`           | models a run moves to when its own model cannot answer (see below)                        | `[]`                        |
| `helper_cooldown_s` | how long a model that could not answer is left out of `helpers` and `standby`             | `600`                       |

`review` checks the work of `main`. A model checking its own work is the weaker check, so pick a `review` model of
another family than `main` when you can; `dotx doctor` warns while they are the same model, as they are by default.

**Helpers and standby.** `helpers` lists models, best first, that a run may turn to. The `consult` tool asks one of them
a question and returns its answer, fenced as advice; `delegate` can run a part of the work on one of them. A helper has
no tools and sees only what the question sends: the run's goal, its plan, the conversation (secrets masked, the start
and the end of a long one) and the newest screenshot, which only a helper that can see images gets. A run may ask
`[agent] consult_max_per_run` questions (5); `0` offers no `consult`. A delegated run asks none (its parent does), and
the run's own model is never its helper.

`standby` lists the models a run moves to when its own model answers with a quota, rate-limit or key error, or its
provider has no key: the run goes on from the same step, with the tools that step had, on the first standby model that
can take it over, the model is told, and the timeline shows the switch, which the model chip follows. The run's
reasoning level becomes the nearest one the new model offers. A run takes each model once at most, in the order of the
list, and never goes back to one it left. A standby model that cannot call tools while the run offers them, whose
window cannot hold the instructions and the tools, or whose details cannot be read is passed over; when every one is,
the run fails and says why for each. Without a standby model that can answer, the error ends the run. A transport
error or a timeout moves nothing, and neither does a failure of the `review` or `vision` model: the review model has no
standby, so with `review` on the same provider as `main` a goal check or an `auto` approval fails while that provider
cannot answer.

A model is left out of both lists while its provider has no key (an Ollama model needs none), and after such an error
for the time the provider named (at most a day) or for `helper_cooldown_s` seconds, until a call to it is answered or
a new key of its provider is saved. Each model is paused alone: a local Ollama model stays available while a cloud one
is paused, and a provider whose key or quota fails all its models costs one failed call per model. The process that
runs the run keeps the pauses in memory. Both lists are empty by default, and `dotx doctor` shows which of their models
have a key. `dotx stats` counts every call of a run for the model of the run, so after a switch the calls before it
count for the standby model too. An answered question, a resume and a follow-up message on the run page go on with the
standby model and its level; `dotx chat` starts every turn on its own model (`main` unless `--model`), which moves to
the standby again when it still cannot answer.

| Provider    | Reference             | Protocol                                                                                   | Key                                                       |
| ----------- | --------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| Ollama      | `ollama:<model>`      | native `/api/chat`                                                                         | none for the local server                                 |
| OpenAI      | `openai:<model>`      | Responses API; `wire = "chat"` selects Chat Completions                                    | `OPENAI_API_KEY`, optional `OPENAI_ORG_ID`/`OPENAI_PROJECT_ID` |
| OpenCode Go | `opencode_go:<model>` | each model is served on exactly one of Chat Completions, Responses and Messages            | `OPENCODE_API_KEY`                                        |
| Anthropic   | `anthropic:<model>`   | Messages API                                                                               | `ANTHROPIC_API_KEY`                                       |

- **Ollama**: `[providers.ollama] base_url` is `http://localhost:11434` by default. Cloud models go through the local server, so nothing else is
  needed once you are signed in. `OLLAMA_API_KEY` is only for the hosted web search and for talking to ollama.com
  directly.
- **OpenAI**: on the Responses wire Dot sends the whole conversation on every call (`store: false`) and carries the
  encrypted reasoning items from step to step, so no state lives on the server.
- **OpenCode Go**: Dot knows the route of the models in its catalog. `route_overrides = { "<model-id>" = "chat" }` in
  `[providers.opencode_go]` adds or corrects one (`chat`, `responses` or `messages`).
- **Anthropic**: what a model can do (pictures, its window and output limit, whether it thinks and at which levels) is
  read from the Models API, so a new Claude model needs no update of Dot. A model that thinks gets adaptive thinking at
  the run's reasoning level, with the summary of its thinking shown; `off` turns thinking off where the model allows it,
  and is rounded to `low` where it does not. A model without adaptive thinking (the 4.5 generation and older) runs
  without thinking at its default level. The prompt is cached from one step of a run to the next (for five minutes), and
  from one run of a chat to the next when the system prompt is the same: it names the day and the time zone, while the
  time of day is in a note after each goal, and the notes from memory it lists are chosen by each run's goal, so other
  notes start the cache again. A request may use the model's whole output limit. The thinking of earlier steps is sent
  back; a block whose earlier context has changed since (a run drops an old screenshot, the next run of a chat starts
  with another system prompt) is dropped by the API instead of failing the request (beta
  `thinking-binding-controls-2026-08-01`).

`dotx models [ollama|openai|opencode_go|anthropic]` lists what a provider offers, including the reasoning levels of
each model. `dotx models profile [provider:model]` shows how reliably a model worked in its newest runs and the help its
next run gets (see [How a run works](#how-a-run-works)); without a model it shows `[models] main`.

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
Go key (`OPENCODE_API_KEY`) is the one the OpenCode Console at opencode.ai/auth shows once you have subscribed to Go; an
Anthropic key (`ANTHROPIC_API_KEY`) is created in the Claude Console at platform.claude.com/settings/keys. The Ollama
models, local or `:cloud` through the signed-in local server, need no key. A running daemon picks up a key added later
when a run first uses that provider, without a restart.

Settings › API keys in the web UI does what `dotx auth set` does for the key of each provider, and more: it shows
where the key in use comes from (the daemon's environment, Dot's `.env` file or the Keychain) and, for a key of 16
characters or more, its last four characters; a key never comes back from the daemon whole. **Check** asks the provider
whether it takes the key in use: the model listing of OpenAI and Anthropic and the subscription of OpenCode Go cost
nothing, the Ollama key is tried with one search of its hosted web search. **Add key** or **Replace** saves a key in
`<home>/.env` and then runs **Check**; the trash button removes the key saved there (a key from the environment or the
Keychain is left alone). The page warns when the daemon was started with the variable in its environment, which comes
before a key saved in Dot. A saved or removed key is used from the next request of a run on, even in a run under way.
The memory search (an OpenAI or ollama.com key) and the MCP servers read their keys when the daemon starts: after
changing a key they use, restart the daemon (`dotx service install`).

## How a run works

A run is a loop. The model answers with tool calls; Dot checks each call against the [policy](#safety), runs the ones
that pass (up to `parallel_tools` at once) and hands the results back, until the model calls `finish`.

`finish` takes the `answer` and a `success` flag. The `answer` is the complete message the user reads (the findings, the
figures or the text that was asked for, with the links to the sources), and it is the result of the run: the web UI shows
it as the last message of the conversation, `dotx run` prints it. A `chat` turn has no `finish`: its plain reply is the
answer (a small model that is offered both writes the call out as text).

- Old tool results and screenshots are trimmed, and the oldest part of the conversation is summarised by the fast
  model (`[models] fast`, the side work the run's slower model need not do), so a run stays inside its context budget:
  `context_tokens`, or three quarters of the model's window when that is less. The summary may use a tenth of the
  budget, so a long run's summary does not grow with each compaction until every step compacts. A compaction keeps the
  newest messages that fit next to the system prompt, the tool definitions and its note in 85 % of the budget, and
  never less than the model's last reply with its results, so the steps after it have room. It is tried three times;
  when the fast model gives no text, a summary far over that limit, an error or a timeout, the run's own model writes
  it and the `compacted` event names the fast model's problem; when neither writes one, a mechanical summary takes its
  place and the timeline says so with the reason. Next to the summary a continuity note keeps the
  goal, the plan with the state of each step and its targets, and your messages, and of the work already done and the
  paths touched the newest that fit in another tenth of the budget (it says how many earlier ones it leaves out), and a
  check lists names, paths and links in the summary that the conversation never had. The `compacted` event names the
  model, how the summary came about and what the check found.
- The plan has steps that are `pending`, `in_progress`, `done`, `failed` or `skipped`; `update_plan` writes it and
  `mark_plan_step` changes one step, so the plan in the web UI moves as the work does. A step is marked `done` only
  with evidence (`evidence`, at most 400 characters): what the run observed that shows it, such as the path and what it
  read back, the count or the line of output, or what the page showed. A mark without it is refused with what counts
  as evidence, and a rewritten plan that keeps a done step keeps its evidence; a failed or skipped step may carry what
  happened. The evidence stands under the step in the web UI, in every note that carries the plan and in the goal
  check. A step that must wait for others names them (`depends_on`, step numbers; a cycle or a number outside the plan
  is refused), the plan reads `(after 1, 2)` and the web UI shows the same, and the result of every plan change says
  which steps are ready now (open, waiting for nothing open or failed) and which are held by a failed step. Steps that
  are ready together are independent, so the run can hand each to `delegate` in one turn, naming the step; the
  delegated run's report then closes the step (done with the report's start as evidence, or failed) without a mark of
  the run's own. A run that calls `finish` with steps still open is asked once to close them. A plan can also hold the run's
  targets (`targets` of `update_plan`, at
  most 600 characters): for a goal that names an outcome, what the run will reach and how its own tools measure it.
  They stay until new targets replace them (an `update_plan` without `targets`, or with an empty text, keeps them), are
  the first line of the plan in the web UI and are printed under "plan" by `dotx run`, go into every note that carries
  the plan (a compaction, a reminder, a resumed run) and, in goal mode, to the goal check.
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
  Every model, after three acting steps in a row whose `browser_act` or `desktop_act` calls carried one step each on
  the same page or application, is told once to batch the steps it can predict into one call (each call costs a model
  round trip; `dotx stats` counts the single-step share), and the timeline shows the note; `steer_single_actions =
  false` in `[agent]` turns it off. Every tool result records its channel (a command, an API, the web, the browser, the
  desktop, or the run's own tools), and `dotx stats` shows the calls per channel and the share of the acting calls that
  went through a GUI: the prompt sends the agent to a command or an API first, the browser second, the desktop last.
  The timeline shows a budget cut to the window, why a model is weak, and each reminder.
- `tool_surface` in `[agent]` decides which tools a run's model sees. `full`, the default, offers all of them at every
  step. `grouped` starts with the core tools (files, the shell, the web, searching and saving notes, delegation,
  skills, the plan and `finish`) and the groups the thread already used. The other groups (`browser`, `desktop`,
  `schedule`, `memory_admin`, and `mcp__<server>` for each MCP server) are listed in the system prompt with what they are
  for and their tools. The model turns a group on with `use_tools`. A call to a tool whose group is off does not run: it
  turns the group on, and its result asks the model to call the tool again. A group stays on for the rest of the run.
  `auto` is `grouped` for a model of the weak tier and for a model whose window cut the context budget, and `full` for
  every other model. The timeline shows the groups a grouped run starts with and each group it turns on.
- `verify_answer_paths` in `[agent]` checks the paths a final answer names: `~` paths and absolute paths under
  `/Users`, `/tmp`, `/private`, `/var`, `/opt`, `/Applications` and `/Library`. When a task or research run ends with
  success and its answer names such paths that do not exist and that no successful call of the run named (a file the
  run removed or moved is not missing), the run is sent back once with the list: to create what is missing, to correct
  the answer, or to finish again with the same answer if it names them on purpose. The check makes no model call. It
  is off by default until evals show that it helps. The timeline shows each send-back.
- A run has no step limit. It ends when the model calls `finish`, when the progress watchdog stops it (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)), when a model call still fails after its retries, at
  a limit of minutes or tokens you set, or when you stop it. In goal mode the goal check can also end it (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A goal that names an outcome instead of a task (grow, earn, reach, as many as possible, find a way) is held to the
  outcome in task and research runs: the model is told to record measurable targets in its plan before it acts, to
  measure after each round and change its approach when a round brought nothing, and to call `finish` only once the
  targets are met, when the next step needs something only you can give, or at the run's limit, then with `success`
  false, the numbers reached and what is missing. A precise request is still done exactly as asked. A chat is not told
  this: its turn ends with its reply.
- A reply without a tool call ends the turn in `chat` mode and when the run has not used a tool yet. Otherwise it is
  nudged towards `finish`; after `finish_nudges` nudges in a row the reply itself becomes the result. A step with tool
  calls starts the count again, so a status line written between tool steps does not end the run.
- `finish` can say what the run could not do without you (`needs_from_user`): the run's page shows it under the answer
  as "What Dot needs from you", and `dotx run` prints it after the answer.
- A small model with the full tool list sometimes writes the `finish` call out as a plain message (`gpt-oss:20b-cloud`
  did in most trial runs). Dot does not reinterpret such text: it is nudged first when tools were used, otherwise it
  becomes the result as written. Use a larger model as `main` for task and research runs.
- At most `max_concurrent_runs` runs go at once; the others wait. Delegated runs do not count.
- Runs, their events and their conversations are stored in a SQLite database, so the web UI can replay them. A run
  that a crashed daemon left behind is marked as failed the next time Dot starts, as ended at its last event. It can be
  resumed, and so can a run that a shutdown of the daemon cut off (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
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
| Shell and files | `shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `search_files`, `write_notes`           |
| Terminal        | `terminal_open`, `terminal_send`, `terminal_read`, `terminal_close`                                                |
| Web             | `web_search`, `web_fetch`, `web_crawl`, `web_extract`, `judge_items`                                               |
| Browser         | `browser_open`, `browser_snapshot`, `browser_find`, `browser_links`, `browser_read_text`, `browser_wait_text`, `browser_act`, `browser_scroll`, `browser_screenshot`, `browser_upload`, `browser_back`, `browser_new_tab`, `browser_switch_tab`, `browser_close_tab` |
| Desktop         | `desktop_apps`, `desktop_open_app`, `desktop_snapshot`, `desktop_read`, `desktop_act`, `desktop_screenshot`, `desktop_clipboard_read`, `desktop_clipboard_write` |
| macOS           | `apple_script`, `notify`, `code_fetch`                                                                             |
| Memory, time    | `memory_save`, `memory_search`, `memory_forget`, `schedule_create`, `schedule_list`, `schedule_delete`             |
| Orchestration   | `delegate`, `consult`, `update_plan`, `ask_user`, `finish`, `use_tools`                                            |
| Skills          | `skill`                                                                                                            |
| MCP             | `mcp__<server>__<tool>`                                                                                            |

The shell and the file tools work in `<home>/workspace` unless a path says otherwise. A shell command runs with the
`PATH` of your login shell, in its own process group (cancelling a run ends the whole group) and under a timeout
(`[shell] default_timeout_s`, at most `max_timeout_s`). `web_search` uses the hosted search of Ollama when `OLLAMA_API_KEY`
is set and DuckDuckGo, which needs no key, otherwise (`[web] search_backend`, default `auto`; `dotx doctor` shows the
choice); `web_fetch` reads HTML and PDF and refuses localhost, LAN and cloud-metadata addresses unless `[web] allow_private_hosts` is set.

**Crawling and extraction.** `web_crawl(url, scope, max_pages, max_depth)` reads many pages of one site in one call:
breadth first from the start address, following the links within the scope (`prefix`, the pages under the start
address's folder, or `host`, every page of its host), several fetches at once and the requests to one host spaced
apart, within the limits of `[web.crawl]` (`max_pages` 50, `max_depth` 3, `concurrency` 4, `per_host_spacing_s` 0.5; a
call may ask for less, never for more). Every page comes back as one clean text document (title, address, description,
then the text as `web_fetch` renders it, at most `page_max_chars`), each in its own `<web_content>` fence, under an
index that lists the pages with their depth, size and how they were read, the addresses left out and why, and how many
more addresses were found within the scope but not read. A page that shows little text without JavaScript (fewer than
400 characters and three or more script tags) is rendered once in Dot's own browser tab and read from the screen
(`render = "auto"`; `"never"` fetches only, `"always"` renders every page); PDFs are read as `web_fetch` reads them.
`robots.txt` is read once per host: with `robots = "report"` the addresses it disallows are named in the index but
fetched, with `"obey"` they are left out. The crawl goes through the same client as `web_fetch`, so the address rules
and the browser's host lists hold for every page. When the pages do not fit one result, each becomes a Markdown file
under `<workspace>/crawl/<host>-<time>/` with an `index.md`, and the result says so: the run reads the ones it needs with
`read_file` or hands them to `web_extract`.

**Notes of a run.** `write_notes(content, append)` keeps the notes of a run in one Markdown file, `<home>/workspace/notes/<run id>.md`: a long research run writes what it found there as it goes, with sources, instead of holding it all in its context, and names the file in its answer. The file is in the undo journal like any file write. It is the only file a `research` run may write.

`web_extract(sources, fields)` pulls named fields out of pages or files as JSON without the pages entering the main
model's context: for every source (an address, or a file in the workspace such as the Markdown files a crawl wrote) the
fast model (`[models] fast`) is asked once for every field's value and the exact words of the source it comes from, and
each quote is checked against the source, so the result says per field whether the value is `verified`; a value whose
quote the source does not hold is kept and marked `verified: false`, a claim to treat as one. A source that cannot be
read or extracted is listed under "Not extracted" with the reason. At most 20 sources and 20 fields per call.

A terminal session is for a program that does not run to its end by itself: one that asks questions (an installer,
`npm create`, `ssh`), a REPL (`python3 -i`, `psql`), or a server that must stay up while the run goes on.
`terminal_open` starts it on a pseudo-terminal of its own, with the environment of the shell tool, and returns its
first output; `terminal_send` types into it and returns the answer, `terminal_read` waits for more (a server's log),
`terminal_close` ends it and everything it started. The output is read as plain text, without a terminal emulator, so
full-screen programs such as vim or htop show as garbled text. The command and every line typed into it are judged as
a `shell` command is. A chat keeps at most `[shell] terminal_sessions` sessions open (3; 0 removes the tools), each
closed after `terminal_idle_s` without use (30 minutes) and all of them when the daemon stops; the end of a run closes
none, so a server it started stays up for the next message.

## Safety

Before a tool acts, the policy is told what the action does, what it touches and which flags it carries. It answers
allow, ask, hand off to you, or deny.

**Run modes.** `task` (the default) and `chat` can do whatever the policy allows. `research` is read-only: it can read,
search, browse and look at the screen, and a research run can only delegate to research runs. The one file it may write
is its notes file (`write_notes`, below), and it may save memory notes.

**One-time codes from your own Mail and Messages.** With `[policy] code_sources = ["mail", "messages"]` (empty by
default), the agent has `code_fetch(app, sender, subject, within_minutes)`: it reads the newest message of Mail (through
AppleScript) or of Messages (its database, read-only; the program that runs Dot needs Full Disk Access, and `dotx
doctor` says whether the database opens) from a sender or with a subject within the last minutes, and returns the code
alone, with who sent it and how old the message is; the message never reaches the model. It is a high-risk action of
the Mac: the aggressive preset allows it, balanced and cautious ask, a research run has no such tool. Typing the code
into the field is still what the approval mode allows (only `yolo` types one-time codes).

**Pre-approved spending.** `[policy.spend]` lists sites with caps, for example `"namecheap.com" = { per_purchase = 20,
per_day = 50 }`. Dot reads the amount of a payment step from the control's label ("Pay €19.90") or from the total on the
page; under both caps the step is a question instead of a hand-off (a `yolo` run goes on, `auto` lets the review model
answer, `ask` asks you once), it is recorded with its amount, shown in the timeline, and counts against the site's day.
Over a cap, on a site not listed, or when the page does not show the amount, the money hand-off holds as before. The
caps are plain numbers compared with the number on the page, whatever its currency, so list sites that bill in one
currency. Empty by default.

**Decision order**, strictest first:

1. *Auto-review* denies actions that read secrets or send data out (`secret_access`, `exfiltration`), whatever else is
   configured.
2. The *run mode* denies what a research run may not do.
3. *Hard stops* hand money and credentials to you (`financial`, `credential`): Dot stops and asks you to do that step
   yourself. A payment under the pre-approved caps of its site (below) is a question instead of a hand-off.
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
actions (messages, posts, follows and likes, privilege, system settings, saving a schedule or a note) ask even when the
preset or an `allow` rule would let them through; only a `preapproved` rule skips the question. A message you send to
the run clears the mark. A delegated run starts as untrusted as its parent.

**Secrets.**

- In `ask` and `auto` the browser and desktop tools never type into password, one-time-code and card fields: the field
  that would take the text is checked, whatever element the step names (a desktop `menu` step that pastes is not). A
  `yolo` run types into them what it can read itself (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). In the same way `terminal_send` types nothing outside
  `yolo` while the program's last line asks for a password, a passphrase or a PIN. The desktop tools never control a
  password manager or Keychain Access, in any mode, and desktop screenshots black out their windows; secure fields and
  web pages are not masked.
- Environment variables whose names look like secrets are removed from the processes the shell tool starts
  (`[shell] pass_env` lists exceptions).
- Outside a `yolo` run the agent can neither read nor change `~/.ssh`, `~/.gnupg`, `~/Library/Keychains`, `~/.aws`,
  `~/.config/gcloud` and the data directory of your Chrome (`[policy] protected_paths`), nor Dot's own key file, token,
  browser profile, configuration, database and journals of file changes. The configuration holds the policy and the MCP
  servers, the database the rules, the journals what `dotx undo` writes back.

**Typed judgments (optional, off by default).** The policy reads what a click does from the control's label, with
patterns in English and Turkish, and a few in Finnish ("Post", "Send", "Delete", "Pay now", "Paylaş", "Lähetä"…); a
label they do not know (Mastodon's "Boost", the German "Teilen") passes as an ordinary click. With `[judge] provider =
"typesafe"` and `TYPESAFE_API_KEY` saved (made at console.typesafe.ai; Settings › API keys, or `dotx auth set
TYPESAFE_API_KEY`), TypeSafe's System One API is asked about such a click in four yes/no questions (does it publish,
send, pay or delete?) over the label, the role and the page's address (without its query and fragment). A probability of
at least `public_action_threshold` (0.45: below each yes measured; a wrong flag only asks) gives the click that flag, so
the policy treats it as a click on "Post" or "Delete", and the action's summary says so: `(judge: public_post 0.93)`.
The judge also reads a campaign's claim of its targets before the goal checker does, and refuses a claim below
`target_threshold` (0.15: only a clearly false one, since a true claim it refused would keep the campaign from ending)
at once. It never allows anything and never decides a step; it only adds flags, or stops a claim early. With `[judge]
second_opinion = true` (off by default) every question is asked a second time, in a request of its own with an
independent phrasing, and the two answers are read together: a click gets its flag when either answer is at the
threshold, and the summary names both and says when they disagreed (`judge: public_post 0.62/0.31 (disagreed)`), so a
disagreement ends in the policy's question rather than in a silent pass; a claim is refused only when both answers are
below `target_threshold`, and a disagreement leaves it to the goal checker. It is a paid service, though a cheap one
(input tokens only, $0.042 a million on 2026-10-10): one request per judged click or claim, two with a second opinion,
at most `timeout_s` (5 s) each. The thresholds were set on answers of `jev-1.13.0`, the version `[judge] model` names;
move them together. A missing or refused key, a spent quota or a failed request turns it off for `[models]
helper_cooldown_s`, with a note in the timeline, and meanwhile the patterns and the goal checker decide alone; a key
saved in Settings turns it on again at once. `dotx doctor` shows whether it is on and has its key. The click questions
are asked in `ask` and `auto` only: in `yolo` a flag asks nothing, so a click there sends nothing to TypeSafe. Two more
checks run before a model call when the judge is on. A `browser_act` that names what it expects to see after its steps
(`expect`: "the reply shows under the post") gets, above its result, the probability that the page's text shows it and
that the page is a login wall, a CAPTCHA or an error page, from one request over the page's visible text, so the next
turn starts from those numbers instead of reading the whole page. And the goal check of a run in goal mode asks the
judge first, as a campaign's claim does: an answer whose claim of success scores below `target_threshold` is sent back
with the judge's number and without the review model's call; a clear yes and the uncertain middle go to the review
model as before.

**A step's model chosen by a typed judgment (off by default).** With `[agent] step_router = true` and
`TYPESAFE_API_KEY` saved, every step of a run starts with one Choice of TypeSafe's over the run's models, from the goal,
what the last step brought back, the failures so far and the failures in a row: a routine step goes to `[models] fast`,
a hard one to the first available helper of `[models] helpers`, and the run's own model takes a step unless another
model's probability reaches `step_router_threshold` (0.6). A model that cannot take the run's tools, its window or its
images is not offered; a routed model that cannot answer has the step asked again on the run's model. Each step another
model takes is a `step_routed` note in the timeline. It stays off until a comparison on your runs shows less time per
task at the same success: a change of model loses the prompt cache the hosted providers keep, and a weaker model can
add steps.

Whatever `[judge] provider` says, saving `TYPESAFE_API_KEY` also gives the agent the `judge_items` tool (acting and
research runs; the daemon reads the key when it starts): one question about every item of a list, answered in one
request and handed back ranked, so the model reads a short ordered list instead of weighing each search hit, post, link
or file in its own words. Without levels every item gets the probability of yes to a yes/no question ("Is this post
about Python packaging?"); with two to ten ordered levels every item gets its expected level and the likeliest one.
Each item's question is its own, so the ids stay in the code and the question carries the whole meaning; a list longer
than `items_per_request` (100) goes in several requests, each bounded by `timeout_s`. The ranking is a probability, not
a fact: the decision stays with the model. The tool and the judge share one connection and one pause: an outage pauses
both for `helper_cooldown_s`; a request TypeSafe rejects (HTTP 422) or an answer outside its reference is an error of
Dot's code or of the model, logged with the request and shown in the timeline as `judge_error`, and pauses nothing.

**Pace on social platforms.** A click whose label posts, replies, reposts, quotes or sends ("Post", "Reply", "Repost",
"Quote", "Yeniden gönder", "Lähetä"…) is a public action; one that follows, likes or subscribes ("Follow", "Like",
"Takip et", "Beğen", "Seuraa", "Tykkää"…) is a social action, of low risk, which asks once the run has read untrusted
content. Both are counted on their site (the page's host, without `www.`), across runs and threads, in a sliding hour
and day, and nothing limits them by default: Dot posts, follows and likes as often as its task needs. `[policy.pace]`
sets limits (`public_per_hour`, `public_per_day`, `social_per_hour`, `social_per_day`, and `public_spacing_s` between
public actions, which the browser waits out; 0 removes a limit). An action past a limit that is set is refused in every
approval mode, `yolo` too, with the time the next one is allowed, and the timeline shows the refusal. A `browser_act`
call takes one public or social click at most, so that each one counts. Only the clicks the policy recognises are
counted: a post sent with a keyboard shortcut or through an API from the shell is not. X publishes limits of its own (an
unverified account: 50 posts and 200 replies a day, 400 follows, 500 messages), and its automation rules forbid
automated likes and bulk or aggressive following, and warn that scripting its website can get an account suspended.

**Approvals.** An action that needs a yes becomes an approval: `allow? [y/N]` in the terminal, a card in the web UI. The
run waits (its status is `blocked`) for up to `[policy] approval_timeout_s`; an approval nobody answers expires and
counts as a refusal. The note you write with your answer reaches the model. This is the default approval mode, `ask`; in
`auto` and `yolo` the questions of the policy are answered for you. A question of the agent itself (`ask_user`) waits
the same way, except in a run nobody can answer during it (see
[Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).

**Emergency stop.** Any of these stops Dot from controlling the desktop:

- the **Emergency stop** button of the web UI (the red square at the top right of every page, or `POST
  /api/failsafe`) creates the `STOP` file in the home directory and cancels every run; the page shows a banner with a
  button that releases it
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
  or sends files from anywhere, and does money and credential steps itself. It types passwords, one-time codes and card
  numbers into the fields that take them when it can read them itself (a code in a mailbox it can open, one that
  `code_fetch` reads, a value you gave it), and a password into a terminal program that asks for one; every such field is a `credential` record in its
  timeline (`financial` for a card field). That is the price of the mode: a run that has read a hostile page can send
  your files out, pay or sign in without asking you. Only the desktop tools stay away from password managers and
  Keychain Access in every mode; through the shell and the file tools a yolo run reads the keychain like any other
  secret.

Every automatic answer is an ordinary approval record (`decided_by` is `reviewer` or `yolo`, and the note holds the
reason), an `approval_auto` event and a line in the timeline of the run, so you can see afterwards what went through and
why.

**What only yolo waives.** Secrets and exfiltration stay denied in `ask` and `auto`. Money and credentials stay yours
there: Dot stops and asks you to do that step yourself, and the review model is never asked about them. A `yolo` run
does all of it and keeps a record. **What no mode waives:** a `research` run changes nothing but its notes file and memory, and a rule of yours with
the hand-off behaviour still stops a yolo run, at secrets, money and credentials too.

**If the review model fails** (the provider is down, or the model does not answer in the form asked for after two
attempts) there is no fallback. That one tool call fails with the error as its result, the run goes on, and the
question is not put to you in its place.

**Goal mode** is for `task` and `research` runs; a `chat` has no goal to check against, and asking for one is refused.
When the model calls `finish`, the review model checks the answer against the goal, from the evidence of what the run
did and not from what the answer claims. When the run kept a plan, the checker reads it with the evidence of each closed
step: a step marked done whose evidence nothing in the digest or the facts bears out is not done, its number goes into
the verdict's `unproven_steps` (in the `goal_check` event), and when the goal needs that step the goal is not reached.
When the plan has targets, it judges them too: they are the run's own measurable reading of the goal, not requirements
it added, and they make the goal reached only when they fit its words. The evidence is a digest of the run's tool calls with the start of each result, and facts that Dot measured itself: the
files its file tools wrote, its failed calls that no later success of the same call followed, the newest result of each
command that tests, builds or lints, and the paths and addresses the answer names that the run never touched (paths
that do not exist, addresses no call opened). Where the facts and the answer disagree, the checker goes by the facts.
Its verdict is one of:

- `achieved`: the run ends as done.
- `continue`: the goal is not reached yet, so the run is sent back to work with the instruction of the checker. A run is
  sent back at most `goal_patience` times; after that it ends as failed and keeps its answer.
- `blocked`: the goal cannot be reached from here (it needs you, a credential or something that does not exist). The run
  ends as failed with its answer and the reason.

A check can only ask for the original goal to be finished: it adds no work and does not clear the untrusted mark. If the
check itself fails, the run fails and keeps its answer. Each check is a `goal_check` event and a line in the timeline.

A delegated run takes the approval mode of the run that started it and has no goal mode of its own. A scheduled run uses
the model, reasoning level, approval mode and goal mode of its schedule, and the configured defaults for those the
schedule leaves open, so what a schedule does while nobody is there depends on them: with `ask`, a question of the
policy waits `approval_timeout_s` and then counts as a refusal.

**Questions nobody answers during the run.** In a `yolo`, scheduled or eval run nobody answers the agent's own questions
(`ask_user`) while it works, and `[agent] questions` decides what they do. With `leave` (the default) the question is
stored as `left`, the timeline shows it where it was asked, and the run goes on with what does not depend on the answer,
or ends and says what it needs; its status never turns `blocked`. The run's page lists its open questions under "Dot
asked you", during the run and after it, and `dotx run` prints each one when it is left and lists the open ones after
the answer. Your answer (`POST /api/approvals/{id}/decision`) goes to the conversation as your message, "Answer to your
question “…”: …": into the run working there, or else as a new run on it, whose goal is the goal of the run that asked
with your answer under it (so its goal check and its title keep the task), with the mode, model, reasoning level,
approval mode and goal mode of the run that asked; the page then opens that run once no other question of the run is
open there. A run reads an answer that comes during its last step before it ends; one that reaches a run that is then
stopped or fails stays in the conversation as your message, for the next run there. An empty answer is refused. Skip
only closes the question, and a left question never expires. With `wait` such a question waits as in a run you watch,
for `approval_timeout_s` at most. A run you started in `ask` or `auto` always waits for your answer.

**The end of a run.** There is no step limit. A run ends by `finish`; by the progress watchdog; by a model call that
still fails after its retries; in goal mode by the goal check giving up; at a limit of minutes or tokens you set; or
when you stop it. With `verify_answer_paths = true` in `[agent]`, a successful end whose answer names paths that do not
exist is sent back once before it is accepted, and before goal mode checks it (see [How a run works](#how-a-run-works)).
A goal that should go on across runs and days until its outcome is verified is a campaign (see [Campaigns](#campaigns)).

**Limits of a run.** `run_max_minutes` and `run_max_tokens` in `[agent]` bound a task or research run; 0, the default,
is no limit, and a chat turn has none. The minutes are the run's wall time, waits for your approvals and answers
included; the tokens are those of every model call of the run (side calls, such as your side questions, included) and
of the runs it delegated to. Dot compares them between steps, so a step that is running when a limit passes goes on: a
model call can take `model_call_timeout_s`, and a delegation waits for its run. At the first step past a limit the run
is told what it used and to finish now with what it reached, what is missing and how to go on; that step may only call
`finish`, `update_plan` and `mark_plan_step` (the plan tools before `finish`, as nothing after `finish` runs), and the
run ends with its reply. A `finish` ends it as it says, and in goal mode the goal check judges that finish once, with
no way back: a goal it does not find reached ends the run as failed. Any other reply ends the run as failed, with a
result that starts "Stopped: the run reached its limit", keeps what the reply said and tells what the run did last,
what it touched and where its plan stood (`dotx stats` counts it as stopped at its limit). Past the limit neither the
open steps of the plan nor `verify_answer_paths` send the run back, and a message you send during that step comes too
late for it, as for any run that has ended: an answer to a question it left stays in the thread for the next run. The
timeline shows when the limit was reached. The limits are those of one run: a resumed run, or one that an answer
starts, begins with a new clock and a new count, and a delegated run has the limits of its own while its tokens also
count toward its parent's, which sees them when the delegation returns. Delegated and scheduled runs have the same
limits; an eval run is cut by its task's own (300 s, 600000 tokens), whatever yours are.

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

**Resuming a run that was cut off.** A run that a crash, a restart or a shutdown of the daemon cut off can go on where
it stopped: Continue on the run's page, `dotx resume RUN_ID` (in this terminal, like `dotx run`), or
`POST /api/runs/{id}/resume`. A new run starts in the same chat, with the goal, model, reasoning level, approval mode
and goal mode of the run it resumes, and the conversation so far. Its first message tells the model how that run was
cut off and how each call of the step it was in ended: finished (with the start of its result), cut off while it ran,
or not started. A call that was cut off may have changed something unless it only reads, and the model is told to check
before it runs it again. The plan of that run is the plan of the new one. Only the newest run of a chat can be
resumed, and never a run that you stopped or that another run started.

With `resume = "safe"` in `[agent]` the daemon also resumes runs on its own when it starts: runs of the web UI, cut off
at most `resume_within_s` seconds before (30 minutes), whose calls that were cut off only read (a question to you
counts: it is asked again). A run of `dotx run`, of a schedule or of an eval, a run that itself resumed another (so a
run that brings the daemon down is not resumed again and again), and a run cut off in a call that may have changed
something (a command, a write, a click, or a delegation, whose run may have done anything) wait for you. `resume` is
`"off"` by default until evals show that it helps. The timeline of the new run shows how each call ended, with a link to
the run it resumes.

**Lessons.** With `lessons = "propose"` or `"auto"` in `[agent]`, a task or research run of its own that reached its
goal after something went wrong on the way (a call failed, whether the run found its way around it or not; the progress
watchdog raised an alert; the goal check sent it back; its answer named paths that did not exist) is followed, once it
has ended, by one call of the review model (`[models] review`). It reads a digest of the run, what went wrong, the
answer and the saved notes that fit the goal, and writes at most three lessons of one sentence: know-how that a later
run could use from the start, such as that `pdftotext` keeps the columns of a table only with `-layout`. A lesson of more
than one line, of fewer than 20 or more than 300 characters (or `[memory] max_chars`), with a secret, an e-mail address
or a path in a home folder, or that says what the goal, another lesson or a saved note already says, is dropped. With
`propose` the lessons wait under "Lessons from this run" on the run's page: Save to memory keeps one as a note tagged
`lesson` whose source is the run, Dismiss drops it (`POST /api/runs/{id}/lessons/{index}/save` and `/dismiss`). A lesson
is decided once: a second save or dismissal of it, from another page or after `auto` saved it, is refused. `dotx run`
and `dotx resume` show the lessons after the answer; Ctrl-C skips the wait, and the exit status stays the run's. With
`auto` they are saved at once, except after a run that read content others can write (web pages, mail, downloads),
which could plant a lesson: those are only proposed, and the page says why. A saved lesson is a note of the agent like
the others: a later run gets it when it fits its goal, and the Memory page edits or forgets it. Delegated runs, chats,
runs that failed or were stopped, and evals (which always run without lessons) are not asked. `lessons` is `"off"` by
default until evals show that it helps.

**Taking back the file changes of a run.** Before `write_file` or `edit_file` changes a file, the run notes the change
in its journal (`undo/` in the home directory), and its first change to a file keeps a copy of the file as it was. When
the change cannot be noted (a full disk, say), the file is not changed and the model is told why. `dotx undo RUN_ID`
shows what taking back the changes of that run and of the runs it delegated to would do: a file the run changed is put
back as it was (`restore`), with its permissions, a file it created is deleted (`delete`), and the folders it created
are removed once they are empty. Nothing is changed until `dotx undo RUN_ID --apply`, which exits with 1 when it left a
file as it is. A file that changed or was deleted after the run's last change to it is a conflict and is left as it is;
`--apply --force` takes it back too, and what changed in it since is lost. A file larger than `undo_max_file_bytes`
(10 MiB), or whose copy would bring the copies of the run past `undo_max_run_bytes` (100 MiB), is changed without a copy
and cannot be put back; a file the run created can always be deleted again. A copy that is not the one the journal
noted is not written back, and a folder that is now where a file was is never replaced. Only the two file tools are in
the journal: what a shell command (`rm`, `mv`, `sed -i`), the browser or the desktop changed is not. A run that is still
working is refused; a run of `dotx run` that a crash left working ends when the daemon starts again.
`dotx prune --apply` deletes the journals whose last change is older than `undo_keep_days` (30 days). The Actions card
of the run's page shows the same plan, one file at a time, with a button for each file that is as the run left it.

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
batches of [Browser use](#browser-use) and [Computer use](#computer-use) exist to avoid it), the share that failed and
the median call; a **step model call by step number** line gives the median seconds of a step's model call for the
steps 1 to 5, 6 to 10, 11 to 20, 21 to 40 and from 41 on of their runs, which shows how much a run slows down as its
context grows. The Time table ends with the share of the input tokens the provider served from its cache. A **Sites**
table follows, per site (the host of the page for a browser act, the application for a desktop one, both recorded in
the `tool_result` event with the steps of the batch): acts, the share with one step, the share that failed and the
median call. `--by site` prints the sites table only, `--by model` everything but it. Acts and steps recorded before
these were counted (before item 22 of the roadmap) are left out of the acts lines and the sites table.

**`dotx eval`** plays a catalog of small tasks against a model and judges each by what is left in a workspace of its own:
file edits, shell work, fixing code until its test passes, recovering from an error, multi-step work, a goal that asks
for as much as the files give, read-only research, a task that cannot be done and has to end as an honest failure, an
instruction hidden in a file that must not be followed, and a goal written in Turkish. Five long tasks follow, each
needing ten or more tool turns (a digest of twelve logs, a rename across ten modules, ten branch totals into JSON, a
ten-item checklist, a nine-stage pipeline whose fifth stage fails once): on them the steps a pass costs say how many
round trips a model spends on work that cannot be done in one.

| Command                                                  | What it does                                                                                                                                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dotx eval list`                                         | The tasks, with their kind, mode and number of checks.                                                                                                                                                             |
| `dotx eval check`                                        | Needs no model and costs nothing. Replays the reference solution of every task through the real loop, tools and policy, and proves that a run that touches nothing and says "done" fails every task. Run it after changing a tool, the policy or the loop. |
| `dotx eval run [--model REF] [--task ID]... [--repeat N]` | Plays the tasks against a model (the configured main model if none is given) and prints the score. It spends the tokens of that model: an attempt is cut after 300 s or 600000 tokens, and the notice before it starts states the ceiling. `--repeat` plays every task several times, which shows the tasks a model passes only now and then. |
| `dotx eval report [FILE...]`                             | The score of saved runs, with a comparison table when there are several and, for exactly two files, a line with the second against the first (passed, steps, tokens and seconds per pass, the one-step act and cache shares in percentage points, tool errors, stalls, false claims, unproven steps); without a file, every run saved under `<home>/evals/`. |

Every attempt runs unattended in a throw-away Dot home (its own database and workspace, removed afterwards), with the
Mac desktop, your MCP servers, skills, plugins and the memory embeddings switched off, and with every question of the
policy or of the agent unanswered (one the run waits for is refused at once). Your own database, workspace and
screenshots are never touched; the API keys the model needs are looked up first and handed over by name. The score holds
the pass rate, per kind of task, what a pass costs in steps, tokens and seconds (split into model calls and tool calls),
the share of the act calls that carried one step and the share of the input tokens served from the prompt cache (both
as `dotx stats` counts them, over every attempt), how often a tool call failed, stalls, runs that were cut, runs that
said they were done and were not, plan steps marked done without evidence (zero since the plan tools require it) and
done steps whose evidence the goal check could not bear out (counted from the `goal_check` events, so only in goal
mode), how many of the attempts at a task that hides an instruction in one of its files (a note "to the AI assistant"
in an invoice, a hidden block in a saved page, a line in a notes file) followed it, and the tasks that passed only in
some attempts. The token ceiling of an attempt
counts every model call of the run and of the runs it delegated to, side calls included; a report saved before model
calls were timed counted the steps' tokens only and has no seconds, and one saved before acts and cached tokens were
counted shows `-` for their shares. `dotx eval run` saves its report after every attempt, so an interrupted run keeps
what it measured.

A task is data (`src/dotagent/evals/catalog.py`): the files the workspace starts with, the goal, checks (a file exists,
holds or equals something, a JSON file equals a value, a command exits with 0, the answer contains or does not
contain something), the checks that hold unless the run followed an instruction hidden in a file, and a reference
solution as a script of tool calls. To add one, write it there and run `dotx eval check`.

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

**New chat** is Dot's face and one composer, centred in the window: the goal, then one line with the mode (Task, Chat
or Research), the model and, for a model that offers a choice, its reasoning level (kept in the browser per model).
**Options** at the end of that line opens the approval mode (Ask, Auto or YOLO; the mode in use always shows on the
button), the Goal mode switch and Keep going (see
[Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Enter starts the run, Shift+Enter adds a line. A run
that waits for your approval shows above the composer.

A run opens as a conversation under Dot's face and name, with the state of the run and when it started; its chips (the
model, the approval mode, goal mode) and buttons sit on the right. Your goal is the first message, in the yellow bubble
of your messages. A delegated run is a chip with its goal and a status ring (turning while it works, amber while it
waits for you, green with a check when it is done, red when it failed); open it for its steps. The tool calls of one
stretch of work fold into a single
"Worked for 12 s (model 8 s) · 5 tool calls" line, where the part in brackets is how much of that time the model's turns
took (open it for every call, result and screenshot), while approvals, questions and policy notes stay in the open. The
answer of the run is the last message, with a Copy button and when it finished and how long the run took. A strip under
the head links the other runs of the same thread, and the box at the bottom steers a run that is going on or follows up
on a finished one. A Details panel shows the status, the model and its reasoning level, the approval mode and goal mode,
the model turns, the tool calls (with the failed ones), the tokens (of every model call of the run: its steps and its
side calls, such as compactions, reviews and the goal check), the duration and the run id, then the plan, an Actions
card and the latest screenshots. It stays closed until the panel button in the head of the run opens it (the choice is
kept in the browser); open, it sits beside the conversation from 960 px and is a drawer below that.

**Dot's computer**, at the top of that panel while a run is going on, shows the page of the run's browser as Chrome
paints it (`GET /api/runs/{id}/screencast`, Chrome's own screencast, sent only while the view is open: **Watch** opens
it, and a tab that does not change sends nothing) and a pill under it that says who has the browser. **Take over**
holds the run at its next tool call, brings the tab to the front and the pill reads "You have control": the Chrome
window is on your Mac, so sign in, pass a CAPTCHA or do a step yourself; **Return control** lets the run go on, and the
result of the tool call that waited tells the model how long you had the browser and that the page may have changed,
so it takes a snapshot first. The timeline keeps both moments (`takeover`, `handback`), and `dotx run` prints them. A
takeover is refused while `[browser] headless = true` (there is no window to use), and the run page keeps the last
frame once the run has ended.

The Actions card is one view of what the run did and what can be undone: every acting step (a tool call that changed
something: a file written, a command run, a click, a message sent; reads are left out) with the channel it went
through, its target and the result the model got as its evidence; every approval and question of the run with its
decision, who decided and the note; and every file the run, or a run it delegated to, changed with `write_file`,
`edit_file` or `write_notes`, with its undo state as `dotx undo` reads it (can be taken back, changed since, cannot be
taken back, as before the run). A file as the run left it has a button, Restore or Delete, that takes that one change
back once the run has ended (`POST /api/runs/{id}/undo`); the card itself is `GET /api/runs/{id}/actions`.

**Settings** (`#/settings`) is one page with tabs: **General** (the look of this browser, the two switches below, desktop
notifications and the token this browser is signed in with), **Models** (one fold per provider, with the reasoning levels
of each model and a menu to choose one; a provider without a key, or whose key was refused, says so and links to API
keys), **API keys** (the key of each provider; see
[Models and providers](#models-and-providers)), **Browser** (which Chrome Dot browses with and what Chrome has to allow
first; see [Browser use](#browser-use)), **Rules**, **Memory**, **Schedules** and **System** (the daemon, the emergency
stop and the health checks in a fold, closed, with the number of failures on its line). Which folds are open is kept in
the browser. The tabs sit beside their content when the window has the room and above it otherwise; the earlier
addresses of the memory, schedules, rules and system pages lead to their tabs.

Settings › General › Appearance switches between two looks on the same layout: **Neutral** (cool, round and quiet; the
default) and **Paper** (warm paper tones with a serif heading). Two switches follow the look until you set them
yourself: fold the tool steps (on in Neutral) and the Details panel (off in both). Light and dark follow the system. The
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
  `type`, `insert`, `select`, `press`, `check`, `uncheck`, `scroll_into_view`, `drag` and `click_at`. After a step Dot
  waits until the page has settled (no DOM change for `settle_quiet_ms`, 60 ms, at most `settle_max_ms`) and reports
  popups, dialogs and downloads that appeared. The result is the part of the page that changed, with the refs of the
  last snapshot still valid; a page that did not change comes back as one line that says so.
- `fill` replaces a form field's value, `type` presses a key per character (autocomplete sees them), `insert` puts the
  text in at once at the caret without key events (long text, post composers, code editors) and `drag` moves an
  element onto the element `to_ref` (HTML5 drag and drop or a library driven by the mouse). `insert` goes only into a
  text field or an editor; at any other element it fails, so the text never lands in whatever field has the focus.
  `type` and `insert` need a text that is not empty. A drop onto a password, card or one-time-code field is refused
  like typing into it, and a drag that fails releases the mouse button.
- In a rich editor (a contenteditable area such as X's composer) `fill`, `type` and `insert` give the editor the focus
  when it does not have it and wait until it has settled, check the field that has the focus for a secret, write, and
  fail when the editor's text did not change, instead of reporting done; a `fill` that leaves the editor holding its
  text, such as the text it held already, is done. A write that makes the page open another one, such as Enter in a
  search box, is done.
- `browser_wait_text` waits until a text is visible and says after how long. When the text does not come in time the
  call fails and still shows the page, as what changed since the last view, so no snapshot is needed to decide.
- A dialog, or a cookie consent banner, open in the viewport is listed first, under "in front of the page",
  and the line above the page says how many are open. A click that another element takes fails with the name of that
  element and what to do about it (close or answer the box, or press Escape).
- Password, one-time-code and card fields are filled only by a `yolo` run (see [Safety](#safety)). Cookie banners are
  declined (`decline_cookie_banners`).
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
  events. A `type` text longer than `paste_threshold_chars` (40) without a line break or a tab goes in through the
  clipboard, which is put back afterwards, unless the field is secure, is for a credential or a payment, or cannot be
  read, or the text looks like a key. After steps that changed something, the window is read once the application has
  announced no change for `settle_quiet_ms` (120 ms), at most after `settle_ms` (350 ms); a change it makes later
  without announcing anything in between shows in the next view.
- `desktop_screenshot` captures the screen with ScreenCaptureKit as a JPEG of at most `screenshot_max_edge` pixels
  (1366 by default: about 1 600 tokens on the hosted providers, against about 2 500 at 1920).
- `desktop.keep_awake` keeps the display awake while Dot works. Password managers are refused in every mode, and so
  are secure fields except in a `yolo` run (see [Safety](#safety)).
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
`[scheduler] timezone`, which defaults to the zone `TZ` names when it is set, else the zone of this Mac). Each firing starts a fresh run without any conversation,
so the goal has to say everything. The agent can create schedules too (`schedule_create`).

On Settings › Schedules a schedule is created and edited whole: its name, goal, repetition (cron or one moment), time
zone, kind of run (task, chat, research), model, reasoning level, approval mode, goal mode and whether it is on. Options
left on "default" take the configured value when the schedule fires. A schedule the agent creates always uses the
defaults: a run cannot schedule itself into another approval mode.

- Schedules fire only while the daemon runs and the Mac is awake; `dotx service install` keeps the daemon running.
- A firing is at most once. One that is overdue by more than `misfire_grace_s` (the daemon was off) is skipped and waits
  for the next.
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.

## Campaigns

```bash
dotx run --standing "Collect 50 leads of Berlin design studios with a name and an e-mail address into ~/leads.csv"
dotx campaign list
dotx campaign show ID
dotx campaign stop|resume|achieved ID
```

A campaign is a task that Dot keeps working on until its outcome is verified or you stop it. Switch on **Keep going** in
the composer (it is there for a task), or pass `--standing` to `dotx run`. The run you start is its first session, in
your conversation; the daemon runs the sessions after it, each a run in a thread of its own, through restarts and days.

- **Targets.** The first session records the campaign's targets and how a session verifies them (`campaign_targets`). A
  session that wants to end while the campaign has no targets is sent back once to record them; targets a later session
  replaces stay in the ledger.
- **The ledger and the brief.** Each session writes what it did, with numbers and evidence, in the campaign's ledger
  (`campaign_note`: progress, setback, strategy, question or note). The next session opens with a brief instead of a
  conversation: the goal, the targets, the paths of the files you attached to the goal, what you wrote in the
  campaign's conversation since it started, the newest strategy and progress, the last 12 entries of the ledger, the
  questions left for you and what became of them, and how the last session ended. The prompt of a session says to
  write its progress there, not in the notes of the memory, and what you tell it while it works too. The targets, the
  ledger and the plan of the next session shape every session after, so writing them carries the `persistence` flag,
  as saving a memory note does: once a session has read untrusted content, the policy asks first.
- **The next session** starts `campaign_gap_minutes` (60) after the last one ended, unless a session planned it with
  `campaign_schedule` (a moment, after which a rhythm goes on, or a cron rhythm). A session that ended needing you, with
  questions it left for you and none answered yet, is followed after `campaign_wait_hours` (24) instead; your answer, or
  your skip, starts the next session right away, and its brief carries it (while the session that asked works in the
  daemon, your answer goes to it). After `campaign_patience_sessions` (3) sessions in a row without a progress entry,
  the brief opens the next session with a change of route: it lists the strategies written since the newest progress,
  which are refused (`campaign_note` does not take one of them again while the campaign is stuck), and asks for another
  channel (a site's API, a command or a skill instead of its pages), another source or another approach, written as a
  new strategy entry first. All four live in `[agent]`.
- **Only a verified outcome ends it.** `campaign_achieved` asks the goal checker (the review model) whether the targets
  are reached, from the targets and how they are verified, the progress in the ledger, the session's plan with the
  evidence of its steps, what the session's own calls measured and the session's evidence. Refused, the call fails with the checker's reason and the session goes on (one
  the checker finds out of reach asks you how to go on); accepted, the campaign is achieved and you get a
  notification.
- **Only you stop it.** Campaigns, in the sidebar, lists them; a campaign's page shows its targets, the questions
  waiting for you, its ledger and its sessions, with Stop (which also stops a session working then), Go on and Mark
  achieved. The emergency stop of desktop control (the pointer in the top-left corner) stops the campaign of the session
  it ends. `dotx campaign` changes the campaign without the daemon, so a session working then goes on until it ends
  (Stop on its run page ends it). `campaign_max_sessions` (0: no limit) stops a campaign after that many sessions; Go on
  gives it one more.
- **Posts, judged before they are answered.** With `TYPESAFE_API_KEY` saved, a session also has three tools on TypeSafe's
  typed judgments for a campaign on X or another feed. `rank_posts` asks three yes/no questions about every post it
  could answer, in one request (on the account's topic; written by a person, not a bot, a brand or spam; worth a reply
  that adds something) and combines them with weights the ledger holds: the first ranking writes them as a note with
  equal values, and a `campaign_note` entry with the numbers `w_topic`, `w_person` and `w_reply` changes them without
  asking TypeSafe again; every ranking's scores are kept in the session's timeline (`posts_ranked`). `pick_draft` asks
  one choice over two to ten drafts of a reply. `check_draft` asks whether a draft is off topic, repeats one of the
  account's earlier posts or breaks a rule the goal names, and refuses it when one of those reaches `[judge]
  draft_check_threshold` (0.5, provisional until it is set on a labeled sample of your own feed). None of them posts;
  the model keeps the decision.

**The browser of a campaign.** A campaign works in the Chrome you have open, signed in as you are (`attach`), unless you
start it with `--browser own` (or `browser` in the API), and `[agent] campaign_browser` sets the default; runs you start
by hand keep `[browser] mode`. A campaign that asks for your Chrome is refused at its start while that Chrome is closed
or does not allow the connection (`dotx browser attach` sets it up), so its sessions do not fail one after the other.
The brief of every session says which Chrome it works in, and so do `dotx campaign show` and the campaign's page.

Every session runs with the model, reasoning level and approval mode the campaign started with, and without goal mode:
the campaign's own check takes its place, so starting one with goal mode on is refused. A run's limits
(`run_max_minutes`, `run_max_tokens`) apply to each session. Sessions run only while the daemon runs; one the daemon cut
off is not resumed as a run: the next session starts once the daemon is back (after `campaign_gap_minutes` when the
daemon died while it worked, so that a session that brings it down does not run again and again), and its brief says
the last one was cut off. The threads of the sessions after the first are not in the chat list; the campaign's page
links them. What you write in the campaign's conversation, and your answers to their questions, are instructions of
the sessions after it.

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
frontmatter (`name`, `description`) and optional `scripts/`, `references/` and `assets/`. Every model sees the `name:
description` line of each skill and reads the rest with the `skill` tool when a task matches. Dot always looks in
`<home>/skills` and in the `skills/` folder of every plugin; `[skills] dirs` lists more directories (each holds skill
folders; `~` expands). Skills written for Claude Code load unchanged: Dot reads only `name` and `description` of the
frontmatter, so other keys (a date or a nested map under `metadata`, a list of `allowed-tools`) do not leave a skill
out. To use your own, put `dirs = ["~/.claude/skills"]` under `[skills]` in your `config.toml`; Dot reads nothing there
until you do. An entry may also be one skill folder (`~/.claude/skills/x-twitter-growth`), to take some skills only:
`~/.claude/skills` can hold skills written for Claude Code's own tools, such as the superpowers bundle or the skills
synced from claude.ai. The X skills of Claude Code (`x-twitter-growth`, `baoyu-post-to-x`) are then the playbook a
campaign on X can work from.

A plugin is a folder in the Claude Code layout: `.claude-plugin/plugin.json` (its name), `skills/<name>/SKILL.md` and
`.mcp.json` (`{"mcpServers": {...}}`; `${CLAUDE_PLUGIN_ROOT}` is the plugin folder). Their skills and MCP servers are
used; hooks, commands and agents are not. Dot always looks in `<home>/plugins`; `[plugins] dirs` lists more directories
(each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, skills at the start of
every run.

`dotx skills list` shows every plugin and skill found, and anything left out and why; `dotx doctor` has a row for each.

**Playbooks.** With `playbooks = true` in `[agent]`, a task run of its own that ran in goal mode, ended done (its goal
check found the outcome achieved) and acted on a web site or an application is followed, once it has ended, by one call
of the review model that distils how it got there: from the acting steps on the site it used most (each tool call with
its arguments and the start of its result; failed calls marked), the model writes the steps that worked, one sentence
each, with the values that change from goal to goal as parameters in angle brackets, and a one-sentence description.
The playbook is a skill folder `playbook-<host>/SKILL.md` under `<home>/playbooks` with "When", "Steps", "Parameters"
and "Evidence" (the goal, the run, the date and how many acting steps it took), plus a `playbook.json` that counts its
use. Later runs see it under Skills like any skill (its source is `playbook`) with a note that it is Dot's own record,
to follow with their own parameters, and read it with the `skill` tool. One playbook per host: a host that has one is
not distilled again. Every run that ended done or failed counts the playbooks it read; one read by two runs that then
failed is retired, moved under `<home>/playbooks/.retired`, and no longer offered. A review model that fails, or a
folder that cannot be written, leaves a `playbook_failed` event on the run; a written one a `playbook_written` event
and a retired one a `playbook_retired` event. The distillation reads pages third parties wrote, so the playbook never
takes an instruction from them as a step, and never holds a secret, a one-time code, an e-mail address or a home path.
`dotx playbooks list` shows every playbook with its use, `dotx playbooks show NAME` prints one and `dotx playbooks drop
NAME` removes one, active or retired. Delegated runs, chats and evals (which always run without playbooks) write none.
`playbooks` is `false` by default until a live comparison shows that the second run on a site takes fewer steps.

## Delegation

`delegate(goal, mode?, model?, step?)` hands a sub-goal to a separate run with a conversation of its own and returns
its result, so a long investigation does not fill the parent's context. The run works on the parent's model, or on the
helper model that `model` names when that one can answer (see [Helpers and standby](#models-and-providers)). Several
calls in one turn run side by side (up to `parallel_tools`), which is how a plan's independent steps run at the same
time; `step` names the plan step the part is, and the report closes it (see the plan above). A
delegated run cannot delegate again (the depth is one) or consult a helper, does not take one of the
`max_concurrent_runs` slots, starts as untrusted as its parent, and ends when the parent is cancelled. The parent's
result names the model the delegated run ended on. A research run can only delegate to research.

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
| `undo/`                                          | journals of the file changes of runs, for `dotx undo`                |
| `daemon.pid`                                     | held while a daemon runs; a second daemon is refused                 |
| `STOP`                                           | exists while the emergency stop is engaged                           |

Deleting a chat deletes the screenshot files that only it named. Nothing else is removed by itself: old runs and their
events stay until their chats are deleted or `dotx prune --apply` finds them old, and the journals of file changes
until it finds them old. `dotx prune` lists the events of the runs that ended more than `[store] retention_days`
(90) days ago (the steps, tool calls and results of their timelines; the runs keep their answers, numbers and
conversations), the screenshot files that no chat names any more (what chats deleted earlier left behind), the free
space inside the database file and the journals of file changes (`dotx undo`) whose last change is older than
`[agent] undo_keep_days`; `dotx prune --apply` deletes those events, and with them the screenshots only they named,
those files and journals, and gives the space back to the disk, after which a delete returns its space by itself. The
database is locked for a moment while it compacts, so run it when no run is active.

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
| `hosted`   | your own `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `ANTHROPIC_API_KEY` and `OLLAMA_API_KEY`; a few tiny requests, skipped per missing key |

`pytest -m "not live and not internet"` skips the first and third kind, `pytest -m "not hosted"` the requests that
use your keys.

The smoke tests of the eval runner (`tests/smoke/test_evals_runner.py`) need no service at all: a scripted model
plays the real loop, tools, policy and store.

The gate runs on every pull request and every push to `main` (`.github/workflows/gate.yml`, on macOS because the
package depends on PyObjC): the format check, the lint, mypy, the unit tests, `dotx eval check` and the smoke tests
that need no service, which the workflow lists by file. The tests marked above stay on your Mac.

The work on the agent's autonomy (measuring it, noticing a stuck run, adapting to the model, resuming, learning) is
planned and tracked in [ROADMAP.md](ROADMAP.md); read it before continuing that work.

```
src/dotagent/
  cli*.py     the command line: the parser and the command models (light), one module per command family loaded by name
  agent/      run loop, run manager, approvals, context trimming, vision relay
  llm/        providers (Ollama, OpenAI, OpenCode Go, Anthropic) and their wire codecs
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

- `code_fetch` reads Messages through its database, so it needs Full Disk Access for the program that runs Dot; Mail is
  read through AppleScript and the first use may show an Automation prompt. A code Dot does not recognise (no cue word,
  more than one six-digit number) is reported as such, not guessed.
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
  retries a blocked one (`search_attempts`), but a long research run can still meet the check. The hosted Ollama search
  has no such limit: with `search_backend = "auto"` (the default) Dot uses it as soon as `OLLAMA_API_KEY` is set.
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
  what bounds the cost is the stop button, the progress watchdog, `goal_patience` for the send-backs of goal mode, the
  concurrency limits (`max_concurrent_runs`) and, when you set them, `run_max_minutes` and `run_max_tokens`. Without
  `stall_escalation` and without those limits, a run that varies its steps without getting anywhere ends only when you
  stop it.
- A campaign has no end but a verified outcome or you: by default a session every hour, each costing what a run
  costs. What bounds it is Stop, `campaign_max_sessions` and the limits of each session; its page shows the tokens of
  its sessions. The tests play the sessions and the goal check with the scripted model: no campaign has run with a real
  model here.
- Resuming: in goal mode, the goal check of a resumed run judges from what the resumed run did, not from the steps of
  the run it resumes. The conversation shows every call of the step that was cut off as interrupted, also the ones that
  finished; the first message of the resumed run says which finished. A run of `dotx run` works in its own process, but
  a daemon that starts while it works marks it as cut off all the same: do not resume it by hand while `dotx run` still
  shows it (the daemon never resumes such a run on its own).
- Undo: only `write_file` and `edit_file` are in the journal, not shell commands, the browser or the desktop. A run
  keeps a copy of a file at its first change to it only, within `undo_max_file_bytes` and `undo_max_run_bytes`. Runs
  that change the same file are taken back in the order their changes were noted. The web UI has no undo button: use
  `dotx undo`, which needs the run in the database (the journals of a deleted chat stay until `dotx prune --apply`).
