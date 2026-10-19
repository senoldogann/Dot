"""SnapShots (item 405) through the daemon and in a real browser, with a capture of the test's own in place of the
window in front (the key is not watched here): the routes (the settings as they stand, a setting changed live and kept
in the config file, a capture taken and claimed once), Settings › SnapShots, and the box that takes a capture that
waited and one that comes while it is on screen."""

import asyncio
import io
import re
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.attachments import attachment_summary, store_attachment
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.desktop.snapshots import SnapshotTaken
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "snapshots-token"


def _taker(runtime: Runtime, asked: list[bool]):
    """A capture of a window of the test's own: a small picture, and its text when asked, kept as the real one is."""

    def take(with_text: bool) -> SnapshotTaken:
        asked.append(with_text)
        buffer: io.BytesIO = io.BytesIO()
        Image.new("RGB", (64, 40), (200, 120, 60)).save(buffer, "JPEG")
        files = [store_attachment(runtime.workspace, "Finder - Desktop.jpg", "image/jpeg", buffer.getvalue())]
        if with_text:
            text: bytes = b'# Finder: Desktop\n\n[a1] window "Desktop"\n[a2] button "New Folder"\n'
            files.append(store_attachment(runtime.workspace, "Finder - Desktop.md", "text/markdown", text))
        return SnapshotTaken(app="Finder", title="Desktop", files=attachment_summary(files), taken_at=time.time())

    return take


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[snapshots]\nenabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    # SnapShots are off in the config, so the key is not watched here; the service still takes captures by hand.
    await built.snapshots.start(_taker(built, []))
    try:
        yield built
    finally:
        await built.snapshots.stop()
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


def _address(client: httpx.AsyncClient) -> tuple[str, int]:
    return "127.0.0.1", int(str(client.base_url).rpartition(":")[2].rstrip("/"))


def _written() -> dict[str, object]:
    """``[snapshots]`` as the config file has it now."""
    return tomllib.loads(config_path().read_text(encoding="utf-8")).get("snapshots", {})


async def test_the_routes_say_the_settings_change_them_live_and_hand_a_capture_to_one_claim(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    view = (await client.get("/api/snapshots")).json()
    assert (view["enabled"], view["shortcut"], view["listening"], view["problem"], view["pending"]) == (
        False,
        "⌘ ⌘",
        False,
        None,
        [],
    )
    assert (view["app_text"], view["sound"], view["animations"]) == (True, "click", True)
    # A setting changed holds at once and is kept in the config file; a value that is not a choice is refused.
    changed = (await client.post("/api/snapshots/settings", json={"key": "sound", "value": "pop"})).json()
    assert changed["sound"] == "pop" and runtime.snapshots.sound == "pop"
    assert (await client.post("/api/snapshots/settings", json={"key": "app_text", "value": False})).json()[
        "app_text"
    ] is False
    assert _written() == {"enabled": False, "sound": "pop", "app_text": False}
    assert (await client.post("/api/snapshots/settings", json={"key": "sound", "value": "loud"})).status_code == 422
    # A value that does not fit its key is refused before the config file is touched (the daemon would not start).
    assert (await client.post("/api/snapshots/settings", json={"key": "sound", "value": True})).status_code == 422
    assert (await client.post("/api/snapshots/settings", json={"key": "enabled", "value": "pop"})).status_code == 422
    assert _written() == {"enabled": False, "sound": "pop", "app_text": False}
    # A capture waits for the composer that claims it; with the text on, its file comes beside the picture.
    taken = (await client.post("/api/snapshots/take")).json()
    assert [capture["app"] for capture in taken["pending"]] == ["Finder"]
    assert [file["name"] for file in taken["pending"][0]["files"]] == ["Finder - Desktop.jpg"]
    await client.post("/api/snapshots/settings", json={"key": "app_text", "value": True})
    again = (await client.post("/api/snapshots/take")).json()
    assert len(again["pending"]) == 2 and [file["kind"] for file in again["pending"][1]["files"]] == ["image", "text"]
    claimed = (await client.post("/api/snapshots/claim")).json()
    assert len(claimed["files"]) == 2 and claimed["animations"] is True
    assert (await client.post("/api/snapshots/claim")).json()["files"] == []
    assert (await client.get("/api/snapshots")).json()["pending"] == []


async def test_the_settings_page_shows_and_changes_what_the_daemon_holds(
    browser: Browser, client: httpx.AsyncClient, runtime: Runtime
) -> None:
    await client.post("/api/snapshots/settings", json={"key": "app_text", "value": True})
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        host, port = _address(client)
        await page.goto(ui_url(host, port, TOKEN))
        await page.goto(f"http://{host}:{port}/#/settings/snapshots")
        card = page.get_by_role("region", name="SnapShots")
        await expect(card).to_be_visible()
        await expect(card.get_by_role("switch", name="SnapShots")).to_have_attribute("aria-checked", "false")
        await expect(card.get_by_text("Off: the Command key is not watched.")).to_be_visible()
        await expect(card.locator(".snapshots-keys")).to_have_text("⌘ ⌘")
        text_switch = card.get_by_role("switch", name="Include app text")
        await expect(text_switch).to_have_attribute("aria-checked", "true")
        await text_switch.click()
        await expect(text_switch).to_have_attribute("aria-checked", "false")
        await card.get_by_role("group", name="Capture sound").get_by_role("button", name="Glass").click()
        await expect(
            card.get_by_role("group", name="Capture sound").get_by_role("button", name="Glass")
        ).to_have_attribute("aria-pressed", "true")
        for _ in range(50):
            if _written().get("sound") == "glass":
                break
            await asyncio.sleep(0.1)
        assert _written().get("sound") == "glass"
        assert _written()["app_text"] is False and runtime.snapshots.app_text is False
        await page.screenshot(path=str(config_path().parent / "snapshots-settings.png"))
    finally:
        await page.close()


async def test_the_box_takes_a_capture_that_waited_and_one_that_comes_while_it_is_open(
    browser: Browser, client: httpx.AsyncClient, runtime: Runtime
) -> None:
    await client.post("/api/snapshots/settings", json={"key": "app_text", "value": True})
    await client.post("/api/snapshots/take")
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        host, port = _address(client)
        await page.goto(ui_url(host, port, TOKEN))
        await page.goto(f"http://{host}:{port}/#/")
        chips = page.locator(".composer .attach-chip")
        # The capture that waited: its picture and its text, as files of the draft.
        await page.wait_for_timeout(1500)
        titles: list[str | None] = [await chip.get_attribute("title") for chip in await chips.all()]
        composers: int = await page.locator(".composer").count()
        assert titles == ["Finder - Desktop.jpg", "Finder - Desktop.md"], (titles, composers)
        await expect(chips.nth(0)).to_have_attribute("title", "Finder - Desktop.jpg")
        await expect(chips.nth(1)).to_have_attribute("title", "Finder - Desktop.md")
        assert (await client.get("/api/snapshots")).json()["pending"] == []
        # A capture taken while the box is open comes in at once, with its spring.
        await client.post("/api/snapshots/settings", json={"key": "app_text", "value": False})
        await client.post("/api/snapshots/take")
        await expect(chips).to_have_count(3)
        await expect(chips.nth(2)).to_have_class(re.compile(r"\barrived\b"))
        # The draft keeps them: a reload lists them again.
        await page.reload()
        await expect(page.locator(".composer .attach-chip")).to_have_count(3)
    finally:
        await page.close()
