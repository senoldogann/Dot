# Configuration

`config.toml` in the home directory is merged over the packaged `src/dotagent/defaults.toml`, which lists every key with
a comment and is the reference. A key that does not exist is an error, and so is a number that would stop Dot from
working: a count of runs, tools, tabs, tries or calls at once below 1, a timeout or the scheduler's tick of 0 or less, a
port outside 1 to 65535, a browser page smaller than 320 by 240 or larger than 3840 by 2160, a screenshot quality
outside 1 to 100, a context budget of 0 or less, a tool output under 2,000 characters, a default shell timeout above
`max_timeout_s`. So is a value no run could use: an address of a provider or of the judge that is not `http://` or
`https://` with a host, a model that is not `<provider>:<model>` with a provider Dot knows (`[models] search` an
Anthropic one), a site under `[policy.spend]` that is not a bare host in lower case without `www.`, such as
`example.com`. The error names each setting; for a key this version of Dot does not have it says that a newer Dot may
add it (a daemon that runs an older checkout than the one that wrote the file refuses it). Two environment variables of
the process move things: `DOT_HOME` (the home directory) and `DOT_LOG_DIR` (the logs).

| Path (under `~/Library/Application Support/Dot`) | Content                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `config.toml`                                    | your settings                                                        |
| `.env`                                           | API keys (mode 0600)                                                 |
| `dot.sqlite3`                                    | runs, events, conversations, notes, schedules, rules, approvals      |
| `api-token`                                      | the bearer token of the web UI and the API (mode 0600)               |
| `workspace/`                                     | the working directory of the shell and the file tools                |
| `skills/`, `plugins/`                            | skills and plugins Dot always reads (more via `[skills]`/`[plugins]` dirs) |
| `added-servers.json`                             | the MCP servers you added from other agents, by name and fingerprint |
| `connected-servers.json`                         | the MCP servers you added in Customize, by address or command        |
| `mcp-sign-ins/`                                  | what signing in to those servers gave: tokens and client (mode 0600) |
| `opencode/`                                      | the OpenCode server Dot starts, the chats' sessions, the free models |
| `themes/`                                        | your own themes, one theme file each (Settings › Appearance)         |
| `browser-profile/`                               | the Default profile of Dot's Chrome, the browser tools' own          |
| `browser-incognito/`                             | the Incognito profile of Dot's Chrome, emptied as Chrome starts and closes |
| `browser-profiles/`                              | the profiles of Dot's Chrome added in Settings › Browser, one folder each |
| `looks/`                                         | Quick Look's drawings of the Office files the panel showed           |
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
A `dotx` command that ends with an error, the daemon's start included (a configuration it refuses, a daemon already
running, an address taken), writes a `command_failed` event to `dotx.log` too, with the command and the error;
`dotx service status` and `dotx service install` read it back (see [Daemon](daemon.md)).
