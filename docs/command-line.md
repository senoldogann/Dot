# The command line

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
- `--effort LEVEL` picks the reasoning level for this run (see [Models and providers](models.md#models-and-providers))
- `--approvals ask|auto|yolo` picks who answers the questions of the policy in this run: you (`ask`, the default), a
  review model (`auto`) or nobody (`yolo`); `--auto-approve` and `--yolo` are short for the last two. `chat` takes the
  same flags (see [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run))
- `--goal-mode` / `--no-goal-mode` turns the check of the answer against the goal on or off for this run (`run` only)
- `--thread ID` continues an earlier conversation; the line printed at the end of a run names its thread
- `--no-input` never prompts: every approval that would wait for you is denied and questions go unanswered. The same
  holds when standard input is not a terminal
- `--thinking` shows the model's reasoning stream, `--verbose` the tool output and the token use
- a goal of `-` is read from standard input
