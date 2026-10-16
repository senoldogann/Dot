"""What the pages tell (item 190): a chat whose queue could not be read says so, with Read again; the rules page
follows the rules changed on another page; the sign-in screen of a daemon that could not answer names its log by the
command that names its folder; a page that an update of Dot left out of date says so and is loaded again. In Chrome,
wide and on a phone, light and dark.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import APIResponse, Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.browser]

GOAL: str = "Wait for the build."
SUMMARY: str = "Then write the summary."
RULE: str = "No forced deletes in the projects folder"
TOKEN: str = "pages-tell-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Runs until the test creates the file ``stop`` in the workspace, at most 30 seconds.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"
_WAITING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The build is done.", "success": True}),)
)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_SETTLE_MS: int = 300
# What the pages of an updated daemon are, as the status a page reads tells it.
_OTHER_PAGES: str = "0" * 16


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], turns: list[ScriptedTurn]
) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``turns``, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
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
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            await asyncio.to_thread(_let_the_command_end)
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


@asynccontextmanager
async def _api(address: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
    ) as client:
        yield client


@asynccontextmanager
async def _chrome(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield browser
        finally:
            await browser.close()


def _let_the_command_end() -> None:
    workspace: Path = workspace_dir()
    if workspace.is_dir():
        (workspace / "stop").touch()


async def _signed_in(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    """A page of the daemon at ``address``, signed in, at ``width`` in ``scheme``."""
    # Without the service worker: once it controls the page, the page's requests pass through it, past the routes a
    # test puts in place.
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 800},
        color_scheme=scheme,
        reduced_motion="reduce",
        service_workers="block",
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await expect(page.locator("main.page")).to_be_visible()
    return page


async def _shot(page: Page, path: Path) -> None:
    await page.mouse.move(0, 0)
    await page.wait_for_timeout(_SETTLE_MS)
    await page.screenshot(path=str(path))


async def _until_called(runtime: Runtime, run_id: str) -> None:
    """Wait until the run called its tool: it is at work on it then."""
    deadline: float = time.monotonic() + WAIT_S
    while not await runtime.store.events_of_kind(run_id, "tool_call", 1):
        assert time.monotonic() < deadline, f"the run {run_id} called no tool in {WAIT_S} s"
        await asyncio.sleep(0.05)


def _refusing_reads(refused: list[str]) -> Callable[[Route], Awaitable[None]]:
    """A handler that answers every read of a chat's queue as a daemon that could not read it does (503), and lets
    the rest through; each refused address goes to ``refused``."""

    async def handle(route: Route) -> None:
        if route.request.method != "GET":
            await route.continue_()
            return
        refused.append(route.request.url)
        body: str = json.dumps({"message": "the store is busy", "context": {}})
        await route.fulfill(status=503, content_type="application/json", body=body)

    return handle


async def _from_other_pages(route: Route) -> None:
    """The daemon's status, as an updated daemon that serves other pages tells it."""
    answer: APIResponse = await route.fetch()
    status: dict[str, object] = await answer.json()
    await route.fulfill(status=answer.status, json={**status, "pages": _OTHER_PAGES})


async def test_a_queue_that_could_not_be_read_says_so_and_is_read_again(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Before, the page showed no queue at all and said nothing; its message seemed lost."""
    with scratch_home() as home, eval_environment(home, {}):
        turns: list[ScriptedTurn] = [_WAITING, _FINISH, _FINISH]
        async with _daemon(login_env, turns) as (runtime, manager, address), _api(address) as api:
            started: httpx.Response = await api.post(
                "/api/runs",
                json={"goal": GOAL, "model": SCRIPTED_MODEL, "approvals": "yolo", "key": str(uuid.uuid4())},
            )
            run_id: str = started.json()["id"]
            thread_id: str = started.json()["thread_id"]
            await _until_called(runtime, run_id)
            queued: httpx.Response = await api.post(
                f"/api/threads/{thread_id}/queue", json={"text": SUMMARY, "model": SCRIPTED_MODEL, "approvals": "yolo"}
            )
            assert queued.status_code == 201, queued.text
            async with _chrome(runtime) as browser:
                for width, scheme in _SHOTS:
                    page: Page = await _signed_in(browser, address, width, scheme)
                    refused: list[str] = []
                    await page.route(f"**/api/threads/{thread_id}/queue", _refusing_reads(refused))
                    await page.goto(f"{address}/#/runs/{run_id}")
                    unread: Locator = page.locator(".queue-unread")
                    await expect(unread).to_contain_text("The queue could not be read: the store is busy")
                    assert refused, "the page did not read the queue"
                    await _shot(page, tmp_path / f"queue-unread-{width}-{scheme}.png")
                    await page.unroute(f"**/api/threads/{thread_id}/queue")
                    await unread.get_by_role("button", name="Read again").click()
                    await expect(unread).to_be_hidden()
                    await expect(page.locator(".queue-list > li")).to_have_count(1)
                    await expect(page.locator(".queue-list")).to_contain_text(SUMMARY)
                    await page.close()
            await asyncio.to_thread(_let_the_command_end)
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(run_id)).status == "done"


async def test_the_rules_page_follows_the_rules_changed_on_another_page(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Before, a rule added or deleted elsewhere showed here only once the page was loaded again."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, []) as (runtime, _, address), _api(address) as api, _chrome(runtime) as browser:
            page: Page = await _signed_in(browser, address, 1280, "light")
            await page.goto(f"{address}/#/settings/rules")
            await expect(page.get_by_text("No rules yet")).to_be_visible()
            rule: httpx.Response = await api.post(
                "/api/rules",
                json={"behavior": "ask", "tool_glob": "shell", "match_regex": "rm -rf", "description": RULE},
            )
            assert rule.status_code == 201, rule.text
            row: Locator = page.locator(".list-row").filter(has_text=RULE)
            await expect(row).to_be_visible()
            switched: httpx.Response = await api.patch(f"/api/rules/{rule.json()['id']}", json={"enabled": False})
            assert switched.status_code == 200, switched.text
            await expect(row).to_have_class("list-row faint")
            await row.scroll_into_view_if_needed()
            await _shot(page, tmp_path / "rules-followed.png")
            deleted: httpx.Response = await api.delete(f"/api/rules/{rule.json()['id']}")
            assert deleted.status_code == 200, deleted.text
            await expect(page.get_by_text("No rules yet")).to_be_visible()


async def test_the_sign_in_screen_of_a_daemon_that_could_not_answer_names_its_log_by_a_command(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Before, it named ``~/Library/Logs/Dot/dotx.log``, where the log of a daemon given another folder is not."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, []) as (runtime, _, address), _chrome(runtime) as browser:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            body: str = json.dumps({"message": "Dot failed on an error it did not expect", "context": {}})
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.route(
                    "**/api/status",
                    lambda route: route.fulfill(status=500, content_type="application/json", body=body),
                )
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.get_by_text("The daemon is running but could not answer")).to_be_visible()
                hint: Locator = page.locator("p").filter(has_text="Its log has the details")
                await expect(hint).to_have_text("Its log has the details: dotx service status names its folder.")
                assert "Library/Logs" not in await page.locator("body").inner_text()
                await _shot(page, tmp_path / f"sign-in-failed-{width}-{scheme}.png")
                await page.close()


async def test_a_page_that_an_update_left_out_of_date_says_so_and_is_loaded_again(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Before, the page went on with the pages it was loaded from beside an updated daemon, and said nothing."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, []) as (runtime, _, address), _chrome(runtime) as browser:
            for width, scheme in _SHOTS:
                page: Page = await _signed_in(browser, address, width, scheme)
                banner: Locator = page.locator(".banner.update")
                await expect(banner).to_be_hidden()
                await page.route("**/api/status", _from_other_pages)
                # The page reads the status again when its window gets the focus (as on a timer and after an event).
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
                await expect(banner).to_be_visible()
                await expect(banner).to_contain_text("Dot was updated since this page was loaded")
                await _shot(page, tmp_path / f"updated-{width}-{scheme}.png")
                await page.unroute("**/api/status")
                await banner.get_by_role("button", name="Load again").click()
                await expect(page.locator("main.page")).to_be_visible()
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
                await page.wait_for_timeout(_SETTLE_MS)
                await expect(page.locator(".banner.update")).to_be_hidden()
                await page.close()
