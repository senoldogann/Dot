"""Taking back the file changes of a run in the real loop: the scripted model edits a file, writes a new one in a new
folder and delegates a write to another run; ``dotx undo`` shows what it would do, then takes every change back, the
delegated run's too, and a second time finds nothing left to do.

No network and no model service is used: the scripted model plays both runs.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.cli_undo import UndoCommand, cmd_undo
from dotagent.config import load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Fix the greeting in notes.txt, add made/new.txt and have another run write child.txt."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _finish(answer: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}))


def _script(workspace: Path) -> list[ScriptedTurn]:
    """The run's first three turns, the two of the run it delegates to, and the run's last."""
    return [
        _turn(
            ScriptedCall(
                tool="edit_file", arguments={"path": str(workspace / "notes.txt"), "old": "Helo", "new": "Hello"}
            )
        ),
        _turn(ScriptedCall(tool="write_file", arguments={"path": str(workspace / "made/new.txt"), "content": "new\n"})),
        _turn(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": f"Write {workspace}/child.txt."})),
        _turn(ScriptedCall(tool="write_file", arguments={"path": str(workspace / "child.txt"), "content": "child\n"})),
        _finish("child.txt is written."),
        _finish("All three changes are made."),
    ]


def _undo(run_id: str, apply: bool) -> UndoCommand:
    return UndoCommand(name="undo", run_id=run_id, apply=apply, force=False)


async def test_dotx_undo_takes_back_the_file_changes_of_a_run_and_of_the_run_it_delegated_to(
    login_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        workspace: Path = runtime.workspace
        try:
            await asyncio.to_thread(
                seed_workspace, workspace, [FileSeed(path="notes.txt", content="Helo, world\n", encoding="text")]
            )
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(workspace)))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", "ask", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
        assert record.status == "done", record
        assert (workspace / "notes.txt").read_text(encoding="utf-8") == "Hello, world\n"
        assert (workspace / "child.txt").is_file()
        capsys.readouterr()

        assert await cmd_undo(_undo(run.id, False)) == 0
        assert capsys.readouterr().out.splitlines() == [
            f"Run {run.id} (done) changed 3 files with write_file or edit_file, counting the 1 run it delegated to:",
            f"  delete    {workspace}/child.txt",
            f"  delete    {workspace}/made/new.txt",
            f"  restore   {workspace}/notes.txt",
            f"Folders it created, removed when they are empty: {workspace}/made",
            f"Nothing was changed. 'dotx undo {run.id} --apply' takes back the files marked restore or delete.",
        ]
        assert (workspace / "notes.txt").read_text(encoding="utf-8") == "Hello, world\n"

        with capture_logs() as logs:
            assert await cmd_undo(_undo(run.id, True)) == 0
        assert capsys.readouterr().out.splitlines() == [
            f"Deleted {workspace}/child.txt",
            f"Deleted {workspace}/made/new.txt",
            f"Restored {workspace}/notes.txt",
            f"Removed the empty folder {workspace}/made",
        ]
        assert [entry for entry in logs if entry["event"] == "run_undone"] == [
            {
                "event": "run_undone",
                "log_level": "info",
                "run_id": run.id,
                "restored": 1,
                "deleted": 2,
                "folders": 1,
                "left": 0,
            }
        ]
        assert (workspace / "notes.txt").read_text(encoding="utf-8") == "Helo, world\n"
        assert not (workspace / "child.txt").exists()
        assert not (workspace / "made").exists()

        with capture_logs():
            assert await cmd_undo(_undo(run.id, True)) == 0
        assert capsys.readouterr().out == "Nothing to take back: every file is as it was before the run.\n"
