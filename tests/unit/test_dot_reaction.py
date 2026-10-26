"""Dot reacts to the owner's message (item 664): the ``react`` tool puts 👍 or 👀 on it as an event the conversation
draws, once a run; a second reaction, a run another run handed its work to, and any other emoji are refused, and
nothing is put on the message then."""

from types import SimpleNamespace

import pytest

from dotagent.core.errors import ToolInputError
from dotagent.core.types import JsonObject
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.meta import DOT_REACTION_EVENT, DOT_REACTIONS, REACT_TOOL, build_meta_tools


class _Store:
    """The store's side of react: the run, with the run it was handed its work by, and the reactions it put."""

    def __init__(self, parent_run_id: str | None) -> None:
        self.parent_run_id: str | None = parent_run_id
        self.reactions: list[JsonObject] = []

    async def get_run(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=run_id, parent_run_id=self.parent_run_id)

    async def events_of_kind(self, run_id: str, kind: str, limit: int) -> list[JsonObject]:
        return self.reactions[-limit:] if kind == DOT_REACTION_EVENT else []


class _Bus:
    def __init__(self, store: _Store) -> None:
        self.store: _Store = store

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> None:
        assert kind == DOT_REACTION_EVENT and persist
        self.store.reactions.append(data)


def _context(parent_run_id: str | None = None) -> SimpleNamespace:
    store: _Store = _Store(parent_run_id)
    return SimpleNamespace(run_id="run_1", runtime=SimpleNamespace(store=store, bus=_Bus(store)))


async def _react(ctx: SimpleNamespace, reaction: str) -> ToolResult:
    tool: RegisteredTool = next(tool for tool in build_meta_tools() if tool.spec.name == REACT_TOOL)
    prepared: PreparedCall = await tool.prepare({"reaction": reaction}, ctx)  # type: ignore[arg-type]
    return await prepared.run(ctx)  # type: ignore[arg-type]


async def test_a_reaction_stands_on_the_message_once_a_run() -> None:
    assert DOT_REACTIONS == ("👍", "👀")
    ctx: SimpleNamespace = _context()
    first: ToolResult = await _react(ctx, "👍")
    assert not first.is_error and "👍 stands on their message" in first.text
    second: ToolResult = await _react(ctx, "👀")
    assert second.is_error and "reacted to their message already" in second.text
    assert ctx.runtime.store.reactions == [{"reaction": "👍"}]


async def test_a_run_handed_its_work_by_another_puts_no_reaction() -> None:
    ctx: SimpleNamespace = _context(parent_run_id="run_0")
    result: ToolResult = await _react(ctx, "👀")
    assert result.is_error and "no message of the owner's" in result.text
    assert ctx.runtime.store.reactions == []


async def test_only_the_two_reactions_are_taken() -> None:
    with pytest.raises(ToolInputError):
        await _react(_context(), "❤️")
