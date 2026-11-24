# Code mode

Code mode (items 1500 to 1507) lets a run write Python that calls its tools, instead of calling them one by one through
its context. The model writes a program, the program calls tools as functions, filters and loops over what they answer,
and only what it prints goes back to the model. A job of many calls or a lot of data costs a short program and a short
answer, not every result in the conversation.

This page describes what exists. It grows with the items: the sandbox and the bridge (item 1500, below) come first, then
the tool `code_run` (item 1501), the tools as files (item 1502), the settings and the checks (item 1503), and in a second
pull request the swap of emails and phone numbers for tokens (item 1504), skills as code (item 1505) and the measurements
(item 1506).

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
