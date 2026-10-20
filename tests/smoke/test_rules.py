"""A rule in plain words, end to end (item 110): a sentence typed on the Rules tab is drafted by the review model (a
scripted one here), read back, fills the form and is added; words that make no rule get the model's reason; an
approval card offers "Make a rule of this", which opens the Rules tab with a sentence about the action in the box. In a
headless Chrome at a wide and at a phone width, in light and in dark."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.agent.review import RULE_TOOL
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.policy.drafting import command_pattern
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "rules-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0
_SENTENCE: str = "Let Dot run git status and git diff without asking me"
_COMMANDS: list[str] = ["git status", "git diff"]
_READ_BACK: str = (
    "For “git status” or “git diff” run alone in the shell, with plain arguments (after a cd at most): "
    "Dot does it without asking."
)
_NO_RULE: str = "This asks about the weather, not about what Dot may do."


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(_PROVIDER, ()).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(_PROVIDER, ()).list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nreview = "{SCRIPTED_MODEL}"\n',
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
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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


def _turn(tool: str, arguments: JsonObject) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _draft(commands: list[str], problem: str) -> ScriptedTurn:
    """The review model's draft: the git commands, or no rule with ``problem`` as the reason."""
    arguments: JsonObject = {
        "tool": "shell" if problem == "" else "",
        "answer": "allow",
        "commands": list(commands),
        "mentions": [],
        "problem": problem,
    }
    return _turn(RULE_TOOL, arguments)


def _address(client: httpx.AsyncClient) -> tuple[str, int]:
    port: int | None = client.base_url.port
    assert port is not None
    return client.base_url.host, port


async def _pending(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def test_a_rule_said_in_plain_words_is_drafted_read_back_and_added(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    MODEL.script = ScriptedProvider(_PROVIDER, [_draft([], _NO_RULE), _draft(_COMMANDS, ""), _draft(_COMMANDS, "")])
    host, port = _address(client)
    pattern: str = command_pattern(_COMMANDS)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"http://{host}:{port}/#/settings/rules")
            await page.get_by_role("button", name="New rule").click()
            # The button that opened the forms shuts them: a cross and Close, not the plus of New rule (item 437).
            opener: Locator = page.locator(".rule-panel > .panel-head").get_by_role("button", name="Close")
            await expect(opener.locator(".icon")).to_have_attribute("data-icon", "x")
            await expect(opener).to_have_attribute("aria-expanded", "true")
            words = page.get_by_role("textbox", name="The rule in your words", exact=True)
            draft = page.get_by_role("button", name="Draft the rule", exact=True)
            await page.get_by_role("button", name="Ask me before Dot deletes anything with rm").click()
            await expect(words).to_have_value("Ask me before Dot deletes anything with rm")
            # Closed and opened again, the forms keep what was written (item 373).
            await page.locator(".rule-panel > .panel-head").get_by_role("button", name="Close").click()
            await expect(words).to_be_hidden()
            await expect(page.get_by_role("button", name="New rule").locator(".icon")).to_have_attribute(
                "data-icon", "plus"
            )
            await page.get_by_role("button", name="New rule").click()
            await expect(words).to_have_value("Ask me before Dot deletes anything with rm")
            await expect(words).to_be_focused()
            await words.fill("What will the weather be tomorrow?")
            await draft.click()
            await expect(page.locator(".rule-outcome.bad")).to_have_text(_NO_RULE)
            await expect(page.get_by_role("textbox", name="Tool", exact=True)).to_have_value("")
            await words.fill(_SENTENCE)
            await words.press("Meta+Enter")
            await expect(page.locator(".rule-outcome.ok .rule-readback")).to_have_text(_READ_BACK)
            await expect(page.get_by_role("textbox", name="Tool", exact=True)).to_have_value("shell")
            await expect(page.get_by_role("textbox", name="Pattern", exact=True)).to_have_value(pattern)
            await expect(page.get_by_role("textbox", name="Why", exact=True)).to_have_value(_READ_BACK)
            await expect(page.locator(".segmented.answers button[aria-pressed='true']")).to_have_text("Do it")
            add = page.get_by_role("button", name="Add rule", exact=True)
            await expect(add).to_be_focused()
            await page.screenshot(path=str(tmp_path / "drafted-1280-light.png"), full_page=True)
            await add.click()
            rows = page.locator(".list-row .rule-sentence")
            await expect(rows).to_have_count(1)
            await expect(rows.first.locator(".badge")).to_have_text("Do it")
            await expect(rows.first.locator("code[title]")).to_have_attribute("title", pattern)
            await expect(page.locator(".list-row .row-text")).to_have_text(_READ_BACK)
            await expect(words).to_have_value("")
            await expect(page.locator(".rule-outcome")).to_be_hidden()
            await page.screenshot(path=str(tmp_path / "added-1280-light.png"), full_page=True)
            await page.close()

            phone: Page = await browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
            await phone.goto(ui_url(host, port, TOKEN))
            await phone.goto(f"http://{host}:{port}/#/settings/rules")
            await phone.get_by_role("button", name="New rule").click()
            await phone.get_by_role("textbox", name="The rule in your words", exact=True).fill(_SENTENCE)
            await phone.get_by_role("button", name="Draft the rule", exact=True).click()
            await expect(phone.locator(".rule-outcome.ok")).to_be_visible()
            await phone.screenshot(path=str(tmp_path / "drafted-390-dark.png"), full_page=True)
            await phone.close()
        finally:
            await browser.close()
    kept: list[JsonObject] = (await client.get("/api/rules")).json()
    assert [(rule["behavior"], rule["tool_glob"], rule["match_regex"], rule["description"]) for rule in kept] == [
        ("allow", "shell", pattern, _READ_BACK)
    ]


async def test_an_approval_card_offers_to_make_a_rule_of_its_action(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    added: httpx.Response = await client.post(
        "/api/rules",
        json={"behavior": "ask", "tool_glob": "write_file", "match_regex": "", "description": "I read every write"},
    )
    assert added.status_code == 201, added.text
    notes: Path = tmp_path / "notes.txt"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn("write_file", {"path": str(notes), "content": "milk, bread"}),
            _turn(FINISH_TOOL, {"answer": "The notes are written.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Write the shopping notes",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "ask",
        False,
        (),
    )
    asked: ApprovalRecord = await _pending(runtime, run.id)
    assert asked.tool == "write_file"
    assert asked.severity not in ("question", "handoff"), asked
    host, port = _address(client)
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                offer = page.get_by_role("link", name="Make a rule of this").first
                await expect(offer).to_be_visible()
                await page.screenshot(path=str(tmp_path / "approval-1280-dark.png"))
                await offer.click()
                await expect(page).to_have_url(re.compile(r"#/settings/rules\?sentence="))
                words = page.get_by_role("textbox", name="The rule in your words", exact=True)
                await expect(words).to_have_value(re.compile(r"^Let Dot use write_file for this without asking me: \S"))
                await expect(words).to_be_focused()
                await page.screenshot(path=str(tmp_path / "offered-1280-dark.png"), full_page=True)
                await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, True, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended


async def test_a_card_the_guard_raised_offers_no_rule_and_says_why(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A message sent for the user waits for their yes in every mode, yolo too, whatever their rules say (item 429): its
    card, in the chat, offers no rule, which would never apply, and says so; on a desktop and on a phone. The card is
    refused at the end, so nothing is sent."""
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn("shell", {"command": "echo hi | mail -s Hello ada@example.com"}),
            _turn(FINISH_TOOL, {"answer": "The mail was not sent.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Mail Ada hello",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "yolo",
        False,
        (),
    )
    asked: ApprovalRecord = await _pending(runtime, run.id)
    assert "Dot asks you before a message or a post in your name" in asked.summary, asked
    host, port = _address(client)
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, height in ((1280, 900), (390, 844)):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": height}, color_scheme="dark"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                    await expect(page.get_by_text("no rule turns that off").first).to_be_visible()
                    await expect(page.get_by_role("link", name="Make a rule of this")).to_have_count(0)
                    await page.screenshot(path=str(tmp_path / f"guard-{width}-dark.png"))
                    await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, False, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
