"""Items 2026 and 2027: what a run nobody started keeps beyond its processes' sandbox. Real runs on a scripted model
through the real manager, with the owner's sandbox switched off:

- 2026: Dot's own file tools, in the daemon's process, refuse a run nobody started what its sandbox seals: a yolo
  schedule's ``read_file`` of Dot's configuration and ``write_file`` of it are refused, a file in the workspace is
  written; the owner's yolo run reads the configuration as before.
- 2027: what Dot resumes on its own (a run the daemon resumed as it started, a helper whose parent ended) is marked as
  the run its chain began with: a schedule's in the owner's chat has nobody there to answer; a run the owner resumes by
  hand, and the owner's own run, stay theirs.

No model is called."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.helper_reports import DetachedHelper
from dotagent.agent.manager import RunManager
from dotagent.agent.resume import RESUME_SOURCE, RESUMED_EVENT
from dotagent.config import load_config
from dotagent.core.paths import config_path, ensure_layout, workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.endings import RESULT_RESTARTED
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.delegate import DELEGATE_SOURCE
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
    that reads it; then two resumed runs that finish at once."""
    unattended: list[ScriptedTurn] = [
        _call("read_file", {"path": str(config)}),
        _call("write_file", {"path": str(config), "content": "[shell]\nsandbox_unattended = false\n"}),
        _call("write_file", {"path": str(workspace / "note.txt"), "content": _WRITTEN}),
        _finish(),
    ]
    owners: list[ScriptedTurn] = [_call("read_file", {"path": str(config)}), _finish()]
    return [*unattended, *owners, *(_finish() for _ in range(4))]


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


async def test_what_dot_resumes_on_its_own_is_marked_as_the_run_its_chain_began_with(runtime: Runtime) -> None:
    """In the owner's chat a schedule's firing is the owner's chat with nobody there to answer (item 860). A run the
    daemon resumed on its own, and a helper resumed after the run that delegated it ended, were marked as runs of the
    owner's there to answer; a run the owner resumes by hand is theirs, there to answer, as before."""
    store = runtime.store

    async def cut(chat: ThreadRecord, source: str, parent: str | None) -> RunRecord:
        made: RunRecord = await store.create_run(
            chat.id, "Check", "task", SCRIPTED_MODEL, parent, source, "medium", "standard", None, "yolo", False,
            None, None, None,
        )  # fmt: skip
        await store.finish_run(made.id, "failed", RESULT_RESTARTED)
        return await store.get_run(made.id)

    async def marked(manager: RunManager, run: RunRecord) -> tuple[bool, bool, bool]:
        gate: RunGate = manager._handle(run.id).gate
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(run.id)
        return gate.owners_word, gate.owners_chat, gate.owner_there

    marks: dict[str, tuple[bool, bool, bool]] = {}
    manager: RunManager = RunManager(runtime)
    try:
        for name, source, automatic in (
            ("schedule, by the daemon", SCHEDULE_SOURCE, True),
            ("schedule, by the owner", SCHEDULE_SOURCE, False),
            ("the owner's, by the daemon", UI_SOURCE, True),
        ):
            old: RunRecord = await cut(await store.create_thread("Mine", UI_SOURCE), source, None)
            run: RunRecord = await manager._resume(old, (), True) if automatic else await manager.resume(old.id)
            marks[name] = await marked(manager, run)
        # Helpers cut off with their parent, which the daemon resumes on its own after the parent ended: of a firing,
        # of a firing the daemon resumed, and of one the owner resumed by hand, whose resume is their word and stops
        # the walk back (the security review of item 2027).
        for name, automatic in (
            ("a helper of a firing", None),
            ("a helper of a firing the daemon resumed", True),
            ("a helper of a firing the owner resumed", False),
        ):
            chat: ThreadRecord = await store.create_thread("Mine", UI_SOURCE)
            firing: RunRecord = await cut(chat, SCHEDULE_SOURCE, None)
            parent: RunRecord = firing
            if automatic is not None:
                parent = await cut(chat, RESUME_SOURCE, None)
                await store.add_event(parent.id, RESUMED_EVENT, {"from_run_id": firing.id, "automatic": automatic})
            helper: RunRecord = await cut(chat, DELEGATE_SOURCE, parent.id)
            entry: DetachedHelper = DetachedHelper(
                helper_run_id=helper.id, parent_run_id=parent.id, thread_id=chat.id, step=None, since=helper.created_at
            )
            assert await manager._resume_helper(entry, helper) is None
            marks[name] = await marked(manager, (await store.list_runs(1, chat.id))[0])
    finally:
        await manager.shutdown()
    assert marks == {
        "schedule, by the daemon": (False, True, False),
        "schedule, by the owner": (False, True, True),
        "the owner's, by the daemon": (False, True, True),
        "a helper of a firing": (False, True, False),
        "a helper of a firing the daemon resumed": (False, True, False),
        "a helper of a firing the owner resumed": (False, True, True),
    }, marks
