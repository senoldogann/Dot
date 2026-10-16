"""Pages catch up after a break of their connection to the daemon (item 160): the stream of the lifecycle replays
nothing, so a page reads again what it shows once the stream is back. A real server of a runtime in a throw-away home
is taken down and brought back up while the runtime works on, as when the network drops, and Chrome shows the pages:
the Activity page lists a run that started and ended meanwhile, and a run's page shows a message queued meanwhile. No
model is called: the scripted model plays the runs.
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
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Read the notes."
QUEUED: str = "Then write the summary."
TOKEN: str = "catch-up-test-token"
HOST: str = "127.0.0.1"
WAIT_S: float = 30.0
# A page that reads again what it shows has done so well within this once its stream is live again.
_CAUGHT_UP_MS: float = 10_000.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_CHANNEL: str = load_default_config().browser.channel
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)
# More replies than any test asks for.
_TURNS: int = 4


class _HeldModel(ScriptedProvider):
    """The scripted model, whose replies finish their run once the test lets them go (``go``); ``asked`` is set once a
    run waits for one."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH] * _TURNS)
        self.go: asyncio.Event = asyncio.Event()
        self.asked: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.set()
        await self.go.wait()
        return await super().complete(request, sink)


class _Server:
    """A real server of a runtime and its runs at a fixed port, which the test takes down and brings back up while the
    runtime works on, as when the connection to the daemon breaks."""

    def __init__(self, runtime: Runtime, manager: RunManager, port: int) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self.port: int = port
        self.address: str = f"http://{HOST}:{port}"
        self._server: DotServer | None = None
        self._serving: asyncio.Task[None] | None = None
        self._stopping: asyncio.Event = asyncio.Event()

    async def up(self) -> None:
        self._stopping = asyncio.Event()
        listener = bind_listener(HOST, self.port)
        context: ServerContext = ServerContext(
            runtime=self._runtime,
            manager=self._manager,
            scheduler=None,
            token=TOKEN,
            host=HOST,
            port=self.port,
            started_at=time.time(),
            stopping=self._stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), self._stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(WAIT_S):
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
        self._server, self._serving = server, serving

    @property
    def serving(self) -> bool:
        return self._serving is not None

    async def down(self) -> None:
        assert self._server is not None and self._serving is not None, "the server is not up"
        self._server.should_exit = True
        self._stopping.set()
        await self._serving
        self._server, self._serving = None, None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[RunManager, _Server]]:
    """A runtime in a throw-away home whose model is ``model``, its runs, and a server of them that is up."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                probe = bind_listener(HOST, 0)
                port: int = probe.getsockname()[1]
                probe.close()
                server: _Server = _Server(runtime, manager, port)
                await server.up()
                try:
                    yield manager, server
                finally:
                    if server.serving:
                        await server.down()
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _page(server: _Server, route: str) -> AsyncIterator[Page]:
    """Chrome with the page at ``route``, its connection to the daemon live."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=_CHANNEL, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url(HOST, server.port, TOKEN))
            await page.goto(f"{server.address}/#/{route}")
            await expect(_connection(page)).to_have_class("live-dot live")
            yield page
        finally:
            await browser.close()


def _connection(page: Page) -> Locator:
    """The dot of the page's connection to the daemon, whose class says how it stands."""
    return page.locator(".pill .live-dot")


async def _break(page: Page, server: _Server) -> None:
    """Take the server down until the page sees its connection broken."""
    await server.down()
    await expect(_connection(page)).to_have_class("live-dot reconnecting")


async def _mend(page: Page, server: _Server) -> None:
    """Bring the server back up until the page's connection is live again."""
    await server.up()
    await expect(_connection(page)).to_have_class("live-dot live", timeout=WAIT_S * 1000)


@pytest.mark.browser
async def test_the_activity_page_lists_a_run_that_ended_while_its_connection_was_broken(
    login_env: dict[str, str],
) -> None:
    async with (
        _served(login_env, ScriptedProvider(_PROVIDER, [_FINISH] * _TURNS)) as (manager, server),
        _page(server, "activity") as page,
    ):
        ended: Locator = page.locator(".activity-section[data-section=recent]")
        await expect(ended).to_contain_text("No run ended today")
        await _break(page, server)
        run: RunRecord = await manager.start(
            GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        await _mend(page, server)
        # No event of the run reaches the page: it reads its lists again because its stream is back.
        await expect(ended).to_contain_text(GOAL, timeout=_CAUGHT_UP_MS)


@pytest.mark.browser
async def test_a_runs_page_shows_a_message_queued_while_its_connection_was_broken(login_env: dict[str, str]) -> None:
    model: _HeldModel = _HeldModel()
    async with _served(login_env, model) as (manager, server):
        run: RunRecord = await manager.start(
            GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            await model.asked.wait()
        async with _page(server, f"runs/{run.id}") as page:
            queue: Locator = page.locator(".dock .queue")
            await expect(page.locator(".dock .btn.send")).to_have_attribute("aria-label", "Stop")
            await expect(queue).to_be_hidden()
            await _break(page, server)
            await manager.queue(
                run.thread_id, QUEUED, (), "task", None, None, "standard", None, NO_PICKS, None, None, None, UI_SOURCE
            )
            await _mend(page, server)
            await expect(queue.locator(".queue-text")).to_have_text([QUEUED], timeout=_CAUGHT_UP_MS)
        model.go.set()
