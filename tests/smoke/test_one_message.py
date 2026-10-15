"""The choices for one message (item 251): the Reasoning menu offers Ultrathink where the model has Max and Ultracode
where it has Extra high, under This message only. The next message's run asks the model for that level, an Ultracode
run is told to split its work across helpers, and the pill reads the choice until the message goes; the chat keeps the
level chosen before, which the next message goes with. A run that goes on keeps the choice, and so does every session
of a campaign; a queued message carries it, and a model without the level refuses it. A real server over a stand-in of
Anthropic's provider in a browser, wide and on a phone, light and dark."""

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
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, Effort, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.endings import RESULT_RESTARTED
from dotagent.store.models import CampaignRecord, ChatChoices, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "one-message-token"
OPUS: str = "anthropic:claude-opus-5-5"
SONNET: str = "anthropic:claude-sonnet-5-5"
HAIKU: str = "anthropic:claude-haiku-5-5"
# Opus offers both levels, Sonnet Max alone, Haiku neither.
_EFFORTS: dict[str, tuple[Effort, ...]] = {
    "claude-opus-5-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-sonnet-5-5": ("low", "medium", "high", "max"),
    "claude-haiku-5-5": ("low", "medium", "high"),
}
_SPLIT: str = "Split: the user asked for the independent parts of this goal to be done at the same time"
# How the instructions of a run's step begin; a title, a lesson or a review is asked otherwise.
_RUN_PROMPT: str = "You are Dot, an autonomous agent"
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_HOME_GOAL: str = "textarea[aria-label='Goal']"
# A chat's run ends with a reply, a task's with finish.
_REPLY: ScriptedTurn = ScriptedTurn(text="Hi.", calls=())
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class Claude:
    """Anthropic's provider as far as the menu and a run need it: the models of ``_EFFORTS``; every call answers, which
    ends a chat's run or a task's, and each step's model, level and whether its instructions told it to split go to
    ``asked``."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Effort | None, bool]] = []

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        instructions: str = request.messages[0].content
        if _RUN_PROMPT in instructions:
            self.asked.append((request.model, request.effort, _SPLIT in instructions))
        turn: ScriptedTurn = _FINISH if any(tool.name == "finish" for tool in request.tools) else _REPLY
        return await ScriptedProvider(self.name, [turn]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=_EFFORTS[model],
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return tuple([await self.model_info(model) for model in _EFFORTS])

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def claude() -> Claude:
    return Claude()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, claude: Claude) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose default model is Opus 5.5, served by the stand-in."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", claude)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run_id)


async def _started(http: httpx.AsyncClient, manager: RunManager, body: dict[str, object]) -> RunRecord:
    """The run ``POST /api/runs`` started with ``body``, once it has ended."""
    response: httpx.Response = await http.post("/api/runs", json=body)
    assert response.status_code == 201, response.text
    return await _ended(manager, response.json()["id"])


async def _next_run(runtime: Runtime, before: RunRecord) -> RunRecord:
    """The run that followed ``before`` on its thread, once the daemon has it."""
    async with asyncio.timeout(_RUN_WAIT_S):
        while True:
            newest: RunRecord = (await runtime.store.list_runs(1, before.thread_id))[0]
            if newest.id != before.id:
                return newest
            await asyncio.sleep(0.05)


async def test_a_choice_for_one_message_sets_the_level_of_its_run_alone(
    runtime: Runtime, served: tuple[RunManager, str], claude: Claude
) -> None:
    manager, address = served
    claude.asked.clear()
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        thought: RunRecord = await _started(
            http, manager, {"goal": "Say hi.", "mode": "chat", "effort": "high", "once": "ultrathink"}
        )
        assert (thought.status, thought.effort, thought.once) == ("done", "max", "ultrathink")
        # The chat keeps the level chosen for the model, which its next message goes with.
        kept: ChatChoices | None = await runtime.store.chat_choices(thought.thread_id)
        assert kept is not None and kept.effort == "high"
        plain: RunRecord = await _started(
            http, manager, {"goal": "Say hi again.", "mode": "chat", "thread_id": thought.thread_id, "effort": "high"}
        )
        assert (plain.effort, plain.once) == ("high", None)
        coded: RunRecord = await _started(
            http,
            manager,
            {
                "goal": "Compare the three reports.",
                "mode": "chat",
                "thread_id": thought.thread_id,
                "effort": "high",
                "once": "ultracode",
            },
        )
        assert (coded.effort, coded.once) == ("xhigh", "ultracode")
        # A message queued in a chat where no run works starts at once, with its choice.
        queued: httpx.Response = await http.post(
            f"/api/threads/{thought.thread_id}/queue",
            json={"text": "And the fourth one.", "mode": "chat", "effort": "high", "once": "ultracode"},
        )
        assert queued.status_code == 201, queued.text
        from_queue: RunRecord = await _ended(manager, (await _next_run(runtime, coded)).id)
        assert (from_queue.effort, from_queue.once) == ("xhigh", "ultracode")
        # A model without the level refuses the choice, and nothing starts.
        for model, once in ((HAIKU, "ultrathink"), (SONNET, "ultracode")):
            refused: httpx.Response = await http.post(
                "/api/runs", json={"goal": "Say hi.", "mode": "chat", "model": model, "once": once}
            )
            assert refused.status_code == 422, refused.text
            assert "the model does not offer that reasoning effort" in refused.text
    # A run a restart cut off goes on with its choice.
    thread: ThreadRecord = await runtime.store.create_thread("Compare the reports", "test")
    cut: RunRecord = await runtime.store.create_run(
        thread.id,
        "Compare the reports",
        "chat",
        OPUS,
        None,
        "test",
        "xhigh",
        "standard",
        "ultracode",
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(
        cut.id, "failed", RESULT_RESTARTED, {"status": "failed", "result": RESULT_RESTARTED, "needs": ""}
    )
    resumed: RunRecord = await _ended(manager, (await manager.resume(cut.id)).id)
    assert (resumed.effort, resumed.once) == ("xhigh", "ultracode")
    # A campaign started with the choice keeps it, the level it asks for too: its opening session and the next one.
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        opening: RunRecord = await _started(
            http,
            manager,
            {
                "goal": "Compare the reports until each one is read.",
                "mode": "task",
                "standing": True,
                "browser": "own",
                "effort": "high",
                "once": "ultracode",
            },
        )
    assert opening.campaign_id is not None
    campaign: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
    assert (campaign.effort, campaign.once, opening.effort, opening.once) == (
        "xhigh",
        "ultracode",
        "xhigh",
        "ultracode",
    )
    session: RunRecord = await _ended(manager, (await manager.start_session(campaign.id)).id)
    assert (session.effort, session.once) == ("xhigh", "ultracode")
    # Each step asked for its run's level, and only the Ultracode runs were told to split: the chat's three, the resumed
    # run and the campaign's two sessions, each of which took two steps, its first finish turned back for want of
    # targets.
    assert claude.asked == [
        ("claude-opus-5-5", "max", False),
        ("claude-opus-5-5", "high", False),
        *[("claude-opus-5-5", "xhigh", True)] * 7,
    ]


def _menu(page: Page, scope: str) -> Locator:
    return page.locator(f"{scope}.composer-popover[aria-label='Reasoning']")


@pytest.mark.browser
async def test_the_reasoning_menu_offers_ultrathink_and_ultracode_for_the_next_message(
    runtime: Runtime, served: tuple[RunManager, str], claude: Claude, tmp_path: Path
) -> None:
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    # A chat of each other model, for its menu.
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        sonnet: RunRecord = await _started(http, manager, {"goal": "Say hi.", "mode": "chat", "model": SONNET})
        haiku: RunRecord = await _started(http, manager, {"goal": "Say hi.", "mode": "chat", "model": HAIKU})
    claude.asked.clear()
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs")
                pill: Locator = page.locator(".composer-pill[data-pill='reasoning']")
                await expect(pill).to_contain_text("Medium")
                await pill.click()
                single: Locator = _menu(page, "").get_by_role("listbox", name="This message only")
                await expect(single.get_by_role("option")).to_have_count(2)
                thinking: Locator = single.get_by_role("option", name="Ultrathink")
                await expect(thinking).to_contain_text("Max reasoning for this message only")
                await expect(single.get_by_role("option", name="Ultracode")).to_contain_text(
                    "Extra high for this message only, its independent parts given to helpers that work at the same "
                    "time"
                )
                await expect(thinking).to_have_attribute("aria-selected", "false")
                await single.get_by_role("option", name="Ultracode").scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"menu-{width}-{scheme}.png"), animations="disabled")
                if index > 0:
                    await page.close()
                    continue
                await thinking.click()
                await expect(pill).to_contain_text("Ultrathink")
                await expect(pill.locator("[data-icon='sparkle']")).to_have_count(1)
                await page.screenshot(path=str(tmp_path / "pill-ultrathink.png"), animations="disabled")
                await page.locator(_HOME_GOAL).fill("Say hi.")
                await page.locator(_HOME_GOAL).press("Enter")
                await expect(page).to_have_url(re.compile(r"#/runs/run_"))
                dock: Locator = page.locator(".dock .composer-pill[data-pill='reasoning']")
                # The chat keeps the level chosen before, and its box shows it.
                await expect(dock).to_contain_text("Medium")
                first: RunRecord = await _ended(manager, page.url.rpartition("/")[2])
                assert (first.effort, first.once) == ("max", "ultrathink")
                await dock.click()
                await _menu(page, ".dock ").get_by_role("option", name="Ultracode").click()
                await expect(dock).to_contain_text("Ultracode")
                await expect(dock.locator("[data-icon='branch']")).to_have_count(1)
                await page.get_by_role("textbox", name="Message for Dot").fill("Compare the three reports.")
                await page.get_by_role("textbox", name="Message for Dot").press("Enter")
                second: RunRecord = await _ended(manager, (await _next_run(runtime, first)).id)
                assert (second.effort, second.once) == ("xhigh", "ultracode")
                # The next message goes with the chat's level again.
                await expect(dock).to_contain_text("Medium")
                await page.close()
            assert claude.asked == [("claude-opus-5-5", "max", False), ("claude-opus-5-5", "xhigh", True)]
            # Sonnet offers Max alone, so Ultrathink alone; Haiku offers neither, so the menu has no such choice.
            for run, offered in ((sonnet, ["Ultrathink"]), (haiku, [])):
                page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await page.locator(".dock .composer-pill[data-pill='reasoning']").click()
                menu: Locator = _menu(page, ".dock ")
                await expect(menu.get_by_role("listbox", name="Reasoning")).to_be_visible()
                await expect(
                    menu.get_by_role("listbox", name="This message only").locator(".pop-option-text > span:first-child")
                ).to_have_text(offered)
                await page.close()
        finally:
            await browser.close()
