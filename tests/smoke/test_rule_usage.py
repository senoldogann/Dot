"""How each rule was used on past steps (item 473), through the daemon's route and the Rules page on a seeded store:
every enabled rule says how many steps it fits and decides with today's rules, a step counting for the first rule that
fits it unless its run was in yolo; a rule switched off says nothing; the days asked are checked."""

import asyncio
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
from dotagent.store.models import RuleRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "rule-usage-token"
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


def _decided(summary: str, verdict: str, source: str, answered_by: str) -> JsonObject:
    """A shell step's result as the executor writes it, with what decided it (item 468)."""
    decision: StepDecision = StepDecision.model_validate(
        {
            "verdict": verdict,
            "source": source,
            "reason": "what decided it",
            "rule_id": None,
            "answered_by": answered_by,
            "preset": "balanced",
            "tainted": False,
        }
    )
    return {
        "tool": "shell",
        "ok": True,
        "text": "done",
        "kind": "shell",
        "risk": "medium",
        "summary": summary,
        "target": summary.removeprefix("Run: "),
        "decision": decision.model_dump(mode="json"),
        "flags": [],
    }


async def _run(runtime: Runtime, approvals: ApprovalMode) -> RunRecord:
    thread: ThreadRecord = await runtime.store.create_thread("Ship it", "ui")
    return await runtime.store.create_run(
        thread.id, "Ship it", "task", OPUS, None, "ui", "high", "standard", None, approvals, False, None, None, None
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded(runtime: Runtime) -> list[RuleRecord]:
    """Three rules, the last switched off, and the steps of two runs: a status and a push in one that asked, a push in a
    yolo run."""
    asked: RunRecord = await _run(runtime, "ask")
    yolo: RunRecord = await _run(runtime, "yolo")
    await runtime.store.add_event(asked.id, "tool_result", _decided("Run: git status", "allow", "rule", "policy"))
    await runtime.store.add_event(asked.id, "tool_result", _decided("Run: git push", "ask", "rule", "user"))
    await runtime.store.add_event(yolo.id, "tool_result", _decided("Run: git push", "ask", "rule", "yolo"))
    rules: list[RuleRecord] = [
        await runtime.store.add_rule("allow", "shell", r"\bgit status\b", "Status is fine"),
        await runtime.store.add_rule("ask", "shell", r"\bgit\b", "Ask about git"),
        await runtime.store.add_rule("handoff", "shell", r"push", "Pushes are mine"),
    ]
    await runtime.store.set_rule_enabled(rules[2].id, False)
    return rules


async def test_the_route_says_how_each_enabled_rule_was_used(
    runtime: Runtime, client: httpx.AsyncClient, seeded: list[RuleRecord]
) -> None:
    rules_before: list[RuleRecord] = await runtime.store.list_rules()
    answer: httpx.Response = await client.get("/api/rules/usage", params={"days": 30})
    assert answer.status_code == 200, answer.text
    view = answer.json()
    assert (view["scanned"], view["partial"], view["problem"]) == (3, False, None)
    used = {use["rule_id"]: use for use in view["rules"]}
    assert set(used) == {seeded[0].id, seeded[1].id}
    status, git = used[seeded[0].id], used[seeded[1].id]
    assert (status["fits"], status["decides"], status["in_yolo"]) == (1, 1, 0)
    assert (git["fits"], git["decides"], git["in_yolo"], git["before_rules"]) == (3, 1, 1, 0)
    assert [(shadow["rule_id"], shadow["steps"]) for shadow in git["shadowed_by"]] == [(seeded[0].id, 1)]
    assert await runtime.store.list_rules() == rules_before
    for params in ({"days": 0}, {"days": 91}, {}):
        refused: httpx.Response = await client.get("/api/rules/usage", params=params)
        assert refused.status_code == 422, (params, refused.text)


@pytest.mark.browser
async def test_each_rule_row_says_how_it_was_used(
    runtime: Runtime, client: httpx.AsyncClient, seeded: list[RuleRecord], tmp_path: Path
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
                lines: Locator = page.locator(".rule-usage")
                await expect(lines).to_have_count(2)
                await expect(lines.nth(0)).to_have_text("Fits 1 step of the last 30 days: decides 1.")
                await expect(lines.nth(1)).to_have_text(
                    "Fits 3 steps of the last 30 days: decides 1; 1 was in YOLO runs, which ask nothing; rule #1"
                    " decides 1 first."
                )
                await expect(page.locator(".rule-list")).not_to_contain_text("null")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / f"usage-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()
