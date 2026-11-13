"""What the page reads as it opens and as a chat opens (items 1169 and 1170): the agents twice, not three times, the
side menu and the new chat sharing one read; a run's record once, though the side menu and the run's page both ask
for it; and a run's events without the loop's snapshots (`loop_state`), which no page draws and which weighed 100 to
330 KB a run, while each earlier turn still draws its work. A real server on a scripted model."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypedDict

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, Request, Response, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-open-payloads-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: int = 8
_SAID: str = "\n\n".join(f"Paragraph {line} of what the run said on its way to the tides." for line in range(4))
# A loop snapshot as heavy as the owner's runs keep them (100 to 330 KB a run): a model's whole context.
_SNAPSHOT_TEXT: str = "context " * 4000
_SNAPSHOTS: int = 4
_NOT_SHOWN: str = "could not be shown"
_PHONE: dict[str, int] = {"width": 390, "height": 844}
_SETTLE_MS: int = 2500


class _ShellResult(TypedDict):
    """A shell call's result as the loop stores it."""

    call_id: str
    tool: str
    ok: bool
    text: str
    images: list[str]
    seconds: float
    waited_s: float
    summary: str
    kind: str
    risk: str
    target: str
    site: str
    steps: int
    channel: str
    tainted: bool
    flags: list[str]
    names_dot: str


def _shell_result(index: int) -> _ShellResult:
    return {
        "call_id": "call_1",
        "tool": "shell",
        "ok": True,
        "text": f"tide {index}",
        "images": [],
        "seconds": 0.1,
        "waited_s": 0.0,
        "summary": f"echo tide {index}",
        "kind": "shell",
        "risk": "low",
        "target": "",
        "site": "",
        "steps": 0,
        "channel": "shell",
        "tainted": False,
        "flags": [],
        "names_dot": "",
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chat(runtime: Runtime) -> AsyncIterator[tuple[str, list[RunRecord]]]:
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
    thread: ThreadRecord = await runtime.store.create_thread("A chat with snapshots", "test")
    runs: list[RunRecord] = []
    for index in range(_TURNS):
        made: RunRecord = await runtime.store.create_run(
            thread.id,
            f"Snapshot question {index}",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            "high",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        await runtime.store.add_event(
            made.id,
            "assistant",
            {"step": 1, "text": _SAID, "thinking": "", "tool_calls": ["shell"], "seconds": 0.1, "finish": "tool_calls"},
        )
        await runtime.store.add_event(
            made.id,
            "tool_call",
            {"call_id": "call_1", "tool": "shell", "args": {"command": f"echo tide {index}"}, "timeout_s": 30.0},
        )
        await runtime.store.add_event(made.id, "tool_result", _shell_result(index))
        for step in range(_SNAPSHOTS):
            await runtime.store.add_event(made.id, "loop_state", {"step": step, "context": _SNAPSHOT_TEXT})
        await runtime.store.finish_run(made.id, "done", f"Snapshot answer {index}.")
        runs.append(made)
    try:
        yield f"http://127.0.0.1:{port}", runs
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


class _Read(TypedDict):
    """What the page asked of the daemon: each API path, in order, and the events it was given."""

    paths: list[str]
    kinds: list[str]


async def _opened(browser: Browser, address: str, run_id: str, read: _Read) -> Page:
    """Dot opened on a phone-sized page, then the chat of ``run_id``; what it read goes into ``read``."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    page: Page = await browser.new_page(viewport=_PHONE, is_mobile=True, has_touch=True)
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.wait_for_timeout(_SETTLE_MS)

    def asked(request: Request) -> None:
        if "/api/" in request.url and "/api/proof" not in request.url:
            read["paths"].append(request.url.split("/api", 1)[1].split("?", 1)[0])

    async def answered(response: Response) -> None:
        if "/events" in response.url and response.ok:
            read["kinds"].extend(event["kind"] for event in json.loads(await response.text()))

    page.on("request", asked)
    page.on("response", lambda response: asyncio.ensure_future(answered(response)))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".history > .turn")).to_have_count(_TURNS - 2, timeout=15000)
    await page.wait_for_timeout(_SETTLE_MS)
    return page


async def test_the_side_menu_and_the_new_chat_read_the_agents_once_as_dot_opens(
    chat: tuple[str, list[RunRecord]],
) -> None:
    address, _ = chat
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    agents: list[str] = []
    async with async_playwright() as playwright:
        for engine in (playwright.chromium, playwright.webkit):
            browser: Browser = await engine.launch()
            try:
                page: Page = await browser.new_page(viewport=_PHONE, is_mobile=True, has_touch=True)
                agents.clear()
                page.on("request", lambda request: agents.append(request.url) if "/api/agents" in request.url else None)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator("a.side-run", has_text="A chat with snapshots")).to_have_count(1)
                await page.wait_for_timeout(_SETTLE_MS)
                # Dot reads every agent's look before it draws (actors.js loadLooks), then the side menu's faces and
                # the new chat's agents read them at once: those two share one read.
                assert len(agents) == 2, (engine.name, agents)
                await page.context.close()
            finally:
                await browser.close()


async def test_a_chat_reads_its_run_once(chat: tuple[str, list[RunRecord]]) -> None:
    address, runs = chat
    async with async_playwright() as playwright:
        for engine in (playwright.chromium, playwright.webkit):
            browser: Browser = await engine.launch()
            try:
                read: _Read = {"paths": [], "kinds": []}
                # A run before the chat's newest, as a loop's chat opened from an older link: the side menu looks up
                # its chat while the run's page reads it.
                opened: RunRecord = runs[-2]
                page: Page = await _opened(browser, address, opened.id, read)
                await expect(page.locator(".history", has_text=_NOT_SHOWN)).to_have_count(0)
                assert read["paths"].count(f"/runs/{opened.id}") == 1, (engine.name, read["paths"])
                await page.context.close()
            finally:
                await browser.close()


async def test_a_chat_reads_no_loop_snapshot_and_draws_each_turns_work(chat: tuple[str, list[RunRecord]]) -> None:
    address, runs = chat
    async with async_playwright() as playwright:
        for engine in (playwright.chromium, playwright.webkit):
            browser: Browser = await engine.launch()
            try:
                read: _Read = {"paths": [], "kinds": []}
                page: Page = await _opened(browser, address, runs[-2].id, read)
                # Each earlier turn read before the page was drawn shows its work, and none says it could not.
                newest = page.locator(".history > .turn").last
                await expect(newest.locator(".turn-say")).to_contain_text("Paragraph 0 of what the run said")
                await expect(page.locator(".history", has_text=_NOT_SHOWN)).to_have_count(0)
                assert "tool_result" in read["kinds"], engine.name
                assert "loop_state" not in read["kinds"], engine.name
                await page.context.close()
            finally:
                await browser.close()
