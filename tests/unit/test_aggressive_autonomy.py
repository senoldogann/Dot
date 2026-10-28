"""A read-only tool may skip the owner's hold and be retried once; an acting tool may not (item 809)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.agent.executor import _run_checked
from dotagent.core.errors import RunCancelled
from dotagent.llm.types import ToolCall, ToolSpec
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import PreparedCall, ToolResult, fail, ok
from dotagent.tools.context import ToolContext


class _Peers:
    def conflict(self, *_args: Any) -> None:
        return None

    def take(self, *_args: Any) -> None:
        return None

    def release(self, *_args: Any) -> None:
        return None

    def saw(self, *_args: Any) -> None:
        return None


class _Gate:
    def __init__(self) -> None:
        self.waited: int = 0
        self.authorized: int = 0

    async def authorize_recorded(self, *_args: Any) -> Any:
        self.authorized += 1
        return SimpleNamespace(note="", decision=None)

    async def wait_free(self) -> float:
        self.waited += 1
        return 0.0

    def observe(self, *_args: Any) -> None:
        return None


class _Tool:
    def __init__(self, name: str, results: list[ToolResult | BaseException]) -> None:
        self.spec = ToolSpec(name=name, description="", parameters={"type": "object", "properties": {}})
        self.timeout_s = 1.0
        self._results = results
        self.calls: int = 0

    async def prepare(self, _arguments: dict[str, Any], _ctx: ToolContext) -> PreparedCall:
        action = ActionInfo(
            tool=self.spec.name,
            kind="browser_click" if self.spec.name == "browser_act" else "read",
            risk="safe",
            summary="",
            target="",
            flags=(),
        )

        async def run(_ctx: ToolContext) -> ToolResult:
            self.calls += 1
            result = self._results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

        return PreparedCall(action=action, run=run)


def _context(gate: _Gate, tools: dict[str, Any] | None = None) -> ToolContext:
    runtime = SimpleNamespace(peers=_Peers(), registry=SimpleNamespace(tools=tools or {}))
    return ToolContext(
        runtime=cast(Any, runtime),
        browser=cast(Any, None),
        run_id="run_test",
        agent_id=None,
        thread_id="thread_test",
        mode="auto",
        approvals="auto",
        questions="leave",
        workspace=Path("/tmp"),
        gate=cast(Any, gate),
        cancel=asyncio.Event(),
        output_chars=1000,
        facts=cast(Any, lambda: None),
    )


async def _run(
    name: str, results: list[ToolResult | BaseException], tools: dict[str, Any] | None = None
) -> tuple[ToolResult, _Gate, int]:
    gate = _Gate()
    tool = _Tool(name, results)
    call = ToolCall(id="call_1", name=name, arguments={})
    checked = await _run_checked(call, cast(Any, tool), _context(gate, tools), cast(Any, gate))
    return checked.result, gate, tool.calls


def test_a_read_only_tool_skips_the_owner_hold_but_still_asks_for_approval() -> None:
    result, gate, _calls = asyncio.run(_run("read_file", [ok("contents")]))
    assert result.text == "contents"
    assert gate.waited == 0
    assert gate.authorized == 1


def test_an_acting_tool_waits_for_the_owner_hold() -> None:
    _result, gate, _calls = asyncio.run(_run("browser_act", [ok("clicked")]))
    assert gate.waited == 1
    assert gate.authorized == 1


def test_a_read_only_tool_is_retried_once_after_a_failure() -> None:
    result, _gate, calls = asyncio.run(_run("read_file", [fail("temporarily unavailable"), ok("contents")]))
    assert result.text == "contents"
    assert calls == 2


def test_an_acting_tool_is_not_retried() -> None:
    result, _gate, calls = asyncio.run(_run("browser_act", [fail("click missed")]))
    assert result.is_error
    assert calls == 1


def test_a_cancelled_run_is_not_retried() -> None:
    with pytest.raises(RunCancelled):
        asyncio.run(_run("read_file", [RunCancelled("stopped", {})]))


def test_a_failed_action_falls_over_to_its_diagnostic_reader_without_claiming_the_action() -> None:
    alternative = _Tool("browser_find", [ok("Found the Save button")])
    result, gate, calls = asyncio.run(_run("browser_act", [fail("the click missed")], {"browser_find": alternative}))
    assert calls == 1
    assert alternative.calls == 1
    assert gate.authorized == 2
    assert "[fallback] browser_act failed" in result.text
    assert "browser_find answered instead" in result.text
    assert "The original action was not performed" in result.text
