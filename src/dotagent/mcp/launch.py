"""The environment an MCP server starts with, and the headers one is reached with."""

import asyncio
import re
from collections.abc import Mapping

from pydantic import JsonValue

from dotagent.config import McpServerConfig
from dotagent.core.errors import ConfigError
from dotagent.core.paths import env_file_path
from dotagent.secrets import resolve_secret

_REFERENCE: re.Pattern[str] = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def referenced_secrets(config: McpServerConfig) -> list[str]:
    """The names written as ``${NAME}`` in the values of ``env`` and ``headers``, each once."""
    values: tuple[str, ...] = (*config.env.values(), *config.headers.values())
    return sorted({name for value in values for name in _REFERENCE.findall(value)})


def expand_references(value: str, secrets: Mapping[str, str]) -> str:
    return _REFERENCE.sub(lambda match: secrets[match.group(1)], value)


async def resolve_server_secrets(config: McpServerConfig) -> dict[str, str]:
    """Every secret the server refers to, looked up like an API key (environment, ``.env``, Keychain)."""
    names: list[str] = referenced_secrets(config)
    looked_up: list[str | None] = list(await asyncio.gather(*(asyncio.to_thread(resolve_secret, n) for n in names)))
    missing: list[JsonValue] = [name for name, value in zip(names, looked_up, strict=True) if value is None]
    if missing:
        raise ConfigError(
            "an MCP server refers to secrets that are not set",
            {
                "server": config.name,
                "missing": missing,
                "hint": f"export NAME=..., add it to {env_file_path()}, or run: dotx auth set NAME",
            },
        )
    return {name: value for name, value in zip(names, looked_up, strict=True) if value is not None}


def server_environment(
    config: McpServerConfig, login_env: Mapping[str, str], secrets: Mapping[str, str]
) -> dict[str, str]:
    """``PATH`` of the login shell, so ``npx``, ``uvx`` and friends are found, plus ``env`` with its secrets filled in.

    Nothing else of Dot's own environment reaches the server: the MCP client adds only a short list of harmless
    variables (``HOME``, ``USER``, ``SHELL``, ``TERM``, ``LOGNAME``).
    """
    configured: dict[str, str] = {key: expand_references(value, secrets) for key, value in config.env.items()}
    path: str | None = login_env.get("PATH")
    return configured if path is None else {"PATH": path, **configured}


def server_headers(config: McpServerConfig, secrets: Mapping[str, str]) -> dict[str, str]:
    """``headers`` with its secrets filled in, sent with every request to a server reached at a URL."""
    return {key: expand_references(value, secrets) for key, value in config.headers.items()}
