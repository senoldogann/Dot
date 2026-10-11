"""The screenshot tour (item 77): every page of the interface, in both colour schemes at 1280 and 390, written as PNGs
into the test's temp directory for reading by eye after each item of part 9. A scripted run with a shell step and a
note in memory give the pages something to show. The test asserts only that every page comes up with its title and
that the two faces are the ones the page uses; the looking is the reader's. A second test holds that a dark scheme kept
in the browser is on the page element before the first paint (item 81: boot.js in the document's head)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "tour-test-token"
_RUN_WAIT_S: float = 90.0
_SETTLE_MS: int = 900
# Every page of the interface by its hash; the run page is added once the scripted run exists.
_PAGES: tuple[tuple[str, str], ...] = (
    ("home", "#/runs"),
    ("activity", "#/activity"),
    ("campaigns", "#/campaigns"),
    ("agents", "#/agents"),
    ("team", "#/team"),
    ("controls", "#/controls"),
    ("settings-general", "#/settings/general"),
    ("settings-models", "#/settings/models"),
    ("settings-keys", "#/settings/keys"),
    ("settings-browser", "#/settings/browser"),
    ("settings-rules", "#/settings/rules"),
    ("memory", "#/memory"),
    ("schedules", "#/schedules"),
    ("settings-system", "#/settings/system"),
)
_SIZES: tuple[tuple[str, int, int], ...] = (("desk", 1280, 860), ("phone", 390, 844))
_SCHEMES: tuple[str, ...] = ("light", "dark")
_ANSWER: str = (
    "# Counted\n\nThe directory holds **three** files; `notes.md` is the newest.\n\n"
    "## What I read\n\n- the listing\n- the dates\n\n```sh\nls -la\n```\n"
)


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider = ScriptedProvider(_PROVIDER, [])

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


async def _shoot(context: BrowserContext, base: str, pages: tuple[tuple[str, str], ...], out: Path, tag: str) -> None:
    page: Page = await context.new_page()
    for name, route in pages:
        await page.goto(f"{base}/{route}")
        await page.wait_for_timeout(_SETTLE_MS)
        assert "Dot" in await page.title(), route
        faces: list[str] = await page.evaluate(
            "[getComputedStyle(document.body).fontFamily, getComputedStyle(document.querySelector('code, .mono') ?? document.body).fontFamily]"
        )
        assert faces[0].startswith('"Inter"') or faces[0].startswith("Inter"), faces
        await page.screenshot(path=str(out / f"{tag}-{name}.png"), full_page=False)
        if name == "run":
            # The side panel with its Details card open (item 84): on a wide window beside the chat, on a phone a drawer.
            await page.get_by_role("button", name="Run details").click()
            await page.locator(".inspector .fold-toggle", has_text="Details").click()
            await page.wait_for_timeout(_SETTLE_MS)
            await page.screenshot(path=str(out / f"{tag}-run-panel.png"), full_page=False)
    await page.close()


async def test_every_page_in_both_schemes_at_both_widths(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    MODEL.script = ScriptedProvider(
        _PROVIDER, [_turn("shell", {"command": "ls -la"}), _turn(FINISH_TOOL, {"answer": _ANSWER, "success": True})]
    )
    run: RunRecord = await manager.start(
        "Count the files in the workspace", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)
    note = await client.post("/api/memories", json={"content": "The owner prefers short answers.", "tags": ["core"]})
    assert note.status_code == 201, note.text
    # An agent (item 86) gives the Agents page a row of its own and an agent page to shoot.
    made = await client.post(
        "/api/agents",
        json={
            "name": "Scout",
            "role": "Finds and checks sources",
            "instructions": "Prefer primary sources. Say what you could not confirm.",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": [],
        },
    )
    assert made.status_code == 201, made.text
    pages: tuple[tuple[str, str], ...] = (
        *_PAGES,
        ("agent", f"#/agents/{made.json()['id']}"),
        ("run", f"#/runs/{run.id}"),
    )
    out: Path = tmp_path / "tour"
    await asyncio.to_thread(out.mkdir)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for scheme in _SCHEMES:
                for size, width, height in _SIZES:
                    context: BrowserContext = await browser.new_context(
                        viewport={"width": width, "height": height}, color_scheme=scheme, device_scale_factor=1
                    )
                    first: Page = await context.new_page()
                    await first.goto(ui_url(host, port, TOKEN))
                    await first.wait_for_timeout(_SETTLE_MS)
                    await first.screenshot(path=str(out / f"{scheme}-{size}-first.png"))
                    await first.close()
                    await _shoot(context, base, pages, out, f"{scheme}-{size}")
                    await context.close()
        finally:
            await browser.close()
    shots: list[Path] = await asyncio.to_thread(lambda: sorted(out.glob("*.png")))
    assert len(shots) == len(_SCHEMES) * len(_SIZES) * (len(pages) + 2)
    print(f"\ntour: {out}")


# Records, before any page script runs, the document's state at the moment the scheme lands on the page element.
_WATCH_SCHEME: str = """
localStorage.setItem("dot.scheme", "dark");
// The page element does not exist yet when this runs, so the document is watched and the page element's attribute found.
new MutationObserver(() => {
  if (window.__schemeAt === undefined && document.documentElement.dataset.scheme === "dark") {
    window.__schemeAt = document.readyState;
  }
}).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-scheme"] });
"""


async def test_a_held_dark_scheme_is_on_the_page_before_the_first_paint(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, color_scheme="light"
            )
            await context.add_init_script(_WATCH_SCHEME)
            page: Page = await context.new_page()
            await page.goto(ui_url(host, port, TOKEN))
            await page.wait_for_timeout(_SETTLE_MS)
            state: list[str] = await page.evaluate(
                "[window.__schemeAt, document.documentElement.dataset.scheme, getComputedStyle(document.body).backgroundColor]"
            )
            assert state[0] == "loading", state
            assert state[1] == "dark", state
            red: int = int(state[2].removeprefix("rgb(").split(",")[0])
            assert red < 64, state
            await context.close()
        finally:
            await browser.close()
