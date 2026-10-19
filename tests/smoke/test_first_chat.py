"""Dot's first conversation and its names (item 420), against a daemon in a throw-away home whose default model is a
stand-in that only finishes: the names are kept in the config file and said to the model; a first open with no chat
has Dot greet the owner, name itself and say what it does, Skip ends it for good, and a home with a chat never shows
it. No model is called."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "first-chat-token"
_MODEL: str = "anthropic:claude-opus-5-5"
# How the instructions of a run's step begin; a title or a review is asked otherwise.
_RUN_PROMPT: str = "You are Dot, an autonomous agent"
_RUN_TIMEOUT_S: float = 60.0
_OWNERS_PHONE: str = "iPhone 17 Pro Max"


class _Finisher:
    """Anthropic's provider as far as a run needs it: each run's step finishes at once, and its instructions are kept
    in ``prompts``; a title or a review gets a word."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if _RUN_PROMPT not in request.messages[0].content:
            return await ScriptedProvider(self.name, [ScriptedTurn(text="Hello", calls=())]).complete(request, sink)
        self.prompts.append(request.messages[0].content)
        finish: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
        )
        return await ScriptedProvider(self.name, [finish]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=("low", "medium", "high"),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("claude-opus-5-5"),)

    async def aclose(self) -> None:
        return None


def _home_with(tmp: Path, assistant: str) -> Path:
    """A home whose config file runs the stand-in model, with a browser without a window and ``assistant`` as its
    ``[assistant]`` lines."""
    home: Path = tmp / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{_MODEL}"\n\n[assistant]\n{assistant}', encoding="utf-8"
    )
    return home


@asynccontextmanager
async def _daemon(tmp: Path, assistant: str) -> AsyncIterator[tuple[Runtime, RunManager, _Finisher, str]]:
    """A daemon in a throw-away home with ``assistant`` as its config file's ``[assistant]`` lines: its runtime, its run
    manager, the stand-in model and the address it serves on."""
    home: Path = await asyncio.to_thread(_home_with, tmp, assistant)
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(tmp / "logs"))
    patch.setenv("DOT_CHROME_DIR", str(tmp / "chrome"))
    finisher: _Finisher = _Finisher()
    try:
        runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
        await runtime.router.register("anthropic", finisher)
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
                yield runtime, manager, finisher, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    finally:
        patch.undo()


def _client(address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)


def _written() -> str:
    return config_path().read_text(encoding="utf-8")


async def test_the_names_are_kept_in_the_config_file_and_said_to_the_model(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, 'name = "Dot"\nowner = ""\nintroduced = false\n') as (runtime, manager, finisher, address),
        _client(address) as client,
    ):
        first: dict[str, object] = (await client.get("/assistant")).json()
        assert {key: first[key] for key in ("name", "owner", "introduced")} == {
            "name": "Dot",
            "owner": "",
            "introduced": False,
        }
        named: httpx.Response = await client.put("/assistant", json={"name": "  Miro ", "owner": "Ada"})
        assert named.status_code == 200, named.text
        assert named.json()["name"] == "Miro" and named.json()["owner"] == "Ada"
        for wrong in ({"name": "   "}, {"name": "Mi\nro"}, {"owner": "x" * 41}):
            assert (await client.put("/assistant", json=wrong)).status_code == 422, wrong
        quoted: httpx.Response = await client.put("/assistant", json={"owner": 'Ada "Al" \\o/'})
        assert quoted.status_code == 200 and quoted.json()["owner"] == 'Ada "Al" \\o/', quoted.text
        assert (await client.put("/assistant", json={"owner": "Ada"})).json()["owner"] == "Ada"
        assert (await client.put("/assistant", json={"introduced": True})).json()["introduced"] is True
        written: str = _written()
        assert 'name = "Miro"' in written and 'owner = "Ada"' in written and "introduced = true" in written, written
        run: RunRecord = await manager.start(
            "Say hello.", "task", None, None, "test", None, None, "standard", None, None, None, ()
        )
        done: RunRecord = await asyncio.wait_for(manager.wait(run.id), _RUN_TIMEOUT_S)
        assert done.status == "done", done.result
    prompt: str = finisher.prompts[0]
    assert "(you said) I call you Miro: answer to that name." in prompt
    assert "(you said) My name is Ada." in prompt
    assert runtime.assistant.owner_name() == "Ada"


async def _home(browser: Browser, address: str, phone: bool, playwright_devices: dict[str, object]) -> Page:
    context: BrowserContext = await browser.new_context(
        **(
            {**playwright_devices[_OWNERS_PHONE], "reduced_motion": "reduce"}  # type: ignore[dict-item]
            if phone
            else {"viewport": {"width": 1280, "height": 900}, "reduced_motion": "reduce"}
        )
    )
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


@pytest.mark.browser
async def test_a_first_open_has_dot_greet_the_owner_name_itself_and_say_what_it_does(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, _manager, _f, address),
        async_playwright() as playwright,
    ):
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            first = page.locator("section.first-chat")
            await expect(first).to_contain_text("Hi Ada! I'm Dot, your assistant on this Mac.")
            await first.get_by_role("button", name="Miro").click()
            await expect(first.locator(".bubble.user")).to_have_text("Miro")
            await expect(first.locator(".first-note")).to_have_text("Name changed to Miro")
            await expect(first).to_contain_text("Miro it is! Nice to meet you, Ada.")
            await expect(first).to_contain_text("I connect to your apps, such as mail and calendar")
            await expect(first).to_contain_text("I ask you before what is yours to decide")
            # Dot's browser here has no window: the card says so instead of offering a sign-in.
            await expect(first.locator(".first-connect")).to_contain_text("runs without a window")
            await expect(page.get_by_role("textbox", name="Goal")).to_have_attribute(
                "placeholder", "Message Miro (@ for skills, / for commands)"
            )
            assert 'name = "Miro"' in _written()
            await first.get_by_role("button", name="Skip the introduction").click()
            await expect(page.locator("section.first-chat")).to_have_count(0)
            await expect(page.locator(".home-greeting")).to_contain_text("Ada")
            assert "introduced = true" in _written()
            await page.reload()
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await expect(page.locator("section.first-chat")).to_have_count(0)
            phone: Page = await _home(webkit, address, True, playwright.devices)
            await expect(phone.get_by_role("textbox", name="Goal")).to_be_visible()
            await expect(phone.locator("section.first-chat")).to_have_count(0)
        finally:
            await chromium.close()
            await webkit.close()


@pytest.mark.browser
async def test_the_first_conversation_fits_a_phone_and_a_message_sent_ends_it(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, _manager, _f, address),
        async_playwright() as playwright,
    ):
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _home(webkit, address, True, playwright.devices)
            first = page.locator("section.first-chat")
            await expect(first).to_contain_text("What would you like to call me?")
            await first.get_by_role("textbox", name="A name of your own").fill("Pixel")
            await first.get_by_role("button", name="Call me that").click()
            await expect(first).to_contain_text("Pixel it is! Nice to meet you, Ada.")
            wide: bool = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            assert not wide, "the first conversation is wider than the phone"
            await page.get_by_role("textbox", name="Goal").fill("Say hello.")
            await page.get_by_role("textbox", name="Goal").press("Enter")
            await page.wait_for_url("**/#/runs/run_*")
            assert "introduced = true" in _written()
        finally:
            await webkit.close()


@pytest.mark.browser
async def test_a_home_with_a_chat_never_shows_the_first_conversation(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, 'name = "Dot"\nowner = ""\nintroduced = false\n') as (_runtime, manager, _f, address),
        async_playwright() as playwright,
    ):
        # A chat is one a person started a run in: Dot has had no first conversation, but the owner has used it.
        earlier: RunRecord = await manager.start(
            "Say hello.", "task", None, None, "test", None, None, "standard", None, None, None, ()
        )
        assert (await asyncio.wait_for(manager.wait(earlier.id), _RUN_TIMEOUT_S)).status == "done"
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await expect(page.locator(".home-greeting")).to_be_visible()
            await page.wait_for_timeout(500)
            await expect(page.locator("section.first-chat")).to_have_count(0)
        finally:
            await chromium.close()
