"""A rule in plain words, end to end (items 110 and 622): the words and the choice of the Rules tab's one line are
made one sentence for the review model (a scripted one here), which drafts the rule; the draft is read back as "Dot
understood: …", shown with its parts under Details, and added only when the owner says so; words that make no rule
keep the words in the box and say why, with a way to fill in the details by hand; a draft that is not what was chosen
is not offered. An approval card offers "Make a rule of this", which fills the box and the choice. Advanced holds the
rest of the page, shut. In a headless Chrome at a wide and at a phone width, in light and in dark."""

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
from dotagent.agent.review import RULE_TOOL
from dotagent.config import load_config
from dotagent.core.paths import skills_dir
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
# What the owner types in the box, the sentence the drafter is given for it with "Allow on its own", and what the
# drafted rule reads back as (the sentence the drafter writes, and the page's own words for it).
_WANTS: str = "run git status and git diff"
_ASKED: str = "When Dot wants to run git status and git diff, it should do it without asking me."
_COMMANDS: list[str] = ["git status", "git diff"]
_READ_BACK: str = (
    "For “git status” or “git diff” run alone in the shell, with plain arguments (after a cd at most): "
    "Dot does it without asking."
)
_SAID: str = (
    "When Dot wants to run “git status” or “git diff” (with plain arguments, after changing folder at most), it goes "
    "ahead without asking."
)
_NO_RULE: str = "This asks about the weather, not about what Dot may do."
_WEATHER: str = "What will the weather be tomorrow?"
_SIZES: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
# The words of a program that a page for someone who knows no software does not say.
_TECHNICAL: re.Pattern[str] = re.compile(
    r"\b(pattern|tool|preset|matching|regex|regular expression)s?\b", re.IGNORECASE
)
# Where the line of the adder stands: the box, the choice and the button, as the page lays them out.
_LINE: str = """() => {
  const rect = (node) => node.getBoundingClientRect();
  const wants = rect(document.querySelector('[aria-label="When Dot wants to"]'));
  const should = rect(document.querySelector('[aria-label="It should"]'));
  const add = rect([...document.querySelectorAll("button")].find((button) => button.textContent.trim() === "Add rule"));
  const card = rect(document.querySelector(".rule-adder-card"));
  return { wants, should, add, card, width: window.innerWidth };
}"""


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None
        # What the model was asked, each call as the text of its messages: the sentence the drafter is given is in it.
        self.asked: list[str] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
        self.asked.append("\n".join(message.content for message in request.messages))
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


def _draft(commands: list[str], problem: str, answer: str = "allow") -> ScriptedTurn:
    """The review model's draft: the git commands, or no rule with ``problem`` as the reason."""
    arguments: JsonObject = {
        "tool": "shell" if problem == "" else "",
        "answer": answer,
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


async def _no_rules(client: httpx.AsyncClient) -> None:
    """The rules of this test alone: every earlier one is deleted."""
    for rule in (await client.get("/api/rules")).json():
        assert (await client.delete(f"/api/rules/{rule['id']}")).status_code == 200


async def _rules_page(
    browser: Browser, host: str, port: int, width: int, scheme: Literal["light", "dark"], height: int = 900
) -> Page:
    """The Rules tab in a window ``width`` wide; one under 720 is a phone, a touch screen."""
    phone: bool = width < 720
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        is_mobile=phone,
        has_touch=phone,
        reduced_motion="reduce",
    )
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/rules")
    return page


async def test_a_rule_said_in_plain_words_is_drafted_read_back_and_added(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """The line, wide and on a phone: the words and the choice are made one sentence for the drafter, the draft is read
    back as a sentence with its parts under Details, the words are kept until the owner goes back to them, and Add it
    adds the rule the owner chose the answer of. Advanced, which holds what is always on, is shut."""
    await _no_rules(client)
    MODEL.asked.clear()
    MODEL.script = ScriptedProvider(_PROVIDER, [_draft(_COMMANDS, ""), _draft(_COMMANDS, ""), _draft(_COMMANDS, "")])
    host, port = _address(client)
    pattern: str = command_pattern(_COMMANDS)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _rules_page(browser, host, port, 1280, "light")
            words: Locator = page.get_by_role("textbox", name="When Dot wants to", exact=True)
            choice: Locator = page.get_by_role("combobox", name="It should", exact=True)
            add: Locator = page.get_by_role("button", name="Add rule", exact=True)
            # The line: the box, the choice that starts at asking, and Add rule, off while the box is empty.
            await expect(page.get_by_role("heading", level=1)).to_have_text("Rules")
            await expect(choice).to_have_value("ask")
            await expect(choice.locator("option")).to_have_text(["Allow on its own", "Ask me first", "Leave it to me"])
            await expect(add).to_be_disabled()
            await expect(page.locator(".rule-hint")).to_have_text(
                "Dot stops and asks you every time; the run waits for your answer."
            )
            # Advanced is shut: what is in it is not on the page until it is opened (below).
            await expect(page.locator("details.rule-advanced")).not_to_have_attribute("open", "")
            await expect(page.locator(".rule-always-note")).to_be_hidden()
            await words.fill(_WANTS)
            await choice.select_option("allow")
            await expect(add).to_be_enabled()
            await expect(page.locator(".rule-hint")).to_contain_text("Dot does it without asking.")
            await add.click()
            pending: Locator = page.locator(".rule-pending")
            await expect(pending.locator(".rule-understood")).to_have_text(f"Dot understood: {_SAID}")
            # What the drafter was given is the owner's wish in one sentence, the choice in their voice.
            assert any(_ASKED in asked for asked in MODEL.asked), MODEL.asked
            # The words and the choice stay as they were drafted from; Add rule waits; Add it has the focus.
            await expect(words).to_be_disabled()
            await expect(choice).to_be_disabled()
            await expect(add).to_be_disabled()
            await expect(pending.get_by_role("button", name="Add it", exact=True)).to_be_focused()
            # A drafted rule is tried on the last days at once, and said in a line.
            await expect(pending.locator(".rule-lately")).to_contain_text("last 30 days")
            # Its parts are under Details, shut until asked for.
            details: Locator = pending.get_by_role("button", name="Details", exact=True)
            await expect(details).to_have_attribute("aria-expanded", "false")
            await expect(page.get_by_role("textbox", name="Tool", exact=True)).to_be_hidden()
            await details.click()
            await expect(details).to_have_attribute("aria-expanded", "true")
            await expect(page.get_by_role("textbox", name="Tool", exact=True)).to_have_value("shell")
            await expect(page.get_by_role("textbox", name="Pattern", exact=True)).to_have_value(pattern)
            answer: Locator = page.get_by_role("combobox", name="Answer", exact=True)
            await expect(answer).to_have_value("allow")
            await expect(page.get_by_role("textbox", name="Why", exact=True)).to_have_value(_READ_BACK)
            # The answer is the choice made above: the drafter wrote the pattern for it, so it is not changed alone. A
            # part changed makes the rule the owner's own, with the answer to set, and the draft's sentence is no longer
            # the owner's why.
            await expect(answer).to_be_disabled()
            await expect(pending.locator(".rule-understood-label")).to_have_text("Dot understood:")
            await page.get_by_role("textbox", name="Pattern", exact=True).fill(pattern + "x")
            await expect(answer).to_be_enabled()
            await expect(pending.locator(".rule-understood-label")).to_have_text("Your rule:")
            await expect(page.get_by_role("textbox", name="Why", exact=True)).to_have_value("")
            await page.screenshot(path=str(tmp_path / "drafted-1280-light.png"), full_page=True)
            # Change the words: the draft is put away and the words are the owner's again, where they were.
            await pending.get_by_role("button", name="Change the words", exact=True).click()
            await expect(pending).to_have_count(0)
            await expect(words).to_have_value(_WANTS)
            await expect(words).to_be_enabled()
            await expect(words).to_be_focused()
            await expect(choice).to_have_value("allow")
            await add.click()
            await expect(pending.locator(".rule-understood")).to_have_text(f"Dot understood: {_SAID}")
            await pending.get_by_role("button", name="Add it", exact=True).click()
            rows: Locator = page.locator(".rule-list > .rule-row")
            await expect(rows).to_have_count(1)
            await expect(rows.first.locator(".rule-sentence")).to_have_text(_SAID)
            await expect(rows.first.locator(".badge")).to_have_text("goes ahead without asking")
            await expect(rows.first.locator(".badge")).to_have_class(re.compile(r"\bb-ok\b"))
            await expect(pending).to_have_count(0)
            await expect(words).to_have_value("")
            await expect(choice).to_have_value("ask")
            await expect(page.locator(".set-meta")).to_have_text("1 rule")
            # The tool, the pattern and why are the rule's Details: said once, for whoever wants them.
            await rows.first.get_by_role("button", name="Details", exact=True).click()
            facts: Locator = rows.first.locator(".rule-facts")
            await expect(facts.locator("dt")).to_have_text(["Tool", "Pattern", "Why", "Used lately"])
            await expect(facts.locator("dd").nth(0)).to_have_text("shell")
            await expect(facts.locator("dd code.rule-pattern")).to_have_text(pattern)
            await expect(facts.locator("dd").nth(2)).to_have_text(_READ_BACK)
            await page.screenshot(path=str(tmp_path / "added-1280-light.png"), full_page=True)
            # What is always on is in Advanced, which opens on its summary and says it in words.
            await page.locator("details.rule-advanced > summary").click()
            await expect(page.get_by_role("heading", name="Always on, whatever your rules say")).to_be_visible()
            # What a yolo run keeps is all of it: it keeps a secret from going out when it has read outside content,
            # and has the review model judge what that content may be after (item 525), as safety.md says (item 559).
            await expect(page.locator(".rule-always-note")).to_contain_text(
                "once a run has read something from outside (a web page, a mail), a secret is still refused on its "
                "way out, and the review model checks any step that page may be after."
            )
            await page.close()

            phone: Page = await _rules_page(browser, host, port, 390, "dark")
            await phone.get_by_role("textbox", name="When Dot wants to", exact=True).fill(_WANTS)
            await phone.get_by_role("combobox", name="It should", exact=True).select_option("allow")
            await phone.get_by_role("button", name="Add rule", exact=True).click()
            await expect(phone.locator(".rule-pending .rule-understood")).to_have_text(f"Dot understood: {_SAID}")
            assert await phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await phone.screenshot(path=str(tmp_path / "drafted-390-dark.png"), full_page=True)
            await phone.close()
        finally:
            await browser.close()
    kept: list[JsonObject] = (await client.get("/api/rules")).json()
    assert [(rule["behavior"], rule["tool_glob"], rule["match_regex"], rule["description"]) for rule in kept] == [
        ("allow", "shell", pattern, _READ_BACK)
    ]


async def test_words_that_make_no_rule_stay_in_the_box_and_the_details_can_be_filled_in_by_hand(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A draft that does not come, wide and on a phone: the model's reason is said in plain words, the words and the
    choice stay, and what is typed again puts the message away. A model that cannot answer is not blamed on the words.
    "Fill in the details yourself" opens the parts to fill in, and the rule is added with the answer chosen."""
    await _no_rules(client)
    # The first reply says why the words make no rule; the scripted model has no reply left after it, which is a model
    # that cannot answer: a second, different failure.
    MODEL.script = ScriptedProvider(_PROVIDER, [_draft([], _NO_RULE)])
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in ((1280, "light"), (390, "dark")):
                if width == 390:
                    MODEL.script = ScriptedProvider(_PROVIDER, [_draft([], _NO_RULE)])
                page: Page = await _rules_page(browser, host, port, width, scheme)
                words: Locator = page.get_by_role("textbox", name="When Dot wants to", exact=True)
                choice: Locator = page.get_by_role("combobox", name="It should", exact=True)
                add: Locator = page.get_by_role("button", name="Add rule", exact=True)
                bad: Locator = page.locator(".rule-outcome.bad")
                await words.fill(_WEATHER)
                await add.click()
                await expect(bad).to_contain_text("Dot could not make a rule of these words.")
                await expect(bad).to_contain_text(_NO_RULE)
                await expect(bad).to_contain_text("Your words are still in the box.")
                await expect(words).to_have_value(_WEATHER)
                await expect(words).to_be_enabled()
                await expect(choice).to_have_value("ask")
                await expect(page.locator(".rule-pending")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / f"no-rule-{width}-{scheme}.png"), full_page=True)
                # Typing again puts the message away.
                await words.press("End")
                await words.type("!")
                await expect(bad).to_be_hidden()
                # The model could not answer at all: the words are not what failed.
                await add.click()
                await expect(bad).to_contain_text("Dot could not read your words as a rule just now.")
                await expect(bad).to_contain_text("fill in the details yourself")
                await expect(words).to_have_value(f"{_WEATHER}!")
                # The details by hand: the parts are open, the tool is asked for, and the sentence follows them.
                await bad.get_by_role("button", name="Fill in the details yourself", exact=True).click()
                pending: Locator = page.locator(".rule-pending")
                await expect(pending.locator(".rule-understood")).to_contain_text("Your rule:")
                await expect(pending.get_by_role("button", name="Details", exact=True)).to_have_attribute(
                    "aria-expanded", "true"
                )
                tool: Locator = page.get_by_role("textbox", name="Tool", exact=True)
                await expect(tool).to_be_focused()
                # The answer is the choice made, and the owner's to set here, where no draft was written for it.
                answer: Locator = page.get_by_role("combobox", name="Answer", exact=True)
                await expect(answer).to_have_value("ask")
                await expect(answer).to_be_enabled()
                await expect(pending.get_by_role("button", name="Add it", exact=True)).to_be_disabled()
                await tool.fill("write_file")
                # The letters of a word, anywhere: a pattern that keeps to whole words is "mentions" (below).
                await page.get_by_role("textbox", name="Pattern", exact=True).fill("invoice")
                await expect(pending.locator(".rule-understood .rule-sentence")).to_have_text(
                    "When Dot wants to write a file that contains “invoice”, it asks you first."
                )
                await page.screenshot(path=str(tmp_path / f"by-hand-{width}-{scheme}.png"), full_page=True)
                await pending.get_by_role("button", name="Add it", exact=True).click()
                rows: Locator = page.locator(".rule-list > .rule-row")
                await expect(rows).to_have_count(1)
                await expect(rows.first.locator(".rule-sentence")).to_have_text(
                    "When Dot wants to write a file that contains “invoice”, it asks you first."
                )
                await expect(words).to_have_value("")
                saved: list[JsonObject] = (await client.get("/api/rules")).json()
                assert [
                    (rule["behavior"], rule["tool_glob"], rule["match_regex"], rule["description"]) for rule in saved
                ] == [("ask", "write_file", "invoice", "")]
                await _no_rules(client)
                await page.close()
        finally:
            await browser.close()


async def test_a_draft_that_is_not_what_was_chosen_is_not_offered(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """The owner chose Ask me first and the model drafted a rule that lets Dot act: a rule that does more than the owner
    chose is never added from words. The page says what it understood and what was chosen, and adds nothing; choosing
    again puts the message away."""
    await _no_rules(client)
    MODEL.script = ScriptedProvider(_PROVIDER, [_draft(_COMMANDS, "", answer="allow")])
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _rules_page(browser, host, port, 1280, "dark")
            words: Locator = page.get_by_role("textbox", name="When Dot wants to", exact=True)
            await words.fill(_WANTS)
            await page.get_by_role("button", name="Add rule", exact=True).click()
            bad: Locator = page.locator(".rule-outcome.bad")
            await expect(bad).to_contain_text("Dot read your words differently from your choice.")
            await expect(bad.locator(".rule-sentence")).to_have_text(_SAID)
            await expect(bad).to_contain_text("You chose “Ask me first”, so nothing was added.")
            await expect(page.locator(".rule-pending")).to_have_count(0)
            await expect(page.locator(".rule-list > .rule-row")).to_have_count(0)
            await expect(words).to_be_enabled()
            await page.screenshot(path=str(tmp_path / "not-chosen-1280-dark.png"), full_page=True)
            await page.get_by_role("combobox", name="It should", exact=True).select_option("allow")
            await expect(bad).to_be_hidden()
            await page.close()
        finally:
            await browser.close()
    assert (await client.get("/api/rules")).json() == []


async def test_an_approval_card_offers_to_make_a_rule_of_its_action(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await _no_rules(client)
    added: httpx.Response = await client.post(
        "/api/rules",
        json={"behavior": "ask", "tool_glob": "write_file", "match_regex": "", "description": "I read every write"},
    )
    assert added.status_code == 201, added.text
    notes: Path = tmp_path / "notes.txt"
    # The drafter's call comes while the run waits for its answer: between the run's two.
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn("write_file", {"path": str(notes), "content": "milk, bread"}),
            _turn(RULE_TOOL, {"tool": "write_file", "answer": "allow", "commands": [], "mentions": [], "problem": ""}),
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
                await expect(page).to_have_url(re.compile(r"#/settings/rules\?wants="))
                # The action fills the box and the choice is the one that stops asking, for the owner to change.
                words = page.get_by_role("textbox", name="When Dot wants to", exact=True)
                await expect(words).to_have_value(re.compile(r"^use write_file: \S"))
                await expect(page.get_by_role("combobox", name="It should", exact=True)).to_have_value("allow")
                await expect(words).to_be_focused()
                await page.screenshot(path=str(tmp_path / "offered-1280-dark.png"), full_page=True)
                # And it works: the rule is drafted from them, read back, and added.
                await page.get_by_role("button", name="Add rule", exact=True).click()
                await expect(page.locator(".rule-pending .rule-understood")).to_have_text(
                    "Dot understood: When Dot wants to write a file, it goes ahead without asking."
                )
                await page.get_by_role("button", name="Add it", exact=True).click()
                await expect(page.locator(".rule-list > .rule-row")).to_have_count(2)
                await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, True, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    kept: list[JsonObject] = (await client.get("/api/rules")).json()
    assert [(rule["behavior"], rule["tool_glob"], rule["match_regex"]) for rule in kept] == [
        ("ask", "write_file", ""),
        ("allow", "write_file", ""),
    ]


async def test_a_card_the_guard_raised_offers_no_rule_and_says_why(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A message sent for the user waits for their yes in every mode that asks, whatever their rules say (item 429): its
    card, in the chat, offers no rule, which would never apply, and says so; on a desktop and on a phone. The card is
    refused at the end, so nothing is sent. (A yolo run asks nothing at all, item 443, so the card is of a mode that
    asks.)"""
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
        "ask",
        False,
        (),
    )
    asked: ApprovalRecord = await _pending(runtime, run.id)
    assert "Dot asks before a message or a post in your name" in asked.summary, asked
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
                    await expect(page.get_by_text("No rule can turn that off").first).to_be_visible()
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


async def test_a_card_for_a_change_of_dots_own_files_offers_no_rule_and_says_why(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 559: the policy asks about a change of Dot's own files before it reads a rule (item 398), in every mode but
    yolo, so its card offers no rule, which would never apply, and says so, as the card of a message in the user's
    name does. The card is refused at the end, so nothing is written."""
    own: Path = skills_dir() / "tide-notes" / "SKILL.md"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn("write_file", {"path": str(own), "content": "# The tide\n\nHigh water at noon.\n"}),
            _turn(FINISH_TOOL, {"answer": "The skill was not written.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Write a skill about the tide",
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
    assert asked.tool == "write_file" and "self_change" in asked.args["flags"], asked
    host, port = _address(client)
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                await expect(page.get_by_text("No rule can turn that off").first).to_be_visible()
                await expect(page.get_by_role("link", name="Make a rule of this")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / "own-files-1280-dark.png"))
                await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, False, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    assert not await asyncio.to_thread(own.exists)


async def _fresh_rules(client: httpx.AsyncClient) -> list[str]:
    """The rules this test reads, alone in the list: everything the shell does, then a question about ``git push`` that
    the first one keeps from ever deciding, then a question about writing files."""
    await _no_rules(client)
    made: list[str] = []
    for behavior, glob, pattern in (
        ("allow", "shell", ""),
        ("ask", "shell", r"\bgit push\b"),
        ("ask", "write_file", ""),
    ):
        created: httpx.Response = await client.post(
            "/api/rules", json={"behavior": behavior, "tool_glob": glob, "match_regex": pattern, "description": ""}
        )
        assert created.status_code == 201, created.text
        made.append(created.json()["id"])
    return made


async def test_a_rule_moves_with_its_arrows_and_one_that_is_never_used_says_so(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 449, on a wide window and a phone, in light and dark: the question under a rule that fits all it fits says
    it is never used, and which rule decides in its place, in words; its up arrow moves it above that rule, the note
    goes, the arrow keeps the focus (the other arrow once the rule is first), and the ends' arrows are off. A hand-off
    of every step added last keeps every other rule from deciding wherever it stands, and their notes say that moving
    does not help (item 481). The arrows show when the pointer is on the row, and always on a touch screen (item
    622)."""
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SIZES:
                await _fresh_rules(client)
                # The phone is a touch screen, where a row's buttons always show; on the wide window they show on hover.
                phone: bool = width < 720
                page: Page = await _rules_page(browser, host, port, width, scheme, height=844 if phone else 900)
                rows: Locator = page.locator(".rule-list > .rule-row")
                await expect(rows).to_have_count(3)
                never: Locator = rows.nth(1).locator(".badge", has_text="Never used")
                await expect(never).to_be_visible()
                await expect(rows.nth(1).locator(".rule-never")).to_have_text(
                    "Rule 1 already covers everything this rule covers and is checked first, so this rule is never "
                    "used. Move it above rule 1, or delete one of them."
                )
                await expect(page.locator(".rule-list .badge", has_text="Never used")).to_have_count(1)
                await expect(page.get_by_role("button", name="Move rule 1 up")).to_be_disabled()
                await expect(page.get_by_role("button", name="Move rule 3 down")).to_be_disabled()
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                shown: str = "1" if phone else "0"
                await expect(page.get_by_role("button", name="Move rule 2 up")).to_have_css("opacity", shown)
                if not phone:
                    await rows.nth(1).hover()
                    await expect(page.get_by_role("button", name="Move rule 2 up")).to_have_css("opacity", "1")
                await page.screenshot(path=str(tmp_path / f"rules-{width}-{scheme}.png"), full_page=True)
                await page.get_by_role("button", name="Move rule 2 up").click()
                await expect(rows.nth(0).locator(".badge", has_text="asks you first")).to_be_visible()
                await expect(page.locator(".rule-list .badge", has_text="Never used")).to_have_count(0)
                # The moved rule is first now, so its up arrow is off and its down arrow holds the focus.
                await expect(page.get_by_role("button", name="Move rule 1 down")).to_be_focused()
                everything: httpx.Response = await client.post(
                    "/api/rules", json={"behavior": "handoff", "tool_glob": "*", "match_regex": "", "description": ""}
                )
                assert everything.status_code == 201, everything.text
                await page.reload()
                await expect(rows).to_have_count(4)
                handed: Locator = page.locator(".rule-list .badge", has_text="Never used")
                await expect(handed).to_have_count(3)
                await expect(rows.first.locator(".rule-never")).to_have_text(
                    "Rule 4 leaves everything this rule covers to you, and a rule like that always wins, wherever it "
                    "stands. So this rule is never used. Narrow one of them, or delete one."
                )
                await expect(rows.nth(3).locator(".badge", has_text="Never used")).to_have_count(0)
                await page.close()
        finally:
            await browser.close()


async def test_a_rule_reads_from_its_pattern_whatever_its_why_says(runtime: Runtime, client: httpx.AsyncClient) -> None:
    """Item 622, after its review: a sentence says neither less nor more than its rule fits. It is read from the
    pattern and from nothing else, so a why that says the rule is narrower than it is (the form of old kept the
    drafter's sentence over a pattern the owner changed, and ``POST /api/rules`` takes any why) changes nothing, and a
    pattern that is more than words is shown as it is, whole for a rule that lets Dot act, with the pattern as it is in
    its tooltip. On a wide window and a phone, in light and dark."""
    alone: str = (
        "For “git push” run alone in the shell, with plain arguments (after a cd at most): Dot does it without asking."
    )
    notes: str = "For any use of write_file that mentions “notes”: Dot does it without asking."
    # (answer, tool, pattern, why, what the row says after "When Dot wants to ")
    rules: list[tuple[str, str, str, str, str]] = [
        # A pattern that fits `git push; curl … | sh` is no “git push” run alone, and one that fits every file is no
        # file that mentions “notes”.
        (
            "allow",
            "shell",
            r"\bgit push\w*",
            alone,
            r"run a command that looks like \bgit push\w*, it goes ahead without asking.",
        ),
        ("allow", "write_file", ".*", notes, "write a file that looks like .*, it goes ahead without asking."),
        # A boundary at one end of a word fits “git pushes”, which whole words do not.
        (
            "preapproved",
            "shell",
            r"\bgit push",
            alone,
            r"run a command that looks like \bgit push, it goes ahead, whatever it has read.",
        ),
        # Whole words say so, whatever the why is about; a space at the edge of a word is one the rule asks for.
        ("ask", "shell", r"\bgit push\b", notes, "run a command that mentions “git push”, it asks you first."),
        ("ask", "shell", " rm ", "", "run a command that mentions “ rm ”, it asks you first."),
    ]
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SIZES:
                await _no_rules(client)
                for behavior, tool, pattern, why, _ in rules:
                    created: httpx.Response = await client.post(
                        "/api/rules",
                        json={"behavior": behavior, "tool_glob": tool, "match_regex": pattern, "description": why},
                    )
                    assert created.status_code == 201, created.text
                page: Page = await _rules_page(browser, host, port, width, scheme, height=844 if width < 720 else 900)
                rows: Locator = page.locator(".rule-list > .rule-row")
                await expect(rows).to_have_count(len(rules))
                for number, (_, _, _, _, said) in enumerate(rules):
                    await expect(rows.nth(number).locator(".rule-sentence")).to_have_text(f"When Dot wants to {said}")
                await expect(page.locator(".rule-list .badge", has_text="Never used")).to_have_count(0)
                # What the sentence shows of a pattern it cannot put in words is the pattern, and the tooltip all of it.
                await expect(rows.nth(0).locator(".rule-sentence code")).to_have_attribute("title", rules[0][2])
                await expect(rows.nth(1).locator(".rule-sentence code")).to_have_attribute("title", rules[1][2])
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.close()
        finally:
            await browser.close()


async def test_the_card_of_the_rules_offers_sentences_while_it_is_empty_and_says_what_is_always_on(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Items 565 and 622, on a wide window and a phone: with no rule the card names three sentences to start from, and
    one puts its words and its choice in the line; the checks that are always on are said under the rules and listed in
    Advanced, which is shut until it is asked for, the sandbox switch among them; the head counts the rules."""
    await _no_rules(client)
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in ((1280, "dark"), (390, "light")):
                page: Page = await _rules_page(browser, host, port, width, scheme)
                await expect(page.get_by_text("No rules yet")).to_be_visible()
                await expect(page.locator(".set-meta")).to_be_hidden()
                words: Locator = page.get_by_role("textbox", name="When Dot wants to", exact=True)
                await expect(words).to_be_visible()
                foot: Locator = page.locator(".rule-foot")
                await expect(foot).to_contain_text(
                    "Dot's built-in safety checks come first, and your rules cannot switch"
                )
                await expect(foot).to_contain_text("YOLO mode, which asks nothing at all, is the one exception.")
                # Advanced is shut; the foot's way to it opens it.
                advanced: Locator = page.locator("details.rule-advanced")
                await expect(advanced).not_to_have_attribute("open", "")
                await expect(page.get_by_role("switch", name="Keep Dot's commands in a safe box")).to_be_hidden()
                await expect(page.get_by_role("heading", name="Always on, whatever your rules say")).to_be_hidden()
                await foot.get_by_role("button", name="See them all", exact=True).click()
                await expect(advanced).to_have_attribute("open", "")
                await expect(page.get_by_role("heading", name="Always on, whatever your rules say")).to_be_visible()
                await expect(page.locator(".rule-always li")).to_have_count(5)
                await expect(page.get_by_role("switch", name="Keep Dot's commands in a safe box")).to_be_visible()
                await advanced.locator("> summary").click()
                await expect(advanced).not_to_have_attribute("open", "")
                # A sentence to start from puts its words and its choice in the line, for the owner to change.
                sentence: Locator = page.get_by_role("button", name="Leave everything on mybank.com to me")
                await sentence.click()
                await expect(words).to_have_value("do anything on mybank.com")
                await expect(page.get_by_role("combobox", name="It should", exact=True)).to_have_value("handoff")
                await expect(words).to_be_focused()
                await expect(page.get_by_role("button", name="Add rule", exact=True)).to_be_enabled()
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"rules-empty-{width}-{scheme}.png"), full_page=True)
                # A rule comes from another page: the empty card gives way to the list, the sentences to start from go,
                # the head counts, and the checks stay said under the rules.
                added: httpx.Response = await client.post(
                    "/api/rules",
                    json={"behavior": "handoff", "tool_glob": "browser_*", "match_regex": "mybank", "description": ""},
                )
                assert added.status_code == 201, added.text
                await expect(page.locator(".rule-list > .rule-row")).to_have_count(1)
                await expect(page.get_by_text("No rules yet")).to_have_count(0)
                await expect(sentence).to_have_count(0)
                await expect(page.locator(".set-meta")).to_have_text("1 rule")
                await expect(foot).to_be_visible()
                # With one rule there is no order to change: no arrows. With two, the way two are told apart is said.
                await expect(page.get_by_role("button", name="Move rule 1 down")).to_have_count(0)
                await expect(foot).not_to_contain_text("If two rules fit the same action")
                second: httpx.Response = await client.post(
                    "/api/rules",
                    json={"behavior": "ask", "tool_glob": "shell", "match_regex": "rm", "description": ""},
                )
                assert second.status_code == 201, second.text
                await expect(page.locator(".rule-list > .rule-row")).to_have_count(2)
                await expect(foot).to_contain_text(
                    "If two rules fit the same action, the one higher up decides. A rule that leaves it to you "
                    "always wins, wherever it stands."
                )
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                for rule in (await client.get("/api/rules")).json():
                    assert (await client.delete(f"/api/rules/{rule['id']}")).status_code == 200
                await expect(page.get_by_text("No rules yet")).to_be_visible()
                await expect(page.locator(".set-meta")).to_be_hidden()
                await page.close()
        finally:
            await browser.close()


async def test_a_rule_is_deleted_after_a_question_and_its_details_stay_open_as_the_list_is_drawn_again(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Delete asks first, in the rule's own sentence, and leaves the rule when it is cancelled; Details of a rule stay
    open when the list is drawn again (a switch turned, a rule moved), and a rule switched off reads as one."""
    await _no_rules(client)
    for behavior, glob, pattern in (("ask", "write_file", ""), ("ask", "shell", r"\bgit push\b")):
        created: httpx.Response = await client.post(
            "/api/rules", json={"behavior": behavior, "tool_glob": glob, "match_regex": pattern, "description": ""}
        )
        assert created.status_code == 201, created.text
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in ((1280, "light"), (390, "dark")):
                page: Page = await _rules_page(browser, host, port, width, scheme)
                rows: Locator = page.locator(".rule-list > .rule-row")
                await expect(rows).to_have_count(2)
                first: Locator = rows.first.get_by_role("button", name="Details", exact=True)
                await first.click()
                await expect(first).to_have_attribute("aria-expanded", "true")
                await expect(rows.first.locator(".rule-facts")).to_be_visible()
                await expect(rows.nth(1).locator(".rule-facts")).to_be_hidden()
                # A switch turned draws the list again, and the open Details stay open.
                await page.get_by_role("switch", name="Switch off rule 2").click()
                await expect(page.get_by_role("switch", name="Switch on rule 2")).to_have_attribute(
                    "aria-checked", "false"
                )
                await expect(rows.nth(1)).to_have_class("rule-row faint")
                await expect(rows.first.locator(".rule-facts")).to_be_visible()
                await rows.nth(1).get_by_role("button", name="Details", exact=True).click()
                await expect(rows.nth(1).locator(".rule-facts")).to_contain_text(
                    "It is switched off, so it decides nothing."
                )
                await page.screenshot(path=str(tmp_path / f"details-{width}-{scheme}.png"), full_page=True)
                await page.get_by_role("switch", name="Switch on rule 2").click()
                await expect(page.get_by_role("switch", name="Switch off rule 2")).to_have_attribute(
                    "aria-checked", "true"
                )
                # Delete asks first, in the rule's own sentence; Cancel leaves it.
                await page.get_by_role("button", name="Delete rule 2", exact=True).click()
                dialog: Locator = page.locator("dialog.modal")
                await expect(dialog).to_contain_text("Delete this rule?")
                await expect(dialog).to_contain_text(
                    "“When Dot wants to run a command that mentions “git push”, it asks you first.” stops applying."
                )
                await dialog.get_by_role("button", name="Cancel", exact=True).click()
                await expect(dialog).to_have_count(0)
                await expect(rows).to_have_count(2)
                await page.get_by_role("button", name="Delete rule 2", exact=True).click()
                await dialog.get_by_role("button", name="Delete", exact=True).click()
                await expect(page.locator(".toast")).to_contain_text("Rule deleted.")
                await expect(rows).to_have_count(1)
                await expect(rows.first.locator(".rule-sentence")).to_have_text(
                    "When Dot wants to write a file, it asks you first."
                )
                assert [rule["tool_glob"] for rule in (await client.get("/api/rules")).json()] == ["write_file"]
                await page.close()
                made: httpx.Response = await client.post(
                    "/api/rules",
                    json={"behavior": "ask", "tool_glob": "shell", "match_regex": r"\bgit push\b", "description": ""},
                )
                assert made.status_code == 201, made.text
        finally:
            await browser.close()


async def test_the_line_that_adds_a_rule_is_two_lines_on_a_phone(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """On a phone, the narrowest included, the box has a line of its own and the choice and the button stand on the
    next, inside the card; on a wide window all three are one line."""
    await _no_rules(client)
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in ((1280, "light"), (390, "light"), (320, "dark")):
                page: Page = await _rules_page(browser, host, port, width, scheme)
                await expect(page.get_by_role("button", name="Add rule", exact=True)).to_be_visible()
                line: dict[str, dict[str, float]] = await page.evaluate(_LINE)
                wants, should, add, card = line["wants"], line["should"], line["add"], line["card"]
                middle_of = lambda box: (box["top"] + box["bottom"]) / 2  # noqa: E731
                if width >= 720:
                    # One line: the box, the choice and the button level, in that order.
                    assert abs(middle_of(wants) - middle_of(should)) <= 4, line
                    assert wants["right"] <= should["left"] <= should["right"] <= add["left"], line
                else:
                    # Two lines: the box above, then the choice and the button level, the button at the right.
                    assert wants["bottom"] <= should["top"], line
                    assert abs(middle_of(should) - middle_of(add)) <= 4, line
                    assert should["right"] <= add["left"], line
                    assert wants["width"] >= card["width"] * 0.8, line
                    assert wants["left"] >= card["left"] and add["right"] <= card["right"], line
                assert add["height"] >= 32, line
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"line-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()


async def test_the_page_uses_no_technical_word_until_the_details_of_a_rule_are_opened(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 622: someone who knows no software reads the page: with no rule, with rules, with a draft read back, with
    a draft that failed or was not what was chosen, and with Advanced opened, no sentence of it says a pattern, a tool,
    a preset, matching or a regular expression. They are under Details, and the page says them there."""
    await _no_rules(client)
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [_draft([], _NO_RULE), _draft(_COMMANDS, "", answer="allow"), _draft(_COMMANDS, "", answer="ask")],
    )
    host, port = _address(client)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await _rules_page(browser, host, port, 1280, "light")
            pane: Locator = page.locator(".settings-pane")
            words: Locator = page.get_by_role("textbox", name="When Dot wants to", exact=True)
            add: Locator = page.get_by_role("button", name="Add rule", exact=True)
            await expect(page.get_by_text("No rules yet")).to_be_visible()
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            # A draft that is no rule, one that is not what was chosen, and one read back with its parts shut.
            await words.fill(_WEATHER)
            await add.click()
            await expect(page.locator(".rule-outcome.bad")).to_contain_text("Dot could not make a rule")
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            await words.fill(_WANTS)
            await add.click()
            await expect(page.locator(".rule-outcome.bad")).to_contain_text("differently from your choice")
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            await add.click()
            await expect(page.locator(".rule-pending .rule-understood")).to_be_visible()
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            await page.get_by_role("button", name="Add it", exact=True).click()
            # Rules of every kind: the drafter's, one of a hand's that is words, one that is more than words, a tool of
            # a connected app, and one that is never used.
            for behavior, glob, pattern in (
                ("handoff", "browser_*", "mybank"),
                ("ask", "shell", r"^rm\s+-rf"),
                ("allow", "mcp__github__create_issue", ""),
                ("allow", "write_file", ""),
                ("ask", "write_file", ""),
            ):
                made: httpx.Response = await client.post(
                    "/api/rules",
                    json={"behavior": behavior, "tool_glob": glob, "match_regex": pattern, "description": "A reason"},
                )
                assert made.status_code == 201, made.text
            await expect(page.locator(".rule-list > .rule-row")).to_have_count(6)
            await expect(page.locator(".rule-list .badge", has_text="Never used")).to_have_count(1)
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            await page.locator("details.rule-advanced > summary").click()
            await expect(page.get_by_role("heading", name="Always on, whatever your rules say")).to_be_visible()
            assert _TECHNICAL.findall(await pane.inner_text()) == []
            # Under Details they are, and the page says them there.
            await page.locator(".rule-row").first.get_by_role("button", name="Details", exact=True).click()
            assert {found.lower() for found in _TECHNICAL.findall(await pane.inner_text())} == {"tool", "pattern"}
            await page.close()
        finally:
            await browser.close()
