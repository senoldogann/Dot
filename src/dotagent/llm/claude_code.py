"""The owner's Claude plan as Dot's model picker lists it (item 1013), provider ``claude_code``: Claude Code's own model
aliases, ``opus`` and ``sonnet``, listed once the ``claude`` program is found and signed in. They answer Claude Code
alone, so a run on one goes to that program whole (:func:`dotagent.claude_code.run.run_in_claude_code`), and a single
question of Dot's (a review, the harness's roles, item 1017) goes to it as one call with no tool of its own
(:mod:`dotagent.claude_code.ask`); a call of a conversation is refused. The program gets Dot's own tools, and only
those, through the provider's bridge (:mod:`dotagent.opencode.bridge`).

Whose plan it is is read from ``~/.claude.json``'s ``oauthAccount`` (:func:`dotagent.repair.claude.read_account`): an
address and an organisation, no secret. Dot never reads Claude Code's credentials nor its Keychain item."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

from dotagent.claude_code.ask import ask_claude
from dotagent.config import ClaudeCodeConfig
from dotagent.core.errors import ClaudeCodeMissing, ConfigError
from dotagent.core.paths import claude_code_command
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import ChatRequest, DeltaSink, Effort, ModelInfo, ModelReply
from dotagent.opencode.bridge import ToolBridge
from dotagent.opencode.server import find_program
from dotagent.repair.claude import ClaudeAccount, claude_state, read_account

CLAUDE_CODE_PROVIDER: str = "claude_code"
# Claude Code's own aliases for the newest of each family, and the model each named (item 1054: its first line,
# ``system``/``init``, says the model; measured on 2.1.319 on 2026-11-08). The alias stays what a run names, so that a
# choice of "the newest Opus" follows Claude Code; the model it names gives its name on the page.
ALIASES: dict[str, str] = {
    "fable": "claude-fable-5-1",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}
# The older models the plan answers on (item 1054): each asked once with `--model` at the lowest and the highest
# effort on 2.1.319 on 2026-11-08, and each answered; Sonnet 4.5 and 4, Haiku 5 and the Claude 3 models did not.
LEGACY: tuple[str, ...] = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)
# The models a run may name: the newest of each family by its alias, then the older ones (item 1054).
MODELS: tuple[str, ...] = (*ALIASES, *LEGACY)
# How hard a run thinks, as Claude Code's `--effort` takes it (item 1014), lowest first.
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
# The models of the plan that have fast mode (Claude Code's `fastMode` setting, item 1014): those of Anthropic's API
# that have it (:data:`dotagent.llm.anthropic.FAST_MODE_MODELS`), Opus 5.5 by its alias and Opus 5 (item 1054). A
# plan without extra usage runs it at the standard speed (measured: every model answered ``extra_usage_disabled``).
FAST_MODELS: frozenset[str] = frozenset({"opus", "claude-opus-5"})
# The families of Claude's models, by the word their names carry.
_FAMILIES: tuple[str, ...] = ("fable", "opus", "sonnet", "haiku")
# The settings that turn fast mode on, given to a run's program with `--settings`.
FAST_SETTINGS: str = '{"fastMode":true}'
NOT_INSTALLED: str = (
    "Claude Code is not installed on this Mac: install it (claude.com/claude-code) and sign in with your Claude plan "
    "by running 'claude' once in Terminal, or set [providers.claude_code] command to where it is"
)
NOT_SIGNED_IN: str = (
    "Claude Code is not signed in: run 'claude' once in Terminal and sign in with your Claude plan (Pro or Max)"
)


def model_label(model: str) -> str:
    """The name of one of Claude Code's models as people know it (item 1054): an alias by the model it names, "Opus
    5.5"; a model's id by its family and version, ``claude-opus-4-7`` "Opus 4.7", ``claude-opus-4-0`` "Opus 4". An id
    that is neither is shown as it is."""
    named: str = ALIASES.get(model, model)
    parts: list[str] = named.removeprefix("claude-").split("-")
    if not parts or parts[0] not in _FAMILIES or not all(part.isdigit() for part in parts[1:]) or len(parts) < 2:
        return model
    version: list[str] = parts[1:-1] if len(parts) > 2 and parts[-1] == "0" else parts[1:]
    return f"{parts[0].capitalize()} {'.'.join(version)}"


def model_info_of(model: str) -> ModelInfo:
    """What one of Claude Code's models offers Dot: it sees, calls tools and thinks; its window is Claude Code's to
    manage, so Dot names none; its reasoning levels are Claude Code's (:data:`EFFORTS`, item 1014)."""
    return ModelInfo(
        id=model,
        vision=True,
        tools=True,
        thinking=True,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=EFFORTS,
        free=False,
    )


class ClaudeCodeProvider:
    """A connector to the owner's Claude plan through the ``claude`` program they installed and signed in to."""

    def __init__(self, config: ClaudeCodeConfig, login_env: Mapping[str, str], bridge: ToolBridge) -> None:
        self._config: ClaudeCodeConfig = config
        self._login_env: dict[str, str] = dict(login_env)
        self._bridge: ToolBridge = bridge

    @property
    def name(self) -> str:
        return CLAUDE_CODE_PROVIDER

    @property
    def config(self) -> ClaudeCodeConfig:
        return self._config

    @property
    def bridge(self) -> ToolBridge:
        """Dot's MCP server that gives the program Dot's own tools while a run works
        (:mod:`dotagent.claude_code.run`)."""
        return self._bridge

    def _find(self) -> Path | None:
        return find_program(claude_code_command(self._config.command), self._login_env.get("PATH", ""))

    async def program(self) -> Path:
        """The ``claude`` program, signed in. Raises :class:`ClaudeCodeMissing` when it is not installed or no account
        is signed in."""
        found: Path | None = await asyncio.to_thread(self._find)
        if found is None:
            raise ClaudeCodeMissing(NOT_INSTALLED, {"command": claude_code_command(self._config.command)})
        if await self.account() is None:
            raise ClaudeCodeMissing(NOT_SIGNED_IN, {"state": str(claude_state(self._login_env))})
        return found

    async def account(self) -> ClaudeAccount | None:
        """The account ``claude`` is signed in with, read from its state file (no secret), or ``None``."""
        return await asyncio.to_thread(read_account, claude_state(self._login_env))

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        """A single question of Dot's (item 1017: a review, the harness's roles) answered by the program in one go,
        with no tool but the shape of its answer (:mod:`dotagent.claude_code.ask`); a run goes to it whole instead."""
        if request.model not in MODELS:
            raise ConfigError(
                f"Claude Code offers Dot its {', '.join(MODELS)} models only; pick one of them",
                {"provider": CLAUDE_CODE_PROVIDER, "model": request.model, "models": list(MODELS)},
            )
        level: Effort | None = effective_effort(EFFORTS, request.effort)
        return await ask_claude(
            await self.program(), request, self._login_env, request.model, EFFORTS[0] if level is None else level
        )

    async def model_info(self, model: str) -> ModelInfo:
        if model not in MODELS:
            raise ConfigError(
                f"Claude Code offers Dot its {', '.join(MODELS)} models only; pick one of them",
                {"provider": CLAUDE_CODE_PROVIDER, "model": model, "models": list(MODELS)},
            )
        await self.program()
        return model_info_of(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Claude Code's models, once the program is found and signed in. Raises :class:`ClaudeCodeMissing` before,
        which the models page shows as the usual state of a provider the owner does not use."""
        await self.program()
        return tuple(model_info_of(model) for model in MODELS)

    async def aclose(self) -> None:
        await self._bridge.aclose()
