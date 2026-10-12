"""The read-only calls of one step run at once; one that fails outside its tool ends the others of its group before
the failure goes on, so none of them outlives the step (item 144)."""

import asyncio

import pytest

from dotagent.agent.executor import ToolOutcome, _together
from dotagent.core.errors import StoreError
from dotagent.llm.types import ToolCall
from dotagent.tools.base import ok

_WAIT_S: float = 30.0
_LIMIT_S: float = 5.0


async def test_a_call_that_fails_outside_its_tool_ends_the_other_calls_of_its_group() -> None:
    ended: list[str] = []

    async def failing() -> ToolOutcome:
        await asyncio.sleep(0)
        raise StoreError("the store could not keep the result", {})

    async def waiting() -> ToolOutcome:
        try:
            await asyncio.sleep(_WAIT_S)
        except asyncio.CancelledError:
            ended.append("cancelled")
            raise
        call: ToolCall = ToolCall(id="call_2", name="read_file", arguments={})
        return ToolOutcome(call=call, result=ok("read"), seconds=_WAIT_S, action=None)

    async with asyncio.timeout(_LIMIT_S):
        with pytest.raises(StoreError):
            await _together([failing(), waiting()])
    assert ended == ["cancelled"]
