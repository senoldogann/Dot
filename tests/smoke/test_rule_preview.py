"""Trying a rule on past steps (item 469), through the daemon's route and the Rules page on a seeded store: the store
finds where a day starts without reading the whole table, the route counts what the rule fits and would decide (a yolo
run's steps, a step the gate did not let through and one from before item 468 apart) and writes nothing, a pattern
that is not one is refused, and the page's Test shows what the rule would have done, each step leading to its run."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.authorization import StepDecision
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "rule-preview-token"
OPUS: str = "anthropic:claude-opus-5-5"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


def _result(tool: str, kind: str, summary: str) -> JsonObject:
    """A step's result as the executor writes it before item 468: no decision and no flags."""
    return {
        "tool": tool,
        "ok": True,
        "text": "done",
        "kind": kind,
        "risk": "medium" if kind != "" else "",
        "summary": summary,
        "target": summary.removeprefix("Run: "),
    }


def _decided(tool: str, kind: str, summary: str, verdict: str, answered_by: str) -> JsonObject:
    """A step's result as the executor writes it since item 468, with what decided it."""
    decision: StepDecision = StepDecision.model_validate(
        {
            "verdict": verdict,
            "source": "preset",
            "reason": "the preset",
            "rule_id": None,
            "answered_by": answered_by,
            "preset": "balanced",
        }
    )
    return {**_result(tool, kind, summary), "decision": decision.model_dump(mode="json"), "flags": []}


async def _run(runtime: Runtime, approvals: ApprovalMode) -> RunRecord:
    thread: ThreadRecord = await runtime.store.create_thread("Ship it", "ui")
    return await runtime.store.create_run(
        thread.id, "Ship it", "task", OPUS, None, "ui", "high", "standard", None, approvals, False, None, None, None
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded(runtime: Runtime) -> list[EventRecord]:
    """The steps of two past runs, oldest first: in one that asked, a push from before item 468, a push the gate did not
    let through, a push the owner approved, a listing, a file written and a call that never reached the policy; in a
    yolo run, a push."""
    asked: RunRecord = await _run(runtime, "ask")
    yolo: RunRecord = await _run(runtime, "yolo")
    stopped: JsonObject = {**_result("shell", "shell", "Run: git push --tags"), "ok": False, "decision": None}
    results: list[tuple[str, JsonObject]] = [
        (asked.id, _result("shell", "shell", "Run: git push origin main")),
        (asked.id, {**stopped, "flags": []}),
        (asked.id, _decided("shell", "shell", "Run: git push", "ask", "user")),
        (asked.id, _decided("shell", "shell", "Run: ls", "allow", "policy")),
        (asked.id, _decided("write_file", "write", "Write notes.md", "allow", "policy")),
        (asked.id, _result("no_such_tool", "", "")),
        (yolo.id, _decided("shell", "shell", "Run: git push", "ask", "yolo")),
    ]
    return [await runtime.store.add_event(run_id, "tool_result", data) for run_id, data in results]


async def test_the_store_finds_where_a_moment_starts_by_halving_the_ids(
    runtime: Runtime, seeded: list[EventRecord]
) -> None:
    assert await runtime.store.first_event_id_since(0.0) <= seeded[0].id
    assert await runtime.store.first_event_id_since(seeded[1].ts) <= seeded[1].id
    last: EventRecord = await runtime.store.add_event(seeded[0].run_id, "note", {"text": "later"})
    assert await runtime.store.first_event_id_since(last.ts + 60) == last.id + 1


async def test_the_route_tries_a_rule_on_past_steps_and_writes_nothing(
    runtime: Runtime, client: httpx.AsyncClient, seeded: list[EventRecord]
) -> None:
    rules_before = await runtime.store.list_rules()
    tried: httpx.Response = await client.post(
        "/api/rules/preview", json={"behavior": "allow", "tool_glob": "shell", "match_regex": "git push", "days": 30}
    )
    assert tried.status_code == 200, tried.text
    view = tried.json()
    # The call that never reached the policy is not a step; the yolo run's push is counted apart.
    assert (view["scanned"], view["fits"], view["in_yolo"], view["would_decide"], view["partial"]) == (
        6,
        4,
        1,
        3,
        False,
    )
    assert (view["looser"], view["stopped"], view["unknown"], view["problem"]) == (1, 1, 1, None)
    newest_first: list[int] = [seeded[6].id, seeded[2].id, seeded[1].id, seeded[0].id]
    assert [example["event_id"] for example in view["examples"]] == newest_first
    assert await runtime.store.list_rules() == rules_before
    not_a_pattern: httpx.Response = await client.post(
        "/api/rules/preview", json={"behavior": "ask", "tool_glob": "shell", "match_regex": "(", "days": 30}
    )
    assert not_a_pattern.status_code == 422, not_a_pattern.text
    assert not_a_pattern.json()["context"]["pattern"] == "("
    no_days: httpx.Response = await client.post(
        "/api/rules/preview", json={"behavior": "ask", "tool_glob": "shell", "match_regex": "", "days": 0}
    )
    assert no_days.status_code == 422, no_days.text


@pytest.mark.browser
async def test_the_rules_page_tests_a_rule_before_it_is_added(
    runtime: Runtime, client: httpx.AsyncClient, seeded: list[EventRecord], tmp_path: Path
) -> None:
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, height, scheme in ((1280, 900, "light"), (390, 844, "dark")):
                phone: bool = width < 720
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    color_scheme=scheme,
                    is_mobile=phone,
                    has_touch=phone,
                    reduced_motion="reduce",
                )
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"http://127.0.0.1:{port}/#/settings/rules")
                await page.get_by_role("button", name="New rule").click()
                await page.get_by_role("textbox", name="Tool").fill("shell")
                await page.get_by_role("textbox", name="Pattern").fill("git push")
                test: Locator = page.get_by_role("button", name="Test on the last 30 days")
                await test.click()
                tried: Locator = page.locator(".rule-tryout")
                await expect(tried).to_contain_text("Would have matched 4 of 6 steps in the last 30 days.")
                await expect(tried).to_contain_text("1 ran in YOLO, which asks nothing whatever the rules say.")
                await expect(tried).to_contain_text(
                    "It would decide 3 of them: 1 would ask less, 1 did not run then, 1 from before Dot kept its"
                    " decisions."
                )
                await expect(tried.locator(".firing-title")).to_have_count(4)
                await expect(tried.locator(".firing-title").first).to_have_text("shell: Run: git push")
                await expect(tried.locator(".firing").nth(2)).to_contain_text("did not run")
                await expect(tried).not_to_contain_text("null")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"tryout-{width}-{scheme}.png"), full_page=True)
                # A change of the rule puts what it would have done away.
                await page.get_by_role("textbox", name="Pattern").fill("git pull")
                await expect(tried).to_be_hidden()
                # Tried again, a step leads to its run.
                await page.get_by_role("textbox", name="Pattern").fill("git push")
                await test.click()
                await tried.locator(".firing-title").first.click()
                await expect(page).to_have_url(re.compile(f"#/runs/{seeded[6].run_id}$"))
                await page.close()
        finally:
            await browser.close()
