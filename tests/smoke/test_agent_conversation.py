"""An agent is one conversation (item 1233, the owner's word of 2026-11-19 after Grok Bot): its face in the sidebar
opens its chat, its page's box goes on in that chat rather than starting a new one each time, New chat in its page's
"…" starts the next message afresh. A real server on a
scripted model, in Chrome."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "agent-conversation-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
_AGENT: dict[str, object] = {
    "name": "Scout",
    "role": "Finds and checks sources",
    "instructions": "Prefer primary sources.",
    "model": "",
    "effort": None,
    "approvals": "yolo",
    "tools": None,
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * 8))
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as client:
            yield manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _opened(browser: Browser, client: httpx.AsyncClient, route: str) -> Page:
    port: int | None = client.base_url.port
    assert port is not None
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"http://127.0.0.1:{port}/#{route}")
    return page


async def _settled(manager: RunManager, run_id: str) -> None:
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)


async def _sent_from_page(manager: RunManager, page: Page, words: str) -> str:
    """Send `words` from the agent page's box; the id of the run it started, once it ended."""
    box: Locator = page.get_by_role("textbox", name="Message")
    await box.fill(words)
    await box.press("Enter")
    await page.wait_for_url("**/#/runs/*")
    run_id: str = page.url.rsplit("/", 1)[1]
    await _settled(manager, run_id)
    return run_id


async def test_an_agent_is_one_conversation(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            # With no chat yet, its face opens its page, whose box starts the first chat.
            page: Page = await _opened(browser, client, f"/agents/{agent_id}")
            face: Locator = page.locator(f".side-agent-faces a[aria-label='{_AGENT['name']}']")
            await expect(face).to_have_attribute("href", f"#/agents/{agent_id}")
            first: str = await _sent_from_page(manager, page, "Find two sources on the matter")
            thread: str = (await client.get(f"/api/runs/{first}")).json()["run"]["thread_id"]

            # Its face now opens that chat, and is marked while it is open.
            await expect(face).to_have_attribute("href", f"#/runs/{first}")
            await expect(face).to_have_attribute("aria-current", "page")

            # A message from its page goes on in the same chat, rather than starting a new one.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{first}"
            )
            second: str = await _sent_from_page(manager, page, "And a third one")
            assert second != first
            assert (await client.get(f"/api/runs/{second}")).json()["run"]["thread_id"] == thread

            # New chat in the page's "…" starts the next message afresh, and the face follows the newest chat.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await page.get_by_role("button", name="More for Scout").click()
            await page.get_by_role("menuitem", name="New chat").click()
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_be_hidden()
            third: str = await _sent_from_page(manager, page, "Something else entirely")
            fresh: str = (await client.get(f"/api/runs/{third}")).json()["run"]["thread_id"]
            assert fresh != thread
            await expect(face).to_have_attribute("href", f"#/runs/{third}")

            # The conversation deleted from the sidebar, the page and the face go on in the chat left, not in one that is gone.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{third}"
            )
            row: Locator = page.locator(".side-chat").filter(
                has=page.get_by_text("Something else entirely", exact=True)
            )
            await row.hover()
            await row.get_by_role("button", name="More for Something else entirely").click()
            await page.get_by_role("menuitem", name="Delete").click()
            await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{second}"
            )
            await expect(face).to_have_attribute("href", f"#/runs/{second}")
            await page.close()
        finally:
            await browser.close()
