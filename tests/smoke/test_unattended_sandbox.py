"""Item 2016: a run nobody started keeps the sandbox in yolo, whatever the owner's [shell] sandbox says. Real runs on
a scripted model through the real manager, with the owner's sandbox switched off: a schedule's yolo run cannot read
Dot's configuration through a shell command, and the owner's yolo run reads it; a run that resumes the schedule's is
the schedule's. The commands are real; no model is called."""

import asyncio
import shlex
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.loop import RunOpening
from dotagent.agent.manager import BACKGROUND_SOURCE, RunManager
from dotagent.agent.resume import RESUME_SOURCE, RESUMED_EVENT, origin_source
from dotagent.config import load_config
from dotagent.core.paths import config_path, ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.models import NO_PICKS, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio, pytest.mark.macos]

RUN_WAIT_S: float = 60.0
MARKER: str = "unattended-sandbox-marker-5c1e"
REFUSED: str = "Operation not permitted"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _read_then_finish(path: Path) -> list[ScriptedTurn]:
    read = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": f"cat {shlex.quote(str(path))}"}),)
    )
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    return [read, finish]


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
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _read_then_finish(config_path()) * 2))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _read(runtime: Runtime, run: RunRecord) -> str:
    """What the run's shell call returned."""
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
    texts: list[str] = [str(event.data.get("text")) for event in results if event.data.get("tool") == "shell"]
    assert len(texts) == 1, texts
    return texts[0]


async def test_a_schedules_yolo_run_keeps_the_sandbox_and_the_owners_does_not(runtime: Runtime) -> None:
    assert not runtime.sandbox.on
    manager: RunManager = RunManager(runtime)
    try:
        chat: ThreadRecord = await runtime.store.create_thread("Morning check", SCHEDULE_SOURCE)
        timed: RunRecord = await manager.start(
            "Check the settings", "task", SCRIPTED_MODEL, chat.id, SCHEDULE_SOURCE, None, None, "standard", None,
            "yolo", False, (),
        )  # fmt: skip
        assert manager._handle(timed.id).gate.unattended
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(timed.id)
        mine: RunRecord = await manager.start(
            "Check the settings", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
        )
        assert not manager._handle(mine.id).gate.unattended
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(mine.id)
    finally:
        await manager.shutdown()
    kept: str = await _read(runtime, timed)
    assert REFUSED.casefold() in kept.casefold() and MARKER not in kept, kept
    assert MARKER in await _read(runtime, mine)


async def test_a_run_that_resumes_a_schedules_run_goes_back_to_it_however_many_times_it_was_resumed(
    runtime: Runtime,
) -> None:
    store = runtime.store
    chat: ThreadRecord = await store.create_thread("Morning check", "ui")
    chain: list[RunRecord] = [
        await store.create_run(
            chat.id, "Check", "task", SCRIPTED_MODEL, None, SCHEDULE_SOURCE, "medium", "standard", None, "yolo",
            False, None, None, None,
        )
    ]  # fmt: skip
    for _ in range(3):
        resumed: RunRecord = await store.create_run(
            chat.id, "Check", "task", SCRIPTED_MODEL, None, RESUME_SOURCE, "medium", "standard", None, "yolo",
            False, None, None, None,
        )  # fmt: skip
        await store.add_event(resumed.id, RESUMED_EVENT, {"from_run_id": chain[-1].id})
        chain.append(resumed)
    assert await origin_source(runtime, chain[-1]) == SCHEDULE_SOURCE
    # A chain that cannot be read is no origin: the manager keeps such a run in the sandbox.
    lost: RunRecord = await store.create_run(
        chat.id, "Check", "task", SCRIPTED_MODEL, None, RESUME_SOURCE, "medium", "standard", None, "yolo", False,
        None, None, None,
    )  # fmt: skip
    assert await origin_source(runtime, lost) is None


async def test_the_run_that_tells_a_firings_background_command_ended_keeps_the_sandbox_in_the_owners_chat(
    runtime: Runtime,
) -> None:
    """The security review of item 2016: a schedule that goes on in the owner's chat (``/loop``) left a command in the
    background, and the run that tells its end was judged by the chat's opener alone, the owner, so it ran unsandboxed.
    It goes on from the firing, and keeps the sandbox; the same run after the owner's own message does not."""
    store = runtime.store
    manager: RunManager = RunManager(runtime)
    try:
        told: list[RunRecord] = []
        for earlier in (SCHEDULE_SOURCE, "ui"):
            chat: ThreadRecord = await store.create_thread("My chat", "ui")
            before: RunRecord = await store.create_run(
                chat.id, "Check", "task", SCRIPTED_MODEL, None, earlier, "medium", "standard", None, "yolo", False,
                None, None, None,
            )  # fmt: skip
            await store.finish_run(before.id, "done", "Done.")
            run: RunRecord = await manager.start(
                "The command ended.", "task", SCRIPTED_MODEL, chat.id, BACKGROUND_SOURCE, None, None, "standard",
                None, "yolo", False, (),
            )  # fmt: skip
            assert manager._handle(run.id).gate.unattended is (earlier == SCHEDULE_SOURCE), earlier
            async with asyncio.timeout(RUN_WAIT_S):
                await manager.wait(run.id)
            told.append(run)
    finally:
        await manager.shutdown()
    kept: str = await _read(runtime, told[0])
    assert REFUSED.casefold() in kept.casefold() and MARKER not in kept, kept
    assert MARKER in await _read(runtime, told[1])


async def test_the_manager_takes_a_resumed_run_and_a_resumed_helper_back_to_who_started_the_work(
    runtime: Runtime,
) -> None:
    """The pre-push review of item 2016: a resumed helper whose parent ended was judged by its parent's own source, and
    a helper's (``delegate``) says nothing of who started the work, so a helper under a helper of a schedule's run ran
    unsandboxed; and nothing went through :meth:`RunManager._origin`. It follows the delegations to the top, and a
    resume to the run it resumes."""
    store = runtime.store
    chat: ThreadRecord = await store.create_thread("Morning check", SCHEDULE_SOURCE)

    async def stored(source: str, parent: str | None) -> RunRecord:
        made: RunRecord = await store.create_run(
            chat.id, "Check", "task", SCRIPTED_MODEL, parent, source, "medium", "standard", None, "yolo", False, None,
            None, None,
        )  # fmt: skip
        await store.finish_run(made.id, "done", "Done.")
        return await store.get_run(made.id)

    firing: RunRecord = await stored(SCHEDULE_SOURCE, None)
    helper: RunRecord = await stored(DELEGATE_SOURCE, firing.id)
    under: RunRecord = await stored(DELEGATE_SOURCE, helper.id)
    mine: RunRecord = await stored("ui", None)
    manager: RunManager = RunManager(runtime)
    try:
        assert await manager._origin(DELEGATE_SOURCE, helper, _opening(None)) == SCHEDULE_SOURCE
        assert await manager._origin(DELEGATE_SOURCE, under, _opening(None)) == SCHEDULE_SOURCE
        assert await manager._origin(RESUME_SOURCE, None, _opening(firing.id)) == SCHEDULE_SOURCE
        assert await manager._origin(RESUME_SOURCE, None, _opening(mine.id)) == "ui"
        assert await manager._origin(RESUME_SOURCE, None, _opening(None)) is None
        assert await manager._origin("ui", None, _opening(None)) == "ui"
    finally:
        await manager.shutdown()


def _opening(from_run_id: str | None) -> RunOpening:
    return RunOpening(text="", files=(), resumed=None, once=None, carried=(), picks=NO_PICKS, from_run_id=from_run_id)
