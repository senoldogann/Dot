"""Dot's first conversation and its names (item 420), against a daemon in a throw-away home whose default model is a
stand-in that only finishes: the names are kept in the config file and said to the model; a first open with no chat
has Dot greet the owner, name itself and say what it does, Skip ends it for good, and a home with a chat never shows
it. No model is called."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
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
from dotagent.server import routes_assistant
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


def _home_with(tmp: Path, browser: str, assistant: str) -> Path:
    """A home whose config file runs the stand-in model, with a browser without a window and ``browser`` as the rest
    of its ``[browser]`` lines, and ``assistant`` as its ``[assistant]`` lines."""
    home: Path = tmp / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n{browser}\n[models]\nmain = "{_MODEL}"\n\n[assistant]\n{assistant}',
        encoding="utf-8",
    )
    return home


@asynccontextmanager
async def _daemon(tmp: Path, browser: str, assistant: str) -> AsyncIterator[tuple[Runtime, RunManager, _Finisher, str]]:
    """A daemon in a throw-away home with ``browser`` and ``assistant`` as lines of its config file
    (:func:`_home_with`): its runtime, its run manager, the stand-in model and the address it serves on."""
    home: Path = await asyncio.to_thread(_home_with, tmp, browser, assistant)
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
        _daemon(tmp_path, "", 'name = "Dot"\nowner = ""\nintroduced = false\n') as (
            runtime,
            manager,
            finisher,
            address,
        ),
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
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, _manager, _f, address),
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
            await expect(first.locator(".first-connect")).to_contain_text("works without a window")
            # A messaging app's few words (item 833); what the at sign and the slash open is the box's tooltip.
            goal_box = page.get_by_role("textbox", name="Goal")
            await expect(goal_box).to_have_attribute("placeholder", "Message Miro")
            await expect(goal_box).to_have_attribute("title", "Type @ for apps, / for skills and commands")
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
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, _manager, _f, address),
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
            # On a touch screen Return breaks the line (item 440): the round send button sends.
            await page.locator(".composer button.send").tap()
            await page.wait_for_url("**/#/runs/run_*")
            assert "introduced = true" in _written()
        finally:
            await webkit.close()


@pytest.mark.browser
async def test_a_home_with_a_chat_never_shows_the_first_conversation(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, "", 'name = "Dot"\nowner = ""\nintroduced = false\n') as (_runtime, manager, _f, address),
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


@pytest.mark.browser
async def test_in_auto_dot_says_a_review_model_answers_for_the_owner_and_promises_no_question(tmp_path: Path) -> None:
    auto: str = 'name = "Dot"\nowner = "Ada"\nintroduced = false\n\n[policy]\napprovals = "auto"\n'
    async with _daemon(tmp_path, "", auto) as (_runtime, _manager, _f, address), async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            first = page.locator("section.first-chat")
            await first.get_by_role("button", name="Nova").click()
            await expect(first).to_contain_text("a second AI answers for you")
            await expect(first).not_to_contain_text("I ask you before what is yours to decide")
            await expect(first).not_to_contain_text("Nothing is sent without your approval")
        finally:
            await chromium.close()


@pytest.mark.browser
async def test_the_look_starts_in_the_mode_its_promises_were_said_in_whatever_the_box_chose(tmp_path: Path) -> None:
    """The owner's Chrome lends its sign-ins (attach), so Google is ready and the look is offered; this browser's box
    chose yolo, the daemon's mode is ask, and the look starts in ask, as the first conversation said."""
    async with (
        _daemon(tmp_path, 'mode = "attach"\n', 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (
            runtime,
            _manager,
            _f,
            address,
        ),
        async_playwright() as playwright,
    ):
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context: BrowserContext = await chromium.new_context(
                viewport={"width": 1280, "height": 900}, reduced_motion="reduce"
            )
            await context.add_init_script("localStorage.setItem('dot.approvals', 'yolo')")
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            first = page.locator("section.first-chat")
            await first.get_by_role("button", name="Dot").click()
            await expect(first).to_contain_text("I work in your own Chrome")
            await expect(first).to_contain_text("I'll only read, and ask you before I do anything.")
            await first.get_by_role("button", name="Yes, have a look").click()
            await page.wait_for_url("**/#/runs/run_*")
            run: RunRecord = await runtime.store.get_run(page.url.rsplit("/", 1)[-1])
            assert run.approvals == "ask", run.approvals
            assert run.goal.startswith("Look through my Gmail inbox"), run.goal
        finally:
            await chromium.close()


# ----- the first steps (item 811) and the chat that is gone (item 810) -------------------------------------------


async def test_the_first_steps_say_what_is_done_and_a_dot_used_for_long_is_not_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with (
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, manager, _f, address),
        _client(address) as client,
    ):
        steps: dict[str, object] = (await client.get("/assistant/first-steps")).json()
        assert steps["fresh"] is True
        assert [(step["id"], step["done"]) for step in steps["steps"]] == [  # type: ignore[index, union-attr]
            ("name", True),
            ("passwords", False),
            ("imessage", False),
            ("phone", False),
            ("apps", False),
            ("chat", False),
        ]
        run: RunRecord = await manager.start(
            "Say hello.", "task", None, None, "ui", None, None, "standard", None, None, None, ()
        )
        await asyncio.wait_for(manager.wait(run.id), _RUN_TIMEOUT_S)
        after: dict[str, object] = (await client.get("/assistant/first-steps")).json()
        assert {step["id"]: step["done"] for step in after["steps"]}["chat"] is True  # type: ignore[index, union-attr]
        assert after["fresh"] is True
        # A first run older than the days a Dot is fresh: the home shows no steps.
        monkeypatch.setattr(routes_assistant, "FRESH_DAYS", -1)
        assert (await client.get("/assistant/first-steps")).json()["fresh"] is False


def _fits(page: Page) -> Awaitable[bool]:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
async def test_a_fresh_home_lists_the_first_steps_on_a_mac_and_a_phone_and_hide_puts_them_away(tmp_path: Path) -> None:
    async with (
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = false\n') as (_runtime, _manager, _f, address),
        async_playwright() as playwright,
    ):
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            card = page.locator("section.first-steps")
            await expect(card).to_be_visible()
            await expect(card.locator(".first-steps-count")).to_have_text("1 of 6 done")
            await expect(card.locator(".first-step[data-step='name']")).to_have_attribute("data-done", "true")
            await expect(card.get_by_role("link", name="Bring your passwords")).to_have_attribute(
                "href", "#/settings/vault"
            )
            await expect(card.get_by_role("link", name="Pair your phone")).to_have_attribute("href", "#/settings/phone")
            # The step done and the first chat lead nowhere: the one is done, the other is the composer above.
            await expect(card.get_by_role("link", name="Tell Dot your name")).to_have_count(0)
            await expect(card.get_by_role("link", name="Introduce yourself")).to_have_count(0)
            assert await _fits(page)
            phone: Page = await _home(webkit, address, True, playwright.devices)
            phone_card = phone.locator("section.first-steps")
            await expect(phone_card).to_be_visible()
            await phone_card.scroll_into_view_if_needed()
            assert await _fits(phone)
            await card.get_by_role("button", name="Hide").click()
            await expect(page.locator("section.first-steps")).to_have_count(0)
            await page.reload()
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await expect(page.locator("section.first-steps")).to_have_count(0)
        finally:
            await chromium.close()
            await webkit.close()


@pytest.mark.browser
async def test_a_chat_dot_opens_at_that_is_gone_opens_new_chat_and_another_missing_task_says_not_found(
    tmp_path: Path,
) -> None:
    async with (
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = true\n') as (_runtime, _manager, _f, address),
        async_playwright() as playwright,
    ):
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            # The chat that was open last, which Dot opens at, is gone (a store started afresh).
            await page.evaluate("localStorage.setItem('dot.lastChat', '/runs/run_mvgone00_000000')")
            # Opened at no page, as Dot.app and a bookmark open it: a load of its own, with the token kept before.
            await page.goto(f"{address}/")
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await expect(page.locator(".toast")).to_contain_text("The chat that was open last is gone")
            assert page.url.endswith("#/runs"), page.url
            assert await page.evaluate("localStorage.getItem('dot.lastChat')") in (None, "/runs")
            # A task that is not the one Dot opens at still says so.
            await page.goto(f"{address}/#/runs/run_mvgone11_111111")
            await expect(page.get_by_text("Task not found")).to_be_visible()
            await expect(page.get_by_text("No task has the ID run_mvgone11_111111.")).to_be_visible()
        finally:
            await chromium.close()


@pytest.mark.browser
async def test_a_chat_dot_opened_at_and_deleted_since_says_not_found(tmp_path: Path) -> None:
    """The review: the mark of the chat a page opened at stayed once that chat loaded, and a later "not found" of it
    opened New chat as if it had been gone at the opening."""
    async with (
        _daemon(tmp_path, "", 'name = "Dot"\nowner = "Ada"\nintroduced = true\n') as (runtime, manager, _f, address),
        async_playwright() as playwright,
    ):
        run: RunRecord = await manager.start(
            "Say hello.", "task", None, None, "ui", None, None, "standard", None, None, None, ()
        )
        await asyncio.wait_for(manager.wait(run.id), _RUN_TIMEOUT_S)
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _home(chromium, address, False, playwright.devices)
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await page.evaluate(f"localStorage.setItem('dot.lastChat', '/runs/{run.id}')")
            await page.goto(f"{address}/")
            await page.wait_for_url(f"**/#/runs/{run.id}")
            await expect(page.get_by_text("Done.").first).to_be_visible()
            await runtime.store.delete_threads([run.thread_id])
            await page.goto(f"{address}/#/runs")
            await expect(page.get_by_role("textbox", name="Goal")).to_be_visible()
            await page.goto(f"{address}/#/runs/{run.id}")
            await expect(page.get_by_text(f"No task has the ID {run.id}.")).to_be_visible()
            await expect(page.locator(".toast")).to_have_count(0)
        finally:
            await chromium.close()
