"""The owner's Claude plan as Dot's model picker lists it (item 1013), provider ``claude_code``: Claude Code's own model
aliases, ``opus`` and ``sonnet``, listed once the ``claude`` program is found and signed in. They answer Claude Code
alone, so a run on one goes to that program whole (:func:`dotagent.claude_code.run.run_in_claude_code`) and no single
call of Dot's reaches one: :meth:`ClaudeCodeProvider.complete` refuses. The program gets Dot's own tools, and only
those, through the provider's bridge (:mod:`dotagent.opencode.bridge`).

Whose plan it is is read from ``~/.claude.json``'s ``oauthAccount`` (:func:`dotagent.repair.claude.read_account`): an
address and an organisation, no secret. Dot never reads Claude Code's credentials nor its Keychain item."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

from dotagent.config import ClaudeCodeConfig
from dotagent.core.errors import ClaudeCodeMissing, ConfigError, ProviderRefusedError
from dotagent.core.paths import claude_code_command
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.opencode.bridge import ToolBridge
from dotagent.opencode.server import find_program
from dotagent.repair.claude import ClaudeAccount, claude_state, read_account

CLAUDE_CODE_PROVIDER: str = "claude_code"
# The models a run may name: Claude Code's own aliases for the newest of each family (measured on 2.1.318, where
# ``haiku`` is not one).
MODELS: tuple[str, ...] = ("opus", "sonnet")
NOT_INSTALLED: str = (
    "Claude Code is not installed on this Mac: install it (claude.com/claude-code) and sign in with your Claude plan "
    "by running 'claude' once in Terminal, or set [providers.claude_code] command to where it is"
)
NOT_SIGNED_IN: str = (
    "Claude Code is not signed in: run 'claude' once in Terminal and sign in with your Claude plan (Pro or Max)"
)


def model_info_of(model: str) -> ModelInfo:
    """What one of Claude Code's models offers Dot: it sees, calls tools and thinks; its window is Claude Code's to
    manage, so Dot names none, and its reasoning is Claude Code's own, so there is no level to pick."""
    return ModelInfo(
        id=model,
        vision=True,
        tools=True,
        thinking=True,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
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
        raise ProviderRefusedError(
            "The models of your Claude plan answer only as Claude Code: a run on one goes to Claude Code whole, and no"
            " single call of Dot's (a side question, a summary, a review) can use one; pick another model for it",
            {"provider": CLAUDE_CODE_PROVIDER, "purpose": request.session_key or ""},
        )

    async def model_info(self, model: str) -> ModelInfo:
        if model not in MODELS:
            raise ConfigError(
                "Claude Code offers Dot its opus and sonnet models only; pick one of them",
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
