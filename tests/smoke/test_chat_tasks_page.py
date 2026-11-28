"""A chat's tasks on the pages (item 2013, slice 3 of item 1982's design), on a daemon served for real, in WebKit: a
computer's window lists the chat's tasks over the composer, each with its state, a Stop and the way to it; a phone's
capsule says "2 tasks" and opens the same rows in a sheet; a task's own page says whose task it is, with the way back;
a row's Stop stops that task alone, and the composer's Stop stops every task of the chat.

No network and no model service is used: the scripted model plays the runs, held at their first step by gates."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "tasks-page-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_LANE_MARK: str = "The new task, the owner's words:"
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class _Model(ScriptedProvider):
    """Runs held at their first step until the pages stop them: every task's words name a gate that stays shut."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH] * 16)
        self.gate: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await self.gate.wait()
        return await super().complete(request, sink)


def _config() -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"busy_parallel": True, "chat_tasks": 5}),
            "models": config.models.model_copy(update={"fast": SCRIPTED_MODEL}),
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(login_env: dict[str, str], model: _Model) -> AsyncIterator[tuple[Runtime, RunManager, int]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
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
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
            yield runtime, manager, port
        finally:
            model.gate.set()
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
            await close_runtime(runtime)


async def _until(read: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline: float = time.monotonic() + WAIT_S
    while not await read():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


async def _ended(runtime: Runtime, run_id: str) -> None:
    async def done() -> bool:
        return (await runtime.store.get_run(run_id)).status in ("done", "failed", "cancelled")

    await _until(done, f"the end of {run_id}")


async def _page(browser: Browser, port: int, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    phone: bool = width < 600
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 844 if phone else 900},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=phone,
        is_mobile=phone,
    )
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"http://127.0.0.1:{port}/#/runs/{run_id}")
    await expect(page.locator(".dock .composer").last).to_be_visible()
    return page


async def _new_task(port: int, thread_id: str, words: str) -> str:
    """A task of the chat, started with ``/new`` through the queue's route: its thread."""
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}/api", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as http:
        answer: httpx.Response = await http.post(
            f"/threads/{thread_id}/queue", json={"text": f"/new {words}", "mode": "task", "model": SCRIPTED_MODEL}
        )
    assert answer.status_code == 201, answer.text
    routed: object = answer.json()["routed"]
    assert isinstance(routed, dict) and routed["kind"] == "new", answer.text
    return str(routed["thread_id"])


async def test_a_chats_tasks_show_on_a_computer_and_a_phone_and_stop_one_or_all(login_env: dict[str, str]) -> None:
    model: _Model = _Model()
    async with _served(login_env, model) as (runtime, manager, port), async_playwright() as playwright:
        chat: RunRecord = await manager.start(
            "Write the quarterly report.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        dentist: str = await _new_task(port, chat.thread_id, "Find a dentist open on Saturday.")
        x_thread: str = await _new_task(port, chat.thread_id, "Improve my x.com account.")
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            # A computer's window: the rows over the composer, the chat's own first.
            desk: Page = await _page(browser, port, chat.id, 1280, "light")
            rows = desk.locator(".dock .chat-tasks .task-row")
            await expect(rows).to_have_count(3)
            await expect(rows.nth(0)).to_contain_text("This chat")
            await expect(rows.nth(1)).to_contain_text("Find a dentist open on Saturday.")
            await expect(rows.nth(1)).to_contain_text("Working")
            await expect(desk.locator(".dock .chat-tasks .chat-loops-count")).to_have_text("3")
            # A phone: the capsule says it, and its sheet holds the same rows.
            for scheme in ("light", "dark"):
                phone: Page = await _page(browser, port, chat.id, 390, scheme)
                segment = phone.locator('[data-segment="tasks"]')
                await expect(segment).to_have_attribute("aria-label", "3 tasks")
                await segment.click()
                sheet = phone.locator(".dock-sheet:not([hidden])")
                await expect(sheet).to_contain_text("Tasks")
                await expect(sheet.locator(".task-row")).to_have_count(3)
                # No row spills past the phone's width.
                wide: bool = await phone.evaluate(
                    "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                assert not wide
                await phone.context.close()
            # A task's own page says whose task it is, with the way back to the chat.
            x_run: RunRecord = (await runtime.store.list_runs(1, x_thread))[0]
            lane: Page = await _page(browser, port, x_run.id, 1280, "light")
            await expect(lane.locator(".lane-line")).to_contain_text("A task of")
            # A row's Stop stops that task alone.
            dentist_run: RunRecord = (await runtime.store.list_runs(1, dentist))[0]
            await rows.nth(1).get_by_role("button", name="Stop Find a dentist open on Saturday.").click()
            await _ended(runtime, dentist_run.id)
            assert (await runtime.store.get_run(chat.id)).status == "running"
            assert (await runtime.store.get_run(x_run.id)).status == "running"
            await expect(rows.nth(1)).to_contain_text("Stopped")
            # The composer's Stop, on the task's own page, stops every task of the chat.
            send = lane.locator(".dock .btn.send")
            await expect(send).to_have_attribute("aria-label", "Stop")
            await send.click()
            await _ended(runtime, chat.id)
            await _ended(runtime, x_run.id)
        finally:
            await browser.close()
