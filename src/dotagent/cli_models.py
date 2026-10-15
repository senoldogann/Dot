"""``dotx models`` and ``dotx auth``: the models each provider offers, and the API keys in the environment file."""

import asyncio
import getpass
import os
import sys

from dotagent.cli_commands import (
    AuthSetCommand,
    AuthStatusCommand,
    Command,
    ModelsCommand,
)
from dotagent.config import AppConfig, load_user_config, providers_by_key
from dotagent.core.errors import ConfigError, DotError, InvalidValueError
from dotagent.core.paths import (
    env_file_path,
)
from dotagent.llm.calls import log_model_call
from dotagent.llm.router import ModelRef, ModelRouter
from dotagent.llm.types import KNOWN_PROVIDERS, ModelInfo
from dotagent.secrets import resolve_secret, write_env_value


async def cmd_models(command: ModelsCommand) -> int:
    config: AppConfig = load_user_config()
    router: ModelRouter = ModelRouter(config, log_model_call, os.environ)
    failures: int = 0
    names: list[str] = [command.provider] if command.provider is not None else list(KNOWN_PROVIDERS)
    try:
        for provider_name in names:
            try:
                models: tuple[ModelInfo, ...] = await (await router.provider(provider_name)).list_models()
            except DotError as exc:
                failures += 1
                sys.stdout.write(f"{provider_name}: unavailable - {exc.message}\n")
                continue
            sys.stdout.write(f"{provider_name}:\n")
            for info in models:
                ref: ModelRef = ModelRef(provider=provider_name, model=info.id)
                traits: str = ", ".join(
                    name
                    for name, present in (("tools", info.tools), ("vision", info.vision), ("thinking", info.thinking))
                    if present
                )
                levels: str = f"  reasoning: {' '.join(info.efforts)}" if len(info.efforts) > 1 else ""
                sys.stdout.write(f"  {ref.label}  [{traits}]{levels}\n")
    finally:
        await router.aclose()
    # Some providers unavailable is the usual state (no key for one); none available is a failure.
    return 1 if failures == len(names) else 0


async def cmd_auth_set(command: AuthSetCommand) -> int:
    known: dict[str, str] = providers_by_key(load_user_config())
    if command.key not in known:
        raise ConfigError("unknown key name", {"key": command.key, "known": list(sorted(known))})
    if sys.stdin.isatty():
        value: str = await asyncio.to_thread(getpass.getpass, f"{command.key}: ")
    else:
        value = (await asyncio.to_thread(sys.stdin.read)).strip()
    if not value.strip():
        raise ConfigError("no value was provided", {"key": command.key})
    await asyncio.to_thread(write_env_value, env_file_path(), command.key, value.strip())
    sys.stdout.write(f"{command.key} stored in {env_file_path()} (mode 0600)\n")
    return 0


async def cmd_auth_status(command: AuthStatusCommand) -> int:
    for variable, provider_name in providers_by_key(load_user_config()).items():
        state: str = "set" if await asyncio.to_thread(resolve_secret, variable) is not None else "missing"
        sys.stdout.write(f"{provider_name:<12} {variable:<18} {state}\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case ModelsCommand():
            return await cmd_models(command)
        case AuthSetCommand():
            return await cmd_auth_set(command)
        case AuthStatusCommand():
            return await cmd_auth_status(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
