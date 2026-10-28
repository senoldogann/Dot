"""Item 809, as its security review left it: in auto and yolo a read that touches neither the browser nor the screen
goes on while the owner holds the run, one that reads the page or the screen waits; a read-only tool is retried once,
an acting one is not; a failed action falls over to a reader of the page or the window, which takes its own arguments
and waits for the owner's hold like any step."""

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
        self.arguments: list[Any] = []

    async def authorize_recorded(self, _action: Any, arguments: Any, *_args: Any) -> Any:
        self.authorized += 1
        self.arguments.append(arguments)
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
        self.prepared: list[dict[str, Any]] = []

    async def prepare(self, arguments: dict[str, Any], _ctx: ToolContext) -> PreparedCall:
        self.prepared.append(arguments)
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


def _context(gate: _Gate, tools: dict[str, Any] | None = None, approvals: str = "auto") -> ToolContext:
    runtime = SimpleNamespace(peers=_Peers(), registry=SimpleNamespace(tools=tools or {}))
    return ToolContext(
        runtime=cast(Any, runtime),
        browser=cast(Any, None),
        run_id="run_test",
        agent_id=None,
        thread_id="thread_test",
        mode="task",
        approvals=cast(Any, approvals),
        questions="leave",
        workspace=Path("/tmp"),
        gate=cast(Any, gate),
        cancel=asyncio.Event(),
        output_chars=1000,
        facts=cast(Any, lambda: None),
    )


async def _run(
    name: str,
    results: list[ToolResult | BaseException],
    tools: dict[str, Any] | None = None,
    approvals: str = "auto",
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, _Gate, int]:
    gate = _Gate()
    tool = _Tool(name, results)
    call = ToolCall(id="call_1", name=name, arguments=arguments or {})
    checked = await _run_checked(call, cast(Any, tool), _context(gate, tools, approvals), cast(Any, gate))
    return checked.result, gate, tool.calls


@pytest.mark.parametrize("approvals", ["auto", "yolo"])
def test_a_read_away_from_the_browser_and_the_screen_skips_the_owner_hold_but_still_asks(approvals: str) -> None:
    result, gate, _calls = asyncio.run(_run("read_file", [ok("contents")], approvals=approvals))
    assert result.text == "contents"
    assert gate.waited == 0
    assert gate.authorized == 1


@pytest.mark.parametrize("name", ["browser_snapshot", "browser_read_text", "desktop_snapshot", "desktop_read"])
def test_a_read_of_the_page_or_the_screen_waits_for_the_owner_hold_even_in_yolo(name: str) -> None:
    """The security review: a page read while the owner types a password in it would hand the password to the model."""
    _result, gate, _calls = asyncio.run(_run(name, [ok("the page")], approvals="yolo"))
    assert gate.waited == 1


@pytest.mark.parametrize("approvals", ["ask", "supervised"])
def test_in_ask_and_supervised_every_tool_waits_for_the_owner_hold(approvals: str) -> None:
    _result, gate, _calls = asyncio.run(_run("read_file", [ok("contents")], approvals=approvals))
    assert gate.waited == 1


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


def test_a_failed_action_falls_over_to_a_reader_of_the_page_without_claiming_the_action() -> None:
    alternative = _Tool("browser_snapshot", [ok("The page as it is")])
    result, gate, calls = asyncio.run(
        _run(
            "browser_act",
            [fail("the click missed")],
            {"browser_snapshot": alternative},
            arguments={"steps": [{"action": "click", "ref": "e9"}]},
        )
    )
    assert calls == 1
    assert alternative.calls == 1
    # The reader takes its own arguments (the act's would be refused), is decided by the gate and waits for the hold.
    assert alternative.prepared == [{}]
    assert gate.authorized == 2 and gate.arguments[1] == {}
    assert gate.waited == 2
    assert "[fallback] browser_act failed" in result.text
    assert "browser_snapshot answered instead" in result.text
    assert "The original action was not performed" in result.text


def test_a_failed_desktop_action_reads_the_window_of_the_app_it_named() -> None:
    alternative = _Tool("desktop_snapshot", [ok("The window")])
    result, _gate, _calls = asyncio.run(
        _run(
            "desktop_act",
            [fail("the button was gone")],
            {"desktop_snapshot": alternative},
            arguments={"app": "Notes", "steps": [{"action": "click", "ref": "3"}]},
        )
    )
    assert alternative.prepared == [{"app": "Notes"}]
    assert "desktop_snapshot answered instead" in result.text
