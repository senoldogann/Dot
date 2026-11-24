"""What the owner does with Dot's tells, as Scheduled tasks shows it (item 1743), in a real browser against the real
daemon routes: the panel under Dot's own initiative has a line for each kind that told, and a watcher's row says what
the owner did with its tells. Seen on a desktop and on a phone, light and dark; nothing runs past the width."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.notify.tells import TELL_RESPONSE_EVENT, TELL_SENT_EVENT, TELLS_RUN_ID
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "tells-panel-token"
OPUS: str = "anthropic:claude-opus-5-5"
_WAIT_S: float = 30.0
_HOUR_S: float = 3600.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _finish() -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\nfast = "{OPUS}"\nreview = "{OPUS}"\n\n'
        '[agent]\ngoal_mode = false\nreflection_time = "off"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_finish() for _ in range(4)]))
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


async def _told(runtime: Runtime, tell_id: str, kind: str, subject: str, route: str, ago_s: float) -> None:
    """A tell that went out ``ago_s`` seconds ago, as the ledger keeps it."""
    now: float = time.time()
    await runtime.store._insert_event(
        TELLS_RUN_ID,
        now - ago_s,
        TELL_SENT_EVENT,
        {"tell_id": tell_id, "kind": kind, "subject": subject, "route": route},
    )
    await runtime.store._db.commit()


async def _answered(runtime: Runtime, tell_id: str, response: str, after_s: float, ago_s: float) -> None:
    now: float = time.time()
    await runtime.store._insert_event(
        TELLS_RUN_ID,
        now - ago_s + after_s,
        TELL_RESPONSE_EVENT,
        {"tell_id": tell_id, "response": response, "via": "app"},
    )
    await runtime.store._db.commit()


async def test_the_page_says_what_the_owner_did_with_the_tells_by_kind_and_by_watcher(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    made: httpx.Response = await client.post(
        "/api/watchers", json={"name": "Reviews", "kind": "github", "enabled": False, "action": "tell", "minutes": 30}
    )
    assert made.status_code == 201, made.text
    watcher: JsonObject = made.json()
    # Four chats the tells open, each of a run that ended.
    runs: list[RunRecord] = []
    for number in range(4):
        started: RunRecord = await manager.start(
            f"Say done {number}.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
        )  # fmt: skip
        assert (await manager.wait(started.id)).status == "done"
        runs.append(started)
    one, two, three, four = (f"#/runs/{run.id}" for run in runs)
    # Two hours ago: a brief, two watcher tells and a restart notice that opens no chat; just now, a watcher tell
    # whose half hour has not ended (it went out after its run). The brief and one watcher tell were opened.
    for tell_id, kind, subject, route, ago in (
        ("t1", "brief", "", one, 2 * _HOUR_S),
        ("t2", "watcher", watcher["id"], two, 2 * _HOUR_S),
        ("t3", "watcher", watcher["id"], three, 2 * _HOUR_S),
        ("t4", "system", "", "#/settings/phone", 2 * _HOUR_S),
        ("t5", "watcher", watcher["id"], four, 0.0),
    ):
        await _told(runtime, tell_id, kind, subject, route, ago)
    await _answered(runtime, "t1", "opened", 40.0, 2 * _HOUR_S)
    await _answered(runtime, "t2", "opened", 90.0, 2 * _HOUR_S)
    report: JsonObject = (await client.get("/api/tells")).json()
    assert report["window_s"] == 1800.0
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, height, scheme in ((1280, 900, "light"), (390, 844, "dark")):
                phone: bool = width < 720
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    color_scheme=scheme,
                    is_mobile=phone,
                    has_touch=phone,
                    reduced_motion="reduce",
                )
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"http://127.0.0.1:{port}/#/schedules")
                panel: Locator = page.locator("[data-tells]")
                await expect(panel.get_by_role("heading", name="What you do with its tells")).to_be_visible()
                await expect(panel.get_by_text("In the last 30 days")).to_be_visible()
                brief: Locator = panel.locator('[data-tell-kind="brief"]')
                await expect(brief).to_contain_text("Morning brief")
                await expect(brief).to_contain_text("1 told · 1 opened or acted on · 100% read")
                # The watchers' tells together: three told, one opened, one let be, one still waiting.
                await expect(panel.locator('[data-tell-kind="watcher"]')).to_contain_text(
                    "3 told · 1 opened or acted on · 1 ignored · 1 waiting · 50% read"
                )
                # A notice that opens no chat is counted apart and left out of the share.
                await expect(panel.locator('[data-tell-kind="system"]')).to_contain_text(
                    "1 told · 1 with no chat to open"
                )
                row: Locator = page.locator(f'[data-watcher="{watcher["id"]}"]')
                await expect(row.locator(".tell-line")).to_have_text(
                    re.compile(r"3 told · 1 opened or acted on · 1 ignored · 1 waiting · 50% read")
                )
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"tells-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()
