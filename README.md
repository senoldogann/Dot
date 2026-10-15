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
[Safety](#safety) before you let it work on a machine that holds data you care about, and
[What leaves this Mac](#what-leaves-this-mac): by default its models run on Ollama's servers.

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
permissions the desktop tools need, the Office applications and the OneDrive folders of the skills that come with Dot
(none is opened), the commands and secrets of the configured MCP servers (none is started), Ollama, the keys of OpenAI,
OpenCode Go and Anthropic, the embedding model and the configured models, each of which its provider must serve to the
key (Ollama and Anthropic answer for the model itself, OpenAI and OpenCode Go through the list of the models they
serve), and says how to fix what is missing. It warns, without failing, when the review model is also the main model,
when an MCP server's command or secret is missing (Dot starts without that server's tools), and when a provider's list
of models could not be read or the Ollama server could not be reached, as the provider's own row says why (the models of
an Ollama server out of reach are not asked, so the doctor does not wait out the retries of each). The examples below
write `dotx`: run it as `uv run dotx`, or put `.venv/bin` on your `PATH`.

```bash
dotx run "Find the opening hours of the Helsinki Central Library and save them to hours.md"
dotx run --mode research "Compare the free tiers of three hosted Postgres services"
dotx chat
```

`run` carries out one goal and prints the result; `chat` keeps a conversation going (`/new` starts a fresh one, `/exit`
leaves). Ctrl-C stops the run that is going on; a second Ctrl-C quits as soon as that stop is recorded (five seconds at
most). Closing the terminal, or SIGTERM, cuts the run off instead: its end is recorded, and it can be resumed like a run
that a stop of the daemon cut off (`dotx resume RUN_ID`, or Continue on its page). A `dotx` command that dies (killed,
or crashed) records nothing: the daemon closes its run within half a minute, as cut off by the command's death, and it
can be resumed too.

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
| `main`              | the agent loop of a run that names no model; Settings › Models sets it                    | `ollama:minimax-m2.7:cloud` |
| `vision`            | describing the images a tool returns, when `main` cannot see them; Settings › Models sets it | `ollama:gemma4:cloud`    |
| `fast`              | writing the summary of a compaction (see [Tools](#tools)); Settings › Models sets it      | `ollama:gpt-oss:20b-cloud`  |
| `search`            | relaying a web search through the Anthropic API's own search tool (see [Tools](#tools))   | `anthropic:claude-haiku-5-5` |
| `review`            | answering the policy's questions in `auto` approvals, checking a run against its goal, and drawing its lessons; Settings › Models sets it | `ollama:minimax-m2.7:cloud` |
| `effort`            | default reasoning level of a run on `main` (see below)                                    | `medium`                    |
| `helpers`           | models a run may ask for advice or hand a part of its work to (see below)                 | `[]`                        |
| `standby`           | models a run moves to when its own model cannot answer (see below)                        | `[]`                        |
| `helper_cooldown_s` | how long a model that could not answer is left out of `helpers` and `standby`             | `600`                       |
| `context`           | a model's context size where it is not `[agent] context_tokens` (see below); Settings › Models sets it | `{}`         |

`review` checks the work of `main`. A model checking its own work is the weaker check, so pick a `review` model of
another family than `main` when you can; `dotx doctor` warns while they are the same model, as they are by default.

Settings › Models chooses `fast`, `review` and `vision` under the default model (item 232), each from the models that
can do its work: those that call tools for `fast` and `review`, those that see for `vision`. A choice holds from the
next call of that work on, with no restart (a run under way keeps the review model it started with for its goal check,
its second opinion and its approvals), and the config file keeps it with the rest of the file as it was; a model that
cannot do the work is refused, and nothing changes (`POST /api/models/roles`).

**Context size.** Beside each model's window, Settings › Models sets the context size of the model's runs (item 250):
how much of a run each step sends the model before Dot summarizes its older part. The menu offers Dot's default
(`[agent] context_tokens`, 96 000, or three quarters of the window when that is less) and the sizes from 48 000 up to
three quarters of the window; a choice holds from the model's next run, with no restart, and the config file keeps it
in `[models.context]` (`"anthropic:claude-opus-5-5" = 192000`) with the rest of the file as it was. A larger size means
fewer summaries, each of which drops detail of the older part and takes a call of the fast model, and more input a step:
steps that are answered more slowly and cost more on a key billed by the token. **Why** opens, under the model's row,
what the size does; the window and where it comes from (the provider's API, the windows the provider publishes as
Dot's table keeps them, an assumption for a model neither names, or the window Dot asks Ollama for); the default, the
most and the least; and what the model's runs of the last 30 days measured (`GET /api/models/context`): the steps and
the runs, the input of a step at the median, for 95 % of the steps and at most, the share the provider served from its
cache, the input of a run's first step, the summaries, and the seconds of a step by its input. A size below 48 000 (a
run's first step alone sends its instructions, its tools and the request) or past three quarters of the window is
refused, and nothing changes (`PUT /api/models/context`).

**Helpers and standby.** `helpers` lists models, best first, that a run may turn to. The `consult` tool asks one of them
a question and returns its answer, fenced as advice; `delegate` can run a part of the work on one of them. A helper has
no tools and sees only what the question sends: the run's goal, its plan, the conversation (secrets masked, the start
and the end of a long one) and the newest screenshot, which only a helper that can see images gets. A run may ask
`[agent] consult_max_per_run` questions (5); `0` offers no `consult`. A delegated run asks none (its parent does), and
the run's own model is never its helper.

`standby` lists the models a run moves to when its own model answers with a quota, rate-limit or key error (HTTP 401),
refuses access to it (HTTP 403: a model, a region or a plan the key may not use), or its provider has no key: the run
goes on from the same step, with the tools that step had, on the first standby model that can take it over, the model is
told, and the timeline shows the switch, which the model chip follows. The run's reasoning level becomes the nearest one
the new model offers. A run takes each model once at most, in the order of the list, and never goes back to one it left.
A standby model that cannot call tools while the run offers them, whose window cannot hold the instructions and the
tools, or whose details cannot be read is passed over; when every one is, the run fails and says why for each. Without a
standby model that can answer, the error ends the run, and its message is the run's result (what the timeline, a
notification and `dotx run` show); its context, the request and the answer of the provider, is kept with the run's
`run_finished` event and in the log. A transport error or a timeout moves nothing (the run waits for its own model
instead, see below), and neither does a failure of the `review` or `vision` model: the review model has no standby, so
with `review` on the same provider as `main` a goal check or an `auto` approval fails while that provider cannot answer.

A model is left out of both lists while its provider has no key (an Ollama model needs none), and after such an error
for the time the provider named (at most a day) or for `helper_cooldown_s` seconds, until a call to it is answered or a
new key of its provider is saved. A model the plan of its provider does not include (HTTP 402, "not in the plan") and
a plan whose included usage is used up (Ollama's 429 that says so) are such errors too: the connector does not retry
them, and the model is paused for `helper_cooldown_s` at once (item 254). The fast model's work leaves a paused fast model out as well (item 240): a run's
summary is written by the run's own model at once, and `web_extract` reads no source and says until when the fast model
cannot answer, instead of spending the connector's retries on a provider that has refused already. Each model is paused
alone: a local Ollama model stays available while a cloud one is paused, and a provider whose key or quota fails all its
models costs one failed call per model. The process that runs the run keeps the pauses in memory. Both lists are empty
by default, and `dotx doctor` shows which of their models have a key and are served to it. `dotx stats` counts every
call of a run for the model of the run, so after a switch the calls before it count for the standby model too. An
answered question and a resume go on with the standby model and its level; a follow-up message on the run page starts on
the chat's own model (item 199), and `dotx chat` every turn on its own (`main` unless `--model`), each moving to the
standby again when that model still cannot answer.

**A model out of reach.** When the run's own model cannot be reached after its connector's retries (the connection broke
or timed out, the call took longer than `model_call_timeout_s`, or its server failed with a status that retrying can get
past, such as 500, 502, 503, 504 or 529) and no standby model takes over, the run waits for it rather than failing: it
asks the same step again after 10 s, then 20 and 40 s, then every 60 s, for `model_wait_s` in `[agent]` (900 s by
default; at least four times however short the wait; 0 does not wait), and goes on from that step as soon as the model
answers, so a network that drops for a while costs a pause instead of the run. The timeline shows one line for the wait,
which each try rewrites with the error, the number of tries, the next try and the time the wait ends, and one more when
the model answers again. A stop ends the wait at once. A model still out of reach when the wait is over fails the run,
and the result names the wait and the error. A refused request ends the run at once; a quota, rate-limit, key or access
error or a missing key moves it to a standby model or ends it at once. An error a model service reports inside its
answer keeps its words, Ollama's too.

| Provider    | Reference             | Protocol                                                                                   | Key                                                       |
| ----------- | --------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| Ollama      | `ollama:<model>`      | native `/api/chat`                                                                         | none for the local server                                 |
| OpenAI      | `openai:<model>`      | Responses API; `wire = "chat"` selects Chat Completions                                    | `OPENAI_API_KEY`, optional `OPENAI_ORG_ID`/`OPENAI_PROJECT_ID` |
| OpenCode Go | `opencode_go:<model>` | each model is served on exactly one of Chat Completions, Responses and Messages            | `OPENCODE_API_KEY`                                        |
| Anthropic   | `anthropic:<model>`   | Messages API                                                                               | `ANTHROPIC_API_KEY`                                       |
| OpenCode    | `opencode:<model>`    | OpenCode's own agent, in the `opencode serve` Dot starts (Zen's free models)               | none; the `opencode` program                              |

- **Ollama**: `[providers.ollama] base_url` is `http://localhost:11434` by default. Cloud models go through the local
  server, so nothing else is needed once you are signed in. `OLLAMA_API_KEY` is only for the hosted web search and for
  talking to ollama.com directly.
- **OpenAI**: on the Responses wire Dot sends the whole conversation on every call (`store: false`) and carries the
  encrypted reasoning items from step to step, so no state lives on the server.
- **OpenCode Go**: Dot knows the route of the models in its catalog, and of a new Claude, Qwen, MiniMax, Grok, GPT or
  Muse model by the start of its name; any other new model goes to Chat Completions. `route_overrides = { "<model-id>" =
  "chat" }` in `[providers.opencode_go]` adds or corrects one (`chat`, `responses` or `messages`). The Messages route
  marks the prompt for caching, so Claude, Qwen and MiniMax read a run's prompt back from the cache from its second step
  on, at a tenth of the price and with a faster first token (Claude caches only so); the Chat Completions models cache
  it by themselves. A model Go serves at no cost (its id ends in `-free`) says Free in the model picker and in
  Settings › Models. The free models of OpenCode Zen do not answer Dot's own calls: Zen serves them only to OpenCode's
  programs and answers any other with a 403 (checked on 2026-10-12 and 2026-10-15, with and without a key), so they
  run as OpenCode's own agent (**OpenCode**, below). The reasoning levels offered are the
  ones the gateway honours: DeepSeek's models offer none, since the chat route thinks the same at every label (measured
  2026-10-15, item 258), so Ultrathink and Ultracode do not show for them; GLM 5.2, Grok, GPT Luna, Muse Spark,
  Longcat, MiMo and Hy keep theirs.
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
- **OpenCode** (Zen's free models, item 283): a free model of OpenCode Zen (Big Pickle, Nemotron, Step, Space Bunny and
  the others the day's list has) answers OpenCode's own agent only, so a run on one goes to that agent whole. It needs
  the `opencode` program (`brew install opencode`, or opencode.ai) and no key. Dot starts `opencode serve` on a free
  loopback port behind a password of its own when a run or the model menu needs it (about 2 s) and stops it after
  `[providers.opencode] idle_stop_s` (600 s) with nothing to do; it holds about 340 MB while up. The menu lists the free
  models that call tools, from OpenCode's list kept for a day (`models_ttl_s`), with their reasoning levels. The server
  keeps to its own folders in Dot's home (`opencode/`) and reads none of your own OpenCode setup: no key, plugin, skill,
  command, tool or rule of yours, nor a project's `opencode.json` or `AGENTS.md`. Its agent works in Dot's workspace
  with OpenCode's own tools (commands, reading and editing files, searching, fetching and searching the web, its to-do
  list), and each of them asks Dot first: Dot weighs it as the same action of its own tools, by your rules, the preset
  and the run's approval mode, and asks you, the review model or nobody as for any run; a refusal tells the agent why,
  and it goes on. A subagent it asks for is refused, as its session would not be the run's, and it has no question for
  you. Its commands run outside Dot's sandbox (Settings › Sandbox), each after Dot's policy. The run's timeline shows
  the agent's text and thinking as they come, each tool call (`opencode_bash`, `opencode_edit`, ...) with its result and
  each step with its tokens; your messages reach the agent while it works, and Stop stops it. A chat keeps its OpenCode
  session from run to run, so the agent remembers it, and a session that missed what the chat said on another model
  hears it with the next message. What Dot's own runs have besides (Dot's tools, memory notes and skills, the checks of
  a finish, lessons and playbooks) a run here does not have, and a free model cannot be the fast, review or vision
  model, whose calls are Dot's own. Zen's terms of the free models apply: the providers of Big Pickle, Exo, MiMo and
  Muse Spark may use what is sent to them to improve their models (opencode.ai/docs/zen, read 2026-10-15), so keep
  private work on other models. The free tier is Zen's to give: it turned requests down for a few minutes after many in
  a short time, OpenCode's own command line's too (measured 2026-10-15), and it is reported to refuse OpenCode with one
  of its tools switched off, so Dot switches none off and has them ask instead; a run it turns down fails with words
  that say so.

`dotx models [ollama|openai|opencode_go|anthropic|opencode]` lists what a provider offers, including the reasoning
levels of each model, and ends with 1 when no provider answered (the one named, when one is). An Ollama server on this
Mac is asked for its list once, so one that is not running is reported at once, here, in the model menu and by
`dotx doctor`, not after the retries a network needs (item 212). `dotx models profile [provider:model]` shows how
reliably a model worked in its newest runs and the help its next run gets (see [How a run works](#how-a-run-works));
without a model it shows `[models] main`.

**Reasoning level.** A model that can think offers a ladder of levels, lowest first, in the words of its provider mapped
to one neutral scale: `off`, `on` (a plain thinking switch), `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. A model
that offers fewer than two has no choice to make, and its picker stays hidden. `models.effort` is the default; one run
picks another with `dotx run --effort high`, with the Reasoning menu of the composer in the web UI, or with `effort` in
`POST /api/runs`. The run keeps the level it was started with (the Details panel shows it). A level the model does not
offer is refused (a 422 over HTTP); the configured default, which is not tied to one model, is rounded to the nearest
level the model has (`off` to its lowest, `max` to its highest).

**Fast mode.** Claude Opus 5.5, Opus 5 and Opus 4.8 have fast mode: up to 2.5 times the output speed, at twice the
price, a research preview that Anthropic lets an organization into from a waitlist. The Models API does not say which
models have it, so Dot names them from Anthropic's page (`FAST_MODE_MODELS` in `llm/anthropic.py`); Settings › Models
marks them `fast mode` (`fast_mode` in `GET /api/models`). The Reasoning menu of the composer has Speed, Standard
or Fast, for such a model, off at first; the pill then reads `High · Fast`. A chat keeps it with its choices, a new
chat's box in this browser, and the run it starts keeps it as its model and level (`speed` in `POST /api/runs` and in
the queue; the Details panel says so). Each step of such a run on a model with fast mode asks for it (`speed: "fast"`
with the beta `fast-mode-2026-02-01`); a step on another model (a routed step, a standby model) runs at the standard
speed, as do helpers on a model without it. Dot never falls back by itself: a refusal (an organization not let in,
fast mode's own rate limit, once retried) is the run's error, as Anthropic says it. Fast mode for a model without it
is refused (a 422 over HTTP). Switching the speed of a chat starts the prompt cache again.

**Ultrathink and Ultracode.** The Reasoning menu has two choices for one message, under This message only (item 251).
Ultrathink, for a model that offers `max`, asks it for Max for this message. Ultracode, for a model that offers `xhigh`,
asks it for Extra high and tells the run, in its instructions, to split the goal: each part that needs no other part's
result goes to a helper of its own (a `delegate`, several in one turn, each with its plan step), and the run does itself
only what joins or checks their reports; the helpers work at the run's level, or at the one their agent names. The pill
reads the choice until the message goes, and the next message goes with the level the chat keeps, which the choice never
changes. The run keeps it as it was started: the run that resumes it, the one that carries on after an answered question
and every session of a campaign started with it (`once` in `POST /api/runs` and in the queue, `ultrathink` or
`ultracode`; the Details panel says `Max, Ultrathink`). A model without the level is refused (a 422 over HTTP), and a
helper, which cannot delegate, is never told to split. The names are Claude Code's, where ultrathink is a word in a
prompt that asks for deeper thinking on that turn and Ultracode runs a task as a workflow of many agents; in Dot they
are these levels, the second with the split. The word `ultrathink` or `ultracode` typed in the composer (a whole word,
any case; the first of the two when both are there) is the same choice for that message when the model offers its
level (item 259): the pill reads it while the word is in the box, the message goes with it and the word stays in the
text; while the choice is armed, by the word or by the menu, the composer's border is a gradient of green, orange and
purple. A model without the level reads the word as any other word. While a run works, the word goes with a queued
message; the box arms nothing while it edits a queued message, and a steer, which joins the run under way at its
level, says the choice waits for a message that starts a run.

**Keys** are read from the environment of the process, then from `<home>/.env`, then from the macOS Keychain (service
`dot-agent`, account = the variable name); a Keychain that does not answer within 15 seconds (locked, or waiting for a
prompt nobody answers) is an error that says so. `dotx auth set OPENAI_API_KEY` reads the value from the terminal
without echoing it and writes the file with mode 0600; `dotx auth status` shows which keys are present. `.env.example`
is a template for the file. An OpenAI key is created on platform.openai.com under API keys (`OPENAI_API_KEY`); the
OpenCode Go key (`OPENCODE_API_KEY`) is the one the OpenCode Console at opencode.ai/auth shows once you have subscribed
to Go; an Anthropic key (`ANTHROPIC_API_KEY`) is created in the Claude Console at platform.claude.com/settings/keys. The
Ollama models, local or `:cloud` through the signed-in local server, need no key. A running daemon picks up a key added
later when a run first uses that provider, without a restart.

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
figures or the text that was asked for, with the links to the sources), and it is the result of the run: the web UI
shows it as the last message of the conversation, `dotx run` prints it. A `chat` turn has no `finish`: its plain reply
is the answer (a small model that is offered both writes the call out as text).

- Old tool results and screenshots are trimmed, and the oldest part of the conversation is summarised by the fast
  model (`[models] fast`, the side work the run's slower model need not do), so a run stays inside its context budget:
  `context_tokens` (96 000 by default) or the model's own context size (Settings › Models, item 250), or three
  quarters of the model's window when that is less. A wider budget means
  fewer summaries in a long run and more input tokens a step (up to twice as many at the end of a long run); 48 000 was
  the default before 2026-10-11, and an owner on a metered key who weighs tokens over summaries sets it back. The
  summary may use a tenth of the budget and never more than 1 500 words, so a long run's summary does not grow with
  each compaction until every step compacts, and does not cost the fast model minutes of writing. A compaction keeps as
  many of the newest messages as fit next to the system prompt, the tool definitions and its note in 85 % of the size at
  which it starts (`context_summarise_share` of `context_tokens` or of the model's own size, or the whole budget when
  the window leaves less), and
  never less than the model's last reply with its results, so the steps after it have room. It is tried three times:
  a summary far over the limit is asked again with the limit and the overrun in front, and one that lacks a section is
  asked again once with the missing sections named and the first draft carried along; when the fast model gives no text,
  a summary far too long, an error or a timeout, the run's own model writes it, and while the fast model is paused after
  a quota, rate-limit or key error (see `standby`) the run's own model writes it without asking the fast one (item 240);
  the `compacted` event names the fast model's problem; when neither writes one, a mechanical summary takes its place
  and the event says so with the reason. The conversation shows no note of a compaction (item 260): the Details panel
  counts them and says how many checks flagged something, and the Cost panel keeps the summaries' share. Next to the
  summary a continuity note keeps the
  goal, the plan with the state of each step and its targets, and your messages, and of the work already done and the
  paths touched the newest that fit in another tenth of the budget (it says how many earlier ones it leaves out), and a
  check lists names, paths and links in the summary that the conversation never had (an address cut short, with the
  ellipsis of a cut value, or one lengthened by a fragment counts as known). The `compacted` event names the model,
  how the summary came about, the attempts it took and what the check found.
- The plan has steps that are `pending`, `in_progress`, `done`, `failed` or `skipped`; `update_plan` writes it and
`mark_plan_step` changes one step, so the plan in the web UI moves as the work does. A step is marked `done` only with
evidence (`evidence`, cut at 400 characters): what the run observed that shows it, such as the path and what it read
back, the count or the line of output, or what the page showed. A mark without it is refused with what counts as
evidence, and a rewritten plan that keeps a done step keeps its evidence; a failed or skipped step may carry what
happened. The evidence stands under the step in the web UI, in every note that carries the plan and in the goal check. A
step that must wait for others names them (`depends_on`, step numbers; a cycle or a number outside the plan is refused),
the plan reads `(after 1, 2)` and the web UI shows the same, and the result of every plan change says which steps are
ready now (open, waiting for nothing open or failed) and which are held by a failed step. Steps that are ready together
are independent, so the run can hand each to `delegate` in one turn, naming the step; the delegated run's report then
closes the step (done with the report's start as evidence, or failed) without a mark of the run's own; a step the plan
does not have (a slip, or a delegate before the plan) does not stop the delegation: the run goes, the result's first
line says the step was not in the plan, and nothing is marked. A run that calls `finish` with steps still open is asked
once to close them. A plan can also hold the run's targets (`targets` of `update_plan`, at most 600 characters): for a
goal that names an outcome, what the run will reach and how its own tools measure it. They stay until new targets
replace them (an `update_plan` without `targets`, or with an empty text, keeps them), are the first line of the plan in
the web UI and are printed under "plan" by `dotx run`, go into every note that carries the plan (a compaction, a
reminder, a resumed run) and, in goal mode, to the goal check.
- How much help a run gets depends on its model. The window is what one request of the model can hold; for Ollama it is
  the smaller of the model's own window and the `num_ctx` Dot sends with every request (the Models table of the System
  page shows it): `[providers.ollama] num_ctx` (65 536) for a model that runs on this Mac, where it sets the memory of
  the model's cache, and `num_ctx_cloud` (262 144) for a cloud model, whose tag is `cloud` or ends in `-cloud`
  (`kimi-k3:cloud`, `gpt-oss:120b-cloud`) and which runs on Ollama's servers; where the model's own window is larger,
  the table shows it beside the one Dot asks for ("of 262k (own 1.0M)", items 221 and 250). OpenAI's and OpenCode
  Go's windows come from Dot's tables of their models, kept to their published pages; Anthropic's from its list of
  models. When three quarters of the window is less than `context_tokens`, the run's budget
  is that, and `tool_output_chars`, the most one tool result may carry, shrinks in the same proportion (never below 2000
  characters). The text of a page or an application window in a browser or desktop result takes what is left next to the
  rest of the result, and never less than half the result, so a small window shortens a page instead of leaving it out.
  A model whose window cannot hold the system prompt and the tool definitions is refused at the start, with
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
  step. `grouped` starts with the core tools (files, the shell, the web, searching and saving notes, delegation, skills,
  the plan and `finish`) and the groups the thread already used. The other groups (`browser`, `desktop`, `schedule`,
  `memory_admin`, and `mcp__<server>` for each MCP server) are listed in the system prompt with what they are for and
  their tools, at most 16 of a group: in a bigger group, those that share the most words with the run's goal first. The
  model turns a group on with `use_tools`. A call to a tool whose group is off does not run: it turns the group on, and
  its result asks the model to call the tool again. A group stays on for the rest of the run. `auto` is `grouped` for a
  model of the weak tier and for a model whose window cut the context budget, and `full` for every other model. The
  timeline shows the groups a grouped run starts with and each group it turns on.
- `verify_answer_paths` in `[agent]` checks the paths a final answer names: `~` paths and absolute paths under
  `/Users`, `/tmp`, `/private`, `/var`, `/opt`, `/Applications` and `/Library`. When a task or research run ends with
  success and its answer names such paths that do not exist and that no successful call of the run named (a file the
  run removed or moved is not missing), the run is sent back once with the list: to create what is missing, to correct
  the answer, or to finish again with the same answer if it names them on purpose. The check makes no model call. It
  is off by default until evals show that it helps. The timeline shows each send-back.
- A run has no step limit. It ends when the model calls `finish`, when the progress watchdog stops it (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)), when a model call still fails after its retries and
  the wait for a model out of reach, at a limit of minutes or tokens you set, or when you stop it. In goal mode the goal
  check can also end it (see [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A goal that names an outcome instead of a task (grow, earn, reach, as many as possible, find a way) is held to the
  outcome in task and research runs: the model is told to record measurable targets in its plan before it acts, to
  measure after each round and change its approach when a round brought nothing, and to call `finish` only once the
  targets are met, when the next step needs something only you can give, or at the run's limit, then with `success`
  false, the numbers reached and what is missing. A precise request is still done exactly as asked. A chat is not told
  this: its turn ends with its reply.
- A reply without a tool call ends the turn in `chat` mode and when the run has not used a tool yet. Otherwise it is
  nudged towards `finish`; after `finish_nudges` nudges in a row the reply itself becomes the result. A step with tool
  calls starts the count again, so a status line written between tool steps does not end the run.
- A reply the model service cut short (at its output limit, or for an abnormal reason such as a content filter) is
  never taken as the answer, in any mode: the model is told how the reply ended and asked to go on with its tools or
  give the complete answer again in fewer words. Each such reply counts as a nudge; after `finish_nudges` of them in
  a row the run fails with "The model's replies were cut short" and the reason the last one ended with.
- `finish` can say what the run could not do without you (`needs_from_user`): the run's page shows it under the answer
  as "What Dot needs from you", and `dotx run` prints it after the answer.
- A small model with the full tool list sometimes writes the `finish` call out as a plain message (`gpt-oss:20b-cloud`
  did in most trial runs). Dot does not reinterpret such text: it is nudged first when tools were used, otherwise it
  becomes the result as written. Use a larger model as `main` for task and research runs.
- At most `max_concurrent_runs` runs go at once; the others wait. Delegated runs do not count.
- Runs, their events and their conversations are stored in a SQLite database (WAL mode; the daemon writes through one
  connection, one writer at a time, and waits out a lock held by another process with logged retries before it gives an
  error; a token count that cannot be written is logged, not fatal to the run), so the web UI can replay them. The end
  of a run that started is one write: its status, the scores of the notes it used and the event that tells of its end
  are stored together or not at all, and a delegated run whose end could not be stored is an error for the run that
  delegated it. A run that is stopped stops the runs it delegated, and its end is stored after theirs, so that its page
  shows each of them ended; the page of a run stored the other way, before item 197, reads how they ended from the
  database. An end the database refused (a full disk, say) is tried again every half minute until it is stored, with the
  event that tells of it and what follows it (the chat's next message, a campaign's next session); until then the run
  stays as the database has it, and a Stop says its end is on its way. A run that could not be started is stored as
  failed with that event too, so a page that showed it queued sees it end. A run that a crashed daemon left behind is
  marked as failed the next time Dot starts, as ended at its last event (the runs it delegated before it), and so is one
  that a `dotx` command left when it died, by the daemon within half a minute when it runs. Either can be resumed, and
  so can a run that a shutdown of the daemon cut off (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A message sent to a running run (steering) reaches the model at its next step. It also clears the run's untrusted
  mark (see [Safety](#safety)).
- A run belongs to the daemon, not to the page that started it: leave the chat and it goes on, its status stays live in
  the sidebar, and you can steer it when you come back.
- A run of a `dotx` command (`dotx run`, `dotx resume`, `dotx chat`) is on the pages too while the daemon runs: it works
  in the sidebar and in Activity, and its page follows it live, as it follows the daemon's own runs, though its text
  comes whole at each step rather than as it is written. Its Stop asks the command to stop it, which it does within a
  second. A question or an approval of it can be answered on the pages, which ends the prompt at its terminal; the first
  answer stands, wherever it was given. It takes no steering from the pages: a message sent there waits in the chat's
  queue. A chat has one run at work at a time, whichever process drives it: a second is refused (409), and the run a
  `dotx` command left at work when it died is closed first.
- A message sent to a chat while its run works waits in the chat's queue, after T3 Code's: it starts as the chat's next
  run, with the mode, model, reasoning, approvals and goal mode it was sent with, once the run before it has ended done,
  one message after the other. A stop, a failed run or a crash of the daemon holds the queue until you resume it; a
  shutdown of the daemon does not, and once the run it cut off is resumed and the new run ends done, the queue goes on.
  The daemon keeps the queue, so it goes on with no page open, and a queued message can be sent to the run at work
  (steered) instead. A message starts once, and only after the run at work on the chat has ended, in whichever process
  that run worked: the daemon moves the queue of a chat whose run a `dotx` command drove once it sees that run end, and
  holds it when it starts after such an end. While a message's run is starting, editing it, taking it out or steering it
  is refused (409). A campaign's chat has no queue: a message there steers its session.
- `/btw <question>` in the box of a run (the slash menu offers it) asks on the side: the run's model answers from what
  the run has done so far,
  without tools, and the run neither sees the question nor stops for it. Several can be asked at once. The question and
  the answer are `aside_question` and `aside_answer` events, never messages of the conversation.
- Files can go with a goal or a steering message (attach, drop or paste them in the web UI; at most 10 of 20 MB each). A
  text pasted into the box that is long (2,000 characters or 40 lines) goes as a file of its own (item 269),
  `pasted-text-<time>.md` when it reads as Markdown and `pasted-text-<time>.txt` otherwise, which the model reads as it
  reads any attached file, as material rather than instructions; the pencil on its chip puts it back into the box as
  text. A message is still the owner's words: a send with files and an empty box asks for a few words and sends
  nothing, and on a run at work it stays a send while a file is listed, never the Stop an empty box makes it. Text, code, PDF, Word, Excel and PowerPoint files are put into the message as text (a long one shortened, with
  its path to read the rest), images go to the model as images or, for a model that cannot see, as the `vision` model's
  description. A Word document (`.docx`) reads as its paragraphs and tables in order, headings and list items marked as
  Markdown marks them; a workbook (`.xlsx`, `.xlsm`) sheet by sheet, each cell after its address and a formula with the
  value Excel last worked out for it (up to 100,000 cells); a presentation (`.pptx`) slide by slide, with its tables,
  its charts' series and its notes; `read_file` reads such a file on disk the same way. A file of before 2007 (`.doc`,
  `.xls`, `.ppt`) or one locked with a password is refused, saying what to save instead. Anything else is refused at
  upload. The files are kept in `workspace/attachments/`, where the agent's tools can open them; one named like a file
  Dot keeps next to it (`meta.json`, `text.txt`) gets an underscore before its name.
- A file opens beside the conversation (item 195). A sent file's chip, a file in the box once uploaded (item 269), a
  file's path an answer names in code (under `~/` or `/Users/<you>/`; Dot names the files it made by their full paths),
  a file in the side panel's **Files** (on its Actions tab: the conversation's files, those its messages carried and
  those its answers name) and a file the run changed, in **Actions**, open it in the side panel, which takes half of the
  page while it has its own width, and the whole screen on a phone. A PDF shows its pages as pictures the daemon draws
  with Core Graphics as they come into view; a workbook, and a CSV or TSV table (split at its commas, or at its
  semicolons where a comma marks decimals), a grid with a tab for each sheet, a click on a cell saying what it holds
  (its formula and the value last worked out); a Word document its headings, paragraphs, lists and tables; a
  presentation its slides with their text, tables, charts' series and notes; an image itself; a Markdown file (`.md`,
  `.markdown`) formatted as an answer is, with its headings, lists, tables and code, and as it is written a click away
  (item 269), its HTML never drawn; any other text as text, never as a page. On a page without the panel (the new
  chat's, an agent's) the file opens in a dialog. Pictures, shapes and formatting in an Office file are not drawn:
  **Open on the Mac** opens a document in its application (`open`), never a script or a program. Close or Escape gives
  the panel back as it was. The panel shows files of the workspace and of the home folder outside hidden folders and
  outside `~/Library`, but for the folders cloud drives sync (OneDrive's and the others' in `~/Library/CloudStorage`,
  iCloud Drive's in `~/Library/Mobile Documents`), judged once links are followed; for another file it says why not. The
  routes are `GET /api/files/preview?path=`, `GET /api/attachments/{id}/preview`, `GET /api/files/page` (a PDF's page as
  a PNG), `GET /api/files/image` and `POST /api/files/open`.

## Tools

| Area            | Tools                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------ |
| Shell and files | `shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `search_files`, `write_notes`           |
| Terminal        | `terminal_open`, `terminal_send`, `terminal_read`, `terminal_close`                                                |
| Web             | `web_search`, `web_fetch`, `web_crawl`, `web_extract`, `judge_items`                                               |
| Browser         | `browser_open`, `browser_snapshot`, `browser_find`, `browser_links`, `browser_read_text`, `browser_wait_text`, `browser_act`, `browser_eval`, `browser_scroll`, `browser_screenshot`, `browser_upload`, `browser_back`, `browser_new_tab`, `browser_switch_tab`, `browser_close_tab` |
| Desktop         | `desktop_apps`, `desktop_open_app`, `desktop_snapshot`, `desktop_read`, `desktop_act`, `desktop_screenshot`, `desktop_clipboard_read`, `desktop_clipboard_write` |
| macOS           | `apple_script`, `notify`, `code_fetch`                                                                             |
| Memory, time    | `memory_save`, `memory_search`, `memory_forget`, `schedule_create`, `schedule_list`, `schedule_delete`             |
| Orchestration   | `delegate`, `consult`, `update_plan`, `ask_user`, `finish`, `use_tools`                                            |
| Pages           | `show_page`                                                                                                        |
| Skills          | `skill`, `find_help`                                                                                               |
| MCP             | `mcp__<server>__<tool>`                                                                                            |

The shell and the file tools work in `<home>/workspace` unless a path says otherwise. Any error of a tool fails that
call, not the run: the model reads what went wrong (for an error no tool expects, its kind and its words, while the log
keeps its traceback) and goes on. `edit_file` changes the text it is given and nothing else: a file that breaks its
lines with CRLF keeps them, in the new text too, and a file that is not UTF-8 text is refused, saying so, for the shell
to change. `read_file` reads a long file in pages of lines, the text of a PDF too, each page ending with the line to go
on from, so no part of it is out of reach. A shell command runs with the `PATH` of your login shell, in its own process
group (cancelling a run ends the whole group) and under a timeout (`[shell] default_timeout_s`, at most
`max_timeout_s`). An AppleScript (`apple_script`) ends with its call as well: one whose run is stopped or whose call
times out is killed. `web_search` climbs a ladder of engines until one answers (`[web] search_backend`, default `auto`;
`dotx doctor` shows the ladder): the hosted search of Ollama when `OLLAMA_API_KEY` is set (no bot check), then the
Anthropic API's own web search when `ANTHROPIC_API_KEY` is set (relayed by `[models] search`, Haiku 5.5 by default; the
API bills $10 per 1,000 searches next to the tokens, a failed search is not billed; `[web] anthropic_search = "first"`
puts it before Ollama, `"off"` leaves it out), then the HTML pages of DuckDuckGo and Brave Search (no key), then
DuckDuckGo opened in Dot's own Chrome when the run's browser is Dot's own with a visible window (its bot check runs
there and passes; a headless Chrome is shown a CAPTCHA, measured 2026-10-11). An engine that limits this machine is
benched (DuckDuckGo 10 minutes, Brave 90 seconds, the Anthropic rung 2 minutes) and the searches of that time skip it at
once; the same query within 10 minutes is answered from memory. The first line of a result names the engine that
answered, every rung skipped with its reason, and the age of a remembered answer. Bing is not a rung: without a session
its page answers some queries with the results of another query. `web_fetch` reads HTML and PDF and refuses localhost,
LAN and cloud-metadata addresses unless `[web] allow_private_hosts` is set. A page that turns the plain request away
(HTTP 401, 403, 429, 503 or 999, LinkedIn's refusal) or shows little text without JavaScript is read in the run's
Chrome, in a tab the owner does not work in that closes after the read: a scratch tab of Dot's own Chrome, or a tab
opened behind the owner's in the owner's attached Chrome (item 98); the first line of the result says so. A host that
refused a plain request once is remembered for the run, and the next fetch of that host goes to the browser first. The
plain request itself sends the headers a browser's address bar sends (`Sec-Fetch-*`, `Upgrade-Insecure-Requests`) with
the same user agent. A page that refuses the browser too says that, with both answers, and names the next rung in one
line: another source, `web_search` with the address as the query for the hosted search's snippet, or `browser_open` and
Take over for a login wall. A page that is not there (HTTP 404 or 410) is reported with the links of the host's front
page that share a word with the address asked for (a dozen at most), so the next turn opens the right page instead of
guessing another address; the system prompt says the same (find a page with `web_search` or `web_crawl`, never by trying
`/careers/` on a hunch). `web_search` returns up to 10 results; a larger `max_results` is lowered to 10 and the result
says so.

**Crawling and extraction.** `web_crawl(url, scope, max_pages, max_depth)` reads many pages of one site in one call:
breadth first from the start address, following the links within the scope (`prefix`, the pages under the start
address's folder, or `host`, every page of its host), several fetches at once and the requests to one host spaced apart,
within the limits of `[web.crawl]` (`max_pages` 50, `max_depth` 3, `concurrency` 4, `per_host_spacing_s` 0.5; a call may
ask for less, never for more). Every page comes back as one clean text document (title, address, description, then the
text as `web_fetch` renders it, at most `page_max_chars`), each in its own `<web_content>` fence, under an index that
lists the pages with their depth, size and how they were read, the addresses left out and why, and how many more
addresses were found within the scope but not read. A page that shows little text without JavaScript (fewer than 400
characters and three or more script tags) is rendered once in Dot's own browser tab and read from the screen
(`render = "auto"`; `"never"` fetches only, `"always"` renders every page); PDFs are read as `web_fetch` reads them. A
page that turns the plain request away is read the way `web_fetch` reads one, in a tab the owner does not work in, and
the index says so (`rendered; the plain request got HTTP 403, read in the browser`); its links are not followed. A page
that refuses the browser too is left out with both answers. `robots.txt` is read once per host: with `robots = "report"`
the addresses it disallows are named in the index but fetched, with `"obey"` they are left out. The crawl goes through
the same client as `web_fetch`, so the address rules and the browser's host lists hold for every page. When the pages do
not fit one result, each becomes a Markdown file under `<workspace>/crawl/<host>-<time>/` with an `index.md`, and the
result says so: the run reads the ones it needs with `read_file` or hands them to `web_extract`.

**Notes of a run.** `write_notes(content, append)` keeps the notes of a run in one Markdown file,
`<home>/workspace/notes/<run id>.md`: a long research run writes what it found there as it goes, with sources, instead
of holding it all in its context, and names the file in its answer. The file is in the undo journal like any file write.
It is the only file a `research` run may write.

**Pages in the conversation.** `show_page(title, html)` shows you a page the run writes in HTML, in the conversation: a
chart, a table, a diagram, a 3D view, a mockup. It sits under its title in a frame as wide as the conversation and as
tall as the page (80 to 2,000 pixels), and Open shows it full size in your browser. It is drawn in the colours of the
theme on screen, given to it as CSS variables (`--background`, `--foreground`, `--accent`, `--chart-1` to `--chart-6`
and others), and takes the new ones when the theme or the system's scheme changes. Its scripts run sandboxed, framed or
full size: its origin is opaque, so they read nothing of the web UI and reach neither the API nor the page around them.
They fetch nothing but libraries from cdn.jsdelivr.net, unpkg.com, cdnjs.cloudflare.com and esm.sh, submit no form and
ask for no camera, microphone or position. A link you click in the page opens in your browser; a script of the page
cannot open one by itself, nor with a click you made elsewhere. A page that loads another page in its frame is closed,
with Show it again. A page of up to 500,000 characters is kept in the database under an id that cannot be guessed, which
is all it takes to read it (`GET /pages/<id>`, no token, never cached); it goes with its chat, and with the events of
its run under `dotx prune`. `dotx run` prints the page's address. The tool is in a group of its own, `pages`, and the
system prompt asks for a page when a chart, a table, a diagram or a 3D view would say more than words.

`web_extract(sources, fields)` pulls named fields out of pages or files as JSON without the pages entering the main
model's context: for every source (an address, or a file: a path, or the name of a file in the workspace such as the
Markdown files a crawl wrote, read as a file even when the name looks like an address, `notes.md`) the fast model
(`[models] fast`) is asked once for every field's value and the exact words of the source it comes from, and each quote
is checked against the source, so the result says per field whether the value is `verified`; a value whose quote the
source does not hold is kept and marked `verified: false`, a claim to treat as one, and so is a value the fast model
gave without a quote. A file's text goes to the fast model, so a file in a protected location is refused, as `read_file`
refuses it. A source that cannot be read or extracted is listed under "Not extracted" with the reason; while the fast
model is paused after a quota, rate-limit or key error, no source is read or sent to it, and each is listed with the
pause (item 240). At most 20 sources and 20 fields per call.

A terminal session is for a program that does not run to its end by itself: one that asks questions (an installer,
`npm create`, `ssh`), a REPL (`python3 -i`, `psql`), or a server that must stay up while the run goes on.
`terminal_open` starts it on a pseudo-terminal of its own, with the environment of the shell tool, and returns its
first output; `terminal_send` types into it and returns the answer, `terminal_read` waits for more (a server's log),
`terminal_close` ends it and everything it started. The output is read as plain text, without a terminal emulator, so
full-screen programs such as vim or htop show as garbled text. The command and every line typed into it are judged as
a `shell` command is, a line typed in parts as a whole. A key a line editor acts upon (Tab, an arrow, a Ctrl- shortcut
other than Ctrl-C, Ctrl-U) leaves a line Dot cannot tell, and running it asks; once a line has run, the program may be
in another directory, so later lines are judged from the home folder. A chat keeps at most `[shell] terminal_sessions`
sessions open (3; 0 removes the tools), each closed after `terminal_idle_s` without use (30 minutes) and all of them
when the daemon stops; the end of a run closes none, so a server it started stays up for the next message.

A command that runs long and ends by itself, as an eval or a long build, past the 30 minutes that `shell` may wait
(`[shell] max_timeout_s`), goes to a terminal session in the background (`terminal_open` with `background`, item 229,
after T3 Code's background work): it is not closed while it runs for being left unread, and the run that started it can
end. When it ends by itself, its chat is told in a message of its queue, which starts the chat's next run with the
chat's choices once no run works there (a held queue keeps it). The message says which command ended, with its exit
status and how long it ran, and nothing it printed, which the run reads with `terminal_read`, as untrusted; Dot wrote
it, so it opens that run as a note and is no instruction of the owner. Right above the composer, the chat's page shows
each of its commands at work, with the time it has run and a Stop; the dot of a command in the background pulses, as its
end will wake Dot. A command stopped there, closed by the run or ended by the daemon's stop tells nothing. A delegated
run or a campaign's session opens none, having no chat of its own to tell, and neither does a `dotx` command, which ends
with its run: a command in the background lives as long as the daemon.

## What leaves this Mac

Dot sends nothing to the people who make it: there is no telemetry, no crash report and no check for updates. What
leaves the Mac is what a run needs, sent where the configuration points:

- **To the models: the run's conversation.** Each call sends the goal, the messages, what the tools returned (the text
  of the files and pages the run read, what commands printed) and the screenshots the run takes or is given. By default
  every role (`main`, `vision`, `fast`, `review`) is a cloud model of Ollama: the Ollama server on this Mac passes each
  call on to Ollama's servers once it is signed in. Dot's own work on what its runs did goes there too: lessons,
  playbooks and the nightly reflection to `review`, the shortening of a long run's conversation to `fast`. OpenAI,
  OpenCode Go and Anthropic get it only when a role, a helper or a standby model names one of their models (see
  [Models and providers](#models-and-providers)).
- **To the embedding model: each note and each search of the memory.** It is Ollama's `bge-m3` on this Mac by default;
  with `[memory] embed_provider = "openai"` the texts go to OpenAI (see [Memory](#memory)).
- **To the search engines: the query.** The `[web] search_backend` ladder asks Ollama's hosted search when
  `OLLAMA_API_KEY` is saved, then DuckDuckGo and Brave Search, then DuckDuckGo in Dot's own Chrome; Anthropic's search
  (relayed by `[models] search`) joins when `ANTHROPIC_API_KEY` is saved, and `[web] anthropic_search = "off"` leaves it
  out.
- **To the sites a run visits: its requests.** The web tools fetch public addresses only, unless
  `[web] allow_private_hosts` allows more; Dot's browser carries the cookies of its profile (Dot's own, or your Chrome's
  when it is attached). `web_extract` sends what it read to the `fast` model, and a page a run shows in the conversation
  loads its libraries from public CDNs (see [Tools](#tools)).
- **From the shell: whatever a command sends.** The sandbox closes Dot's own files, not the network; a run that has read
  untrusted content asks before a line that sends data out (see [Safety](#safety)).
- **To TypeSafe, once its key is saved: the texts it judges.** `judge_items` and the tools of posts and campaigns send
  the texts they judge; with `[judge] provider = "typesafe"` the judge of clicks and checks sends what it judges (a
  button's words, the page's address without its query, its title and visible text). `[judge] provider` is `"none"` by
  default.
- **To a phone, once you turn its notifications on: a line.** A title, up to 140 characters of the question or the
  result and the page to open, encrypted for that phone's browser and carried by its maker's push service (see
  [Dot on the phone](#dot-on-the-phone)).
- **To MCP servers and plugins: what their tools are given.** A server's own process reaches what it reaches; none is
  set up by default (see [MCP servers](#mcp-servers)).

The record of the runs, the notes, the logs and the screenshots stay on this Mac in Dot's folders (see
[Configuration](#configuration)), and a key goes only to its own service. To keep a run's conversation here too, give
every role a model of the Ollama server on this Mac (one whose name has no `cloud`), leave the helpers and standby
models empty or local, and keep the embeddings on Ollama; the searches and pages a run asks for still go to the sites it
reaches (item 216).

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
The payments of a site are decided one at a time, each from what the ones before it spent, so two runs paying at once
cannot both take what is left of the day. Over a cap, on a site not listed, or when the page does not show the amount,
the money hand-off holds as before. The caps are plain numbers compared with the number on the page, whatever its
currency, so list sites that bill in one currency. Empty by default.

**Decision order**, strictest first:

1. *Auto-review* denies actions that read secrets or send data out (`secret_access`, `exfiltration`), whatever else is
   configured.
2. The *run mode* denies what a research run may not do.
3. *Hard stops* hand money and credentials to you (`financial`, `credential`): Dot stops and asks you to do that step
   yourself. A payment under the pre-approved caps of its site (below) is a question instead of a hand-off.
4. *Your rules* come next. The first enabled rule, in order, whose tool pattern and regular expression match the action
   decides, with one of four answers: *do it* (`allow`), *do it, whatever it has read* (`preapproved`: the question
   untrusted content raises is skipped too), *ask me first* (`ask`) or *hand it to me* (`handoff`: Dot stops, says what
   it needs done and waits; on a browser step you can take the browser over, do it and give it back; when you say you
   did it, the run goes on from there). The expression is searched, case-insensitive, in the action's one-line summary
   (a shell command's starts with `Run: `), a line break and its target (the command, the path, the address). A rule
   reads as a sentence in Settings › Rules, "When Dot would use `shell`, matching `\bgit push\b`: ask me first", and
   is managed there or through `/api/rules`. It can also be said in your own words ("let Dot run git status and git
   diff without asking me"): the review model drafts its parts, the tool, the answer and the commands or words the
   action must mention, and Dot writes the expression from them, so that a rule that lets Dot run a command fits it
   run alone with plain arguments (after a `cd` at most; nothing chained to it, substituted into it or sent on with
   `|` or `>`), and a rule that stops Dot fits the command or the word wherever the action mentions it, as a whole
   word (`x.com` does not fit `netflix.com`). The draft fills the form, its description the sentence the rule reads
   back as ("For “git status” run alone in the shell, …: Dot does it without asking."), and applies once you add it.
   Words that cover every action ("Dot can run everything without asking me") draft a rule for every tool ("For every
   use of any tool: Dot does it without asking."), which the protections that are always on still bound; words that
   make no rule are answered with the reason, in English.
   An approval card offers *Make a rule of this*, which opens the tab with a sentence about the action in the box.
5. *The preset* decides everything that is left:

| Preset                 | Asks before                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `aggressive` (default) | privilege, system settings, public posts, messages sent for you, and destructive actions outside the workspace              |
| `balanced`             | all of that, plus anything destructive or installing, anything of high risk, and writing or shell commands outside the workspace |
| `cautious`             | everything that is not read-only                                                                                            |

A shell line is destructive outside the workspace when it removes or cuts a file there, and when it replaces one that is
there: a redirect that empties it (`>`, `>|`, `&>`), a copy or a move onto it or into a folder that holds a file of its
name (not `cp -n`), `ln -f`, `tee` without `-a`, `dd` without `conv=notrunc`. The shell keeps no copy of what it
replaces, unlike the file tools, whose changes `dotx undo` writes back.

Who answers what the policy asks depends on the approval mode of the run: you, a review model or nobody (see
[Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). The first three steps above hold in every mode.

**Untrusted content.** Web pages, screens and MCP results can be written by third parties. Dot wraps them in a fence
with a random marker per call (`<web_content …>`, `<screen_content …>`) and tells the model that what is inside is data,
never instructions. What a shell command printed after it reached the network (`curl`, `ssh`, `git clone`, `gh`, a line
the guard cannot read), what a terminal program prints, and a find_help answer that lists skills or MCP servers that
other agents of the Mac set up count the same way, unfenced. A run that has seen such content is marked untrusted, and
from then on outward-facing or lasting actions (messages, posts, follows and likes, privilege, system settings, saving a
schedule or a note, and a shell line that sends data out: an upload or a form, a push, a copy to another machine, a
change on a code host, inline code that reaches the network or runs a command of it) ask even when the preset or an
`allow` rule would let them through; only a `preapproved` rule skips the question. A message you send to the run clears
the mark. A delegated run starts as untrusted as its parent, a resumed run as the run it resumes was when it was cut off,
and the run that tells a chat that its command in the background ended as the chat's last run ended: that message is
Dot's, not yours. A message you send starts a run unmarked.

**Secrets.**

- In `ask` and `auto` the browser and desktop tools never type into password, one-time-code and card fields: the field
  that would take the text is checked, whatever element the step names (a desktop `menu` step that pastes is not). A
  `yolo` run types into them what it can read itself (see
  [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). In the same way `terminal_send` types nothing outside
  `yolo` while the program's last line asks for a password, a passphrase or a PIN. The desktop tools never control a
  password manager or Keychain Access, in any mode, nor a window of Dot's outside `yolo`: the desktop app's, or one of
  another application (a browser, an installed web app) that shows the web UI, known at each step by the title the UI
  gives its page (its approvals, rules and settings decide what a run may do; a page of another site that takes such a
  title is refused as well). Desktop screenshots black out the password managers' windows; secure fields and web pages
  are not masked.
- Environment variables whose names look like secrets (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL` or
  `AUTH` in the name) are removed from the processes the shell tool starts, `SSH_AUTH_SOCK` among them, so commands do
  not reach the keys of your SSH agent (`[shell] pass_env` lists exceptions).
- Outside a `yolo` run the agent can neither read nor change `~/.ssh`, `~/.gnupg`, `~/Library/Keychains`, `~/.aws`,
  `~/.config/gcloud`, the data of your browsers (Chrome, Safari, Firefox, Brave, Edge and Arc) and their cookies, the
  tokens and passwords that command lines keep (`~/.netrc`, `~/.git-credentials`, `~/.config/gh`, `~/.npmrc`,
  `~/.pypirc`, the credentials of Cargo and RubyGems, `~/.docker/config.json`, `~/.kube`, `~/.azure`, `~/.config/op`,
  `~/.password-store`), the shell's history, Messages and Mail (`[policy] protected_paths`; a list of your own replaces
  this one), nor Dot's own key file, token, browser profile, configuration, database and journals of file changes. The
  configuration holds the policy and the MCP servers, the database the rules, the journals what `dotx undo` writes back.
  In any mode, a tool that names files (the file tools, `web_extract`, `browser_upload`) looks at them again as it
  starts: a call whose path leads elsewhere by then (a link moved while the call waited for its approval) is refused,
  and called again it is decided for where the path leads now.
- Outside a `yolo` run the agent reads what Dot loads and runs, and runs a skill's files, but never changes them: its
  own code (the folder its package is imported from, the Python environment it runs in and the Python under that, and
  the file of the launchd job that starts the daemon), its plugins and their own files, its skills (with the
  `[skills] dirs` and `[plugins] dirs` folders, and the copies of the skills you added from other agents), Dot's record
  of the MCP servers you added from other agents, the playbooks it learned and the file of its emergency stop. The file
  tools refuse to write there, and so does the shell guard for a line that writes there by a copy or a move (its target
  folder option too), `dd`, a redirect, a download, a clone, an unpacked archive, git run in one of those folders, a
  file edited in place (`sed -i`, `perl -i`, `ruby -i`) or inline code that names the place (in a word of switches such
  as `-pe` too). The guard reads the line, not what a program does; the sandbox below holds what it cannot read. A
  plugin's MCP servers, and those you added, start with the daemon and are given keys from the key store: put a plugin
  or a skill in place yourself.
- Outside a `yolo` run the processes of a run start in a macOS sandbox (`sandbox-exec`; `[shell] sandbox`, on by
  default): a shell command, a terminal session, an AppleScript and what it runs can neither read nor change Dot's own
  files above (its key file, token, push key, browser profile, configuration, database and journals, and the data
  directory of your Chrome), nor change what Dot loads and runs, nor rename a folder above any of them, whatever program
  does it: a script the run wrote, code that builds the path, a link, another letter case. macOS answers
  `Operation not permitted`. The search tools may not read your protected locations either; a command still may (ssh and
  git read their keys there), and for those the guard reads its line. A search that meets a place it may not read shows
  what it found elsewhere and names the places it did not read, as a search of any run does with a folder whose reading
  macOS broke off (it does now and then under `~/Library/Containers` when the home folder is searched). macOS starts no
  program that runs as root inside the sandbox (`ps`, `top`, `sudo`, `crontab`): `pgrep -lf` and `lsof` work, a `yolo`
  run runs them, `[shell] sandbox = false` turns the sandbox off. Settings › Rules switches it while the daemon runs
  ("Sandbox for a run's commands", `PUT /api/shell/sandbox`): from the next command on, with no restart, and the config
  file keeps it; a terminal session already open keeps how it started. Switching it on where `sandbox-exec` does not
  start is refused, and nothing changes.
- What Dot runs at its start is kept from a run's commands too: the login shell whose environment it reads runs its
  startup files, which a command may change, in the same sandbox, and the daemon's launchd job starts Python with `-P`,
  so a file a command left in Dot's home, the job's working directory, is never imported (run `dotx service install`
  again after an update to rewrite the job).
- What the sandbox does not cover: a program a command has act for it (Terminal through `open`, a launchd job, an
  application driven by Apple Events) acts outside it, and so do the programs Dot starts itself (its browser, the MCP
  servers), from where they are installed: a command can change the programs installed under your account (Homebrew's,
  the caches of npm and uv) and your shell's startup files, which those programs and your own Terminal then run. And it
  keeps files: a key saved with `dotx auth set` or in Settings is in the key file, which it seals, while a key kept in
  the keychain (service `dot-agent`) can be read by a command through `security`, in the sandbox too.

**Typed judgments (optional, off by default).** The policy reads what a click does from the control's label, with
patterns in English and Turkish, and a few in Finnish ("Post", "Send", "Delete", "Pay now", "Paylaş", "Lähetä"…); a
label they do not know (Mastodon's "Boost", the German "Teilen") passes as an ordinary click. With `[judge] provider =
"typesafe"` (the switch "Typed judge (Jev)" on the row of the key in Settings › API keys writes it and switches the
judge from the next judgment on, with no restart; item 255) and `TYPESAFE_API_KEY` saved (made at console.typesafe.ai;
Settings › API keys, or `dotx auth set TYPESAFE_API_KEY`), TypeSafe's System One API is asked about such a click in four
yes/no questions (does it publish,
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
approval mode, `yolo` too, with the time the next one is allowed, and the timeline shows the refusal. The actions of
two runs on one site are judged one after the other, so the second counts the first. While a limit of public or
social actions is set, a `browser_act` call takes one public or social click at most, so that each one counts; with
none set, as by default, a call takes as many as its steps (item 266). Only the clicks the policy recognises are
counted: a post sent with a keyboard shortcut, by a script in the page (`browser_eval`) or through an API from the
shell is not. X publishes limits of its own (an
unverified account: 50 posts and 200 replies a day, 400 follows, 500 messages), and its automation rules forbid
automated likes and bulk or aggressive following, and warn that scripting its website can get an account suspended.

**Approvals.** An action that needs a yes becomes an approval: `allow? [y/N]` in the terminal, a card in the web UI. The
run waits (its status is `blocked`) for up to `[policy] approval_timeout_s`; an approval nobody answers expires and
counts as a refusal. The note you write with your answer reaches the model. This is the default approval mode, `ask`; in
`auto` and `yolo` the questions of the policy are answered for you. A question of the agent itself (`ask_user`) waits
the same way, except in a run nobody can answer during it (see
[Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Its options are rows on the card, each with what it
leads to, and the one Dot recommends is marked: a row sends its option, or, when several may be chosen, the rows ticked
are sent with what you write, as one answer. In the terminal you type an option's number (several, separated by commas)
or your own words. The run reads which option you chose, and whether it was the one it recommended.

**Emergency stop.** Any of these stops Dot from controlling the desktop:

- the **Emergency stop** button of the web UI (the red square in the sidebar's foot beside Settings, a tap away behind
  the sidebar's opener while it is closed; or `POST /api/failsafe`) creates the `STOP` file in the home
  directory and ends every run as an emergency stop, a run that was starting at that moment too (a campaign whose
  session it ends stops too); the page shows a banner with a button that releases it
- `touch ~/Library/Application\ Support/Dot/STOP` blocks desktop control the same way, without ending a run; delete the
  file to allow it again
- while the `STOP` file exists, no schedule fires (a firing due then is skipped to its next time, saying why; a
  one-time schedule, which has no next time, waits and fires once the stop is released) and no campaign starts a
  session (it starts once the stop is released); a run that a schedule or a campaign was starting when the file
  appeared ends as an emergency stop as soon as it exists
- with `[desktop] failsafe_corner = true` (the default), moving the pointer into the top-left corner of the screen
  aborts the desktop step in progress, and Dot never clicks that corner
- before each step of a desktop batch Dot checks that the pointer is where the previous step left it; if someone else is
  moving the mouse, the rest of the batch is not run

> **The shell guardrail is best-effort; the sandbox holds only Dot's own files.** Dot reads a shell command before it
> runs it and judges what it would touch: it reads the line as the shell does (quotes, comments, here-documents, the
> commands substituted into it, a `cd`, a variable, a link it makes, a wrapper such as `bash -c`, `env` or `xargs`),
> compares paths without case, and treats what it cannot read (a command named by a substitution, `eval` of one, a line
> it cannot parse) as a destructive step outside the workspace, which every preset asks about. It does not follow what a
> recursive command does inside the folders it walks, nor the code of a script it runs; the sandbox keeps those out of
> Dot's own files, not out of yours, and a shell can do more than any analysis can foresee. Treat Dot as a user of your
> account with the rights of your account: keep backups, keep the workspace for scratch work, and use `balanced` or
> `cautious` on a machine where a mistake would be expensive.

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

- `ask` (the default): you do. The run waits for your answer, on a card that shows what the action acts on whole (a
  long command scrolls within it).
- `auto`: the review model (`[models] review`) does. It is given your instructions (the goal and every message you sent
  to the run: the only authority it recognises), the action (up to 12,000 characters of what it acts on, and of a longer
  target its start and its end), why the policy stopped at it and whether the run has read untrusted content. It answers
  `approve` when the action is plainly what you asked for; `deny` when it is not, goes further than you asked or looks
  like something other than your instructions caused it; and `escalate` when it cannot tell, and then the question comes
  to you as in `ask`, with the reason of the review model in it. What the agent wrote into the action (its summary and
  target) is data to the review model, never an instruction.
- `yolo`: nobody does. The action is allowed and recorded. The question that untrusted content raises (see
[Safety](#safety)) is waived as well, and so are the refusals and hand-offs below: a yolo run reads secrets, uploads or
sends files from anywhere, and does money and credential steps itself. It types passwords, one-time codes and card
numbers into the fields that take them when it can read them itself (a code in a mailbox it can open, one that
`code_fetch` reads, a value you gave it), and a password into a terminal program that asks for one; every such field is
a `credential` record in its timeline (`financial` for a card field). That is the price of the mode: a run that has read
a hostile page can send your files out, pay or sign in without asking you. Only the desktop tools stay away from
password managers and Keychain Access in every mode; through the shell and the file tools a yolo run reads the keychain
like any other secret.

Every automatic answer is an ordinary approval record (`decided_by` is `reviewer` or `yolo`, the summary is the action
and the note holds the reason), an `approval_auto` event and one line in the timeline of the run ("YOLO, not asked:
click 'Reply' — untrusted content is in context and this action reaches outside the Mac"), so you can see afterwards
what went through and why.

**What only yolo waives.** Secrets and exfiltration stay denied in `ask` and `auto`. Money and credentials stay yours
there: Dot stops and asks you to do that step yourself, and the review model is never asked about them. A `yolo` run
does all of it and keeps a record. **What no mode waives:** a `research` run changes nothing but its notes file and
memory, and a rule of yours with the hand-off behaviour still stops a yolo run, at secrets, money and credentials too.

**If the review model fails** (the provider is down, or the model does not answer in the form asked for after two
attempts) there is no fallback. That one tool call fails with the error as its result, the run goes on, and the
question is not put to you in its place.

**Goal mode** is for `task` and `research` runs; a `chat` has no goal to check against, and asking for one is refused.
When the model calls `finish`, the review model checks the answer against the goal, from the evidence of what the run
did and not from what the answer claims. When the run kept a plan, the checker reads it with the evidence of each closed
step: a step marked done whose evidence nothing in the digest or the facts bears out is not done, its number goes into
the verdict's `unproven_steps` (in the `goal_check` event), and when the goal needs that step the goal is not reached.
When the plan has targets, it judges them too: they are the run's own measurable reading of the goal, not requirements
it added, and they make the goal reached only when they fit its words. The evidence is a digest of the run's tool calls
with the start of each result, and facts that Dot measured itself: the files its file tools wrote, its failed calls that
no later success of the same call followed, the newest result of each command that tests, builds or lints, and the paths
and addresses the answer names that the run never touched (paths that do not exist, addresses no call opened). Where the
facts and the answer disagree, the checker goes by the facts. Its verdict is one of:

- `achieved`: the run ends as done.
- `continue`: the goal is not reached yet, so the run is sent back to work with the instruction of the checker. A run is
  sent back at most `goal_patience` times; after that it ends as failed and keeps its answer.
- `blocked`: the goal cannot be reached from here (it needs you, a credential or something that does not exist). The run
  ends as failed with its answer and the reason.

A check can only ask for the original goal to be finished: it adds no work and does not clear the untrusted mark. If the
check itself fails, the run fails and keeps its answer. Each check is a `goal_check` event and a line in the timeline.
An answer that ends by offering to do a part of the goal that is not done yet, which the run could do with its own
tools, is `continue`, so that the part is done instead of offered; an offer of work the goal did not ask for (a fix when
only a diagnosis was asked) changes nothing, and what needs you (a decision, a credential) may stay an offer.

**Asked not to stop.** When the message you start a `task` or `research` run with asks it not to stop until the work is
done (whole words such as "don't stop", "keep going", "never stop", "until it is done", "durma", "hiç durma", "bitene
kadar", "tamamlanana kadar"; not "don't stop the server", "wait until it is done" or "bitene kadar bekle"), the run is
in goal mode whatever the switch says, and the panel's Details show "On, asked not to stop". Its goal check is told that
you asked: it accepts a finish only when every part of the goal is done and the evidence shows it, and a stop to report
progress is `continue`. A delegated run and a campaign's session keep their own setting, and a resumed run, one that an
answer starts and a follow-up on the run's page keep the run's. Whatever the model, every task and research run outside
a campaign is told to keep acting while the goal leaves work it can do, not to stop to report progress or to offer what
the goal asks for, and to take an ask to keep going at its word. This is not **Keep going** in the composer's Options,
which makes the goal a campaign worked on in sessions (see [Campaigns](#campaigns)).

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
still fails after its retries and the wait for a model out of reach (see [Models and providers](#models-and-providers));
in goal mode by the goal check giving up; at a limit of minutes or tokens you set; or when you stop it. With
`verify_answer_paths = true` in `[agent]`, a successful end whose answer names paths that do not exist is sent back once
before it is accepted, and before goal mode checks it (see [How a run works](#how-a-run-works)). A goal that should go
on across runs and days until its outcome is verified is a campaign (see [Campaigns](#campaigns)).

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
to you (not in yolo, scheduled, delegated or eval runs, where nobody would answer); and the stop. Twelve steps without
an alert bring the ladder one rung down again, so a long run is not stopped for hiccups that are hours apart. Every
alert shows in the timeline with what was seen and what was done. The ladder is off by default until evals show that it
helps; without it, a run that keeps changing its steps without getting anywhere goes on using the model until you stop
it. Give such a run a goal that can be checked, and keep an eye on the token count of a long one (`--verbose`, or the
Details panel).

**Resuming a run that was cut off.** A run that a crash, a restart or a shutdown of the daemon cut off, or a run of
`dotx run` whose terminal was closed (or that got SIGTERM) or whose command died, can go on where it stopped: Continue
on the run's page, `dotx resume RUN_ID` (in this terminal, like `dotx run`), or `POST /api/runs/{id}/resume`. A new run
starts in the same chat, with the goal, model, reasoning level, approval mode and goal mode of the run it resumes, and
the conversation so far, where a shutdown or a restart of the daemon, or a closed terminal, keeps the results of the
calls of the step it was in that had ended (a crash keeps none). Its first message tells the model how that run was cut
off and how each call of that step without a result ended: finished (with the start of its result), cut off while it
ran, or not started. A call that was cut off may have changed something unless it only reads, and the model is told to
check before it runs it again. The plan of that run is the plan of the new one. Only the newest run of a chat can be
resumed, and never a run that you stopped or that another run started. After a shutdown, the chat's queue goes on once
the new run ends done; after a crash or a restart, it waits until you resume it.

By default (`resume = "safe"` in `[agent]`, item 248) the daemon also resumes runs on its own when it starts: runs of
the web UI, cut off at most `resume_within_s` seconds before (30 minutes), whose calls that were cut off only read (a
question to you counts: it is asked again). A run of `dotx run`, of a schedule or of an eval, a run that itself resumed
another (so a run that brings the daemon down is not resumed again and again), and a run cut off in a call that may have
changed something (a command, a write, a click, or a delegation, whose run may have done anything) wait for you. `resume
= "off"` leaves every such run to you; **Go on after a restart** in Settings › System › Daemon writes the choice into
the config file (item 231), and the daemon's next start reads it. The timeline of the new run shows how each call ended,
with a link to the run it resumes.

**Lessons.** With `lessons = "propose"` or `"auto"` in `[agent]`, a task or research run of its own that reached its
goal after something went wrong on the way (a call failed, whether the run found its way around it or not; the progress
watchdog raised an alert; the goal check sent it back; its answer named paths that did not exist) is followed, once it
has ended, by one call of the review model (`[models] review`). It reads a digest of the run, what went wrong, the
answer and the saved notes that fit the goal, and writes at most three lessons of one sentence: know-how that a later
run could use from the start, such as that `pdftotext` keeps the columns of a table only with `-layout`. A lesson of
more than one line, of fewer than 20 or more than 300 characters (or `[memory] max_chars`), with a secret, an e-mail
address or a path in a home folder, or that says what the goal, another lesson or a saved note already says, is dropped;
one that says not to do what a saved note says to do (or the other way round) is no repeat of it, however alike their
words and meaning are (`not`, `never`, `n't`, the Turkish `değil`, `yok`, `asla`; the suffix `-ma`/`-me` is not
recognised). With `propose` the lessons wait under "Lessons from this run" on the run's page: Save to memory keeps one
as a note tagged `lesson` whose source is the run, Dismiss drops it (`POST /api/runs/{id}/lessons/{index}/save` and
`/dismiss`). A lesson is decided once: a second save or dismissal of it, from another page or after `auto` saved it, is
refused. `dotx run` and `dotx resume` show the lessons after the answer; Ctrl-C skips the wait, and the exit status
stays the run's. With `auto` they are saved at once, except after a run that read content others can write (web pages,
mail, downloads), which could plant a lesson: those are only proposed, and the page says why. A saved lesson is a note
of the agent like the others: a later run gets it when it fits its goal, and the Memory page edits or forgets it; a
lesson of an agent's run is that agent's own (see Agents). Delegated runs, chats, runs that failed or were stopped, and
evals (which always run without lessons) are not asked at their end; the nightly reflection asks about the failed and
the stopped ones. `lessons` is `"propose"` by default; `"off"` learns nothing. Every note a run is handed (at its start,
by `memory_search`, or changed while it ran) is recorded with how the run ended, so the Memory page says "Handed to 5
runs, 4 done" under a note and `dotx stats` scores the notes.

**Reflection.** Once a day at `[agent] reflection_time` (`"03:30"` in `[scheduler] timezone`; `"off"` never), the
daemon reflects on the runs that ended since the last reflection (the last day, the first time): it asks the review
model for the lessons of the task and research runs the loop did not ask at their end, the failed and the stopped ones
included, with how the run ended as one more signal and in words that say it did not reach its goal, under the
`lessons` setting above (`propose` leaves them on the run's page, `auto` saves them, `off` draws none); it folds the
lesson notes of one agent that say the same, the same way round, into the newer one (by the embeddings when both have
one, by the words otherwise; a resting lesson is left alone, so one learnt again does not take its rest); it rests a
lesson that no run was handed for
`lesson_rest_days` (60), or whose score says it did not help (handed to `[memory] score_min_uses` runs in the last
`score_days`, more of which failed than ended done; a lesson you saved yourself is never retired this way), by tagging
it `rest`, after which searches skip it while `dotx memory list` and the Memory page still show it, and the log line
`lesson_retired` says which reason with the numbers; and when anything happened it writes a journal note, tagged
`journal`, of the day it read: the day of the middle of that time, so the reflection at 03:30 names the day before ("On
2026-10-11 I worked on 4 runs (2 done, 1 failed, 1 cancelled). Learned: … Tidied the lessons: 1 merged, 1 retired."), or
its first and last day when it read more than a day and a quarter, after a daemon that was off ("From 2026-10-08 to
2026-10-12 I worked on …"). The reflection touches only the notes Dot wrote, never yours. It also keeps what Dot knows
about you (see Memory): after a day with runs, one call of the review model rewrites Dot's lines of that section from
the section as it stands, your own lines, your notes and the day's goals, at most `[memory] owner_chars` (1500)
characters. A night the Mac slept through is caught up once when it wakes. A daemon that was off at that time, or whose
stop cut the reflection (a stop does not wait for one, and it records nothing until its end), reflects at the next
`reflection_time` on everything since the last reflection. Activity lists the reflection among what ended today;
`dotx reflect` runs one now.

**Taking back the file changes of a run.** Before `write_file` or `edit_file` changes a file, the run notes the change
in its journal (`undo/` in the home directory), and its first change to a file keeps a copy of the file as it was. When
the change cannot be noted (a full disk, say), the file is not changed and the model is told why; a note the disk cut
short is left out of the journal, so the run's next change is noted after it and `dotx undo` still reads it.
`dotx undo RUN_ID` shows what taking back the changes of that run and of the runs it delegated to would do: a file the
run changed is put back as it was (`restore`), with its permissions, a file it created is deleted (`delete`), and the
folders it created are removed once they are empty. Nothing is changed until `dotx undo RUN_ID --apply`, which exits
with 1 when it left a file as it is. A file that changed or was deleted after the run's last change to it is a conflict
and is left as it is; `--apply --force` takes it back too, and what changed in it since is lost. A file larger than
`undo_max_file_bytes` (10 MiB), or whose copy would bring the copies of the run past `undo_max_run_bytes` (100 MiB), is
changed without a copy and cannot be put back; a file the run created can always be deleted again. A copy that is not
the one the journal noted is not written back, and a folder that is now where a file was is never replaced. Only the two
file tools are in the journal: what a shell command (`rm`, `mv`, `sed -i`), the browser or the desktop changed is not. A
run that is still working is refused; a run of `dotx run` that a crash left working ends when the daemon starts again.
`dotx prune --apply` deletes the journals whose last change is older than `undo_keep_days` (30 days). The Actions tab of
the run's page shows the same plan, one file at a time, with a button for each file that is as the run left it.

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

## The daemon, the web UI and the service

```bash
dotx daemon     # serve the API and the web UI, run the agent and fire the schedules, in this terminal
dotx open       # open the web UI of the running daemon in your default browser, signed in
dotx url        # print its address with the sign-in token
```

The web UI is laid out like a chat app: the chats in a sidebar on the left, one page on the right, and the settings on a
page of their own.

The sidebar lists your chats (the conversation threads) grouped by day, newest activity first, the pinned ones above the
days, and a chat opens at its latest run. Each chat wears a creature of Dot's character engine, as a messenger's list
shows faces (item 277): a chat with an agent the agent's look, a chat with Dot a shape and a colour its own id picks;
the creature works while the chat's run goes on, waits while the run waits for you and is sad once it failed. Its name
stands over its last line: the answer of its latest run in plain words, or your message, after "You:", while the run
works. The creatures rest; only those at work move. Each chat stands once (item 202, after T3 Code's Working shelf):
while its latest run works, a chat leaves its day for the **Working** section above the list, and goes back once the run
ends; a pinned chat stays under Pinned. At the right of a chat at work the time gives way to a dashed circle,
**Working** and how long since the run was asked for ("12s", "4m", "1h 5m", read again every second), as T3 Code's
sidebar has it. Under it hang all the runs it delegated (item 198), each saying the same while it works and how it ended
once it has, behind a line that says how many there are and, while some have ended, how many work; a click on it folds
them, and the fold is kept. Working lists too what is no chat's: a schedule about to fire, the session of a campaign
(which works in a thread of its own). A chat whose run waits for you keeps its waiting creature and its time among the
days, and what it asks stands under **Waiting for you**. Working counts what it lists, and Activity the runs that work,
which it shows under the run too. Folded, Working keeps the open chat in sight, and the search finds the chats at work
too. Above the list: a search over the titles (⌘K or `/`), **New chat** (⇧⌘O) and the ways to **Agents**, **Activity**
and **Campaigns**; in the foot, the **Connect apps** pill ([Connect apps](#connect-apps)) above **Settings** and the red
square of the **Emergency stop**, which stays there while the sidebar is closed (item 273): the top bar then holds the
sidebar's opener alone, the size of the sidebar's own buttons and of the side panel's switch and close. The top bar
shows only what needs you: the approvals waiting, and the connection to the daemon while it is being made or lost; what
works stands under Working. The sections (Waiting for you, Working, Chats) fold on a click of their label, and the folds
are kept between visits. The sidebar's head holds only the button that folds it away. **Select** turns the list into
checkboxes: tick chats (or Select all) and **Delete** asks once, then removes them with every run, step, event and
approval of theirs. A chat with a run under way is refused, the run of a `dotx` command too: wait for the run to end, or
cancel it (a `dotx` command's with Ctrl-C). Escape leaves the selection. Under 860 px the sidebar becomes a drawer. The
list stays where you scrolled it while it is painted again (a chat deleted, pinned, renamed or marked, chats deleted
under Select, a run that starts or ends, a follow-up in the open chat): it brings the open chat into view only when the
page comes to another chat (item 204).

A chat's menu, after T3 Code's thread menu, opens from the **…** button at the right of its row (shown where the pointer
rests or the focus is, over the time, and always on a touch screen), from a right-click on the row, or from the
context-menu key (Shift+F10); the arrow keys walk it and Escape closes it. **Pin** keeps the chat under **Pinned**,
above the days, the last pinned first, however old it grows; **Unpin** puts it back among the days. **Rename** makes its
name a field in the row: Enter or leaving the field keeps a new name, Escape leaves the old one. **Copy link** puts the
chat's address (its latest run) on the clipboard, for this browser or another one signed in to this Dot. **Select**
starts choosing chats with this one ticked, and **Delete** asks first, as Select's does. The daemon keeps the pin and
the name, so every browser shows them.

A chat whose latest run ended after you last saw it is **unread**: a dot in the accent colour at the right of its row,
under its time, and "unread" for a screen reader. Opening the chat marks it seen, and so does
having it open while the page is in view (shown, in a window that has the focus), so a run that ends in front of you
leaves no dot. **Mark unread** in a read chat's menu (once its run is over) brings the dot back until the chat is
opened again or the page comes back into view, and **Mark read** clears it. The daemon keeps when each chat was last
seen, so the phone and the desktop agree; the chats there when Dot learnt this are taken as seen, and a run that ends
while nobody watches, a scheduled one too, leaves its chat unread.

Above the chats, two sections appear while they have rows: **Waiting for you** (the approvals and questions runs wait
for, and a run whose browser you took over) and **Working** (the runs going on, a session of a campaign, a run delegated
by another, and a schedule that fires within the hour), each row with its state dot, the first words of its title on one
line, and under it the channel it last worked in (browser, files, shell, desktop, mail) and what it needs; a row opens
the run. **Activity** (`#/activity`) is the same in full, with the runs that ended today and a filter by kind (runs,
campaigns, schedules); it is `GET /api/activity`. A run that ended shows how long it took and what it cost in tokens.
Both follow the lifecycle stream, so a run moves between the sections the moment it starts, waits, is taken over or
ends. The stream replays nothing after a break of the connection (the network dropped, the daemon restarted), so once it
is back every page that follows it, the run's page and its queue among them, reads again what it shows; so does every
page when the stream fell so far behind that events were lost (`resync`). A page that cannot read again keeps what it
showed and says so. A stream from which nothing came for 45 seconds, not even the keepalive the daemon writes every 15,
has stopped without closing (a network that changed, a Mac that slept): the page cuts it and makes it again (item 200).
Between tries the page waits half a second, then longer, up to 8 seconds, and from half a second again once a stream is
made; a page that comes back into view, or a browser back on the network, tries at once. The rules page reads its rules
again when one is added, switched or deleted on another page. A page loaded before Dot was updated says so in a banner,
with **Load again**: the daemon's status tells what the pages it serves are (`pages`, a digest of their files at its
start), and the page compares it with what it was loaded from. A run delegated by another run sits under the run that
delegated it, in both the sidebar and the Activity page, with its steps so far and its channel; the parent's row says
how many runs it delegated. In the chat, the delegated run's card carries a chip with its progress ("step 3 ·
web_fetch", then "done · 12 steps") and an **open** link to the run's own page.

**New chat** is Dot's presence and one composer, standing in the middle of the space under the top bar, across and down,
however long the day under them is. The face is Dot's character, moving (idle with a wink now and then; a still frame
with "Faces that move" off), and shows the state of the whole of Dot (waiting when something waits for you, working
while a run is active, else idle) under Dot's name (yours to give in Settings › General; Dot until then, and kept in
this browser only); a greeting by the hour says your name when you gave one there ("Good morning, Ada. What should I
work on?"; "Something waits for you." when it does) and one line says what is going on ("Working on 2 runs · 1 waits for
you · 3 ended today · 1 lesson · 2 things due today"; a quiet day says so). Under the composer, the **Today** strip
lists it: what waits for you, what works, what is due later today (the schedules that fire and the campaigns whose next
session starts), what was learned today (the lessons and the day's journal) and what ended, each row as on the Activity
page and each opening what it is about; two rows a section (a run without the runs it delegated, which the Activity page
nests under it), then "and N more in Activity", and the page scrolls to what does not fit. A section folds on a click of
its title, which counts its rows, and the folds are kept in that browser. The line and the strip follow the lifecycle
stream, so an answer or a run's end moves them at once; `GET /api/activity` carries the two new lists as `learned_today`
and `due`. The composer is one frame on a 24 px corner with a soft shadow under it: the goal in a box of one line that
grows with it, the files attached under it, then one bar that never wraps: the attach button and the mode (Task, Chat or
Research; its menu says what each does) at the left; at the right the agent with its face and name once an agent exists
(its menu lists the agents with their roles), **Model** (the mark of its provider and its name: the daemon's default or
one picked), **Reasoning** (a brain and the level, only for a model that offers a choice; kept in the browser per model)
and **Options** (a lock, closed for Ask, a shield for Auto, open for YOLO: the approval mode, which the pill always
shows, the Goal mode switch and Keep going, see [Autonomy](#autonomy-approvals-goal-mode-and-the-end-of-a-run); YOLO in
the warn ink, on no fill) and the round send, quiet while the box is empty. Each pill is an icon and its value, on no
ground until the pointer is over it; as the frame narrows the mode's name folds to its icon, then the chevrons and the
agent's name go and the model's name gives way with an ellipsis, while the reasoning keeps its level's word (item 273),
so on a phone the bar is still one line. On a phone, and on any touch screen, the box's text is at least 16 px, as iOS
zooms the page into a box whose text is smaller, and the bar's buttons are 36 px. While Keep going is on, one line under the frame says so. Each pill opens its menu above it, and a
pick or Escape or a click outside closes it. The model's menu (item 208, after T3 Code's) has a rail on its left, a star
for the favourites and the mark of each provider, that shows the favourites or one provider's models; a search over the
models of every provider, which has the keys as the menu opens and reads names, providers and "free"; and on each model
a star that keeps it among the favourites in that browser, where the menu opens once there are any. The arrows and Enter
pick a model, ⌘1 to ⌘9 the rows that show those keys, and ⌘D stars the row the keys are on. The default's row, the first
of every view, has **Change** (item 209): the list then chooses the model new chats start on, a bar under the search
says so and the default has the tick, and the model picked (by a click, Enter or ⌘1 to ⌘9) becomes the default, as in
Settings › Models; the menu goes back to the chat's model and stays open. A chat that has a model of its own keeps it,
and one that follows the default moves with it. Escape or Cancel goes back without a change. What you typed and attached
and did not send is kept in this browser and is there again when you come back (so is the box of each run). Dot's face
looks down at the box while you write and brightens when you send. Enter starts the run, Shift+Enter adds a line, or
⌘Enter (Ctrl+Enter) starts it and Enter adds a line, as Settings › General › Composer chooses for every composer of this
browser (item 230, after T3 Code's settings). The
markdown in the box shows as it reads (item 207, after T3 Code's composer): its marks (a heading's hashes, a list's
mark, the stars of bold, the backticks of code, a link's brackets, a table's pipes) quieter than its words, bold words,
headings and a table's head heavier, code tinted, quotes softer and links in the colour of links; the box is still a
text field, with its caret, selection, spelling and undo, and what is sent is the text as typed. A message whose answer
was lost on its way back (the page says the sending failed and keeps the message) is done once when it is sent again,
here and in the box of a run: the page sends each new message with a key of its own, and the daemon answers the same
message sent again with what its first sending made. `/` at the start of a word opens the slash menu over the box: the
commands of the composer (`/task`, `/chat`, `/research` here; `/btw` in the box of a run), the skills by name with their
description, and each MCP server with its tools, read from the daemon as a run reads them (`GET /api/palette`); letters
filter, the arrows move, Enter or Tab picks and Escape closes. A skill picked reads "use the skill NAME: " in the box, a
tool its name, a command does its work. A run that waits for your approval shows above the composer, as a card, and
moves it down only when the cards do not fit there.

A run opens as a conversation under Dot's face and name. The face is the state: still when the run rests, a slow breath
while it works, an amber ring while it waits for you, a green tick for a moment when the run ends done, red when it
failed (with reduced motion the tick simply shows). The panel button sits on the right; the run's facts (its state,
start, agent, model, approval mode, goal mode and campaign) are in the panel's Details, on its Run tab. Your goal is the
first message, in the bubble of your messages on the raised surface. What you write reads there as markdown, as an
answer does: its headings, lists, tables, code, quotes and emphasis (item 206). Under each of your messages, once its
turn has its answer, stand the time it was sent (the day too when it was not today, the full date where the pointer
rests), a copy of it as you wrote it and **Send again**, which sends it once more with its files, as the box would, and
leaves what you typed in the box (item 205, after T3 Code); the files a message came with stand between it and that
line. A delegated run is a row: the face with its state at the corner (turning while it works, amber while it waits for
you, green with a check when it is done, red when it failed), its goal over the last thing it did and, once it ended,
the start of its answer (item 245), the time it has taken (counting on every second while it works), the way to its own
page and a chevron that opens its steps. Rows that follow each other gather in one group, as T3 Code groups its
subagents: its head shows their faces, how many they are and how they stand ("3 helpers", "1 working · 2 done"), the
time from the first one's start to the last one's end and a chevron that folds the rows away; an approval a helper asks
for opens the group. The row is closed from the start, as T3 Code draws a subagent: an approval the run asks for
opens it until you have decided, and a row you never opened or closed yourself closes again. Resting the pointer on a
row, or moving the focus to it, shows its card (item 226, after T3 Code's): the whole goal, the model with its reasoning
level, how the run stands and for how long, counting on while it works, and what it does now or did last; the pointer
leaving, a click or Escape puts it away, and on a touch screen a tap opens the row as before. Its own page names the
parent in the top bar as a breadcrumb (the parent's goal, a link, then its own) and has a pill where the box would be,
which stays at the window's foot as the conversation scrolls (item 244), as T3 Code keeps a subagent's bar: the model,
how long it took ("Completed in 1m 46s", "Working for 2m"), whether its parent still works ("Runs on its own" once not),
"Open parent" and, while it works, **Stop**. The tool calls of one stretch of work fold into a single "Worked
for 12 s (model 8 s) · 5 tool calls" line, where the part in brackets is how much of that time the model's turns took
(open it for every call, result and screenshot: each call one quiet line with its icon, its result on a hairline under
it once opened), while approvals, questions and policy notes stay in the open. What the model thought in a step is a
line of its own there, before the step's calls (item 246, after T3 Code), or out of the fold before the message the
step writes or the answer it ends the run with: a brain where a call has its icon, "Thinking…" while the model thinks
and "Thought" once it is done, the start of the thought beside it; opened, the whole thought: up to 12,000 characters
of it, of a longer one its start and its end, its secrets masked; it is there when the page is loaded again. A step
asked again from its start drops what its failed try streamed. A message streams as it will stay (item 274): formatted
as it comes, drawn at most once a frame with the parts already written left as they are, its caret at the end of its
text, so that its end moves nothing. The answer of the run is the last message, with a copy button (its icon turns to a check once the answer is on the clipboard), when it came and how the
run ended under it; how long the run took shows where the pointer rests on it (on a touch screen it always shows). A
copy out of the conversation keeps its markdown, as T3 Code's does (item 206): a selection that starts in a message or
an answer goes on the clipboard as the markdown it was written in, a list as a list, a table as a table and code in its
fence, with the formatted copy alongside for the applications that read it; the line under a message or an answer stays
out, and a copy within a block of code is its code. The box stands at the foot of the window from the first message, the
conversation filling the space above it from the top, and is the same frame as the home's, with the attach button and
the send in its bar; what you typed there and did not send waits for you. While the end of the conversation, or its
answer, is below the window, a round arrow just above the composer leads there; it stays away while the page follows a
working run down, as the page does until you scroll up. At the left of the conversation a column
of short marks stands for your messages, one mark for each, top to bottom (item 201, after T3 Code's): resting the
pointer on it shows the message nearest to it and the start of its answer, a click goes to that message, and from the
keyboard the arrows walk the marks and Enter goes; the marks of the messages in sight are darker. The column needs a
pointer that rests, two messages or more, and a window wider than a phone's; there, the conversation leaves it its room
at the left, beside the side panel too (item 228). In the side panel (Run details), under Details on the Run tab,
**Cost** lists the bill of the run, each value beside its label as in Details: where the minutes went (the model, the
summaries, the tools, the time spent waiting for you), the steps, the input tokens with the share the provider served
from its cache, the output tokens, the compactions (with their rate per 100 steps), the runs it delegated and the web
searches by engine. It is `GET /api/runs/{id}/stats` (the same numbers as `dotx stats --run ID`), read again every ten
seconds while the run works and once at its end. A strip under the head links the other runs of the same thread, and the
box at the bottom queues a message for after a run that is going on, or follows up on a finished one. While the run
works and the box is empty, the box's send is the run's **Stop** (a round button in the danger colour with a square) and
cancels it; a word typed turns it back into the send, **Queue**, and the message waits in the chat's queue (see [How a
run works](#how-a-run-works)), shown above the box as T3 Code shows it: a sheet whose head (Queued and the count) folds
it, with a row per message: a handle to drag it to another place or move it with the arrow keys, its text on one line
(all of it in the tooltip), a pencil that edits it in the box (the send says Save; Escape or Cancel gives the box its
draft back), **Steer**, which sends it to the run now, and × to take it out. ⌘⇧Enter steers the run with what the box
holds, past the queue, or with the queue's next message when the box is empty. Settings › General › Composer can have a
message steer the run instead (item 230): the send then says **Steer**, and ⌘⇧Enter puts what the box holds in the
queue. A held queue says it is paused, with **Resume**, and a queue the page could not read says why, with **Read
again**. When the run ends done, the next message starts and the page follows it; every open page shows the queue as it
changes. In a campaign's chat the message steers the session. The box wears the home's pills too, after T3 Code's
composer: the mode, the model with its reasoning and the Options (approvals and goal mode) of the chat, and a follow-up
or a queued message starts its run with them on the same thread, for the same agent. The chat keeps them (item 199):
those its first run started with, then each one chosen in the box, at once and for every browser, so a reload, a restart
of the daemon or a run moved to a standby model leaves them as they are (a chat from before has the open run's until its
next run starts). They are not kept in the browser as the home's are, and Keep going stays a new chat's choice. The run
under way keeps its own: while it works, the pills step back and say that they wait for the next follow-up. Beside the
send, a ring after T3 Code's shows how much of the run's context budget its last step filled (item 222): the tokens that
step sent the model against the run's budget (`context_tokens` or the model's own context size, or three quarters of
the window when that is less); it turns to the warning colour past 80 % and to the danger colour past the budget, and a
click shows the figures, the point past which Dot summarizes the older part of the run, the model's window, and why
the budget is smaller than the window (each step sends the model the whole run again, so a run kept within it is
answered sooner and costs less) and where it is set (items 239 and 250): `[agent] context_tokens`, or the size chosen
for the model in Settings › Models, which the timeline notes too. It
reads the run's `scaffold` and `assistant` events and asks the model nothing. A run the daemon cut off offers
**Continue** on a line above the box. The panel's Details show the status, the agent of a chat with one, the model and
its reasoning level, the approval mode and goal mode, the campaign of a session, the model turns, the tool calls (with
the failed ones), the tokens (of every model call of the run: its steps and its side calls, such as compactions, reviews
and the goal check), the duration, the start and the run id; its other tabs hold the plan, the actions, the agents of
the chat and the latest screenshots. Each tab wears its icon, and where the strip has no room for every name, the tabs
not chosen show their icon and count alone (item 227). The Agents tab lists, newest first, the runs the chat's runs
handed work to, after T3 Code's "Previous agents": five at first and the rest on **Show N more**, each with its face and
goal, its state and model and how long it took, and a row opens its page. It stays closed until the panel button in the
head of the run opens it (the choice is kept in the browser); open, it sits beside the conversation from 960 px and is a
drawer below that. The run's head is the top bar itself: Dot's face and name on the left, the panel button on the right;
the bar is solid with a hairline under it, so nothing scrolls under a blur, the panel stays in place under it, and the
box stays at the bottom. Every other page names itself in the same bar (Agents, Activity, Campaigns, Settings) and opens
with one line of description.

In the conversation each turn of the actor (Dot, or the agent the run belongs to, in its look), and the answer at the
end, opens with its still face; the run's state is the top bar's face and the first fact of Details, with no card for it
at the head of the conversation. The panel is one card on the composer's corner, in four tabs after T3 Code's side panel
(item 203): **Run** (Details and Cost), **Plan** (its steps; the tab says how many are done), **Actions** (what the run
changed and asked, and the conversation's Files) and **Screen** (Dot's computer and the screenshots, which the tab
counts). A tab shows its sections open, on hairlines; the arrow keys, Home and End move between the tabs, and the tab
chosen last is kept in the browser (`dot.panelTab`). Beside the conversation the panel slides in as it is shown and out
before it hides, the conversation taking its room back once the slide is over, at once under reduced motion (item 220).
It stands at the window's right edge, as T3 Code's does, as far from it as from the top bar, and the conversation keeps
its reading width in the room the panel leaves it (item 238). It has a size: three buttons on Dot's computer make it
**Compact** (its own 320 px), **Wide** (half of the page) or **Full** (the conversation at its narrowest, 360 px, the
rest for the panel), and the handle between the conversation and the panel drags it to any width between (the arrow keys
move it a step, a double click gives it back its own width); the size is kept in the browser (`dot.computer.size`).
While the panel is wider than its own width, the computer's view grows with the panel in its warm frame. The drawer of a
narrow page has one width. **Dot's computer**, on the panel's Screen tab, shows the page of the run's browser as Chrome
paints it (`GET /api/runs/{id}/screencast`, Chrome's own screencast, sent only while the view is open and the page in
sight; a tab that does not change sends nothing). A view whose stream breaks or goes silent is made again by itself
after a short pause, and one put out of sight (a phone that put the app away) starts again once it is back (item 276).
The view starts by itself at the run's first browser step; until a frame comes it keeps the
shape of a screen and says why, with a mark and a title (no page yet, waiting for the page, the view closed, the run
ended, or a run in a `dotx` command, item 220). **Watch** and **Close the view** open and close it by hand; once the run
has ended it keeps the last screen and Watch goes. A pill under it says who has the browser ("Dot has control", or the
agent's name). Since that view shows the page, Dot's own Chrome stays in the background while a run works
(`[browser] raise_window`, off by default, items 97 and 118): the tabs Dot opens are opened behind, the app you were in
gets the focus back right after Chrome starts and after a page opens a tab of its own, and switching tabs does not raise
the window; Settings › Browser has the switch "Bring Chrome to the front while working" for the other way round. **Take
over** holds the run at its next tool call, brings its tab to the front either way, in the Chrome the run works in (a
campaign's session, in the campaign's), and the pill reads "You have control" (when the tab cannot be brought up, the
run is not held): the Chrome window is on your Mac, so sign in, pass a CAPTCHA or do a step yourself; **Return control**
lets the run go on, and the result of the tool call that waited tells the model how long you had the browser and that
the page may have changed, so it takes a snapshot first. The timeline keeps both moments (`takeover`, `handback`), and
`dotx run` prints them. A takeover is refused while `[browser] headless = true` (there is no window to use), and the run
page keeps the last frame once the run has ended.

The **Actions** section of the panel's Actions tab is one view of what the run did and what can be undone: every acting
step (a tool call that changed something: a file written, a command run, a click, a message sent; reads are left out)
with the channel it went through, its target and the result the model got as its evidence; every approval and question
of the run with its decision, who decided and the note; and every file the run, or a run it delegated to, changed with
`write_file`, `edit_file` or `write_notes`, with its undo state as `dotx undo` reads it (can be taken back, changed
since, cannot be taken back, as before the run). A file as the run left it has a button, Restore or Delete, that takes
that one change back once the run has ended (`POST /api/runs/{id}/undo`); the section itself is
`GET /api/runs/{id}/actions`.

**Settings** (`#/settings`) is one page with tabs: **General** (Dot's name and yours, Dot's own look, desktop
notifications and the token this browser is signed in with), **Appearance** (the look of this browser, below),
**Models** (the **Default model** first, a menu of the models that can call tools: the runs that name no model, a new
chat's among them, start on the one chosen at once, and `config.toml` keeps it as `[models] main`, as when it is changed
from the composer's model menu; then one fold per provider, with the reasoning levels of each model and a menu to choose
one; a provider without a key, or whose key was refused (HTTP 401), says so and links to API keys, and one that refused
access (HTTP 403) says that, with its words), **API keys** (the key of each provider; see [Models and
providers](#models-and-providers)), **Browser** (which Chrome Dot browses with and what Chrome has to allow first; see
[Browser use](#browser-use)), **Rules**, **Memory**, **Skills**, **Schedules**, **Phone** (the addresses a phone reaches
Dot at and a pairing code for its home-screen app; see [Dot on the phone](#dot-on-the-phone)) and **System** (the
daemon, the emergency stop and the health checks in a fold, closed, with the number of failures on its line). Which folds are open is kept in
the browser. The tabs sit beside their content when the window has the room and above it otherwise, in one row that
scrolls sideways with the open tab in sight and stays under the bar as the page scrolls (item 272); the earlier
addresses of the memory, schedules, rules and system pages lead to their tabs.

Settings › Appearance (⌥⌘A), after T3 Code's page, holds the look of this browser. **Colour scheme** is three cards,
each a miniature of Dot's window drawn in the scheme it names: System (the default, split down the middle), Light and
Dark; ⇧⌥⌘A steps through them. **Themes** is a gallery of cards with a light and a dark orb each: the card takes its
theme for both schemes, an orb for its own scheme only (a sun or a moon marks the picks), so light can wear one theme
and dark another. Dot's two looks are **Neutral** (warm grey and ink, round and quiet; the default) and **Paper** (warm
paper tones with a serif heading and a terracotta accent), whose serif voice follows the scheme it is picked for; T3
Code's six palettes are **Clean** (T3 Code's own look: near-white, grey and a blue accent), **Rose** (T3 Chat's),
**Grove**, **Ocean**, **Ember** and **Iris**. A palette's colours are mapped onto Dot's tokens (its action colour, which
T3 Code paints its solid controls with, is Dot's accent) and held to the rule of the looks below: a text tier or an ink
that would read under its ratio is moved in lightness until it reads, and in dark a card lies a step above the page; a
browser test measures every theme in both schemes as the page paints it. The two picks are written on the page as
`light-dark()` pairs (nothing when one look is picked for both) and kept with the picks, so the first paint after a
reload already wears them. Themes of your own join the gallery: **Create theme** starts from the themes in use for light
and dark, **Duplicate** (on a card, as Edit, Export and Delete are on one of your own) copies any theme, **Export**
saves one as T3 Code's theme file, and **Delete** asks first and gives a scheme it was picked for back to Neutral. The
editor takes a name and, for each scheme the theme has, the **Background** and the **Accent** its palette is made from,
as T3 Code's generator makes one, and the **Action** colour Dot paints its buttons, switches and send button with (the
generator turns it away from the accent, so Dot lets you set it); **Colour families** change one colour, and the ones
made with it, at a time. The page wears the palette while you change it. **Add theme** reads a file: T3 Code's theme
file (version 1, or the short form of a name, an appearance, a canvas and an accent) or a VS Code colour theme, mapped
as T3 Code maps one. The daemon keeps them, one theme file each, in its home's `themes` folder (made with the first
theme saved; 32 themes, 32 KB a file and 192 KB in all at most), so every browser has them; a file put there by hand
shows after a reload, one Dot cannot read is listed under the gallery with the reason, and a colour a theme leaves out
is T3 Chat's, as in T3 Code. **Interface**: **Contrast**, 50 to 200 % (100, the theme's own, by default): under 100 the
text tiers and the lines fade toward the page, over it they move toward black in light and white in dark, the lines a
quarter as far, as T3 Code mixes them; **Chat width**, how wide the home and a conversation read (Comfortable, 768 px,
the default; Wide, 1152 px; Full); **Density**, how close the rows, cards and lists sit (comfortable or compact); and
two switches that follow the theme picked for light until you set them yourself: fold the tool steps (on in all but
Paper) and the Details panel (off in all). **Motion**: **Panel animation**, how long the sidebar and the details panel
take to slide (0 to 400 ms, 200 by default; while the system reduces motion they never slide), and "Faces that move"
(below). **Type**: the interface's font (Inter, the system's, or families typed, which go before Inter so that no glyph
goes missing) and its size (13 to 17 px around 14, every text size scaled with it); the answers' font (the theme's,
which is a serif in Paper, the interface's, or a serif); the composer's font (the interface's, a serif, the code's, or
families typed) and size (12 to 20 px, 15 by default); the code's font (Geist Mono, SF Mono, Menlo, or families typed)
and the size of an answer's code blocks (10 to 18 px, 13 by default), each with a line of preview; font smoothing (on)
and long lines of code wrapped (off). The choices are kept in the browser only, and `boot.js` puts each of them on the
page before the first paint. The palettes, the colour arithmetic, the orbs, the contrast's mixing, the families typed,
the theme generator, the theme file with its checks and the VS Code import are a port of T3 Code
(github.com/pingdotgg/t3code, MIT, T3 Tools Inc.) to plain ES modules (`static/js/appearance/LICENSE`); Dot is not
affiliated with T3 Tools Inc. The looks are one visual system. Colour: every colour is a token of `themes.css` naming
its light and its dark value in one place; surfaces step up by lightness (page, sunken sidebar, raised cards) rather
than by shadow in dark; borders are the text colour at a small alpha so they sit on every surface; the text tiers are
measured against every surface by a unit test (4.5:1 for text and its second tier, 3:1 for the muted and faint ones);
the yellow is the owner's bubbles. Type: Inter for the interface and the answers, Geist Mono for code and identifiers,
both served from Dot's own origin (`static/fonts`, SIL Open Font License); seven sizes each with its line-height (12/16
badges and chips, 13/18 meta and hints, 14/20 the interface, 15/24 prose and the composer, 17/24 section titles, 20/28
page titles, 24/32 the greeting), three weights (400 text, 500 labels and names, 600 titles), tabular numerals
throughout; a unit test refuses any size, weight, line-height or family in the sheets that is not one of these tokens.
Surfaces: cards are flat, a surface with a hairline and a 12 px radius and no shadow (shadows stay on what floats:
menus, dialogs, the composer in focus); section labels are sentence case; the facts of a row are spans a gap apart
rather than one dotted string; an empty list is one line that says so and what to do; your messages sit on the raised
surface. Controls: buttons, inputs and selects 36 px tall on an 8 px corner with a hairline (32 for the small ones), the
same 2 px focus ring on each, a select with its own drawn chevron, a range on a hairline track filled with the accent up
to its value under the switch's knob, field labels on the interface line and hints on the small one, the composer's
menus as round pills of 32 px (an icon of 16 px, the value, a chevron of 12; no ground at rest, a tint under the
pointer) that open a panel above them, the providers' marks served from `static/icons/providers/` with their licences,
table rows 40 px with their content on the middle; `#/controls` shows every control on one page for checking a look.
Faces: every actor is a character (below); an agent wears the look you choose, Dot its own (Settings › General), and an
actor without one (a delegated run, a campaign, a schedule) the shape and colour its id's hue gives it, one of eight, so
it keeps its face in the sidebar, the lists, the rows of a chat and the bar of its own page. Characters: the animated
faces (`static/js/character/`) are a port of bloub (github.com/jeremy-prt/bloub, MIT, Jérémy Perret) to plain ES
modules, one filled shape with two eyes that are holes in it, in eight shapes, sixteen expressions, twelve colours (the
theme's `--character-*` tokens) and fourteen animations; bloub recreates the behaviour of x.ai's bot avatar as an
exercise and is affiliated with nobody, and neither is Dot: the licence covers the code, not the design it imitates
(`static/js/character/LICENSE`). One loop drives every moving character on the page and stops while the tab is hidden;
under reduced motion, or with "Faces that move" off in Settings › Appearance, each shows one still frame (while the
system reduces motion the switch says so, and where that is turned off). A still character follows a change of its shape
or expression at once. Two characters move: the home's face and the look editor's preview; every other face is a still
frame. One moving face costs about a millisecond of main-thread time a frame (`tests/smoke/test_character_cost.py` reads
it from Chrome's performance metrics and caps it). `#/controls` shows every shape, expression, colour and animation.
Space: every gap, padding and margin sits on one rhythm of 8 px (4 for the smallest, a 2 px hairline token for what sits
a breath apart; a unit test checks it); the sidebar is a tone darker than the page with no line between them, has no
dividers between its groups, its labels are the lightest ink that reads, a chat's last activity sits at the right of its
row in figures of one width ("5m", "3h", "2d") and its open row is a surface step rather than a tint; the top bar's
pills show a border only when they count something; a card's head is lighter than its body; a folded step of the
timeline is one line whose duration shows on hover. Motion: three durations (120, 200 and 320 ms) and two easings for
what changes, and the rhythms of what repeats (the face's breath and pulse, a blink, a spin); a unit test holds every
transition and animation to them. The face breathes slowly at rest and pulses while a run works; cards, rows, the slash
menu and the toasts fade in; a state badge cross-fades. Under the system's reduced-motion setting every transform goes
(the breath, the pulse, the spinners, the drawer's slide) and the fades stay, so a state change is still seen. Dot's
mark is a yellow dot with the character's eyes, the o of the wordmark "dot" between a d and a t drawn in one line
(`static/index.html`); the favicon, the app's icon, the menu bar's and the web app's icons are drawn from it
(`desktop/icons/render.py`), and the wordmark heads the sign-in card. A window that opens the UI plays the opening once:
the d and the t hop up, the dot drops in with its eyes shut, bounces twice, opens its eyes and blinks, and the page
shows through, 1.3 s in all; a click or a key cuts it short, it takes no pointer, and it plays neither on a reload nor
under reduced motion. The look, the scheme and the density you chose are put on the page by a small script in the
document's head before the first paint, so a held dark scheme never flashes light on the way in; a screenshot tour
(`tests/smoke/test_tour.py`) photographs every page in both schemes at 1280 and 390 for reading by eye.

While you are elsewhere, the tab title counts the approvals that wait: `(2) New chat · Dot`. Settings › General ›
Notifications adds
desktop notifications (off until you switch them on; the browser asks for its permission on that click): Dot says
when it asks for an approval, a question or your help, and when it leaves you a question that a run could not wait for
(a `yolo`, scheduled or campaign run goes on without the answer); when a run ends (done, failed or stopped, with the
first line of its answer; the sub-runs of a run stay quiet); when a campaign ends by itself (achieved, or stopped by
the emergency stop or by `campaign_max_sessions`); and when a schedule misses a firing that nobody saw (overdue, or
its run could not start; told once while the reason stays the same). It says nothing while you are looking at that
run: its page open, the tab visible and the window active. On another
page, in a background tab or in an inactive window you are told; "Tell me even when I am looking" under the same
setting turns the rule off. Clicking a notification opens what it tells of. The tab's
icon is the same face in the state of the whole of Dot (amber ring while something waits for you, working while a run
is active, still otherwise), and a notification carries the face in the state it tells of.

Ctrl-C stops the daemon: the runs that are going on are cancelled, and the browser and the other services are closed.

The server listens on `127.0.0.1:8765` (`[server]`) and refuses to start on any other address. Every request must carry
a `Host` of the loopback address and, when it has one, an `Origin` of the server itself; every call under `/api` except
`/api/health` and `/api/proof` needs the bearer token in `<home>/api-token` (mode 0600). A page a run showed
(`/pages/<id>`) needs none: its id cannot be guessed, and it is served sandboxed (see [Tools](#tools)). `dotx url` puts
the token in the fragment of the address (`#token=…`), so it never reaches the server or a log; the page keeps it in the
browser's local storage and removes it from the address bar. A page that cannot sign in says why: a token the daemon
does not take is asked for again, a daemon that does not answer is to be started, and one that answered with an error is
running, and the page shows that error. Pages are served with a strict content security policy and no CORS headers.

The token goes only to the daemon. Before `dotx open`, `dotx url` and `dotx service permissions` hand it over, and
before each request of the page and each poll of the desktop shell that carries it, the client sends a fresh nonce to
`GET /api/proof` and checks the answer: an HMAC-SHA256 of the nonce keyed with the token, which only a holder of the
token can give. A program that took the port while the daemon was down cannot, so it is sent nothing: the command stops
and names the one that shows what listens on the port, the page asks for the token again, and the menu bar item says
that something else answers at the daemon's address. What the check cannot cover is a page that such a program served at
that address: it reads what the browser keeps for the address, the token among it.

```bash
dotx service install     # a launchd agent: starts the daemon at every login and after a crash, never after a clean stop
dotx service status      # whether the job is loaded and the daemon answers
dotx service permissions # make macOS ask, in the name of the service, for the desktop permissions it lacks
dotx service uninstall   # stop the daemon and remove the job
```

- Run `service install` again to restart the daemon after you changed the configuration. It stops the running job before
  it writes the new job's file; when launchd does not load the new job, the file of before is put back and its job
  started again, and the install fails with launchd's words.
- A launchd job has no terminal, so macOS attaches Accessibility (it covers posting input events) and Screen Recording
  to the Python binary that runs the daemon, not to your terminal. Grant them to that binary; Settings › System in the
  web UI reports what the daemon itself has, and `dotx desktop check` what your terminal has. This prints the binary:

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
| `POST /models/main` (`{"model": "provider:model"}`)                            | make the default model, kept in `config.toml` (422 for one that cannot call tools or of no known provider; the provider's error, 502, for one it does not know) |
| `POST /models/roles` (`{"role": "review", "model": "provider:model"}`)         | make the fast, review or vision model, kept in `config.toml` (422 for one that cannot do that work) |
| `PUT /agent/resume` (`{"resume": "safe"}`)                                     | whether the daemon's next start resumes the runs it cut off (`"safe"` or `"off"`), kept in `config.toml` |
| `GET\|PUT /shell/sandbox` (`{"sandbox": false}`)                                | whether a run's commands outside yolo start in the macOS sandbox, from the next one on, kept in `config.toml` (422 when the sandbox does not start) |
| `PUT /models/context` (`{"model": "provider:model", "tokens": 192000}`)        | the context size of the model's runs from its next one, kept in `config.toml`; `null` for `[agent] context_tokens` (422 below 48 000 or past three quarters of its window) |
| `GET /models/context?model=provider:model`                                    | what the model's runs of the last 30 days measured about its context size |
| `GET /runs`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`            | list, start and read runs                                    |
| `GET /runs/{id}/stream?after=N`, `GET /stream`                                  | live events of a run with its sub-runs; lifecycle of all runs |
| `POST /runs/{id}/steer`, `POST /runs/{id}/cancel`                               | message a running run; stop it (a `dotx` command's run: the command is asked to stop it) |
| `POST /runs/{id}/btw` (`{"text": …}`)                                           | a side question, answered without the run seeing it          |
| `GET\|POST\|PUT /threads/{id}/queue`, `POST /threads/{id}/queue/resume`         | a chat's queue (`held`, `messages`); queue a message (`text` and the choices of `POST /runs`; 409 for a chat with no run yet, a campaign's, or the thread of a delegated run); reorder it (`{"ids": […]}`, each once); resume a held one |
| `PATCH\|DELETE /queue/{id}` (`{"text": …}`), `POST /queue/{id}/steer`            | edit or take out a queued message; send it to the run at work now (409 with none); 409 while its run is starting; each answers with the queue |
| `POST /attachments` (`{"name", "media_type", "data_b64"}`)                      | keep a file for a message; `POST /runs` and `steer` name it in `attachments` |
| `GET /threads?limit=N`, `DELETE /threads` (`{"ids": […]}`)                      | the chats, the pinned first, then newest activity first, each with the face it wears (`actor_id`) and its last line (`last_message`, `last_role`); delete some with their runs (409 with the ids while one has a run under way) |
| `PATCH /threads/{id}` (`{"title": …, "pinned": …, "seen": …, "choices": …}`)   | name a chat, pin it, mark it seen or unseen, or keep the choices its next follow-up starts with (`mode`, `model`, `effort`, `approvals`, `goal_mode`; `GET /runs/{id}` has them as `choices`); one at least (204; 404 for no chat); a chat's `unread` is in the list |
| `GET /threads/{id}/helpers?limit=N`                                            | the runs a chat's runs delegated, newest first (the side panel's Agents tab; 404 for no chat) |
| `GET /threads/{id}/terminals`                                                  | the chat's terminal sessions: command, background or not, start, end and exit status (404 for no chat) |
| `DELETE /threads/{id}/terminals/{session}`                                     | ends a session's program and what it started; the chat is told nothing (404 for no such session) |
| `GET /approvals`, `POST /approvals/{id}/decision`                               | pending approvals; answer one                                |
| `GET\|POST /rules`, `PATCH\|DELETE /rules/{id}`                                 | custom policy rules                                          |
| `POST /rules/draft` (`{"sentence": …}`)                                         | a rule drafted from your words, not kept (422 with the model's reason when the words make no rule) |
| `GET /memories`, `GET /memories/search`, `POST /memories`, `PATCH\|DELETE /memories/{id}` | notes                                             |
| `GET\|POST /schedules`, `GET\|PATCH /schedules/{id}`, `POST /schedules/{id}/enable\|disable`, `DELETE /schedules/{id}` | schedules (`PATCH` takes the whole schedule) |
| `GET\|POST\|DELETE /failsafe`, `GET /images/{name}`                             | emergency stop; screenshots of runs                          |
| `GET /browser`, `POST /browser/mode` (`{"mode": "own"\|"attach"}`)              | which Chrome Dot browses with and what Chrome allows; switch it (409 while runs are active, 422 when Chrome refuses) |
| `POST /browser/debug-port` (`{"debug_port": 9222}`, 0 for none)                 | the DevTools port of Dot's own Chrome (409 while runs are active, 422 below 1024 or when another program holds the port) |
| `GET /connections`, `POST /connections/{id}`                                    | the applications of Connect apps, where each stands (`state`) and its step (`step`); take the step: ask macOS (a minute for your answer), show the Automation pane, open OneDrive, or open the sign-in page in Dot's browser (404 for no such application, 409 for a sign-in in My Chrome or in a browser without a window) |
| `GET /phone`, `POST /phone/pairing`                                             | the addresses a phone reaches Dot at (`[server] reach`); a new pairing code (item 267) |
| `POST /phone/pair` (public; `{"code": "7KQ2-M9XD"}`)                            | the access token for a good pairing code, once (403 when it is wrong, used or out of date) |
| `POST /desktop/permissions`                                                     | make macOS ask for the desktop permissions the daemon lacks  |

`POST /runs`, `POST /runs/{id}/steer` and `POST /threads/{id}/queue` take a `key` of the caller's own (16 to 64 letters,
digits and dashes), which the pages send with each new message. The same message sent again with its key is not done
twice: it is answered with the run its first sending started or steered, or with the chat's queue; 409 while its first
sending is under way, for two minutes at most. A key is kept for a day.

Errors come as `{"message": …, "context": {…}}`, those of the web framework itself (a path that does not exist, a method
a path does not take) included; an error Dot did not expect is a 500 whose context names its kind and its words, and the
log keeps its traceback. Invalid input is a 422 with the validation errors in the context.

## The desktop shell

`desktop/` is a native Mac window around the web UI, built with Tauri 2: a few megabytes, the one web UI, and a
menu bar item. The daemon stays owned by launchd (`dotx service install`); the shell connects to it and never runs
the agent itself.

- **One interface.** The window is the web UI at the size the screenshot tour reviews (1280 × 860, down to a
  phone's 390 wide). The system draws only the traffic lights over the page, on the centre line of the top bars with
  room above and below them; the page keeps room for them in the sidebar's head, as tall as the top bar so that its
  button stands on the same line, or in the top bar while the sidebar is away, and the window drags by those bars.
  The themes, the colour scheme and the density are the web UI's own settings.
- **Signed in by itself.** The shell reads the daemon's address from `config.toml` (`[server]`, or the defaults without
  that file) and the token from the token file under the home directory, and opens the UI with the token in the
  fragment, never typed, once the daemon has proven that it holds that token (see the server above; each poll checks it
  again before it sends the token). While the daemon is down, the window shows Dot's wordmark and the one command that
  starts it, and opens the UI by itself when the daemon answers. When the shell could not read one of those files, that
  page and the menu bar item's tip say which and why instead, and the shell guesses no address: a `config.toml` that is
  not valid TOML, with the line and column of the fault; a file it may not read, in the system's words; no token file
  yet, which the daemon makes the first time it starts. The window leaves the UI for that page only after ten polls in
  a row go unanswered (item 200): a restart of the daemon, a few seconds, and one slow answer of a busy daemon leave the
  open page, which says that it reconnects and reads again what changed once the daemon is back.
- **The menu bar item** is Dot's mark in the state of the whole of Dot (looking at you at rest, glancing up while a
run works, a blue pastille while something waits for you) with the count of what waits for you beside it, read from
`GET /api/activity/count` every second. A click opens the window; the menu has Open Dot, New chat and Quit. Closing the
window keeps the shell in the menu bar.
- **⌘⇧Space** anywhere brings the window up on New chat, with the cursor in the box. When another app holds the
  shortcut, the shell starts without it and its menu says so; New chat stays in the menu.
- **Links** that a page opens in a new window, such as the links of a model's answer, open in the default browser;
  the shell opens no window of its own for them, and only web addresses (`http`, `https`).
- **Notifications** are the system's: the web UI sends them through the shell (Settings › General › Notifications,
  the same switch). A click brings Dot to the front, where Waiting for you leads to the run.
- **Only Dot's pages call the shell.** The shell's own pages and the pages of the daemon's address may call the shell,
  to send a notification or drag the window: the address it reads from `config.toml`, and any it read there before
  since it started. A page of any other address, another port of the Mac among them, gets nothing from it.
- **Files** drop into the composer as in the browser.

Build it on your Mac with the Rust toolchain and Node installed:

```sh
cd desktop
npm install
npm run build      # desktop/src-tauri/target/release/bundle/macos/Dot.app (and a .dmg)
npm run dev        # a debug window with live reload of the shell
```

The build is signed ad hoc; a build other Macs open without a warning needs an Apple developer account
(`bundle.macOS.signingIdentity` in `desktop/src-tauri/tauri.conf.json`). The icons come from Dot's mark:
`uv run python desktop/icons/render.py` draws the favicon, the app icon (the mark lit as a ball on a dark tile), the
three menu bar icons and the web app's icons, and `npm run icons` makes the bundle's set from the app icon.

## Dot on the phone

The web UI is a progressive web app: open it on the phone, add it to the home screen, and it opens on Activity with
the sidebar's sections, the chats and the run pages at a phone width. Its colours follow the light or dark appearance
through CSS `light-dark()`, so it needs iOS 17.5 or later (Safari 17.5; Chrome 123 and Firefox 120 on a computer). It
needs to reach the Mac, and a push needs a secure address, so the way there is your own network:

1. The daemon stays on the loopback address. With Tailscale on the Mac and the phone, `tailscale serve --bg 8765`
   presents it as `https://<mac>.<tailnet>.ts.net`, with a certificate of its own, to your tailnet only.
2. Put that name in `[server] reach = ["<mac>.<tailnet>.ts.net"]` and restart the daemon: a request with that `Host`,
   and a page from `https://` that name, is served like a local one. `dotx url` then prints the signed-in address for
   the phone too (the token stays in the fragment). Settings › Phone lists the address (item 267).
3. On the phone, Settings › General › Notifications on: the browser subscribes to the daemon's web push, and from then
   on the daemon tells the phone what the desktop notifications say, in the same words, page open or closed, and while
   you look at that run on the phone too: the rule that keeps a notification quiet while you look at its run holds for
   the notifications of a page, not for a push; a tap opens what it tells of. The phone also hears what no page can, as
   it happens before a page is back: the daemon started again after it did not stop cleanly (it was killed, crashed or
   ended with an error; it leaves `<home>/daemon.running` while it runs), with how many runs that cut off. The daemon
   signs the pushes with a key pair it makes on first start (`<home>/vapid-key.pem`); subscriptions live in the store
   and go when their push service says they are gone. Settings › General › Notifications off drops the subscription.

On a phone the page holds still (item 275). Every page fits the screen's width: a long link, path or word breaks, and
code and tables scroll in their own frame. No text field is under 16 px, so iOS never zooms into one; a double tap does
not zoom, and the page does not stretch past its ends. The app added to the home screen runs to the screen's edges, and
the bar, the sidebar, the composer and the side panel keep out of the rounded corners, the notch and the home
indicator. The bar stays at the top of what is seen, also while the keyboard is up.

An app added to the home screen keeps storage of its own, apart from Safari's (iOS does so), so it has none of the token
that the signed-in address gave Safari, and its sign-in screen asks for a pairing code instead (item 267): Settings ›
Phone, on the Mac or in Safari where Dot is open, shows one with **Pair a phone**, eight characters in two groups
(Crockford's base32, typed in any case, with or without the dash), good for ten minutes and for one use; a new code
takes the place of the one before, and five wrong tries use a code up. The app trades it for the token (`POST
/api/phone/pair`, the one route besides the health check and the proof that takes no token; the Host and Origin checks
hold for it as for any other) and proves the daemon with it as with any token. The daemon is not proven before a code
goes, so the screen sends nothing that cannot be a code: a token pasted into the code's field stays on the phone. A code
is as good as the token while it lasts: whoever reads it and reaches the port first is let in, so it is asked for when
the phone is at hand, and a phone that the code then refuses means that someone else used it. `POST /api/phone/pairing`
gives a code, `GET /api/phone` the addresses. Neither the code nor the token is logged. The phone reaches Dot while the
Mac is on and awake.

The service worker (`sw.js`) keeps the shell files as a fallback for when the Mac is out of reach and caches nothing
under `/api`. A page loaded from them while the daemon is out of reach says so and tries again by itself (item 200):
after 2 seconds, then twice as long each time up to 30, and at once when the browser is back on the network or the page
back in view, until the daemon answers. Without Tailscale, the same works on the Mac itself (`localhost` counts as
secure) and on nothing else.

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

Cookies are encrypted with a key, and a profile cannot be read with another key than the one it was written with. A
profile that Dot made uses Playwright's mock keychain in the sign-in window and in the automated sessions alike, so no
Keychain item is involved; the profile of an import uses the system keychain in both (`dotx doctor` shows which). A
sign-in made with an older version of `dotx browser login`, which used the real key, has to be made again.

Dot's own Chrome loads the extensions installed in its profile: Playwright starts Chrome with `--disable-extensions`,
which Dot leaves out (item 264). Install one from the Chrome Web Store in the window of `dotx browser login`; it stays
in the profile and loads at every start of Dot's Chrome.

**`attach`** works in new tabs of the Chrome you already have open, signed in as you are, and starts no Chrome of its
own. It needs Chrome 144 or later and its switch "Allow remote debugging for this browser instance" at
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
- A tab that Dot opens is opened behind the tab you work in (with `raise_window` on it becomes the active tab of your
  window), and Chrome may throttle a tab in the background.
- Downloads go to the download folder of Chrome, not to Dot's `downloads/`.
- Dot closes the tabs it opened when they have been idle for `tab_idle_s`. If Dot itself crashes they stay open.
- Calls that arrive while Dot waits for the click queue behind it, and each may in turn wait up to `attach_timeout_s` if
  you do not click.
- Only the stable Google Chrome on its default data directory is supported. `channel`, `headless` and `raise_window`
  apply to `own` only.

The mode can also switch without a restart: Settings › Browser in the web UI, or `POST /api/browser/mode` with
`{"mode": "own"}` or `{"mode": "attach"}`. The live switch runs the same one-blank-tab proof; it is refused with 409
while runs are active, and a failed proof leaves the session in the Chrome it had (422). When your Chrome is closed,
the live switch opens it first, as a click on its icon does, and waits up to 30 seconds for it to start and 10 more for
it to listen; with remote debugging off, the switch then says how to turn it on (item 261). While the switch is under
way, Settings says what it waits for, and after a failed one it shows how Chrome stands. Runs, campaigns and
`dotx browser attach` never open Chrome; `dotx browser attach` and `dotx browser detach` only write the mode, which the
service takes up on its next start. The same tab's switch "Bring
Chrome to the front while working" is `POST /api/browser/raise` with `{"raise_window": true}` or `false`: it takes
effect at the next browser call, Chrome is not restarted, and the value is written to `config.toml` the same way.

`[browser] debug_port` (0, none, by default; else from 1024 to 65535) is a port of this Mac on which Dot's own Chrome
also takes DevTools connections, besides Playwright's pipe (item 265): Chrome listens on `127.0.0.1:<port>`, and a run
whose browser is Dot's own and that has a tool to run scripts (`shell`, `terminal_send`) is told the address in its
instructions, so a script it writes can drive Chrome with the Chrome DevTools Protocol in tabs of its own once one of
the run's browser calls has started Chrome. The run is told to connect with Playwright's `connect_over_cdp` and
`no_defaults=True` (`noDefaults` in Node), which leaves Dot's downloads as they are, or with Puppeteer's `connect` and
`defaultViewport: null`. Dot's Chrome does not start while another program holds the port, with an error that names
the port; a script reaches whatever listens on the port, which is Dot's Chrome while it runs. Settings › Browser sets it
(`POST /api/browser/debug-port`, refused while runs are active and when the port is taken): every browser session is
checked before any changes, Dot's Chrome, open on another port, is closed and starts on the new one with the next call
that needs it, and the value is written to `config.toml`. The port asks for no token: while Dot's Chrome runs, any
program on this Mac can connect to it and drive Chrome with the sign-ins of Dot's profile.

Both modes share what follows.

- The page is observed as an accessibility snapshot in which every element has a ref (`e12`). It covers the viewport
  plus `viewport_margin_px`, and `browser_find`, `browser_links` and `browser_read_text` reach what is outside.
- A collapsed dropdown lists its options on its own line, for example
  `combobox "Shipping" [ref=e3]: options "Standard (3-5 days)" [selected], "Express (next day)"`. `select` takes the
  label or the value of one of them, written as listed; a choice that is not there is refused at once with the options
  that are, the closest first, instead of waiting for the timeout. An option that a script adds within a second of the
  call is still found. A very long list is cut with the number of options left out and the current choice.
- `browser_act` runs up to `max_batch_steps` steps in one call (5 by default; a longer batch is cut to the first ones
  and the result says how many remain, so the call is not lost): `click`, `double_click`, `right_click`, `hover`,
  `fill`, `type`, `insert`, `select`, `press`, `check`, `uncheck`, `scroll_into_view`, `drag` and `click_at`. After a
  step Dot waits until the page has settled (no DOM change for `settle_quiet_ms`, 60 ms, at most `settle_max_ms`) and
  reports popups, dialogs and downloads that appeared. The result is the part of the page that changed, with the refs of
  the last snapshot still valid; a page that did not change comes back as one line that says so. A tab that closes under
  a batch (a sign-in callback that did its job, a tab the user closed) stops the batch without a failed step: the result
  says the page closed it and shows the tab that is now current. A popup that opens and closes itself before the model
  could see it is a note on the page that opened it.
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
- `browser_eval` runs JavaScript in the page of the run's own tab, in either mode, and gives back its value as JSON: an
  expression (`document.title`), a function, or a promise it awaits (`timeout_s`, 30 seconds by default, at most 600).
  It reads what the snapshot shortens (a whole list, a table, the data behind a page) and does in one call what would
  take many steps. A script that throws fails the call with its message; one still running when the wait ends goes on in
  the page, and the call says so. The page is settled after every script as after a step, so a script that leads the tab
  elsewhere lets it land, also when the page answers after the script's value came back (item 270), and says where, and
  the URL rules hold after it as after a step: a forbidden address resets the tab and fails the call. The value is
  fenced as the page's text and makes the run untrusted. As an action it is a click of medium risk on the tab's page:
  the aggressive and balanced presets allow it, the cautious preset asks, a research run does not have it and a `yolo`
  run does it without asking. What the script does inside the page is not checked or paced action by action, as
  `browser_act`'s steps are. It takes the place of AppleScript's `execute javascript`, which lands in the front window
  of whichever Chrome macOS picks when several run (item 263).
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
  cloud-metadata hosts. Dot's own interface is never opened, whatever the lists say: this Mac on `[server] port` in any
  spelling (`localhost`, `127.0.0.1`, `[::1]` and the like), and the names of `[server] reach`; your Chrome may be
  signed in there. A server of this Mac on another port opens as any site.
- `raise_window = false` (the default) keeps the window in the background while a run works: every tab Dot opens, a
  run's or a quiet read's, is created in the background (a new tab opened the usual way brings Chrome to the front of
  the Mac), and it still paints the page under Dot's computer. macOS brings a newly started Chrome to the front, and
  Chrome takes the front itself when a page opens a tab of its own (a link to a new tab, `window.open`): the app that
  had the focus is brought back right after, as long as the program that runs Dot has Accessibility. Switching tabs
  does not raise the window. With `true` the window comes to the front at the start, with each new tab and on each tab
  switch. Take over brings the tab to the front either way.
- `headless = true` hides the window. Starting Chrome and loading the first page took about 2 seconds with it on an
  Apple-silicon Mac; later calls take well under a second. When Chrome crashes or the connection to it is lost, the
  next call starts it again.
- Chrome runs with its sandbox on. Playwright would start it with `--no-sandbox` unless told otherwise, which makes
  Chrome show a warning bar and weakens the browser that opens untrusted pages.
- Dot's Chrome is the same application as yours, only with another profile. To give it an application of its own, run
  `uv run playwright install chromium` and set `channel = "chromium"`.

## Computer use

The desktop tools see and drive any application through the accessibility tree and, where an application has none,
through screenshots and coordinates. macOS must grant the app that runs Dot three permissions: **Accessibility**,
**Input events** and **Screen Recording**. `dotx desktop check` shows which it has and `dotx desktop setup` asks macOS
for the missing ones and opens their Settings panes.

- `desktop_apps` lists the running applications and `desktop_open_app` brings one to the front, starting it when it is
not running: by name (Terminal), bundle id (com.apple.Terminal) or bundle path (/Applications/Terminal.app). A path to
anything else (a binary) is refused at once with those three forms, instead of being retried against Launch Services.
- `desktop_snapshot` lists an application's window as elements with a role, a name, a value, a state, a position and a
  ref (`a12`). A second view lists only what changed; `query` finds elements anywhere in the application and
  `scope = menu_bar` lists the menu bar. `desktop_read` reads one element's full text.
- `desktop_act` runs up to `max_batch_steps` steps in one call (25 by default; a longer batch is cut the same way):
  `click`, `double_click`, `right_click`, `hover`, `press`,
  `focus`, `set_value`, `type`, `paste`, `key`, `scroll`, `drag`, `menu`, `wait` and `activate`. A step names an element
  by its ref or a point by its coordinates. `press`, `focus` and `set_value` work through accessibility without moving
  the pointer; `click`, `hover`, `scroll` and `drag` are real pointer events; `type`, `paste` and `key` are keyboard
  events. A `type` text longer than `paste_threshold_chars` (40) without a line break or a tab goes in through the
  clipboard, which is put back afterwards, unless the field is secure, is for a credential or a payment, or cannot be
  read, or the text looks like a key. After steps that changed something, the window is read once the application has
  announced no change for `settle_quiet_ms` (120 ms), at most after `settle_ms` (350 ms); a change it makes later
  without announcing anything in between shows in the next view.
- In a terminal application (Terminal, iTerm2, Ghostty and the like) text and keys are shell input. Dot keeps the
  command line it typed there since the line was last empty and judges it whole, as a `shell` command, when Return or
  a Ctrl- key that may run it goes in, so a command typed in parts is judged as one, from the home folder. After a
  click, a focus, a menu, a drag, a Cmd shortcut or an arrow key the line cannot be told, and the next Return asks, as
  the first one in a terminal Dot has not typed into does; Ctrl-C starts a line Dot knows again.
- `desktop_screenshot` captures the screen with ScreenCaptureKit as a JPEG of at most `screenshot_max_edge` pixels
  (1366 by default: about 1 600 tokens on the hosted providers, against about 2 500 at 1920).
- `desktop.keep_awake` keeps the display awake while Dot works. Password managers are refused in every mode, and so
  are secure fields except in a `yolo` run (see [Safety](#safety)).
- `dotx desktop apps`, `dotx desktop snapshot [app] [--scope …] [--query …]` and `dotx desktop screenshot PATH` show
  what the agent would see.

## Memory

Dot keeps short notes between runs: facts about you, your machine and how you like things done. `memory_save`,
`memory_search` and `memory_forget` are tools of the agent; you manage the same notes with `dotx memory` and in the web
UI, where Settings › Memory lists the notes changed last first, 30 at a time with **Load more** for the next, in a
box that keeps its size and scrolls inside past its first notes (item 218); forgetting, correcting or adding a note
keeps the notes read and the list where it was, and a correction open on another note stays open (item 204).

```bash
dotx memory add --tag core "I prefer answers in Turkish, with code and commands in English"
dotx memory search --limit 5 how do I like my answers
dotx memory list
dotx memory forget NOTE_ID
dotx memory reindex      # embed every note again, for example after changing [memory] embed_model
```

- A run starts with the notes tagged `core` (newest first), then the notes you wrote yourself (the ones closest to the
  goal first), then the agent's notes close enough to the goal and the earlier goals of the conversation
  (`inject_min_similarity`; by fit and score), as many as fit a budget of tokens: `inject_share` (2%) of the model's
  context window, at most `inject_tokens` (1500; the cap alone when the provider does not say the window). Each of
  these groups first gets its own share, a quarter of the budget, and what is left then goes to them in the same order,
  so many notes of yours do not crowd out the agent's lessons. A note that does not fit is passed over, a shorter one
  after it may still fit, and `memory_search` finds the rest. The run's
  timeline says how many notes it recalled and the tokens they took.
- Use decides what is recalled: a note handed to a run counts as used, and a search lifts the agent's notes a run was
  handed lately (a use a moment ago is worth a second place in one ranking; the lift halves every `use_half_life_days`,
  30), so the notes that help overtake the ones first written. An agent's note that no run was handed for `rest_days`
  (90) rests: the Memory page lists it under "Resting", a search still finds it, but runs no longer start with it on
  its own. Your notes and the `core` ones never rest. Under each note the page says how the runs it was handed to
  ended ("Handed to 5 runs, 4 done"); every use is recorded with the run's end, which `dotx stats` reads.
- What Dot knows about you opens the memory part of every prompt: a short section the nightly reflection keeps (your
  language, hours, tools and sites, the ways you ask, the limits you set), rewritten each night from its previous
  version, your notes and the day's runs. The Memory page shows it first; the lines you write under it are yours, kept
  as they are, marked "(you said)" in the prompt, never rewritten, and the reflection never contradicts them
  (`GET`/`PUT /api/memories/owner`).
- The score decides too: over the last `score_days` (30), a note's balance is the runs handed it that ended done less
  the ones that failed. Once `score_min_uses` (3) runs were handed it, the balance lifts or lowers the note in a search
  as much as a fresh use would, and a negative balance rests it at once, before the nightly reflection retires it.
- A note saved or changed while a run works reaches that run at its next step, when the run would be handed it at its
  start: not another agent's lesson, not a resting note, and only as many as fit the budget of the starting notes (the
  run is told how many more changed; `memory_search` finds them). A change of tags alone, such as the nightly `rest` or
  merged tags, is no change: the note keeps its date, so an old fact retagged at night does not pass for newer than its
  correction.
- Two tags are the nightly reflection's: `journal` on the day's note, `rest` on a lesson no run was handed for sixty
  days, which searches skip (the list and the page still show it; take the tag off to wake it).
- `memory_update` lets the agent correct a note in place, and the note is then the agent's, even one you wrote: the
  prompt no longer shows the model's words as yours. You edit one on the Memory page or with `PATCH /api/memories/{id}`,
  and it keeps its author. The page also shows who wrote each note and how often runs used it.
- Search combines SQLite full-text search (BM25) with cosine similarity of embeddings and fuses both rankings.
  `embed_enabled = false` searches by words only. An answer of Ollama that says its model runner broke off is asked
  again, as a broken connection is; when the embedding service still fails at the start of a run, the run recalls its
  notes and lists its skills by their words alone and goes on, and the timeline and the terminal say so in a warning
  line (`embedding_failed`, item 256). The word search does not mind case, accents or the Turkish dotted and dotless i
  (`toplanti` finds `Toplantı`), and a word of five letters or more also finds the words that start with its first
  five letters, so Turkish endings meet the root (`toplantıda`, `toplantılar`).
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
`[scheduler] timezone`, which defaults to the zone `TZ` names when it is set, else the zone of this Mac). Each firing
starts a run in a new chat, so the goal has to say everything; its first message carries, in front of the goal, the
dated final answers of the schedule's last ended runs (`[scheduler] history_runs`, 3; 0 for none; at most
`history_chars` characters, each answer cut at 600), marked as what the schedule found before and not as instructions,
so the run builds on them instead of doing the same work again (item 257). The chat shows the goal alone, and the run's
reviewer takes only the goal as your words; a one-time schedule that fires late says when it was due in that first
message too. With `same_chat` a firing goes on in the chat of the one before (a new one when you deleted it), where the
run reads their conversation instead of the digest. Either way it reads earlier runs' words without a message of
yours, so it starts tainted (asking before outward-facing actions) when one of the runs it reads ended so. The digest
counts the firings since item 257: a firing from before names no schedule. The agent can create schedules too
(`schedule_create`). The scheduler also fires the nightly reflection at `[agent] reflection_time` (see Reflection
above); it is not a schedule and does not count against `max_schedules`.

On Settings › Schedules a schedule is created and edited whole: its name, goal, repetition (cron or one moment), time
zone, kind of run (task, chat, research), model, reasoning level, approval mode, goal mode, the earlier runs it knows,
whether it keeps its chat and whether it is on (`history_runs` and `same_chat` in `POST` and `PATCH /api/schedules`,
left out for the configured ones). Options
left on "default" take the configured value when the schedule fires. A schedule the agent creates always uses the
defaults: a run cannot schedule itself into another approval mode.

- Schedules fire only while the daemon runs and the Mac is awake; `dotx service install` keeps the daemon running.
- A firing is at most once. One that is overdue by more than `misfire_grace_s` (the daemon was off, or the Mac slept)
  is skipped and waits for the next; you are told, as you are when a firing's run cannot start (see the notifications
  under the web UI). A one-time schedule has no next time and is never dropped: overdue, it fires late, and its run
  reads first when it was due and when it starts.
- The scheduler goes on past a failure: the schedules, the campaigns and the nightly reflection are each looked at on
  their own at every tick, one that fails (the store, a campaign's start) is logged, and the next tick tries again. Only
  20 failed ticks in a row (five minutes at the default `tick_s`) end the scheduler, and the daemon with it, with an
  error, so that schedules never stop firing unseen; the service that `dotx service install` sets up starts it again.
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.

## Agents

An agent is a profile you make for a kind of work: a name, a role in one line, instructions, and bounds for its runs
(a model, a reasoning level, approvals, the tools it may use; each left out takes the configured one, no tool picked
means every tool) and a look: a shape, a rest expression and a colour of the character (see the design system), picked
in the editor's Look field with a moving preview, worn as a still face wherever the agent shows, and until you choose
one the shape and colour its id's hue gives it (`PUT /api/agents/{id}/look` sets the look alone; Dot's own is the
"Dot's look" card of Settings › General, kept by the daemon for every browser). **Agents** in the sidebar lists Dot
first and then your agents, each with its newest chat; **New agent** opens the editor; an agent's page greets with
its face and role, takes a message for it ("Message Scout") and lists its chats, with Edit and Delete in the bar. The
home composer offers an Agent menu once an agent exists, Dot by default. A run of an agent opens its system prompt
with the agent's own section ("You are Scout, an agent the owner made: ..." and your instructions), ahead of Dot's
rules, which still hold; its tools are the ones the agent names plus finishing, asking you and turning tool groups on.
The run's page wears the agent's face and name in the bar with a chip to its page; its rows on the Activity page and
in the sidebar wear the face too. Deleting an agent leaves its chats, its lessons and its learned skills to Dot.

An agent learns for itself. A lesson drawn from an agent's run is the agent's own (the note carries its `agent_id`), and
so is a skill its run was distilled into (`agent_id` in `playbook.json`); Dot's lessons and skills are everyone's. An
agent's run is handed its own lessons first, then Dot's notes, never another agent's, at its start and through
`memory_search` alike; it is offered its own learned skills and Dot's, and another agent's only once you mark that skill
shared (Settings › Skills or the agent's page, `PUT /api/skills/learned/{name}` with `shared`). A skill a run is not
offered stays out of its reach: the `skill` tool reads only one the run is offered, and asked for another by its name,
it says that no skill offered to the run is called so and names only those that are. The scores the agent ranks its
notes by, and the ones its page shows, count its own runs alone. An agent's page lists what it learned ("What it
learned": its lessons with how its runs fared after each, with Forget, and its skills with Share and Drop), and the
Memory page marks a lesson with its agent's name. A note a run saves with `memory_save` is Dot's, for every run.

A run can make a teammate itself (item 99). The `create_agent` tool (a name, a role, instructions, the tools it may
use, a model if one is wanted) makes an agent as the Agents page does, under your approvals (the `persistence` flag,
like saving a note: `ask` asks once the run has read untrusted content, `yolo` makes it); the agent lasts after the
run, its record keeps the run that made it, and the Agents page marks it "Made by Dot". The prompt's Team section
tells a run with no teammate that it may make one for a kind of work that recurs and deserves a role of its own,
never for a single step; `message_agent` is offered whenever `create_agent` is, so a teammate made mid-run can be
messaged at once. Every step, a run that may message the team learns of the agents that joined since it started
(made by it, by you or by another run) through a note with their names and roles. Delegated runs and the runs that
answer a message make no agents.

Agents work together through messages. A run (Dot's or an agent's) sends one with the `message_agent` tool, naming the
agent and the message; the agent answers in a run of its own (its instructions, its tools, its model, and the stricter
of its approvals and the sender's, so an agent set to `yolo` asks when a run that asks messages it) that cannot see the
sender's conversation, and the reply comes back as the tool's result, the way a delegated run's report does. The prompt
lists the team by name and role under "Team" whenever there is another agent to message; an agent does not message
itself, and an answering run messages no one further. The sender's chat shows the exchange as a delegated row wearing
the agent's face. **Team** (a button on the Agents page, `#/team`) is today's thread of these messages: who to whom, the
message, the reply, the time and the two runs; an agent's page lists the messages it sent or received. `GET /api/team`
gives the thread. Three `[agent]` settings follow the harness study arXiv 2609.20804: `context_elide_share` (0.6, past
which older tool output is shortened, once, to 700 characters; a step's request to Anthropic also marks the end of the
part of the run the next step sends unchanged, before the newest tool results, as Anthropic looks for a cached prefix
only 20 blocks back from a mark, so the step after a shortening still reads the run before it from the provider's
cache), `context_summarise_share` (0.85 of the configured budget, past which the older part of the run is summarised)
and `plan_each_turn` (on: the open plan is appended to the system message of every step). The API: `GET /api/agents`
(Dot first, each agent with its newest run), `POST /api/agents`, `GET`/`PUT`/`DELETE /api/agents/{id}` (Dot is neither
changed nor deleted), `GET /api/tools` (the names an agent may be given), and `POST /api/runs` with `agent_id` (not with
`standing`: a campaign is Dot's own, and the two together are refused with a 422).

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
A campaign whose first session cannot start is not kept.

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
evidence of its steps, what the session's own calls measured and the session's evidence. Refused, the call fails with
the checker's reason and the session goes on (one the checker finds out of reach asks you how to go on); accepted, the
campaign is achieved and you are told (the notifications under the web UI and the phone's pushes), as you are when the
emergency stop or `campaign_max_sessions` stops it.
- **Only you stop it.** Campaigns, in the sidebar, lists them; a campaign's page shows its targets, the questions
  waiting for you, its ledger and its sessions, with Stop (which also stops a session working then), Go on and Mark
  achieved. The emergency stop (its button, or the pointer in the top-left corner during a desktop step) stops the
  campaign of the session it ends, and while the stop is engaged no session starts. `dotx campaign` changes the
  campaign without the daemon, so a session working then goes on until it ends (Stop on its run page ends it).
  `campaign_max_sessions` (0: no limit) stops a campaign after that many sessions; Go on gives it one more.
- **Posts, judged before they are answered.** With `TYPESAFE_API_KEY` saved, a session also has three tools on
  TypeSafe's typed judgments for a campaign on X or another feed. `rank_posts` asks three yes/no questions about every
  post it could answer, in one request (on the account's topic; written by a person, not a bot, a brand or spam; worth a
  reply that adds something) and combines them with weights the ledger holds: the first ranking writes them as a note
  with equal values, and a `campaign_note` entry with the numbers `w_topic`, `w_person` and `w_reply` changes them
  without asking TypeSafe again; every ranking's scores are kept in the session's timeline (`posts_ranked`).
  `pick_draft` asks one choice over two to ten drafts of a reply. `check_draft` asks whether a draft is off topic,
  repeats one of the account's earlier posts or breaks a rule the goal names, and refuses it when one of those reaches
  `[judge] draft_check_threshold` (0.5, provisional until it is set on a labeled sample of your own feed). None of them
  posts; the model keeps the decision.

**The browser of a campaign.** A campaign works in the Chrome you have open, signed in as you are (`attach`), unless you
start it with `--browser own` (or `browser` in the API, which goes with `standing` only), and `[agent] campaign_browser`
sets the default; runs you start by hand keep `[browser] mode`. A campaign that asks for your Chrome is refused at its
start while that Chrome is closed or does not allow the connection (`dotx browser attach` sets it up), so its sessions
do not fail one after the other. The brief of every session says which Chrome it works in, and so do
`dotx campaign show` and the campaign's page.

Every session runs with the model, reasoning level and approval mode the campaign started with, and without goal mode:
the campaign's own check takes its place, so starting one with goal mode on is refused. A run's limits
(`run_max_minutes`, `run_max_tokens`) apply to each session. Sessions run only while the daemon runs; one the daemon cut
off is not resumed as a run: the next session starts once the daemon is back (after `campaign_gap_minutes` when the
daemon died while it worked, so that a session that brings it down does not run again and again), and its brief says the
last one was cut off. A session whose `dotx` command died (the first one, from `dotx run --standing`) is closed by the
daemon within half a minute, and the next one starts `campaign_gap_minutes` after that. The threads of the sessions
after the first are not in the chat list; the campaign's page links them. What you write in the campaign's conversation,
and your answers to their questions, are instructions of the sessions after it.

## Connect apps

**Connect apps**, the pill in the sidebar's foot (item 278), lists the applications Dot works with, each with what Dot
does with it, where it stands and the one step that sets it up; **Check again** reads them all anew.

- **On this Mac**: Mail, Calendar, Notes, Reminders, Messages, Contacts, Excel, Word, PowerPoint and Outlook, which Dot
  drives with AppleScript. macOS lets an app send Apple events to another only once you have said yes to its question
  (Privacy & Security › Automation), so a row says **Allowed**, **Not asked yet**, **Refused**, **Not checked** (macOS
  tells about an application only while it runs) or **Not installed**. **Allow** asks macOS, which shows its question
  on the Mac; an application that is not open is opened hidden first, without coming to the front. The page waits a
  minute for your answer, then says that the question waits on the Mac. After a no macOS does not ask again: **Open
  System Settings** opens the Automation pane, and the row says what to switch on there, under the app that runs Dot
  (Terminal, iTerm, Claude, or the Dot service).
- **Your files**: OneDrive, whose folders (`~/Library/CloudStorage/OneDrive-*`) Dot's `onedrive` skill works in. It
  says **Syncing** once a folder of it is there; else **Open** opens OneDrive to sign in.
- **On the web**: Google, Microsoft 365, GitHub, Slack, Notion, LinkedIn, X and Instagram, used in Dot's own browser.
  A row says **Signed in** when that browser holds a cookie the site keeps only for a signed-in visitor, read by host
  and name and never by value: from Chrome while it runs, else from a copy of its profile's cookie file. **Sign in**
  opens the sign-in page in a tab of Dot's browser on the Mac. With My Chrome (Settings › Browser) Dot works in your own
  Chrome, where you are signed in as you always are; a browser without a window (`headless = true` under `[browser]`)
  has none to sign in in, which the section says once.

A sign-in is told by its cookie alone: a session the site ended stays **Signed in** until its cookie expires.

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
the next call. A server that is enabled but cannot start (its command, a secret or its working directory is missing, it
quits or does not answer in time, its `allow` list names a tool it does not offer, two of its tools would share a name,
or the input schema of one of its tools cannot be handed to a model: a reference that leads nowhere, or a tree whose
nodes hold nodes, which `allow` can leave out) is left out, and Dot starts without its tools: Settings › System names it
with the reason, `dotx run`, `dotx resume` and `dotx chat` say so before they begin, and `dotx doctor` warns of a
missing command or secret. It is tried again when Dot starts again; `enabled = false` stops trying it. Plugins add their
servers too (see [Skills and plugins](#skills-and-plugins)); their names become `<plugin>-<server>`. MCP results are
untrusted content, and the stderr of a stdio server goes to `<logs>/mcp-<name>.log`. A tool that its server declares
read-only (`readOnlyHint`) is taken at its word and runs as a read, with no flag; any other counts as one that may
change things (`destructive`) and send to the world (`external_send`), unless its server says it does not.

## Skills and plugins

A skill is a folder of instructions for one kind of work, in the Agent Skills layout: `<name>/SKILL.md` with a YAML
frontmatter (`name`, `description`) and optional `scripts/`, `references/` and `assets/`. Every model is given, in its
instructions, the `name: description` line of each skill its run is offered, and reads the rest with the `skill` tool
when a task matches; instructions longer than one tool result come in parts, each saying where the next one starts, so
none is cut out of the middle. A run offered more than `listed` under `[skills]` (20) is given those that fit its goal
and the goals of the latest earlier runs of its thread best, as its notes are chosen, the best fit first and at most
that many, and is told how many more there are: `find_help` searches them all, and `skill` reads one by its name. A
skill fits by the words it shares with the goal and, with the memory's embeddings on, by how close it is to the goal in
meaning (at least `min_similarity` under `[memory]`), the two rankings fused as the notes' are; bge-m3 reads Turkish as
well as English, so a goal in one language is given the skills described in the other, while by words alone it shares
none with them. Each skill is embedded once and kept while Dot runs; when the skills must be picked and the embedding
service fails, the run fails to start, as it does when its notes cannot be searched. A skill that shares only some of
the goal's words is kept, as a goal can ask for more than one kind of work. Dot always looks in `<home>/skills` and in
the `skills/` folder of every plugin; `[skills] dirs` lists more directories (each holds skill folders; `~` expands).
Skills written for Claude Code load unchanged: Dot reads only `name` and `description` of the frontmatter, so other keys
(a date or a nested map under `metadata`, a list of `allowed-tools`) do not leave a skill out. To use your own, put
`dirs = ["~/.claude/skills"]` under `[skills]` in your `config.toml`; Dot reads nothing there until you do. An entry may
also be one skill folder (`~/.claude/skills/<name>`), to take some skills only: `~/.claude/skills` can hold skills
written for Claude Code's own tools, such as the superpowers bundle or the skills synced from claude.ai. The skills you
wrote for Claude Code are then the playbook a campaign can work from.

Dot comes with three skills of its own, for work that has to know this Mac: `excel` (reading and changing a workbook
with openpyxl, getting the results of its formulas from LibreOffice or Excel, which openpyxl does not compute, and
driving Excel with AppleScript), `word` (changing the text of a Word document with python-docx without losing its
formatting, and driving Word with AppleScript) and `onedrive` (where OneDrive keeps the folders it syncs,
`~/Library/CloudStorage/OneDrive-*`, the files it keeps only online, and what a change there does in the cloud). They
are read after every other skill, so a skill of yours with the same name is used instead of one of them, and
`builtin = false` under `[skills]` leaves them out. A skill's instructions name Dot's own Python, which has openpyxl,
python-docx and python-pptx, as `{{dot_python}}`: the `skill` tool puts the path of that Python there, quoted for the
shell. `dotx doctor` says which of Excel, Word and LibreOffice are installed, at which version, and counts the folders
OneDrive syncs by the kind of account (personal, work or school, shared libraries), never by their names, which hold the
organization's.

A plugin is a folder in the Claude Code layout: `.claude-plugin/plugin.json` (its name), `skills/<name>/SKILL.md` and
`.mcp.json` (`{"mcpServers": {...}}`; `${CLAUDE_PLUGIN_ROOT}` is the plugin folder). Their skills and MCP servers are
used; hooks, commands and agents are not. Dot always looks in `<home>/plugins`; `[plugins] dirs` lists more directories
(each holds plugins, or is one plugin itself; `~` expands). Plugins are read when Dot starts, skills at the start of
every run.

`dotx skills list` shows every plugin and skill found, and anything left out and why; `dotx doctor` has a row for each.

**Found on this Mac.** Dot also finds, read-only, the skills and MCP servers that the Mac's other agents have set up:
the skills of Claude Code (`~/.claude/skills`), of the plugins installed in Claude Code (each one's `installPath` in
`~/.claude/plugins/installed_plugins.json`, with its `skills/` and `.mcp.json`) and of Codex (`~/.codex/skills`), and
the MCP servers of Claude Code (those for every project, `mcpServers` in `~/.claude.json`; a project's own are not
read), Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`), VS Code (`Code/User/mcp.json`) and
Codex (`mcp_servers` in `~/.codex/config.toml`). Their skills are found as Dot finds its own: the skill folders of a
folder, and those of the folders in it a few levels down. Each is found once by its agent and its name, which is how an
import names it: a plugin installed for you and for a project is read from your install, the one Claude Code uses
outside a project. Only what names a thing is kept: a skill's name, description and folder, and a server's name, its
transport and what it reaches, the base name of the program it starts or the host of its URL, when that is a plain name.
A server's arguments, environment and headers, and the rest of its URL, can hold keys and tokens: they are never kept,
logged or shown, not even in the reason a server is left out. A skill is marked when Dot reads its folder, a server when
Dot has one under the name it would give it (`<plugin>-<server>` for a plugin's). What cannot be read is listed apart
with why: a file, a skill whose `SKILL.md` Dot cannot use, a skill named as one before it in the same agent's folders (a
second copy reached through a link is not counted), a plugin named as one of another marketplace before it, a server
that names no command or URL to use, whose command does not start with the name of a program (a URL, or a variable set
before it), whose URL names no host, or whose entry holds values of the wrong kind (told by their keys).
`dotx skills list` shows them after Dot's own, with `?` for any character a terminal would act on; Settings › Skills
shows them under "Found on this Mac", read apart from the learned skills; and `GET /api/skills/elsewhere` returns them.
Runs do not see them: Dot uses one only once you add it (below), or name its folder in `[skills] dirs` or
`[plugins] dirs`.

**Adding what is found.** Under Found on this Mac, Add to Dot asks first, then adds a skill or an MCP server to Dot;
Remove asks, then takes it back (`PUT /api/skills/elsewhere` with `kind`, `agent`, `name` and `added`;
`dotx skills list` marks what you added). A skill is copied into `<home>/skills/<name>`, without its hidden files and
with a hidden marker of where it came from, and every run is offered it at once. The copy is what you said yes to: it
does not follow the agent's later changes (remove it and add it again to take them), and a run outside `yolo` may read
it but never change it. Remove deletes that copy and nothing else. A server is added as a line of
`<home>/added-servers.json` that names its agent and its name, with a fingerprint of its entry (a SHA-256 hash) and
nothing it is started with; a run outside `yolo` may read that record but never change it. From Dot's next start
(`dotx service install` restarts the daemon), Dot reads the entry from that agent's file each time it starts and starts
the server as the agent does, with its command, arguments and environment, or its URL and headers, under the name Dot
gives it (lower case, `-` for `_` and spaces); its tools are offered and approved as those of an `[[mcp]]` server. An
entry that changed after you added it is not started, and the page says so until you remove the server and add it again:
a change made in the agent's file, by a run or by anyone else, never starts on your earlier yes. One that is gone from
its agent's file, or that Dot cannot start as it is written by then, is left out and logged too. What you added that its
agent no longer has is listed apart, under Added, no longer set up, with Remove; Dot keeps such a skill's copy until you
remove it. What cannot be added says why instead of offering the button: a skill whose name Dot has already, or that
Dot's skills folder holds; a skill folder that holds a link, something that is neither a file nor a folder, or more than
2000 files or 50 MB; a server of a plugin, which comes with its plugin (name the plugin's folder in `[plugins] dirs`); a
server whose name does not fit Dot's rule for server names (a lower-case letter, then lower-case letters, digits and
hyphens, at most 24 characters) or is taken, or that uses a variable its agent fills in: any `${...}` in its command,
arguments or URL, or one in its environment or headers other than `${NAME}`, which Dot looks up like an API key.

**Importing several at once.** Import from other apps, at the head of Settings › Skills, opens a checklist of what is
found that Dot can add: the skills by agent, each with its description, ticked at first, then the MCP servers, each
with its agent and what it starts or reaches, not ticked, as a server starts a program of this Mac; Choose all ticks
or clears every one, and a line says how many others Dot has already or cannot add (Found on this Mac says why). The
button is off until what the other agents set up is read and something of it can be added. Import N adds what is
ticked in one request (`POST /api/skills/elsewhere/import` with `items`, each `kind`, `agent` and `name`, at most 500
and each once), each as Add to Dot would, from one look at the other agents' files: what is added is said in a toast,
and what is not, with its reason, in one toast each, as when two agents have a skill of the same name and the first
is copied. Cancel adds nothing.

**Finding help.** Before it takes a hard way (writing a script or a program for the work, clicking through an
application or a site, trial and error in the shell), a run is told to call `find_help` with its need in a few plain
words, and to call it again once a way has failed twice. It searches the skills the run is offered, the tools of the MCP
servers that the run's mode allows and, when the run can use `apple_script`, the applications that AppleScript can
drive: those whose Info.plist names a scripting dictionary or turns AppleScript on, in `/Applications`,
`/System/Applications` (each with its `Utilities`), `/System/Library/CoreServices` and `~/Applications`, each told by
the commands and classes of its dictionary's own suites (the suites that many applications share are ranked, not
listed). It ranks them by the words they share with the need (BM25; case, accents, common words and plurals do not
matter; one that scores less than half as well as the best one is left out) and, with the memory's embeddings on, by how
close they are to the need in meaning, as the skills a run is given are, and lists the best eight, each with how to use
it: `skill` with its name, the MCP tool by its name, or `apple_script` with `tell application`, and `sdef` with the
application's path, which prints its whole dictionary. With `elsewhere = true` under `[skills]` (the default), it also
searches the skills and MCP servers found on this Mac that you can add, each told with how to ask you to add it: a run
never adds one itself. When the embedding service fails, it says so and lists what the words found. With nothing found,
it says what to try next. It changes nothing.

**Learned skills.** With `playbooks = true` in `[agent]` (the default), a task run of its own that ended done (its
answer checked, or its goal check found the outcome achieved) after at least `skill_min_steps` (4) acting steps (every
tool call but the ones that plan, ask, remember, consult, read a skill, find help or finish) is followed, once it has
ended, by one call of the review model that distils how it got there: from the acting steps (each tool call with its
arguments and the start of its result; failed calls marked) the model names the kind of work in a few words, writes the
steps that worked, one sentence each, with the values that change from goal to goal as parameters in angle brackets, and
a one-sentence description. The skill is a folder `learned-<kind>/SKILL.md` under `<home>/playbooks` with "Kind of
work", "When", "Steps", "Parameters" and "Evidence" (the goal, every run it was learned from with its acting steps, the
date), plus a `playbook.json` that keeps the kind and counts its use. A later run of the same kind (matched by the
meaning of the kind when embeddings are on, by its name otherwise) updates the skill rather than writing another: the
steps are the newer run's, the evidence keeps every run. Later runs see it under Skills like any skill (its source is
`learned`) with a note that it is advice from Dot's own work, to follow with their own parameters and judgement, and
read it with the `skill` tool. Every run that ended done or failed counts the learned skills it read; one read by two
runs that then failed is retired, moved under `<home>/playbooks/.retired`, and no longer offered. A review model that
fails, or a folder that cannot be written, leaves a `playbook_failed` event on the run; a written or updated one a
`playbook_written` event and a retired one a `playbook_retired` event. The distillation reads pages third parties wrote,
so the skill never takes an instruction from them as a step, and never holds a secret, a one-time code, an e-mail
address or a home path. Settings › Skills lists the learned skills (kind, where, the runs, how they fared, the retired
ones apart, an agent's with its name and Share) with Drop, each list in a box that keeps its size and scrolls inside
past its first rows (item 218); `dotx playbooks list` shows them with their use,
`dotx playbooks show NAME` prints one and `dotx playbooks drop NAME` removes one, active or retired. Delegated runs,
chats and evals (which always run without learned skills) write none. `dotx stats` counts the skills written, retired
and read, and compares, per host, the runs that read a skill with the runs on that host that did not (act calls and
seconds a run).

## Delegation

`delegate(goal, model?, step?)` hands a sub-goal to a separate run with a conversation of its own and returns
its result, so a long investigation does not fill the parent's context. The run works on the parent's model, or on the
helper model that `model` names when that one can answer (see [Helpers and standby](#models-and-providers)). Several
calls in one turn run side by side (up to `parallel_tools`), which is how a plan's independent steps run at the same
time; `step` names the plan step the part is, and the report closes it (see the plan above). A
delegated run cannot delegate again (the depth is one), ask you a question (its approvals still come to you) or
consult a helper, does not take one of the
`max_concurrent_runs` slots, starts as untrusted as its parent, and ends when the parent is cancelled. The parent's
result names the model the delegated run ended on. The delegated run takes its parent's mode, which the model
cannot choose: a task run's helper can do what the task run can, a research run's helper only reads.

## Configuration

`config.toml` in the home directory is merged over the packaged `src/dotagent/defaults.toml`, which lists every key with
a comment and is the reference. A key that does not exist is an error, and so is a number that would stop Dot from
working: a count of runs, tools, tabs, tries or calls at once below 1, a timeout or the scheduler's tick of 0 or less, a
port outside 1 to 65535, a browser page smaller than 320 by 240 or larger than 3840 by 2160, a screenshot quality
outside 1 to 100, a context budget of 0 or less, a tool output under 2,000 characters, a default shell timeout above
`max_timeout_s`. So is a value no run could use: an address of a provider or of the judge that is not `http://` or
`https://` with a host, a model that is not `<provider>:<model>` with a provider Dot knows (`[models] search` an
Anthropic one), a site under `[policy.spend]` that is not a bare host in lower case without `www.`, such as
`example.com`. Two environment variables of the process move things: `DOT_HOME` (the home directory) and `DOT_LOG_DIR`
(the logs).

| Path (under `~/Library/Application Support/Dot`) | Content                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `config.toml`                                    | your settings                                                        |
| `.env`                                           | API keys (mode 0600)                                                 |
| `dot.sqlite3`                                    | runs, events, conversations, notes, schedules, rules, approvals      |
| `api-token`                                      | the bearer token of the web UI and the API (mode 0600)               |
| `workspace/`                                     | the working directory of the shell and the file tools                |
| `skills/`, `plugins/`                            | skills and plugins Dot always reads (more via `[skills]`/`[plugins]` dirs) |
| `added-servers.json`                             | the MCP servers you added from other agents, by name and fingerprint |
| `opencode/`                                      | the OpenCode server Dot starts, the chats' sessions, the free models |
| `themes/`                                        | your own themes, one T3 Code theme file each (Settings › Appearance) |
| `browser-profile/`                               | the Chrome profile of the browser tools                              |
| `screenshots/`, `downloads/`                     | what the tools capture and download                                  |
| `undo/`                                          | journals of the file changes of runs, for `dotx undo`                |
| `daemon.pid`                                     | held while a daemon runs; a second daemon is refused                 |
| `STOP`                                           | exists while the emergency stop is engaged                           |
| `run-owners/`                                    | one lock per process that drives runs (the daemon, `dotx run`)       |

Deleting a chat deletes the screenshot files that only it named. Nothing else is removed by itself: old runs and their
events stay until their chats are deleted or `dotx prune --apply` finds them old, and the journals of file changes
until it finds them old. `dotx prune` lists the events of the runs that ended more than `[store] retention_days`
(90) days ago (the steps, tool calls and results of their timelines; the runs keep their answers, numbers and
conversations), the screenshot files that no chat names any more (what chats deleted earlier left behind), the free
space inside the database file and the journals of file changes (`dotx undo`) whose last change is older than
`[agent] undo_keep_days`; `dotx prune --apply` deletes those events, and with them the screenshots only they named,
those files and journals, and gives the space back to the disk, after which a delete returns its space by itself. The
database is locked for a moment while it compacts, so run it when no run is active.

Logs are structured and go to `~/Library/Logs/Dot/dotx.log` (another folder with `DOT_LOG_DIR`: `dotx service status`
names the one in use, and the sign-in screen of a daemon that could not answer points to it); at 10 MB it is moved to
`dotx.log.1` (the one before is dropped) and a new one is started. The output of the launchd job goes to
`daemon.out.log` and `daemon.err.log` in the same directory, and the stderr of each MCP server to `mcp-<name>.log`.

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
`ollama:minimax-m2.7:cloud` against a temporary home. What the gate does with each answer of the review model, and what
each mode waives, is tested exactly in `tests/smoke/test_approval_modes.py` with the real store and broker. Judging is
model work, so another review model may judge differently.
- **Not verified live:** MCP against third-party servers (the HTTP and SSE transports ran against a local fixture
server); macOS older than 15.2 (the error messages for it were run with a faked answer, the Mac they ran on has macOS
26); the notification banner of the web UI, which needs your permission in the browser; `dotx browser import-chrome`
against a real Chrome profile and the real Keychain, which only you can run (its logic ran on a synthetic profile with
the real file layout, and the shared key between the sign-in window and the automated sessions ran with an ordinary
Chrome on the mock keychain); `attach` mode against your own Chrome. Its logic and its tab handling ran against a hidden
scratch Chrome with a debugging port, which plays your Chrome but has no Allow dialog, so what only your Chrome shows is
open: the dialog itself, opening a tab in the window you work in, how long a connection takes with very many tabs open,
and what Chrome does to a tab in the background. The web UI (chats, their selection and deletion, the settings tabs
including the Browser one) ran in a headless Chromium against the static files and a mocked API, and the API itself in
the server smoke tests; the two were not run together against a live daemon. No model worked from a real skill or plugin
folder (discovery, `dotx skills list` and the `skill` tool ran on real folders).
- The shell guardrail is best-effort, and the sandbox of a run's processes keeps them out of Dot's own files only (see
  [Safety](#safety)).
- The token goes only to a server that proves it holds it, but a page that another program served at the daemon's
  address reads what the browser keeps for that address, the token among it (see
  [The daemon, the web UI and the service](#the-daemon-the-web-ui-and-the-service)).
- DuckDuckGo answers a burst of searches with its bot check (HTTP 202) and Brave Search with a rate limit (HTTP 429)
  after a handful in quick succession. Dot spaces its searches (`search_spacing_s`), benches an engine that limits it
  and moves to the next rung, so a search fails outright only when every engine does (`dotx stats` counts these under
  the web failures; the browser rung needs Dot's own Chrome with a visible window). The hosted Ollama search has no such
  limit: with `search_backend = "auto"` (the default) it is the first rung as soon as `OLLAMA_API_KEY` is set.
- The thresholds of the memory search were measured on a small hand-written set of English and Turkish notes
  (indicative, not a benchmark); another language, another embedding model or a much larger memory may need other
  `min_similarity` values.
- Schedules fire only while the daemon runs and the Mac is awake. `dotx service install` writes, lints and loads the
  launchd job that keeps the daemon running in your login session.
- MCP: tools only, over stdio or the streamable HTTP and SSE transports (no resources, prompts, sampling or OAuth).
- All conversation threads share one Chrome window.
- Every model call carries the schemas of over forty tools, several thousand tokens. A model with a small context
  window or a metered key feels it; `allow` lists keep the MCP part short.
- A run costs what its model costs: the loop makes many calls, and screenshots are expensive. A run has no step limit,
  so what bounds the cost is the stop button, the progress watchdog, `goal_patience` for the send-backs of goal mode,
  the concurrency limits (`max_concurrent_runs`) and, when you set them, `run_max_minutes` and `run_max_tokens`. Without
  `stall_escalation` and without those limits, a run that varies its steps without getting anywhere ends only when you
  stop it.
- A campaign has no end but a verified outcome or you: by default a session every hour, each costing what a run
  costs. What bounds it is Stop, `campaign_max_sessions` and the limits of each session; its page shows the tokens of
  its sessions. The tests play the sessions and the goal check with the scripted model: no campaign has run with a real
  model here.
- Resuming: in goal mode, the goal check of a resumed run judges from what the resumed run did, not from the steps of
  the run it resumes. After a crash the conversation shows every call of the step that was cut off as interrupted, also
  the ones that finished (a stop keeps the results of the ones that had ended); the first message of the resumed run
  says which finished. A run of `dotx run` works in its own process: a daemon that starts while it works leaves it, and
  the approvals it waits on, to that process, and marks it as cut off only once the process has ended (the daemon never
  resumes such a run on its own).
- Undo: only `write_file` and `edit_file` are in the journal, not shell commands, the browser or the desktop. A run
  keeps a copy of a file at its first change to it only, within `undo_max_file_bytes` and `undo_max_run_bytes`. Runs
  that change the same file are taken back in the order their changes were noted. The web UI has no undo button: use
  `dotx undo`, which needs the run in the database (the journals of a deleted chat stay until `dotx prune --apply`).

## License and security

Dot is released under the MIT License ([LICENSE](LICENSE)). The parts it carries from others keep their own licences,
each beside them: the Inter and Geist Mono fonts (SIL Open Font License, `static/fonts`), the icons, which are Lucide's
(ISC, and MIT for those Lucide took from Feather, `static/js/LICENSE-Lucide.txt`), the providers' marks
(`static/icons/providers/LICENSE`), the parts of the appearance settings ported from T3 Code (MIT,
`static/js/appearance/LICENSE`) and the character, a port of bloub (MIT, `static/js/character/LICENSE`).

Report a security problem privately, as [SECURITY.md](SECURITY.md) says, not in a public issue. Dependabot raises
alerts and fixes for dependencies with a known vulnerability, and each week one pull request per kind of dependency
that brings it up to date (`.github/dependabot.yml`: Python, the desktop app's npm and Cargo packages, the CI's
actions); the weekly audit of the CI (`audit.yml`, item 217) stays.
