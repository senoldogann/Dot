"""The seconds a call spends in its tool, which the tool's own limit holds (item 1406). A call's ``seconds`` hold more
than that (the gate before it, a hold of the run, the lessons after it), so ``browser_requests`` showing 102 s on a
limit of 45 did not say whether the limit was overrun. ``tool_result`` now carries ``ran_s`` beside ``waited_s``, and a
call whose tool ran past its limit by more than a few seconds, cut or not, is logged as ``tool_overran``.

One way a call outlives its limit is real, and pinned here with a stand-in of the Playwright driver: a call that waits
for Dot's own Chrome to start and is cut at its limit goes on to wait for the launch to end and for the Chrome it
started to be closed, so that no Chrome is left holding the profile (item 144, ``test_browser_profile.py``). The wait is
the launch's own, 30 s at most by Playwright's default, and it stands inside the call's clock."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

from dotagent.agent import executor
from dotagent.agent.executor import _run_checked
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.config import BrowserConfig, load_default_config
from dotagent.llm.types import ToolCall, ToolSpec
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import PreparedCall, ToolResult, ok
from dotagent.tools.context import ToolContext

LIMIT_S: float = 0.2
GRACE_S: float = 0.1
# How long the stand-in driver takes to start a Chrome.
LAUNCH_S: float = 0.8


class _Peers:
    def conflict(self, *_args: Any) -> None:
        return None

    def take(self, *_args: Any) -> None:
        return None

    def saw(self, *_args: Any) -> None:
        return None


class _Gate:
    """The run's gate; a question a running tool asks it takes ``asking_s``."""

    def __init__(self, asking_s: float = 0.0) -> None:
        self.asking_s: float = asking_s

    async def authorize_recorded(self, *_args: Any) -> Any:
        return SimpleNamespace(note="", decision=None)

    async def authorize(self, _info: Any) -> str:
        await asyncio.sleep(self.asking_s)
        return ""

    def add_instruction(self, _text: str) -> None:
        return None

    async def wait_free(self) -> float:
        return 0.0

    def observe(self, *_args: Any) -> None:
        return None


class _Tool:
    def __init__(self, name: str, body: Callable[[ToolContext], Awaitable[ToolResult]]) -> None:
        self.spec = ToolSpec(name=name, description="", parameters={"type": "object", "properties": {}})
        self.timeout_s = LIMIT_S
        self._body = body

    async def prepare(self, _arguments: dict[str, Any], _ctx: ToolContext) -> PreparedCall:
        action = ActionInfo(tool=self.spec.name, kind="read", risk="safe", summary="", target="", flags=())
        return PreparedCall(action=action, run=self._body)


def _context(gate: _Gate) -> ToolContext:
    runtime = SimpleNamespace(peers=_Peers(), registry=SimpleNamespace(tools={}))
    return ToolContext(
        runtime=cast(Any, runtime),
        browser=cast(Any, None),
        run_id="run_test",
        agent_id=None,
        thread_id="thread_test",
        mode="task",
        approvals="auto",
        questions="leave",
        workspace=Path("/tmp"),
        gate=cast(Any, gate),
        cancel=asyncio.Event(),
        output_chars=1000,
        facts=cast(Any, lambda: None),
    )


async def _call(
    body: Callable[[ToolContext], Awaitable[ToolResult]], gate: _Gate
) -> tuple[executor._Checked, list[dict[str, Any]]]:
    tool = _Tool("browser_requests", body)
    with capture_logs() as logs:
        checked = await _run_checked(
            ToolCall(id="call_1", name="browser_requests", arguments={}),
            cast(Any, tool),
            _context(gate),
            cast(Any, gate),
        )
    return checked, [entry for entry in logs if entry["event"] == "tool_overran"]


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_OVERRUN_GRACE_S", GRACE_S)


async def test_a_tool_that_holds_its_cut_call_past_the_limit_is_logged_and_its_own_seconds_say_so() -> None:
    async def holds_the_cut(_ctx: ToolContext) -> ToolResult:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            # What a browser call did while it was cut: wait for the Chrome that was starting to be closed.
            await asyncio.sleep(0.6)
            raise
        return ok("never")

    checked, overran = await _call(holds_the_cut, _Gate())
    assert checked.result.is_error and "timed out" in checked.result.text
    assert checked.ran_s > LIMIT_S + GRACE_S
    assert len(overran) == 1, overran
    assert overran[0]["tool"] == "browser_requests" and overran[0]["run_id"] == "run_test"
    assert overran[0]["limit_s"] == LIMIT_S and overran[0]["took_s"] > LIMIT_S + GRACE_S


async def test_a_tool_cut_at_its_limit_is_not_logged_as_an_overrun() -> None:
    async def never_ends(_ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(30)
        return ok("never")

    checked, overran = await _call(never_ends, _Gate())
    assert checked.result.is_error and "timed out" in checked.result.text
    assert LIMIT_S <= checked.ran_s < LIMIT_S + GRACE_S
    assert overran == []


async def test_the_time_a_running_tool_waits_for_the_owner_is_not_its_own() -> None:
    async def asks_then_answers(ctx: ToolContext) -> ToolResult:
        await ctx.gate.authorize(
            ActionInfo(tool="browser_requests", kind="read", risk="safe", summary="", target="", flags=())
        )
        return ok("answered")

    # The question takes longer than the limit and the grace together: the tool's clock stood still meanwhile.
    checked, overran = await _call(asks_then_answers, _Gate(asking_s=LIMIT_S + GRACE_S + 0.3))
    assert checked.result.text == "answered"
    assert checked.waited_s >= LIMIT_S + GRACE_S + 0.3
    assert checked.ran_s < GRACE_S
    assert overran == []


async def test_a_wait_a_tool_makes_off_its_clock_is_not_its_own() -> None:
    """A batch of desktop steps that waits for the owner to step away (item 1890) is not cut at the tool's limit."""

    async def waits_for_the_owner(ctx: ToolContext) -> ToolResult:
        await ctx.off_clock(asyncio.sleep(LIMIT_S + GRACE_S + 0.3))
        return ok("went on")

    checked, overran = await _call(waits_for_the_owner, _Gate())
    assert checked.result.text == "went on"
    assert checked.waited_s >= LIMIT_S + GRACE_S + 0.3
    assert checked.ran_s < GRACE_S
    assert overran == []


class _Context:
    """The context of a Chrome that started: it is told what a session tells one and records its closing."""

    def __init__(self) -> None:
        self.closed: bool = False
        self.pages: list[object] = []

    async def route(self, *_args: object, **_kwargs: object) -> None:
        return None

    def set_default_timeout(self, _ms: float) -> None:
        return None

    def set_default_navigation_timeout(self, _ms: float) -> None:
        return None

    def on(self, *_args: object) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _Chromium:
    def __init__(self) -> None:
        self.started: list[_Context] = []

    async def launch_persistent_context(self, **_kwargs: object) -> _Context:
        await asyncio.sleep(LAUNCH_S)
        context: _Context = _Context()
        self.started.append(context)
        return context


class _Driver:
    def __init__(self) -> None:
        self.chromium: _Chromium = _Chromium()

    async def stop(self) -> None:
        return None


def _session(tmp_path: Path, driver: _Driver) -> BrowserSession:
    config: BrowserConfig = load_default_config().browser.model_copy(update={"headless": True, "mode": "own"})
    (tmp_path / "profile").mkdir()
    (tmp_path / "downloads").mkdir()
    session: BrowserSession = BrowserSession(
        config, load_default_config().server, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    # The driver is given, so that nothing starts a Node process: ``_driver`` returns what the session holds.
    session._playwright = cast(Any, driver)
    return session


async def test_a_call_cut_while_chrome_starts_waits_for_the_launch_and_leaves_no_chrome_behind(tmp_path: Path) -> None:
    """Item 144's contract, and what it costs: the cut call ends once the Chrome the launch started is closed, which is
    after the launch's own time, not at the call's limit."""
    driver: _Driver = _Driver()
    session: BrowserSession = _session(tmp_path, driver)
    started: float = time.monotonic()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(LIMIT_S / 2):
            await session._ensure_context()
    ended_after: float = time.monotonic() - started
    assert ended_after >= LAUNCH_S * 0.9, f"the cut call took {ended_after:.2f} s on a limit of {LIMIT_S / 2} s"
    assert [context.closed for context in driver.chromium.started] == [True]
    assert session._context is None
    await session.aclose()


async def test_a_browser_call_held_for_the_launch_it_gave_up_is_told_as_an_overrun_with_its_numbers(
    tmp_path: Path,
) -> None:
    """The audit's call (`browser_requests`, 102 s on a limit of 45) as it would show now: the tool's own seconds are
    past its limit by the launch's time, and the log names the tool, the limit and the seconds."""
    driver: _Driver = _Driver()
    session: BrowserSession = _session(tmp_path, driver)

    async def waits_for_chrome(_ctx: ToolContext) -> ToolResult:
        await session._ensure_context()
        return ok("never: the limit cuts it first")

    checked, overran = await _call(waits_for_chrome, _Gate())
    await session.aclose()
    assert checked.result.is_error and "timed out" in checked.result.text
    assert checked.ran_s >= LAUNCH_S * 0.9 > LIMIT_S + GRACE_S
    assert len(overran) == 1, overran
    assert overran[0]["tool"] == "browser_requests" and overran[0]["limit_s"] == LIMIT_S
    assert overran[0]["took_s"] >= LAUNCH_S * 0.9
