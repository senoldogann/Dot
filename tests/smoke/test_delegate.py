"""Delegation end to end: a run hands part of its work to a run of its own, against the real store.

The runs use the real local Ollama server and a cloud model; the tests that need it are skipped when it is not there.
The manager is built with a single run slot, so a delegated run can only finish if it does not need a slot of its own.
"""

import asyncio
import secrets
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import RunConflict
from dotagent.core.paths import workspace_dir
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunMode, RunRecord, ThreadRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool

pytestmark = pytest.mark.asyncio(loop_scope="module")

OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
MODEL_REF: str = f"ollama:{MODEL}"
RUN_TIMEOUT_S: float = 240.0
CANCEL_TIMEOUT_S: float = 60.0
GONE_TIMEOUT_S: float = 15.0
DELEGATE_SOURCE: str = "delegate"
FENCE_OPENING: str = "<delegate_content"


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {MODEL}")


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        f'[models]\nmain = "{MODEL_REF}"\n\n[agent]\nmax_concurrent_runs = 1\n', encoding="utf-8"
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


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


async def _stored_run(runtime: Runtime, mode: RunMode, parent_run_id: str | None, source: str) -> RunRecord:
    """A run that only exists in the store: enough for the checks that happen before any model is involved."""
    thread: ThreadRecord = await runtime.store.create_thread("delegation test", source)
    return await runtime.store.create_run(
        thread.id, "goal", mode, MODEL_REF, parent_run_id, source, "medium", "ask", False, None
    )


def _secret_file() -> tuple[Path, str]:
    token: str = f"MARKER-{secrets.token_hex(6)}"
    path: Path = workspace_dir() / f"delegate-{secrets.token_hex(3)}.txt"
    path.write_text(token, encoding="utf-8")
    return path, token


def _delegating_goal(path: Path) -> str:
    return (
        f"Call the delegate tool exactly once, with mode task. Its goal must tell the sub-agent to read the file {path} "
        "and report the exact text in it. When it has answered, call finish with the text it reported."
    )


async def _children_of(runtime: Runtime, parent_id: str) -> list[RunRecord]:
    return [run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id]


async def _delegate_result(runtime: Runtime, run_id: str) -> str:
    """The text the delegating run was shown for its delegate call."""
    events: list[EventRecord] = await runtime.store.list_events(run_id, 0, 500)
    texts: list[str] = [
        str(event.data["text"]) for event in events if event.kind == "tool_result" and event.data["tool"] == "delegate"
    ]
    assert len(texts) == 1, events
    return texts[0]


async def _count_processes(marker: str) -> int:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "pgrep", "-f", marker, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return len(out.split())


async def _assert_gone(marker: str) -> None:
    deadline: float = time.monotonic() + GONE_TIMEOUT_S
    while await _count_processes(marker) > 0:
        assert time.monotonic() < deadline, f"a process with {marker} is still running"
        await asyncio.sleep(0.25)


async def _until_running(marker: str) -> None:
    deadline: float = time.monotonic() + CANCEL_TIMEOUT_S
    while await _count_processes(marker) == 0:
        assert time.monotonic() < deadline, f"no process with {marker} ever started"
        await asyncio.sleep(0.25)


async def _until_a_run_is_active(manager: RunManager) -> str:
    deadline: float = time.monotonic() + CANCEL_TIMEOUT_S
    while not (active := manager.active_run_ids()):
        assert time.monotonic() < deadline, "the delegated run never started"
        await asyncio.sleep(0.1)
    return active[0]


# ------------------------------------------------------------------------------------------ the rules


async def test_a_delegated_run_cannot_delegate_again(runtime: Runtime, manager: RunManager) -> None:
    parent: RunRecord = await _stored_run(runtime, "task", None, "test")
    child: RunRecord = await _stored_run(runtime, "task", parent.id, DELEGATE_SOURCE)
    with pytest.raises(RunConflict) as refused:
        await manager.delegate(child.id, "anything", "task", None)
    assert "cannot delegate further" in refused.value.message
    assert manager.active_run_ids() == []


async def test_a_research_run_only_delegates_research(runtime: Runtime, manager: RunManager) -> None:
    parent: RunRecord = await _stored_run(runtime, "research", None, "test")
    with pytest.raises(RunConflict) as refused:
        await manager.delegate(parent.id, "anything", "task", None)
    assert "only delegate research" in refused.value.message
    assert manager.active_run_ids() == []
    assert await _children_of(runtime, parent.id) == []


async def test_the_delegate_tool_is_known_to_every_mode_and_classified_as_safe(runtime: Runtime) -> None:
    tool: RegisteredTool = runtime.registry.tools["delegate"]
    assert tool.modes == ALL_MODES
    assert tool.parallel_safe


# ------------------------------------------------------------------------------------------ with a model


@requires_model
async def test_a_run_reads_a_file_through_a_delegated_run(runtime: Runtime, manager: RunManager) -> None:
    path, token = _secret_file()
    parent: RunRecord = await manager.start(
        _delegating_goal(path), "task", None, None, "test", None, None, None, None, ()
    )
    finished: RunRecord = await asyncio.wait_for(manager.wait(parent.id), RUN_TIMEOUT_S)
    children: list[RunRecord] = await _children_of(runtime, parent.id)
    assert finished.status == "done", finished
    assert token in finished.result
    assert len(children) == 1, children
    child: RunRecord = children[0]
    assert (child.status, child.source, child.mode, child.model) == ("done", DELEGATE_SOURCE, "task", MODEL_REF)
    assert child.thread_id != parent.thread_id
    assert token in child.result
    shown: str = await _delegate_result(runtime, parent.id)
    assert token in shown
    assert FENCE_OPENING not in shown
    assert manager.active_run_ids() == []


@requires_model
@pytest.mark.internet
async def test_a_delegated_run_starts_as_untrusted_as_its_parent(runtime: Runtime, manager: RunManager) -> None:
    path, token = _secret_file()
    goal: str = (
        "Do these in order. 1. Fetch https://www.iana.org/help/example-domains with web_fetch. 2. "
        f"{_delegating_goal(path)}"
    )
    parent: RunRecord = await manager.start(goal, "task", None, None, "test", None, None, None, None, ())
    finished: RunRecord = await asyncio.wait_for(manager.wait(parent.id), RUN_TIMEOUT_S)
    assert finished.status == "done", finished
    assert token in finished.result
    shown: str = await _delegate_result(runtime, parent.id)
    assert FENCE_OPENING in shown
    assert token in shown


@requires_model
async def test_stopping_the_waiting_caller_stops_the_delegated_run(runtime: Runtime, manager: RunManager) -> None:
    marker: str = f"sleep 47.{secrets.randbelow(900) + 100}"
    parent: RunRecord = await _stored_run(runtime, "task", None, "test")
    goal: str = f"Run the shell command `{marker}`, then call finish."
    waiting: asyncio.Task[object] = asyncio.create_task(manager.delegate(parent.id, goal, "task", None))
    child_id: str = await _until_a_run_is_active(manager)
    await _until_running(marker)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    stopped: RunRecord = await asyncio.wait_for(manager.wait(child_id), CANCEL_TIMEOUT_S)
    assert stopped.status == "cancelled", stopped
    assert stopped.parent_run_id == parent.id
    assert manager.active_run_ids() == []
    await _assert_gone(marker)
