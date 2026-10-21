"""A rule in four answers, end to end (item 48): a *hand it to me* rule on ``write_file`` stops the write, the run asks
the user to do the step, and goes on when the user says they did it. The Rules tab shows the rule as a sentence with
the four answers in a segmented control, in a headless Chrome at a wide and at a phone width, in light and in dark. A
step kept for the owner when nobody answered (item 482) shows as kept, and I did it carries the work on."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

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
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "rule-handoff-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0
_WHY: str = "You write the notes yourself"


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


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


async def _pending(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def test_a_hand_it_to_me_rule_stops_the_write_asks_and_the_run_goes_on_when_the_user_did_it(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    added: httpx.Response = await client.post(
        "/api/rules", json={"behavior": "handoff", "tool_glob": "write_file", "match_regex": "", "description": _WHY}
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
    assert (asked.severity, asked.tool) == ("handoff", "write_file")
    assert _WHY in asked.summary
    assert not await asyncio.to_thread(notes.exists), "Dot wrote the file it had to hand over"

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    schemes: tuple[tuple[int, Literal["light", "dark"]], ...] = (
        (1280, "light"),
        (1280, "dark"),
        (390, "light"),
        (390, "dark"),
    )
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for index, (width, scheme) in enumerate(schemes):
                    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"http://{host}:{port}/#/settings/rules")
                    await expect(page.locator(".segmented.answers button")).to_have_count(4)
                    await expect(page.locator(".rule-sentence-preview")).to_contain_text("When Dot would use")
                    rows = page.locator(".list-row .rule-sentence")
                    await expect(rows).to_have_count(1)
                    await expect(rows.first).to_contain_text("When Dot would use")
                    await expect(rows.first.locator(".badge")).to_have_text("Hand it to me")
                    await page.screenshot(path=str(tmp_path / f"rules-{width}-{scheme}.png"))
                    await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                    await expect(page.get_by_text("Dot needs you to take over this step")).to_be_visible()
                    did_it = page.get_by_role("button", name="I did it")
                    await expect(did_it).to_be_visible()
                    await expect(page.get_by_role("button", name="Take the browser over")).to_have_count(0)
                    await page.screenshot(path=str(tmp_path / f"handoff-{width}-{scheme}.png"))
                    if index == len(schemes) - 1:
                        await did_it.click()
                    await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, True, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    assert not await asyncio.to_thread(notes.exists), "Dot wrote the file after the user said they did"
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 2)
    assert any("The user handled this step themselves" in str(event.data.get("text", "")) for event in results), results


async def test_a_hand_off_of_a_browser_step_offers_the_takeover(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """The card of a browser hand-off carries "Take the browser over"; here the browser is headless, so the offer is
    refused with the reason, and the step is handed over without it. The policy stops ``browser_open`` before any
    Chrome starts."""
    added: httpx.Response = await client.post(
        "/api/rules", json={"behavior": "handoff", "tool_glob": "browser_*", "match_regex": "", "description": ""}
    )
    assert added.status_code == 201, added.text
    opening: ScriptedTurn = _turn("browser_open", {"url": "https://example.com/"})
    MODEL.script = ScriptedProvider(_PROVIDER, [opening, _turn(FINISH_TOOL, {"answer": "Opened.", "success": True})])
    run: RunRecord = await manager.start(
        "Open example.com", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    asked: ApprovalRecord = await _pending(runtime, run.id)
    assert (asked.severity, asked.tool) == ("handoff", "browser_open")
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                offer = page.get_by_role("button", name="Take the browser over")
                await expect(offer).to_be_visible()
                await page.screenshot(path=str(tmp_path / "browser-handoff.png"))
                await offer.click()
                await expect(page.locator(".toast.error")).to_contain_text("headless")
                await expect(offer).to_be_enabled()
                await page.get_by_role("button", name="I did it").click()
                await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(asked.id)).status == "pending":
            await manager.decide(asked.id, True, "test", "")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended


async def test_an_earlier_rule_that_allows_the_write_does_not_keep_the_hand_off_from_deciding(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 481: with a rule that allows writes first and one that hands them to the owner after it, an "ask" run's
    write is handed over and not done: a rule that hands off decides wherever it stands."""
    for record in await runtime.store.list_rules():
        await runtime.store.delete_rule(record.id)
    for behavior, description in (("allow", "Notes are fine"), ("handoff", _WHY)):
        added: httpx.Response = await client.post(
            "/api/rules",
            json={"behavior": behavior, "tool_glob": "write_file", "match_regex": "", "description": description},
        )
        assert added.status_code == 201, added.text
    assert [record.behavior for record in await runtime.store.list_rules()] == ["allow", "handoff"]
    notes: Path = tmp_path / "list.txt"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn("write_file", {"path": str(notes), "content": "milk, bread"}),
            _turn(FINISH_TOOL, {"answer": "The owner writes the list.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Write the shopping list", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    try:
        asked: ApprovalRecord = await _pending(runtime, run.id)
        assert (asked.severity, asked.tool) == ("handoff", "write_file") and _WHY in asked.summary
        assert not await asyncio.to_thread(notes.exists), "Dot wrote the file the hand-off was for"
        await manager.decide(asked.id, True, "test", "")
    finally:
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        for record in await runtime.store.list_rules():
            await runtime.store.delete_rule(record.id)
    assert ended.status == "done", ended
    assert not await asyncio.to_thread(notes.exists)


async def test_a_step_kept_for_the_owner_shows_as_kept_and_i_did_it_carries_the_work_on(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 482: a hand-off that nobody answered in time and the run kept for the owner is listed on the run's page as
    "Kept for you", with what I did it does and until when it waits, and among what waits on the Activity list; I did
    it there starts the run that goes on from it in the same chat, with Dot's words under the goal. A question kept for
    the owner's yes (item 483) offers Allow once and Skip, and Skip starts nothing."""
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(FINISH_TOOL, {"answer": "The invoice waits for you.", "success": True}),
            _turn(FINISH_TOOL, {"answer": "The invoices are filed.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "File the invoices", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run.id)
    target: str = str(tmp_path / "invoice-1042.txt")
    kept: ApprovalRecord = await runtime.store.create_kept_approval(
        run.id,
        "write_file",
        f"Write {target} — {_WHY}",
        "handoff",
        {"tool": "write_file", "kind": "write", "risk": "medium", "target": target, "amount": None, "flags": []},
        "digest-1042",
        time.time() + 72 * 3600,
    )
    report: ApprovalRecord = await runtime.store.create_kept_approval(
        run.id,
        "shell",
        "Run: ./report.sh (writes a file) — a rule of yours asks",
        "medium",
        {"tool": "shell", "kind": "shell", "risk": "medium", "target": "./report.sh", "amount": None, "flags": []},
        "digest-report",
        time.time() + 72 * 3600,
    )
    activity: httpx.Response = await client.get("/api/activity")
    assert activity.status_code == 200, activity.text
    rows: list[dict[str, object]] = [row for row in activity.json()["waiting"] if row["id"] == kept.id]
    assert [(row["kind"], row["detail"]) for row in rows] == [
        ("approval", "kept for you; once you did it, saying so carries the work on")
    ]
    asked: list[dict[str, object]] = [row for row in activity.json()["waiting"] if row["id"] == report.id]
    assert [row["detail"] for row in asked] == ["kept for you; allowing it once carries the work on"]
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    schemes: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(schemes):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
                card = page.locator(".approval.handoff", has_text="Kept for you")
                await expect(card).to_be_visible()
                await expect(card).to_contain_text("Nobody answered in time, so the run went on without this step.")
                await expect(card).to_contain_text("It waits until")
                await expect(card.get_by_role("button", name="Skip this step")).to_be_visible()
                if index == 0:
                    # The sidebar lists each step that waits, two of one run included.
                    waiting = page.locator("[data-section='waiting'] .side-activity-title")
                    await expect(waiting).to_have_count(2)
                    await expect(waiting.first).to_contain_text("Write ")
                    await expect(waiting.last).to_contain_text("Run: ./report.sh")
                question = page.locator(".approval:not(.handoff)", has_text="Kept for you")
                await expect(question).to_contain_text("Allow once carries the work on in this conversation")
                await expect(question.get_by_role("button", name="Allow once")).to_be_visible()
                await expect(question.get_by_role("button", name="Skip", exact=True)).to_be_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await card.scroll_into_view_if_needed()
                await page.mouse.move(0, 0)
                await page.screenshot(path=str(tmp_path / f"kept-{width}-{scheme}.png"), animations="disabled")
                if index == len(schemes) - 1:
                    await question.get_by_role("button", name="Skip", exact=True).click()
                    await expect(question).to_have_count(0)
                    await card.get_by_role("button", name="I did it").click()
                    await expect(card).to_have_count(0)
                await page.close()
        finally:
            await browser.close()
    assert (await runtime.store.get_approval(kept.id)).status == "approved"
    assert (await runtime.store.get_approval(report.id)).status == "denied"
    # Skip started nothing; I did it started the one run after it.
    assert len(await runtime.store.list_runs(5, run.thread_id)) == 2
    follow: RunRecord = (await runtime.store.list_runs(1, run.thread_id))[0]
    assert follow.id != run.id
    # The goal is the owner's (the run's, with no note); Dot's words are what the new run reads under it.
    assert follow.goal == "File the invoices"
    told: str = (
        f"File the invoices\n\nThe user did the step that waited for them ({kept.id}) themselves; go on from there."
    )
    assert told in [message.content for message in await runtime.store.load_messages(run.thread_id, 0)]
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(follow.id)).status == "done"
