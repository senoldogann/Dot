"""API key lookup: process environment, then ``<home>/.env``, then the macOS Keychain.

Keychain items are read from service ``dot-agent`` (account = variable name); add one with
``security add-generic-password -s dot-agent -a OPENAI_API_KEY -w`` (the command prompts for the value).
"""

import os
import subprocess
from pathlib import Path

from dotagent.core.errors import ConfigError
from dotagent.core.paths import env_file_path

KEYCHAIN_SERVICE: str = "dot-agent"


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


def write_env_value(path: Path, name: str, value: str) -> None:
    """Set ``name`` in the env file (created with mode 0600) keeping every other entry."""
    current: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    updated: dict[str, str] = {**current, name: value}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_env_file(updated), encoding="utf-8")
    os.chmod(path, 0o600)


def _from_env_file(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    return parse_env_file(path.read_text(encoding="utf-8")).get(name)


def _from_keychain(name: str) -> str | None:
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value: str = completed.stdout.strip()
    return value or None


def resolve_secret(name: str) -> str | None:
    """Return the secret called ``name`` from the first source that has it, else ``None``."""
    from_env: str | None = os.environ.get(name)
    if from_env:
        return from_env
    from_file: str | None = _from_env_file(env_file_path(), name)
    if from_file:
        return from_file
    return _from_keychain(name)


def require_secret(name: str, purpose: str) -> str:
    value: str | None = resolve_secret(name)
    if value is None:
        raise ConfigError(
            "missing API key",
            {
                "name": name,
                "purpose": purpose,
                "hint": f"export {name}=..., add it to {env_file_path()}, or run: dotx auth set {name}",
            },
        )
    return value
