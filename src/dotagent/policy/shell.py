"""Risk assessment for shell commands: a conservative guardrail against accidents, not a sandbox.

A line is read the way the shell reads it before anything runs (:func:`_read`): comments, quotes, here-documents and
the commands substituted into it, each of which is judged as a command of its own. Its simple commands are then judged
in order, each with what the line set up before it (:class:`ShellScope`): the directories a ``cd`` may have moved it
to, the values it gave its variables and the symbolic links it made. What only the running line knows is judged at its
worst: such a path counts as one outside the workspace, and such code (a command named by a substitution, ``eval`` of
one, a line that cannot be parsed) as a destructive step outside it, which every preset asks about. Neither what a
command does with the files it walks (a recursive copy of the home folder) nor the code of a script it runs is
followed."""

import os
import re
import shlex
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from dotagent.core.types import FrozenModel
from dotagent.policy.paths import (
    CHANGES_DOT,
    PathClass,
    classify_path,
    expand_path,
    glob_reaches_protected,
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
_SHELL_OPERATOR: re.Pattern[str] = re.compile(r";;&|;;|;&|;\||&&|\|\||\|&|&>>|&>|&\||>>|>&|>\||<&|<<<|<<|<>|[;&|()<>]")
_OPERATOR_CHARS: str = ";&|()<>"
# A quoted or escaped operator character is a letter of its word: the reader writes it as one of these stand-ins, which
# the tokenizer takes for a letter, and the words get the character back (:func:`_commands`).
_LITERAL_OPERATORS: str = ""
_HIDE_OPERATORS: dict[int, int] = str.maketrans(_OPERATOR_CHARS, _LITERAL_OPERATORS)
_SHOW_OPERATORS: dict[int, int] = str.maketrans(_LITERAL_OPERATORS, _OPERATOR_CHARS)
_WORD_BREAKS: str = " \t\n;&|()<>"
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
_RUNTIME_VARIABLES: frozenset[str] = frozenset({"_", "OLDPWD", "REPLY"})
_BRACES: re.Pattern[str] = re.compile(r"\{([^{},]*(?:,[^{},]*)+)\}")
_NAME: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_ASSIGNMENT: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_GLOB_CHARS: str = "*?["
_MOST_VALUES: int = 64
_MOST_PLACES: int = 16
_DEEPEST: int = 12
_QUIET_TARGETS: frozenset[str] = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})

# Reserved words in front of a command, and the heads of compound commands that run nothing themselves.
_KEYWORDS: frozenset[str] = frozenset(
    {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "esac", "!", "time", "coproc", "always"}
)
_HEADERS: frozenset[str] = frozenset({"for", "select", "case", "foreach"})
_DECLARERS: frozenset[str] = frozenset({"export", "local", "declare", "typeset", "readonly", "integer", "float"})

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
    }
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
_SHELL_VALUES: frozenset[str] = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_INTERPRETERS: frozenset[str] = frozenset(
    {"python", "python3", "node", "ruby", "perl", "php", "swift", "deno", "bun", "lua", "Rscript", "julia"}
)
_INLINE_CODE: frozenset[str] = frozenset({"-c", "-e", "-E", "--eval", "-p"})
# Code that reaches the network: a URL, or the client libraries and calls of the interpreters above.
_NETWORK_CODE: re.Pattern[str] = re.compile(
    r"\w+://|urllib|\brequests\b|http\.client|httpx|aiohttp|\bsocket\b|\bfetch\s*\(|XMLHttpRequest|axios|"
    r"\bnet/http\b|Net::HTTP|LWP::|HTTP::Tiny|open-uri|URLSession|curl_exec|file_get_contents|fsockopen",
    re.IGNORECASE,
)
_NETWORK_FETCHERS: frozenset[str] = frozenset({"curl", "wget", "http", "https", "xh", "aria2c"})
_REMOTE_COPY: frozenset[str] = frozenset({"scp", "sftp", "rsync", "nc", "ncat", "socat", "telnet", "ftp", "ssh"})
# A path of another machine as scp and rsync take one: ``host:path``, ``user@host:path``, ``host::module``, ``rsync://``.
_REMOTE_PATH: re.Pattern[str] = re.compile(r"[^/]*:")
_TUNNELS: frozenset[str] = frozenset({"ngrok", "cloudflared", "localtunnel", "lt", "bore", "serveo"})
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

_PIPE_TO_SHELL: re.Pattern[str] = re.compile(
    r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(bash|sh|zsh|python3?|ruby|perl|node)\b|"
    r"\b(bash|sh|zsh)\s+<\(\s*(curl|wget)\b|\beval\s+\"?\$\(\s*(curl|wget)\b"
)
_FORK_BOMB: re.Pattern[str] = re.compile(r":\(\)\s*\{")
_DEVICE_WRITE: re.Pattern[str] = re.compile(r"(>\s*|of=)/dev/(r?disk|sd|nvme)")
_UPLOAD_FLAGS: frozenset[str] = frozenset(
    {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form", "-T", "--upload-file"}
)
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
    talks to a remote machine, or is code the guard cannot read), so its output is untrusted."""

    risk: Risk
    flags: tuple[Flag, ...]
    reason: str
    fetches: bool


class ShellAssessment(FrozenModel):
    risk: Risk
    flags: tuple[Flag, ...]
    reasons: tuple[str, ...]
    fetches: bool


@dataclass(frozen=True)
class ShellScope:
    """What a command is judged in: where the line may be, and what it set up before the command (item 116).

    ``cwds`` are the directories the line may be in: the one it starts in and each one a ``cd`` may have moved it to (a
    ``cd`` can fail or run in a subshell, so the earlier ones stay). ``links`` are the symbolic links the line made,
    ``(link, target)``, so that a path through one is judged where it lands; ``variables`` hold every value the line
    gave each variable; ``depth`` counts the command strings the command is nested in (``bash -c``, ``$(...)``)."""

    cwds: tuple[Path, ...]
    workspace: Path
    protected: tuple[Path, ...]
    dot_paths: tuple[Path, ...]
    links: tuple[tuple[Path, Path], ...]
    variables: Mapping[str, tuple[str, ...]]
    depth: int


@dataclass(frozen=True)
class _Reading:
    """A line as the tokenizer is to read it (``text``), the commands substituted into it, the bodies of its
    here-documents (``text`` names each by a ``_DOCUMENT`` word after its ``<<``), and the code it runs that cannot be
    read (zsh glob qualifiers that run code)."""

    text: str
    substituted: tuple[str, ...]
    documents: tuple[str, ...]
    hidden: tuple[str, ...]


@dataclass(frozen=True)
class _Input:
    """What a command reads on its standard input, as far as the line shows it: the text (a here-document, a
    here-string, what an ``echo`` pipes in) and the paths it names (``find ... |``); an unknown path is
    ``_SUBSTITUTED``."""

    text: str | None
    paths: tuple[str, ...]


_NO_INPUT: _Input = _Input(text=None, paths=(_SUBSTITUTED,))


def _finding(risk: Risk, flags: tuple[Flag, ...], reason: str) -> Finding:
    return Finding(risk=risk, flags=flags, reason=reason, fetches=False)


def _fetching(risk: Risk, flags: tuple[Flag, ...], reason: str) -> Finding:
    return Finding(risk=risk, flags=flags, reason=reason, fetches=True)


def _unknown(reason: str) -> Finding:
    """Code the guard cannot read, judged as the worst it could be: a destructive step outside the workspace, which
    every preset asks about, that may fetch anything."""
    return _fetching("high", ("destructive", "outside_workspace"), reason)


def _given_text(text: str) -> _Input:
    return _Input(text=text, paths=tuple(text.split()) or (_SUBSTITUTED,))


# ---------- reading the line ----------


def _closing_paren(text: str, start: int) -> int:
    """The index of the ``)`` that closes a ``(`` opened just before ``start``, past quotes and nested parentheses;
    the end of ``text`` when it never closes."""
    depth: int = 1
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
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
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
            index = end + 1
        elif char == "`":
            end = _closing_backtick(command, index + 1)
            body = command[index + 1 : end]
            substituted.append(body)
            out.append(_TEMP_STAND_IN if _MKTEMP.fullmatch(body) else _SUBSTITUTED)
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
    return _Reading(text="".join(out), substituted=tuple(substituted), documents=tuple(documents), hidden=tuple(hidden))


def _tokenize(text: str) -> list[str] | None:
    """The words and operators of ``text`` (as :func:`_read` gives it), None when its quotes do not close. A run of
    operator characters is split into the shell's operators (``);`` is ``)`` and ``;``)."""
    lexer: shlex.shlex = shlex.shlex(text, posix=True, punctuation_chars=True)
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


def _split_redirects(words: list[str], documents: tuple[str, ...]) -> tuple[list[str], list[str], str | None]:
    """Separate ``(words, output redirect targets, the text given on standard input)``: the body of a here-document
    (named by the ``_DOCUMENT`` word :func:`_read` put after its ``<<``) or the word of a here-string. Descriptor
    targets such as ``&1`` are ignored."""
    plain: list[str] = []
    targets: list[str] = []
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
            index += 2
            continue
        if token.isdigit() and index + 1 < len(words) and _is_redirect(words[index + 1]):
            index += 1
            continue
        plain.append(token)
        index += 1
    return plain, targets, text


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
        current = Path(os.path.realpath(f"{target}{str(current)[len(str(link)) :]}"))
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
    classes: list[PathClass] | None = _path_classes(raw, scope)
    return classes is not None and "protected" in classes


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
        if _ENV_ASSIGNMENT.fullmatch(word):
            name, _, value = word.partition("=")
            current = _assigned(current, name, [value])
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
    rest: list[str] = plain[count:]
    command: str = Path(rest[0]).name if rest else ""
    if command in ("cd", "pushd"):
        operands: list[str] = _positional(rest[1:])
        wanted: bool = len(operands) == 1 and operands[0] != "-"
        return _with_places(assigned, [*assigned.cwds, *(_places(operands[0], assigned) if wanted else [Path.home()])])
    if command == "ln":
        return _linked(assigned, rest[1:])
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
            given = _Input(text=None, paths=tuple(operands) or (".",))
        elif command not in _FILTERS:
            given = _NO_INPUT
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


def _assess_writes(command: str, args: list[str], scope: ShellScope) -> Finding:
    targets: list[str] = [target for target in _positional(args) if not target.isdigit()]
    if command == "chmod" or command == "chown" or command == "chgrp":
        targets = targets[1:]
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
    if command == "truncate":
        return _finding(
            "high", ("destructive", *settings, "outside_workspace"), "truncate cuts files outside the workspace"
        )
    if worst == "system":
        return _finding("high", (*settings, "outside_workspace"), f"{command} modifies a system location")
    return _finding("medium", ("outside_workspace",), f"{command} modifies files outside the workspace")


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


def _protected_words(words: list[str], scope: ShellScope) -> list[Finding]:
    """A finding when a word names a protected location: as a path (a glob that can reach one, a path through a link
    the line made or a variable it set), or as the value of an assignment or an option (``K=~/.ssh``,
    ``--key=~/.ssh/id``)."""
    candidates: list[str] = [part for word in words for part in (word, word.partition("=")[2]) if part]
    if any(_reaches_protected(candidate, scope) for candidate in candidates):
        return [_finding("high", ("secret_access",), "names a protected location")]
    return []


_GIT_GLOBAL_WITH_VALUE: frozenset[str] = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
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


def _assess_git(args: list[str], scope: ShellScope) -> Finding:
    sub, rest = _git_subcommand(args)
    where: ShellScope = _git_scope(args, rest, scope)
    outside: tuple[Flag, ...] = () if _cwd_class(where) in ("workspace", "temp") else ("outside_workspace",)
    destructive_args: set[str] = {"--hard", "--force", "-f", "-fd", "-fdx", "-df", "-D", "--delete", "drop", "clear"}
    if sub == "push":
        if any(arg in ("--force", "-f", "--force-with-lease", "--delete", "--mirror") for arg in rest):
            return _finding("high", ("destructive", "external_send", *outside), "force-pushes or deletes remote refs")
        return _finding("medium", ("upload",), "pushes commits to a remote")
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


def _cloned_into(args: list[str], scope: ShellScope) -> list[Finding]:
    """What ``git clone`` writes: the folder it is given, else a folder in the directory the line is in."""
    sub, rest = _git_subcommand(args)
    if sub != "clone":
        return []
    operands: list[str] = []
    index: int = 0
    while index < len(rest):
        if rest[index] in _GIT_CLONE_VALUES:
            index += 2
            continue
        if not rest[index].startswith("-"):
            operands.append(rest[index])
        index += 1
    return _writes_to("git clone", operands[1:2] or ["."], _git_scope(args, rest, scope))


def _assess_network_fetcher(command: str, args: list[str]) -> Finding:
    uploads: bool = any(arg in _UPLOAD_FLAGS or arg.startswith("--data") for arg in args)
    method: bool = any(
        arg.upper() in ("POST", "PUT", "DELETE", "PATCH") for arg in args if not arg.startswith("-") and arg.isalpha()
    )
    if uploads or method:
        return _fetching("medium", ("upload",), f"{command} sends data to a remote server")
    return _fetching("low", (), f"{command} fetches from a remote server")


def _fetched_files(command: str, args: list[str]) -> list[str]:
    """Where a fetcher saves what it fetched: the ``-o`` files of curl and the others, the folder of curl's ``-O`` (its
    ``--output-dir``, else the directory the line is in), and wget's ``-O`` file or else its ``-P`` folder or the
    directory the line is in (wget's ``-o`` is its log); ``-`` is the standard output."""
    files: list[str] = _option_values(args, "-o", "--output")
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


def _assess_interpreter(command: str, args: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """An interpreter runs code the guard does not read. Its inline code (``-c``, ``-e``, a here-document or a pipe it
    reads as its program) that names the network counts as fetching and sending, and code that names one of Dot's own
    folders as changing it."""
    operands: list[str] = _positional(args)
    inline: list[str] = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg in _INLINE_CODE]
    if given.text is not None and (not operands or operands[0] == "-") and not inline:
        inline.append(given.text)
    code: str = "\n".join(inline)
    findings: list[Finding] = [
        _finding("medium" if inline else "low", (), f"{command} runs {'inline code' if inline else 'a script'}")
    ]
    if _NETWORK_CODE.search(code):
        findings.append(_fetching("medium", ("upload",), f"{command} runs code that reaches the network"))
    if mentions_protected(code, scope.dot_paths):
        findings.append(_finding("high", ("secret_access",), f"{command} runs code that {CHANGES_DOT}"))
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
    """A shell given ``-c`` is judged by its command string; one given no script, by the text it reads when the line
    shows it (a here-document, ``echo ... |``)."""
    inline, operands, reads_input = _shell_arguments(args)
    if inline:
        return _assess_all(operands[0], scope) if operands else []
    if (reads_input or not operands) and given.text is not None:
        return _assess_all(given.text, scope)
    return [_finding("low", (), f"runs {command}")]


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
    return [found for run in runs for found in _assess_simple(run, scope, _NO_INPUT)]


def _runs_with(command: list[str], roots: list[str], scope: ShellScope, placeholder: re.Pattern[str]) -> list[Finding]:
    """A command that ``find`` or ``fd`` runs on what it finds, judged on the roots it searches: put in for its
    placeholder, or after it."""
    if any(placeholder.search(word) for word in command):
        runs: list[list[str]] = [[placeholder.sub(root, word) for word in command] for root in roots]
    else:
        runs = [[*command, *roots]]
    return [found for run in runs for found in _assess_simple(run, scope, _NO_INPUT)]


def _assess_find(args: list[str], scope: ShellScope) -> list[Finding]:
    """find judged on every root it searches: ``-delete`` removes under them, ``-exec`` (and ``-execdir``, ``-ok``)
    runs its command on them, ``-fprint`` and ``-fls`` write a file."""
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
    searched: list[str] = roots or ["."]
    expression: list[str] = args[index:]
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


def _assess_sed(args: list[str], scope: ShellScope) -> list[Finding]:
    """sed judged on the files it writes: in place (``-i``), or with its ``w`` command or flag; and on the protected
    files its ``r`` command reads."""
    findings: list[Finding] = []
    if any(arg.startswith(("-i", "--in-place")) for arg in args):
        findings.append(_assess_writes("sed", [arg for arg in args if arg != ""], scope))
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
        if any(target.endswith(_OPEN_RUNS) for target in opened):
            return [_finding("medium", (), "opens a script that runs as it opens")]
        return [_finding("safe", (), "open")]
    return None


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
        flags.append("external_send")
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
    if command == "xargs":
        return _assess_xargs(args, scope, given)
    if command in ("rm", "rmdir", "unlink", "shred", "srm"):
        return [_assess_delete(args, scope)]
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
    if command in _TUNNELS:
        return [_finding("high", ("external_send",), f"{command} exposes this machine to the internet")]
    if command == "osascript":
        return _assess_osascript(args, scope, given)
    if command == "security":
        return [_finding("high", ("secret_access",), "uses the macOS keychain command line")]
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
        return [_finding("medium", (), "dd copies raw data")]
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
        return [_assess_writes(command, args, scope)]
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
    if command == "timeout":
        return _assess_simple(rest[1:], scope, given)
    if command == "watch":
        return _assess_all(" ".join(rest), scope)
    if command == "command" and any(name in ("-v", "-V") for name, _ in options):
        return [_finding("safe", (), "command -v")]
    return _assess_simple(rest, scope, given)


def _assess_simple(words: list[str], scope: ShellScope, given: _Input) -> list[Finding]:
    """Judge one simple command (words without operators or redirects). Its reserved words, assignments and wrappers
    are looked through to the command they run, and a command named by a variable is judged under each name the line
    gave the variable; one named by what only the running line knows cannot be judged."""
    plain: list[str] = _plain(words)
    if not plain or plain[0] in _HEADERS:
        return []
    head: str = plain[0]
    if _ENV_ASSIGNMENT.fullmatch(head):
        return _assess_simple(plain[1:], scope, given)
    names: list[str] | None = _expansions(head, scope)
    if names is None:
        return [_unknown("runs a command whose name is only known when the line runs")]
    if names != [head]:
        return [found for name in names for found in _assess_simple([*name.split(), *plain[1:]], scope, given)]
    command: str = Path(head).name
    if command in _PRIVILEGE_WRAPPERS:
        return _assess_privileged(command, plain[1:], scope, given)
    if command in _WRAPPERS:
        return _assess_wrapped(command, plain[1:], scope, given)
    return _assess_command(command, plain[1:], scope, given)


def _assess_segment(
    segment: list[str], scope: ShellScope, piped: _Input, documents: tuple[str, ...]
) -> tuple[list[Finding], ShellScope]:
    """Judge one simple command of the line as written (redirects included), and the scope it leaves."""
    words, targets, text = _split_redirects(segment, documents)
    given: _Input = piped if text is None else _given_text(text)
    findings: list[Finding] = [
        *_protected_words([word for word in segment if not _is_redirect(word)], scope),
        *_assess_simple(words, scope, given),
        *_assess_redirects(targets, scope),
    ]
    return findings, _next_scope(words, scope)


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
    tokens: list[str] | None = _tokenize(reading.text)
    if tokens is None:
        return [*findings, _unknown("the line cannot be parsed, so what it runs cannot be judged")]
    state: ShellScope = replace(scope, depth=scope.depth + 1)
    stages: list[list[str]] = []
    for operator, segment in _commands(tokens):
        stages = [*stages, segment] if operator in ("|", "|&") else [segment]
        found, state = _assess_segment(segment, state, _piped(stages[:-1], reading.documents), reading.documents)
        findings.extend(found)
    for inner in reading.substituted:
        findings.extend(_assess_all(inner, state))
    return findings


def _merge(findings: list[Finding]) -> ShellAssessment:
    if not findings:
        return ShellAssessment(risk="low", flags=(), reasons=("empty command",), fetches=False)
    risk: Risk = max((finding.risk for finding in findings), key=lambda value: _RISK_ORDER[value])
    flags: list[Flag] = []
    for finding in findings:
        flags.extend(finding.flags)
    reasons: list[str] = [finding.reason for finding in findings if finding.risk != "safe"]
    return ShellAssessment(
        risk=risk,
        flags=tuple(dict.fromkeys(flags)),
        reasons=tuple(dict.fromkeys(reasons)) or ("read-only command",),
        fetches=any(finding.fetches for finding in findings),
    )


def _scope(cwd: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> ShellScope:
    return ShellScope(
        cwds=(cwd,), workspace=workspace, protected=protected, dot_paths=dot_paths, links=(), variables={}, depth=0
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
