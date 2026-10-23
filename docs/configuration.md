# Configuration

`config.toml` in the home directory is merged over the packaged `src/dotagent/defaults.toml`, which lists every key with
a comment and is the reference. A key that does not exist is an error, and so is a number that would stop Dot from
working: a count of runs, tools, tabs, tries (a page's `navigation_attempts` too) or calls at once below 1, the steps of
a browser or desktop batch below 1, a limit of the progress watchdog that would fire at every step (`stall_limit` or
`revisit_window` below 2, `failure_streak` below 1), a count of nudges or send-backs below 0, a timeout or the
scheduler's tick of 0 or less, a port outside 1 to 65535, a browser page smaller than 320 by 240 or larger than 3840 by
2160, a screenshot quality outside 1 to 100 (the desktop's too), a context budget of 0 or less, a tool output under
2,000 characters, a default shell timeout above `max_timeout_s`. So is a value no run could use: an address of a
provider or of the judge that is not `http://` or `https://` with a host, a model that is not `<provider>:<model>` with
a provider Dot knows (`[models] search` an Anthropic one), a site under `[policy.spend]` that is not a bare host in
lower case without `www.`, such as `example.com`. The error names each setting; for a key this version of Dot does not
have it says that a newer Dot may add it (a daemon that runs an older checkout than the one that wrote the file refuses
it). Two environment variables of the process move things: `DOT_HOME` (the home directory) and `DOT_LOG_DIR` (the logs).

| Path (under `~/Library/Application Support/Dot`) | Content                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `config.toml`                                    | your settings                                                        |
| `.env`                                           | API keys (mode 0600)                                                 |
| `dot.sqlite3`                                    | runs, events, conversations, notes, schedules, rules, approvals      |
| `api-token`                                      | the bearer token of the web UI and the API (mode 0600)               |
| `vapid-key.pem`                                  | the daemon's web push key pair, made on its first start (see [Dot on the phone](phone.md#dot-on-the-phone)) |
| `workspace/`                                     | the working directory of the shell and the file tools                |
| `skills/`, `plugins/`                            | skills and plugins Dot always reads (more via `[skills]`/`[plugins]` dirs) |
| `plugins-data/`                                  | each plugin's own files, its `${CLAUDE_PLUGIN_DATA}` (`plugins-data/<name>`) |
| `runtime/`                                       | what the desktop app installs: Dot's package, the Python it runs on, uv's cache, the links to the tools the app carries and the marker of the install (item 575; a run's commands cannot change it) |
| `playbooks/`                                     | the skills Dot learned (`learned-<kind>`) and those you taught it (`taught-<kind>`), the retired ones under `.retired` |
| `evals/`                                         | the reports of eval runs, one file each                              |
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
| `daemon.running`                                 | left while a daemon runs and removed when it ends without an error   |
| `STOP`                                           | exists while the emergency stop is engaged                           |
| `run-owners/`                                    | one lock per process that drives runs (the daemon, `dotx run`)       |
| `backups/`                                       | the copies of the database, unless `[store] backup_dir` names a folder |
| `backups-partial/`                               | the uncompressed file of a copy under way                            |

Deleting a chat deletes the screenshot files that only it named, and the daemon thins its copies of the database
(below). Nothing else is removed by itself: old runs and their
events stay until their chats are deleted or `dotx prune --apply` finds them old, and the journals of file changes
until it finds them old. `dotx prune` lists the events of the runs that ended more than `[store] retention_days`
(90) days ago (the steps, tool calls and results of their timelines; the runs keep their answers, numbers and
conversations), the screenshot files that no chat names any more (what chats deleted earlier left behind), the free
space inside the database file and the journals of file changes (`dotx undo`) whose last change is older than
`[agent] undo_keep_days`; `dotx prune --apply` deletes those events, and with them the screenshots only they named,
those files and journals, and gives the space back to the disk, after which a delete returns its space by itself. Two
things those events said are kept (item 556): that a run ended after reading outside content, which decides how a run
that resumes it, or a schedule's firing that builds on it, starts; and that you saved a lesson from a run's page, which
keeps the nightly reflection from folding or resting it. The earlier versions of a chat's history that compacting keeps
aside, so that the chat can be rewound, stay until the chat is deleted or rewound; `dotx prune` lists how many messages
they are and their size, and the screenshots they name stay with them. The database is locked for a moment while it
compacts, so run it when no run is active.

**Copies of the database** (item 526). While the daemon runs, it copies the database every `[store] backup_every_hours`
hours (1), and as it starts when no copy is younger, whenever the database changed since the last copy; a database found
unchanged is looked at again every ten minutes, so a write after a quiet spell is copied soon. SQLite's online backup
makes the copy in one read of the database, which in its write-ahead mode holds back none of the daemon's writes, so
runs go on while it is made; the copy is checked (`quick_check`), compressed and kept as `dot-<UTC time>.sqlite3.gz`
(mode 0600) in `[store] backup_dir`, written under a hidden name and linked to its own name once whole (renamed on a
disk without links, such as iCloud Drive or exFAT). The newest `backup_keep_hourly` (24) copies named before now are
kept, the newest copy of each of the last `backup_keep_days` (14) days, and a copy named after now (a clock that was
ahead, another Mac's) until its name is past, taking no place of these; only files named as copies are ever removed from
the folder. An empty `backup_dir` is `<home>/backups` (a folder of your own is absolute or starts with `~`), which goes
with the home folder if that is lost: point it at an external disk, or at iCloud Drive (`~/Library/Mobile
Documents/com~apple~CloudDocs/Dot Backups`), to keep the copies off this Mac, and give each Mac a folder of its own
there, as Dot takes every copy in the folder for its own. A run reads and changes neither the copies nor that folder, as
it does not the database. While a copy is made, its uncompressed file is in `<home>/backups-partial` (0600), removed
once compressed; what a copy cut off left is cleared by the next. `backup_every_hours = 0` makes none. `dotx backup now`
makes one at once and `dotx backup list` lists them; `dotx doctor` says how old the newest is, calls it late only when
the database changed since, and says so when the folder cannot be read. Two copies made in the same second keep the
first, and a copy named after now (a clock that was ahead, another Mac's) is left out of when the next is due. A copy
that fails is logged (`backup_failed`) and tried again ten minutes later. To go back to a copy, stop the daemon (`dotx
service uninstall`), move `dot.sqlite3` and its `dot.sqlite3-wal` and `dot.sqlite3-shm` aside (an old write-ahead log
left beside the copy would be applied to it), unpack the copy in its place (`gunzip -c <copy> > "<home>/dot.sqlite3"`),
and start the daemon again (`dotx service install`).

Logs are structured and go to `~/Library/Logs/Dot/dotx.log` (another folder with `DOT_LOG_DIR`: `dotx service status`
names the one in use, and the sign-in screen of a daemon that could not answer points to it); at 10 MB it is moved to
`dotx.log.1` (the one before is dropped) and a new one is started (two processes that find it full together take
turns). A line the disk will not take is dropped, never raised into the code that logs, and the next line that is
written says how many were lost (`log_lines_dropped`). The output of the launchd job goes to
`daemon.out.log` and `daemon.err.log` in the same directory, and the stderr of each MCP server to `mcp-<name>.log`.
A `dotx` command that ends with an error, the daemon's start included (a configuration it refuses, a daemon already
running, an address taken), writes a `command_failed` event to `dotx.log` too, with the command and the error;
`dotx service status` and `dotx service install` read it back (see [Daemon](daemon.md)).
