"""The agent operates a Mac application end to end: a live model reads the fixture window and drives it.

The fixture app (``tests/fixtures/dot_fixture_app.py``) writes one JSON line per event it receives, so the check is what
the application saw and not what the model says about it. The test uses the real local Ollama server with a cloud model
and moves the real pointer and keyboard of this Mac, so it only runs when ``DOT_DESKTOP_TESTS=1``, macOS has granted
Accessibility, input events and screen recording to the process that runs pytest, and the model is listed.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fixture_app import FixtureApp, FixtureEvent, events, fixture_app

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
TYPED_NAME: str = "Ayşe Çelik"
PRESSES: int = 2


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {MODEL}")


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FixtureApp]:
    permissions: Permissions = check_permissions()
    if not (permissions.accessibility and permissions.post_event and permissions.screen_recording):
        pytest.skip(f"macOS has not granted the desktop permissions to this process: {permissions}")
    with fixture_app(tmp_path_factory.mktemp("fixture") / "events.jsonl") as started:
        yield started


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
        run: RunRecord = await manager.start(
            goal, "task", None, None, "test", None, None, "standard", None, None, False, ()
        )
        finished: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_TIMEOUT_S)
    finally:
        await manager.shutdown()
    exit_code: int | None = app.process.poll()
    assert exit_code is None, f"the fixture app exited with code {exit_code} during the run: {finished.result}"
    assert finished.status == "done", finished.result
    seen: list[FixtureEvent] = events(app.log)
    typed: list[str | None] = [event.value for event in seen if event.event == "text" and event.field == "name"]
    assert typed
    assert typed[-1] == TYPED_NAME, typed
    assert [event.count for event in seen if event.event == "button"] == list(range(1, PRESSES + 1))
    ticks: list[bool | None] = [event.on for event in seen if event.event == "checkbox"]
    assert ticks
    assert ticks[-1] is True, ticks
