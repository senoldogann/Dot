"""Settings › Hooks (``docs/hooks.md``) in a real browser, wide and on a phone, against a real server: New hook makes a
hook bound to an agent and shows its address and secret once; the secret is gone once its card is closed and from what
the page reads after; Rotate secret shows a new one once; a call signed with it is listed with its run; the switch turns
it off; Delete takes it away; and nothing runs past the screen's edge."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.hooks.model import signature_of
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import AgentRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "hooks-page-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_OVER_PX: int = 1
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class Served:
    def __init__(self, address: str, runtime: Runtime, agent: AgentRecord) -> None:
        self.address: str = address
        self.runtime: Runtime = runtime
        self.agent: AgentRecord = agent

    @property
    def port(self) -> int:
        return int(self.address.rpartition(":")[2])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Served]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[memory]\nembed_enabled = false\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    # The agent's runs are played by a scripted model that finishes at once.
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * 8))
    agent: AgentRecord = await runtime.store.create_agent(
        "Triage", "Triages issues", "", SCRIPTED_MODEL, None, "ask", (), None, None
    )
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
        yield Served(f"http://127.0.0.1:{port}", runtime, agent)
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(served: Served) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(
            channel=served.runtime.config.browser.channel, headless=True
        )
        try:
            yield launched
        finally:
            await launched.close()


async def _opened(browser: Browser, served: Served, width: int, scheme: Literal["light", "dark"]) -> Page:
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", served.port, TOKEN))
    await page.goto(f"{served.address}/#/settings/hooks")
    return page


async def _fits(page: Page, label: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    assert over <= _OVER_PX, f"{label}: {over} px past the screen's edge"


async def _call(served: Served, path: str, secret: str) -> httpx.Response:
    body: bytes = json.dumps({"title": "Build failed"}).encode()
    stamp: str = str(int(time.time()))
    async with httpx.AsyncClient(base_url=served.address) as client:
        return await client.post(
            path,
            content=body,
            headers={"X-Dot-Timestamp": stamp, "X-Dot-Signature": f"sha256={signature_of(secret, stamp, body)}"},
        )


async def test_a_hook_is_made_its_secret_shown_once_rotated_called_switched_and_deleted(
    browser: Browser, served: Served
) -> None:
    for width, scheme in _SHOTS:
        page: Page = await _opened(browser, served, width, scheme)
        label: str = f"{width} {scheme}"
        await expect(page.get_by_text("No hooks yet")).to_be_visible()
        await page.get_by_role("button", name="New hook").click()
        dialog: Locator = page.get_by_role("dialog", name="New hook")
        await dialog.get_by_label("Name").fill("Issues")
        await dialog.get_by_label("What it starts").select_option(f"agent:{served.agent.id}")
        await dialog.get_by_label("What the run does").fill("Triage {title}.")
        await dialog.get_by_label("Fields").fill("title")
        await dialog.get_by_role("button", name="Make hook").click()
        await expect(dialog).to_have_count(0)
        fresh: Locator = page.locator(".set-section").filter(has=page.get_by_text("The secret of Issues", exact=True))
        secret: str = (await fresh.locator(".hook-secret").inner_text()).strip()
        address: str = (await fresh.locator(".hook-value").first.inner_text()).strip()
        assert len(secret) >= 40 and address.startswith(f"{served.address}/api/hooks/")
        await _fits(page, label)
        await fresh.get_by_role("button", name="I copied it").click()
        await expect(page.locator(".hook-secret")).to_have_count(0)
        assert secret not in await page.content()
        # The page drawn again from the daemon has no secret either.
        await page.reload()
        section: Locator = page.locator(".set-section").filter(has=page.get_by_role("heading", name="Issues"))
        await expect(section.get_by_text("Bot: Triage")).to_be_visible()
        assert secret not in await page.content()
        # Rotate: a new secret, shown once; the old one signs nothing.
        await section.get_by_role("button", name="Rotate secret").click()
        await page.get_by_role("dialog").get_by_role("button", name="Rotate").click()
        rotated: str = (await page.locator(".hook-secret").inner_text()).strip()
        assert rotated != secret
        path: str = address.removeprefix(served.address)
        assert (await _call(served, path, secret)).status_code == 401
        started: httpx.Response = await _call(served, path, rotated)
        assert started.status_code == 202, started.text
        await page.reload()
        section = page.locator(".set-section").filter(has=page.get_by_role("heading", name="Issues"))
        calls: Locator = section.get_by_role("list", name="Last calls of Issues").locator(".hook-call")
        await expect(calls).to_have_count(1)
        await expect(calls.first.locator(".hook-call-status")).to_have_text("202")
        await expect(calls.first.get_by_role("link", name="Open run")).to_have_attribute(
            "href", f"#/runs/{started.json()['run_id']}"
        )
        refused: Locator = section.get_by_role("list", name="Refused calls of Issues").locator(".hook-call")
        await expect(refused).to_have_count(1)
        await expect(refused.first.locator(".hook-call-reason")).to_have_text("the signature does not match")
        await _fits(page, label)
        # Off: its calls are refused.
        await section.get_by_role("switch", name="Issues on").click()
        await expect(section.get_by_role("switch", name="Issues on")).to_have_attribute("aria-checked", "false")
        assert (await _call(served, path, rotated)).status_code == 401
        await section.get_by_role("button", name="Delete").click()
        await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
        await expect(page.get_by_text("No hooks yet")).to_be_visible()
        await page.context.close()
