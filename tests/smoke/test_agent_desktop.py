"""The agent operates a Mac application end to end: a live model reads the fixture window and drives it.

The fixture app (``tests/fixtures/dot_fixture_app.py``) writes one JSON line per event it receives, so the check is what
the application saw and not what the model says about it. The test uses the real local Ollama server with a cloud model
and moves the real pointer and keyboard of this Mac, so it only runs when ``DOT_DESKTOP_TESTS=1``, macOS has granted
Accessibility, input events and screen recording to the process that runs pytest, and the model is listed.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.desktop.perms import check_permissions
from dotagent.desktop.types import Permissions
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord

pytestmark = [
    pytest.mark.live,
    pytest.mark.desktop,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.environ.get("DOT_DESKTOP_TESTS") != "1",
        reason="set DOT_DESKTOP_TESTS=1 to move the real pointer and keyboard",
    ),
]

OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
MODEL_REF: str = f"ollama:{MODEL}"
RUN_TIMEOUT_S: float = 300.0
READY_TIMEOUT_S: float = 15.0
TYPED_NAME: str = "Ayşe Çelik"
PRESSES: int = 2
_FIXTURE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_fixture_app.py"


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {MODEL}")


class FixtureEvent(BaseModel):
    """One line of the fixture's event log; only the fields the checks read."""

    model_config = ConfigDict(extra="ignore")

    event: str
    field: str | None = None
    value: str | None = None
    count: int | None = None
    on: bool | None = None
    pid: int | None = None


@dataclass(frozen=True)
class FixtureApp:
    pid: int
    log: Path
    process: subprocess.Popen[bytes]


def _events(log: Path) -> list[FixtureEvent]:
    # The app may be in the middle of writing its last line; only lines that end in a newline are complete.
    complete: list[str] = log.read_text(encoding="utf-8").split("\n")[:-1]
    return [FixtureEvent.model_validate(json.loads(line)) for line in complete if line.strip()]


def _wait_until_ready(process: subprocess.Popen[bytes], log: Path) -> int:
    deadline: float = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        assert process.poll() is None, f"the fixture app exited with code {process.returncode}"
        if log.exists():
            for event in _events(log):
                if event.event == "ready" and event.pid is not None:
                    return event.pid
        time.sleep(0.1)
    raise AssertionError("the fixture app did not become ready")


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FixtureApp]:
    permissions: Permissions = check_permissions()
    if not (permissions.accessibility and permissions.post_event and permissions.screen_recording):
        pytest.skip(f"macOS has not granted the desktop permissions to this process: {permissions}")
    log: Path = tmp_path_factory.mktemp("fixture") / "events.jsonl"
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, str(_FIXTURE), str(log)])
    try:
        yield FixtureApp(pid=_wait_until_ready(process, log), log=log, process=process)
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        f'[models]\nmain = "{MODEL_REF}"\n\n[desktop]\nenabled = true\n\n[agent]\nmax_concurrent_runs = 1\n',
        encoding="utf-8",
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@requires_model
async def test_the_agent_types_presses_and_ticks_in_a_mac_application(runtime: Runtime, app: FixtureApp) -> None:
    manager: RunManager = RunManager(runtime)
    goal: str = (
        f"Open the Mac application with the process id {app.pid} (its window is titled Dot Fixture). Click the Name "
        f"field and type {TYPED_NAME} with the keyboard, press the Increment button exactly {PRESSES} times and tick "
        "the Enabled checkbox. Work only in that application. Then call finish and tell me what the counter label says."
    )
    try:
        run: RunRecord = await manager.start(goal, "task", None, None, "test", None, None, "standard", None, None, ())
        finished: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_TIMEOUT_S)
    finally:
        await manager.shutdown()
    exit_code: int | None = app.process.poll()
    assert exit_code is None, f"the fixture app exited with code {exit_code} during the run: {finished.result}"
    assert finished.status == "done", finished.result
    seen: list[FixtureEvent] = _events(app.log)
    typed: list[str | None] = [event.value for event in seen if event.event == "text" and event.field == "name"]
    assert typed
    assert typed[-1] == TYPED_NAME, typed
    assert [event.count for event in seen if event.event == "button"] == list(range(1, PRESSES + 1))
    ticks: list[bool | None] = [event.on for event in seen if event.event == "checkbox"]
    assert ticks
    assert ticks[-1] is True, ticks
