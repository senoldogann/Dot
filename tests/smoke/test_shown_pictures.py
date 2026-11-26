"""A picture a run makes (`generate_image`, item 1969) shows once in the conversation, on the pages of a daemon in
Chrome: as its card, a part of the answer, and not also as a thumbnail in the tool's row or a screen of Dot's computer.

No network and no model service is used: the scripted model plays a run that draws an SVG with the tool, which Dot's own
headless Chrome draws; the daemon is a real server.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ModelInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.images import GENERATE_IMAGE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "shown-pictures-test-token"
WAIT_S: float = 60.0
_SVG: str = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#fff"/>'
    '<circle cx="80" cy="45" r="30" fill="#2060d0"/></svg>'
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: list[ScriptedTurn] = [
    ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=GENERATE_IMAGE_TOOL,
                arguments={"path": "cards/dot.png", "svg": _SVG, "width": 640, "height": 360},
            ),
        ),
    ),
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The picture is above.", "success": True}),)
    ),
]


class _SeeingProvider(ScriptedProvider):
    """The scripted model, which takes images: a run that gets a picture back is not sent to the vision model."""

    async def model_info(self, model: str) -> ModelInfo:
        return (await super().model_info(model)).model_copy(update={"vision": True})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``_TURNS``, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, _SeeingProvider(_PROVIDER, _TURNS))
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


@pytest.mark.browser
async def test_a_picture_a_run_makes_shows_once_as_its_card_and_not_also_as_a_thumbnail(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (manager, address), async_playwright() as playwright:
            run: RunRecord = await manager.start(
                "Draw a card", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
            )
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            browser: Browser = await playwright.chromium.launch(
                channel=load_default_config().browser.channel, headless=True
            )
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                card: Locator = page.locator(".shown-picture")
                await expect(card).to_have_count(1)
                picture: Locator = card.locator("img.shown-picture-image")
                await expect(picture).to_have_count(1)
                await expect(picture).to_have_js_property("naturalWidth", 640)
                # The one picture of the run on the page: the tool's row holds no thumbnail of it, and no screen of
                # Dot's computer is made of it.
                await expect(page.locator(".shot")).to_have_count(0)
                assert (
                    await page.locator("img").evaluate_all(
                        "(images) => images.filter((i) => i.naturalWidth === 640 && i.naturalHeight === 360).length"
                    )
                    == 1
                )
            finally:
                await browser.close()
