# The command line

`dotx setup` checks this Mac, asks for the model Dot runs on and stores its key, and installs the service (see
[Install](../README.md#install)); `dotx --help` lists every command, and `dotx COMMAND --help` its flags. The model it
asks for is also the fast and review model (and the vision model when it sees images), except a model that answers only as
its own agent (one of Zen's free models through OpenCode, or Claude Code's on your plan), which is the main model alone. With the models of a provider other than
Ollama it asks the Ollama of this Mac for `bge-m3`, and where there is none it writes `[memory] embed_enabled = false`
(the notes are found by their words alone, as `dotx doctor` then expects) and says how to switch it back on.

`dotx doctor` checks the home directory, ripgrep, Chrome (with a throwaway start of it), whether the sandbox of code mode
starts Python and holds (item 1503: its row `code mode`, which `code_run` goes by: where it fails, no code runs), the macOS version and the
permissions the desktop tools need, the Office applications and the OneDrive folders of the skills that come with Dot
(none is opened), the commands and secrets of the configured MCP servers (none is started), Ollama, the keys of OpenAI,
OpenCode Go, Anthropic, OpenRouter and Google (OpenRouter's row says what its key has spent), the login of a Google
Antigravity account, the embedding model and the configured models, each of which its provider must serve to the key
(Ollama and Anthropic answer for the model itself, OpenAI, OpenCode Go, OpenRouter and Google through the list of the
models they serve), and says how to fix what is missing.
It warns, without failing, when the review model is also the main model,
when an MCP server's command or secret is missing (Dot starts without that server's tools), when a single tool of a
connected server was left out because its schema cannot be used (item 1001: `dotx doctor` starts no server, so it asks
the running daemon, once it has proven that it holds the token, and prints a row per tool with why; with no daemon
running it tells none, and a daemon that cannot be asked is a warn row of its own with why and its fix; Settings ›
System's checks and the service's page name them too), and when a provider's list
of models could not be read or the Ollama server could not be reached, as the provider's own row says why (the models of
an Ollama server out of reach are not asked, so the doctor does not wait out the retries of each). The examples below
write `dotx`: run it as `uv run dotx`, or put `.venv/bin` on your `PATH`.

```bash
dotx run "Find the opening hours of the Helsinki Central Library and save them to hours.md"
dotx run --mode research "Compare the free tiers of three hosted Postgres services"
dotx chat
```

`run` carries out one goal and prints the result; `chat` keeps a conversation going (`/new` starts a fresh one, `/exit`
leaves).

While the daemon of this Dot home runs, `run` hands its run to it (item 1203): the daemon starts the run, with the
source that a run of the command has (`cli`, `review` with `--review`), in its own Chrome, a tab beside its chats'
(Dot's Chrome profile is one process's at a time, so a run of the command's own process waited on the daemon's browser
work, and theirs on it), and its screen is on the pages as any run's is, the phone's too. The command follows it over
the daemon's API (the address of `[server]` and the token in the Dot home, once the daemon has proven that it holds it)
and prints what it prints for a run of its own: the steps, the answer, what the run needs from you, the questions it
left and, with `--standing`, the campaign's line. The lessons of the run are drawn by the daemon after its end: the
run's page shows them. With no daemon of the home (it never started there, or nothing listens at its address), the run
works in the command's own process, as on a Mac without one; a program or a daemon of another Dot home at the address is
said, and sent nothing. `dotx resume` and `dotx chat` work in their own process whether or not a daemon runs.

Ctrl-C stops the run that is going on; a second Ctrl-C quits (for a run of the command's own process, as soon as that
stop is recorded, five seconds at most). For a run of the command's own process, closing the terminal, or SIGTERM, cuts
the run off instead: its end is recorded, and it can be resumed like a run that a stop of the daemon cut off (`dotx
resume RUN_ID`, or Continue on its page). A run handed to the daemon is stopped by them, as nothing follows it any more.
A `dotx` command that dies (killed, or crashed) records nothing: the daemon closes its run within half a minute, as cut
off by the command's death, and it can be resumed too; a run handed to the daemon goes on there, on its page.

- `--mode research` makes the run read-only
- `--model provider:model` picks the model for this run, for example `ollama:gpt-oss:20b-cloud`
- `--effort LEVEL` picks the reasoning level for this run (see [Models and providers](models.md#models-and-providers))
- `--approvals supervised|ask|auto|yolo` picks who answers the questions of the policy in this run: you, and before
  every change too (`supervised`), you (`ask`, the default), a review model (`auto`) or nobody (`yolo`);
  `--auto-approve` and `--yolo` are short for the last two. `chat` takes the same flags (see
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run))
- `--goal-mode` / `--no-goal-mode` turns the check of the answer against the goal on or off for this run (`run` only)
- `--proof COMMAND` (repeat it, at most 5) holds the run's finish, or with `--standing` the campaign's claim, to a
  command that must exit 0, such as a test run; `--proof-timeout SECONDS` is each one's limit (300, at most `[shell] max_timeout_s`, 1800 by default). Dot
  runs them at the finish and sends the run back with their output while one fails (see
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)) (`run` only)
- `--standing` keeps going until done: it starts a campaign on the goal with this run as its first session, and the
  daemon runs the sessions after it until the outcome is verified (see
  [Agents and campaigns](agents-and-campaigns.md)); `--browser own|attach` (with `--standing`) picks the Chrome it works
  in, Dot's own or the one you have open, signed in as you are (default: `[browser] mode`) (`run` only)
- `--thread ID` continues an earlier conversation; the line printed at the end of a run names its thread
- `--no-input` never prompts: every approval that would wait for you is denied and questions go unanswered. The same
  holds when standard input is not a terminal, and for a run handed to the daemon: the command answers its cards as
  the terminal (`cli`), so the caller is never kept waiting. A run of the daemon may also leave a question for you
  and go on without it, as a chat's run does: it stays on the run's page
- `--review` marks a code review (the pre-push hook's, item 1256): its chat is kept under **Code reviews** in the
  sidebar and in the iPhone app's list (item 1664), folded, apart from your chats, and is never Dot's home chat
- `--thinking` shows the model's reasoning stream, `--verbose` the tool output and the token use
- a goal of `-` is read from standard input

`dotx harness` manages the versions of what a run's prompt takes from the harness, which [The harness](harness.md)
describes: `list`, `show [ID]`, `diff ID [--against ID]`, `set --summary TEXT [--habits FILE] [--tool-guidance FILE]
[--adopt]` (a component from a file, `-` for standard input, the other kept from the adopted version), `adopt ID
[--yes]` and `retire ID`. A version that may come from content others can write (the warning says what its run read,
also when it was composed of such versions) is adopted after a warning and a yes (`--yes` gives it beforehand; with no
terminal to ask on, it is not adopted); one whose text the gate for learned texts refuses now (`show` says so on a
`refused:` line) is not adopted at all, and the command says why and that `retire ID` sets it aside. The daemon reads
the adopted version as each run starts, so none of them needs a restart. `propose RUN_ID [--scope invariant|domain]`
asks the proposer for a candidate from one run; `step [--run ID] [--scripted]` runs one step of the improvement loop
now, its probes in processes of their own (`probe --ask FILE`, which the step starts), and `final [ID]` plays the final
set once under the adopted version; `--scripted` plays the references through the scripted model and has the roles
answer from a script, a check of the loop at no model's cost. `graph [--steps N]` draws the provenance (the newest N
steps, 50 by default, and four experiences a step) as a Mermaid flowchart, `steps` and `experiences` list the newest of
each, and `show ID` ends with the version's lineage.

## Code review before a push

`.claude/hooks/pre-push-review.sh`, the pre-push hook of Dot's own repository, has Dot review each push following
REVIEW.md, in research mode (it reads the code and cannot change it), with `dotx run --review`. `dotx review plan
--lines N --changed FILE --guide REVIEW.md` tells it what to run on (item 1663): `[code_review]` of `config.toml`,
which Settings › Models › Code review chooses ([Models](models.md)), for a change of N changed lines (added and removed)
whose paths `FILE` lists one per line. It prints `size small` or `size full`, `limit <seconds>`, then one
`try <model> <effort>` line per model in the order they are tried, and a `route` line saying why the first is the first:
the small change model first for a change of at most `small_max_lines` lines that touches no path REVIEW.md lists under
"Always needs a human", when one is chosen (`small`), else the review model; then the fallbacks, each once. When the
review model is on the Claude plan, two things send the review to the fallbacks that are not on the plan first, the
review model coming after them: the change touches a path REVIEW.md lists under "Always needs a human" (`security`,
item 1954: the plan's model refused 48 such diffs in a week as a cyber risk, and each was a try that gave no verdict),
and the plan's five-hour window is used up as far as 80 % or refuses calls and has not reset (`plan_spent`, item 1954),
as the newest step of Dot's own runs recorded it in the store, which `dotx review plan` reads without writing. Of 532
reviews on the plan in a week, 56 started with the window at 80 % or more, and 4 of the 6 that the usage limit then cut
would have started on the second model.

The hook tries them in that order under **Code reviews** (the first in the branch's review chat, each next one in a
chat of its own), as long as one gives no verdict (a
`VERDICT: PASS` or `VERDICT: BLOCK` line: Claude refusing the diff, a plan that is spent and a failed run give none). A
`BLOCK` stops the push. The push stops too, with "could not be reviewed" and why, when no model gave a verdict, when the
review ran past `max_minutes` in all (the run is then stopped: `dotx run` asks Dot to stop it on SIGTERM), and when the
plan cannot be read; it never passes unreviewed. `git push --no-verify` skips it. A commit that passed is not reviewed
again, nor a push that only brings main in.

A push is reviewed from the newest commit of the branch that was reviewed here, one that passed or one whose review
`BLOCK`ed with findings, not from where the branch leaves main (item 1954; a first push that `BLOCK`s never reaches the
remote, so each round read the whole change again: in a week 319 of 593 reviews, 702 of 1 608 M tokens, were a later one
in a chat that had one). It does so only while main has not moved under the branch since that commit, and a review that
gave no verdict counts as read by nobody, nor does a `BLOCK` that counts no Important finding (the review could not read
the change or ran out of its budget). The reviewer is told to read only the files the change touches (listed in
`.git/claude-review/<commit>.paths`) and REVIEW.md, and, after a `BLOCK`, is given that review's file to check that each
Important finding in it is fixed by the new diff or still stands. A model that is not on the Claude plan is also told
its budget, 40 tool calls and four fifths of the time left in minutes, and to write its findings and the `VERDICT` line
at three quarters of either: DeepSeek reads the repository at about ten seconds a step and stopped at the time limit with
no verdict in 21 of its 61 reviews. A hook change applies to the owner only once their own checkout has pulled it.
