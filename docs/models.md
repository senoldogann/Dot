# Models and providers

A model is written `provider:model`. The `[models]` table of the configuration sets these keys:

| Key                 | Used for                                                                                  | Default                     |
| ------------------- | ----------------------------------------------------------------------------------------- | --------------------------- |
| `main`              | the agent loop of a run that names no model; Settings › Models sets it                    | `ollama:minimax-m2.7:cloud` |
| `vision`            | describing the images a tool returns, when `main` cannot see them; Settings › Models sets it | `ollama:gemma4:cloud`    |
| `fast`              | writing the summary of a compaction (see [Tools](runs.md#tools)) and a run's recap (its page's Task tab, item 536); Settings › Models sets it | `ollama:gpt-oss:20b-cloud`  |
| `search`            | relaying a web search through the Anthropic API's own search tool (see [Tools](runs.md#tools))   | `anthropic:claude-haiku-5-5` |
| `review`            | answering the policy's questions in `auto` approvals, checking a run against its goal, and drawing its lessons; Settings › Models sets it | `ollama:minimax-m2.7:cloud` |
| `effort`            | default reasoning level of a run on `main` (see below)                                    | `medium`                    |
| `helpers`           | models a run may ask for advice or hand a part of its work to (see below)                 | `[]`                        |
| `standby`           | models a run moves to when its own model cannot answer (see below); Settings › Models sets it | `[]`                    |
| `helper_cooldown_s` | how long a model that could not answer is left out of `helpers` and `standby`             | `600`                       |
| `context`           | a model's context size where it is not `[agent] context_tokens` (see below); Settings › Models sets it | `{}`         |

`review` checks the work of `main`. A model checking its own work is the weaker check, so pick a `review` model of
another family than `main` when you can; `dotx doctor` warns while they are the same model, as they are by default.
With `[agent] lessons = "auto"` the warning also says that lessons are approved by the run's own model, and that an
independent check needs another `[models] review` model.
What `review` is asked has the secrets Dot holds and the ones it recognises masked, as it may be another provider's.

Settings › Models chooses `fast`, `review` and `vision` under the default model (item 232), each from the models that
can do its work: those that call tools for `fast` and `review`, those that see for `vision`. A choice holds from the
next call of that work on, with no restart (a run under way keeps the review model it started with for its goal check,
its second opinion and its approvals), and the config file keeps it with the rest of the file as it was; a model that
cannot do the work is refused, and nothing changes (`POST /api/models/roles`).

Under them, **Fallbacks** (item 478, the standby models; drawn as a numbered list since item 563) lists the models a run
moves to when its own model cannot answer (below), in their order: **Add a fallback model** puts one of the models that
call tools at the end, the arrows move one up or down, and × takes it off; a model whose provider has no key says so,
since a run passes it over. The list holds from the next move
on, with no restart (a run that moved already keeps its model), and the config file keeps it as `standby` with the rest
of the file as it was; a model named twice, one that cannot call tools and one of Zen's free models are refused, and
nothing changes (`PUT /api/models/standby`, eight at most). An empty list moves no run: the error ends it.

**Chat size** (the context size). Beside each model's window, Settings › Models sets the chat size of the model's runs (item 250):
how much of a run each step sends the model before Dot summarizes its older part. The menu offers Dot's default and the
smaller sizes from 48 000 up. The default is the most the model's window allows, three quarters of it, the first entry
of the menu; an Ollama model whose own window is larger than the one Dot asks for by default (`num_ctx`, or
`num_ctx_cloud` for a cloud model) is offered sizes up to three quarters of its own window too, the most marked so, and
Dot then asks Ollama for the window its size needs and plans the run with it (item 520; on this Mac, a larger window
takes more memory) (`[agent] context_tokens = "most"`, item 396; 96 000 for a model whose provider does not say its
window); a number there gives every model that size instead, or three quarters of its window when that is less. A choice
holds from the model's next run, with no restart, and the config file keeps it in `[models.context]`
(`"anthropic:claude-opus-5-5" = 192000`) with the rest of the file as it was. A larger size means fewer summaries, each
of which drops detail of the older part and takes a call of the fast model, and more input a step: steps that are
answered more slowly and cost more on a key billed by the token. **Why** opens, under the model's row, what the size
does; the window and where it comes from (the provider's API, the windows the provider publishes as Dot's table keeps
them, an assumption for a model neither names, or the window Dot asks Ollama for); the default, the most and the least;
and what the model's runs of the last 30 days measured (`GET /api/models/context`): the steps and the runs, the input of
a step at the median, for 95 % of the steps and at most, the share the provider served from its cache, the input of a
run's first step, the summaries, and the seconds of a step by its input. A size below 48 000 (a run's first step alone
sends its instructions, its tools and the request) or past three quarters of the window (of the model's own window, for
such an Ollama model) is refused, and nothing changes (`PUT /api/models/context`). The ring beside a run's send (**Chat size**)
links to its model's row (**Change the chat size**, `#/settings/models?model=<provider:model>`): its provider's fold
opens and the row comes into sight, marked, with the focus on its size.

**Helpers and standby.** `helpers` lists models, best first, that a run may turn to. The `consult` tool asks one of them
a question and returns its answer, fenced as advice; `delegate` can run a part of the work on one of them. A helper has
no tools and sees only what the question sends: the run's goal, its plan, the conversation (secrets masked, the start
and the end of a long one) and the newest screenshot, which only a helper that can see images gets. A run may ask
`[agent] consult_max_per_run` questions (5); `0` offers no `consult`. A delegated run asks none (its parent does), and
the run's own model is never its helper.

`standby` lists the models a run moves to when its own model answers with a quota, rate-limit or key error (HTTP 401),
refuses access to it (HTTP 403: a model, a region or a plan the key may not use; or OpenRouter's 404 for a model the
account's privacy settings leave no provider of, item 361), its provider has no key, or its service's content filter
stops the reply (item 437; that pauses the model for no other run): the run goes on from the same step, with the tools
that step had, on the first standby model that can take it over, the model is told, and the timeline shows the switch,
which the model chip follows. The run's reasoning level becomes the nearest one the new model offers. A run takes each
model once at most, in the order of the list, and never goes back to one it left. A standby model that cannot call tools
while the run offers them, whose window cannot hold the instructions and the tools, or whose details cannot be read is
passed over; when every one is, the run fails and says why for each. Without a standby model that can answer, the error
ends the run (one that comes back by itself is waited for first: a spent quota, below), and its message is the run's
result (what the timeline, a notification and `dotx run` show); its context, the request and the answer of the provider,
is kept with the run's `run_finished` event and in the log. A transport error or a timeout moves nothing (the run waits
for its own model instead, see below), and neither does a failure of the `review` or `vision` model in a check: a goal
check and an `auto` approval are not passed to another model, as the run's own model would be judging itself, so with
`review` on the same provider as `main` they fail while that provider cannot answer. Dot's own learning is passed on
(item 649): the lessons of a run and their vetting, the upkeep of the notes and the skills, and the playbooks go, when
the review model declines them (item 249) or answers with a quota, rate-limit or key error, to the run's own model and
then to the standby models, in turn. A review model the router has paused for that is not asked again while another can
be, and when none can, the run's lessons say who was asked and why. With one provider, or a model on this Mac alone, the
run's own model is the one that writes them. What these questions carry (a run's digest, the notes, the owner's section,
a skill), its secrets masked, then goes to that model's provider too, a standby's among them.

A model is left out of both lists while its provider has no key (an Ollama model needs none), and after such an error
for the time the provider named (at most a day) or for `helper_cooldown_s` seconds, until a call to it is answered or a
new key of its provider is saved. A model the plan of its provider does not include (HTTP 402, "not in the plan") and
a plan whose included usage is used up (Ollama's 429 that says so) are such errors too: the connector does not retry
them, and the model is paused for `helper_cooldown_s` at once (item 254). OpenRouter's 402 that speaks of credits
pauses it as "out of credits". Dot sends no `max_tokens` unless a call sets one, and OpenRouter then prices a call at
the model's longest answer (131072 tokens for some), so an account with few credits can be refused a call whose answer
would be short; when that 402 names the longest answer the credits pay for (`can only afford 29597`) and it is at least
1,024 tokens, the connector asks once more with that as `max_tokens`, logs `openrouter_max_tokens_lowered`, and the
model is not paused. The fast model's work leaves a paused fast model out as well (item 240): a run's
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
and the result names the wait and the error. A refused request ends the run at once; a key or access error, spent
credits (OpenRouter's, Anthropic's prepaid balance, OpenCode's `CreditsError`), a model the plan does not include or a
missing key moves it to a standby model or ends it at once.

**A spent quota** (item 713). When the run's model cannot answer for a while that comes back by itself and no standby
model can take the run over, the run waits instead of failing: a rate limit, or a subscription's usage window (OpenCode
Go's "Go usage limit exceeded", its five hours or its week). It asks the same step again at the time the provider named,
within bounds, else every 10 minutes (at least a minute apart, at most half an hour), for `quota_wait_s` in `[agent]` (6
hours by default; 0 ends the run at once, as before), and goes on from that step as soon as the model answers; a
connection that breaks meanwhile (a Mac that wakes) is part of that wait. The timeline's line for the wait says why
("cannot answer (usage window spent)"), when it asks again and until when. What does not come back by itself is not
waited for, whatever its provider calls it or the time it names (a gateway may name one on every answer): a month's plan
(Ollama's "used all included Pro usage"), spent credits, a spend limit, any other quota. A wait still not over when its
time is ends the run with the provider's words. A waiting run keeps its place among `max_concurrent_runs`, as one
waiting for a model out of reach does, and a stop ends the wait at once. An error a model service reports inside its
answer keeps its words, Ollama's too.

| Provider    | Reference                  | Protocol                                                                        | Key                                                            |
| ----------- | -------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| Ollama      | `ollama:<model>`           | native `/api/chat`                                                              | none for the local server                                      |
| OpenAI      | `openai:<model>`           | Responses API; `wire = "chat"` selects Chat Completions                         | `OPENAI_API_KEY`, optional `OPENAI_ORG_ID`/`OPENAI_PROJECT_ID` |
| OpenCode Go | `opencode_go:<model>`      | each model is served on exactly one of Chat Completions, Responses and Messages | `OPENCODE_API_KEY`                                             |
| Anthropic   | `anthropic:<model>`        | Messages API                                                                    | `ANTHROPIC_API_KEY`                                            |
| OpenRouter  | `openrouter:<lab>/<model>` | Chat Completions, the models of many labs                                       | `OPENROUTER_API_KEY`                                           |
| OpenCode    | `opencode:<model>`         | OpenCode's own agent, in the `opencode serve` Dot starts (Zen's free models)    | none; the `opencode` program                                   |

- **Ollama**: `[providers.ollama] base_url` is `http://localhost:11434` by default. Cloud models go through the local
  server, so nothing else is needed once you are signed in. `OLLAMA_API_KEY` is only for the hosted web search and for
  talking to ollama.com directly.
- **OpenAI**: on the Responses wire Dot sends the whole conversation on every call (`store: false`) and carries the
  encrypted reasoning items from step to step, so no state lives on the server.
- **OpenRouter** (item 349): one key and one balance for the models of many labs,
  `openrouter:anthropic/claude-sonnet-5.5` or `openrouter:openai/gpt-5.6-luna` for example. What each model can do comes
  from OpenRouter's own list of its models, read when a model is first asked about and kept for `[providers.openrouter]
  models_ttl_s` (an hour): whether it reads images and calls tools (the model picker offers only those that do), its
  context and output limits, whether it takes a temperature, and its reasoning levels, the ones OpenRouter lists for it.
  A variant OpenRouter does not list (`:nitro`, `:floor` or `:online` after the id) can do what its model can.
  A model whose reasoning is a switch offers On and Off, and one that always reasons and names no levels offers none.
  The level goes as OpenRouter's `reasoning` object; a step's reasoning details go back to the same model on its next
  step, unchanged, as OpenRouter asks beside tool calls, so a model that thinks keeps its thread across a run's tool
  calls. A model whose id ends in `:free`, or whose prices are both zero, says Free; OpenRouter limits the free ones to
  a few requests a minute and a day. Dot sends no `HTTP-Referer` or `X-OpenRouter-Title`, so its requests do not count
  Dot in OpenRouter's rankings. The prompt caching OpenRouter does by itself (OpenAI, DeepSeek, Gemini and others)
  applies; the cache marks Claude would need through OpenRouter are not sent. A model that the account's privacy
  settings leave no provider of, as a model whose providers keep what they are sent is under Zero Data Retention, is
  answered 404; Dot reads that as a refused access: the error names what excluded the providers and where to change it
  (https://openrouter.ai/settings/privacy), or to choose another model. As after a 403, a run whose own model it is
  moves to its standby model, and the model is paused (item 361).
- **OpenCode Go**: Dot knows the route of the models in its catalog, and of a new Claude, Qwen, MiniMax, Grok, GPT or
  Muse model by the start of its name; any other new model goes to Chat Completions. `route_overrides = { "<model-id>" =
  "chat" }` in `[providers.opencode_go]` adds or corrects one (`chat`, `responses` or `messages`). The Messages route
  marks the prompt for caching, so Claude, Qwen and MiniMax read a run's prompt back from the cache from its second step
  on, at a tenth of the price and with a faster first token (Claude caches only so); the Chat Completions models cache
  it by themselves. A model Go serves at no cost (its id ends in `-free`) says Free in the model picker and in
  Settings › Models. The free models of OpenCode Zen do not answer Dot's own calls: Zen serves them only to OpenCode's
  programs and answers any other with a 403 (checked on 2026-10-12 and 2026-10-15, with and without a key), so they
  run as OpenCode's own agent (**OpenCode**, below). The reasoning levels offered are the
  ones the gateway honours: DeepSeek's models offer none, since the chat route thought the same at every label (measured
  2026-10-15, item 258), so Ultrathink and Ultracode do not show for them, but for DeepSeek V4.1 Flash, which keeps Low,
  High and Max at the owner's choice (item 290); GLM 5.2, Grok, GPT Luna, Muse Spark, Longcat, MiMo and Hy keep theirs.
- **Anthropic**: what a model can do (pictures, its window and output limit, whether it thinks and at which levels) is
  read from the Models API, so a new Claude model needs no update of Dot. A model that thinks gets adaptive thinking at
  the run's reasoning level, with the summary of its thinking shown; `off` turns thinking off where the model allows it,
  and is rounded to `low` where it does not. A model without adaptive thinking (the 4.5 generation and older) runs
  without thinking at its default level. The prompt is cached from one step of a run to the next (for five minutes), and
  from one run of a chat to the next: the system prompt is the same bytes from one message of a chat to the next (it
  names the day and the time zone; the time of day, the notes recalled for the message and the skills that fit it go in
  a note under the message, item 404), so the cache holds the instructions and the history up to the message. A request
  may use the model's whole output limit. The thinking of earlier steps is sent back; a block whose earlier context has
  changed since (a run drops an old screenshot, a compaction folds the history, a new day changes the instructions) is
  dropped by the API instead of failing the request (beta `thinking-binding-controls-2026-08-01`).
- **OpenCode** (Zen's free models, item 283): a free model of OpenCode Zen (Big Pickle, Nemotron, Step, Space Bunny and
  the others the day's list has) answers OpenCode's own agent only, so a run on one goes to that agent whole. It needs
  the `opencode` program (`brew install opencode`, or opencode.ai) and no key. Dot starts `opencode serve` on a free
  loopback port behind a password of its own when a run needs it (about 2 s) and stops it after `[providers.opencode]
  idle_stop_s` (600 s) with nothing to do; it holds about 340 MB while up. The menu lists every free model that calls
  tools, with its reasoning levels, from `opencode models opencode --refresh` (OpenCode's catalog refreshed from
  models.dev first, so a model Zen has just added is there), kept for an hour (`models_ttl_s`). The server keeps to its
  own folders in Dot's home (`opencode/`) and reads none of your own OpenCode setup: no key, plugin, skill, command,
  tool or rule of yours, nor a project's `opencode.json` or `AGENTS.md`. Its agent works in Dot's workspace with
  OpenCode's own tools (commands, reading and editing files, searching, fetching and searching the web, its to-do list),
  and each of them asks Dot first: Dot weighs it as the same action of its own tools, by your rules, the preset and the
  run's approval mode, and asks you, the review model or nobody as for any run; a refusal tells the agent why, and it
  goes on. A subagent it asks for is refused, as its session would not be the run's. An edit is weighed by every file
  it writes (a patch of several files by the one your policy weighs most), and a command in the folder it runs in.
  Outside `yolo` the server and all it runs work in Dot's sandbox, as Dot's own commands do (Settings › Rules ›
  Advanced › Keep Dot's commands in a safe box, item 677): from a home of their own (`opencode/sandboxed`), whose
  data, state and cache they write and whose config they do not, and nothing it starts reaches the server's own port
  (where the password its environment holds would answer its own asks); a `yolo` run's agent works in the other server,
  as before. Its web fetches reach no address of this Mac or its network
  unless `[web] allow_private_hosts` says so. Besides its own, the agent has Dot's tools for what OpenCode has none for
  (item 284): Dot's browser with your sign-ins, the Mac's applications, memory, schedules, notify, a page in the
  conversation, skills, a question to you (`ask_user`) and the apps you connected, as the run's mode and agent allow
  them. Dot serves them to the OpenCode server it started as an MCP server of its own on a loopback port, one door per
  chat (`dot_` and eight hex digits, with a secret of its own), and a chat's session sees its own door's tools only;
  each call goes through the run's approvals and time limit as a call of Dot's own loop does, and shows on the timeline
  under the tool's own name. A call that waits for your answer tells OpenCode so every 20 s, so a slow approval does not
  time it out. The run's timeline shows the agent's text and thinking as they come, each tool call (`opencode_bash`,
  `opencode_edit`, `browser_open`, ...) with its result and each step with its tokens; your messages reach the agent
  while it works, and Stop stops it. A chat keeps its OpenCode session from run to run, so the agent remembers it, and a
  session that missed what the chat said on another model hears it with the next message. The run's limits
  (`run_max_minutes`, `run_max_tokens`) bind the agent as they bind Dot's own loop (item 552): the first step that ends
  past one while the agent goes on tells it to stop and answer, and an agent that goes on two steps after that is
  stopped and the run ends failed. A reply that stopped at the model's output limit ends the run failed too ("the
  model's replies were cut short"), not done with half an answer. What Dot's own runs bring
  besides (the memory recalled at the start, the checks of a finish, lessons and playbooks) a run here does not have,
  and a free model cannot be the fast, review or vision model, whose calls are Dot's own. Zen's terms of the free models
  apply: the providers of Big Pickle, Exo, MiMo and Muse Spark may use what is sent to them to improve their models
  (opencode.ai/docs/zen, read 2026-10-15), so keep private work on other models. The free tier is Zen's to give: it
  turned requests down for a few minutes after many in a short time, OpenCode's own command line's too (measured
  2026-10-15), and it is reported to refuse OpenCode with one of its tools switched off, so Dot switches none off and
  has them ask instead; a run it turns down fails with words that say so.

`dotx models [ollama|openai|opencode_go|anthropic|openrouter|opencode]` lists what a provider offers, including the
reasoning levels of each model, and ends with 1 when no provider answered (the one named, when one is). An Ollama server
on this Mac is asked for its list once, so one that is not running is reported at once, here, in the model menu and by
`dotx doctor`, not after the retries a network needs (item 212). `dotx models profile [provider:model]` shows how
reliably a model worked in its newest runs and the help its next run gets (see [How a run
works](runs.md#how-a-run-works)); without a model it shows `[models] main`.

**Reasoning level.** A model that can think offers a ladder of levels, lowest first, in the words of its provider mapped
to one neutral scale: `off`, `on` (a plain thinking switch), `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. A model
that offers fewer than two has no choice to make, and its picker stays hidden. `models.effort` is the default; one run
picks another with `dotx run --effort high`, with the Reasoning menu of the composer in the web UI, or with `effort` in
`POST /api/runs`. The run keeps the level it was started with (the facts under the side panel's More show it). A level
the model does not offer is refused (a 422 over HTTP); the configured default, which is not tied to one model, is
rounded to the nearest
level the model has (`off` to its lowest, `max` to its highest).

**Fast mode.** Claude Opus 5.5, Opus 5 and Opus 4.8 have fast mode: up to 2.5 times the output speed, at twice the price
(Opus 5.5: $8 and $40 a million tokens; Opus 5 and 4.8: $10 and $50). On Opus 5.5 it is open to every organization in
the Start, Build and Scale usage tiers, with a rate limit of its own and no access request; on Opus 5 and Opus 4.8 it is
a research preview that Anthropic lets an organization into from a waitlist. It is not for the Batch API or a Priority
Tier commitment. The Models API does not say which models have it, so Dot names them from Anthropic's page
(`FAST_MODE_MODELS` in `llm/anthropic.py`); Settings › Models marks them `fast mode` (`fast_mode` in `GET /api/models`).
The Reasoning menu of the composer has Speed, Standard or Fast, for such a model, off at first; the pill then reads
`High · Fast`. A chat keeps it with its choices, a new chat's box in this browser, and the run it starts keeps it as its
model and level (`speed` in `POST /api/runs` and in the queue; the facts under the side panel's More say so). Each step
of such a run on a model with fast mode asks for it (`speed: "fast"` with the beta `fast-mode-2026-02-01`, which the
preview models need
and Opus 5.5 takes without effect); a step on another model (a routed step, a standby model) runs at the standard speed,
as do helpers on a model without it. Dot never falls back by itself: a refusal (an organization not let into a preview
model's fast mode, fast mode's own rate limit, once retried) is the run's error, as Anthropic says it. Fast mode for a
model without it is refused (a 422 over HTTP). Switching the speed of a chat starts the prompt cache again.

**Ultrathink and Ultracode.** The Reasoning menu has two choices for one message, under This message only (item 251).
Ultrathink, for a model that offers `max`, asks it for Max for this message. Ultracode, for a model that offers `xhigh`,
asks it for Extra high and tells the run, in its instructions, to split the goal: each part that needs no other part's
result goes to a helper of its own (a `delegate`, several in one turn, each with its plan step), and the run does itself
only what joins or checks their reports; the helpers work at the run's level, or at the one their agent names. The pill
reads the choice until the message goes, and the next message goes with the level the chat keeps, which the choice never
changes. The run keeps it as it was started: the run that resumes it, the one that carries on after an answered question
and every session of a campaign started with it (`once` in `POST /api/runs` and in the queue, `ultrathink` or
`ultracode`; the facts under the side panel's More say `Max, Ultrathink`). A model without the level is refused (a 422
over HTTP), and a helper, which cannot delegate, is never told to split. The names are Claude Code's, where ultrathink
is a word in a
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
prompt nobody answers) or that refuses the lookup (a prompt you denied, a locked one with nobody to ask) is an error
that says so, with what `security` said, and not a key that is missing; one that holds no such item is a key that is
missing. The page of keys shows such a key as unset and the log says why (`keychain_unreadable`, item 561). A key the
daemon can start without is taken as no key, at its start and where a connector or the memory's embedder is made
(Ollama's, which a local Ollama needs none of; the Anthropic key of web search's relay; the judge's; the organization
and project of an OpenAI account), so that a Keychain that is locked does not stop Dot from starting. What needs the key
later fails for want of it (web search's hosted rungs with the Keychain's own error); a key a connector cannot be built
without (OpenAI's, Anthropic's, OpenRouter's, OpenCode Go's) keeps the error wherever it is read.
`dotx auth set OPENAI_API_KEY` reads the value from the terminal
without echoing it and writes the file with mode 0600 (a value with a line break in it is refused, and two processes
changing the file take turns); `dotx auth status` shows which keys are present. `.env.example`
is a template for the file. An OpenAI key is created on platform.openai.com under API keys (`OPENAI_API_KEY`); the
OpenCode Go key (`OPENCODE_API_KEY`) is the one the OpenCode Console at opencode.ai/auth shows once you have subscribed
to Go; an Anthropic key (`ANTHROPIC_API_KEY`) is created in the Claude Console at platform.claude.com/settings/keys; an
OpenRouter key (`OPENROUTER_API_KEY`) at openrouter.ai/settings/keys. The
Ollama models, local or `:cloud` through the signed-in local server, need no key. A running daemon picks up a key added
later when a run first uses that provider, without a restart.

Settings › AI services in the web UI does what `dotx auth set` does for the key of each provider, and more: it shows where
the key in use comes from (the daemon's environment, Dot's `.env` file or the Keychain) and, for a key of 16 characters
or more, its last four characters; a key never comes back from the daemon whole. **Check** asks the provider whether it
takes the key in use: the model listing of OpenAI and Anthropic, the subscription of OpenCode Go and the key's own page
at OpenRouter (what it has spent and may spend) cost nothing, the Ollama key is tried with one search of its hosted web
search. **Add key** or **Replace key** asks for the key in a dialog (item 615), saves it in `<home>/.env` and then runs
**Check**; the key is hidden until you show it, a key with a space inside is refused there, and one typed and dropped
is gone when the dialog opens again; **Remove** takes away the
key saved there (a key from the environment or the Keychain is left alone). The page warns when the daemon was started
with the variable in its environment, which comes before a key saved in Dot. A saved or removed key is used from the
next request of a run on, even in a run under way; so are the Ollama and Anthropic rungs of web search, which join the
ladder with the key saved here and leave it with the key removed (item 558; a key set with `dotx auth set` or in the
environment joins them at the daemon's next start). The memory search (an OpenAI or ollama.com key) and the MCP servers
read their keys when the daemon starts: after changing a key they use, restart the daemon (`dotx service restart`; a
run in `yolo` asks for `dotx service restart --when-idle`, see [Daemon](daemon.md)).
