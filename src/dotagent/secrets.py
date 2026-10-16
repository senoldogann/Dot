"""API key lookup: process environment, then ``<home>/.env``, then the macOS Keychain.

Keychain items are read from service ``dot-agent`` (account = variable name); add one with
``security add-generic-password -s dot-agent -a OPENAI_API_KEY -w`` (the command prompts for the value).
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from dotagent.core.errors import ConfigError, MissingSecretError
from dotagent.core.paths import env_file_path
from dotagent.core.text import KNOWN_SECRET_MIN_CHARS
from dotagent.core.types import FrozenModel

KEYCHAIN_SERVICE: str = "dot-agent"
# The end of a key that may be shown so the owner can tell two keys apart; a shorter key shows nothing of itself.
_SECURITY: str = "/usr/bin/security"
_KEYCHAIN_TIMEOUT_S: float = 15.0
_TAIL_CHARS: int = 4
_TAIL_MIN_CHARS: int = 16

type SecretSource = Literal["environment", "env_file", "keychain"]

# The values :func:`resolve_secret` returned in this process, for :func:`known_secrets`.
_resolved: set[str] = set()


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


def render_env_file(values: dict[str, str]) -> str:
    return "".join(f'{key}="{value}"\n' for key, value in sorted(values.items()))


def _write_env_file(path: Path, text: str) -> None:
    """Write the env file whole or not at all, readable by the owner alone from its first byte: into a new file that
    ``mkstemp`` makes with mode 0600 beside it, then moved over it (item 116)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_env_value(path: Path, name: str, value: str) -> None:
    """Set ``name`` in the env file (mode 0600) keeping every other entry."""
    current: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    _write_env_file(path, render_env_file({**current, name: value}))


def remove_env_value(path: Path, name: str) -> bool:
    """Drop ``name`` from the env file keeping every other entry; ``False`` when the file has no entry of that name."""
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
    """The secret called ``name`` in the login Keychain, or ``None``. A Keychain that does not answer (locked, or
    waiting for a prompt nobody answers) is a ``ConfigError`` that says so."""
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
                "hint": "unlock the login keychain or answer its prompt, or keep the key in the env file (dotx auth set)",
            },
        ) from exc
    if completed.returncode != 0:
        return None
    value: str = completed.stdout.strip()
    return value or None


def resolve_secret(name: str) -> str | None:
    """Return the secret called ``name`` from the first source that has it, else ``None``. The value is one of
    :func:`known_secrets` from then on."""
    found: str | None = os.environ.get(name) or _from_env_file(env_file_path(), name) or _from_keychain(name)
    if found:
        _resolved.add(found)
    return found or None


def known_secrets() -> tuple[str, ...]:
    """The secrets Dot holds, to be masked wherever they stand (review Y3,
    :func:`dotagent.core.text.redact_known`): the values :func:`resolve_secret` returned in this process and every
    value of ``<home>/.env``, each once, the longest first, none shorter than
    :data:`~dotagent.core.text.KNOWN_SECRET_MIN_CHARS`. None of them is ever logged or put in an error."""
    path: Path = env_file_path()
    saved: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    values: set[str] = {value for value in (*_resolved, *saved.values()) if len(value) >= KNOWN_SECRET_MIN_CHARS}
    return tuple(sorted(values, key=len, reverse=True))


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
    from_keychain: str | None = _from_keychain(name)
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
