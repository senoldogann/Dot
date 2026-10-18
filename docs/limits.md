# Status and known limits

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
including the Browser one) runs in a headless Chromium against the daemon's own server and API in the browser smoke
tests, started in the test's process with a throwaway home, not against the daemon you run. No model worked from a real
skill or plugin folder (discovery, `dotx skills list` and the `skill` tool ran on real folders).
- The shell guardrail is best-effort, and the sandbox of a run's processes keeps them out of Dot's own files only (see
  [Safety](safety.md#safety)).
- The token goes only to a server that proves it holds it, but a page that another program served at the daemon's
  address reads what the browser keeps for that address, the token among it (see
  [The daemon, the web UI and the service](daemon.md#the-daemon-the-web-ui-and-the-service)).
- DuckDuckGo answers a burst of searches with its bot check (HTTP 202) and Brave Search with a rate limit (HTTP 429)
  after a handful in quick succession. Dot spaces its searches (`search_spacing_s`), benches an engine that limits it
  and moves to the next rung, so a search fails outright only when every engine does (`dotx stats` counts these under
  the web failures; the browser rung needs Dot's own Chrome with a visible window). A SearXNG instance of the owner's
  (`[web] searxng_url`) sits first on the auto ladder when it is set and is benched for two minutes when it answers 429;
  its own engines rate-limit too, so an instance whose engines all refused falls through to the next rung. One that
  answers 403 has its JSON format off: the search says to list `json` in `search.formats` of its `settings.yml`. The
  hosted Ollama search has no such limit, and with `search_backend = "auto"` it is the next rung as soon as
  `OLLAMA_API_KEY` is set.
- The thresholds of the memory search were measured on a small hand-written set of English and Turkish notes
  (indicative, not a benchmark); another language, another embedding model or a much larger memory may need other
  `min_similarity` values.
- Schedules fire only while the daemon runs and the Mac is awake. `dotx service install` writes, lints and loads the
  launchd job that keeps the daemon running in your login session.
- MCP: tools only, over stdio or the streamable HTTP and SSE transports (no resources, prompts or sampling); a server
  at a URL may sign in with OAuth (see [MCP servers](extending.md#mcp-servers)).
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
- Undo: only `write_file`, `edit_file` and `write_notes` are in the journal, not shell commands, the browser or the
  desktop. A run keeps a copy of a file at its first change to it only, within `undo_max_file_bytes` and
  `undo_max_run_bytes`. Runs that change the same file are taken back in the order their changes were noted. `dotx undo`
  and `POST /api/runs/{id}/undo` need the run in the database (the journals of a deleted
  chat stay until `dotx prune --apply`).
