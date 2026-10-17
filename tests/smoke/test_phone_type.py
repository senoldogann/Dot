"""Dot on a phone reads at a phone's size and takes a finger (item 329), in WebKit as the owner's iPhone and as a small
one, on a served daemon with chats, agents, schedules and notes.

On a phone, a touch screen of a phone's width, on every page and in the open sidebar:

- no text is under 13 px, and at least nine characters in ten are 15 px or more (the body is 16 px, as iOS's own);
  code keeps the size chosen for it, 13 px, and is held to the first alone;
- nothing that is tapped is under 44 by 44 px, but a link inside a line of text;
- a step's duration shows without a hover, and a tap anywhere on a setting's row turns its switch.

In a Mac's window the seven sizes of the text, the body and the switch are as before. Before, the body was 14 px, most
text 13 or 14 px, the switch 34 by 20 px and most controls under 32 px. The model is a scripted one.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-type-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_PHONES: tuple[str, ...] = ("iPhone 17 Pro Max", "iPhone SE")
_SETTINGS: tuple[str, ...] = (
    "general",
    "appearance",
    "models",
    "keys",
    "browser",
    "rules",
    "memory",
    "schedules",
    "phone",
    "system",
)
# A phone's text: nothing under the least size, and at least the share of the characters at the reading size or more.
_LEAST_PX: float = 13.0
_READ_PX: float = 15.0
_READ_SHARE: float = 0.9
# The least side of what a finger taps, Apple's 44 pt.
_FINGER_PX: float = 44.0
# A page is read once two looks this far apart find the same, and given this long to settle.
_SETTLE_MS: int = 400
_SETTLE_S: float = 10.0
# A Mac's sizes, as before item 329: the seven sizes of the text, the body, and the switch's width and height.
_MAC_SIZES: list[str] = ["12px", "13px", "14px", "15px", "17px", "20px", "24px"]
_MAC_BODY: str = "14px"
_MAC_SWITCH: list[int] = [34, 20]
# On a phone the switch's box is a finger's height around a 40 by 24 px track (item 337; iOS's 52 by 32 px before).
_PHONE_SWITCH: list[int] = [48, 44]
_PHONE_TRACK: list[int] = [40, 24]
_CLEAR: str = "rgba(0, 0, 0, 0)"
# A switch's track (its ::before) by its size, whether its corners are half its height (a pill), and the box's ground.
_TRACK: str = """(s) => {
  const track = getComputedStyle(s, "::before");
  const height = parseFloat(track.height);
  const radius = parseFloat(track.borderTopLeftRadius);
  return {
    track: [Math.round(parseFloat(track.width)), Math.round(height)],
    round: Math.min(radius, height / 2) === height / 2,
    box: getComputedStyle(s).backgroundColor,
  };
}"""
_DURATION: re.Pattern[str] = re.compile(r"\d")
_ANSWER: str = (
    "## Lisbon, two nights under 400 EUR\n\n"
    "Here is a plan that keeps to the budget; the fares are on [TAP's site](https://www.flytap.com/).\n\n"
    "- **Flights:** TAP, Friday 07:15, back Sunday 21:40, 186 EUR.\n"
    "- **Stay:** Casa do Bairro in Alfama, 2 nights, 142 EUR.\n\n"
    "```\nTotal: 186 + 142 + 14 + 40 (food) = 382 EUR\n```\n"
)

# What a phone shows of the page: the characters of every size, the text under the least size, and what is tapped
# under a finger's size. Code keeps the size chosen for it (Settings › Appearance, 13 px), so it is held to the least
# size alone. A link inside a line of text is no target of its own (WCAG 2.5.8), and a control of one pixel or less is
# drawn for a screen reader alone.
_MEASURE: str = """([least, finger, scope]) => {
  const within = document.querySelector(scope);
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.right <= 0 || r.left >= window.innerWidth) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity) > 0.05;
  };
  const name = (el) => `${el.tagName.toLowerCase()}.${String(el.className || "").trim().split(/\\s+/).join(".")}`;
  const sizes = {};
  const small = [];
  const walker = document.createTreeWalker(within, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = walker.currentNode.textContent.trim();
    const el = walker.currentNode.parentElement;
    if (!text || !el || !shown(el)) continue;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (el.closest("pre, code, kbd, samp") === null) sizes[size] = (sizes[size] || 0) + text.length;
    if (size < least) small.push(`${size}px ${name(el)}: ${text.slice(0, 30)}`);
  }
  const targets = [];
  const tapped = "button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=switch],"
    + " [role=tab], [role=option], [role=menuitem]";
  for (const el of within.querySelectorAll(tapped)) {
    if (!shown(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    if (el.tagName === "A" && getComputedStyle(el).display === "inline") continue;
    if (Math.min(r.width, r.height) < finger) {
      const label = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24);
      targets.push(`${Math.round(r.width)}x${Math.round(r.height)} ${name(el)} "${label}"`);
    }
  }
  return { sizes, small, targets };
}"""

# The seven sizes as the page resolves them, the body's size and the switch's box.
_SIZES: str = """() => {
  const probe = document.createElement("span");
  document.body.append(probe);
  const sizes = ["xs", "sm", "ui", "md", "lg", "xl", "2xl"].map((tier) => {
    probe.style.fontSize = `var(--text-${tier})`;
    return getComputedStyle(probe).fontSize;
  });
  probe.remove();
  const toggle = document.querySelector(".switch").getBoundingClientRect();
  return { sizes, body: getComputedStyle(document.body).fontSize, toggle: [toggle.width, toggle.height].map(Math.round) };
}"""


@dataclass(frozen=True)
class Seen:
    """What a page shows on a phone: the characters of each size in px, and what is too small."""

    sizes: dict[str, int]
    small: list[str]
    targets: list[str]


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    yield home


def _turns() -> list[ScriptedTurn]:
    """A task that runs one command and answers with a link in a line, then a chat's reply."""
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo 'TAP 186 EUR'"}),)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _ANSWER, "success": True}),)),
        ScriptedTurn(
            text="Helsinki. It has been Finland's capital since 1812, when it took over from Turku.", calls=()
        ),
    ]


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose model is the scripted one: its run manager and the address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns()))
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
        await close_runtime(runtime)


async def _seed(manager: RunManager, address: str) -> tuple[RunRecord, RunRecord, str]:
    """A finished task and a chat, three agents, two schedules and notes: the task, the chat and an agent's id."""
    task: RunRecord = await manager.start(
        "Plan a weekend in Lisbon under 400 EUR", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None,
        "yolo", False, (),
    )  # fmt: skip
    await manager.wait(task.id)
    chat: RunRecord = await manager.start(
        "What is the capital of Finland?", "chat", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "ask",
        False, (),
    )  # fmt: skip
    await manager.wait(chat.id)
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        templates: object = (await client.get("/api/agent-templates")).json()
        assert isinstance(templates, list)
        agents: list[str] = []
        for template in templates[:3]:
            assert isinstance(template, dict)
            made: httpx.Response = await client.post(f"/api/agent-templates/{template['id']}/use")
            assert made.status_code == 201, made.text
            agents.append(str(made.json()["id"]))
        for name, goal, cron in (
            ("Morning brief", "Summarise my unread mail and today's calendar", "0 8 * * 1-5"),
            ("Weekly report", "Write the weekly report from the tasks done this week", "0 17 * * 5"),
        ):
            made = await client.post("/api/schedules", json={"name": name, "goal": goal, "cron": cron})
            assert made.status_code == 201, made.text
        for content, tags in (
            ("Prefers window seats and morning flights.", ["travel"]),
            ("The team stand-up is at 09:30 on weekdays.", ["work"]),
            ("Writes reports in British English.", ["writing"]),
        ):
            made = await client.post("/api/memories", json={"content": content, "tags": tags})
            assert made.status_code == 201, made.text
    return task, chat, agents[0]


async def _phone(browser: Browser, playwright: Playwright, device: str) -> BrowserContext:
    context: BrowserContext = await browser.new_context(
        **playwright.devices[device], color_scheme="light", reduced_motion="reduce"
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    return context


async def _seen(page: Page, scope: str) -> Seen:
    """What the page shows within ``scope`` (the open sidebar covers the page) once two looks find the same."""
    async with asyncio.timeout(_SETTLE_S):
        last: object = None
        while True:
            found: object = await page.evaluate(_MEASURE, [_LEAST_PX, _FINGER_PX, scope])
            if found == last:
                break
            last = found
            await page.wait_for_timeout(_SETTLE_MS)
    assert isinstance(found, dict)
    sizes: object = found["sizes"]
    small: object = found["small"]
    targets: object = found["targets"]
    assert isinstance(sizes, dict) and isinstance(small, list) and isinstance(targets, list)
    return Seen(
        sizes={str(size): int(count) for size, count in sizes.items()},
        small=[str(line) for line in small],
        targets=[str(line) for line in targets],
    )


def _faults(label: str, seen: Seen) -> list[str]:
    """What breaks a phone's text and touch on the page named ``label``."""
    total: int = sum(seen.sizes.values())
    read: int = sum(count for size, count in seen.sizes.items() if float(size) >= _READ_PX)
    faults: list[str] = [f"{label}: under {_LEAST_PX} px: {line}" for line in seen.small]
    faults += [f"{label}: under {_FINGER_PX} px: {line}" for line in seen.targets]
    if total > 0 and read / total < _READ_SHARE:
        faults.append(f"{label}: {read} of {total} characters at {_READ_PX} px or more ({seen.sizes})")
    return faults


async def test_every_page_on_a_phone_reads_at_a_phones_size_and_takes_a_finger(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        task, chat, agent = await _seed(manager, address)
        pages: list[tuple[str, str]] = [
            ("home", "#/runs"),
            ("task", f"#/runs/{task.id}"),
            ("chat", f"#/runs/{chat.id}"),
            ("activity", "#/activity"),
            ("agents", "#/agents"),
            ("agent", f"#/agents/{agent}"),
            ("team", "#/team"),
            ("campaigns", "#/campaigns"),
            ("plugins", "#/customize/plugins"),
            ("plugin", "#/customize/plugins/notion"),
            ("skills", "#/customize/skills"),
            ("settings", "#/settings"),
            *[(f"settings {tab}", f"#/settings/{tab}") for tab in _SETTINGS],
        ]
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            faults: list[str] = []
            for device in _PHONES:
                context: BrowserContext = await _phone(browser, playwright, device)
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                for label, route in pages:
                    await page.goto(f"{address}/{route}")
                    await expect(page.locator("main.page")).to_be_visible()
                    faults += _faults(f"{device} {label}", await _seen(page, "body"))

                # The list of chats, as the bar's button opens it.
                await page.goto(f"{address}/#/runs/{task.id}")
                await page.get_by_role("button", name="Show sidebar").click()
                await expect(page.locator(".sidebar")).to_be_in_viewport()
                faults += _faults(f"{device} sidebar", await _seen(page, ".sidebar"))
                await context.close()
            assert faults == [], "\n".join(faults)
        finally:
            await browser.close()


async def test_a_steps_duration_shows_and_a_settings_row_turns_its_switch_on_a_phone(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        task, _chat, _agent = await _seed(manager, address)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await _phone(browser, playwright, _PHONES[0])
            page: Page = await context.new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))

            # A step's duration, once its turn's steps are unfolded, with no hover.
            await page.goto(f"{address}/#/runs/{task.id}")
            await page.locator("summary.steps-head").first.tap()
            took: Locator = page.locator(".tool-time").first
            await expect(took).to_be_visible()
            await expect(took).to_have_text(_DURATION)
            await expect(took).to_have_css("opacity", "1")

            # A tap on the row's words turns the switch, and a tap on the switch turns it back.
            await page.goto(f"{address}/#/settings/appearance")
            toggle: Locator = page.get_by_role("switch", name="Fold tool steps")
            was: str | None = await toggle.get_attribute("aria-checked")
            await page.locator(".setting .row", has=toggle).get_by_text("Fold tool steps", exact=True).tap()
            await expect(toggle).not_to_have_attribute("aria-checked", str(was))
            await toggle.tap()
            await expect(toggle).to_have_attribute("aria-checked", str(was))
            assert await toggle.evaluate("(s) => [s.offsetWidth, s.offsetHeight]") == _PHONE_SWITCH
            # The track is drawn by ::before, a pill in either state, and the box around it stays clear (review B2:
            # on, the box's own ground filled all of it).
            for _ in range(2):
                drawn: object = await toggle.evaluate(_TRACK)
                assert drawn == {"track": _PHONE_TRACK, "round": True, "box": _CLEAR}, drawn
                await toggle.tap()
            await context.close()
        finally:
            await browser.close()


async def test_a_macs_window_keeps_its_sizes(own_home: Path) -> None:
    async with _served(own_home) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/settings/appearance")
            await expect(page.get_by_role("switch", name="Fold tool steps")).to_be_visible()
            assert await page.evaluate(_SIZES) == {"sizes": _MAC_SIZES, "body": _MAC_BODY, "toggle": _MAC_SWITCH}
        finally:
            await browser.close()
