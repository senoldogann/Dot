"""The screenshot tour (item 77): every page of the interface, in both colour schemes at 1280 and 390, written as PNGs
into the test's temp directory for reading by eye after each item of part 9. A scripted run with a shell step and a
note in memory give the pages something to show. The test asserts only that every page comes up with its title and
that the two faces are the ones the page uses; the looking is the reader's. A second test holds that a dark scheme kept
in the browser is on the page element before the first paint (item 81: boot.js in the document's head)."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import META_FILE, PlaybookMeta
from dotagent.config import load_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.skills.types import SKILL_FILE
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
    ("settings-skills", "#/settings/skills"),
    ("memory", "#/memory"),
    ("schedules", "#/schedules"),
    ("settings-system", "#/settings/system"),
)
_SIZES: tuple[tuple[str, int, int], ...] = (("desk", 1280, 860), ("phone", 390, 844))
# Where macOS centres the traffic lights of an overlay title bar, from the window's top edge.
_LIGHTS_Y: float = 15.0
_SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
# The steps before the answer, each a line of text and a shell call (the commands vary, so no step repeats enough to
# be called a loop), so the conversation is taller than a window and the way down (the jump arrow) has something to
# point at. Lessons are off: the scripted model has no reply left for drawing them.
_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Listing the workspace first, to see what is there before counting anything; the names and the sizes come with it.",
        "ls -la",
    ),
    (
        "Reading the dates of each file, newest first, so the latest change stands out before any of them is named.",
        "ls -lt",
    ),
    (
        "Checking the hidden entries too, since a dotfile counts as a file of the folder and the count must include it.",
        "ls -A",
    ),
    (
        "Checking where the folder is, so the count is of the workspace and not of a folder further up the tree.",
        "pwd",
    ),
    (
        "Measuring the folder, to say how much the three files weigh together and whether any of them is unexpectedly large.",
        "du -sh .",
    ),
    (
        "Reading the listing once more with the sizes, to be sure of the count before the answer is written down.",
        "ls -la",
    ),
    (
        "Sorting by date again, so the newest file is named with confidence and not from the first listing alone.",
        "ls -lt",
    ),
    (
        "Looking at the hidden entries a last time; nothing new appeared there, so the count stands as read before.",
        "ls -A",
    ),
    (
        "Checking the folder once more, since a change of directory in between would make the count another folder's.",
        "pwd",
    ),
    (
        "Measuring the folder a second time, so the size in the answer is the one of the moment it is written.",
        "du -sh .",
    ),
    ("Reading the listing a last time, with every name in view, so nothing is left out of the count.", "ls -la"),
    ("Writing the answer up with the count, the newest file and what was read on the way to it.", "ls -lt"),
)
_PLAYBOOK: str = (
    '---\nname: learned-count-files\ndescription: "Count the files of a folder and name the newest."\n---\n\n'
    "# learned-count-files\n\nLearned by Dot from its own work (1 run).\n\n## Kind of work\n\ncount files\n\n"
    "## Steps\n\n1. List the folder.\n2. Read the dates.\n3. Name the newest.\n"
)
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
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = true\nlessons = "off"\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n',
        encoding="utf-8",
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
        if name == "home":
            # Inside the desktop shell (item 88) the page keeps room for the traffic lights: in the sidebar's head, and
            # in the top bar once the sidebar is away.
            await page.evaluate("document.documentElement.dataset.shell = 'desktop'")
            await page.wait_for_timeout(_SETTLE_MS)
            await page.screenshot(path=str(out / f"{tag}-home-desktop.png"), full_page=False)
            # The brand's centre on the traffic lights' line, 15 px under the top edge (item 107).
            brand = await page.locator(".side-head .brand").bounding_box()
            assert brand is not None and abs(brand["y"] + brand["height"] / 2 - _LIGHTS_Y) <= 2, brand
            # The sidebar goes and comes back through its own buttons (item 100: setting the state on the element had
            # hidden the sidebar and its opener from every later desk shot).
            hide = page.get_by_role("button", name="Hide sidebar")
            docked: bool = await hide.is_visible()
            if docked:
                await hide.click()
                await page.wait_for_timeout(_SETTLE_MS)
            await page.screenshot(path=str(out / f"{tag}-home-desktop-closed.png"), full_page=False)
            if docked:
                await page.get_by_role("button", name="Show sidebar").click()
                await page.wait_for_timeout(_SETTLE_MS)
            await page.evaluate("delete document.documentElement.dataset.shell")
        if name == "activity":
            # An empty section says what would show there (item 105).
            await expect(page.locator(".activity-section[data-section=waiting] .empty-line")).to_contain_text(
                "shows here"
            )
        if name == "run":
            # From the top of a conversation taller than the window, the way down to the answer (item 96).
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(_SETTLE_MS)
            await expect(page.get_by_role("button", name="Jump to the answer")).to_be_visible()
            await page.screenshot(path=str(out / f"{tag}-run-top.png"), full_page=False)
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
        _PROVIDER,
        [
            *(
                ScriptedTurn(text=text, calls=(ScriptedCall(tool="shell", arguments={"command": command}),))
                for text, command in _STEPS
            ),
            _turn(FINISH_TOOL, {"answer": _ANSWER, "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Count the files in the workspace", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)
    # A lesson the run was handed, so the Memory page shows a score (item 105).
    note = await client.post("/api/memories", json={"content": "The owner prefers short answers.", "tags": ["core"]})
    assert note.status_code == 201, note.text
    await runtime.store.mark_memories_used(run.id, [note.json()["record"]["id"]], time.time(), "start")
    await runtime.store.close_memory_uses(run.id, "done")
    # A skill Dot learned, so the Skills tab has a row.
    folder: Path = playbooks_dir() / "learned-count-files"
    meta: PlaybookMeta = PlaybookMeta(
        name="learned-count-files",
        kind="count files",
        host="the Mac",
        runs=(run.id,),
        acting_steps=(12,),
        written_at=time.time(),
        updated_at=time.time(),
        uses=1,
        failures=0,
    )

    def write_playbook() -> None:
        folder.mkdir(parents=True)
        (folder / SKILL_FILE).write_text(_PLAYBOOK, encoding="utf-8")
        (folder / META_FILE).write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")

    await asyncio.to_thread(write_playbook)
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
    assert len(shots) == len(_SCHEMES) * len(_SIZES) * (len(pages) + 5)
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
