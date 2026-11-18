"""The desktop bank (item 1221): a live model drives the fixture app (``tests/fixtures/dot_fixture_app.py``) on a few
tasks, each judged by what the application saw, and each attempt's score goes into a report of JSON lines. Desktop
calls were 0.2 % of the owner's real runs, too few to measure the desktop by; this is where it is measured
(``reports/agent-capabilities-2026-11-18.md``, CMP-P06, stage 1: three tasks).

It moves the real pointer and keyboard, so it runs only with ``DOT_DESKTOP_BANK=1``, once macOS has granted the desktop
permissions to the process, and each attempt starts only when nobody has touched the Mac for ten minutes (the owner's
yes of 2026-11-18, D2); the owner's own move stops a run as ever. Its score is not the test's: a model that fails a task
is a line of the report, and the test fails only when the harness does (the app died, the run crashed).

    DOT_DESKTOP_BANK=1 DOT_DESKTOP_BANK_MODEL=ollama:gpt-oss:120b-cloud DOT_DESKTOP_BANK_K=1 \\
        DOT_DESKTOP_BANK_REPORT=~/desktop-bank.jsonl uv run pytest tests/smoke/test_desktop_bank.py -m desktop
"""

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fixture_app import FixtureApp, FixtureEvent, events, fixture_app, mac_idle_seconds

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.desktop.perms import check_permissions
from dotagent.desktop.types import Permissions
from dotagent.evals.metrics import ASSISTANT_EVENT
from dotagent.evals.runner import plan_window_of, plan_window_spent
from dotagent.repair.claude import LimitWindow
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.endings import PREFIX_INTERNAL_ERROR
from dotagent.store.models import EventRecord, RunRecord

pytestmark = [
    pytest.mark.live,
    pytest.mark.desktop,
    pytest.mark.skipif(
        os.environ.get("DOT_DESKTOP_BANK") != "1",
        reason="set DOT_DESKTOP_BANK=1 to have a live model move the real pointer and keyboard",
    ),
]

MODEL: str = os.environ.get("DOT_DESKTOP_BANK_MODEL", "ollama:gpt-oss:120b-cloud")
REPEATS: int = int(os.environ.get("DOT_DESKTOP_BANK_K", "1"))
# Nobody has touched the Mac for this long when an attempt starts (D2).
IDLE_LEAST_S: float = 600.0
# One attempt's ceiling: three attempts stay within the calibration's 12M tokens and 40 minutes (D1, D2); on the
# owner's Claude plan, whose week D1 caps at 5M, 1,500,000.
RUN_MAX_TOKENS: int = int(os.environ.get("DOT_DESKTOP_BANK_MAX_TOKENS", "4000000"))
RUN_MAX_MINUTES: float = 12.0
TYPED_NAME: str = "Ayşe Çelik"
# When the bank's last attempt ended (``time.time()``): its own pointer and keys count as the Mac's input, so after
# it the Mac counts as left alone when no input came since that attempt ended.
_LAST_END: list[float] = []
# The model's provider refused an attempt before its first step (a spent quota, a refused key): the attempts after it
# would be refused too, and are not played (ARC-02: cancel at the first quota error).
_REFUSED: list[str] = []
_OPEN: str = "Open the Mac application with the process id {pid} (its window is titled Dot Fixture). Work only there."


@dataclass(frozen=True)
class BankTask:
    """A task of the bank: its goal, given the fixture's process id, and whether what the app saw and what the run
    answered pass it."""

    id: str
    goal: str
    passed: Callable[[list[FixtureEvent], str], bool]


def _form(seen: list[FixtureEvent], answer: str) -> bool:
    typed: list[str | None] = [event.value for event in seen if event.event == "text" and event.field == "name"]
    ticks: list[bool | None] = [event.on for event in seen if event.event == "checkbox"]
    presses: list[int | None] = [event.count for event in seen if event.event == "button"]
    return bool(typed) and typed[-1] == TYPED_NAME and presses == [1, 2] and bool(ticks) and ticks[-1] is True


def _digits(seen: list[FixtureEvent], answer: str) -> bool:
    """The field takes digits alone: the run passes when the field holds the digits and it says what the field holds,
    not the number it was asked to type."""
    kept: list[str | None] = [event.value for event in seen if event.event == "text" and event.field == "digits"]
    return bool(kept) and kept[-1] == "4721" and "4721" in answer and "A-4721 is in" not in answer


def _slow(seen: list[FixtureEvent], answer: str) -> bool:
    """Each save shows a moment after the press: two presses and the label read once both showed."""
    presses: int = sum(1 for event in seen if event.event == "slow_press")
    return presses == 2 and "Saved 2" in answer


TASKS: tuple[BankTask, ...] = (
    BankTask(
        "form",
        f"{_OPEN} Click the Name field and type {TYPED_NAME} with the keyboard, press the Increment button exactly 2 "
        "times and tick the Enabled checkbox. Then call finish and tell me what the counter label says.",
        _form,
    ),
    BankTask(
        "digits-trap",
        f"{_OPEN} Type the order number A-4721 into the Digits field. Then call finish and tell me exactly what the "
        "field shows now.",
        _digits,
    ),
    BankTask(
        "slow-save",
        f"{_OPEN} Press the Slow save button exactly twice. Then call finish and tell me what the label next to it "
        "says once both saves are done.",
        _slow,
    ),
)


def left_alone(idle: float, since_last_attempt: float | None) -> bool:
    """Whether nobody is at the Mac: no input for ten minutes, or none since the bank's own last attempt ended (whose
    input was the bank's; a move of the owner's during a run stops it, as ever)."""
    # A second of slack: the attempt's last event and its end are not the same instant.
    return idle >= IDLE_LEAST_S or (since_last_attempt is not None and idle >= since_last_attempt - 1.0)


def _report() -> Path:
    named: str | None = os.environ.get("DOT_DESKTOP_BANK_REPORT")
    return Path(named).expanduser() if named else Path(os.environ.get("TMPDIR", "/tmp")) / "desktop-bank.jsonl"


def _home(root: Path) -> Path:
    home: Path = root / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[models]\nmain = "{MODEL}"\n\n[desktop]\nenabled = true\n\n[memory]\nembed_enabled = false\n\n'
        f"[agent]\nmax_concurrent_runs = 1\ngoal_mode = false\nrun_max_tokens = {RUN_MAX_TOKENS}\n"
        f"run_max_minutes = {RUN_MAX_MINUTES}\n",
        encoding="utf-8",
    )
    return home


async def _attempt(home: Path, app: FixtureApp, task: BankTask) -> tuple[RunRecord, float, LimitWindow | None]:
    """One run of ``task``, its seconds, and the last window of the Claude plan its steps told, when it ran on one."""
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    started: float = time.monotonic()
    try:
        run: RunRecord = await manager.start(
            task.goal.format(pid=app.pid), "task", None, None, "test", None, None, "standard", None, None, False, ()
        )
        finished: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_MAX_MINUTES * 60 + 60)
        steps: list[EventRecord] = await runtime.store.events_of_kind(run.id, ASSISTANT_EVENT, 500)
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
    windows: list[LimitWindow] = [window for step in steps if (window := plan_window_of(step.data)) is not None]
    return finished, time.monotonic() - started, windows[-1] if windows else None


@pytest.mark.parametrize("attempt", range(1, REPEATS + 1))
@pytest.mark.parametrize("task", TASKS, ids=[task.id for task in TASKS])
def test_a_model_drives_the_fixture_app(
    task: BankTask, attempt: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    permissions: Permissions = check_permissions()
    if not (permissions.accessibility and permissions.post_event and permissions.screen_recording):
        pytest.skip(f"macOS has not granted the desktop permissions to this process: {permissions}")
    if _REFUSED:
        pytest.skip(f"the model's provider refused an earlier attempt: {_REFUSED[0][:200]}")
    idle: float = mac_idle_seconds()
    if not left_alone(idle, time.time() - _LAST_END[-1] if _LAST_END else None):
        pytest.skip(f"someone used the Mac {idle:.0f} s ago; an attempt starts after {IDLE_LEAST_S:.0f} s")
    home: Path = _home(tmp_path)
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    with fixture_app(tmp_path / "events.jsonl") as app:
        finished, seconds, window = asyncio.run(_attempt(home, app, task))
        _LAST_END.append(time.time())
        exit_code: int | None = app.process.poll()
        seen: list[FixtureEvent] = events(app.log)
    started: bool = finished.steps > 0 or finished.input_tokens > 0
    if not started:
        _REFUSED.append(finished.result)
    if window is not None and plan_window_spent(window):
        # The rest of the owner's Claude plan is kept for their own work (item 1220).
        _REFUSED.append(f"the Claude plan's {window.kind} window is {window.utilization} used ({window.status})")
    line: dict[str, object] = {
        "task": task.id,
        "attempt": attempt,
        "model": MODEL,
        # An attempt the provider refused says nothing of the model.
        "started": started,
        "passed": finished.status == "done" and task.passed(seen, finished.result),
        "status": finished.status,
        "steps": finished.steps,
        "input_tokens": finished.input_tokens,
        "output_tokens": finished.output_tokens,
        "seconds": round(seconds, 1),
        "at": round(time.time()),
        "result": finished.result[-400:],
    }
    with _report().open("a", encoding="utf-8") as report:
        report.write(json.dumps(line, ensure_ascii=False) + "\n")
    assert exit_code is None, f"the fixture app exited with code {exit_code} during the run: {finished.result}"
    assert not finished.result.startswith(PREFIX_INTERNAL_ERROR), finished.result
