"""A batch stops when a step brings another application to the front (item 749): the keys of the steps after it would
go to that application, which the model has not looked at. The steps and the macOS calls are stand-ins."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.desktop import memory
from dotagent.desktop.actions import Effect, StepEnv
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import AppInfo, Step


def _app(pid: int, name: str) -> AppInfo:
    return AppInfo(
        pid=pid, name=name, bundle_id=None, bundle_path=f"/Applications/{name}.app", kind="regular", frontmost=False
    )


_MAIL: AppInfo = _app(10, "Mail")
_SAFARI: AppInfo = _app(11, "Safari")
_FINDER: AppInfo = _app(12, "Finder")
_RUNNING: tuple[AppInfo, ...] = (_MAIL, _SAFARI, _FINDER)
_THREE: list[Step] = [
    Step(action="click", ref="a1"),
    Step(action="type", text="hello"),
    Step(action="key", key="return"),
]


def _env(tmp_path: Path, front: int | None) -> StepEnv:
    return StepEnv(
        app=_MAIL,
        app_element=None,  # type: ignore[arg-type]
        elements={},
        running=_RUNNING,
        displays=(),
        scale=1.0,
        source=None,  # type: ignore[arg-type]
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "stop",
        expected_front=front,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=None,  # type: ignore[arg-type]
    )


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fronts: list[int | None]) -> DesktopSession:
    """A session whose steps do nothing and whose front is the next of ``fronts`` each time it is asked."""
    config = load_default_config().desktop.model_copy(update={"show_pointer": False, "settle_ms": 0})
    monkeypatch.setattr("dotagent.desktop.session._STEP_GAP_S", 0.0)
    monkeypatch.setattr("dotagent.desktop.session.apps.frontmost_pid", lambda: fronts.pop(0))
    monkeypatch.setattr("dotagent.desktop.session.apps.list_apps", lambda: _RUNNING)
    return DesktopSession(config, tmp_path / "stop", no_browser_chrome)


async def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: int | None, fronts: list[int | None]
) -> tuple[list[str], str | None]:
    session: DesktopSession = _session(tmp_path, monkeypatch, fronts)

    async def done(env: StepEnv, step: Step, index: int, reviewer: object) -> Effect:
        return Effect(detail="done", pointer=None, opens_ui=False, switched_to=None)

    monkeypatch.setattr(session, "_run_step", done)
    try:
        batch = await session._run_steps(
            "chat",
            memory.fresh_thread(),
            _env(tmp_path, start),
            _THREE,
            None,  # type: ignore[arg-type]
        )
    finally:
        await session.close()
    return [report.outcome for report in batch.reports], batch.stopped


async def test_a_step_that_brings_another_application_forward_stops_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes, stopped = await _run(tmp_path, monkeypatch, _MAIL.pid, [_SAFARI.pid, _SAFARI.pid])
    assert outcomes == ["done", "skipped", "skipped"]
    assert stopped == "Safari came to the front after step 1; look at the screen before going on"


async def test_a_batch_goes_on_while_the_front_stays_and_when_its_own_application_comes_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes, stopped = await _run(tmp_path, monkeypatch, _MAIL.pid, [_MAIL.pid, _MAIL.pid])
    assert outcomes == ["done", "done", "done"] and stopped is None
    # Mail was behind Finder at the start and a click brings it forward: that is what the batch wanted.
    outcomes, stopped = await _run(tmp_path, monkeypatch, _FINDER.pid, [_MAIL.pid, _MAIL.pid])
    assert outcomes == ["done", "done", "done"] and stopped is None
    # Another application in front all along, or a front that cannot be told: nothing came forward.
    assert (await _run(tmp_path, monkeypatch, _FINDER.pid, [_FINDER.pid, _FINDER.pid]))[1] is None
    assert (await _run(tmp_path, monkeypatch, _MAIL.pid, [None, None]))[1] is None


async def test_another_application_coming_forward_while_a_background_one_is_worked_in_stops_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes, stopped = await _run(tmp_path, monkeypatch, _FINDER.pid, [_SAFARI.pid, _SAFARI.pid])
    assert outcomes == ["done", "skipped", "skipped"] and stopped is not None and "Safari" in stopped


async def test_an_activate_step_chooses_the_front_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The front changes after an ``activate`` step by its own doing; the batch goes on."""
    session: DesktopSession = _session(tmp_path, monkeypatch, [_SAFARI.pid, _SAFARI.pid, _SAFARI.pid])

    async def switched(env: StepEnv, step: Step, index: int, reviewer: object) -> Effect:
        return Effect(detail="Safari is in front", pointer=None, opens_ui=True, switched_to=_SAFARI)

    monkeypatch.setattr(session, "_run_step", switched)
    monkeypatch.setattr("dotagent.desktop.session.ax.app_element", lambda pid, timeout: None)
    try:
        batch = await session._run_steps(
            "chat",
            memory.fresh_thread(),
            _env(tmp_path, _MAIL.pid),
            _THREE[:2],
            None,  # type: ignore[arg-type]
        )
    finally:
        await session.close()
    assert batch.stopped is None and [report.outcome for report in batch.reports] == ["done", "done"]
