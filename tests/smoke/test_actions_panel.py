"""The Actions panel of a run's page, end to end: a scripted model edits a file and writes a new one, the API lists the
acting steps and the file changes with their undo state, and the headless UI opens the panel and takes one change back
with its button. A real uvicorn server and a real headless Chrome are used; no model service is called.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

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
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

GOAL: str = "Fix the greeting in notes.txt and add made/new.txt."
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_UI_TIMEOUT_MS: float = 15_000


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
            await asyncio.to_thread(seed_workspace, workspace, [FileSeed(path="notes.txt", content="Helo, world\n")])
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(workspace)))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
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


async def test_the_web_ui_shows_the_panel_and_its_button_takes_a_change_back(
    served: tuple[Runtime, RunRecord, str],
) -> None:
    runtime, run, address = served
    workspace: Path = runtime.workspace
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "Hello, world\n"
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page()
            await page.goto(ui_url(host, port, TOKEN))
            await expect(page.locator(".shell")).to_be_visible()
            await page.goto(f"{address}/#/runs/{run.id}")
            await page.click("button[aria-label='Run details']")
            # The panel opens on its Run tab; the Actions tab (item 203) shows what the run changed and renders no null.
            panel = page.locator("#panel-run-actions")
            await expect(panel).to_be_hidden()
            await page.get_by_role("tab", name="Actions").click()
            await expect(panel).to_be_visible(timeout=_UI_TIMEOUT_MS)
            assert "null" not in await page.locator(".inspector").inner_text()
            await expect(panel.locator(".action-step")).to_have_count(2)
            await expect(panel.locator(".action-edit[data-state='ready']")).to_have_count(2)
            notes = panel.locator(".action-edit", has_text="notes.txt")
            await notes.get_by_role("button", name="Restore").click()
            await page.locator("dialog.modal button", has_text="Restore").click()
            await expect(notes).to_have_attribute("data-state", "as_before", timeout=_UI_TIMEOUT_MS)
            await expect(notes.get_by_role("button", name="Restore")).to_have_count(0)
            await expect(panel.locator(".action-edit[data-state='ready']")).to_have_count(1)
        finally:
            await browser.close()
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "Helo, world\n"
    assert (workspace / "made/new.txt").is_file()
