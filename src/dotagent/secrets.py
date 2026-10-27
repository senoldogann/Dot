"""API key lookup: process environment, then ``<home>/.env``, then the macOS Keychain.

Keychain items are read from service ``dot-agent`` (account = variable name); add one with
``security add-generic-password -s dot-agent -a OPENAI_API_KEY -w`` (the command prompts for the value).
"""

import fcntl
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from dotagent.core.errors import ConfigError, MissingSecretError
from dotagent.core.files import link_target
from dotagent.core.logs import get_logger
from dotagent.core.paths import env_file_path
from dotagent.core.text import KNOWN_SECRET_MIN_CHARS, mask_known
from dotagent.core.types import FrozenModel

KEYCHAIN_SERVICE: str = "dot-agent"
# The end of a key that may be shown so the owner can tell two keys apart; a shorter key shows nothing of itself.
_SECURITY: str = "/usr/bin/security"
_KEYCHAIN_TIMEOUT_S: float = 15.0
# What ``security find-generic-password`` exits with when there is nothing to find: the low byte of the status it got,
# errSecItemNotFound (-25300), errSecNoDefaultKeychain (-25307) and errSecNoSuchKeychain (-25294). Any other exit is a
# Keychain that would not answer: errSecInteractionNotAllowed (36, locked, with nobody to ask), a prompt the owner
# denied (128), a failed authorization (51) (item 561).
_KEYCHAIN_HOLDS_NONE: frozenset[int] = frozenset({44, 37, 50})
_KEYCHAIN_ERROR_CHARS: int = 300
_TAIL_CHARS: int = 4
_TAIL_MIN_CHARS: int = 16
# The name of an entry of the env file, as a shell names a variable.
_ENTRY_NAME: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCK_FILE_MODE: int = 0o600

type SecretSource = Literal["environment", "env_file", "keychain"]

log = get_logger(__name__)

# The values :func:`resolve_secret` returned in this process, for :func:`known_secrets`.
_resolved: set[str] = set()
# The values of the owner's password manager and of Dot's own vault that were filled into a page in this process
# (:func:`hold_filled`), for :func:`known_secrets`. One set that is never changed, only replaced under the lock: the
# worker threads that read the secrets Dot holds never iterate a set that another thread is adding to.
_filled: frozenset[str] = frozenset()
_filled_lock: threading.Lock = threading.Lock()


class SecretState(FrozenModel):
    """A secret described without its value: ``source`` is where the value :func:`resolve_secret` returns comes from
    (``None``: no source has one), ``saved`` whether ``<home>/.env`` holds a value, and ``tail`` the last characters of
    the value in use ("" when there is none or it is too short to show any of it)."""

    source: SecretSource | None
    saved: bool
    tail: str


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines (optional ``export`` prefix, optional quotes, ``#`` comments)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line: str = raw.strip()
        if line == "" or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if separator == "":
            continue
        cleaned: str = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
            cleaned = cleaned[1:-1]
        values[key.strip()] = cleaned
    return values


def _one_line(text: str) -> bool:
    """Whether ``text`` stays on one line of the env file as :func:`parse_env_file` reads it, which breaks lines where
    :meth:`str.splitlines` does (a line feed, a carriage return and the other line boundaries of Unicode)."""
    return text.splitlines() == ([text] if text else [])


def render_env_file(values: dict[str, str]) -> str:
    """The env file's text: a ``KEY="value"`` line for each entry, sorted. A name that is not a variable's, or a value
    that would not stay on its line, is refused with :class:`ConfigError` (item 350: a value with a line break in it
    wrote a second entry of its own); the error names the entry, never its value."""
    for key, value in values.items():
        if _ENTRY_NAME.fullmatch(key) is None:
            raise ConfigError("the name of a key must be a variable name (letters, digits and _)", {"name": repr(key)})
        if not _one_line(value):
            raise ConfigError("the value of a key may not hold a line break", {"name": key})
    return "".join(f'{key}="{value}"\n' for key, value in sorted(values.items()))


def env_lock_path(path: Path) -> Path:
    """The lock that the processes changing the env file at ``path`` hold in turn."""
    return path.with_name(f"{path.name}.lock")


@contextmanager
def _changing(path: Path) -> Iterator[None]:
    """Hold the lock of the env file at ``path`` until the block ends: a change reads the file and writes it whole, so
    two processes changing it at once (the daemon and ``dotx auth``) take turns and both keep their change (item 350:
    the later write had been read before the earlier one landed, and undid it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int = os.open(env_lock_path(path), os.O_RDWR | os.O_CREAT, _LOCK_FILE_MODE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_env_file(path: Path, text: str) -> None:
    """Write the env file whole or not at all, readable by the owner alone from its first byte: into a new file that
    ``mkstemp`` makes with mode 0600 beside it, then moved over it (item 116). An env file that is a link is written
    where it leads, and stays a link (item 728)."""
    target: Path = link_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, target)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_env_value(path: Path, name: str, value: str) -> None:
    """Set ``name`` in the env file (mode 0600) keeping every other entry, under the file's lock."""
    with _changing(path):
        current: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
        _write_env_file(path, render_env_file({**current, name: value}))


def remove_env_value(path: Path, name: str) -> bool:
    """Drop ``name`` from the env file keeping every other entry, under the file's lock; ``False`` when the file has no
    entry of that name."""
    with _changing(path):
        current: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
        if name not in current:
            return False
        _write_env_file(path, render_env_file({key: value for key, value in current.items() if key != name}))
        return True


def _from_env_file(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    return parse_env_file(path.read_text(encoding="utf-8")).get(name)


def _from_keychain(name: str) -> str | None:
    """The secret called ``name`` in the login Keychain, or ``None`` when it holds none (:data:`_KEYCHAIN_HOLDS_NONE`).
    A Keychain that does not answer (locked, or waiting for a prompt nobody answers) or that refuses (a prompt the
    owner denied, a locked one with nobody to ask) is a ``ConfigError`` that says so, not a key that is missing."""
    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            [_SECURITY, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
            timeout=_KEYCHAIN_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            "the macOS Keychain did not answer",
            {
                "name": name,
                "timeout_s": _KEYCHAIN_TIMEOUT_S,
                "hint": (
                    "unlock the login keychain or answer its prompt, or keep the key in the env file (dotx auth set)"
                ),
            },
        ) from exc
    if completed.returncode in _KEYCHAIN_HOLDS_NONE:
        return None
    if completed.returncode != 0:
        # Not "no key": the owner would be told to enter a key that is there. What ``security`` says holds no secret.
        raise ConfigError(
            "the macOS Keychain refused the lookup",
            {
                "name": name,
                "returncode": completed.returncode,
                "stderr": " ".join(completed.stderr.split())[:_KEYCHAIN_ERROR_CHARS],
                "hint": (
                    "unlock the login keychain, or allow Dot's access when macOS asks, or keep the key in the env "
                    "file (dotx auth set)"
                ),
            },
        )
    value: str = completed.stdout.strip()
    return value or None


def resolve_secret(name: str) -> str | None:
    """Return the secret called ``name`` from the first source that has it, else ``None``. The value is one of
    :func:`known_secrets` from then on."""
    found: str | None = os.environ.get(name) or _from_env_file(env_file_path(), name) or _from_keychain(name)
    if found:
        _resolved.add(found)
    return found or None


def _say_unreadable(error: ConfigError) -> None:
    """The log's line for a Keychain that did not answer or refused: what ``error`` says of it, without the hint."""
    log.warning(
        "keychain_unreadable",
        error=error.message,
        **{key: value for key, value in error.context.items() if key != "hint"},
    )


def optional_secret(name: str) -> str | None:
    """The secret called ``name`` for a service Dot can start without (the key of a hosted rung of a ladder, the
    organization of an OpenAI account): a Keychain that does not answer or that refuses (locked, a prompt denied) is
    logged (``keychain_unreadable``) and taken as no key, so that the daemon still starts and the page of the service
    still opens, and a run that needs the key meets the Keychain's error itself (:func:`require_secret`). A key the
    service cannot be built without keeps :func:`require_secret` and its error (item 561). Blocking."""
    try:
        return resolve_secret(name)
    except ConfigError as error:
        _say_unreadable(error)
        return None


def hold_filled(*values: str) -> None:
    """Note values that a login of a password manager or of Dot's vault gave a page (item 621): they are among
    :func:`known_secrets` (``filled`` true) from then on, for as long as this process runs, so that every place that
    masks the secrets Dot holds masks them too (what a model says, an approval, an event, the tail of a log, a note a
    run saves). They are masks and no more: a run that cannot have seen them is not judged by them, and nothing
    refuses or chooses by them (``known_secrets(filled=False)``)."""
    global _filled
    with _filled_lock:
        _filled = _filled | frozenset(value for value in values if value)


def known_secrets(*, filled: bool) -> tuple[str, ...]:
    """The secrets Dot holds (review Y3): the values :func:`resolve_secret` returned in this process, every value of
    ``<home>/.env`` and, with ``filled`` true, the values filled into a page from a vault (:func:`hold_filled`), each
    once, the longest first, none shorter than :data:`~dotagent.core.text.KNOWN_SECRET_MIN_CHARS`. None of them is ever
    logged or put in an error.

    ``filled`` has no default, so that each caller says which it is (item 621). A caller that *masks* (text shown,
    logged, stored or sent on with the values replaced, :func:`dotagent.core.text.redact_known`) passes true: a value a
    page was given stands nowhere. A caller that *refuses, blocks or chooses another outcome* because a value is
    present passes false: a run never saw a filled value, so what it writes, saves or sends is not judged by it, and
    an outcome that differs by whether a guess is the value tells the run the value (the checks of items 525 and 621).
    A caller that does both refuses by false and then stores or shows the text with every value masked
    (:func:`mask_filled`, or true)."""
    path: Path = env_file_path()
    saved: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    held: frozenset[str] = _filled if filled else frozenset()
    values: set[str] = {value for value in (*_resolved, *held, *saved.values()) if len(value) >= KNOWN_SECRET_MIN_CHARS}
    return tuple(sorted(values, key=len, reverse=True))


def mask_filled(text: str) -> str:
    """``text`` with the values filled into a page masked where they stand, and nothing else (item 621): for a text
    that is stored or shown after a check that did not look at them (a note, a harness text, a line of the owner
    section, an exported agent). The values are in memory, so it reads no file and may be called on the event loop."""
    return mask_known(text, tuple(_filled))


def _tail(value: str) -> str:
    return value[-_TAIL_CHARS:] if len(value) >= _TAIL_MIN_CHARS else ""


def secret_state(name: str) -> SecretState:
    """Where the secret called ``name`` comes from, read in the order of :func:`resolve_secret` (the Keychain only when
    neither the environment nor the env file has it), without giving the value away."""
    saved: str | None = _from_env_file(env_file_path(), name)
    from_env: str | None = os.environ.get(name)
    if from_env:
        return SecretState(source="environment", saved=bool(saved), tail=_tail(from_env))
    if saved:
        return SecretState(source="env_file", saved=True, tail=_tail(saved))
    try:
        from_keychain: str | None = _from_keychain(name)
    except ConfigError as error:
        # A view of where keys are kept must not fail with a Keychain that does not answer: the key shows as unset, and
        # the log says why; a run that needs the key meets the error itself (:func:`resolve_secret`, item 561).
        _say_unreadable(error)
        from_keychain = None
    if from_keychain:
        return SecretState(source="keychain", saved=False, tail=_tail(from_keychain))
    return SecretState(source=None, saved=False, tail="")


def require_secret(name: str, purpose: str) -> str:
    value: str | None = resolve_secret(name)
    if value is None:
        raise MissingSecretError(
            "missing API key",
            {
                "name": name,
                "purpose": purpose,
                "hint": f"export {name}=..., add it to {env_file_path()}, or run: dotx auth set {name}",
            },
        )
    return value
