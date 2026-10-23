# The command line

`dotx setup` checks this Mac, asks for the model Dot runs on and stores its key, and installs the service (see
[Install](../README.md#install)); `dotx --help` lists every command, and `dotx COMMAND --help` its flags. The model it
asks for is also the fast and review model (and the vision model when it sees images), except one of Zen's free models,
which answers only as OpenCode's own agent and so is the main model alone. With the models of a provider other than
Ollama it asks the Ollama of this Mac for `bge-m3`, and where there is none it writes `[memory] embed_enabled = false`
(the notes are found by their words alone, as `dotx doctor` then expects) and says how to switch it back on.

`dotx doctor` checks the home directory, ripgrep, Chrome (with a throwaway start of it), the macOS version and the
permissions the desktop tools need, the Office applications and the OneDrive folders of the skills that come with Dot
(none is opened), the commands and secrets of the configured MCP servers (none is started), Ollama, the keys of OpenAI,
OpenCode Go, Anthropic and OpenRouter (OpenRouter's row says what its key has spent), the embedding model and the
configured models, each of which its provider must serve to the key (Ollama and Anthropic answer for the model itself,
OpenAI, OpenCode Go and OpenRouter through the list of the models they serve), and says how to fix what is missing.
It warns, without failing, when the review model is also the main model,
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
- `--effort LEVEL` picks the reasoning level for this run (see [Models and providers](models.md#models-and-providers))
- `--approvals supervised|ask|auto|yolo` picks who answers the questions of the policy in this run: you, and before
  every change too (`supervised`), you (`ask`, the default), a review model (`auto`) or nobody (`yolo`);
  `--auto-approve` and `--yolo` are short for the last two. `chat` takes the same flags (see
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run))
- `--goal-mode` / `--no-goal-mode` turns the check of the answer against the goal on or off for this run (`run` only)
- `--standing` keeps going until done: it starts a campaign on the goal with this run as its first session, and the
  daemon runs the sessions after it until the outcome is verified (see
  [Agents and campaigns](agents-and-campaigns.md)); `--browser own|attach` (with `--standing`) picks the Chrome it works
  in, Dot's own or the one you have open, signed in as you are (default: `[browser] mode`) (`run` only)
- `--thread ID` continues an earlier conversation; the line printed at the end of a run names its thread
- `--no-input` never prompts: every approval that would wait for you is denied and questions go unanswered. The same
  holds when standard input is not a terminal
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
