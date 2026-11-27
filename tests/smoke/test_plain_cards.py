"""What a card says in plain words (items 22 and 25 of the 2026-11-26 review), in Chrome on a daemon's own pages.

An approval card says what Dot wants to do and why it asks in one plain sentence; the command stays in its box, and what
the step counts as, its risk and the policy's own words ("aggressive preset still asks for: privilege") are folded under
Details. A run that ended for a fault of Dot's says what happened and what to do, and the words the program used stand
under Details, in English and in Turkish. No network and no model service is used: a scripted model plays the runs."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import DotError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "plain-cards-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SUDO: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sudo -n true"}),))


class FailingProvider(ScriptedProvider):
    """A model whose service fails: the run ends with a fault of the harness's, not an answer."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [])

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        raise DotError("the scripted model failed on purpose", {})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], provider: ScriptedProvider
) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, under the aggressive preset, whose model is `provider`."""
    config: AppConfig = eval_config(load_default_config())
    aggressive: AppConfig = config.model_copy(
        update={"policy": config.policy.model_copy(update={"preset": "aggressive"})}
    )
    runtime: Runtime = await open_runtime(aggressive, login_env)
    try:
        await runtime.router.register(_PROVIDER, provider)
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
    finally:
        await close_runtime(runtime)


async def _page(browser: Browser, address: str, language: str) -> Page:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context = await browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
    page: Page = await context.new_page()
    await page.add_init_script(
        f"sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.uiLanguage', '{language}')"
    )
    await page.goto(ui_url(host, port, TOKEN))
    return page


async def _asking(runtime: Runtime, manager: RunManager) -> tuple[RunRecord, ApprovalRecord]:
    run: RunRecord = await manager.start(
        "Check the privileges", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    deadline: float = time.monotonic() + WAIT_S
    while not (
        asked := [record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run.id]
    ):
        assert time.monotonic() < deadline, f"the run {run.id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return run, asked[0]


@pytest.mark.parametrize(
    ("language", "why", "details"),
    [
        ("en", "Dot asks because this needs administrator rights.", "Details"),
        ("tr", "Dot soruyor, çünkü bu", "Ayrıntılar"),
    ],
)
async def test_an_approval_card_says_why_it_asks_in_one_plain_sentence(
    login_env: dict[str, str], language: str, why: str, details: str
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        provider: ScriptedProvider = ScriptedProvider(_PROVIDER, [_SUDO])
        async with _daemon(login_env, provider) as (runtime, manager, address), async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _page(browser, address, language)
                run, _approval = await _asking(runtime, manager)
                await page.goto(f"{address}/#/runs/{run.id}")
                card: Locator = page.locator(".approval").first
                await expect(card).to_be_visible(timeout=WAIT_S * 1000)
                # One plain sentence says why Dot asks; the policy's own words are not on the card but under Details.
                await expect(card.locator(".approval-why")).to_contain_text(why)
                await expect(card.locator(".badge")).to_have_count(0)
                text: str = await card.inner_text()
                assert "aggressive preset" not in text
                fold: Locator = card.locator("details.approval-fold")
                await expect(fold).to_have_count(1)
                assert await fold.get_attribute("open") is None
                await expect(fold.locator("summary")).to_have_text(details)
                await fold.locator("summary").click()
                await expect(fold).to_contain_text("aggressive preset still asks for: privilege")
            finally:
                await browser.close()


@pytest.mark.parametrize(
    ("language", "title", "details"),
    [("en", "The task stopped", "Details"), ("tr", "Görev durdu", "Ayrıntılar")],
)
async def test_a_run_that_ended_for_a_fault_says_what_to_do_and_folds_the_words_of_the_program(
    login_env: dict[str, str], language: str, title: str, details: str
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env, FailingProvider()) as (runtime, manager, address),
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _page(browser, address, language)
                run: RunRecord = await manager.start(
                    "Fail on purpose",
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
                await page.goto(f"{address}/#/runs/{run.id}")
                answer: Locator = page.locator(".answer-failed")
                await expect(answer).to_be_visible(timeout=WAIT_S * 1000)
                await expect(answer.locator(".answer-alert")).to_contain_text(title)
                # The words the program used are folded: not on the card until Details is opened.
                raw: Locator = answer.locator("details.error-details")
                await expect(raw.locator("summary")).to_have_text(details)
                assert await raw.get_attribute("open") is None
                await raw.locator("summary").click()
                await expect(raw).to_contain_text("failed on purpose")
            finally:
                await browser.close()
