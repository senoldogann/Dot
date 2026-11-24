# Code mode

Code mode (items 1500 to 1507) lets a run write Python that calls its tools, instead of calling them one by one through
its context. The model writes a program, the program calls tools as functions, filters and loops over what they answer,
and only what it prints goes back to the model. A job of many calls or a lot of data costs a short program and a short
answer, not every result in the conversation.

This page describes what exists. It grows with the items: the tool `code_run` (item 1501) and the sandbox and the bridge
it runs on (item 1500) are below; the tools as files and the deny list's tests follow (item 1502), the settings, the
checks and the threat model (item 1503), and in a second pull request the swap of emails and phone numbers for tokens
(item 1504), skills as code (item 1505) and the measurements (item 1506).

## The tool `code_run` (item 1501)

`code_run` takes the Python 3.13 `code`, a one-line `title` (the row of the step) and `timeout_s`. The model reads only
what the code prints, stdout and stderr in the order they were written, as a head and a tail when it is long, and a
footer with the number of tool calls and the seconds: `[3 tool calls, 1.2 s]`. A code that ends by itself with nothing
printed is told so. A code that fails is an error result with the end of its traceback, in which Dot's own paths are said
short (`<tools>`, `<scratch>`, `<python>`, `<dot home>`, `~`) and the lines of the code are shown; one that is stopped
(its own time, its memory, its calls, its output, its scratch folder, the run's Stop) is an error that says which and what
to do about it, with what it printed before.

The code calls tools as functions of `dot_tools` (see "The tools as files" below), parameters as keywords. A result is the
JSON the tool said, as Python objects, when it said JSON, else its text, and a picture is saved in the scratch folder
(`images/`) and replaced in the text by its path. Results are not cut to what a model reads (`[agent] tool_output_chars`,
item 494: a code reads what it filters); a result is cut at `[code] result_chars` (5,000,000 characters) so that one tool
cannot flood the code's memory. `dot_tools.search_tools("words", detail="name"|"description"|"schema")` finds tools without
listing them all, and `dot_tools.list_servers()` says which servers the run's tools are of: Dot's own (`dot`) and each MCP
server. A code polls, retries and branches in Python: `time.sleep` is fine, and what counts against `[code] max_seconds` is
the code's own time, not the time a tool call takes or the time you take to answer a question.

### The tools as files (item 1502)

On its first `code_run` a run gets its tools written as files in `tools/dot_tools/`, and the code imports them as it would
any library:

```python
from dot_tools.servers.dot import read_file, search_files     # Dot's own tools
from dot_tools.servers.github import list_issues              # an MCP server's tools, by the server's name
rows = read_file(path="notes.txt")                            # parameters as keywords
```

`servers/<server>/<tool>.py` holds one function for each tool the run may call from code: a signature of keyword-only
parameters made from the tool's JSON schema (a required parameter has no default, an optional one is `None` and is left out
of the call when it is; `string` is `str`, `integer` `int`, `number` `float`, `boolean` `bool`, an array `list[...]`, an
object `dict[str, Any]`, an enum a `Literal`, `anyOf` a union), the tool's description and its parameters as the docstring
(`help(read_file)`), and one call into the bridge. A name that is a keyword of Python, begins with a digit or has a `-` is
written as Python needs (`class` is `class_`, a server `web-research` is `web_research`; an MCP server called `dot` is
`dot_mcp`), and the same tools get the same names every time. A server's package imports a tool's file when it is asked for,
so a code that needs two tools of a server of two hundred reads two files. A tool the code cannot call (the list above) has
no file, and a tool the run was not given has none either.

The code finds what it needs without a list in any prompt: `dot_tools.search_tools("words", detail="name")` gives
`["github.create_issue", ...]` (at most 25, the best first: the whole query as a name, then each word in a name, then in a
description), `detail="description"` adds what each does, the line that imports it and the name `dot_tools.call` takes, and
`detail="schema"` adds its parameters as a JSON schema; `dot_tools.list_servers()` gives each server, its kind and how many
tools it has; `dot_tools.call("<name>", ...)` calls a tool by its name. What `code_run`'s own definition costs the model does
not depend on how many tools the run has: it is the same 1,186 characters for ten tools and for three hundred.

The files are written again only when the tools of the run changed (a digest of the catalogue is kept in
`dot_tools/.digest`), and then the `servers` folder is removed first, so that a tool the run no longer has is not left to be
imported; the launcher, the bridge and `dot_tools` itself are written before every start. Measured with 300 tools (ten
servers of 30, eight parameters each, descriptions of 2,000 characters): 313 files, 2.5 MB, made in 10 ms; the first
`code_run` of the run, with the sandbox probe and the writing, took 84 ms, the next 29 ms.

### What it is to the policy

`code_run` is an action like a shell line: kind `shell`, risk `medium` (what the shell guard makes of `python3 -c`), the
summary `Run code: <first line> (N lines)`, the target the scratch folder, and the whole code as the body, which your rules
(their expressions are matched against the summary, the target and the body) and the review model read. Every approval mode
treats it as it treats a command: `supervised` and `cautious` ask, `ask`, `auto` and `yolo` as they do for `python3 -c`.
It is offered to the runs that act (`task` and `chat`), not to a research run, which may not run commands.

It adds nothing a command does not have, because the code itself can do next to nothing (the sandbox above) and everything
it asks of Dot is a call of the run's own: each tool call the code makes is validated, classified, checked against the
other runs, judged by the run's own gate (the secret a tainted run would send out, the mode, your rules, the preset, the
taint, the question to you or the review model), run under the tool's own time limit, masked of every secret Dot holds,
recorded in the undo journal where the tool writes files, and shown on the timeline, by the same path a call of the model's
takes (`agent/executor.py`: there is no second one). What differs is only what is on the timeline: the `tool_call` and
`tool_result` events of such a call carry `"via": "code"` and the id of the `code_run` call as `"parent_call_id"`, and its
id is that call's with a dot and its number (`call_7.3`). A question is the owner's as any other: it waits for you, and the
code waits (the wait is not its time). A refusal, by the policy, your rules, you or the review model, raises
`dot_tools.ToolRefused` in the code with the gate's words, and so does a step you did yourself; a call that fails raises
`dot_tools.ToolError`. A result that third parties can author (a page, a mail) taints the run at once, as for the model,
and marks the result of the `code_run`.

What a code cannot call: `code_run` (no code starts code, and a call made from code is handed no way to make calls either),
`finish`, `ask_user`, `ask_secret`, `browser_captcha`, `use_tools`, the plan and the reaction (`update_plan`,
`mark_plan_step`, `react`), the tools that start or steer another run, agent or chat (`delegate`, `consult`,
`message_agent`, `assign_work`, `work_orders`, `review_work`, `create_agent`, `create_team`, `send_to_chat`,
`pass_reply`), a campaign's own (`campaign_*`), `wait_until` (poll with `time.sleep`), and the families `terminal_*`,
`meeting_*` and `phone_*`. The code is told which, and why, and the gate is not asked. Everything else the run's mode and
its tool list allow is callable.

### Where it is offered

In Dot's own loop `code_run` is in the core group, so a run that has it has what it needs; the system prompt teaches it in a
few lines that hold no state of the run (when to write code: many calls, a lot of data, a loop or a poll; when to call the
tool: one call, or a result to read whole). On the owner's Claude plan it is a door tool like the others, found with
`ToolSearch`, and its lines are in the door's prompt. Its own definition is under 1,200 characters; no definition of a tool
the code can call goes into any prompt.

### Settings

`[code]` in `config.toml` (the defaults are in `defaults.toml`):

| Key            | Default     | What it is |
| -------------- | ----------- | ---------- |
| `enabled`      | `true`      | whether `code_run` is offered at all (read when the daemon starts) |
| `max_seconds`  | `120`       | the most the code's own time may be, 1 to 900; `timeout_s` asks for less |
| `max_mb`       | `512`       | the most resident memory the code may hold, 64 to 8192 |
| `output_chars` | `8000`      | the characters of what it prints that reach the model, the head and the tail |
| `max_calls`    | `200`       | the tool calls one `code_run` may make; the next one ends the code |
| `result_chars` | `5000000`   | the longest a tool's result may be when it is handed to the code |

The step's own limit is an hour: the code's time is `max_seconds`, and what a tool call takes and the time you take to answer
have limits of their own.

### Its folders

The folders of a run's code are `<workspace>/code/<run id>/tools` and `.../scratch`. The scratch folder is kept between the
`code_run`s of a run (a file one writes is there for the next), and a folder nothing has written to for three days is removed
the first time the daemon runs code after it starts. The code of each `code_run` is kept as `tools/snippets/<n>.py`.

## The sandbox (item 1500)

The code of a `code_run` runs in a macOS sandbox of its own, which is not the shell's. The shell's sandbox
([Safety](safety.md)) allows everything but Dot's own files, because a command is a program of yours. The code is text a
model wrote, which a page it read may have shaped, so its profile is the other way round: `sandbox-exec` with `(deny
default)`, and only what Python needs to start is allowed back.

What the code can do:

- start the interpreter and read its installation and the system's libraries;
- read the run's `tools` folder (`<workspace>/code/<run id>/tools`: the launcher and `dot_tools`, the package its tools
  are imported from);
- read and write the run's `scratch` folder (`<workspace>/code/<run id>/scratch`), its working folder, `HOME` and
  `TMPDIR`; it keeps what it writes there from one `code_run` of the run to the next;
- use threads, the whole standard library, the time (the local time zone too), and ask the daemon for tool calls over the
  bridge.

What it cannot do, and the kernel says so (`Operation not permitted`) whatever path, link, letter case or library it uses:

- reach the network: a socket can be made, and every connect, bind and send is refused (`urllib`, DNS and a Unix socket
  too);
- start a program: `subprocess`, `os.system`, `fork`, `exec` and `posix_spawn` are refused, and the process may hold no
  second process (`RLIMIT_NPROC` is 1, so a fork bomb dies at its first fork);
- read or write any file but those two folders: not yours, not Dot's home, not `~/Library`, not the keychain, not even
  the names in `/Users`;
- reach a service of the system (the clipboard, the keychain, Launch Services) or signal another process;
- map a page of memory that is both writable and executable;
- learn the name of the Mac or the user: `os.uname()`, `socket.gethostname()` and `pwd` are refused, so `import ctypes` and
  `import platform` fail (a `ctypes` call to `system` or `open` is refused by the profile all the same);
- see the daemon's environment: it gets `HOME` and `TMPDIR` (the scratch folder), `LANG`, and `TZ`, `LC_ALL` and
  `LC_CTYPE` when set, and none that a command's environment is held back for (a name that says it holds a secret, an
  address with a password in it).

The kernel holds the process to limits too: CPU time, the largest file it writes (64 MB), the files it has open (256), one
process, no core file. What the kernel cannot hold, Dot watches from outside, many times a second, and answers with a
kill of the whole process group: the code's own time (`time.sleep` is part of it; the time a tool call takes and the time
you take to answer are not, they have limits of their own), its resident memory (macOS has no kernel limit for it: this is
a watch, and a single allocation larger than what is read between two looks goes over before it is seen), what it prints
(a head and a tail are kept; past 64 MB it is stopped), what it leaves in its scratch folder (256 MB and 5,000 entries; a
folder over that is emptied before the next code), the run's Stop, and a frame on the bridge that is not a call.

### It fails closed

The only way Dot starts the code is behind `sandbox-exec`. Before the first `code_run` of the daemon Dot probes it once:
it starts Python in a throwaway sandbox and checks that the code can use its two folders and that a file outside, a write
outside, `/Users`, the network, `fork` and a spawn are each refused by the sandbox itself. A success is kept for as long as
the daemon runs, a failure for five minutes. A Mac where the probe fails (no `sandbox-exec`, a profile the kernel refuses,
a sandbox that does not hold) runs no code: `code_run` answers that code mode needs the macOS sandbox, which did not start,
and why. Nothing is ever run without it.

The Python the code runs on is the daemon's base interpreter by its real path, never a virtual environment's (whose
packages are Dot's), and its installation's folder is read whole, so a folder too broad for that (the root, `/usr`, a home
folder) or one that holds your home, Dot's home or the run's own folders is refused.

### The bridge

The way out of the sandbox is two pipes the process inherits: the code writes one line of JSON for each call,
`{"id", "tool", "args"}`, and reads one back, `{"id", "ok", "text", "images"}` or `{"id", "ok": false, "error",
"refused"}`. Dot reads each line as exactly that and nothing else: a line past 32 MiB, one that is no JSON, one with another
key or a key of another type, or a number out of its turn is a malformed frame and ends the code. At most `max_calls` calls
(200) go over it; the next ends the code. The daemon does not read the next request until the code has read the last answer.
What the code prints is its own stdout, never the protocol.

### The folders

`<workspace>/code/<run id>/` holds `tools/` (written by Dot before every start; the code reads it and cannot change it)
and `scratch/`. They are in the workspace, where your run's commands may write too, and Dot writes into them with your
rights, so it never follows a link there: each folder is opened from the one above it without following links, a file is
written beside its target and moved over it, and a link a command left where a folder or a file is to be makes the write
fail instead of leading it elsewhere.

## The threat model (item 1503)

Code mode must not widen what a run can do. The code is text a model wrote, which a page it read may have shaped, so it is
treated as hostile text that has to be useful. What it can reach, and what stops it, attack by attack. Each row has a test
that makes the attack with no sandbox first (so that it is shown to be an attack) and then in the sandbox, and a twin: the
harmless thing next to it, which works.

| The code tries to | What stops it | Where it is tested |
| ----------------- | ------------- | ------------------ |
| open a file outside its scratch folder; write one; reach Dot's home, `~/Library`, the keychain, `/Users` | the profile: `(deny default)`, read of its interpreter and its `tools`, read and write of its `scratch` | `test_code_attacks.py` (read, write, tools read-only, Dot's home) |
| use the network: a socket, `urllib`, DNS, a bind, a UDP send | the profile: no `network*` | `test_code_attacks.py` (network) |
| start a program: `subprocess`, `os.system`, `fork`, `exec`, `posix_spawn`, `ctypes` | the profile (no `process-fork`, no `exec` but the interpreter) and one process (`RLIMIT_NPROC`) | `test_code_attacks.py` (programs, ctypes, fork bomb) |
| read the daemon's secrets from its environment | a clean environment: its two folders, the language and the zone, and nothing a command's environment is held back for | `test_code_attacks.py` (environment) |
| run for ever, or sleep for ever | the watch of the code's own time, and a CPU limit in the kernel | `test_code_attacks.py` (infinite loop) |
| hold the memory | the watch of its resident memory (a watch, not a wall: measured overshoot below) | `test_code_attacks.py` (memory bomb) |
| print without end; fill the scratch folder; write a huge file | a head and a tail kept and the code stopped at 64 MiB; a quota on the folder; a file size limit in the kernel | `test_code_attacks.py` (print, scratch, file size) |
| send the daemon something that is not a call: not JSON, another key, a call out of its turn, a line of 32 MiB; never read its replies | the bridge reads exactly one shape and ends the code on any other; no request is read until the last reply is read | `test_code_bridge.py` |
| make more than `max_calls` calls | the 201st ends the code | `test_code_attacks.py`, `test_code_run_gate.py` (the gate saw exactly 200) |
| call a tool that ends the run, asks you, starts a run, or is `code_run` itself | the deny list, answered without the gate being asked; and a call made in code has no way to make calls | `test_code_run_gate.py` (each name; code from code with the list switched off) |
| make a call the run's own policy would refuse (a write in a research run, a step you said no to, a secret sent out) | the same gate: each call is judged one by one, and a refusal is `ToolRefused` in the code | `test_code_run_gate.py`, `smoke/test_code_mode.py` (a real gate and your approvals) |
| use what a page told it to, after reading the page | the run is tainted as the call that read it ends, so the next call of the same code is judged under it | `test_code_run_gate.py` (taint) |
| keep what a tool answered out of the masking | every result is masked of the secrets Dot holds before the code reads it, and what it prints is masked again | `test_code_run_gate.py` (secret) |
| have the daemon write through a link it planted in the run's folders | every write of the daemon there goes through a descriptor of the folder above, without following a link | `test_code_sandbox.py` (links) |
| run where the sandbox does not start, or does not hold | the probe, and no other way to start the code: nothing runs and the model is told why | `test_code_attacks.py`, `test_code_run_gate.py`, `test_doctor_rows.py` |
| outlive the run: the run's Stop, a cancelled step, a daemon that went away | the process group is killed whatever ends the call; the code leaves when its parent is gone | `test_code_attacks.py` (Stop), `test_code_bridge.py` (a fault) |

What the code does reach: what it prints, which the model reads (with the model's own caution towards what tools answered, as
before), what the tools it may call answer (each judged by the gate), and its scratch folder. What was not shown: that no
hole exists in `sandbox-exec` itself; the profile on another macOS than 26.6.2; a Mac that is not Apple silicon. The memory
limit is a watch of 25 ms: a single allocation of 3 GB against a limit of 300 MB was seen and killed at 463 MB held.
