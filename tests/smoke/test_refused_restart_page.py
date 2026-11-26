"""The page of a run whose model declined its task (item 1889), on a served daemon: the timeline says the model
declined, the line over the composer offers Restart on…, whose menu is the model menu without the model that declined,
and the model picked starts the task again in the chat; Settings › Models chooses the security research model. Wide and
on a phone, light and dark. The models are scripted."""

import asyncio
import re
import time
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from model_menu import expect_chosen, open_menu, pick
from playwright.async_api import Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path, ensure_layout
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ModelInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "refused-restart-test-token"
DECLINING: str = "ollama:scripted"
OTHER: str = "ollama:deepseek-scripted"
GOAL: str = "Summarise the release notes."
_SETTLE_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


class TwoModels(ScriptedProvider):
    """A scripted provider that lists a second model, which a restart can be picked on."""

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("scripted"), await self.model_info("deepseek-scripted"))


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        f'[agent]\ngoal_mode = false\n\n[browser]\nheadless = true\n\n[models]\nmain = "{DECLINING}"\n',
        encoding="utf-8",
    )
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The notes, in short.", "success": True}),)
    )
    await runtime.router.register(
        "ollama", TwoModels("ollama", [ScriptedTurn(text="I'm sorry, but I can't help with that.", calls=()), finish])
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
        yield runtime, manager, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)


async def test_a_refused_run_offers_a_restart_on_a_model_the_owner_picks(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (runtime, manager, address), async_playwright() as playwright:
        # A run Dot began itself has no grounds to give: the model's no stands at once.
        refused: RunRecord = await manager.start(
            GOAL, "task", None, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(_SETTLE_S):
            ended: RunRecord = await manager.wait(refused.id)
        assert (ended.status, ended.end_reason) == ("failed", "model_refused"), ended
        port: int = int(address.rpartition(":")[2])
        browser = await playwright.chromium.launch(headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await (
                    await browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                ).new_page()
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/runs/{refused.id}")
                await expect(page.get_by_text("declined the task, and the task ends with its words")).to_be_visible()
                line: Locator = page.locator(".refused-line")
                await expect(line).to_contain_text("declined this task.")
                await expect(line.get_by_role("button", name="Restart on…")).to_be_visible()
                # Continue would go on with the model that declined: the restart is the one way on.
                await expect(page.locator(".resume-line:not(.refused-line)")).to_be_hidden()
                await page.screenshot(path=str(tmp_path / f"refused-{width}-{scheme}.png"))
                await page.close()

            page = await (await browser.new_context(viewport={"width": 1280, "height": 900})).new_page()
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/runs/{refused.id}")
            await page.locator(".refused-line").get_by_role("button", name="Restart on…").click()
            menu: Locator = open_menu(page)
            await expect(menu).to_be_visible()
            await menu.locator(".model-rail-item[data-view='ollama']").click()
            rows: list[str] = await menu.locator(".model-option[title]").evaluate_all(
                "(rows) => rows.map((row) => row.title)"
            )
            assert rows == [OTHER]
            await page.screenshot(path=str(tmp_path / "restart-menu.png"))
            await menu.locator(f".model-option[title='{OTHER}']").click()
            await expect(page).not_to_have_url(re.compile(rf"#/runs/{refused.id}$"))
            new_id: str = page.url.rpartition("/")[2]
            async with asyncio.timeout(_SETTLE_S):
                restarted: RunRecord = await manager.wait(new_id)
            assert (restarted.model, restarted.goal, restarted.thread_id) == (OTHER, GOAL, refused.thread_id)
            await expect(page.get_by_text("The notes, in short.")).to_be_visible()

            # Settings › Models: the model of security research within a scope.
            await page.goto(f"{address}/#/settings/models")
            await pick(page, "Security research model", OTHER)
            await expect(page.get_by_role("button", name=re.compile(r"^Security research model: "))).to_have_attribute(
                "title", OTHER
            )
            await page.screenshot(path=str(tmp_path / "settings-models.png"))
        finally:
            await browser.close()
    kept: dict[str, object] = tomllib.loads(config_path().read_text(encoding="utf-8"))
    assert kept["models"] == {"main": DECLINING, "kinds": {"security": OTHER}}
    assert runtime.roles.kind_model("security") == OTHER


async def test_settings_persists_task_and_independent_check_models(own_home: Path) -> None:
    async with _served(own_home) as (runtime, _manager, address), async_playwright() as playwright:
        port: int = int(address.rpartition(":")[2])
        browser = await playwright.chromium.launch(headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 390, "height": 900}, is_mobile=True, has_touch=True)
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/settings/models")
            for label in (
                "Quick task model",
                "Deep task model",
                "Writing model",
                "Coding model",
                "Other-family check model",
            ):
                await expect_chosen(page, label, "")
                await pick(page, label, OTHER)
                await expect_chosen(page, label, OTHER)
            await page.reload()
            await expect_chosen(page, "Coding model", OTHER)
            await expect_chosen(page, "Other-family check model", OTHER)
            await pick(page, "Other-family check model", "")
            await expect_chosen(page, "Other-family check model", "")
        finally:
            await browser.close()
    assert runtime.roles.applied(load_config(config_path()).models).kinds == {
        "quick": OTHER,
        "deep": OTHER,
        "writing": OTHER,
        "code": OTHER,
    }
    assert load_config(config_path()).models.check_other_family == ""
