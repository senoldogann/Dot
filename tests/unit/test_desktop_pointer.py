"""Dot's own pointer (item 705): where it is drawn, what the session tells it, and that it never costs a step.

The session's steps are stood in for; the helper's protocol runs against a stand-in that draws nothing, and against one
that never answers. Nothing here looks at or draws on the screen.
"""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from dotagent.config import DesktopConfig, load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import memory, pointer
from dotagent.desktop.actions import Effect, StepEnv
from dotagent.desktop.pointer import Fades, PointerCommand, PointerOverlay, glide_s, read_command, window_origin
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import AppInfo, Point, ScreenshotView, Step


class _Pointer(PointerOverlay):
    """Keeps what it was told instead of drawing."""

    def __init__(self) -> None:
        super().__init__(enabled=False)
        self.told: list[tuple[str, ...]] = []

    async def warm(self) -> None:
        self.told.append(("warm",))

    async def move(self, x: float, y: float, click: bool) -> None:
        self.told.append(("move", f"{x:g}", f"{y:g}", str(click)))

    async def hide(self) -> None:
        self.told.append(("hide",))

    async def reveal(self) -> None:
        self.told.append(("reveal",))

    async def rest(self) -> None:
        self.told.append(("rest",))


_APP: AppInfo = AppInfo(
    pid=10, name="Notes", bundle_id="com.apple.Notes", bundle_path="/System/Applications/Notes.app", kind="regular",
    frontmost=True,
)  # fmt: skip


def _env(config: DesktopConfig, tmp_path: Path) -> StepEnv:
    return StepEnv(
        app=_APP,
        app_element=None,
        elements={},
        running=(_APP,),
        displays=(),
        scale=1.0,
        source=None,
        config=config,
        failsafe_file=tmp_path / "stop",
        expected_front=_APP.pid,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=None,  # type: ignore[arg-type]
    )


def _session(tmp_path: Path, shown: bool = True) -> tuple[DesktopSession, _Pointer]:
    told: _Pointer = _Pointer()
    config: DesktopConfig = load_default_config().desktop.model_copy(update={"show_pointer": shown})
    return DesktopSession(config, tmp_path / "stop", no_browser_chrome, told), told


def _steps_go(session: DesktopSession, monkeypatch: pytest.MonkeyPatch, where: Point | None) -> None:
    async def done(env: StepEnv, step: Step, index: int, reviewer: object) -> Effect:
        return Effect(detail="done", pointer=where, opens_ui=False, switched_to=None)

    monkeypatch.setattr(session, "_run_step", done)


async def test_a_click_moves_the_pointer_where_it_went_and_ripples_and_a_key_does_not_move_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, told = _session(tmp_path)
    env: StepEnv = _env(session._config, tmp_path)
    try:
        _steps_go(session, monkeypatch, Point(412.0, 233.5))
        await session._run_steps("chat", memory.fresh_thread(), env, [Step(action="click", ref="a3")], None)  # type: ignore[arg-type]
        await session._run_steps(
            "chat", memory.fresh_thread(), env, [Step(action="scroll", ref="a3", direction="down")], None
        )  # type: ignore[arg-type]
        _steps_go(session, monkeypatch, None)
        await session._run_steps("chat", memory.fresh_thread(), env, [Step(action="key", key="cmd+s")], None)  # type: ignore[arg-type]
        assert told.told == [("move", "412", "233.5", "True"), ("move", "412", "233.5", "False")]
        # The end of the chat's run fades it; a chat that moved nothing fades nothing.
        await session.end_of_run("other")
        await session.end_of_run("chat")
        assert told.told[-1] == ("rest",) and told.told.count(("rest",)) == 1
    finally:
        await session.close()


async def test_with_the_setting_off_no_pointer_is_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session, told = _session(tmp_path, shown=False)
    try:
        _steps_go(session, monkeypatch, Point(1.0, 2.0))
        await session._run_steps(
            "chat", memory.fresh_thread(), _env(session._config, tmp_path), [Step(action="click", ref="a3")], None
        )  # type: ignore[arg-type]
        assert told.told == []
    finally:
        await session.close()


async def test_a_capture_is_taken_with_the_pointer_hidden_and_it_comes_back_after_also_when_it_fails(
    tmp_path: Path,
) -> None:
    session, told = _session(tmp_path)
    seen: list[list[tuple[str, ...]]] = []

    def capture() -> ScreenshotView:
        seen.append(list(told.told))
        raise DesktopError("no screen", {})

    try:
        with pytest.raises(DesktopError):
            await session._without_pointer(capture)
        assert seen == [[("hide",)]]
        assert told.told == [("hide",), ("reveal",)]
    finally:
        await session.close()


def test_the_tip_of_the_arrow_is_on_the_point_and_a_glide_is_short_enough_to_follow() -> None:
    left, bottom = window_origin(100.0, 50.0, 900.0)
    # AppKit's window, from the bottom left: the tip sits TIP_PX in from its left and from its top.
    assert left + pointer.TIP_PX == 100.0
    assert 900.0 - (bottom + pointer.SIZE_PX - pointer.TIP_PX) == 50.0
    assert glide_s(0) == pytest.approx(pointer.GLIDE_MIN_S)
    assert glide_s(100) < glide_s(1500) <= pointer.GLIDE_MAX_S
    assert read_command('{"op": "move", "x": 1, "y": 2, "click": true}') == PointerCommand(
        op="move", x=1, y=2, click=True
    )
    assert read_command('{"op": "teleport"}') is None


def _stand_in(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """Have the overlay start ``script`` in place of the helper that draws."""
    real: Callable[..., object] = asyncio.create_subprocess_exec

    async def started(*args: str, **kwargs: object) -> object:
        return await real(sys.executable, "-c", script, **kwargs)  # type: ignore[operator]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", started)


async def test_a_hide_waits_for_its_own_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper answers each hide with its number; an answer that came late for an earlier hide is not this one's."""
    _stand_in(
        monkeypatch,
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    c = json.loads(line)\n"
        "    if c['op'] == 'hide':\n"
        "        print('hidden 0'); print('hidden %d' % c['number'], flush=True)\n",
    )
    overlay: PointerOverlay = PointerOverlay()
    try:
        await overlay.move(1.0, 2.0, False)
        await overlay.hide()
        await overlay.hide()
        assert overlay._process is not None and overlay._process.returncode is None
        assert overlay._enabled
    finally:
        await overlay.aclose()


async def test_a_helper_that_does_not_answer_is_ended_and_no_capture_waits_for_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stand_in(monkeypatch, "import sys\nfor line in sys.stdin:\n    pass\n")
    monkeypatch.setattr(pointer, "HIDE_WITHIN_S", 0.2)
    overlay: PointerOverlay = PointerOverlay()
    await overlay.move(1.0, 2.0, False)
    await overlay.hide()
    assert overlay._process is None and not overlay._enabled
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    started: float = loop.time()
    await overlay.hide()
    await overlay.move(3.0, 4.0, True)
    assert loop.time() - started < 0.05 and overlay._process is None
    await overlay.aclose()


async def test_a_helper_that_cannot_start_costs_only_the_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*args: str, **kwargs: object) -> object:
        raise OSError("no such program")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refused)
    overlay: PointerOverlay = PointerOverlay()
    await overlay.warm()
    await overlay.move(1.0, 2.0, True)
    await overlay.hide()
    await overlay.rest()
    await overlay.aclose()


def test_a_pointer_fades_after_its_last_move_also_when_a_capture_hid_and_revealed_it_meanwhile() -> None:
    """Item 705's review: a hide and a reveal around a screenshot called off the fade the move had set off, and the
    arrow stayed until the run ended. They are no moves: the fade of the last move still comes."""
    fades: Fades = Fades()
    first: int = fades.moved()
    # A screenshot right after the step: hidden, then revealed, which sets the fade off again for the same move.
    again: int | None = fades.revealed()
    assert again == first
    assert fades.due(first) and not fades.shown
    assert not fades.due(first)  # the second of the two fades for that move does nothing
    # A move after another drops the earlier one's fade; a rest fades the last at once.
    one: int = fades.moved()
    two: int = fades.moved()
    assert not fades.due(one) and fades.shown
    assert fades.due(two)
    # A pointer that does not show is not set off again by a reveal.
    assert fades.revealed() is None
