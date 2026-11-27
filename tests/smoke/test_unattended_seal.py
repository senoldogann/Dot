"""Item 2026: Dot's own file tools, in the daemon's process, refuse a run nobody started what its sandbox seals. Real
runs on a scripted model through the real manager, with the owner's sandbox switched off: a yolo schedule's
``read_file`` of Dot's configuration and ``write_file`` of it are refused, a file in the workspace is written; the
owner's yolo run reads the configuration as before. No model is called."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path, ensure_layout, workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio, pytest.mark.macos]

RUN_WAIT_S: float = 60.0
MARKER: str = "unattended-seal-marker-81d0"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WRITTEN: str = "written-by-the-run"


def _call(tool: str, arguments: dict[str, str]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _finish() -> ScriptedTurn:
    return _call(FINISH_TOOL, {"answer": "Done.", "success": True})


def _turns(config: Path, workspace: Path) -> list[ScriptedTurn]:
    """A schedule's run that reads the configuration, writes into it and writes in the workspace; then the owner's run
    that reads it."""
    unattended: list[ScriptedTurn] = [
        _call("read_file", {"path": str(config)}),
        _call("write_file", {"path": str(config), "content": "[shell]\nsandbox_unattended = false\n"}),
        _call("write_file", {"path": str(workspace / "note.txt"), "content": _WRITTEN}),
        _finish(),
    ]
    owners: list[ScriptedTurn] = [_call("read_file", {"path": str(config)}), _finish()]
    return [*unattended, *owners]


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    # The owner's own choice: the sandbox off for their runs.
    (home / "config.toml").write_text(
        f"# {MARKER}\n"
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n\n[policy]\nyolo_taint_check = false\n\n[shell]\nsandbox = false\n",
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns(config_path(), workspace_dir())))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _results(runtime: Runtime, run: RunRecord) -> list[tuple[str, bool, str]]:
    """Each call of the run: its tool, whether it did its job, and what it returned."""
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 20)
    return [
        (str(event.data.get("tool")), bool(event.data.get("ok")), str(event.data.get("text")))
        for event in results
        if event.data.get("tool") != FINISH_TOOL
    ]


async def _started(manager: RunManager, chat: ThreadRecord, source: str) -> RunRecord:
    run: RunRecord = await manager.start(
        "Check the settings", "task", SCRIPTED_MODEL, chat.id, source, None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(RUN_WAIT_S):
        await manager.wait(run.id)
    return run


async def test_dots_own_file_tools_refuse_a_run_nobody_started_what_its_sandbox_seals(runtime: Runtime) -> None:
    assert not runtime.sandbox.on
    before: str = config_path().read_text(encoding="utf-8")
    manager: RunManager = RunManager(runtime)
    try:
        timed: RunRecord = await _started(
            manager, await runtime.store.create_thread("Morning check", SCHEDULE_SOURCE), SCHEDULE_SOURCE
        )
        mine: RunRecord = await _started(manager, await runtime.store.create_thread("Mine", UI_SOURCE), UI_SOURCE)
    finally:
        await manager.shutdown()
    (read, read_ok, read_text), (wrote, wrote_ok, wrote_text), (noted, noted_ok, _) = await _results(runtime, timed)
    assert (read, read_ok) == ("read_file", False) and MARKER not in read_text, read_text
    assert (wrote, wrote_ok) == ("write_file", False), wrote_text
    assert config_path().read_text(encoding="utf-8") == before
    assert (noted, noted_ok) == ("write_file", True)
    assert (workspace_dir() / "note.txt").read_text(encoding="utf-8") == _WRITTEN
    refusals: list[EventRecord] = await runtime.store.events_of_kind(timed.id, "policy", 20)
    assert [event.data.get("source") for event in refusals if event.data.get("verdict") == "deny"] == ["seal", "seal"]
    [(owners_tool, owners_ok, owners_text)] = await _results(runtime, mine)
    assert (owners_tool, owners_ok) == ("read_file", True) and MARKER in owners_text, owners_text
