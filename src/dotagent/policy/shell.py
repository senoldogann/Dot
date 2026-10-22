"""Risk assessment for shell commands: a conservative guardrail against accidents, not a sandbox.

A line is read the way the shell reads it before anything runs (:func:`_read`): comments, quotes, here-documents and
the commands substituted into it, each of which is judged as a command of its own. Its simple commands are then judged
in order, each with what the line set up before it (:class:`ShellScope`): the directories a ``cd`` may have moved it
to, the values it gave its variables and the symbolic links it made. What only the running line knows is judged at its
worst: such a path counts as one outside the workspace, and such code (a command named by a substitution, ``eval`` of
one, a line that cannot be parsed) as a destructive step outside it, which every preset asks about. Neither what a
command does with the files it walks (a recursive copy of the home folder) nor the code of a script it runs is
followed."""

import fnmatch
import os
import re
import shlex
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, replace
from pathlib import Path

from dotagent.core.types import FrozenModel
from dotagent.policy.paths import (
    CHANGES_DOT,
    PathClass,
    classify_path,
    expand_path,
    glob_reaches_protected,
    kernel_path,
    mentions_protected,
    within,
)
from dotagent.policy.types import Flag, Risk

_RISK_ORDER: dict[Risk, int] = {"safe": 0, "low": 1, "medium": 2, "high": 3}

# Keys whose effect on a terminal's line the text tells, the same for readline, zle and the terminal's own line
# discipline: Enter, Ctrl-C, Ctrl-U and Delete; Tab completes a word, which the text cannot tell, but runs nothing.
_SUBMITS: str = "\n\r"
_DROPS_LINE: str = "\x03"
_KILLS_LINE: str = "\x15"
_ERASES_CHAR: str = "\x7f"
_COMPLETES: str = "\t"

_OPERATORS: frozenset[str] = frozenset(
    {";", "&&", "||", "|", "&", "|&", "&|", "(", ")", ";;", ";&", ";;&", ";|", "{", "}"}
)
_SHELL_OPERATOR: re.Pattern[str] = re.compile(
    r";;&|;;|;&|;\||&&|\|\||\|&|&>>\||&>>|&>\||&>|&\||>>&\||>>&|>>\||>>|>&\||>&|>\||<&|<<<|<<|<>|[;&|()<>]"
)
# zsh spells the redirects that write past noclobber with "!" as well as "|" (">!", ">>!", "&>!", ">&!", "&>>!",
# ">>&!"): a "!" right after an unquoted ">" or ">&" (not "<>") is that letter, which the reader turns into "|".
_CLOBBER_BANG: re.Pattern[str] = re.compile(r"(?:(?<=>)(?<!<>)|(?<=>&))!")
_OPERATOR_CHARS: str = ";&|()<>"
# A quoted or escaped operator character is a letter of its word: the reader writes it as one of these stand-ins, which
# the tokenizer takes for a letter, and the words get the character back (:func:`_commands`).
_LITERAL_OPERATORS: str = ""
_HIDE_OPERATORS: dict[int, int] = str.maketrans(_OPERATOR_CHARS, _LITERAL_OPERATORS)
_SHOW_OPERATORS: dict[int, int] = str.maketrans(_LITERAL_OPERATORS, _OPERATOR_CHARS)
_WORD_BREAKS: str = " \t\n;&|()<>"
# What stands before a reserved word where a command begins, and after ``case`` (its word follows) and ``esac``.
_COMMAND_START: tuple[str, ...] = ("", " ", "\t", "\n", ";", "&", "|", "(")
_CASE_ENDS: tuple[str, ...] = (" ", "\t", "\n")
_ESAC_ENDS: tuple[str, ...] = ("", " ", "\t", "\n", ";", "&", "|", ")")
# What a command begins after, blanks between: an operator or a newline, and the reserved words a command follows.
_OPENS_COMMAND: str = "\n;&|("
_BEFORE_COMMAND: frozenset[str] = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"})
# What ends an arm of a ``case``, so that a pattern follows (``;|`` is zsh's).
_ARM_ENDS: frozenset[str] = frozenset({";;", ";&", ";;&", ";|"})
# A word only the running line knows (what a substitution prints, what an unknown pipe passes on), and the temp path a
# plain ``mktemp`` prints, which a later ``rm -rf "$dir"`` may remove.
_SUBSTITUTED: str = "$__dot_substituted__"
_TEMP_STAND_IN: str = str(Path(tempfile.gettempdir()) / "dot-mktemp")
_MKTEMP: re.Pattern[str] = re.compile(r"\s*mktemp(?:\s+-[dqtu]+)*(?:\s+[\w.-]+)?\s*")
_DOCUMENT: re.Pattern[str] = re.compile(r"__dot_document_(\d+)")
_ANSI_C: re.Pattern[str] = re.compile(
    r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c.|.)", re.DOTALL
)
_ANSI_ESCAPES: dict[str, str] = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
# A zsh glob qualifier that runs code (``*(e:'cmd':)``, ``*(+fn)``), and expansions that run a variable's text as code
# (zsh's ``${(e)var}``, bash's ``${var@P}``).
_CODE_QUALIFIER: re.Pattern[str] = re.compile(r"\([^)]*?(?:e[^\w\s)]|\+)")
_EXPANDS_AS_CODE: re.Pattern[str] = re.compile(r"\$\{\([^)]*e[^)]*\)|@P\}")
_VARIABLE: re.Pattern[str] = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*)|([$!?#]))")
# Variables the shell sets as the line runs: numbers, or values the line does not show.
_NUMERIC_VARIABLES: frozenset[str] = frozenset({"RANDOM", "SECONDS", "LINENO", "EPOCHSECONDS", "PPID", "UID", "EUID"})
# Any expansion of a word, for one :func:`_expansions` cannot spell out (``${HOME%/}``, a substitution's stand-in).
_UNKNOWN_EXPANSION: re.Pattern[str] = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9$!?#@*-])")
_RUNTIME_VARIABLES: frozenset[str] = frozenset({"_", "OLDPWD", "REPLY"})
_BRACES: re.Pattern[str] = re.compile(r"\{([^{},]*(?:,[^{},]*)+)\}")
_NAME: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# An assignment: a variable's name, an element's subscript (``arr[0]=x``) or ``+=``, then the value; and the start of
# one that gives an array its values (``files=(*.txt)``).
_ENV_ASSIGNMENT: re.Pattern[str] = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=(.*)", re.DOTALL)
_ARRAY_ASSIGNMENT: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_GLOB_CHARS: str = "*?["
_MOST_VALUES: int = 64
# How many parts of a word that only the running line knows :func:`_leads_toward_protected` reads (a leading one stands
# for the home folder, then the first that follows decides).
_MOST_UNKNOWN: int = 8
_MOST_PLACES: int = 16
_DEEPEST: int = 12
_QUIET_TARGETS: frozenset[str] = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})
# The reason of a part that only carries the files it changes (:func:`_changing`).
_CHANGES: str = "changes files"
# Writers that change no file's content: a folder made, a file touched, a mode or an owner changed.
_MODE_WRITERS: frozenset[str] = frozenset({"touch", "mkdir", "chmod", "chown", "chgrp"})

# Reserved words in front of a command, and the heads of compound commands that run nothing themselves.
_KEYWORDS: frozenset[str] = frozenset(
    {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "!", "time", "coproc", "always"}
)
_HEADERS: frozenset[str] = frozenset({"for", "select", "case", "foreach"})
_DECLARERS: frozenset[str] = frozenset({"export", "local", "declare", "typeset", "readonly", "integer", "float"})
# The commands whose name holds a glob character and is not a pattern.
_TEST_COMMANDS: frozenset[str] = frozenset({"[", "[["})
# Variables whose value a program runs as a command line (git's ssh, pager and editor, ``less``'s preprocessor), and
# ones that make a program load code from where they point or take settings that run commands.
_COMMAND_VARIABLES: frozenset[str] = frozenset(
    {
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_PAGER",
        "PAGER",
        "MANPAGER",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "EDITOR",
        "VISUAL",
        "FCEDIT",
        "GIT_EXTERNAL_DIFF",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "SUDO_ASKPASS",
        "LESSOPEN",
        "LESSCLOSE",
        "PROMPT_COMMAND",
        "BROWSER",
        "RSYNC_RSH",
    }
)
# ``ENV`` is not one: a POSIX shell reads it only when it is interactive, which a line's shells are not.
_LOADING_VARIABLES: frozenset[str] = frozenset(
    {
        "BASH_ENV",
        "ZDOTDIR",
        "DYLD_INSERT_LIBRARIES",
        "LD_PRELOAD",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_EXEC_PATH",
    }
)

_WRAPPERS: frozenset[str] = frozenset(
    {
        "env",
        "command",
        "exec",
        "nohup",
        "nice",
        "ionice",
        "caffeinate",
        "timeout",
        "stdbuf",
        "builtin",
        "arch",
        "noglob",
        "nocorrect",
        "watch",
        "xcrun",
        "script",
        "unbuffer",
        "taskpolicy",
        "gtimeout",
        "gnice",
        "gstdbuf",
    }
)
# What a wrapper is past its options: the command it runs, after its duration (``timeout 5 rm``) or the file it logs to
# (``script -q log rm``).
_WRAPPER_SKIPS: dict[str, int] = {"timeout": 1, "gtimeout": 1, "script": 1}
# The precommand modifiers that run a builtin of the shell itself, so ``builtin cd ~`` moves the line as ``cd ~`` does.
_BUILTIN_MODIFIERS: frozenset[str] = frozenset({"builtin", "command", "noglob", "nocorrect"})
# Tools that run the command their operands name, after a word of their own (``uv run pytest``, ``bundle exec rake``)
# or at once (``npx``, ``bunx``). Their options that take a value are many and not all known here, so an operand right
# after an option may begin the command (:func:`_runner_starts`), and each such reading is judged.
_RUNNERS: dict[str, frozenset[str]] = {
    "uv": frozenset({"run"}),
    "poetry": frozenset({"run"}),
    "pipenv": frozenset({"run"}),
    "pdm": frozenset({"run"}),
    "hatch": frozenset({"run"}),
    "rye": frozenset({"run"}),
    "conda": frozenset({"run"}),
    "bundle": frozenset({"exec"}),
    "rbenv": frozenset({"exec"}),
    "pyenv": frozenset({"exec"}),
    "asdf": frozenset({"exec"}),
    "mise": frozenset({"exec", "x"}),
    "direnv": frozenset({"exec"}),
    "npm": frozenset({"exec"}),
    "pnpm": frozenset({"exec", "dlx"}),
    "yarn": frozenset({"exec", "dlx"}),
    "npx": frozenset(),
    "bunx": frozenset(),
    "uvx": frozenset(),
}
# Runners whose own operands come before the command: direnv's folder (``direnv exec DIR cmd``), and mise's tools
# (``mise x node@20 -- node``), as many as are given, which None stands for: each may be the command too.
_RUNNER_OPERANDS: dict[str, int | None] = {"direnv": 1, "mise": None}
# Options of these runners that take no value, so the word after one is the command.
_RUNNER_SWITCHES: frozenset[str] = frozenset(
    {"-y", "--yes", "--no", "-q", "--quiet", "--frozen", "--locked", "--no-sync", "--isolated", "--offline"}
)
_PRIVILEGE_WRAPPERS: frozenset[str] = frozenset({"sudo", "doas", "su", "pkexec"})
# The options of a wrapper that take a value, so that the value is not taken for the command it runs.
_WRAPPER_VALUES: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "-C", "-S", "-P", "--unset", "--chdir", "--split-string"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "--class", "--classdata", "--pid"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "caffeinate": frozenset({"-t", "-w"}),
    "exec": frozenset({"-a"}),
    "xcrun": frozenset({"-sdk", "--sdk", "-toolchain", "--toolchain"}),
    "script": frozenset({"-t", "-T"}),
    "taskpolicy": frozenset({"-c", "-d", "-g", "-t", "-l", "-p"}),
    "gtimeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "gnice": frozenset({"-n", "--adjustment"}),
    "gstdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "arch": frozenset({"-arch", "-e", "-d"}),
    "watch": frozenset({"-n", "--interval"}),
    "sudo": frozenset(
        {
            "-u",
            "-g",
            "-h",
            "-p",
            "-U",
            "-C",
            "-D",
            "-r",
            "-t",
            "-T",
            "-R",
            "--user",
            "--group",
            "--host",
            "--prompt",
            "--other-user",
            "--close-from",
            "--chdir",
            "--role",
            "--type",
            "--command-timeout",
            "--chroot",
        }
    ),
    "doas": frozenset({"-u", "-C"}),
    "su": frozenset({"-c", "-s", "-g", "-G", "--command", "--shell", "--group", "--supp-group"}),
    "pkexec": frozenset({"--user"}),
}
_SHELLS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
# A shell's own standard input, named as the file it reads commands from (``source /dev/stdin``).
_STDIN_FILES: frozenset[str] = frozenset({"/dev/stdin", "/dev/fd/0"})
_SHELL_VALUES: frozenset[str] = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_INTERPRETERS: frozenset[str] = frozenset(
    {"python", "python3", "node", "ruby", "perl", "php", "swift", "deno", "bun", "lua", "Rscript", "julia"}
)
_INLINE_CODE: frozenset[str] = frozenset({"-c", "-e", "-E", "--eval", "-p"})
# node's long spellings of its inline code, as the word before the code or with "=code" attached.
_LONG_INLINE_CODE: tuple[str, ...] = ("--eval", "--print")
# Code that reaches the network: a URL, or the client libraries and calls of the interpreters above.
_NETWORK_CODE: re.Pattern[str] = re.compile(
    r"\w+://|urllib|\brequests\b|http\.client|httpx|aiohttp|\bsocket\b|\bfetch\s*\(|XMLHttpRequest|axios|"
    r"\bnet/http\b|Net::HTTP|LWP::|HTTP::Tiny|open-uri|URLSession|curl_exec|file_get_contents|fsockopen|"
    r"smtplib|ftplib|imaplib|paramiko|Net::SMTP|Net::FTP",
    re.IGNORECASE,
)
# Code that runs a command of the network (os.system("scp ..."), `ssh ...`, system("curl ...")).
_NETWORK_COMMAND_CODE: re.Pattern[str] = re.compile(
    r"\b(?:curl|wget|scp|sftp|rsync|ssh|ncat|socat|telnet)\b|\bnc\s+-?[\w.]"
)
_NETWORK_FETCHERS: frozenset[str] = frozenset({"curl", "wget", "http", "https", "xh", "aria2c"})
_REMOTE_COPY: frozenset[str] = frozenset({"scp", "sftp", "rsync", "nc", "ncat", "socat", "telnet", "ftp", "ssh"})
# A path of another machine as scp and rsync take one: ``host:path``, ``user@host:path``, ``host::module``, ``rsync://``.
_REMOTE_PATH: re.Pattern[str] = re.compile(r"[^/]*:")
_TUNNELS: frozenset[str] = frozenset({"ngrok", "cloudflared", "localtunnel", "lt", "bore", "serveo"})
# The mailers of the command line (item 429): sendmail and msmtp send what they are given; mail, mailx and mutt send
# when a word follows them (a recipient), and only open the mailbox otherwise.
_SENDERS: frozenset[str] = frozenset({"sendmail", "msmtp"})
_MAILERS: frozenset[str] = frozenset({"mail", "mailx", "mutt"})
# Command lines of code hosts: what they print (issues, reviews, comments) is written by other people.
_CODE_HOSTS: frozenset[str] = frozenset({"gh", "glab", "hub", "tea"})
# What a code host's command line does that only reads (``gh pr view``, ``gh run list``, ``gh repo clone``).
_CODE_HOST_READS: frozenset[str] = frozenset(
    {"view", "list", "ls", "status", "diff", "checks", "search", "browse", "clone", "download", "checkout", "watch"}
)
_CODE_HOST_SENDS: frozenset[str] = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_GIT_CLONE_VALUES: frozenset[str] = frozenset(
    {
        "-b",
        "--branch",
        "-o",
        "--origin",
        "-u",
        "--upload-pack",
        "-c",
        "--config",
        "-j",
        "--jobs",
        "--depth",
        "--reference",
        "--reference-if-able",
        "--separate-git-dir",
        "--template",
        "--shallow-since",
        "--shallow-exclude",
        "--filter",
        "--server-option",
        "--bundle-uri",
    }
)
_GIT_REMOTE_READS: frozenset[str] = frozenset({"clone", "fetch", "pull", "ls-remote", "submodule"})
_POWER: frozenset[str] = frozenset({"shutdown", "reboot", "halt", "poweroff", "logout"})
_PROCESS_KILLERS: frozenset[str] = frozenset({"kill", "killall", "pkill"})
_SYSTEM_TOOLS: frozenset[str] = frozenset(
    {
        "scutil",
        "networksetup",
        "pmset",
        "systemsetup",
        "spctl",
        "csrutil",
        "tccutil",
        "dscl",
        "sysadminctl",
        "nvram",
        "crontab",
        "at",
        "systemextensionsctl",
        "kextload",
        "kextunload",
        "mdutil",
        "profiles",
        "softwareupdate",
        "dseditgroup",
        "pwpolicy",
        "fdesetup",
    }
)
_DISK_DESTROYERS: frozenset[str] = frozenset({"mkfs", "newfs", "fdisk", "gpt", "format", "newfs_apfs", "newfs_hfs"})
_WRITERS: frozenset[str] = frozenset(
    {"mv", "cp", "ln", "install", "touch", "mkdir", "chmod", "chown", "chgrp", "tee", "truncate", "ditto", "rsync"}
)
# Writers that read every operand but the last one, which they write.
_COPIES: frozenset[str] = frozenset({"cp", "ln", "install", "ditto", "rsync", "scp"})
# Copies and moves that can be given the folder they write into by an option (GNU's -t and --target-directory).
_TARGET_FOLDER_COMMANDS: frozenset[str] = frozenset({"cp", "mv", "ln", "install"})
_TARGET_FOLDER_OPTION: str = "--target-directory"
# The redirects that empty the file they name before they write (``>|``, ``&>|`` and ``>&|`` write past zsh's noclobber,
# spelt with "!" too); ``>>`` and its kin append.
_REPLACING_REDIRECTS: frozenset[str] = frozenset({">", ">|", "&>", ">&", "&>|", ">&|"})
# Copies and moves that put a file in the place of one that is there, unless told to keep it.
_REPLACING_COPIES: frozenset[str] = frozenset({"cp", "mv", "install", "ditto"})
_REPLACED: str = "replaces a file that is there outside the workspace"
_TARS: frozenset[str] = frozenset({"tar", "bsdtar", "gtar"})
# unzip's switches that list, test or print an archive instead of unpacking it.
_UNZIP_READS: frozenset[str] = frozenset({"-l", "-t", "-v", "-p", "-z", "-Z"})
_SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "fd",
        "pwd",
        "echo",
        "printf",
        "date",
        "which",
        "whereis",
        "type",
        "whoami",
        "id",
        "uname",
        "sw_vers",
        "hostname",
        "df",
        "du",
        "ps",
        "pgrep",
        "stat",
        "file",
        "diff",
        "cmp",
        "less",
        "more",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "printenv",
        "true",
        "false",
        "test",
        "[",
        "[[",
        "sleep",
        "seq",
        "awk",
        "jq",
        "yq",
        "column",
        "nl",
        "tac",
        "rev",
        "md5",
        "shasum",
        "sha256sum",
        "lsof",
        "uptime",
        "vm_stat",
        "sysctl",
        "ioreg",
        "system_profiler",
        "mdfind",
        "mdls",
        "xxd",
        "hexdump",
        "strings",
        "od",
        "base64",
        "pbpaste",
        "tree",
        "cd",
        "pushd",
        "popd",
        "dirs",
        "read",
        "export",
        "local",
        "declare",
        "typeset",
        "readonly",
        "set",
        "unset",
        "alias",
        "wait",
        "open",
        "say",
        "afplay",
    }
)
# What a pipe carries, as far as the line shows it: the paths a listing names, the text a printer prints, and the line
# filters that pass either on.
_LISTERS: frozenset[str] = frozenset({"find", "fd", "ls", "git"})
_PRINTERS: frozenset[str] = frozenset({"echo", "printf"})
_SEARCHERS: frozenset[str] = frozenset({"grep", "egrep", "fgrep", "rg"})
_FILTERS: frozenset[str] = frozenset({"sort", "uniq", "head", "tail", "grep", "egrep", "fgrep", "rg"})
# The commands that print a count or a digest of what they read, so that a listing piped into one is no list of names.
_COUNTERS: frozenset[str] = frozenset({"wc", "md5", "md5sum", "shasum", "sha1sum", "sha256sum", "cksum"})
# The GNU tools Homebrew installs under a name of their own, read as the tools they are (item 531).
_GNU_NAMES: dict[str, str] = {
    "ggrep": "grep",
    "gegrep": "egrep",
    "gfgrep": "fgrep",
    "gfind": "find",
    "gcp": "cp",
    "gdiff": "diff",
    "gxargs": "xargs",
}
_LIST_SWITCH: re.Pattern[str] = re.compile(r"-[A-Za-z]*l[A-Za-z]*|--files|--files-with-matches|--files-without-match")

_XARGS_VALUES: frozenset[str] = frozenset(
    {
        "-E",
        "-I",
        "-J",
        "-L",
        "-n",
        "-P",
        "-R",
        "-S",
        "-s",
        "-a",
        "-d",
        "--arg-file",
        "--delimiter",
        "--eof",
        "--max-lines",
        "--max-args",
        "--max-procs",
        "--max-chars",
        "--process-slot-var",
    }
)
_FIND_EXEC: frozenset[str] = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITES: frozenset[str] = frozenset({"-fprint", "-fprint0", "-fls", "-fprintf"})
_FD_EXEC: frozenset[str] = frozenset({"-x", "--exec", "-X", "--exec-batch"})
_FD_PLACEHOLDER: re.Pattern[str] = re.compile(r"\{/?/?\.?\}")
_AWKS: frozenset[str] = frozenset({"awk", "gawk", "nawk", "mawk"})
_AWK_VALUES: frozenset[str] = frozenset(
    {"-F", "-v", "-f", "-e", "-i", "-l", "--file", "--source", "--assign", "--field-separator", "--include", "--load"}
)
_AWK_MARK: str = "(\\d+)"
_AWK_SYSTEM: re.Pattern[str] = re.compile(r"\bsystem\s*\((?:\s*" + _AWK_MARK + r"\s*\))?")
_AWK_PIPE_OUT: re.Pattern[str] = re.compile(
    r"(?<!\|)\|&?(?!\|)(?!\s*getline)(?:\s*" + _AWK_MARK + r"(?=\s*(?:[;})\n]|$)))?"
)
_AWK_PIPE_IN: re.Pattern[str] = re.compile(r"(?:(?:^|(?<=[;{}(\n]))\s*" + _AWK_MARK + r")?\s*\|&?\s*getline")
_AWK_WRITE: re.Pattern[str] = re.compile(r">>?\s*" + _AWK_MARK)
_AWK_REGEX_AFTER: str = "(,{};!~&|\n?:"
_SED_FILES: re.Pattern[str] = re.compile(r"(?:^|[;{}!$/0-9])[gpiIemM0-9]*\s*([wWrR])\s+([^\n]+)", re.MULTILINE)
_UNIQ_VALUES: frozenset[str] = frozenset({"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"})
_XXD_VALUES: frozenset[str] = frozenset(
    {"-c", "-g", "-l", "-o", "-s", "-n", "-cols", "-groupsize", "-len", "-seek", "-offset", "-name"}
)
_OPEN_RUNS: tuple[str, ...] = (
    ".command",
    ".tool",
    ".sh",
    ".zsh",
    ".bash",
    ".csh",
    ".terminal",
    ".workflow",
    ".scpt",
    ".applescript",
    ".jar",
)
_OPEN_INSTALLS: tuple[str, ...] = (".pkg", ".mpkg")
# The terminal apps ``open -a`` or ``open -b`` can hand a file to, which run it as a script (item 385); a name is
# matched inside the app's name or bundle id (``com.apple.Terminal``).
_TERMINAL_APPS: tuple[str, ...] = (
    "terminal",
    "iterm",
    "warp",
    "alacritty",
    "kitty",
    "wezterm",
    "ghostty",
    "hyper",
    "xterm",
    "tabby",
    "termius",
)

_PIPE_TO_SHELL: re.Pattern[str] = re.compile(
    r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(bash|sh|zsh|python3?|ruby|perl|node)\b|"
    r"\b(bash|sh|zsh)\s+<\(\s*(curl|wget)\b|\beval\s+\"?\$\(\s*(curl|wget)\b"
)
_FORK_BOMB: re.Pattern[str] = re.compile(r":\(\)\s*\{")
_DEVICE_WRITE: re.Pattern[str] = re.compile(r"(>\s*|of=)/dev/(r?disk|sd|nvme)")
# The long options of curl and wget that send a body, a form or a file, as the word or with "=value" attached
# (--data-raw, --form-string and --post-file among them).
_SEND_OPTIONS: tuple[str, ...] = (
    "--data",
    "--form",
    "--json",
    "--upload-file",
    "--post-data",
    "--post-file",
    "--body-data",
    "--body-file",
)
_SEND_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH"})
_METHODS: frozenset[str] = frozenset({*_SEND_METHODS, "GET", "HEAD", "OPTIONS"})
# curl's short switches that send (a body, a form, a file) and those that take a value: in a word of switches such as
# -sSd@notes.txt, what follows one that takes a value is that value.
_CURL_SENDS: str = "dFT"
_CURL_VALUES: str = "AbcCdDeEFHKmoPQrtTuUwxXyYz"
# A data item of httpie and xh after the URL: name=value, name:=json, field@file (name==value is a query parameter
# and Name:value a header).
_HTTPIE_DATA: re.Pattern[str] = re.compile(r"^[^=:@\s]+(?:=@|:=@|:=|=(?!=)|@)")
_AS_ADMIN: re.Pattern[str] = re.compile(r"administrator privileges", re.IGNORECASE)
_AS_SEND: re.Pattern[str] = re.compile(r"\bsend\b|\bmake new outgoing message\b", re.IGNORECASE)
_AS_POWER: re.Pattern[str] = re.compile(r"\b(shut down|restart|log out|sleep)\b", re.IGNORECASE)
_AS_DELETE: re.Pattern[str] = re.compile(r"\bdelete\b|\bmove to trash\b|\bempty trash\b", re.IGNORECASE)
# The calls of a script that run a shell line or type one into a terminal, in AppleScript and in JXA (item 116 added
# Terminal's ``doScript(``, iTerm's ``writeText(`` and ``write({text: ...})``).
_AS_SHELL: re.Pattern[str] = re.compile(
    r"\bdo\s+shell\s+script\b|\bdoShellScript\s*\(|\bdo\s+script\b|\bdoScript\s*\(|\bwrite\s+text\b|"
    r"\bwriteText\s*\(|\.write\s*\(\s*\{\s*text\s*:",
    re.IGNORECASE,
)
_AS_KEYSTROKES: re.Pattern[str] = re.compile(r"\bkeystroke\b|\bkey\s+code\b|\bkeyCode\s*\(", re.IGNORECASE)
_AS_NATIVE: re.Pattern[str] = re.compile(r"\bObjC\.import\b|\$\.NS[A-Z]|\bNSTask\b|\bNSAppleScript\b")
_AS_LITERAL: re.Pattern[str] = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
_AS_GLUE: re.Pattern[str] = re.compile(r"with\s+administrator\s+privileges|[\s&()+,;¬{}:]|\btext\b", re.IGNORECASE)
_AS_ESCAPE: re.Pattern[str] = re.compile(r"\\(.)")
_AS_ESCAPES: dict[str, str] = {"n": "\n", "r": "\n", "t": "\t"}
_AS_CONTINUATIONS: tuple[str, ...] = ("¬", "&", "+", "(", ",")


class Finding(FrozenModel):
    """What one part of a line does. ``fetches``: it may bring in what other people wrote (it reads from the network,
    talks to a remote machine, or is code the guard cannot read), so its output is untrusted. ``changes``: the files it
    changes or removes, as far as the line shows them (item 305, :func:`_changing`)."""

    risk: Risk
    flags: tuple[Flag, ...]
    reason: str
    fetches: bool
    changes: tuple[str, ...]


class ShellAssessment(FrozenModel):
    """How a line is judged, and the files it changes or removes as far as it shows them (``changes``, absolute paths
    from each directory the line may be in), which the runs at work at the same time keep to (item 305)."""

    risk: Risk
    flags: tuple[Flag, ...]
    reasons: tuple[str, ...]
    fetches: bool
    changes: tuple[str, ...]


@dataclass(frozen=True)
class ShellScope:
    """What a command is judged in: where the line may be, and what it set up before the command (item 116).

    ``cwds`` are the directories the line may be in: the one it starts in and each one a ``cd`` may have moved it to (a
    ``cd`` can fail or run in a subshell, so the earlier ones stay). ``links`` are the symbolic links the line made,
    ``(link, target)``, so that a path through one is judged where it lands; ``variables`` hold every value the line
    gave each variable; ``depth`` counts the command strings the command is nested in (``bash -c``, ``$(...)``);
    ``downloaded`` are the files a fetcher saved and the folders ``git clone`` made earlier in the line (item 385);
    ``decoded`` are the files a decoder wrote earlier in the line, opaque code the guard never read, like a download
    (item 528)."""

    cwds: tuple[Path, ...]
    workspace: Path
    protected: tuple[Path, ...]
    dot_paths: tuple[Path, ...]
    links: tuple[tuple[Path, Path], ...]
    variables: Mapping[str, tuple[str, ...]]
    depth: int
    downloaded: tuple[Path, ...]
    decoded: tuple[Path, ...]


@dataclass(frozen=True)
class _Reading:
    """A line as the tokenizer is to read it (``text``), the commands substituted into it, the bodies of its
    here-documents (``text`` names each by a ``_DOCUMENT`` word after its ``<<``), the code it runs that cannot be
    read (zsh glob qualifiers that run code), and whether a substitution in it never closes, so that where it ends
    cannot be told."""

    text: str
    substituted: tuple[str, ...]
    documents: tuple[str, ...]
    hidden: tuple[str, ...]
    unclosed: bool


@dataclass(frozen=True)
class _Input:
    """What a command reads on its standard input, as far as the line shows it: the text (a here-document, a
    here-string, what an ``echo`` pipes in) and the paths it names (``find ... |``); an unknown path is
    ``_SUBSTITUTED``. ``walked`` are the folders a listing searched, as the line gives them (item 531): the files a
    command reads there are every file under them."""

    text: str | None
    paths: tuple[str, ...]
    walked: tuple[str, ...] = ()


_NO_INPUT: _Input = _Input(text=None, paths=(_SUBSTITUTED,))


@dataclass(frozen=True)
class _Switches:
    """How an interpreter reads a word of switches such as ``-pi``, ``-lne`` or ``-Bc``, letter by letter: the letters
    whose value is the code it runs, the letters whose value is something else (each takes the rest of the word when
    ``joined``, else or when the rest is empty the next word), the letters whose value can only be attached (the rest
    of the word), the letters followed by digits alone, the attached letter that edits the file operands in place, and
    the letters after whose value the rest of the line is the program's own arguments."""

    code: str
    values: str
    joined: bool
    attached: str
    digits: str
    in_place: str
    ends: str


# The interpreters whose words of switches are read letter by letter; the others by their inline code switches alone.
# node takes no value in the word of its switch: -pe is --print with the code in the next word.
_SWITCHES: Mapping[str, _Switches] = {
    "perl": _Switches(code="eE", values="I", joined=True, attached="CDFMdimx", digits="0l", in_place="i", ends=""),
    "ruby": _Switches(code="e", values="rICE", joined=True, attached="FKWix", digits="0T", in_place="i", ends=""),
    "python": _Switches(code="c", values="WXm", joined=True, attached="", digits="", in_place="", ends="cm"),
    "python3": _Switches(code="c", values="WXm", joined=True, attached="", digits="", in_place="", ends="cm"),
    "node": _Switches(code="ep", values="rC", joined=False, attached="", digits="", in_place="", ends=""),
    "php": _Switches(code="rBRE", values="cdfzt", joined=True, attached="", digits="", in_place="", ends=""),
}


@dataclass(frozen=True)
class _InterpreterLine:
    """An interpreter's words as it reads them: its inline code, its operands (the script first when there is no inline
    code) and whether it edits the files among them in place."""

    code: tuple[str, ...]
    operands: tuple[str, ...]
    in_place: bool


def _finding(risk: Risk, flags: tuple[Flag, ...], reason: str) -> Finding:
    return Finding(risk=risk, flags=flags, reason=reason, fetches=False, changes=())


def _fetching(risk: Risk, flags: tuple[Flag, ...], reason: str) -> Finding:
    return Finding(risk=risk, flags=flags, reason=reason, fetches=True, changes=())


def _changing(paths: list[str]) -> list[Finding]:
    """A part that changes or removes the files ``paths``: it only carries them (item 305) and takes no part in the
    judgement (:func:`_merge` leaves it out)."""
    return [Finding(risk="safe", flags=(), reason=_CHANGES, fetches=False, changes=tuple(paths))] if paths else []


def _unknown(reason: str) -> Finding:
    """Code the guard cannot read, judged as the worst it could be: a destructive step outside the workspace, which
    every preset asks about, that may fetch anything."""
    return _fetching("high", ("destructive", "outside_workspace"), reason)


def _given_text(text: str) -> _Input:
    return _Input(text=text, paths=tuple(text.split()) or (_SUBSTITUTED,))


# ---------- reading the line ----------


def _reserved_word(text: str, index: int, word: str, ends: tuple[str, ...]) -> bool:
    """Whether the word ``word`` starts at ``index`` of ``text``, after a blank or an operator and followed by one of
    ``ends``; :func:`_command_starts` tells whether it is a reserved word there."""
    before: str = text[index - 1 : index]
    after: str = text[index + len(word) : index + len(word) + 1]
    return text.startswith(word, index) and before in _COMMAND_START and after in ends


def _command_starts(text: str, start: int, index: int, patterns: Set[int]) -> bool:
    """Whether a command begins at ``index`` of ``text``, a substitution's body from ``start``, so that a reserved word
    there is one (in ``$(echo case x)`` it is a word ``echo`` prints): at the body's start, after an operator, a
    newline, a ``(`` or the ``)`` that ends a case pattern (``patterns``), or after a reserved word a command follows,
    blanks between."""
    before: str = text[start:index].rstrip(" \t")
    if not before or before[-1] in _OPENS_COMMAND or start + len(before) - 1 in patterns:
        return True
    return before.split()[-1] in _BEFORE_COMMAND


def _closing_paren(text: str, start: int) -> int:
    """The index of the ``)`` that closes a ``(`` opened just before ``start``, past quotes, nested parentheses and the
    patterns of a ``case`` (in ``$(case x in x) rm ;; esac)`` the first ``)`` ends a pattern, and the substitution
    goes on to its ``esac``); the end of ``text`` when it never closes."""
    depth: int = 1
    # The depth each ``case`` not yet ended by its ``esac`` stands at: a ``)`` there ends one of its patterns.
    cases: list[int] = []
    patterns: set[int] = set()
    quote: str | None = None
    index: int = start
    while index < len(text):
        char: str = text[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            index += 1
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and cases and cases[-1] == depth:
            patterns.add(index)
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        elif _reserved_word(text, index, "case", _CASE_ENDS) and _command_starts(text, start, index, patterns):
            cases.append(depth)
        elif (
            _reserved_word(text, index, "esac", _ESAC_ENDS) and cases and _command_starts(text, start, index, patterns)
        ):
            cases.pop()
        index += 1
    return len(text)


def _closing_backtick(text: str, start: int) -> int:
    """The index of the backtick that closes one opened just before ``start``; the end of ``text`` when none does."""
    index: int = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "`":
            return index
        index += 1
    return len(text)


def _ansi_escape(match: re.Match[str]) -> str:
    """One backslash escape of a ``$'...'`` string, resolved as the shell resolves it."""
    code: str = match.group(1)
    if code[0] in "xuU" and len(code) > 1:
        return chr(min(int(code[1:], 16), 0x10FFFF))
    if code[0] in "01234567":
        return chr(int(code, 8))
    if code[0] == "c" and len(code) > 1:
        return chr(ord(code[1]) & 0x1F)
    return _ANSI_ESCAPES.get(code, f"\\{code}")


def _substitutions(text: str) -> list[str]:
    """The commands substituted into free text (a here-document's body, an arithmetic expression): every ``$(...)``
    and backtick, quotes or not, since the body's quotes are only characters."""
    found: list[str] = []
    index: int = 0
    while index < len(text):
        if text.startswith("$(", index):
            end: int = _closing_paren(text, index + 2)
            body: str = text[index + 2 : end]
            found.extend(_substitutions(body) if body.startswith("(") and body.endswith(")") else [body])
            index = end + 1
        elif text[index] == "`":
            end = _closing_backtick(text, index + 1)
            found.append(text[index + 1 : end])
            index = end + 1
        else:
            index += 1
    return found


def _heredoc(command: str, index: int) -> tuple[int, str, bool, bool] | None:
    """A here-document operator at ``index`` (``<<`` or ``<<-``, not the ``<<<`` of a here-string): where its
    delimiter word ends, the delimiter as the shell compares it (its quotes removed), whether tabs are stripped from
    the body, and whether the body expands (no part of the delimiter was quoted). None when there is no such
    operator."""
    if not command.startswith("<<", index) or command.startswith("<<<", index):
        return None
    at: int = index + 2
    strip: bool = command.startswith("-", at)
    at += 1 if strip else 0
    while at < len(command) and command[at] in " \t":
        at += 1
    word: list[str] = []
    quoted: bool = False
    while at < len(command) and command[at] not in _WORD_BREAKS:
        char: str = command[at]
        if char in ("'", '"'):
            end: int = command.find(char, at + 1)
            end = len(command) if end == -1 else end
            word.append(command[at + 1 : end])
            quoted = True
            at = end + 1
        elif char == "\\":
            word.append(command[at + 1 : at + 2])
            quoted = True
            at += 2
        else:
            word.append(char)
            at += 1
    if not word:
        return None
    return at, "".join(word), strip, not quoted


def _document(command: str, start: int, delimiter: str, strip: bool) -> tuple[list[str], int]:
    """The lines of a here-document's body from ``start`` up to its delimiter line, and where the text goes on after
    it: the end of ``command`` when the delimiter never comes, as in the shell."""
    lines: list[str] = []
    index: int = start
    while index < len(command):
        newline: int = command.find("\n", index)
        end: int = len(command) if newline == -1 else newline
        line: str = command[index:end]
        index = len(command) if newline == -1 else newline + 1
        if (line.lstrip("\t") if strip else line) == delimiter:
            return lines, index
        lines.append(line)
    return lines, index


def _last_word(out: list[str]) -> str:
    """The word :func:`_read` is writing: its text since the last blank or operator."""
    text: str = "".join(out)
    return text[max(text.rfind(char) for char in _WORD_BREAKS) + 1 :]


def _read(command: str) -> _Reading:
    """``command`` read the way the shell reads it before it splits it into words (item 116).

    A ``#`` that starts a word comments out the rest of its line; a ``$'...'`` string is decoded and quoted again
    plainly; a backslash before a newline joins the lines; an unquoted newline separates commands; a quoted or escaped
    operator character is a letter of its word (``_LITERAL_OPERATORS``), as is one inside ``(( ))`` arithmetic. A
    here-document's body leaves the text for ``documents``; an unquoted delimiter's body still runs the substitutions it
    holds. Every ``$(...)``, backtick and process substitution outside single quotes is returned to be judged as a
    command of its own and stands in the text as a word only the running line knows (``_SUBSTITUTED``, or a temp path
    for a plain ``mktemp``); a ``$((...))`` stands as a number."""
    out: list[str] = []
    substituted: list[str] = []
    documents: list[str] = []
    hidden: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    quote: str | None = None
    arithmetic_until: int = -1
    unclosed: bool = False
    index: int = 0
    while index < len(command):
        char: str = command[index]
        attached: bool = bool(out) and out[-1][-1:] not in _WORD_BREAKS
        if quote == "'":
            out.append(char if char == "'" else char.translate(_HIDE_OPERATORS))
            quote = None if char == "'" else quote
            index += 1
        elif command.startswith("\\\n", index):
            index += 2
        elif char == "\\":
            escaped: str = command[index + 1 : index + 2]
            if quote == '"':
                out.append("\\" + escaped.translate(_HIDE_OPERATORS))
            elif escaped != "" and escaped in _OPERATOR_CHARS:
                out.append(escaped.translate(_HIDE_OPERATORS))
            else:
                out.append(command[index : index + 2])
            index += 2
        elif (
            command.startswith("$(", index)
            or (quote is None and command.startswith(("<(", ">("), index))
            or (quote is None and not attached and command.startswith("=(", index))
        ):
            end: int = _closing_paren(command, index + 2)
            body: str = command[index + 2 : end]
            if char == "$" and body.startswith("(") and body.endswith(")"):
                substituted.extend(_substitutions(body))
                out.append("0")
            else:
                substituted.append(body)
                out.append(_TEMP_STAND_IN if char == "$" and _MKTEMP.fullmatch(body) else _SUBSTITUTED)
            unclosed = unclosed or end == len(command)
            index = end + 1
        elif char == "`":
            end = _closing_backtick(command, index + 1)
            body = command[index + 1 : end]
            substituted.append(body)
            out.append(_TEMP_STAND_IN if _MKTEMP.fullmatch(body) else _SUBSTITUTED)
            unclosed = unclosed or end == len(command)
            index = end + 1
        elif quote == '"':
            out.append(char if char == '"' else char.translate(_HIDE_OPERATORS))
            quote = None if char == '"' else quote
            index += 1
        elif command.startswith("$'", index):
            end = index + 2
            while end < len(command) and command[end] != "'":
                end += 2 if command[end] == "\\" else 1
            out.append(shlex.quote(_ANSI_C.sub(_ansi_escape, command[index + 2 : end])).translate(_HIDE_OPERATORS))
            index = end + 1
        elif char in ("'", '"'):
            out.append(char)
            quote = char
            index += 1
        elif index < arithmetic_until:
            out.append(char.translate(_HIDE_OPERATORS))
            index += 1
        elif char == "#" and not attached:
            newline: int = command.find("\n", index)
            index = len(command) if newline == -1 else newline
        elif command.startswith("((", index) and not attached:
            arithmetic_until = _closing_paren(command, index + 1)
            out.append("((")
            index += 2
        elif char == "(" and attached and _ARRAY_ASSIGNMENT.fullmatch(_last_word(out)):
            # An array's values (``files=(*.txt)``) name no command, and an operator among them runs nothing (bash
            # refuses the line, zsh passes over a ";"); what they substitute and zsh's glob qualifiers still run.
            end = _closing_paren(command, index + 1)
            values: _Reading = _read(command[index + 1 : end])
            substituted.extend(values.substituted)
            hidden.extend(values.hidden)
            out.append(_SUBSTITUTED)
            unclosed = unclosed or values.unclosed or end == len(command)
            index = end + 1
        elif char == "(" and attached and out[-1][-1:] not in "$=" and _CODE_QUALIFIER.match(command, index):
            end = _closing_paren(command, index + 1)
            hidden.append(command[index + 1 : end])
            index = end + 1
        elif command.startswith("<<<", index):
            out.append("<<<")
            index += 3
        elif (operator := _heredoc(command, index)) is not None:
            end, delimiter, strip, expands = operator
            pending.append((delimiter, strip, expands))
            out.append(f" << __dot_document_{len(documents) + len(pending) - 1} ")
            index = end
        elif char == "\n":
            out.append(" ; ")
            index += 1
            for delimiter, strip, expands in pending:
                lines, index = _document(command, index, delimiter, strip)
                documents.append("\n".join(lines))
                substituted.extend(_substitutions(documents[-1]) if expands else [])
            pending = []
        else:
            out.append(char)
            index += 1
    documents.extend("" for _ in pending)
    return _Reading(
        text="".join(out),
        substituted=tuple(substituted),
        documents=tuple(documents),
        hidden=tuple(hidden),
        unclosed=unclosed,
    )


def _tokenize(text: str) -> list[str] | None:
    """The words and operators of ``text`` (as :func:`_read` gives it), None when its quotes do not close. A run of
    operator characters is split into the shell's operators (``);`` is ``)`` and ``;``); zsh's ``>!`` is ``>|``."""
    lexer: shlex.shlex = shlex.shlex(_CLOBBER_BANG.sub("|", text), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens: list[str] = list(lexer)
    except ValueError:
        return None
    return [
        part
        for token in tokens
        for part in (
            _SHELL_OPERATOR.findall(token) if token and all(char in _OPERATOR_CHARS for char in token) else [token]
        )
    ]


def _without_patterns(tokens: list[str]) -> list[str]:
    """``tokens`` without the patterns of the arms of a ``case``, which name no command (``*)`` is no command named by
    a pattern): the words after its ``in`` and after each ``;;`` (``;&``, ``;;&``, ``;|``) up to the ``)`` that ends
    them, with the ``|`` between them. A ``case`` or ``esac`` is a reserved word only where a command begins."""
    kept: list[str] = []
    cases: int = 0
    header: bool = False
    pattern: bool = False
    starts: bool = True
    for token in tokens:
        if pattern and token == "esac":
            cases, pattern = cases - 1, False
        elif pattern:
            pattern = token != ")"
            starts = not pattern
            kept.extend([token] if not pattern else [])
            continue
        elif header and token == "in":
            header, pattern = False, True
        elif starts and token == "case":
            cases, header = cases + 1, True
        elif starts and token == "esac" and cases:
            cases -= 1
        elif cases and token in _ARM_ENDS:
            pattern = True
        kept.append(token)
        starts = token in _OPERATORS or (starts and token in _BEFORE_COMMAND)
    return kept


def _split_words(text: str) -> list[str] | None:
    try:
        return shlex.split(text)
    except ValueError:
        return None


def _commands(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """The simple commands of ``tokens``, each with the operator before it ("" for the first), their quoted operator
    characters given back. A bare ``{`` or ``}`` ends a command too, so that zsh's ``if test { cmd }`` shows ``cmd``."""
    commands: list[tuple[str, list[str]]] = []
    current: list[str] = []
    operator: str = ""
    for token in tokens:
        if token in _OPERATORS:
            if current:
                commands.append((operator, current))
            current = []
            operator = token
        else:
            current.append(token.translate(_SHOW_OPERATORS))
    if current:
        commands.append((operator, current))
    return commands


def _is_redirect(token: str) -> bool:
    return token != "" and all(char in "<>&|" for char in token) and token not in _OPERATORS


def _split_redirects(
    words: list[str], documents: tuple[str, ...]
) -> tuple[list[str], list[str], list[str], str | None]:
    """Separate ``(words, output redirect targets, those of them a redirect empties first, the text given on standard
    input)``: the body of a here-document (named by the ``_DOCUMENT`` word :func:`_read` put after its ``<<``) or the
    word of a here-string. Descriptor targets such as ``&1`` are ignored."""
    plain: list[str] = []
    targets: list[str] = []
    emptied: list[str] = []
    text: str | None = None
    index: int = 0
    while index < len(words):
        token: str = words[index]
        if _is_redirect(token):
            operand: str = words[index + 1] if index + 1 < len(words) else ""
            document: re.Match[str] | None = _DOCUMENT.fullmatch(operand)
            if token == "<<" and document is not None and int(document.group(1)) < len(documents):
                text = documents[int(document.group(1))]
            elif token == "<<<":
                text = operand
            elif ">" in token and not operand.isdigit() and operand not in ("-", ""):
                targets.append(operand)
                if token in _REPLACING_REDIRECTS:
                    emptied.append(operand)
            index += 2
            continue
        if token.isdigit() and index + 1 < len(words) and _is_redirect(words[index + 1]):
            index += 1
            continue
        plain.append(token)
        index += 1
    return plain, targets, emptied, text


def _plain(words: list[str]) -> list[str]:
    """``words`` without the reserved words in front of the command: ``if``, ``then``, ``do``, ``!``, ``time``, a
    ``function`` keyword and its name, zsh's ``repeat`` and its count."""
    index: int = 0
    while index < len(words):
        if words[index] in _KEYWORDS:
            index += 1
        elif words[index] in ("function", "repeat"):
            index += 2
        else:
            break
    return words[index:]


def _options(args: list[str], valued: frozenset[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """The leading options of ``args``, each with its value ("" for a switch), and the words after them. ``valued``
    names the options that take a value: the next word, or the rest of the word (``-n10``, ``--signal=KILL``). A
    cluster of short switches (``-rf``) is split; ``--`` ends the options."""
    options: list[tuple[str, str]] = []
    index: int = 0
    while index < len(args):
        word: str = args[index]
        if word == "--":
            return options, args[index + 1 :]
        if not word.startswith("-") or word == "-":
            break
        name, equals, value = word.partition("=")
        if word.startswith("--") or word in valued:
            taken: bool = name in valued and not equals
            options.append((name, args[index + 1] if taken and index + 1 < len(args) else value))
            index += 2 if taken else 1
            continue
        for at in range(1, len(word)):
            short: str = f"-{word[at]}"
            if short in valued:
                rest: str = word[at + 1 :]
                options.append((short, rest or (args[index + 1] if index + 1 < len(args) else "")))
                index += 0 if rest else 1
                break
            options.append((short, ""))
        index += 1
    return options, args[index:]


def _option_values(args: list[str], short: str, long: str) -> list[str]:
    """The values given to one option anywhere among ``args``: ``-o out``, ``-oout``, ``-ro out``, ``--output out``
    or ``--output=out`` (``short`` is ``-o``, ``long`` is ``--output``; either may be "")."""
    values: list[str] = []
    for index, word in enumerate(args):
        following: str = args[index + 1] if index + 1 < len(args) else ""
        if long and word == long:
            values.append(following)
        elif long and word.startswith(f"{long}="):
            values.append(word[len(long) + 1 :])
        elif short and word.startswith("-") and not word.startswith("--") and short[1] in word[1:]:
            values.append(word[word.index(short[1], 1) + 1 :] or following)
    return values


def _positional(args: list[str]) -> list[str]:
    """Arguments that are not options; everything after ``--`` counts as positional."""
    positional: list[str] = []
    after_separator: bool = False
    for arg in args:
        if after_separator or not arg.startswith("-") or arg == "-":
            positional.append(arg)
        elif arg == "--":
            after_separator = True
    return positional


# ---------- where a word points ----------


def _held(name: str, scope: ShellScope) -> tuple[str, ...] | None:
    """The values a variable may hold: those the line gave it, the directories the line may be in for ``PWD``, a
    number for the shell's counters, else the daemon's environment; None when only the running line knows."""
    if name in scope.variables:
        return scope.variables[name]
    if name == "PWD":
        return tuple(str(cwd) for cwd in scope.cwds)
    if name in _NUMERIC_VARIABLES or name in "$!?#":
        return ("0",)
    if name in _RUNTIME_VARIABLES:
        return None
    value: str | None = os.environ.get(name)
    return None if value is None else (value,)


def _unbraced(values: list[str]) -> list[str] | None:
    """``values`` with their brace expansions spelled out (``~/.{ssh,aws}`` is ``~/.ssh`` and ``~/.aws``); None when
    there are too many to judge."""
    done: list[str] = []
    pending: list[str] = list(values)
    while pending:
        value: str = pending.pop()
        match: re.Match[str] | None = _BRACES.search(value)
        if match is None:
            done.append(value)
        else:
            pending.extend(
                f"{value[: match.start()]}{choice}{value[match.end() :]}" for choice in match.group(1).split(",")
            )
        if len(done) + len(pending) > _MOST_VALUES:
            return None
    return done


def _expansions(raw: str, scope: ShellScope) -> list[str] | None:
    """The values the word ``raw`` can take when it runs: its variables put in (every value each may hold, see
    :func:`_held`), its braces spelled out and a leading ``~`` expanded; None when part of it is only known then (a
    substitution, a variable nothing set, ``$1``, ``${x:-y}``)."""
    pieces: list[tuple[str, ...]] = []
    last: int = 0
    for match in _VARIABLE.finditer(raw):
        held: tuple[str, ...] | None = _held(match.group(1) or match.group(2) or match.group(3), scope)
        if held is None:
            return None
        pieces.extend([(raw[last : match.start()],), held])
        last = match.end()
    pieces.append((raw[last:],))
    values: list[str] = [""]
    for choices in pieces:
        values = [value + choice for value in values for choice in choices]
        if len(values) > _MOST_VALUES:
            return None
    spelled: list[str] | None = _unbraced(values)
    if spelled is None:
        return None
    expanded: list[str] = [os.path.expanduser(value) for value in spelled]
    return None if any("$" in value for value in expanded) else expanded


def _through_links(path: Path, links: tuple[tuple[Path, Path], ...]) -> Path:
    """``path`` with a link the line made in front of it replaced by where the link points, link after link."""
    current: Path = path
    for _ in range(8):
        found: tuple[Path, Path] | None = next(
            ((link, target) for link, target in links if within(current, link)), None
        )
        if found is None:
            return current
        link, target = found
        current = kernel_path(Path(os.path.realpath(f"{target}{str(current)[len(str(link)) :]}")))
    return current


def _located(value: str, cwd: Path, scope: ShellScope) -> Path:
    return _through_links(expand_path(value, cwd), scope.links)


def _class_of(path: Path, scope: ShellScope) -> PathClass:
    """The class of a resolved path. A glob is protected when it can reach a protected location, Dot's own when it can
    reach one of Dot's own folders, and is otherwise judged by the directory it starts from."""
    if not any(char in part for part in path.parts for char in _GLOB_CHARS):
        return classify_path(path, scope.workspace, scope.protected, scope.dot_paths)
    if glob_reaches_protected(path, scope.protected):
        return "protected"
    if glob_reaches_protected(path, scope.dot_paths):
        return "dot"
    static: list[str] = []
    for part in path.parts:
        if any(char in part for char in _GLOB_CHARS):
            break
        static.append(part)
    return classify_path(Path(*static), scope.workspace, scope.protected, scope.dot_paths)


def _path_classes(raw: str, scope: ShellScope) -> list[PathClass] | None:
    values: list[str] | None = _expansions(raw, scope)
    if values is None:
        return None
    return [_class_of(_located(value, cwd, scope), scope) for value in values for cwd in scope.cwds]


def _path_class(raw: str, scope: ShellScope) -> PathClass:
    """The worst class ``raw`` can have from any directory the line may be in; a path only the running line knows
    counts as one in the home folder."""
    classes: list[PathClass] | None = _path_classes(raw, scope)
    return "home" if classes is None else _worst_class(classes)


def _reaches_protected(raw: str, scope: ShellScope) -> bool:
    """Whether ``raw`` can name a protected location. A part only the running line knows stands for the home folder,
    where the protected locations are (``${HOME%/}/.ssh``, ``$(echo ~)/.ssh``)."""
    classes: list[PathClass] | None = _path_classes(raw, scope)
    if classes is None:
        home: str = str(Path.home())
        classes = _path_classes(_UNKNOWN_EXPANSION.sub(lambda _: home, raw), scope)
    return classes is not None and "protected" in classes


def _known_paths(raws: list[str], scope: ShellScope) -> list[str]:
    """The paths ``raws`` name from each directory the line may be in, as far as the line shows them: a word only the
    running line knows names none, and neither does a terminal or ``/dev/null``."""
    return [
        str(_located(value, cwd, scope))
        for raw in raws
        if raw not in _QUIET_TARGETS
        for value in _expansions(raw, scope) or []
        for cwd in scope.cwds
    ]


def _written_paths(command: str, args: list[str], scope: ShellScope) -> list[str]:
    """The files a command of ``_WRITERS`` changes or removes: what a copy or a move puts in place (in a folder it
    lands in, the file of each source's name), what a move takes away, every file ``tee`` and ``truncate`` name. A
    folder made, a file touched and a mode changed change no file's content."""
    if command in _MODE_WRITERS:
        return []
    words, folders = _target_folders(args) if command in _TARGET_FOLDER_COMMANDS else (args, [])
    operands: list[str] = _positional(words)
    if command not in _COPIES and command != "mv":
        return _known_paths(operands, scope)
    sources, destinations = (operands, folders) if folders else (operands[:-1], operands[-1:])
    landing: list[str] = [
        str(Path(place) / Path(source).name) if Path(place).is_dir() else place
        for place in _known_paths(destinations, scope)
        for source in sources
    ]
    return [*(_known_paths(sources, scope) if command == "mv" else []), *landing]


def _places(raw: str, scope: ShellScope) -> list[Path]:
    """The directories ``raw`` names from each directory the line may be in; the home folder stands for one only the
    running line knows."""
    values: list[str] | None = _expansions(raw, scope)
    if values is None:
        return [Path.home()]
    return [_located(value, cwd, scope) for value in values for cwd in scope.cwds]


def _with_places(scope: ShellScope, places: list[Path]) -> ShellScope:
    """``scope`` in the directories ``places``; past ``_MOST_PLACES`` of them, the home folder stands for the rest."""
    cwds: tuple[Path, ...] = tuple(dict.fromkeys(places))
    if len(cwds) > _MOST_PLACES:
        cwds = (*cwds[: _MOST_PLACES - 1], Path.home())
    return replace(scope, cwds=cwds)


def _worst_class(classes: list[PathClass]) -> PathClass:
    order: tuple[PathClass, ...] = ("workspace", "temp", "home", "system", "dot", "protected")
    return max(classes, key=order.index) if classes else "workspace"


def _cwd_class(scope: ShellScope) -> PathClass:
    return _worst_class([classify_path(cwd, scope.workspace, scope.protected, scope.dot_paths) for cwd in scope.cwds])


# ---------- what the line sets up ----------


def _assigned(scope: ShellScope, name: str, raws: list[str]) -> ShellScope:
    """``scope`` with ``name`` also holding the values of ``raws`` (as :func:`_expansions` gives them, a stand-in for
    one only the running line knows); a variable keeps every value the line gave it."""
    values: list[str] = []
    for raw in raws:
        values.extend(_expansions(raw, scope) or [_SUBSTITUTED])
    held: tuple[str, ...] = tuple(dict.fromkeys([*scope.variables.get(name, ()), *values]))
    return replace(scope, variables={**scope.variables, name: held if len(held) <= _MOST_VALUES else (_SUBSTITUTED,)})


def _assign_words(scope: ShellScope, words: list[str]) -> ShellScope:
    current: ShellScope = scope
    for word in words:
        assignment: re.Match[str] | None = _ENV_ASSIGNMENT.fullmatch(word)
        if assignment is not None:
            current = _assigned(current, assignment.group(1), [assignment.group(2)])
    return current


def _linked(scope: ShellScope, args: list[str]) -> ShellScope:
    """``scope`` with the symbolic links an ``ln -s`` makes (``ln -s target``, ``ln -s target link``, ``ln -s target...
    dir``); a link into an existing directory is made inside it."""
    options, operands = _options(args, frozenset({"-t", "-S", "--target-directory", "--suffix"}))
    if not any(name in ("-s", "--symbolic") for name, _ in options) or not operands:
        return scope
    folder: str | None = next((value for name, value in options if name in ("-t", "--target-directory")), None)
    if folder is not None:
        pairs: list[tuple[str, str]] = [(target, f"{folder}/{Path(target).name}") for target in operands]
    elif len(operands) == 1:
        pairs = [(operands[0], Path(operands[0]).name)]
    elif len(operands) == 2:
        pairs = [(operands[0], operands[1])]
    else:
        pairs = [(target, f"{operands[-1]}/{Path(target).name}") for target in operands[:-1]]
    made: list[tuple[Path, Path]] = []
    for target, link in pairs:
        for place in _places(link, scope):
            where: Path = place / Path(target).name if place.is_dir() and not place.is_symlink() else place
            values: list[str] | None = _expansions(target, scope)
            made.extend((where, expand_path(value, where.parent)) for value in (values or [str(Path.home())]))
    return replace(scope, links=(*scope.links, *made))


def _past_modifiers(words: list[str]) -> list[str]:
    """``words`` past the precommand modifiers in front of a builtin (``builtin cd``, ``command -p cd``)."""
    rest: list[str] = words
    while rest and rest[0] in _BUILTIN_MODIFIERS:
        rest = rest[1:]
        while rest and rest[0].startswith("-") and rest[0] != "-":
            rest = rest[1:]
    return rest


def _line_scope(line: str, scope: ShellScope) -> ShellScope:
    """The scope ``line`` leaves when the shell runs it in place (``eval``): its simple commands in turn. A line that
    cannot be parsed may have moved anywhere, which the home folder stands for."""
    tokens: list[str] | None = _tokenize(_read(line).text)
    if tokens is None:
        return _with_places(scope, [*scope.cwds, Path.home()])
    state: ShellScope = scope
    for _, segment in _commands(tokens):
        words, _, _, _ = _split_redirects(segment, ())
        state = _next_scope(words, state)
    return state


def _run_by_wrappers(words: list[str], scope: ShellScope) -> tuple[list[str], ShellScope]:
    """The command one simple command's ``words`` come to through the assignments, precommand modifiers and wrappers
    in front of it (``X=1 timeout 5 curl -o y.sh U``, ``env -C /tmp nice sh``), with its arguments, and the scope it
    runs in (``env -C`` moves it); a wrapper given a whole line (``watch``, ``env -S``) stands as the command, as only
    the line's own reading judges it. What a line fetches and what it feeds a shell are read through them (item 385)."""
    rest: list[str] = words
    here: ShellScope = scope
    while rest:
        while rest and _ENV_ASSIGNMENT.fullmatch(rest[0]):
            rest = rest[1:]
        rest = _past_modifiers(rest)
        if not rest:
            break
        command: str = Path(rest[0]).name
        if command not in _WRAPPERS:
            break
        options, after = _options(rest[1:], _WRAPPER_VALUES.get(command, frozenset()))
        if command == "watch" or (command == "env" and any(name in ("-S", "--split-string") for name, _ in options)):
            break
        if command == "env":
            places: list[str] = [value for name, value in options if name in ("-C", "--chdir")]
            if places:
                here = _with_places(here, _places(places[-1], here))
            if after[:1] == ["-"]:
                after = after[1:]
        rest = after[_WRAPPER_SKIPS.get(command, 0) :]
    return rest, here


def _next_scope(words: list[str], scope: ShellScope) -> ShellScope:
    """The scope after one simple command: the directories a ``cd`` or ``pushd`` may have moved the line to, the
    values an assignment, ``export`` or ``for`` gave a variable, the links an ``ln -s`` made."""
    plain: list[str] = _plain(words)
    if not plain:
        return scope
    if plain[0] in ("for", "select", "foreach"):
        if len(plain) < 2 or not _NAME.fullmatch(plain[1]):
            return scope
        return _assigned(scope, plain[1], plain[3:] if plain[2:3] == ["in"] else [_SUBSTITUTED])
    if plain[0] in _DECLARERS:
        return _assign_words(scope, plain[1:])
    count: int = 0
    while count < len(plain) and _ENV_ASSIGNMENT.fullmatch(plain[count]):
        count += 1
    assigned: ShellScope = _assign_words(scope, plain[:count])
    rest: list[str] = _past_modifiers(plain[count:])
    command: str = Path(rest[0]).name if rest else ""
    if command == "eval":
        return _line_scope(" ".join(rest[1:]), assigned)
    if command in ("cd", "pushd"):
        operands: list[str] = _positional(rest[1:])
        wanted: bool = len(operands) == 1 and operands[0] != "-"
        return _with_places(assigned, [*assigned.cwds, *(_places(operands[0], assigned) if wanted else [Path.home()])])
    if command == "ln":
        return _linked(assigned, rest[1:])
    run, where = _run_by_wrappers(rest, assigned)
    ran: str = Path(run[0]).name if run else ""
    places: list[Path] = _fetched_places(ran, run[1:], where) or _located_all(
        _carried_download(ran, run[1:], where), where
    )
    if places:
        return replace(assigned, downloaded=(*assigned.downloaded, *places))
    return assigned


def _piped(stages: list[list[str]], documents: tuple[str, ...]) -> _Input:
    """What the last of ``stages`` (one pipeline's commands so far) writes into the pipe, as far as the line shows it:
    the text an ``echo`` or ``printf`` prints, the paths a listing names (``find``, ``ls``, ``grep -l``), and what a
    line filter (``sort``, ``grep``, ``head``) passes on of either. Anything else is unknown."""
    given: _Input = _NO_INPUT
    for stage in stages:
        words: list[str] = _plain(_split_redirects(stage, documents)[0])
        command: str = Path(words[0]).name if words else ""
        operands: list[str] = _positional(words[1:])
        if command in _PRINTERS:
            given = _given_text(" ".join(operands))
        elif command in _LISTERS or (command in _SEARCHERS and any(_LIST_SWITCH.fullmatch(w) for w in words[1:])):
            given = _Input(
                text=None, paths=tuple(operands) or (".",), walked=tuple(_listed_folders(command, words[1:]))
            )
        elif command not in _FILTERS:
            # A stage it does not know (``cat``, ``awk``, ``tr``) passes on names it cannot read, still those of the
            # listing it follows; a count or a digest passes none (item 531).
            given = _NO_INPUT if command in _COUNTERS else _Input(text=None, paths=(_SUBSTITUTED,), walked=given.walked)
    return given


# ---------- judging commands ----------


def _assess_delete(args: list[str], scope: ShellScope) -> Finding:
    targets: list[str] = _positional(args)
    worst: PathClass = _worst_class([_path_class(target, scope) for target in targets])
    if worst in ("workspace", "temp"):
        return _finding("low", (), "removes files inside the workspace or temp")
    if worst == "protected":
        return _finding("high", ("destructive", "secret_access"), "removes a protected location")
    if worst == "dot":
        return _finding("high", ("destructive", "secret_access"), CHANGES_DOT)
    return _finding("high", ("destructive", "outside_workspace"), "removes files outside the workspace")


def _read_as(path_class: PathClass) -> PathClass:
    """The class of a path a command only reads: Dot's own folders are read as the home folder is."""
    return "home" if path_class == "dot" else path_class


def _target_folders(args: list[str]) -> tuple[list[str], list[str]]:
    """The words of a copy or a move without the option that names the folder it writes into (``-t dir``, ``-tdir``,
    ``-rt dir``, ``--target-directory dir``, ``--target-directory=dir``), and the folders that option names."""
    words: list[str] = []
    folders: list[str] = []
    index: int = 0
    while index < len(args):
        word: str = args[index]
        following: list[str] = args[index + 1 : index + 2]
        index += 1
        if word == "--":
            words.extend(args[index - 1 :])
            break
        if word == _TARGET_FOLDER_OPTION:
            folders.extend(following)
            index += len(following)
        elif word.startswith(f"{_TARGET_FOLDER_OPTION}="):
            folders.append(word.removeprefix(f"{_TARGET_FOLDER_OPTION}="))
        elif word.startswith("-") and not word.startswith("--") and "t" in word[1:]:
            attached: str = word[word.index("t", 1) + 1 :]
            if attached:
                folders.append(attached)
            else:
                folders.extend(following)
                index += len(following)
        else:
            words.append(word)
    return words, folders


def _assess_writes(command: str, args: list[str], scope: ShellScope) -> Finding:
    words, folders = _target_folders(args) if command in _TARGET_FOLDER_COMMANDS else (args, [])
    # The folder an option names is where the command writes: it comes last, as the operand a copy writes. The mode or
    # the owner of chmod, chown and chgrp counts as a path too: none can be told apart for sure (a number is left out,
    # and a mode can start with "-", like an option), and as a word of the line's own folder it judges no path milder.
    targets: list[str] = [*(target for target in _positional(words) if not target.isdigit()), *folders]
    classes: list[PathClass] = [_path_class(target, scope) for target in targets]
    if command in _COPIES:
        classes = [*(_read_as(path_class) for path_class in classes[:-1]), *classes[-1:]]
    worst: PathClass = _worst_class(classes)
    if worst in ("workspace", "temp"):
        return _finding("low", (), f"{command} inside the workspace or temp")
    if worst == "protected":
        return _finding("high", ("secret_access",), f"{command} touches a protected location")
    if worst == "dot":
        return _finding("high", ("secret_access",), f"{command} {CHANGES_DOT}")
    settings: tuple[Flag, ...] = ("system_settings",) if worst == "system" else ()
    if command == "mv" and len(classes) > 1 and _worst_class(classes[:-1]) in ("home", "system"):
        # A move takes its sources away from where they were, as a removal does (item 385); the folder it moves into
        # comes last, whether an option or the last operand names it.
        return _finding(
            "high", ("destructive", *settings, "outside_workspace"), "mv takes files from outside the workspace"
        )
    if command == "truncate":
        return _finding(
            "high", ("destructive", *settings, "outside_workspace"), "truncate cuts files outside the workspace"
        )
    if worst == "system":
        return _finding("high", (*settings, "outside_workspace"), f"{command} modifies a system location")
    return _finding("medium", ("outside_workspace",), f"{command} modifies files outside the workspace")


def _replaces_a_file(raw: str, names: list[str], scope: ShellScope) -> bool:
    """Whether writing ``raw`` whole replaces a file that is there outside the workspace and the temporary folders:
    ``raw`` itself, or the file of each of ``names`` in it when it is a folder. What the file held is lost then, as when
    it is removed (the file tools keep a copy for ``dotx undo``, the shell does not); a file made anew loses nothing."""
    for place in _places(raw, scope):
        if _class_of(place, scope) not in ("home", "system"):
            continue
        landing: list[Path] = [place / name for name in names] if place.is_dir() else [place]
        if any(path.is_file() for path in landing):
            return True
    return False


def _replacing(targets: list[str], names: list[str], scope: ShellScope) -> list[Finding]:
    if any(_replaces_a_file(target, names, scope) for target in targets):
        return [_finding("high", ("destructive", "outside_workspace"), _REPLACED)]
    return []


def _has_switch(args: list[str], letter: str, long: str) -> bool:
    """Whether ``args`` turn on ``-<letter>`` (alone or in a word of switches) or ``long`` before ``--``."""
    for arg in args:
        if arg == "--":
            return False
        if arg == long or (arg.startswith("-") and not arg.startswith("--") and letter in arg[1:]):
            return True
    return False


def _replaced_by(command: str, args: list[str], scope: ShellScope) -> list[Finding]:
    """A writer that puts a file in the place of one that is there outside the workspace: a copy or a move onto it, or
    into a folder that holds one of its name (``cp`` and ``mv`` told to keep it with ``-n`` aside), ``ln -f`` onto
    it, ``tee`` without ``-a``."""
    if command == "tee":
        return [] if _has_switch(args, "a", "--append") else _replacing(_positional(args), [], scope)
    if command == "ln" and not _has_switch(args, "f", "--force"):
        return []
    if command not in _REPLACING_COPIES and command != "ln":
        return []
    if command in ("cp", "mv") and _has_switch(args, "n", "--no-clobber"):
        return []
    words, folders = _target_folders(args) if command in _TARGET_FOLDER_COMMANDS else (args, [])
    operands: list[str] = _positional(words)
    sources, destinations = (operands, folders) if folders else (operands[:-1], operands[-1:])
    return _replacing(destinations, [Path(source).name for source in sources], scope)


def _writes_to(command: str, targets: list[str], scope: ShellScope) -> list[Finding]:
    return [_assess_writes(command, ["--", *targets], scope)] if targets else []


def _assess_redirects(targets: list[str], scope: ShellScope) -> list[Finding]:
    findings: list[Finding] = []
    for target in targets:
        if target in _QUIET_TARGETS:
            continue
        path_class: PathClass = _path_class(target, scope)
        if path_class == "protected":
            findings.append(_finding("high", ("secret_access",), "redirects output into a protected location"))
        elif path_class == "dot":
            findings.append(_finding("high", ("secret_access",), f"redirects output: {CHANGES_DOT}"))
        elif path_class == "system":
            findings.append(_finding("high", ("system_settings", "outside_workspace"), "redirects output into system"))
        elif path_class == "home":
            findings.append(_finding("medium", ("outside_workspace",), "redirects output outside the workspace"))
    return findings


# The commands that move the line to another folder: a climb (``..``) beside a part only the running line knows is no
# step toward a protected location in ``cd "$(dirname "$f")/.."``, which reads nothing. Where the line lands is still
# read: the beginning and the end of the path a move names are judged as any other word's.
_MOVES: frozenset[str] = frozenset({"cd", "pushd", "popd"})


def _protected_words(words: list[str], scope: ShellScope, *, climbs: bool = True) -> list[Finding]:
    """A finding when a word names a protected location: as a path (a glob that can reach one, a path through a link
    the line made or a variable it set), or as the value of an assignment or an option (``K=~/.ssh``,
    ``--key=~/.ssh/id``), or as the file a command reads after an at sign (curl's ``--data-binary @~/.ssh/id``, item
    385; ``@-`` is the standard input). A word that may lead into one without saying so
    (:func:`_leads_toward_protected`) is a finding too; ``climbs`` is false for a move, which is no step by a ``..``."""
    parts: list[str] = [part for word in words for part in (word, word.partition("=")[2]) if part]
    candidates: list[str] = [*parts, *(part[1:] for part in parts if part.startswith("@") and part != "@-")]
    if any(_reaches_protected(candidate, scope) for candidate in candidates):
        return [_finding("high", ("secret_access",), "names a protected location")]
    if any(
        _leads_toward_protected(candidate, scope, climbs=climbs) or _has_too_many_spellings(candidate, scope)
        for candidate in candidates
    ):
        return [
            _maybe_protected(
                "names a path that may lie in a protected location: part of it is only known when the line runs"
            )
        ]
    return []


def _has_too_many_spellings(raw: str, scope: ShellScope) -> bool:
    """Whether ``raw``, a word with no part only the running line knows, has more spellings (variables, braces) than
    :data:`_MOST_VALUES`: the guard reads none of them, and a protected location may be one."""
    return _expansions(raw, scope) is None and not _unknown_parts(raw, scope)


def _maybe_protected(reason: str) -> Finding:
    """A step that may reach a protected location, which the line does not show: asked about in every preset (item
    531), as a step the guard cannot read is (:func:`_unknown`), but one that fetches nothing and destroys nothing."""
    return _finding("high", ("protected_read", "outside_workspace"), reason)


def _unknown_parts(text: str, scope: ShellScope) -> list[re.Match[str]]:
    """The expansions of ``text`` that only the running line knows: a substitution, a variable nothing set, ``${x:-y}``,
    ``$1``, a variable the line filled with what a command prints; one a variable of the line or the environment can
    fill is known."""
    parts: list[re.Match[str]] = []
    for match in _UNKNOWN_EXPANSION.finditer(text):
        variable: re.Match[str] | None = _VARIABLE.fullmatch(match.group(0))
        held: tuple[str, ...] | None = (
            None if variable is None else _held(variable.group(1) or variable.group(2) or variable.group(3), scope)
        )
        if held is None or any("$" in value for value in held):
            parts.append(match)
    return parts


def _leads_toward_protected(raw: str, scope: ShellScope, *, climbs: bool = True) -> bool:
    """Whether ``raw``, a word with a part only the running line knows, can name a protected location or a file inside
    one (item 531). What it says before that part is the start of a protected location's path, letter case aside
    (``~/.s`` of ``~/.s$(echo sh)/id_rsa``, ``~/Library/`` of ``~/Library/${X:-Safari}/History.db``), or lies inside
    one, whatever the part turns out to be; a part that begins the word stands for the home folder, where the protected
    locations are, as in :func:`_reaches_protected`. What it says after the last such part begins with the end of a
    protected location's path, since the part may lead out of the folder before it
    (``~/Documents/$(echo ..)/.ssh/id_rsa``, ``$(pwd)/../.ssh/id_rsa``). And, unless it is a move that is judged
    (``climbs`` false), a word that climbs (``..``) in the part it knows is not told from one that reaches a protected
    location, whatever the unknown part is. What the part itself
    says is not read: one that holds the protected folder's own name (``~/Documents/$(echo ../.ssh)/id_rsa``) is not
    told. A word that is known whole is judged by :func:`_reaches_protected`."""
    parts: list[re.Match[str]] = _unknown_parts(raw, scope)
    if not parts:
        return False
    known: list[str] = [
        raw[begin:stop]
        for begin, stop in zip(
            [0, *(part.end() for part in parts)], [*(part.start() for part in parts), len(raw)], strict=True
        )
    ]
    if (climbs and _climbs(known, scope)) or _ends_a_path_into_protected(known[-1], scope):
        return True
    text: str = raw
    for _ in range(_MOST_UNKNOWN):
        found: list[re.Match[str]] = _unknown_parts(text, scope)
        if not found:
            return False
        if found[0].start() == 0:
            text = str(Path.home()) + text[found[0].end() :]
            continue
        starts: list[str] | None = _expansions(text[: found[0].start()], scope)
        return starts is not None and any(
            _starts_a_path_into_protected(expand_path(start, cwd), scope) for start in starts for cwd in scope.cwds
        )
    return False


def _climbs(pieces: list[str], scope: ShellScope) -> bool:
    """Whether one of ``pieces``, the parts of a word that the line shows, spells a climb (``..``) once its variables
    are put in and its braces spelled out (``$D`` for ``D=..``, ``.{.,}``)."""
    return any(".." in value.split("/") for piece in pieces for value in _expansions(piece, scope) or [piece])


def _ends_a_path_into_protected(tail: str, scope: ShellScope) -> bool:
    """Whether ``tail``, the known end of a path whose beginning is not known, begins with the last names of a protected
    location's path (``.ssh`` of ``/.ssh/id_rsa``, ``Library/Safari`` of ``Library/Safari/History.db``), letter case
    aside: what comes before it may lead out of any folder to the protected one."""
    values: list[str] | None = _expansions(tail, scope)
    if values is None:
        return False
    for value in values:
        names: list[str] = []
        for name in value.split("/"):
            if name == "..":
                if names:
                    names.pop()
            elif name not in ("", "."):
                names.append(name.casefold())
        for location in scope.protected:
            held: list[str] = [name.casefold() for name in str(location).split("/") if name]
            if any(names[:size] == held[-size:] for size in range(1, min(len(names), len(held)) + 1)):
                return True
    return False


def _starts_a_path_into_protected(start: Path, scope: ShellScope) -> bool:
    """Whether a path that begins as ``start`` (it may end in the middle of a name) can be a protected location or
    lie in one."""
    folded: str = str(start).casefold()
    for location in scope.protected:
        place: str = str(location).casefold().rstrip("/")
        if place.startswith(folded) or folded == place or folded.startswith(f"{place}/"):
            return True
    return False


_GIT_GLOBAL_WITH_VALUE: frozenset[str] = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
# The settings a git line may give (``git -c``, ``git config``) without asking: none of them runs a command, loads a
# file or sends to another address, as ``core.fsmonitor``, ``core.sshCommand``, ``core.pager``, ``alias.*``,
# ``include.path`` and ``url.*.insteadOf`` do. Matched without letter case, as git reads a key.
_GIT_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "user.name",
        "user.email",
        "core.quotepath",
        "core.autocrlf",
        "core.safecrlf",
        "core.eol",
        "core.filemode",
        "core.ignorecase",
        "core.precomposeunicode",
        "core.abbrev",
        "core.whitespace",
        "init.defaultbranch",
        "pull.rebase",
        "pull.ff",
        "push.default",
        "push.autosetupremote",
        "fetch.prune",
        "rebase.autosquash",
        "rebase.autostash",
        "merge.conflictstyle",
        "diff.noprefix",
        "diff.renames",
        "diff.algorithm",
        "diff.colormoved",
        "commit.verbose",
        "gc.auto",
        "maintenance.auto",
        "safe.directory",
        "protocol.version",
    }
)
_GIT_SAFE_KEY_PREFIXES: tuple[str, ...] = ("color.", "advice.", "log.", "status.", "column.", "i18n.", "blame.")
# What ``git config`` is given besides a key and a value: the options that only read, and the ones that take a value.
_GIT_CONFIG_READS: frozenset[str] = frozenset(
    {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "-l", "--list"}
)
_GIT_CONFIG_REMOVES: frozenset[str] = frozenset({"--unset", "--unset-all", "--remove-section"})
_GIT_CONFIG_VALUES: frozenset[str] = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment"})
_GIT_CONFIG_VERBS: frozenset[str] = frozenset({"get", "list", "set", "unset", "rename-section", "remove-section"})
_GIT_READ_ONLY: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "ls-files",
        "blame",
        "describe",
        "fetch",
        "remote",
        "config",
        "grep",
        "shortlog",
        "ls-remote",
        "rev-list",
        "cat-file",
        "tag",
        "branch",
        "stash",
    }
)
# What git may do in one of Dot's own folders: read. The others above can also write (a config, a stash, a ref).
_GIT_READS_IN_DOT: frozenset[str] = _GIT_READ_ONLY - {"fetch", "remote", "config", "tag", "branch", "stash"}


def _git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    index: int = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in _GIT_GLOBAL_WITH_VALUE else 1
    if index >= len(args):
        return "", []
    return args[index], args[index + 1 :]


def _git_scope(args: list[str], rest: list[str], scope: ShellScope) -> ShellScope:
    """``scope`` in the directories git's ``-C`` options move it to (``rest`` follows the subcommand)."""
    where: ShellScope = scope
    for index, arg in enumerate(args[: len(args) - len(rest)]):
        if arg == "-C" and index + 1 < len(args):
            where = _with_places(where, _places(args[index + 1], where))
    return where


def _git_safe_key(key: str) -> bool:
    folded: str = key.casefold()
    return folded in _GIT_SAFE_KEYS or folded.startswith(_GIT_SAFE_KEY_PREFIXES)


def _git_settings(globals_: list[str]) -> list[str]:
    """The keys the global options of a git line (``globals_``, before its subcommand) set for it: ``-c key=value``,
    ``--config-env key=VAR``; an ``--exec-path`` that moves where git finds its commands counts as an unknown key."""
    keys: list[str] = []
    for index, arg in enumerate(globals_):
        if arg in ("-c", "--config-env") and index + 1 < len(globals_):
            keys.append(globals_[index + 1].partition("=")[0])
        elif arg.startswith("--config-env="):
            keys.append(arg.removeprefix("--config-env=").partition("=")[0])
        elif arg.startswith("--exec-path="):
            keys.append("--exec-path")
    return keys


def _git_config_key(rest: list[str]) -> str | None:
    """The key a ``git config`` line (``rest`` follows ``config``) sets; None when it only reads or removes."""
    if any(arg in _GIT_CONFIG_READS or arg in _GIT_CONFIG_REMOVES for arg in rest):
        return None
    operands: list[str] = []
    index: int = 0
    while index < len(rest):
        if rest[index] in _GIT_CONFIG_VALUES:
            index += 2
            continue
        if not rest[index].startswith("-"):
            operands.append(rest[index])
        index += 1
    if "--rename-section" in rest or operands[:1] == ["rename-section"]:
        # The section's new name is what the settings under it become.
        return operands[-1] if operands else None
    if operands[:1] and operands[0] in _GIT_CONFIG_VERBS:
        return operands[1] if operands[0] == "set" and len(operands) > 1 else None
    return operands[0] if operands else None


def _assess_git(args: list[str], scope: ShellScope) -> Finding:
    sub, rest = _git_subcommand(args)
    settings: list[str] = _git_settings(args[: len(args) - len(rest)])
    if sub == "config":
        settings.extend(key for key in [_git_config_key(rest)] if key is not None)
    unsafe: list[str] = [key for key in settings if not _git_safe_key(key)]
    if unsafe:
        return _unknown(f"git is given {', '.join(unsafe)}, a setting that can make it run a command or load a file")
    where: ShellScope = _git_scope(args, rest, scope)
    outside: tuple[Flag, ...] = () if _cwd_class(where) in ("workspace", "temp") else ("outside_workspace",)
    destructive_args: set[str] = {"--hard", "--force", "-f", "-fd", "-fdx", "-df", "-D", "--delete", "drop", "clear"}
    if sub == "push":
        if any(arg in ("--force", "-f", "--force-with-lease", "--delete", "--mirror") for arg in rest):
            return _finding("high", ("destructive", "external_send", *outside), "force-pushes or deletes remote refs")
        return _finding("medium", ("upload",), "pushes commits to a remote")
    if sub and sub not in _GIT_READS_IN_DOT and _cwd_class(where) == "dot":
        return _finding("high", ("secret_access",), f"git {sub} {CHANGES_DOT}")
    if sub in ("reset", "clean", "checkout", "restore", "rebase", "filter-branch", "gc", "reflog", "switch"):
        if sub in ("reset", "clean", "filter-branch") and destructive_args.intersection(rest):
            return _finding("medium", ("destructive", *outside), f"git {sub} discards work")
        if sub in ("checkout", "restore") and ("--" in rest or "." in rest):
            return _finding("medium", ("destructive", *outside), f"git {sub} discards working tree changes")
        return _finding("low", (), f"git {sub}")
    if sub in ("branch", "stash", "tag") and destructive_args.intersection(rest):
        return _finding("medium", ("destructive", *outside), f"git {sub} deletes refs or stashes")
    if sub in _GIT_REMOTE_READS:
        return _fetching("safe" if sub in _GIT_READ_ONLY else "low", (), f"git {sub} reads from a remote")
    if sub in _GIT_READ_ONLY:
        return _finding("safe", (), f"git {sub}")
    return _finding("low", (), f"git {sub or 'command'}")


def _clone_operands(rest: list[str]) -> list[str]:
    """The operands of ``git clone`` (its address, then the folder it may be given), its options left out."""
    operands: list[str] = []
    index: int = 0
    while index < len(rest):
        if rest[index] in _GIT_CLONE_VALUES:
            index += 2
            continue
        if not rest[index].startswith("-"):
            operands.append(rest[index])
        index += 1
    return operands


def _cloned_into(args: list[str], scope: ShellScope) -> list[Finding]:
    """What ``git clone`` writes: the folder it is given, else a folder in the directory the line is in."""
    sub, rest = _git_subcommand(args)
    if sub != "clone":
        return []
    operands: list[str] = _clone_operands(rest)
    return _writes_to("git clone", operands[1:2] or ["."], _git_scope(args, rest, scope))


def _fetched_places(command: str, args: list[str], scope: ShellScope) -> list[Path]:
    """Where a command puts what it brings from the network (item 385): the files of a fetcher
    (:func:`_fetched_files`), and the folder ``git clone`` makes (the one it is given, else the one its address
    names), from the directories git's ``-C`` moves it to."""
    if command in _NETWORK_FETCHERS:
        return _located_all(_fetched_files(command, args), scope)
    if command != "git":
        return []
    sub, rest = _git_subcommand(args)
    operands: list[str] = _clone_operands(rest) if sub == "clone" else []
    if not operands:
        return []
    folder: list[str] = operands[1:2] or [Path(operands[0].rstrip("/")).name.removesuffix(".git")]
    return _located_all(folder, _git_scope(args, rest, scope))


def _located_all(raws: list[str], scope: ShellScope) -> list[Path]:
    """Where each of ``raws`` lands from each directory the line may be in; a word only the running line knows is
    taken as written."""
    return [
        _located(value, cwd, scope) for raw in raws for value in _expansions(raw, scope) or [raw] for cwd in scope.cwds
    ]


def _carried_download(command: str, args: list[str], scope: ShellScope) -> list[str]:
    """Where a copy or a move (``cp``, ``mv``, ``ditto``, ``install``, ``rsync``) puts a file the line downloaded, which
    then still counts as downloaded (item 385)."""
    if command not in ("cp", "mv", "ditto", "install", "rsync") or not scope.downloaded:
        return []
    # ``-t dir`` names the folder for GNU cp, mv and install alone; rsync's ``-t`` keeps times, ditto has none.
    words, folders = _target_folders(args) if command in _TARGET_FOLDER_COMMANDS else (args, [])
    operands: list[str] = _positional(words)
    sources, destinations = (operands, folders) if folders else (operands[:-1], operands[-1:])
    if any(_names_one(source, scope.downloaded, scope, under=True) for source in sources):
        return destinations
    return []


def _names_one(raw: str, places: tuple[Path, ...], scope: ShellScope, *, under: bool) -> bool:
    """Whether ``raw``, a path the line runs or feeds a shell, is one of ``places`` or, with ``under``, lies under one
    (the folder ``git clone`` made holds the scripts in it). The files a decoder named are matched as written: a decoder
    that names ``/`` as an input makes every path under it no decoded code."""
    if not places:
        return False
    paths: list[Path] = [
        _located(value, cwd, scope) for value in _expansions(raw, scope) or [raw] for cwd in scope.cwds
    ]
    return any(path == place or (under and place in path.parents) for path in paths for place in places)


def _fed_a_download(stages: list[list[str]], scope: ShellScope) -> list[Finding]:
    """A finding when the last of ``stages`` (one pipeline's commands so far) is a shell or an interpreter and a file
    the line downloaded or decoded reaches its standard input: by ``<`` (``sh < y.sh``) or as an operand of a command
    before it in the pipe (``cat y.sh | bash``). That runs the file as naming it does (item 385, item 528). A download
    reached the network and marks the run; a decode did not."""
    words, _, _, _ = _split_redirects(stages[-1], ())
    run, _ = _run_by_wrappers(_plain(words), scope)
    if not run or Path(run[0]).name not in (_SHELLS | _INTERPRETERS):
        return []
    redirected: list[str] = [stages[-1][index + 1] for index, token in enumerate(stages[-1][:-1]) if token == "<"]
    piped: list[str] = [word for stage in stages[:-1] for word in _positional(_plain(stage)[1:])]
    fed: tuple[str, ...] = (*redirected, *piped)
    if any(_names_one(word, scope.downloaded, scope, under=True) for word in fed):
        return [_fetching("high", ("install",), "runs a script the line downloaded, from its standard input")]
    if any(_names_one(word, scope.decoded, scope, under=False) for word in fed):
        return [_finding("high", ("install",), "runs code the line decoded, from its standard input")]
    return []


def _runs_download(raw: str, scope: ShellScope) -> list[Finding]:
    """A finding when ``raw``, a script the line runs, is a file the line fetched (or lies in a folder it cloned) or a
    file a decoder wrote: fetching or decoding a script and then running it is piping it into a shell in two steps
    (item 385, item 528). A download reached the network, so it marks the run; a decode did not."""
    if _names_one(raw, scope.downloaded, scope, under=True):
        return [_fetching("high", ("install",), "runs a script the line downloaded")]
    if _names_one(raw, scope.decoded, scope, under=False):
        return [_finding("high", ("install",), "runs code the line decoded")]
    return []


# base64, base32, basenc, xxd and gpg fold their short switches into one word, with any value glued to the end
# (``-rp``, ``-dox.sh``), so a decode switch is read among a word's leading letters, where a value cannot reach; the
# ``d`` of gpg's ``-d`` is its decrypt and no other of its switches. openssl spells each switch on its own and reuses
# letters (``-md`` is a digest, not decode), so only its own ``-d``/``-decrypt`` word counts; uudecode and b64decode
# only ever decode.
_DECODE_LETTERS: dict[str, str] = {
    "base64": "dD",
    "gbase64": "dD",
    "base32": "d",
    "gbase32": "d",
    "basenc": "d",
    "xxd": "r",
    "gpg": "d",
    "gpg2": "d",
}
_DECODE_LONG: frozenset[str] = frozenset({"--decode", "--decrypt"})
_ALWAYS_DECODE: frozenset[str] = frozenset({"uudecode", "b64decode"})
_LEADING_FLAGS: re.Pattern[str] = re.compile(r"-([A-Za-z]+)")


def _decodes(run: list[str]) -> bool:
    """Whether ``run`` -- a command and its arguments, its wrappers already looked through -- turns an encoded or
    encrypted blob back into the bytes it stood for: ``base64 -d`` (and ``gbase64``, ``base32``, ``basenc``), ``xxd
    -r``, ``gpg -d`` or ``openssl`` given a decrypt switch, or ``uudecode``/``b64decode``, whose only work is to
    decode. ``base64 blob`` without ``-d`` encodes, so what it prints is not code; it does not count."""
    if not run:
        return False
    command: str = Path(run[0]).name
    if command in _ALWAYS_DECODE:
        return True
    if command == "openssl":
        return "-d" in run[1:] or "-decrypt" in run[1:]
    letters: str | None = _DECODE_LETTERS.get(command)
    if letters is None:
        return False
    wanted: set[str] = set(letters)
    for word in run[1:]:
        if word in _DECODE_LONG:
            return True
        lead: re.Match[str] | None = _LEADING_FLAGS.match(word) if not word.startswith("--") else None
        if lead is not None and wanted & set(lead.group(1)):
            return True
    return False


def _decoded_writes(words: list[str], targets: list[str], scope: ShellScope) -> tuple[Path, ...]:
    """Where a decoder on this line may have written what it decoded, located. Which word is a decoder's output file
    differs by tool and by option spelling (``base64 -o``/``--output=``/``-ox``, ``openssl -out``, ``xxd``'s second
    operand), too many to read one by one, so the guard takes every file the decoder names -- the words that are not
    options (:func:`_positional`: its operands and space-separated option values), the value of ``-o``/``--output`` in
    any spelling, the value glued after ``=`` in a long option, and its ``>`` redirects -- as code it may have written.
    The file is opaque code on disk, judged like a download when a later line runs it; taking an input file too costs
    only a question if that file is run, the safe side (item 528)."""
    run, _ = _run_by_wrappers(_plain(words), scope)
    if not _decodes(run):
        return ()
    args: list[str] = run[1:]
    glued: list[str] = [word.split("=", 1)[1] for word in args if word.startswith("-") and "=" in word]
    written: list[str] = [*_positional(args), *_option_values(args, "-o", "--output"), *glued, *targets]
    return tuple(_located_all(written, scope))


def _fed_a_decode(stages: list[list[str]], scope: ShellScope) -> list[Finding]:
    """A finding when the last of ``stages`` (one pipeline's commands so far) is a shell or an interpreter and an
    earlier command in the pipe decodes its standard input (``base64 -d blob | sh``, ``cat blob | base64 -d | bash``).
    Decoding a blob and running it hides the code from the guard as a download does, and runs it with the owner's
    rights the same way, so it is judged as ``curl | sh`` is (item 528)."""
    words, _, _, _ = _split_redirects(stages[-1], ())
    run, _ = _run_by_wrappers(_plain(words), scope)
    if not run or Path(run[0]).name not in (_SHELLS | _INTERPRETERS):
        return []
    if any(_decodes(_run_by_wrappers(_plain(stage), scope)[0]) for stage in stages[:-1]):
        return [_finding("high", ("install",), "runs code it decoded from its standard input")]
    return []


def _assess_network_fetcher(command: str, args: list[str]) -> Finding:
    if _fetcher_sends(command, args):
        return _fetching("medium", ("upload",), f"{command} sends data to a remote server")
    return _fetching("low", (), f"{command} fetches from a remote server")


def _fetcher_sends(command: str, args: list[str]) -> bool:
    """Whether a fetcher sends a body, a form or a file, or asks for a method that changes something, each read by its
    own options: curl's and wget's long options of ``_SEND_OPTIONS`` and their method options, curl's switches, and
    httpie's forms, methods and data items."""
    if command in ("http", "https", "xh"):
        return _httpie_sends(args)
    if any(word.startswith(_SEND_OPTIONS) for word in args):
        return True
    if command == "wget":
        return _names_a_sending_method(args, "--method")
    return command == "curl" and (_names_a_sending_method(args, "--request") or _curl_switches_send(args))


def _names_a_sending_method(args: list[str], option: str) -> bool:
    """Whether ``option`` (curl's ``--request``, wget's ``--method``) names POST, PUT, DELETE or PATCH, as the word
    after it or attached with "="."""
    for index, word in enumerate(args):
        named: str = args[index + 1] if word == option and index + 1 < len(args) else ""
        if word.startswith(f"{option}="):
            named = word.removeprefix(f"{option}=")
        if named.upper() in _SEND_METHODS:
            return True
    return False


def _curl_switches_send(args: list[str]) -> bool:
    """Whether a word of curl's short switches sends: ``-d``, ``-F`` or ``-T`` anywhere before a switch that takes a
    value (``-sSd@notes.txt``), or ``-X`` with a method that changes something (``-XPOST``, ``-X POST``)."""
    for index, word in enumerate(args):
        if not word.startswith("-") or word.startswith("--"):
            continue
        for position, letter in enumerate(word[1:], start=1):
            if letter in _CURL_SENDS:
                return True
            if letter in _CURL_VALUES:
                method: str = word[position + 1 :] or (args[index + 1] if index + 1 < len(args) else "")
                if letter == "X" and method.upper() in _SEND_METHODS:
                    return True
                break
    return False


def _httpie_sends(args: list[str]) -> bool:
    """Whether httpie or xh send a body: a form (``-f``, ``--form``, ``--multipart``) or a raw body (``--raw``), a
    method that changes something before the URL, or a data item after it."""
    if any(word in ("-f", "--form", "--multipart") or word.startswith("--raw") for word in args):
        return True
    words: list[str] = _positional(args)
    named: bool = bool(words) and words[0].upper() in _METHODS
    if named and words[0].upper() in _SEND_METHODS:
        return True
    return any(_HTTPIE_DATA.match(item) for item in words[2 if named else 1 :])


def _fetched_files(command: str, args: list[str]) -> list[str]:
    """Where a fetcher saves what it fetched: the ``-o`` files of curl and the others, the folder of curl's ``-O`` (its
    ``--output-dir``, else the directory the line is in), and wget's ``-O`` file or else its ``-P`` folder or the
    directory the line is in (wget's ``-o`` is its log); ``-`` is the standard output. A ``-o`` file of curl lies in its
    ``--output-dir``, and one of aria2c in its ``-d`` folder."""
    named: list[str] = _option_values(args, "-o", "--output")
    # curl's --output-dir and aria2c's -d hold the -o files too, when these do not name a folder of their own.
    folder: list[str] = (
        _option_values(args, "", "--output-dir")
        if command == "curl"
        else _option_values(args, "-d", "--dir")
        if command == "aria2c"
        else []
    )
    files: list[str] = [
        str(Path(folder[-1]) / name) if folder and name != "-" and not name.startswith(("/", "~", "$")) else name
        for name in named
    ]
    if command == "curl" and any(
        arg in ("--remote-name", "--remote-name-all") or re.fullmatch(r"-[A-Za-z#]*O[A-Za-z#]*", arg) for arg in args
    ):
        files.extend(_option_values(args, "", "--output-dir") or ["."])
    if command == "wget":
        documents: list[str] = _option_values(args, "-O", "--output-document")
        files.extend(documents or _option_values(args, "-P", "--directory-prefix") or ["."])
    return [path for path in files if path != "-"]


def _remote_sends(command: str, args: list[str]) -> bool:
    """Whether a remote command may send this machine's data out: scp and rsync when they copy to another machine
    (their last operand is remote), the shells and sockets always."""
    if command not in ("scp", "rsync"):
        return True
    operands: list[str] = _positional(args)
    return bool(operands) and _REMOTE_PATH.match(operands[-1]) is not None


def _assess_code_host(command: str, args: list[str]) -> Finding:
    """A code host's command line reads unless it creates, comments, edits, merges or deletes there (``gh api`` with
    fields or a method other than GET)."""
    words: list[str] = _positional(args)
    if words[:1] == ["api"]:
        methods: list[str] = [value.upper() for value in _option_values(args, "-X", "--method")]
        sends: bool = any(arg in _CODE_HOST_SENDS or arg.startswith("--field=") for arg in args) or any(
            method != "GET" for method in methods
        )
    else:
        sends = bool(words) and not _CODE_HOST_READS.intersection(words[:2])
    if sends:
        return _fetching("medium", ("upload",), f"{command} changes something on a code host")
    return _fetching("low", (), f"{command} reads from a code host")


def _unpacked_into(command: str, args: list[str]) -> list[str]:
    """Where an archive is unpacked: tar's ``-C`` folder when it extracts, unzip's ``-d`` folder, else the directory
    the line is in; nothing when the command lists, tests or creates an archive."""
    if command in _TARS:
        first: str = args[0] if args else ""
        extracts: bool = any(
            arg in ("--extract", "--get") or re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", arg) for arg in args
        ) or (not first.startswith("-") and "x" in first)
        return (_option_values(args, "-C", "--directory") or ["."]) if extracts else []
    if any(arg in _UNZIP_READS for arg in args):
        return []
    return _option_values(args, "-d", "") or ["."]


def _read_switches(switches: _Switches, args: list[str]) -> _InterpreterLine:
    """``args`` read as an interpreter with ``switches`` reads them: its words of switches letter by letter (the code of
    ``-pe 'code'`` or ``-cCODE``, the values its other switches take), until the first operand or ``--``."""
    code: list[str] = []
    in_place: bool = False
    index: int = 0
    while index < len(args):
        word: str = args[index]
        index += 1
        if word == "--":
            return _InterpreterLine(code=tuple(code), operands=tuple(args[index:]), in_place=in_place)
        if word in _LONG_INLINE_CODE:
            code.extend(args[index : index + 1])
            index += 1
            continue
        if word.startswith(tuple(f"{option}=" for option in _LONG_INLINE_CODE)):
            code.append(word.split("=", 1)[1])
            continue
        if word == "-" or not word.startswith("-"):
            return _InterpreterLine(code=tuple(code), operands=tuple(args[index - 1 :]), in_place=in_place)
        if word.startswith("--"):
            continue
        position: int = 1
        while position < len(word):
            letter: str = word[position]
            position += 1
            if letter in switches.code or letter in switches.values:
                value: str = word[position:] if switches.joined else ""
                if not value and index < len(args):
                    value = args[index]
                    index += 1
                if letter in switches.code:
                    code.append(value)
                if letter in switches.ends:
                    # The rest of the line is the program's own arguments; a module's name stands as its script.
                    rest: tuple[str, ...] = tuple(args[index:]) if letter in switches.code else (value, *args[index:])
                    return _InterpreterLine(code=tuple(code), operands=rest, in_place=in_place)
                break
            if letter in switches.attached:
                in_place = in_place or letter in switches.in_place
                break
            if letter in switches.digits:
                while position < len(word) and word[position] in "01234567":
                    position += 1
    return _InterpreterLine(code=tuple(code), operands=(), in_place=in_place)


def _interpreter_line(command: str, args: list[str]) -> _InterpreterLine:
    """``args`` read as ``command`` reads them: by its words of switches when the guard knows how it reads them
    (``_SWITCHES``), else by the switches that take inline code (``-c``, ``-e``, ``--eval``) alone."""
    switches: _Switches | None = _SWITCHES.get(command)
    if switches is not None:
        return _read_switches(switches, args)
    return _InterpreterLine(
        code=tuple(args[index + 1] for index, arg in enumerate(args[:-1]) if arg in _INLINE_CODE),
        operands=tuple(_positional(args)),
        in_place=False,
    )


def _assess_interpreter(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """An interpreter runs code the guard does not read. Its inline code (``-c``, ``-e``, in a word of switches such as
    ``-pe``, a here-document or a pipe it reads as its program) that names the network, or runs a command of the
    network, counts as fetching and sending, and code that names one of Dot's own folders as changing it. The files perl
    and ruby edit in place (``-i``) are judged as written."""
    line: _InterpreterLine = _interpreter_line(command, args)
    inline: list[str] = list(line.code)
    if given.text is not None and (not line.operands or line.operands[0] == "-") and not inline:
        inline.append(given.text)
    code: str = "\n".join(inline)
    findings: list[Finding] = [
        _finding("medium" if inline else "low", (), f"{command} runs {'inline code' if inline else 'a script'}"),
        *(_runs_download(line.operands[0], scope) if not inline and line.operands else []),
    ]
    if _NETWORK_CODE.search(code) or _NETWORK_COMMAND_CODE.search(code):
        findings.append(_fetching("medium", ("upload",), f"{command} runs code that reaches the network"))
    if mentions_protected(code, scope.dot_paths):
        findings.append(_finding("high", ("secret_access",), f"{command} runs code that {CHANGES_DOT}"))
    if line.in_place:
        # Without inline code, the first operand is the script; the files after it are the ones edited.
        findings.extend(_writes_to(command, list(line.operands if line.code else line.operands[1:]), scope))
    return findings


def _assess_installer(command: str, args: list[str]) -> Finding | None:
    sub: str = next((arg for arg in args if not arg.startswith("-")), "")
    global_flag: bool = any(arg in ("-g", "--global") for arg in args)
    reason: str = f"{command} {sub}".strip()
    if command == "brew" and sub in ("install", "reinstall", "upgrade", "uninstall", "remove", "tap", "untap", "cask"):
        return _finding("medium", ("install",), f"{reason} changes installed software")
    if command in ("npm", "pnpm", "yarn", "bun") and global_flag:
        return _finding("medium", ("install",), f"{reason} installs globally")
    if command in ("pip", "pip3") and sub in ("install", "uninstall"):
        return _finding("medium", ("install",), f"{reason} may change the global Python environment")
    if command in ("gem", "cargo", "go", "pipx", "port", "mas") and sub in ("install", "uninstall", "upgrade"):
        return _finding("medium", ("install",), f"{reason} installs software")
    if command == "uv" and sub == "tool":
        return _finding("medium", ("install",), "uv tool installs software")
    return None


def _shell_arguments(args: list[str]) -> tuple[bool, list[str], bool]:
    """How a shell is called: whether it runs a command string (``-c`` anywhere in its options, as in ``bash -lc``,
    or fish's ``--command``), the words after its options (the string first), and whether it reads commands from
    its input (``-s``)."""
    inline: bool = False
    reads_input: bool = False
    index: int = 0
    while index < len(args):
        word: str = args[index]
        if word in ("--", "-"):
            index += 1
            break
        if word == "--command" or word.startswith("--command="):
            value: str = word.partition("=")[2] if "=" in word else (args[index + 1] if index + 1 < len(args) else "")
            return True, [value], False
        if word in _SHELL_VALUES:
            index += 2
            continue
        if word.startswith("--"):
            index += 1
            continue
        if len(word) > 1 and word[0] in "-+":
            inline = inline or (word[0] == "-" and "c" in word[1:])
            reads_input = reads_input or (word[0] == "-" and "s" in word[1:])
            index += 2 if word[-1] in "oO" else 1
            continue
        break
    return inline, args[index:], reads_input


def _assess_shell_call(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """A shell given ``-c`` is judged by its command string; one given no script, or its standard input by name
    (``bash /dev/stdin``), by the text it reads when the line shows it (a here-document, ``echo ... |``)."""
    inline, operands, reads_input = _shell_arguments(args)
    if inline:
        return _assess_all(operands[0], scope) if operands else []
    if (reads_input or not operands or operands[0] in _STDIN_FILES) and given.text is not None:
        return _assess_all(given.text, scope)
    return [_finding("low", (), f"runs {command}"), *(_runs_download(operands[0], scope) if operands else [])]


def _assess_source(args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """``source`` and ``.`` run a file's commands in the line's own shell. Its standard input by name (``/dev/stdin``)
    holds the text a here-string, a here-document or a pipe gives it, judged as a line, or code the line does not
    show; a command's output (``source <(...)``) is code only the running line knows. A file on disk is a script the
    guard does not read, as when ``bash`` runs one; without a file nothing runs."""
    operands: list[str] = args[1:] if args[:1] == ["--"] else args
    if not operands:
        return [_finding("low", (), "source names no file")]
    files: list[str] = _expansions(operands[0], scope) or [operands[0]]
    if any(name in _STDIN_FILES for name in files):
        if given.text is None:
            return [_unknown("sources what its standard input brings, which the line does not show")]
        return _assess_all(given.text, scope)
    if _SUBSTITUTED in files:
        return [_unknown("sources the output of a command, which only the running line knows")]
    return [_finding("low", (), "source runs a script file"), *_runs_download(operands[0], scope)]


def _assess_xargs(args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """xargs runs its command with the words it reads in place of its placeholder (``-I {}``) or after its arguments:
    the paths the pipe carries, as far as the line shows them (:func:`_piped`)."""
    options, rest = _options(args, _XARGS_VALUES)
    placeholder: str = next((value for name, value in options if name in ("-I", "-J")), "")
    if not placeholder and any(name in ("-i", "--replace") for name, _ in options):
        placeholder = next((value for name, value in options if name == "--replace" and value), "{}")
    command: list[str] = rest or ["echo"]
    runs: list[list[str]] = (
        [[word.replace(placeholder, path) for word in command] for path in given.paths]
        if placeholder
        else [[*command, *given.paths]]
    )
    findings: list[Finding] = [found for run in runs for found in _assess_simple(run, scope, _NO_INPUT)]
    if Path(command[0]).name not in _NAMES_ONLY and _holds_protected(given.walked, scope):
        # The piped twin of ``find ~ -exec cat {} +`` (item 531): every file the listing walked may be one it reads.
        findings.append(
            _maybe_protected(
                "runs a command on the files a search lists under a folder that holds a protected location"
            )
        )
    return findings


def _runs_with(command: list[str], roots: list[str], scope: ShellScope, placeholder: re.Pattern[str]) -> list[Finding]:
    """A command that ``find`` or ``fd`` runs on what it finds, judged on the roots it searches: put in for its
    placeholder, or after it."""
    if any(placeholder.search(word) for word in command):
        runs: list[list[str]] = [[placeholder.sub(root, word) for word in command] for root in roots]
    else:
        runs = [[*command, *roots]]
    return [found for run in runs for found in _assess_simple(run, scope, _NO_INPUT)]


def _find_parts(args: list[str]) -> tuple[list[str], list[str]]:
    """The roots ``find`` searches (the directory the line is in when it is given none) and its expression."""
    index: int = 0
    roots: list[str] = []
    while index < len(args) and re.fullmatch(r"-[HLPEXdsx]+|-f|-O\d", args[index]):
        if args[index] == "-f" and index + 1 < len(args):
            roots.append(args[index + 1])
            index += 1
        index += 1
    while index < len(args) and not args[index].startswith("-") and args[index] not in ("(", "!", ")", ","):
        roots.append(args[index])
        index += 1
    return roots or ["."], args[index:]


# The commands that read every file under a folder they are given, and the words that make some of them do it: a
# recursive grep or copy, a search (ripgrep, ag, ack) always, a find that runs a command on what it finds (item 531).
_RECURSIVE_GREPS: frozenset[str] = frozenset({"grep", "egrep", "fgrep"})
_RECURSIVE_SEARCHES: frozenset[str] = frozenset({"rg", "ag", "ack"})
_RECURSIVE_COPIES: frozenset[str] = frozenset({"cp", "rsync"})
_RECURSE: re.Pattern[str] = re.compile(r"-[A-Za-z0-9]*[rR][A-Za-z0-9]*")
_RECURSE_COPY: re.Pattern[str] = re.compile(r"-[A-Za-z0-9]*[rRa][A-Za-z0-9]*")
# tar creating an archive, or adding to one (append, update): it reads the files it is given.
_TAR_CREATES: re.Pattern[str] = re.compile(r"-[A-Za-z0-9]*[cru][A-Za-z0-9]*|[cru][A-Za-z0-9]*")


def _searched(operands: list[str]) -> list[str]:
    """The folders a search may read, as its operands give them, and the directory the line is in. Which operand is a
    folder is not told without each tool's options (the pattern and the value of an option such as ``-A 30`` or
    ``--ignore-file /dev/null`` stand among them, and one can look like a path), and a search given no folder reads the
    directory the line is in; so every operand may be one, and that directory is always counted, which costs a question
    from the home folder, not a miss."""
    return [*operands, "."]


def _walked_roots(command: str, args: list[str]) -> list[str]:
    """The folders ``command`` reads every file under, as it is given them (item 531): what a recursive ``grep``, a
    search, a recursive ``cp`` or ``rsync``, ``ditto``, ``tar`` creating an archive, ``zip -r`` and a ``find`` that runs
    a command on what it finds read, which may be a protected location below the folder."""
    operands: list[str] = _positional(args)
    short: list[str] = [word for word in args if word.startswith("-") and not word.startswith("--")]
    long: set[str] = {word.partition("=")[0] for word in args if word.startswith("--")}
    if command in _RECURSIVE_GREPS:
        recursive: bool = (
            any(_RECURSE.fullmatch(word) for word in short)
            or bool(long & {"--recursive", "--dereference-recursive"})
            or "recurse" in _option_values(args, "-d", "--directories")
        )
        return _searched(operands) if recursive else []
    if command in _RECURSIVE_SEARCHES:
        # ``rg --files`` lists the names of the files and reads none of them.
        return [] if "--files" in long else _searched(operands)
    if command == "git":
        # ``git grep --no-index`` searches the files of a folder as any recursive grep does, and ``git diff --no-index``
        # compares two folders file by file, printing what differs: against an empty folder, all of it.
        sub, rest = _git_subcommand(args)
        if sub == "grep" and "--no-index" in rest:
            return _searched(_positional(rest))
        return _positional(rest) if sub == "diff" and "--no-index" in rest else []
    if command == "diff":
        # ``diff -r a b`` prints the files of one folder that the other has not (``-N``: as if they were empty).
        return operands if any(_RECURSE.fullmatch(word) for word in short) or "--recursive" in long else []
    if command == "scp":
        return operands if any(_RECURSE.fullmatch(word) for word in short) else []
    if command == "hdiutil":
        return [args[index + 1] for index, word in enumerate(args[:-1]) if word in ("-srcfolder", "-srcdir")]
    if command in ("rclone", "pax"):
        return operands
    if command == "aws":
        return operands if "s3" in operands and ("sync" in operands or "--recursive" in long) else []
    if command == "gsutil":
        recursive_gs: bool = any(_RECURSE.fullmatch(word) for word in short)
        return operands if ("rsync" in operands and recursive_gs) or ("cp" in operands and recursive_gs) else []
    if command in _RECURSIVE_COPIES:
        # Every operand: the folder a copy goes into may stand anywhere (``cp -R -t dest src``).
        recursive = any(_RECURSE_COPY.fullmatch(word) for word in short) or bool(long & {"--recursive", "--archive"})
        return operands if recursive else []
    if command == "ditto":
        return operands
    if command in _TARS:
        creates: bool = any(_TAR_CREATES.fullmatch(word) for word in [*short, *operands[:1]]) or bool(
            long & {"--create", "--append", "--update"}
        )
        return [*operands, *_option_values(args, "-C", "--directory")] if creates else []
    if command == "zip":
        return operands if any(_RECURSE.fullmatch(word) for word in short) else []
    if command == "find":
        roots, expression = _find_parts(args)
        return roots if _runs_a_reader(expression, _FIND_EXEC) else []
    if command == "fd":
        start: int | None = next((index for index, word in enumerate(args) if word in _FD_EXEC), None)
        return _searched(_positional(args[:start])) if start is not None and _runs_a_reader(args, _FD_EXEC) else []
    return []


def _runs_a_reader(words: list[str], flags: frozenset[str]) -> bool:
    """Whether ``words`` (the expression of a ``find``, the words of an ``fd``) run a command, by one of ``flags``, that
    may read the files it is given: one that only prints their names or sizes (:data:`_NAMES_ONLY`) does not."""
    return any(
        word in flags and (index + 1 == len(words) or Path(words[index + 1]).name not in _NAMES_ONLY)
        for index, word in enumerate(words)
    )


def _listed_folders(command: str, args: list[str]) -> list[str]:
    """The folders a listing is made from, as the line gives them (item 531): the roots of a ``find``, the paths of
    ``fd`` and of ``rg --files`` (:func:`_searched`). Other listings name files the line shows."""
    if command == "find":
        return _find_parts(args)[0]
    if command == "fd" or (command in _RECURSIVE_SEARCHES and "--files" in args):
        return _searched(_positional(args))
    return []


def _glob_holds_protected(pattern: Path, scope: ShellScope) -> bool:
    """Whether the folder glob ``pattern`` (``~/Lib*``, ``/Users/*``) matches a protected location or a folder above
    one, letter case aside."""
    folded: str = str(pattern).casefold()
    return any(
        fnmatch.fnmatchcase(str(place).casefold(), folded)
        for location in scope.protected
        for place in (location, *location.parents)
    )


def _holds_protected(folders: Sequence[str], scope: ShellScope) -> bool:
    """Whether one of ``folders``, as the line gives them, is or holds a protected location, a glob that matches one
    too. A folder only the running line knows stands for the home folder, as in :func:`_places`."""
    for folder in folders:
        for place in _places(folder, scope):
            if any(within(location, place) for location in scope.protected):
                return True
            if any(char in str(place) for char in _GLOB_CHARS) and _glob_holds_protected(place, scope):
                return True
    return False


def _substituted_listing(inner: str, scope: ShellScope) -> list[Finding]:
    """A finding when what the command ``inner``, substituted into the line, prints is a listing of the files under a
    folder that holds a protected location (item 531): ``cat $(find ~ -name id_rsa)``, the substituted twin of ``find ~
    -name id_rsa | xargs cat``. The line hands those files to a command the guard cannot follow, which may read them; a
    listing that goes through ``wc`` or the like prints only a count."""
    reading: _Reading = _read(inner)
    tokens: list[str] | None = _tokenize(reading.text)
    if tokens is None:
        return []
    stages: list[list[str]] = []
    for operator, segment in _commands(_without_patterns(tokens)):
        stages = [*stages, segment] if operator in ("|", "|&") else [segment]
    if _holds_protected(_piped(stages, reading.documents).walked, scope):
        return [_maybe_protected("hands the files a search lists under a folder that holds a protected location on")]
    return []


def _walks_over_protected(command: str, args: list[str], scope: ShellScope) -> list[Finding]:
    """A finding when ``command`` reads every file under a folder that holds a protected location (item 531):
    ``grep -r secret ~``, ``find ~/Library -exec cat {} \\;``, ``cp -R ~/Library copy``. The guard reads the line, not
    what the program finds in the folders it walks, so a folder above a protected location is asked about."""
    if _holds_protected(_walked_roots(command, args), scope):
        return [_maybe_protected("reads every file under a folder that holds a protected location")]
    return []


def _reads_a_name_list(command: str, args: list[str]) -> bool:
    """Whether ``command`` reads the names of the files it works on from its standard input: ``cpio`` and ``pax``,
    ``tar -T -``, ``zip -@``, ``rsync --files-from=-`` (``xargs`` is read apart, :func:`_assess_xargs`)."""
    if command in ("cpio", "pax"):
        return True
    if command in _TARS:
        return any(word.startswith(("-T", "--files-from")) for word in args)
    if command == "zip":
        return any(
            word == "-@" or (word.startswith("-") and not word.startswith("--") and "@" in word) for word in args
        )
    return command == "rsync" and any(word.startswith("--files-from") for word in args)


# The commands that print or count what they are given without reading it, so that ``find ~ | xargs ls`` is no walk.
_NAMES_ONLY: frozenset[str] = frozenset(
    {"echo", "printf", "ls", "stat", "basename", "dirname", "realpath", "readlink", "du", "file", "true"}
)


def _assess_find(args: list[str], scope: ShellScope) -> list[Finding]:
    """find judged on every root it searches: ``-delete`` removes under them, ``-exec`` (and ``-execdir``, ``-ok``)
    runs its command on them, ``-fprint`` and ``-fls`` write a file."""
    searched, expression = _find_parts(args)
    findings: list[Finding] = []
    at: int = 0
    while at < len(expression):
        word: str = expression[at]
        if word == "-delete":
            findings.append(_assess_delete(["--", *searched], scope))
        elif word in _FIND_EXEC:
            end: int = next(
                (stop for stop in range(at + 1, len(expression)) if expression[stop] in (";", "+")), len(expression)
            )
            findings.extend(_runs_with(expression[at + 1 : end], searched, scope, re.compile(r"\{\}")))
            at = end
        elif word in _FIND_WRITES and at + 1 < len(expression):
            findings.extend(_writes_to("find", [expression[at + 1]], scope))
            at += 1
        at += 1
    return findings or [_finding("safe", (), "find")]


def _assess_fd(args: list[str], scope: ShellScope) -> list[Finding]:
    """fd judged on what it runs (``-x``, ``-X``) on the paths it searches: every word before the command that is not
    an option counts as one, the pattern included, or the directory the line is in when there is none."""
    start: int | None = next((index for index, word in enumerate(args) if word in _FD_EXEC), None)
    if start is None:
        return [_finding("safe", (), "fd")]
    roots: list[str] = [word for word in args[:start] if not word.startswith("-")] or ["."]
    end: int = next((stop for stop in range(start + 1, len(args)) if args[stop] == ";"), len(args))
    return _runs_with(args[start + 1 : end], roots, scope, _FD_PLACEHOLDER)


def _assess_rg(args: list[str], scope: ShellScope) -> list[Finding]:
    """rg judged on the program its ``--pre`` runs on each file it searches."""
    programs: list[str] = _option_values(args, "", "--pre")
    if not programs:
        return [_finding("safe", (), "rg")]
    files: list[str] = [word for word in args if not word.startswith("-") and word not in programs] or ["."]
    return [
        found for program in programs for file in files for found in _assess_simple([program, file], scope, _NO_INPUT)
    ]


def _awk_blanked(program: str) -> tuple[str, list[str]]:
    """``program`` (awk) with each string literal replaced by a numbered mark and each regular expression literal by
    a blank one, so that a ``|`` or ``>`` found in it is awk's own, and the strings' values."""
    out: list[str] = []
    strings: list[str] = []
    last: str = ""
    index: int = 0
    while index < len(program):
        char: str = program[index]
        if char == '"':
            end: int = index + 1
            while end < len(program) and program[end] != '"':
                end += 2 if program[end] == "\\" else 1
            strings.append(
                _AS_ESCAPE.sub(lambda found: _AS_ESCAPES.get(found.group(1), found.group(1)), program[index + 1 : end])
            )
            out.append(f"{len(strings) - 1}")
            last = '"'
            index = end + 1
        elif char == "/" and (last == "" or last in _AWK_REGEX_AFTER):
            end = index + 1
            while end < len(program) and program[end] not in "/\n":
                end += 2 if program[end] == "\\" else 1
            out.append("")
            last = "/"
            index = end + 1
        else:
            out.append(char)
            last = last if char in " \t" else char
            index += 1
    return "".join(out), strings


def _assess_awk(command: str, args: list[str], scope: ShellScope) -> list[Finding]:
    """awk judged on its program: the command lines it runs (``system("...")``, ``print | "..."``,
    ``"..." | getline``), the files it prints into (``print > "..."``) and the protected locations its strings name; a
    command it puts together while it runs cannot be judged."""
    options, rest = _options(args, _AWK_VALUES)
    if any(name in ("-f", "--file", "-i", "--include", "-l", "--load") for name, _ in options):
        return [_finding("low", (), f"{command} runs a program from a file")]
    sources: list[str] = [value for name, value in options if name in ("-e", "--source")]
    program: str = "\n".join(sources) if sources else (rest[0] if rest else "")
    blanked, strings = _awk_blanked(program)
    findings: list[Finding] = [*_protected_words(strings, scope)]
    for pattern in (_AWK_SYSTEM, _AWK_PIPE_OUT, _AWK_PIPE_IN):
        for match in pattern.finditer(blanked):
            if match.group(1) is None:
                findings.append(_unknown(f"{command} runs a command it puts together while it runs"))
            else:
                findings.extend(_assess_all(strings[int(match.group(1))], scope))
    findings.extend(_assess_redirects([strings[int(match.group(1))] for match in _AWK_WRITE.finditer(blanked)], scope))
    return findings or [_finding("safe", (), command)]


def _assess_dd(args: list[str], scope: ShellScope) -> list[Finding]:
    """dd judged on the file it writes (``of=``), which it empties first unless told ``conv=notrunc``, and on a
    protected one it reads (``if=``)."""
    written: list[str] = [
        word.removeprefix("of=") for word in args if word.startswith("of=") and word[3:] not in _QUIET_TARGETS
    ]
    findings: list[Finding] = _writes_to("dd", written, scope)
    if not any(word.startswith("conv=") and "notrunc" in word.removeprefix("conv=").split(",") for word in args):
        findings.extend(_replacing(written, [], scope))
    if any(word.startswith("if=") and _reaches_protected(word.removeprefix("if="), scope) for word in args):
        findings.append(_finding("high", ("secret_access",), "dd reads a protected location"))
    return findings


def _assess_sed(args: list[str], scope: ShellScope) -> list[Finding]:
    """sed judged on the files it writes: in place (``-i``), or with its ``w`` command or flag; and on the protected
    files its ``r`` command reads."""
    findings: list[Finding] = []
    if any(arg.startswith(("-i", "--in-place")) for arg in args):
        findings.append(_assess_writes("sed", [arg for arg in args if arg != ""], scope))
        # The files it edits in place: every operand after the script, or every one when -e or -f gives it.
        operands: list[str] = _positional([arg for arg in args if arg != ""])
        given: bool = any(arg.startswith(("-e", "-f", "--expression", "--file")) for arg in args)
        findings.extend(_changing(_known_paths(operands if given else operands[1:], scope)))
    for word in args:
        for match in _SED_FILES.finditer(word):
            target: str = match.group(2).strip()
            if match.group(1) in "wW":
                findings.extend(_assess_redirects([target], scope))
            elif _reaches_protected(target, scope):
                findings.append(_finding("high", ("secret_access",), "sed reads a protected location"))
    return findings or [_finding("safe", (), "sed")]


def _assess_reader(command: str, args: list[str], scope: ShellScope) -> list[Finding] | None:
    """Commands that read and print, judged safe unless an option makes them write a file (``sort -o``, ``uniq in
    out``, ``xxd in out``, ``base64 -o``, ``tree -o``, ``yq -i``), run a program (``sort --compress-program``) or
    change the system (``sysctl -w``), or unless what ``open`` opens runs or installs something. None for any other
    command."""
    if command == "sort":
        programs: list[str] = _option_values(args, "", "--compress-program")
        runs: list[Finding] = [found for program in programs for found in _assess_simple([program], scope, _NO_INPUT)]
        return [*_writes_to("sort", _option_values(args, "-o", "--output"), scope), *runs] or [
            _finding("safe", (), "sort")
        ]
    if command in ("uniq", "xxd"):
        operands: list[str] = _positional(_options(args, _UNIQ_VALUES if command == "uniq" else _XXD_VALUES)[1])
        return _writes_to(command, operands[1:2], scope) or [_finding("safe", (), command)]
    if command in ("base64", "tree"):
        targets: list[str] = _option_values(args, "-o", "--output" if command == "base64" else "")
        return _writes_to(command, targets, scope) or [_finding("safe", (), command)]
    if command == "yq":
        in_place: bool = any(arg == "--inplace" or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", arg) for arg in args)
        return [_assess_writes("yq", args, scope)] if in_place else [_finding("safe", (), "yq")]
    if command == "sysctl":
        if any(arg == "-w" or "=" in arg for arg in args):
            return [_finding("high", ("system_settings",), "sysctl changes kernel settings")]
        return [_finding("safe", (), "sysctl")]
    if command == "open":
        opened: list[str] = [target.casefold().rstrip("/") for target in _positional(args)]
        if any(target.endswith(_OPEN_INSTALLS) for target in opened):
            return [_finding("medium", ("install",), "opens an installer package")]
        if any(target.endswith(".mobileconfig") for target in opened):
            return [_finding("high", ("system_settings",), "opens a configuration profile to install")]
        apps, files = _open_operands(args)
        # An app named only when the line runs may be a terminal; ``-f`` hands it what the standard input brings.
        terminal: bool = any(name in app or "$" in app for app in apps for name in _TERMINAL_APPS)
        in_terminal: bool = terminal and (bool(files) or _has_switch(args, "f", "--stdin"))
        if in_terminal or any(target.endswith(_OPEN_RUNS) for target in files):
            # The program that runs it is not the line's own process, so Dot's sandbox does not hold it (item 385).
            return [_finding("medium", ("privilege",), "opens a script that runs as it opens, outside Dot's sandbox")]
        return [_finding("safe", (), "open")]
    return None


def _open_operands(args: list[str]) -> tuple[list[str], list[str]]:
    """The apps ``open`` is told to use (``-a`` and ``-b``, apart or glued, as many as given) and the files it opens,
    each told apart by where it stands (item 385: a file named as an app is still a file), all without letter case."""
    apps: list[str] = []
    files: list[str] = []
    index: int = 0
    while index < len(args):
        word: str = args[index]
        index += 1
        if re.fullmatch(r"-[A-Za-z]*[ab]", word) and index < len(args):
            # ``-a`` or ``-b`` alone, or last in a word of switches (``-na``), takes the next word as the app.
            apps.append(args[index].casefold())
            index += 1
        elif re.fullmatch(r"-[A-Za-z]*[ab].+", word) and not word.startswith("--"):
            letter: int = min(at for at in (word.find("a", 1), word.find("b", 1)) if at > 0)
            apps.append(word[letter + 1 :].casefold())
        elif word == "--args":
            break
        elif not word.startswith("-"):
            files.append(word.casefold().rstrip("/"))
    return apps, files


def _shell_statements(script: str) -> list[str]:
    """The text after each shell-running call of ``script``, up to the end of its statement."""
    statements: list[str] = []
    for match in _AS_SHELL.finditer(script):
        lines: list[str] = script[match.end() :].split("\n")
        taken: list[str] = [lines[0]]
        for line in lines[1:]:
            if not taken[-1].rstrip().endswith(_AS_CONTINUATIONS):
                break
            taken.append(line)
        statements.append(" ".join(taken))
    return statements


def _literal_text(statement: str) -> str:
    """The string literals of ``statement`` joined in order, escapes resolved: the command as far as it is known."""
    pieces: list[str] = [
        match.group(1) if match.group(1) is not None else match.group(2) for match in _AS_LITERAL.finditer(statement)
    ]
    return "".join(_AS_ESCAPE.sub(lambda found: _AS_ESCAPES.get(found.group(1), found.group(1)), p) for p in pieces)


def _literal_only(statement: str) -> bool:
    """Whether ``statement`` is nothing but string literals (and the glue between them)."""
    return _AS_GLUE.sub("", _AS_LITERAL.sub("", statement)) == ""


def _shell_statement_findings(statement: str, scope: ShellScope) -> list[Finding]:
    command: str = _literal_text(statement).strip()
    findings: list[Finding] = [_finding("medium", (), "runs a shell command from a script")]
    if command:
        findings.extend(_assess_all(command, scope))
    if not command or not _literal_only(statement):
        findings.append(_finding("high", (), "runs a shell command that is put together while the script runs"))
    return findings


def _applescript_findings(script: str, scope: ShellScope) -> list[Finding]:
    flags: list[Flag] = []
    reasons: list[str] = []
    risk: Risk = "medium"
    if _AS_ADMIN.search(script):
        flags.append("privilege")
        risk = "high"
        reasons.append("requests administrator privileges")
    if _AS_POWER.search(script):
        flags.append("system_settings")
        risk = "high"
        reasons.append("changes power or session state")
    if _AS_SEND.search(script):
        flags.extend(("external_send", "message_send"))
        risk = "high" if risk != "high" else risk
        reasons.append("sends a message")
    if _AS_DELETE.search(script):
        flags.append("destructive")
        risk = "high" if risk != "high" else risk
        reasons.append("deletes data")
    reason: str = "; ".join(reasons) if reasons else "runs AppleScript"
    findings: list[Finding] = [_finding(risk, tuple(dict.fromkeys(flags)), reason)]
    for statement in _shell_statements(script):
        findings.extend(_shell_statement_findings(statement, scope))
    if _AS_KEYSTROKES.search(script):
        findings.append(_finding("high", (), "types into whatever application has the keyboard focus"))
    if _AS_NATIVE.search(script):
        findings.append(_finding("high", (), "calls native system APIs through the Objective-C bridge"))
    return findings


def _osascript_source(args: list[str]) -> str | None:
    """The inline script of an osascript call (its -e arguments), or None when it runs a file or reads stdin."""
    parts: list[str] = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-e"]
    return "\n".join(parts) if parts else None


def _assess_osascript(args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    source: str | None = _osascript_source(args)
    if source is None and not _positional(args) and given.text is not None:
        return _applescript_findings(given.text, scope)
    if source is None:
        return [_finding("high", (), "runs a script file that cannot be inspected")]
    return _applescript_findings(source, scope)


def _assess_command(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """Judge one command, its reserved words, assignments and wrappers already looked through."""
    if command in _SHELLS:
        return _assess_shell_call(command, args, scope, given)
    if command == "eval":
        return _assess_all(" ".join(args), scope)
    if command in ("source", "."):
        return _assess_source(args, scope, given)
    if command == "trap":
        return _assess_trap(args, scope)
    if command in _RUNNERS:
        ran: list[Finding] | None = _assess_runner(command, args, scope, given)
        if ran is not None:
            return ran
    if command == "xargs":
        return _assess_xargs(args, scope, given)
    if command in ("rm", "rmdir", "unlink", "shred", "srm"):
        return [_assess_delete(args, scope), *_changing(_known_paths(_positional(args), scope))]
    if command == "find":
        return _assess_find(args, scope)
    if command == "fd":
        return _assess_fd(args, scope)
    if command == "rg":
        return _assess_rg(args, scope)
    if command in _AWKS:
        return _assess_awk(command, args, scope)
    if command == "sed":
        return _assess_sed(args, scope)
    reader: list[Finding] | None = _assess_reader(command, args, scope)
    if reader is not None:
        return reader
    if command == "git":
        return [_assess_git(args, scope), *_cloned_into(args, scope)]
    if command in _NETWORK_FETCHERS:
        return [_assess_network_fetcher(command, args), *_writes_to(command, _fetched_files(command, args), scope)]
    if command in _REMOTE_COPY:
        talks: Finding = _fetching(
            "medium", ("upload",) if _remote_sends(command, args) else (), f"{command} talks to a remote machine"
        )
        return [talks, _assess_writes(command, args, scope)] if command in _COPIES else [talks]
    if command in _CODE_HOSTS:
        return [_assess_code_host(command, args)]
    if command in _SENDERS or (command in _MAILERS and _positional(args)):
        return [_finding("high", ("external_send", "message_send"), f"{command} sends an email")]
    if command in _TUNNELS:
        return [_finding("high", ("external_send",), f"{command} exposes this machine to the internet")]
    if command == "osascript":
        return _assess_osascript(args, scope, given)
    if command == "security":
        return [_finding("high", ("secret_access",), "uses the macOS keychain command line")]
    if command == "op":
        # 1Password's command line reads the owner's vaults (item 493): a run fills a login through Dot, which shows
        # its model none of it, and never reads one itself.
        return [_finding("high", ("secret_access",), "uses 1Password's command line")]
    if command in _POWER:
        return [_finding("high", ("system_settings",), f"{command} changes the machine power state")]
    if command in _DISK_DESTROYERS or command.startswith("mkfs"):
        return [_finding("high", ("destructive", "system_settings"), f"{command} creates or erases filesystems")]
    if command == "diskutil":
        destructive_verbs: tuple[str, ...] = ("erase", "partition", "secureerase", "reformat", "deletevolume", "zero")
        if any(verb in " ".join(args).lower() for verb in destructive_verbs):
            return [_finding("high", ("destructive", "system_settings"), "diskutil erases or repartitions disks")]
        return [_finding("safe", (), "diskutil query")]
    if command == "dd":
        return [_finding("medium", (), "dd copies raw data"), *_assess_dd(args, scope)]
    if command == "defaults":
        verb: str = args[0] if args else ""
        if verb in ("write", "delete", "import", "rename"):
            return [_finding("medium", ("system_settings",), f"defaults {verb} changes preferences")]
        return [_finding("safe", (), "defaults read")]
    if command == "launchctl":
        verb = args[0] if args else ""
        if verb in ("list", "print", "blame", "print-cache", "print-disabled", "version", "error"):
            return [_finding("safe", (), "launchctl query")]
        return [_finding("high", ("system_settings",), f"launchctl {verb} changes launch services")]
    if command in _SYSTEM_TOOLS:
        read_only: bool = any(arg.startswith(("-get", "-list", "--status", "status", "-g", "-read")) for arg in args)
        if command in ("pmset", "scutil", "networksetup", "systemsetup", "spctl", "csrutil") and read_only:
            return [_finding("safe", (), f"{command} query")]
        return [_finding("high", ("system_settings",), f"{command} changes system configuration")]
    if command in _PROCESS_KILLERS:
        return [_finding("medium", (), f"{command} terminates processes")]
    installer: Finding | None = _assess_installer(command, args)
    if installer is not None:
        return [installer]
    if command in _INTERPRETERS:
        return _assess_interpreter(command, args, scope, given)
    if command in _WRITERS:
        return [
            _assess_writes(command, args, scope),
            *_replaced_by(command, args, scope),
            *_changing(_written_paths(command, args, scope)),
        ]
    if command in _TARS or command == "unzip":
        return [_finding("low", (), f"runs {command}"), *_writes_to(command, _unpacked_into(command, args), scope)]
    if command in _SAFE_COMMANDS:
        return [_finding("safe", (), command)]
    return [_finding("low", (), f"runs {command}")]


def _assess_privileged(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    options, rest = _options(args, _WRAPPER_VALUES[command])
    elevated: Finding = _finding("high", ("privilege",), f"runs with elevated privileges via {command}")
    if command == "su":
        lines: list[str] = [value for name, value in options if name in ("-c", "--command")]
        return [elevated, *[found for line in lines for found in _assess_all(line, scope)]]
    return [elevated, *_assess_simple(rest, scope, given)]


# The options that move a command into another folder before it reads the names it is given: ``-C`` of git, make, tar
# and env, and the long ones of uv, yarn, npm and the like (item 531).
_CHDIR_OPTIONS: frozenset[str] = frozenset(
    {"-C", "--directory", "--cwd", "--chdir", "--dir", "--working-directory", "--working-dir", "--workdir", "--prefix"}
)
# The most different folders the options of one command may move it to that are read: each is judged against every
# word of the command, so a line of many (``env -C ~ -C ~ ...`` forty times took a minute) asks instead.
_MOST_MOVES: int = 4


def _moved_scopes(words: list[str], scope: ShellScope) -> list[ShellScope] | None:
    """``scope`` in each folder the options of ``words`` move their command to, one after the other, so that a relative
    name is read from there: ``git -C ~ diff --no-index .ssh/id_rsa`` reads the key of ``~/.ssh``. A word that is no
    folder (``grep -C 3``) moves the scope to a place that holds nothing. A move to where an earlier one went is the
    same scope; None when there are more than :data:`_MOST_MOVES` different ones."""
    moved: list[ShellScope] = []
    where: ShellScope = scope
    for index, word in enumerate(words):
        name, glued, value = word.partition("=")
        if word in _CHDIR_OPTIONS and index + 1 < len(words):
            folder: str = words[index + 1]
        elif glued and name in _CHDIR_OPTIONS and name.startswith("--"):
            folder = value
        elif word.startswith("-C") and not word.startswith("--") and len(word) > 2:
            folder = word[2:]
        else:
            continue
        where = _with_places(where, _places(folder, where))
        if all(where.cwds != earlier.cwds for earlier in moved):
            moved.append(where)
            if len(moved) > _MOST_MOVES:
                return None
    return moved


def _assess_wrapped(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """A wrapper judged by the command it runs, its options and their values passed over: ``env`` in the directory of
    its ``-C`` and with the words of its ``-S``, ``timeout`` past its duration, ``watch`` on the line it repeats."""
    options, rest = _options(args, _WRAPPER_VALUES.get(command, frozenset()))
    if command == "env":
        places: list[str] = [value for name, value in options if name in ("-C", "--chdir")]
        here: ShellScope = _with_places(scope, _places(places[-1], scope)) if places else scope
        words: list[str] = rest[1:] if rest[:1] == ["-"] else rest
        split: list[str] = [value for name, value in options if name in ("-S", "--split-string")]
        if not split:
            return _assess_simple(words, here, given)
        parts: list[str] | None = _split_words(split[-1])
        if parts is None:
            return [_unknown("env -S is given a string that cannot be parsed")]
        return _assess_simple([*parts, *words], here, given)
    if command in _WRAPPER_SKIPS:
        return _assess_simple(rest[_WRAPPER_SKIPS[command] :], scope, given)
    if command == "watch":
        return _assess_all(" ".join(rest), scope)
    if command == "command" and any(name in ("-v", "-V") for name, _ in options):
        return [_finding("safe", (), "command -v")]
    if command == "xcrun" and any(name in ("-f", "--find") for name, _ in options):
        return [_finding("safe", (), "xcrun --find")]
    return _assess_simple(rest, scope, given)


def _assignment_findings(word: str, scope: ShellScope) -> list[Finding]:
    """What the assignment ``word`` (``NAME=value``) sets up for the programs it reaches: a command line one of them
    runs, judged now, or code one of them loads, which cannot be read; an empty value loads nothing."""
    assignment: re.Match[str] | None = _ENV_ASSIGNMENT.fullmatch(word)
    if assignment is None:
        return []
    name, value = assignment.group(1), assignment.group(2)
    if name in _COMMAND_VARIABLES:
        return _assess_all(value, scope)
    if name in _LOADING_VARIABLES and value:
        return [_unknown(f"sets {name}, which makes programs load or run what it names")]
    return []


def _assess_trap(args: list[str], scope: ShellScope) -> list[Finding]:
    """``trap`` keeps its first operand as a command line that runs later (on a signal, or when the shell exits), which
    is judged now; ``-l`` and ``-p`` only print, and ``-`` resets."""
    operands: list[str] = args[1:] if args[:1] == ["--"] else args
    if not operands or operands[0] in ("-l", "-p", "-", ""):
        return [_finding("safe", (), "trap")]
    return _assess_all(operands[0], scope)


def _runner_starts(args: list[str], own: int | None) -> list[int]:
    """Where in ``args``, the words a runner is given, the command it runs may begin. An operand right after an option
    may be that option's value (one not in :data:`_RUNNER_SWITCHES`, without ``=``), so the command may begin there
    or at a later operand; one after another operand, or first, is where it begins, and the words after it are its
    own, as is the word after ``--``. ``own`` operands of the runner come first; None stands for any number, each of
    which may be the command too."""
    starts: list[int] = []
    left: int = own or 0
    for index, word in enumerate(args):
        if word == "--":
            return [*starts, index + 1] if index + 1 < len(args) else starts
        if word.startswith("-"):
            continue
        if left:
            left -= 1
            continue
        starts.append(index)
        before: str = args[index - 1] if index else ""
        valued: bool = before.startswith("-") and "=" not in before and before not in _RUNNER_SWITCHES
        if own is not None and not valued:
            return starts
    return starts


def _assess_runner(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding] | None:
    """A tool of :data:`_RUNNERS` judged by the command it runs (:func:`_runner_starts`), after its own word where a
    command would stand (``uv --directory x run``); None when it is not running one (``uv sync``, ``npm test``)."""
    words: frozenset[str] = _RUNNERS[command]
    rest: list[str] = args
    if words:
        at: int | None = next((index for index in _runner_starts(args, 0) if args[index] in words), None)
        if at is None:
            return None
        rest = args[at + 1 :]
    starts: list[int] = _runner_starts(rest, _RUNNER_OPERANDS.get(command, 0))
    readings: list[Finding] = [found for index in starts for found in _assess_simple(rest[index:], scope, given)]
    return [_finding("low", (), f"runs {command}"), *readings]


def _assess_simple(words: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """Judge one simple command (words without operators or redirects). Its reserved words, assignments and wrappers
    are looked through to the command they run, and a command named by a variable is judged under each name the line
    gave the variable; one named by what only the running line knows cannot be judged."""
    plain: list[str] = _plain(words)
    if not plain or plain[0] in _HEADERS:
        return []
    head: str = plain[0]
    if _ENV_ASSIGNMENT.fullmatch(head):
        return [*_assignment_findings(head, scope), *_assess_simple(plain[1:], scope, given)]
    if len(head) > 1 and head.startswith("="):
        # zsh puts the path of the command in place of ``=rm``.
        return _assess_simple([head[1:], *plain[1:]], scope, given)
    names: list[str] | None = _expansions(head, scope)
    if names is None:
        return [_unknown("runs a command whose name is only known when the line runs")]
    if names != [head]:
        return [found for name in names for found in _assess_simple([*name.split(), *plain[1:]], scope, given)]
    if head not in _TEST_COMMANDS and any(char in head for char in _GLOB_CHARS):
        return [_unknown("runs a command whose name is a pattern the shell matches against file names")]
    command: str = head if head == "." else _GNU_NAMES.get(Path(head).name, Path(head).name)
    named: list[Finding] = [
        *_assess_named(command, plain, scope, given),
        *_walks_over_protected(command, plain[1:], scope),
    ]
    if given.walked and _reads_a_name_list(command, plain[1:]) and _holds_protected(given.walked, scope):
        named.append(
            _maybe_protected(
                "reads the names of the files a search lists under a folder that holds a protected location"
            )
        )
    moves: list[ShellScope] | None = _moved_scopes(plain[1:], scope)
    if moves is None:
        named.append(
            _maybe_protected("moves between more folders than the guard reads, one of which may be a protected one")
        )
    for moved in moves or []:
        # The words it names are read from the folder its option moves it to (item 531: ``env -C ~/Library cat
        # Safari/History.db``, ``git -C ~ diff --no-index .ssh/id_rsa /dev/null``), where the line, judged from the
        # folder it is in, does not show them.
        named.extend((*_protected_words(plain[1:], moved), *_walks_over_protected(command, plain[1:], moved)))
    if "/" in head and head != command:
        # A program named by its path runs as itself: one the line downloaded or decoded counts as that run (items 385,
        # 528). What the program is is still read: a wrapper or a privilege command at that path still runs its command.
        return [*_runs_download(head, scope), *named]
    return named


def _assess_named(command: str, plain: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """The command ``plain`` runs, named ``command``: a declarer, a privilege command, a wrapper (all looked through to
    what they run) or any other."""
    if command in _DECLARERS:
        declared: list[Finding] = [found for word in plain[1:] for found in _assignment_findings(word, scope)]
        return [*declared, *_assess_command(command, plain[1:], scope, given)]
    if command in _PRIVILEGE_WRAPPERS:
        return _assess_privileged(command, plain[1:], scope, given)
    if command in _WRAPPERS:
        return _assess_wrapped(command, plain[1:], scope, given)
    return _assess_command(command, plain[1:], scope, given)


def _assess_segment(
    segment: list[str], scope: ShellScope, piped: _Input, documents: tuple[str, ...]
) -> tuple[list[Finding], ShellScope]:
    """Judge one simple command of the line as written (redirects included), and the scope it leaves."""
    words, targets, emptied, text = _split_redirects(segment, documents)
    given: _Input = piped if text is None else _given_text(text)
    plain: list[str] = _plain(words)
    moves: bool = bool(plain) and plain[0] in _MOVES
    findings: list[Finding] = [
        *_protected_words([word for word in segment if not _is_redirect(word)], scope, climbs=not moves),
        *_assess_simple(words, scope, given),
        *_assess_redirects(targets, scope),
        *_replacing(emptied, [], scope),
        *_changing(_known_paths(targets, scope)),
    ]
    after: ShellScope = _next_scope(words, scope)
    decoded: tuple[Path, ...] = _decoded_writes(words, targets, scope)
    if decoded:
        after = replace(after, decoded=(*after.decoded, *decoded))
    return findings, after


def _universal_findings(command: str, scope: ShellScope) -> list[Finding]:
    findings: list[Finding] = []
    if mentions_protected(command, scope.protected):
        findings.append(_finding("high", ("secret_access",), "mentions a protected location"))
    if _PIPE_TO_SHELL.search(command):
        findings.append(_fetching("high", ("install",), "executes code downloaded from the network"))
    if _FORK_BOMB.search(command):
        findings.append(_finding("high", ("destructive",), "looks like a fork bomb"))
    if _DEVICE_WRITE.search(command):
        findings.append(_finding("high", ("destructive", "system_settings"), "writes to a raw disk device"))
    if _EXPANDS_AS_CODE.search(command):
        findings.append(_unknown("expands a variable as code"))
    return findings


def _assess_all(command: str, scope: ShellScope) -> list[Finding]:
    """Judge a whole line: what it says anywhere (:func:`_universal_findings`), each simple command in order with the
    scope the commands before it leave and the input the pipe gives it, then the commands substituted into it, with
    the scope the line ends in (which holds every directory, value and link the line may have had)."""
    if scope.depth > _DEEPEST:
        return [_unknown("nests command strings too deeply to judge")]
    reading: _Reading = _read(command)
    findings: list[Finding] = _universal_findings(command, scope)
    if reading.hidden:
        findings.append(_unknown("runs code from a zsh glob qualifier"))
    if reading.unclosed:
        findings.append(_unknown("a command substitution never closes, so where it ends cannot be told"))
    tokens: list[str] | None = _tokenize(reading.text)
    if tokens is None:
        return [*findings, _unknown("the line cannot be parsed, so what it runs cannot be judged")]
    state: ShellScope = replace(scope, depth=scope.depth + 1)
    stages: list[list[str]] = []
    for operator, segment in _commands(_without_patterns(tokens)):
        stages = [*stages, segment] if operator in ("|", "|&") else [segment]
        found, state = _assess_segment(segment, state, _piped(stages[:-1], reading.documents), reading.documents)
        findings.extend(found)
        findings.extend(_fed_a_download(stages, state))
        findings.extend(_fed_a_decode(stages, state))
    for inner in reading.substituted:
        findings.extend(_assess_all(inner, state))
        findings.extend(_substituted_listing(inner, state))
    return findings


def _merge(findings: list[Finding]) -> ShellAssessment:
    changes: tuple[str, ...] = tuple(dict.fromkeys(path for finding in findings for path in finding.changes))
    judged: list[Finding] = [finding for finding in findings if finding.reason != _CHANGES]
    if not judged:
        return ShellAssessment(risk="low", flags=(), reasons=("empty command",), fetches=False, changes=changes)
    risk: Risk = max((finding.risk for finding in judged), key=lambda value: _RISK_ORDER[value])
    flags: list[Flag] = []
    for finding in judged:
        flags.extend(finding.flags)
    reasons: list[str] = [finding.reason for finding in judged if finding.risk != "safe"]
    return ShellAssessment(
        risk=risk,
        flags=tuple(dict.fromkeys(flags)),
        reasons=tuple(dict.fromkeys(reasons)) or ("read-only command",),
        fetches=any(finding.fetches for finding in judged),
        changes=changes,
    )


def _scope(cwd: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> ShellScope:
    return ShellScope(
        cwds=(cwd,),
        workspace=workspace,
        protected=protected,
        dot_paths=dot_paths,
        links=(),
        variables={},
        depth=0,
        downloaded=(),
        decoded=(),
    )


def assess_shell(
    command: str, cwd: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> ShellAssessment:
    """Classify ``command`` (a full shell line) as it would run in ``cwd``."""
    return _merge(_assess_all(command, _scope(cwd, workspace, protected, dot_paths)))


def typed_lines(pending: str | None, text: str) -> tuple[tuple[str | None, ...], str | None]:
    """The lines typing ``text`` at a terminal submits, the first one starting with ``pending`` (what sits on the line
    since it was last empty), and what is left on the line after it; ``None`` stands for a line that cannot be told.

    Enter submits the line, Delete takes back its last character, Ctrl-C drops it and Ctrl-U kills it back to the
    cursor, which is its end while the line is known; Tab completes a word, so the line can no longer be told. Any
    other control key (Escape and the arrows it starts, Ctrl-D, Ctrl-O, another Ctrl- shortcut) may move the cursor,
    recall history or submit the line, so it counts as submitting a line that cannot be told. An unknown line stays
    so until Enter or Ctrl-C."""
    lines: list[str | None] = []
    line: str | None = pending
    for char in text.replace("\r\n", "\n"):
        if char in _SUBMITS:
            lines.append(line)
            line = ""
        elif char == _DROPS_LINE:
            line = ""
        elif char in (_KILLS_LINE, _ERASES_CHAR):
            line = None if line is None else "" if char == _KILLS_LINE else line[:-1]
        elif char == _COMPLETES:
            line = None
        elif unicodedata.category(char) == "Cc":
            lines.append(None)
            line = None
        elif line is not None:
            line += char
    return tuple(lines), line


def assess_typed(
    lines: Sequence[str | None], cwd: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> ShellAssessment:
    """``lines`` submitted at a terminal (as :func:`typed_lines` gives them), judged as one script run in ``cwd``; a
    line that cannot be told runs code that cannot be judged."""
    findings: list[Finding] = _assess_all(
        "\n".join(line for line in lines if line is not None), _scope(cwd, workspace, protected, dot_paths)
    )
    if None in lines:
        findings.append(
            _unknown("the terminal line holds text Dot did not type or keys that edit it, so what it runs is unknown")
        )
    return _merge(findings)


def assess_applescript(
    script: str, cwd: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> ShellAssessment:
    """Classify an AppleScript or JXA ``script``; the shell commands it spells out are judged like shell lines."""
    return _merge(_applescript_findings(script, _scope(cwd, workspace, protected, dot_paths)))


def script_reaches_shell(script: str) -> bool:
    """Whether ``script`` can run shell commands, directly or by typing them into a terminal."""
    return _AS_SHELL.search(script) is not None
