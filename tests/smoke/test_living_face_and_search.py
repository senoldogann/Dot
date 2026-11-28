"""The living face and the search's chips (the owner's asks of 2026-11-27, after Grok's app). A chat whose run works
wears its agent's face in its sidebar row, living, where its badge stands at rest, and the chat's bar shows it the same
way; once the run is stopped the row and the bar go back to the badge; under reduced motion the face glows and does not
move. The search's chips (All, Messages, Bots, Files) stand under the search while it has words: All shows what the
chats say, the bots and the files under their headings, a chip only its kind (Bots and Files hide the list), and a
message found opens its chat at that message. Scripted runs, served by a real server, at 1440x900 and 390x844, light and
dark, in English and in Turkish."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonValue
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "living-face-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_ANSWER: str = "I booked the Lastikçi garage for Friday at nine."
_LOOKS: tuple[tuple[int, int, Literal["light", "dark"], Literal["en", "tr"]], ...] = (
    (1440, 900, "light", "en"),
    (1440, 900, "dark", "tr"),
    (390, 844, "light", "tr"),
    (390, 844, "dark", "en"),
)
_WORDS: dict[str, dict[str, str]] = {
    "en": {"search": "Search chats", "all": "All", "messages": "Messages", "bots": "Bots", "files": "Files"},
    "tr": {"search": "Sohbetlerde ara", "all": "Tümü", "messages": "Mesajlar", "bots": "Botlar", "files": "Dosyalar"},
}


def _script() -> list[ScriptedTurn]:
    """The garage chat answers at once; the build chat waits in a shell command until it is stopped."""
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _ANSWER, "success": True}),)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 300"}),)),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    manager: RunManager = RunManager(runtime)
    try:
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _first_tool_call(runtime: Runtime, run_id: str) -> None:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_call", 1)
        if calls:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} made no tool call in {_STEP_WAIT_S} s")


async def _open(browser: Browser, address: str, look: tuple[int, int, Literal["light", "dark"], str]) -> Page:
    width, height, scheme, language = look
    page: Page = await browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
    await page.add_init_script(f"localStorage.setItem('dot.uiLanguage', '{language}')")
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    # The curtain of the opening lifts once the page shows (item 328).
    await expect(page.locator(".waiting")).to_have_count(0)
    if width < 860:
        await page.locator(
            ".side-toggle, [aria-label='Show sidebar'], [aria-label='Kenar çubuğunu göster']"
        ).first.click()
    return page


async def test_a_working_chat_wears_its_face_living_and_the_search_has_chips_wide_and_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    garage: RunRecord = await manager.start(
        "Book the garage", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(garage.id)).status == "done"
    files: list[JsonValue] = [{"id": "att_1_ab", "name": "Lastikci-fatura.pdf", "kind": "pdf", "size": 10}]
    await runtime.store.add_event(garage.id, "attachments", {"files": files})
    build: RunRecord = await manager.start(
        "Watch the build", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _first_tool_call(runtime, build.id)
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        made: httpx.Response = await client.post(
            "/api/agents",
            json={
                "name": "Lastikçi Uzmanı",
                "role": "Knows the tyres",
                "instructions": "",
                "model": "",
                "effort": None,
                "approvals": None,
            },
        )
        assert made.status_code in (200, 201), made.text
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for look in _LOOKS:
                width, _, scheme, language = look
                words: dict[str, str] = _WORDS[language]
                page: Page = await _open(browser, address, look)
                sidebar: Locator = page.locator("#sidebar")
                # The chat at work wears its agent's face in its badge's place, living; the chat at rest its badge.
                working: Locator = sidebar.locator(".side-chat").filter(
                    has=page.locator(f"a.side-run[href='#/runs/{build.id}']")
                )
                await expect(working.locator(".side-badge-face .avatar[data-living]")).to_have_count(1)
                await expect(working.locator(".side-badge")).to_have_count(0)
                resting: Locator = sidebar.locator(f"a.side-run[href='#/runs/{garage.id}']")
                await expect(resting.locator(".side-badge")).to_have_count(1)
                await expect(resting.locator("[data-living]")).to_have_count(0)
                # The sidebar paints its rows again as the runs move: the style is read with a retry.
                await expect(working.locator(".avatar[data-living]")).to_have_css("animation-name", "face-live")
                await page.screenshot(path=str(tmp_path / f"face-{width}-{scheme}-{language}.png"))
                # The search's chips stand under it once it has words; All shows the message, the bot and the file.
                search: Locator = page.get_by_role("searchbox", name=words["search"])
                chips: Locator = sidebar.locator(".side-chips")
                await expect(chips).to_be_hidden()
                await search.fill("lastikci")
                await expect(chips.locator(".side-chip")).to_have_text(
                    [words["all"], words["messages"], words["bots"], words["files"]]
                )
                await expect(chips.locator(".side-chip[aria-pressed=true]")).to_have_text(words["all"])
                finds: Locator = sidebar.locator(".side-finds")
                await expect(finds.locator("[data-kind=messages] .side-find strong")).to_have_text(["Lastikçi"])
                await expect(finds.locator("[data-kind=bots] .side-find-title")).to_have_text(["Lastikçi Uzmanı"])
                await expect(finds.locator("[data-kind=files] .side-find-title")).to_have_text(["Lastikci-fatura.pdf"])
                await expect(finds).not_to_contain_text("null")
                # The list says no chat matches only when the search found nothing else.
                await expect(sidebar.locator(".side-recent .side-note")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / f"search-all-{width}-{scheme}-{language}.png"))
                # A chip shows its kind alone; Files and Bots hide the list.
                await chips.get_by_role("button", name=words["files"]).click()
                await expect(chips.locator(".side-chip[aria-pressed=true]")).to_have_text(words["files"])
                await expect(finds.locator("[data-kind=messages], [data-kind=bots]")).to_have_count(0)
                await expect(finds.locator("[data-kind=files] .side-find")).to_have_count(1)
                await expect(sidebar.locator(".side-recent")).to_be_hidden()
                await page.screenshot(path=str(tmp_path / f"search-files-{width}-{scheme}-{language}.png"))
                await chips.get_by_role("button", name=words["bots"]).click()
                await expect(finds.locator("[data-kind=bots] .side-find")).to_have_count(1)
                await expect(finds.locator("[data-kind=files]")).to_have_count(0)
                await chips.get_by_role("button", name=words["messages"]).click()
                # The list is back (no chat's title holds the word here, so it shows no row).
                await expect(sidebar).to_have_attribute("data-scope", "messages")
                assert (
                    await sidebar.locator(".side-recent").evaluate("(nav) => getComputedStyle(nav).display") != "none"
                )
                # A message found opens its chat at that message, which wears the ring of a find for a moment.
                await finds.locator("[data-kind=messages] .side-find").first.click()
                await expect(page).to_have_url(re.compile(f"#/runs/{garage.id}$"))
                await expect(page.locator(".find-hit")).to_have_count(1)
                await expect(page.locator(".find-hit")).to_contain_text("Lastikçi")
                await page.screenshot(path=str(tmp_path / f"search-opened-{width}-{scheme}-{language}.png"))
                await page.close()
            # Under reduced motion the living face does not move: it glows.
            still: Page = await browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            await still.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            glowing: Locator = still.locator(f"#sidebar a.side-run[href='#/runs/{build.id}'] .avatar[data-living]")
            await expect(glowing).to_have_count(1)
            await expect(glowing).to_have_css("animation-name", "face-glow")
            await still.close()
            # The chat's own bar shows the living face while its run works; stopped, the bar and the row rest.
            page = await _open(browser, address, _LOOKS[0])
            await page.goto(page.url.split("#")[0] + f"#/runs/{build.id}")
            await expect(page.locator(".waiting")).to_have_count(0)
            bar: Locator = page.locator(".stage-badge")
            await expect(bar.locator(".avatar[data-living]")).to_have_count(1)
            await page.screenshot(path=str(tmp_path / "face-bar.png"))
            manager.cancel(build.id)
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(build.id)).status == "cancelled"
            await expect(bar.locator(".avatar[data-living]")).to_have_count(0)
            row: Locator = page.locator(f"#sidebar a.side-run[href='#/runs/{build.id}']")
            await expect(row.locator(".side-badge")).to_have_count(1)
            await expect(page.locator("#sidebar [data-living]")).to_have_count(0)
            await page.close()
        finally:
            await browser.close()
