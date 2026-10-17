"""``dotx setup``: what this Mac has for Dot, the model Dot runs on with the key of its provider, and the service that
keeps Dot running. Flags give each step, for a setup without a terminal; in a terminal what they leave out is asked."""

import asyncio
import os
import sys
from collections.abc import Sequence

from dotagent.cli_commands import AuthSetCommand, Command, ServiceInstallCommand, SetupCommand
from dotagent.cli_daemon import cmd_service_install
from dotagent.cli_doctor import check_line
from dotagent.cli_models import cmd_auth_set
from dotagent.config import AppConfig, load_user_config
from dotagent.config_edit import set_default_model, set_role_model
from dotagent.core.errors import ConfigError, DotError, InvalidValueError, MissingSecretError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel
from dotagent.doctor import CheckResult, setup_checks
from dotagent.llm.calls import log_model_call
from dotagent.llm.roles import ModelRole
from dotagent.llm.router import ModelRef, ModelRouter, parse_model_ref
from dotagent.llm.types import ModelInfo
from dotagent.secrets import resolve_secret
from dotagent.server.links import base_url


class ProviderChoice(FrozenModel):
    """A provider as the setup offers it: its id, the name people know it by and what its models need."""

    provider: str
    label: str
    needs: str


# The providers in the order the setup offers them.
CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice(provider="ollama", label="Ollama", needs="local models, and cloud models with an Ollama account"),
    ProviderChoice(provider="openai", label="OpenAI", needs="an OpenAI API key"),
    ProviderChoice(provider="anthropic", label="Anthropic", needs="an Anthropic API key (the Claude models)"),
    ProviderChoice(provider="opencode_go", label="OpenCode Go", needs="an OpenCode Go API key"),
    ProviderChoice(
        provider="opencode", label="OpenCode Zen's free models", needs="no key; OpenCode (brew install opencode)"
    ),
)


def _key_name(config: AppConfig, provider: str) -> str | None:
    """The variable that holds the API key of ``provider``'s models, or ``None`` when they need none: Ollama's server
    runs them, and OpenCode's own agent serves Zen's free models."""
    match provider:
        case "openai":
            return config.providers.openai.api_key_env
        case "anthropic":
            return config.providers.anthropic.api_key_env
        case "opencode_go":
            return config.providers.opencode_go.api_key_env
        case _:
            return None


def _unread_fix(provider: str) -> str:
    """What to do when ``provider`` lists no models."""
    match provider:
        case "ollama":
            return "install Ollama (ollama.com) and open it, then run 'dotx setup' again"
        case "opencode":
            return "install OpenCode (brew install opencode), then run 'dotx setup' again"
        case _:
            return "check the network and the key; 'dotx setup --key-stdin' stores another key"


def _unlisted_fix(ref: ModelRef) -> str:
    """What to do when ``ref``'s provider does not list it."""
    if ref.provider == "ollama":
        return f"pull it first: ollama pull {ref.model} (a cloud model needs 'ollama signin' once)"
    return f"'dotx models {ref.provider}' lists the models it serves"


async def _ask(prompt: str) -> str:
    try:
        return (await asyncio.to_thread(input, prompt)).strip()
    except EOFError as exc:
        raise ConfigError("the terminal closed before an answer", {"prompt": prompt}) from exc


async def _pick(question: str, options: Sequence[str], suggested: int) -> int:
    """The index of the option whose number the person types; Enter takes ``suggested``."""
    sys.stdout.write(f"\n{question}\n")
    for number, option in enumerate(options, start=1):
        sys.stdout.write(f"  {number}. {option}\n")
    while True:
        answer: str = await _ask(f"Number [{suggested + 1}]: ")
        if answer == "":
            return suggested
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        sys.stdout.write(f"Type a number from 1 to {len(options)}.\n")


async def _listed(config: AppConfig, provider: str) -> tuple[ModelInfo, ...]:
    """The models ``provider`` lists to its key, as Settings › Models shows them."""
    router: ModelRouter = ModelRouter(config, log_model_call, os.environ)
    try:
        return await (await router.provider(provider)).list_models()
    except DotError as exc:
        raise ConfigError(
            f"the models of {provider} could not be listed: {exc.message}",
            {**exc.context, "provider": provider, "fix": _unread_fix(provider)},
        ) from exc
    finally:
        await router.aclose()


def _served(ref: ModelRef, listed: Sequence[ModelInfo]) -> ModelInfo:
    """The model ``ref`` names among those its provider ``listed``; a run needs one that can call tools."""
    found: ModelInfo | None = next((info for info in listed if info.id == ref.model), None)
    if found is None:
        raise ConfigError(
            f"{ref.provider} does not list {ref.model}",
            {"model": ref.label, "listed": len(listed), "fix": _unlisted_fix(ref)},
        )
    if not found.tools:
        raise ConfigError("a run needs a model that can call tools", {"model": ref.label})
    return found


def _write_models(ref: ModelRef, info: ModelInfo) -> tuple[ModelRole, ...]:
    """Make ``ref`` the model new chats start on, and the model of Dot's own work: the fast and the review model, and
    the vision model when it sees images. Returns the roles written."""
    roles: tuple[ModelRole, ...] = ("fast", "review", "vision") if info.vision else ("fast", "review")
    set_default_model(config_path(), ref.label)
    for role in roles:
        set_role_model(config_path(), role, ref.label)
    return roles


async def _choose_provider(asked: ModelRef | None) -> str:
    """The provider of the model asked for, or the one the person picks."""
    if asked is not None:
        return asked.provider
    options: list[str] = [f"{choice.label}: {choice.needs}" for choice in CHOICES]
    return CHOICES[await _pick("Which models should Dot run on?", options, 0)].provider


async def _keep_key(name: str) -> None:
    """The API key ``name`` as it is set, or, while it is set nowhere, the one the terminal asks for, stored in Dot's
    ``.env`` file as ``dotx auth set`` stores it."""
    if await asyncio.to_thread(resolve_secret, name) is not None:
        sys.stdout.write(f"{name} is set and kept; 'dotx auth set {name}' replaces it.\n")
        return
    if not sys.stdin.isatty():
        raise MissingSecretError(
            "the provider's API key is not set",
            {"name": name, "fix": f"pipe it in with --key-stdin, or run: dotx auth set {name}"},
        )
    await cmd_auth_set(AuthSetCommand(name="auth-set", key=name))


async def _choose_model(config: AppConfig, provider: str, asked: ModelRef | None) -> ModelInfo:
    """The model asked for, which ``provider`` must list and which must call tools, or the one the person picks among
    those it lists that do, the configured main model first when it is one of them."""
    listed: tuple[ModelInfo, ...] = await _listed(config, provider)
    if asked is not None:
        return _served(asked, listed)
    usable: list[ModelInfo] = [info for info in listed if info.tools]
    if not usable:
        raise ConfigError(f"{provider} lists no model that can call tools", {"listed": len(listed)})
    refs: list[str] = [f"{provider}:{info.id}" for info in usable]
    suggested: int = refs.index(config.models.main) if config.models.main in refs else 0
    return usable[await _pick("Which model should new chats start on?", [info.id for info in usable], suggested)]


async def cmd_setup(command: SetupCommand) -> int:
    asked: ModelRef | None = None if command.model is None else parse_model_ref(command.model)
    if asked is None and not sys.stdin.isatty():
        raise ConfigError("no model was chosen", {"fix": "choose one with --model provider:model"})
    sys.stdout.write("This Mac\n")
    rows: list[CheckResult] = await asyncio.to_thread(setup_checks, not command.no_service)
    for row in rows:
        sys.stdout.write(f"  {check_line(row)}")
    if any(not row.ok and row.required for row in rows):
        sys.stdout.write("Fix what is marked FAIL, then run 'dotx setup' again.\n")
        return 1
    config: AppConfig = load_user_config()
    provider: str = await _choose_provider(asked)
    key: str | None = _key_name(config, provider)
    if key is None and command.key_stdin:
        raise ConfigError("the models of this provider need no key", {"provider": provider, "flag": "--key-stdin"})
    if key is not None and command.key_stdin:
        await cmd_auth_set(AuthSetCommand(name="auth-set", key=key))
    elif key is not None:
        await _keep_key(key)
    info: ModelInfo = await _choose_model(config, provider, asked)
    ref: ModelRef = ModelRef(provider=provider, model=info.id)
    roles: tuple[ModelRole, ...] = await asyncio.to_thread(_write_models, ref, info)
    sys.stdout.write(f"\nModels\n  main      {ref.label}\n")
    for role in roles:
        sys.stdout.write(f"  {role:<9} {ref.label}\n")
    if "vision" not in roles:
        sys.stdout.write(f"  vision    {config.models.vision} (kept: {ref.label} sees no images)\n")
    sys.stdout.write("Settings › Models in the web UI chooses each of them.\n")
    if command.no_service:
        sys.stdout.write(
            "\nThe service is left as it is. Start Dot with 'dotx daemon' (in this terminal) or 'dotx service install' "
            f"(in the background, from every login); its web UI is at {base_url(config.server.host, config.server.port)}"
            ", and 'dotx open' opens it signed in. A daemon that already runs keeps its models until it starts again.\n"
        )
        return 0
    sys.stdout.write("\nService\n")
    return await cmd_service_install(ServiceInstallCommand(name="service-install"))


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case SetupCommand():
            return await cmd_setup(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
