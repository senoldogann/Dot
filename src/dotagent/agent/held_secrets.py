"""The secrets the owner gave a chat on a run's card (item 857): a key, a token or a password a run asked for with
``ask_secret``. Each is held for :data:`HOLD_S` in a file of the chat's own under Dot's home, readable by the owner
alone, and joins the environment of that chat's commands under the name the run asked for. Its value is never written
anywhere else: not an event, an approval, the chat's history, a prompt or a log line. From when it is saved, and again
when a daemon reads the file after a restart, it is among the secrets Dot masks (:func:`dotagent.secrets.hold_filled`),
so a command that prints it shows it masked. A run reads the names a chat holds, never their values."""

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from pydantic import Field

from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import dot_home
from dotagent.core.text import KNOWN_SECRET_MIN_CHARS
from dotagent.core.types import FrozenModel
from dotagent.secrets import hold_filled

# The severity of the card that asks for one among the approvals: answered through its own route, which takes the value
# alone, and never in the terminal.
SECRET_SEVERITY: str = "secret"
# How long a secret given on a card holds for its chat's commands: a day, as the card says.
HOLD_S: float = 24 * 3600.0
# The longest secret a card takes: a token, a key, a password, a service account's file in one line.
MOST_VALUE_CHARS: int = 8192
_NAME: re.Pattern[str] = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_THREAD: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]{1,80}")
# The names the shell, the loader or a language reads to decide how a command runs: a secret given under one would
# change what the chat's commands do, not only what they can reach.
_RESERVED: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "USER",
        "LOGNAME",
        "PWD",
        "OLDPWD",
        "TMPDIR",
        "TERM",
        "LANG",
        "IFS",
        "ENV",
        "BASH_ENV",
        "ZDOTDIR",
        "PS1",
        "PS4",
        "PROMPT_COMMAND",
        "CDPATH",
        "FPATH",
        "MANPATH",
        "EDITOR",
        "VISUAL",
        "PAGER",
        "BROWSER",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PERL5LIB",
        "PERL5OPT",
        "RUBYLIB",
        "RUBYOPT",
        "NODE_OPTIONS",
        "NODE_PATH",
        "JAVA_TOOL_OPTIONS",
        "CLASSPATH",
        "HISTFILE",
        "INPUTRC",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "SHELLOPTS",
        "BASHOPTS",
        "LESSOPEN",
        "LESSCLOSE",
        "PYTHONWARNINGS",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "JDK_JAVA_OPTIONS",
        # Where a command's connections go and whom they trust: a secret given under one would send them elsewhere,
        # or write their keys to a file (``SSLKEYLOGFILE``).
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSLKEYLOGFILE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "CURL_HOME",
        "NODE_EXTRA_CA_CERTS",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "OPENSSL_CONF",
    }
)
_RESERVED_PREFIXES: tuple[str, ...] = (
    "DYLD_",
    "LD_",
    "GIT_",
    "SSH_",
    "DOT_",
    "LC_",
    "BASH_FUNC_",
    "PIP_",
    "NPM_CONFIG_",
)
_FILE_MODE: int = 0o600
_lock: threading.Lock = threading.Lock()


class HeldSecret(FrozenModel):
    """One secret a chat holds: the name its commands read it as, the words of the card it was asked on (``label``),
    and until when it holds. Its value is not a field: :func:`held_values` alone reads it."""

    name: str
    label: str
    until: float
    # What :func:`save_secret` replaced, for :func:`forget_secret` to put back: never shown, dumped or listed.
    replaced: dict[str, object] | None = Field(default=None, exclude=True, repr=False)


def checked_name(name: str) -> str:
    """``name`` as a secret's environment variable may be: upper case letters, digits and underscores, starting with a
    letter, 2 to 64 long, and none the shell, the loader or a language reads (:data:`_RESERVED`); else refused."""
    if _NAME.fullmatch(name) is None:
        raise InvalidValueError(
            "a secret's name is an environment variable's: capitals, digits and underscores, starting with a capital",
            {"name": name[:80]},
        )
    if name in _RESERVED or name.startswith(_RESERVED_PREFIXES):
        raise InvalidValueError("a secret cannot take a name the shell or a program reads to run", {"name": name})
    return name


def _usable_name(name: str) -> bool:
    try:
        checked_name(name)
    except InvalidValueError:
        return False
    return True


def held_dir() -> Path:
    """The folder of the chats' files: no run reads or changes it (the guard and the sandbox, ``_guarded_files``), so a
    chat's command reads its own secrets from its environment alone, and another chat's not at all."""
    return dot_home() / "held-secrets"


def _path(thread_id: str) -> Path:
    if _THREAD.fullmatch(thread_id) is None:
        raise InvalidValueError("not a chat's id", {"thread_id": thread_id[:80]})
    return held_dir() / f"{thread_id}.json"


def _held_path(thread_id: str) -> Path | None:
    """The file of the chat ``thread_id``, or None for an id that names no file of the folder: such a chat holds
    nothing (a run's own names, a test's)."""
    return None if _THREAD.fullmatch(thread_id) is None else held_dir() / f"{thread_id}.json"


def _read(path: Path) -> dict[str, dict[str, object]]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # A file that cannot be read holds nothing: the run is asked again.
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): entry for name, entry in raw.items() if isinstance(entry, dict)}


def _write(path: Path, entries: dict[str, dict[str, object]]) -> None:
    """Write the chat's file whole or not at all, readable by the owner alone from its first byte (mkstemp's 0600, then
    moved over it); a chat that holds nothing has no file."""
    if not entries:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
        os.chmod(temporary, _FILE_MODE)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _live(entries: dict[str, dict[str, object]], now: float) -> dict[str, dict[str, object]]:
    return {
        name: entry
        for name, entry in entries.items()
        if isinstance(entry.get("value"), str)
        and isinstance(entry.get("until"), int | float)
        and float(entry["until"]) > now  # type: ignore[arg-type]
    }


def save_secret(thread_id: str, name: str, label: str, value: str, now: float | None = None) -> HeldSecret:
    """Hold ``value``, without the spaces and line ends around it, for the chat ``thread_id`` under ``name`` for
    :data:`HOLD_S` from ``now``, replacing a secret of that name, and mask it from now on. A value shorter than the
    masks reach (8 characters, as a printed one would then stand bare) or longer than :data:`MOST_VALUE_CHARS` is
    refused."""
    checked_name(name)
    # Kept as it is masked: a space or a line end pasted around it would stand in a printed value, and the mask would
    # not match it.
    value = value.strip()
    if len(value) < KNOWN_SECRET_MIN_CHARS:
        raise InvalidValueError(
            f"a secret shorter than {KNOWN_SECRET_MIN_CHARS} characters cannot be masked where a command prints it",
            {"name": name},
        )
    if len(value) > MOST_VALUE_CHARS:
        raise InvalidValueError("a secret is at most 8192 characters", {"name": name})
    at: float = time.time() if now is None else now
    until: float = at + HOLD_S
    path: Path = _path(thread_id)
    with _lock:
        entries: dict[str, dict[str, object]] = _live(_read(path), at)
        replaced: dict[str, object] | None = entries.get(name)
        entries[name] = {"value": value, "label": label[:200], "until": until}
        _write(path, entries)
    hold_filled(value)
    return HeldSecret(name=name, label=label[:200], until=until, replaced=replaced)


def forget_secret(saved: HeldSecret, thread_id: str, value: str, now: float | None = None) -> None:
    """Take back what :func:`save_secret` held as ``saved`` for the chat ``thread_id``, when the card was answered
    otherwise meanwhile (two tabs, its deadline): the secret it replaced is put back while it holds, and a value saved
    since (the other tab's) is left as it is. The mask stays, as a value once given stands nowhere."""
    at: float = time.time() if now is None else now
    path: Path | None = _held_path(thread_id)
    if path is None:
        return
    with _lock:
        entries: dict[str, dict[str, object]] = _live(_read(path), at)
        current: dict[str, object] | None = entries.get(saved.name)
        if current is None or current.get("value") != value.strip():
            return
        before: dict[str, dict[str, object]] = _live({saved.name: saved.replaced}, at) if saved.replaced else {}
        if before:
            entries[saved.name] = before[saved.name]
        else:
            del entries[saved.name]
        _write(path, entries)


def held_secrets(thread_id: str, now: float | None = None) -> list[HeldSecret]:
    """The secrets the chat ``thread_id`` holds now, by name: their names and labels, never their values."""
    at: float = time.time() if now is None else now
    path: Path | None = _held_path(thread_id)
    entries = {} if path is None else _live(_read(path), at)
    return [
        HeldSecret(name=name, label=str(entry.get("label", "")), until=float(entry["until"]))  # type: ignore[arg-type]
        for name, entry in sorted(entries.items())
    ]


def held_values(thread_id: str, now: float | None = None) -> dict[str, str]:
    """The environment the chat ``thread_id``'s commands get of its secrets: each name with its value, while it holds.
    For the command's environment alone; nothing else may read the values."""
    at: float = time.time() if now is None else now
    path: Path | None = _held_path(thread_id)
    entries = {} if path is None else _live(_read(path), at)
    # A name the file holds is checked again: a file written by other means than :func:`save_secret` (a yolo run, which
    # may write any file) must not set what the shell or the loader reads.
    return {name: str(entry["value"]) for name, entry in entries.items() if _usable_name(name)}


def all_held_values(now: float | None = None) -> tuple[str, ...]:
    """The values every chat holds now, to mask in what a tool returns: a process reads them from the files, as one
    given on the daemon's page after it started is in the daemon's masks alone (a `dotx run` is a process of its own).
    It removes nothing."""
    at: float = time.time() if now is None else now
    folder: Path = held_dir()
    if not folder.is_dir():
        return ()
    with _lock:
        return tuple(str(entry["value"]) for path in folder.glob("*.json") for entry in _live(_read(path), at).values())


def mask_all_held(now: float | None = None) -> int:
    """Mask the values every chat holds, as a daemon starts (the masks are in memory, :func:`hold_filled`), and remove
    the files that hold nothing live any more. Returns how many values are masked."""
    at: float = time.time() if now is None else now
    folder: Path = held_dir()
    if not folder.is_dir():
        return 0
    values: list[str] = []
    with _lock:
        for path in folder.glob("*.json"):
            live = _live(_read(path), at)
            if not live:
                path.unlink(missing_ok=True)
                continue
            values.extend(str(entry["value"]) for entry in live.values())
    kept: list[str] = [value for value in values if len(value) >= KNOWN_SECRET_MIN_CHARS]
    if kept:
        hold_filled(*kept)
    return len(kept)
