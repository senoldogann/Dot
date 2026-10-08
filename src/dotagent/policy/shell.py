"""Risk assessment for shell commands: a conservative guardrail against accidents, not a sandbox."""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from dotagent.core.types import FrozenModel
from dotagent.policy.paths import PathClass, classify_path, expand_path, mentions_protected
from dotagent.policy.types import Flag, Risk

_RISK_ORDER: dict[Risk, int] = {"safe": 0, "low": 1, "medium": 2, "high": 3}

_OPERATORS: frozenset[str] = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")", ";;"})
_ENV_ASSIGNMENT: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_DYNAMIC: re.Pattern[str] = re.compile(r"\$\(|`")
_GLOB_CHARS: str = "*?["

_TRANSPARENT_WRAPPERS: frozenset[str] = frozenset(
    {"env", "command", "exec", "time", "nohup", "nice", "ionice", "caffeinate", "timeout", "stdbuf", "builtin", "arch"}
)
_PRIVILEGE_WRAPPERS: frozenset[str] = frozenset({"sudo", "doas", "su", "pkexec"})
_SHELLS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
_INTERPRETERS: frozenset[str] = frozenset(
    {"python", "python3", "node", "ruby", "perl", "php", "swift", "deno", "bun", "lua", "Rscript", "julia"}
)
_NETWORK_FETCHERS: frozenset[str] = frozenset({"curl", "wget", "http", "https", "xh", "aria2c"})
_REMOTE_COPY: frozenset[str] = frozenset({"scp", "sftp", "rsync", "nc", "ncat", "socat", "telnet", "ftp", "ssh"})
_TUNNELS: frozenset[str] = frozenset({"ngrok", "cloudflared", "localtunnel", "lt", "bore", "serveo"})
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
        "export",
        "set",
        "unset",
        "alias",
        "wait",
        "xargs",
        "open",
        "say",
        "afplay",
    }
)

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
_AS_SHELL: re.Pattern[str] = re.compile(
    r"\bdo\s+shell\s+script\b|\bdoShellScript\s*\(|\bdo\s+script\b|\bwrite\s+text\b", re.IGNORECASE
)
_AS_KEYSTROKES: re.Pattern[str] = re.compile(r"\bkeystroke\b|\bkey\s+code\b", re.IGNORECASE)
_AS_NATIVE: re.Pattern[str] = re.compile(r"\bObjC\.import\b|\$\.NS[A-Z]|\bNSTask\b|\bNSAppleScript\b")
_AS_LITERAL: re.Pattern[str] = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
_AS_GLUE: re.Pattern[str] = re.compile(r"with\s+administrator\s+privileges|[\s&()+,;¬]", re.IGNORECASE)
_AS_ESCAPE: re.Pattern[str] = re.compile(r"\\(.)")
_AS_ESCAPES: dict[str, str] = {"n": "\n", "r": "\n", "t": "\t"}
_AS_CONTINUATIONS: tuple[str, ...] = ("¬", "&", "+", "(", ",")


class Finding(FrozenModel):
    risk: Risk
    flags: tuple[Flag, ...]
    reason: str


class ShellAssessment(FrozenModel):
    risk: Risk
    flags: tuple[Flag, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ShellScope:
    """Where a command runs and which locations matter to the assessment."""

    cwd: Path
    workspace: Path
    protected: tuple[Path, ...]


def _finding(risk: Risk, flags: tuple[Flag, ...], reason: str) -> Finding:
    return Finding(risk=risk, flags=flags, reason=reason)


def _split_unquoted_newlines(command: str) -> str:
    """Replace newlines outside quotes with ``;`` so each line becomes its own command."""
    out: list[str] = []
    quote: str | None = None
    escaped: bool = False
    for char in command:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            out.append(char)
            escaped = True
        elif quote is not None:
            out.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            out.append(char)
            quote = char
        elif char == "\n":
            out.append(" ; ")
        else:
            out.append(char)
    return "".join(out)


def _tokenize(command: str) -> list[str] | None:
    lexer: shlex.shlex = shlex.shlex(_split_unquoted_newlines(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _OPERATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _is_redirect(token: str) -> bool:
    return token != "" and all(char in "<>&|" for char in token) and token not in _OPERATORS


def _split_redirects(words: list[str]) -> tuple[list[str], list[str]]:
    """Separate ``(words, output redirect targets)``; descriptor targets such as ``&1`` are ignored."""
    plain: list[str] = []
    targets: list[str] = []
    index: int = 0
    while index < len(words):
        token: str = words[index]
        if _is_redirect(token):
            if ">" in token and index + 1 < len(words):
                target: str = words[index + 1]
                if not target.isdigit() and target != "-" and target != "/dev/null":
                    targets.append(target)
            index += 2
            continue
        if token.isdigit() and index + 1 < len(words) and _is_redirect(words[index + 1]):
            index += 1
            continue
        plain.append(token)
        index += 1
    return plain, targets


def _strip_wrappers(words: list[str]) -> tuple[list[str], list[Finding]]:
    """Drop env assignments and transparent wrappers; report privilege wrappers."""
    findings: list[Finding] = []
    remaining: list[str] = list(words)
    while remaining:
        head: str = remaining[0]
        if _ENV_ASSIGNMENT.fullmatch(head):
            remaining = remaining[1:]
        elif head in _PRIVILEGE_WRAPPERS:
            findings.append(_finding("high", ("privilege",), f"runs with elevated privileges via {head}"))
            remaining = remaining[1:]
            while remaining and remaining[0].startswith("-"):
                option: str = remaining[0]
                remaining = remaining[2:] if option in ("-u", "-g", "-h", "-p", "-U") else remaining[1:]
        elif head in _TRANSPARENT_WRAPPERS:
            remaining = remaining[1:]
            while remaining and (remaining[0].startswith("-") or remaining[0].replace(".", "", 1).isdigit()):
                remaining = remaining[1:]
            while remaining and _ENV_ASSIGNMENT.fullmatch(remaining[0]):
                remaining = remaining[1:]
        else:
            break
    return remaining, findings


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


def _path_class(raw: str, scope: ShellScope) -> PathClass:
    if _DYNAMIC.search(raw):
        return "home"
    return classify_path(expand_path(raw, scope.cwd), scope.workspace, scope.protected)


def _worst_class(classes: list[PathClass]) -> PathClass:
    order: tuple[PathClass, ...] = ("workspace", "temp", "home", "system", "protected")
    return max(classes, key=order.index) if classes else "workspace"


def _assess_delete(args: list[str], scope: ShellScope) -> Finding:
    targets: list[str] = _positional(args)
    worst: PathClass = _worst_class([_path_class(target, scope) for target in targets])
    if worst in ("workspace", "temp"):
        return _finding("low", (), "removes files inside the workspace or temp")
    if worst == "protected":
        return _finding("high", ("destructive", "secret_access"), "removes a protected location")
    return _finding("high", ("destructive", "outside_workspace"), "removes files outside the workspace")


def _assess_writes(command: str, args: list[str], scope: ShellScope) -> Finding:
    targets: list[str] = [target for target in _positional(args) if not target.isdigit()]
    if command == "chmod" or command == "chown" or command == "chgrp":
        targets = targets[1:]
    worst: PathClass = _worst_class([_path_class(target, scope) for target in targets])
    if worst in ("workspace", "temp"):
        return _finding("low", (), f"{command} inside the workspace or temp")
    if worst == "protected":
        return _finding("high", ("secret_access",), f"{command} touches a protected location")
    if worst == "system":
        return _finding("high", ("system_settings", "outside_workspace"), f"{command} modifies a system location")
    return _finding("medium", ("outside_workspace",), f"{command} modifies files outside the workspace")


def _assess_redirects(targets: list[str], scope: ShellScope) -> list[Finding]:
    findings: list[Finding] = []
    for target in targets:
        path_class: PathClass = _path_class(target, scope)
        if path_class == "protected":
            findings.append(_finding("high", ("secret_access",), "redirects output into a protected location"))
        elif path_class == "system":
            findings.append(_finding("high", ("system_settings", "outside_workspace"), "redirects output into system"))
        elif path_class == "home":
            findings.append(_finding("medium", ("outside_workspace",), "redirects output outside the workspace"))
    return findings


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


def _assess_git(args: list[str], scope: ShellScope) -> Finding:
    sub, rest = _git_subcommand(args)
    outside: tuple[Flag, ...] = (
        () if _path_class(str(scope.cwd), scope) in ("workspace", "temp") else ("outside_workspace",)
    )
    destructive_args: set[str] = {"--hard", "--force", "-f", "-fd", "-fdx", "-df", "-D", "--delete", "drop", "clear"}
    if sub == "push":
        if any(arg in ("--force", "-f", "--force-with-lease", "--delete", "--mirror") for arg in rest):
            return _finding("high", ("destructive", "external_send", *outside), "force-pushes or deletes remote refs")
        return _finding("medium", (), "pushes commits to a remote")
    if sub in ("reset", "clean", "checkout", "restore", "rebase", "filter-branch", "gc", "reflog", "switch"):
        if sub in ("reset", "clean", "filter-branch") and destructive_args.intersection(rest):
            return _finding("medium", ("destructive", *outside), f"git {sub} discards work")
        if sub in ("checkout", "restore") and ("--" in rest or "." in rest):
            return _finding("medium", ("destructive", *outside), f"git {sub} discards working tree changes")
        return _finding("low", (), f"git {sub}")
    if sub in ("branch", "stash", "tag") and destructive_args.intersection(rest):
        return _finding("medium", ("destructive", *outside), f"git {sub} deletes refs or stashes")
    if sub in _GIT_READ_ONLY:
        return _finding("safe", (), f"git {sub}")
    return _finding("low", (), f"git {sub or 'command'}")


def _assess_network_fetcher(command: str, args: list[str]) -> Finding:
    uploads: bool = any(arg in _UPLOAD_FLAGS or arg.startswith("--data") for arg in args)
    method: bool = any(
        arg.upper() in ("POST", "PUT", "DELETE", "PATCH") for arg in args if not arg.startswith("-") and arg.isalpha()
    )
    if uploads or method:
        return _finding("medium", (), f"{command} sends data to a remote server")
    return _finding("low", (), f"{command} fetches from a remote server")


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


def _assess_osascript(args: list[str], scope: ShellScope) -> list[Finding]:
    source: str | None = _osascript_source(args)
    if source is None:
        return [_finding("high", (), "runs a script file that cannot be inspected")]
    return _applescript_findings(source, scope)


def _assess_command(words: list[str], scope: ShellScope) -> list[Finding]:
    """Assess one command with its wrappers already stripped."""
    if not words:
        return []
    command: str = Path(words[0]).name
    args: list[str] = words[1:]
    if command in _SHELLS and "-c" in args:
        position: int = args.index("-c")
        inner: str = args[position + 1] if position + 1 < len(args) else ""
        return _assess_all(inner, scope)
    if command == "eval":
        return _assess_all(" ".join(args), scope)
    if command == "xargs":
        stripped, wrapper_findings = _strip_wrappers([arg for arg in args if not arg.startswith("-")])
        return [*wrapper_findings, *_assess_command(stripped, scope)]
    if command in ("rm", "rmdir", "unlink", "shred", "srm"):
        return [_assess_delete(args, scope)]
    if command == "find":
        if "-delete" in args or any(arg in ("-exec", "-execdir", "-ok") for arg in args):
            roots: list[str] = [arg for arg in args if not arg.startswith("-")][:1] or ["."]
            return [_assess_delete(roots, scope)]
        return [_finding("safe", (), "find")]
    if command == "git":
        return [_assess_git(args, scope)]
    if command in _NETWORK_FETCHERS:
        return [_assess_network_fetcher(command, args)]
    if command in _REMOTE_COPY:
        return [_finding("medium", (), f"{command} talks to a remote machine")]
    if command in _TUNNELS:
        return [_finding("high", ("external_send",), f"{command} exposes this machine to the internet")]
    if command == "osascript":
        return _assess_osascript(args, scope)
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
        inline: bool = any(arg in ("-c", "-e", "-E", "--eval", "-p") for arg in args)
        return [
            _finding("medium" if inline else "low", (), f"{command} runs {'inline code' if inline else 'a script'}")
        ]
    if command in _WRITERS:
        return [_assess_writes(command, args, scope)]
    if command == "sed" and any(arg.startswith("-i") for arg in args):
        return [_assess_writes("sed", [arg for arg in args if arg != ""], scope)]
    if command in _SAFE_COMMANDS or command in ("sed", "cd"):
        return [_finding("safe", (), command)]
    return [_finding("low", (), f"runs {command}")]


def _assess_segment(segment: list[str], scope: ShellScope) -> list[Finding]:
    words, redirect_targets = _split_redirects(segment)
    stripped, wrapper_findings = _strip_wrappers(words)
    return [*wrapper_findings, *_assess_command(stripped, scope), *_assess_redirects(redirect_targets, scope)]


def _universal_findings(command: str, scope: ShellScope) -> list[Finding]:
    findings: list[Finding] = []
    if mentions_protected(command, scope.protected):
        findings.append(_finding("high", ("secret_access",), "mentions a protected location"))
    if _PIPE_TO_SHELL.search(command):
        findings.append(_finding("high", ("install",), "executes code downloaded from the network"))
    if _FORK_BOMB.search(command):
        findings.append(_finding("high", ("destructive",), "looks like a fork bomb"))
    if _DEVICE_WRITE.search(command):
        findings.append(_finding("high", ("destructive", "system_settings"), "writes to a raw disk device"))
    return findings


def _assess_all(command: str, scope: ShellScope) -> list[Finding]:
    findings: list[Finding] = _universal_findings(command, scope)
    tokens: list[str] | None = _tokenize(command)
    if tokens is None:
        findings.append(_finding("medium", (), "shell syntax could not be parsed"))
        return findings
    for segment in _segments(tokens):
        findings.extend(_assess_segment(segment, scope))
    return findings


def _merge(findings: list[Finding]) -> ShellAssessment:
    if not findings:
        return ShellAssessment(risk="low", flags=(), reasons=("empty command",))
    risk: Risk = max((finding.risk for finding in findings), key=lambda value: _RISK_ORDER[value])
    flags: list[Flag] = []
    for finding in findings:
        flags.extend(finding.flags)
    reasons: list[str] = [finding.reason for finding in findings if finding.risk != "safe"]
    return ShellAssessment(
        risk=risk,
        flags=tuple(dict.fromkeys(flags)),
        reasons=tuple(dict.fromkeys(reasons)) or ("read-only command",),
    )


def assess_shell(command: str, cwd: Path, workspace: Path, protected: tuple[Path, ...]) -> ShellAssessment:
    """Classify ``command`` (a full shell line) as it would run in ``cwd``."""
    scope: ShellScope = ShellScope(cwd=cwd, workspace=workspace, protected=protected)
    return _merge(_assess_all(command, scope))


def assess_applescript(script: str, cwd: Path, workspace: Path, protected: tuple[Path, ...]) -> ShellAssessment:
    """Classify an AppleScript or JXA ``script``; the shell commands it spells out are judged like shell lines."""
    scope: ShellScope = ShellScope(cwd=cwd, workspace=workspace, protected=protected)
    return _merge(_applescript_findings(script, scope))


def script_reaches_shell(script: str) -> bool:
    """Whether ``script`` can run shell commands, directly or by typing them into a terminal."""
    return _AS_SHELL.search(script) is not None
