"""The owner's Claude plan as Dot's model picker lists it (item 1013), provider ``claude_code``: the models the
``claude`` program itself lists (item 1367, :mod:`dotagent.claude_code.models`), once it is found and signed in. They
answer Claude Code alone, so a run on one goes to that program whole
(:func:`dotagent.claude_code.run.run_in_claude_code`), and a single question of Dot's (a review, the harness's
roles, item 1017) goes to it as one call with no tool of its own (:mod:`dotagent.claude_code.ask`); a call of a
conversation is refused. The program gets Dot's own tools, and only those, through the provider's bridge
(:mod:`dotagent.opencode.bridge`).

Whose plan it is is read from ``~/.claude.json``'s ``oauthAccount`` (:func:`dotagent.repair.claude.read_account`): an
address and an organisation, no secret. Dot never reads Claude Code's credentials nor its Keychain item."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

from dotagent.claude_code.ask import ask_claude
from dotagent.claude_code.models import PlanModel, find_model, plan_models
from dotagent.config import ClaudeCodeConfig
from dotagent.core.errors import ClaudeCodeMissing
from dotagent.core.paths import claude_code_command
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import ChatRequest, DeltaSink, Effort, ModelInfo, ModelReply
from dotagent.opencode.bridge import ToolBridge
from dotagent.opencode.server import find_program
from dotagent.repair.claude import ClaudeAccount, claude_state, read_account

CLAUDE_CODE_PROVIDER: str = "claude_code"
# How hard a run thinks, as Claude Code's `--effort` takes it (item 1014), lowest first.
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
# The settings that turn fast mode on, given to a run's program with `--settings`.
FAST_SETTINGS: str = '{"fastMode":true}'
NOT_INSTALLED: str = (
    "Claude Code is not installed on this Mac: install it (claude.com/claude-code) and sign in with your Claude plan "
    "by running 'claude' once in Terminal, or set [providers.claude_code] command to where it is"
)
NOT_SIGNED_IN: str = (
    "Claude Code is not signed in: run 'claude' once in Terminal and sign in with your Claude plan (Pro or Max)"
)


def model_info_of(model: PlanModel) -> ModelInfo:
    """What one of the plan's models offers Dot: it sees, calls tools and thinks; its window is Claude Code's to
    manage, so Dot names none; its reasoning levels are those Claude Code lists for it (item 1367)."""
    return ModelInfo(
        id=model.id,
        vision=True,
        tools=True,
        thinking=True,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=model.efforts,
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

    async def models(self) -> tuple[PlanModel, ...]:
        """The plan's models as the program lists them (item 1367), once it is found and signed in."""
        return await plan_models(await self.program(), self._login_env)

    async def _model(self, name: str) -> PlanModel:
        """The plan's model ``name`` names. One the program does not list is not refused (a chat or a schedule may
        name a model an older list offered, ``claude-opus-4-5``): it goes to the program, which answers on it or says
        why not, with Claude Code's levels and no fast mode."""
        found: PlanModel | None = find_model(await self.models(), name)
        if found is not None:
            return found
        return PlanModel(id=name, resolved=name, label=name, efforts=EFFORTS, fast=False)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        """A single question of Dot's (item 1017: a review, the harness's roles) answered by the program in one go,
        with no tool but the shape of its answer (:mod:`dotagent.claude_code.ask`); a run goes to it whole instead."""
        await self._model(request.model)
        level: Effort | None = effective_effort(EFFORTS, request.effort)
        return await ask_claude(
            await self.program(), request, self._login_env, request.model, EFFORTS[0] if level is None else level
        )

    async def model_info(self, model: str) -> ModelInfo:
        return model_info_of(await self._model(model))

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """The plan's models, once the program is found and signed in. Raises :class:`ClaudeCodeMissing` before,
        which the models page shows as the usual state of a provider the owner does not use."""
        return tuple(model_info_of(model) for model in await self.models())

    async def aclose(self) -> None:
        await self._bridge.aclose()
