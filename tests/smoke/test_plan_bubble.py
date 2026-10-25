"""The plan over the composer (item 644): while a run works and has a plan, the line over the composer has a part that
says how far the plan has come ("Plan 1/3"); a tap opens the plan as a bubble over the dock, with its targets and its
steps as the Plan tab draws them and the way to that tab; it follows the plan while it is open, fits the window of a
phone, and closes on Escape. First over a static page with the page's own ``capsule.js`` and ``planbubble.js``, at a
phone's width and a desktop's; then on a real run, whose scripted model writes a plan with ``update_plan`` and goes on
working until it is stopped, which takes the part and its bubble away."""

import asyncio
import functools
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import STATIC_DIR, create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "plan-bubble-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TARGETS: str = "One app idea nobody has built, worth building"
_STEPS: list[dict[str, str]] = [
    {"text": "Scan the new projects on GitHub, X and LinkedIn", "status": "done"},
    {"text": "Blend the ideas and keep the three strongest", "status": "in_progress"},
    {"text": "Present the chosen idea to the owner", "status": "pending"},
]
_PAGE: str = """<!doctype html><html lang="en" class="chat-shell"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="dot-pages" content="">
<link rel="stylesheet" href="/css/app.css"><link rel="stylesheet" href="/css/shell.css">
<link rel="stylesheet" href="/css/run.css"><link rel="stylesheet" href="/css/themes.css"></head>
<body style="margin:0;padding-top:420px">
<div class="dock" id="dock" style="position:relative;padding:0 8px"></div>
<script type="module">
import { createCapsule } from "/js/capsule.js";
import { el } from "/js/dom.js";
import { planBubble } from "/js/planbubble.js";
const steps = %s;
const targets = %s;
let bubble = null;
const capsule = createCapsule({ onWork() {}, onHelpers() {}, onQueued() {} });
const dock = document.getElementById("dock");
dock.append(el("div", { class: "status-bar" }, capsule.element), el("div", { class: "composer" }, "Queue a message"));
bubble = planBubble({ anchor: capsule.plan, frame: dock, onOpenTab: () => { window.opened = "plan"; } });
const statusAt = (index, done) => (index < done ? "done" : index === done ? "in_progress" : "pending");
window.paint = (done, working) => {
  const now = steps.map((step, index) => ({ ...step, status: statusAt(index, done) }));
  const plan = { done, total: now.length };
  const at = 1800000000;
  capsule.paint({ status: working ? "running" : null, since: at - 97, now: at, plan, helpers: [], queued: 0 });
  bubble.paint(now, targets);
};
window.paint(1, true);
window.ready = true;
</script></body></html>"""


# Where the open bubble's left edge is in the dock, and where it belongs: at the part's left edge, kept 8 px inside the
# dock's edges.
_PLACED: str = """() => {
  const dock = document.getElementById('dock');
  const frame = dock.getBoundingClientRect();
  const part = document.querySelector('.capsule-plan').getBoundingClientRect();
  const bubble = dock.querySelector('.composer-popover:not([hidden])').getBoundingClientRect();
  const room = dock.clientWidth - bubble.width - 8;
  return { left: bubble.left - frame.left, want: Math.max(8, Math.min(part.left - frame.left, room)) };
}"""


class _Quiet(SimpleHTTPRequestHandler):
    """The page's own files, and the page that draws the line and the bubble; nothing is logged."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/":
            super().do_GET()
            return
        body: bytes = (_PAGE % (json.dumps(_STEPS), json.dumps(_TARGETS))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def static_page() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(STATIC_DIR))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


async def _box(locator: Locator) -> dict[str, float]:
    box: Any = await locator.bounding_box()
    assert box is not None, locator
    return box


@pytest.mark.parametrize("width", [390, 1280])
async def test_the_plans_part_opens_the_plan_as_a_bubble_that_follows_it_and_fits_the_window(
    static_page: str, width: int, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": width, "height": 844}, reduced_motion="reduce")
            await page.goto(static_page)
            await page.wait_for_function("window.ready === true")
            part: Locator = page.get_by_role("button", name="Plan, 1 of 3 steps done")
            await expect(part).to_have_text("Plan 1/3")
            await part.click()
            bubble: Locator = page.get_by_role("dialog", name="Plan")
            await expect(bubble).to_be_visible()
            await expect(part).to_have_attribute("aria-expanded", "true")
            await expect(bubble.locator(".plan-popover-count")).to_have_text("1 / 3")
            await expect(bubble.locator(".plan-targets")).to_have_text(_TARGETS)
            await expect(bubble.locator(".plan-step")).to_have_count(3)
            await expect(bubble.locator(".plan-step.done")).to_have_text(_STEPS[0]["text"])
            await expect(bubble.locator(".plan-step.in_progress")).to_have_text(_STEPS[1]["text"])
            # Over the line, inside the window.
            shown: dict[str, float] = await _box(bubble)
            under: dict[str, float] = await _box(part)
            assert shown["y"] + shown["height"] <= under["y"], (shown, under)
            assert shown["x"] >= 0 and shown["x"] + shown["width"] <= width, (shown, width)
            # Its left edge at the part's, unless that takes it past the dock's edge: the part's place is measured in
            # the dock, not in the bar around it, whose backdrop filter makes it the part's offset parent.
            placed: dict[str, float] = await page.evaluate(_PLACED)
            assert abs(placed["left"] - placed["want"]) <= 1, placed
            await page.screenshot(path=str(tmp_path / f"plan-bubble-{width}.png"))
            # It follows the plan while it is open.
            await page.evaluate("paint(2, true)")
            await expect(bubble.locator(".plan-popover-count")).to_have_text("2 / 3")
            await expect(page.get_by_role("button", name="Plan, 2 of 3 steps done")).to_have_text("Plan 2/3")
            # Escape closes it; the way to the Plan tab closes it too. (The part's name now says 2 of 3.)
            part = page.locator(".capsule-plan")
            await page.keyboard.press("Escape")
            await expect(bubble).to_be_hidden()
            await expect(part).to_have_attribute("aria-expanded", "false")
            await part.click()
            await bubble.get_by_role("button", name="Open in the side panel").click()
            await expect(bubble).to_be_hidden()
            assert await page.evaluate("window.opened") == "plan"
            # A run that no longer works has no plan's part over its composer.
            await page.evaluate("paint(3, false)")
            await expect(page.locator(".capsule-plan")).to_be_hidden()
        finally:
            await browser.close()


# ------------------------------------------------------------------------------------------------------- a real run

_WAIT: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 600"}),))


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str, int]]:
    """A daemon whose scripted model writes a plan of two steps and then waits in a command: its run manager, its
    address and its port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    plan: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool="update_plan",
                arguments={
                    "steps": [
                        {"text": "Read the owner's notes", "status": "in_progress"},
                        {"text": "Write the summary", "status": "pending"},
                    ]
                },
            ),
        ),
    )
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [plan, *([_WAIT] * 20)]))
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
            yield manager, f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_a_run_that_writes_a_plan_shows_it_over_its_composer_and_leads_to_its_tab(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await manager.start(
            "Summarise my notes", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                part: Locator = page.get_by_role("button", name="Plan, 0 of 2 steps done")
                await expect(part).to_have_text("Plan 0/2", timeout=20_000)
                await part.click()
                bubble: Locator = page.get_by_role("dialog", name="Plan")
                await expect(bubble.locator(".plan-step")).to_have_count(2)
                await expect(bubble.locator(".plan-step.in_progress")).to_have_text("Read the owner's notes")
                # Over the line, inside the window, as on the static page.
                shown: dict[str, float] = await _box(bubble)
                under: dict[str, float] = await _box(part)
                assert shown["y"] >= 0 and shown["y"] + shown["height"] <= under["y"], (shown, under)
                assert shown["x"] >= 0 and shown["x"] + shown["width"] <= 1280, shown
                await page.screenshot(path=str(tmp_path / "plan-bubble-run.png"))
                await bubble.get_by_role("button", name="Open in the side panel").click()
                await expect(bubble).to_be_hidden()
                await expect(page.locator("#inspector-panel-plan .plan-step")).to_have_count(2)
                # Open again, then the run stops: the part goes, and the bubble with it.
                await page.locator(".capsule-plan").click()
                await expect(bubble).to_be_visible()
                await manager.stop(run.id)
                await expect(page.locator(".capsule-plan")).to_be_hidden(timeout=10_000)
                await expect(bubble).to_be_hidden()
            finally:
                await browser.close()
