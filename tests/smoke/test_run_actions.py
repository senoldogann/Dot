"""The actions of a run, end to end: a scripted model edits a file and writes a new one, and the API lists the acting
steps and the file changes with their undo state, which ``POST /api/runs/{id}/undo`` takes back. The run's page no
longer shows them (item 364). A real uvicorn server is used; no model service is called.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Fix the greeting in notes.txt and add made/new.txt."
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _script(workspace: Path) -> list[ScriptedTurn]:
    return [
        _turn(
            ScriptedCall(
                tool="edit_file", arguments={"path": str(workspace / "notes.txt"), "old": "Helo", "new": "Hello"}
            )
        ),
        _turn(ScriptedCall(tool="write_file", arguments={"path": str(workspace / "made/new.txt"), "content": "new\n"})),
        _turn(ScriptedCall(tool="read_file", arguments={"path": str(workspace / "notes.txt")})),
        _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Both changes are made.", "success": True})),
    ]


async def _serve(runtime: Runtime, manager: RunManager) -> tuple[DotServer, asyncio.Task[None], str]:
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    return server, serving, f"http://127.0.0.1:{port}"


@pytest_asyncio.fixture(loop_scope="module")
async def served(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunRecord, str]]:
    """A runtime in a throw-away home whose scripted run has ended, served by a real server: the runtime, the run and
    the server's address."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            workspace: Path = runtime.workspace
            await asyncio.to_thread(
                seed_workspace, workspace, [FileSeed(path="notes.txt", content="Helo, world\n", encoding="text")]
            )
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(workspace)))
            # A rule of the owner's that lets new files be written: the step it decides names it (item 468).
            await runtime.store.add_rule("allow", "write_file", "", "New files are fine")
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
                assert record.status == "done", record
                server, serving, address = await _serve(runtime, manager)
                try:
                    yield runtime, record, address
                finally:
                    server.should_exit = True
                    await serving
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_the_api_lists_the_acting_steps_and_the_file_changes_with_their_undo_state(
    served: tuple[Runtime, RunRecord, str],
) -> None:
    runtime, run, address = served
    workspace: Path = runtime.workspace
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        answer: httpx.Response = await client.get(f"/api/runs/{run.id}/actions")
        assert answer.status_code == 200, answer.text
        view = answer.json()
        refused: httpx.Response = await client.post(f"/api/runs/{run.id}/undo", json={"path": str(workspace / "x")})
        assert refused.status_code == 404, refused.text
    # The read is not an acting step; the two file changes are, through the command channel, with their targets.
    assert [(step["tool"], step["channel"], step["kind"], step["target"], step["ok"]) for step in view["steps"]] == [
        ("edit_file", "command", "write", str(workspace / "notes.txt"), True),
        ("write_file", "command", "write", str(workspace / "made/new.txt"), True),
    ]
    assert view["approvals"] == []
    assert view["ended"] is True
    assert [(edit["path"], edit["state"], edit["action"], edit["tool"], edit["changes"]) for edit in view["edits"]] == [
        (str(workspace / "made/new.txt"), "ready", "delete", "write_file", 1),
        (str(workspace / "notes.txt"), "ready", "restore", "edit_file", 1),
    ]


async def test_each_acting_step_keeps_what_decided_it_in_its_result_and_the_api(
    served: tuple[Runtime, RunRecord, str],
) -> None:
    """Item 468: the rule that let the new file be written is named, the edit that the preset let through names the
    preset, nobody was asked, and the step's ``tool_result`` keeps the same."""
    runtime, run, address = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        view = (await client.get(f"/api/runs/{run.id}/actions")).json()
    rule = next(rule for rule in await runtime.store.list_rules() if rule.description == "New files are fine")
    decided = {step["tool"]: step["decision"] for step in view["steps"]}
    assert (decided["write_file"]["source"], decided["write_file"]["rule_id"], decided["write_file"]["reason"]) == (
        "rule",
        rule.id,
        "New files are fine",
    )
    assert (decided["edit_file"]["source"], decided["edit_file"]["rule_id"]) == ("preset", None)
    assert {decision["answered_by"] for decision in decided.values()} == {"policy"}
    assert all(step["flags"] == [] for step in view["steps"])
    results = [event for event in await runtime.store.list_events(run.id, 0, 500) if event.kind == "tool_result"]
    written = next(event for event in results if event.data["tool"] == "write_file")
    assert written.data["decision"] == decided["write_file"]
