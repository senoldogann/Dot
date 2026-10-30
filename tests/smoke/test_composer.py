"""The composer (items 64, 104 and 115): typing ``/`` on the new chat page opens the menu of commands and skills,
letters filter it, the arrows move, Enter picks without sending (a skill as a chip, item 318), Escape closes;
Shift+Enter breaks a line. The bar's pills open the Model and the Options menus, and the "+" goal mode; a draft
survives a navigation; a photo in the box shows the picture itself, its name its tooltip, in one row beside the other
files; the bar keeps one line from the desk to the phone. Wide and on a phone."""

import asyncio
import re
import time
from base64 import b64decode
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from composer_choices import show_choices
from playwright.async_api import Browser, ConsoleMessage, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "composer-test-token"
# A model that offers reasoning levels, added in the page to the daemon's list (the scripted model offers none), so the
# Reasoning pill has levels to show.
_THINKER: dict[str, object] = {
    "ref": "ollama:thinker",
    "vision": False,
    "tools": True,
    "thinking": True,
    "context_length": 32768,
    "efforts": ["low", "medium", "high"],
    "default_effort": "medium",
}


def _collect_refusals(refused: list[str]) -> Callable[[ConsoleMessage], None]:
    """A console listener that keeps what the page's Content-Security-Policy refused."""

    def listen(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text:
            refused.append(message.text)

    return listen


async def _with_a_thinking_model(route: Route) -> None:
    response = await route.fetch()
    providers: list[dict[str, list[dict[str, object]]]] = await response.json()
    for provider in providers:
        if provider["provider"] == "ollama":
            provider["models"].append(_THINKER)
    await route.fulfill(response=response, json=providers)


SKILL: str = """---
name: scan-catalogue
description: "Scan a product catalogue page by page and report the items."
---
# Scan the catalogue

Read every page.
"""
GOAL: str = "textarea[aria-label='Goal']"

# Two 4x4 PNGs, enough for the browser to read a photo and lay it out at a thumbnail's size.
_SUNSET_PNG: str = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAIElEQVR4nGP8MSnoxYsXEhISL168YIKzJCQkmOAsdBkAwLAZnDmVq0MA"
    "AAAASUVORK5CYII="
)
_HARBOUR_PNG: str = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAIElEQVR4nGMMmvTjxYsXEhISL168YIKzJCQkmOAsdBkAv2QZnKBSoFwA"
    "AAAASUVORK5CYII="
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    (home / "skills" / "scan-catalogue").mkdir(parents=True)
    (home / "skills" / "scan-catalogue" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def test_the_palette_names_the_skills_and_the_servers(client: httpx.AsyncClient) -> None:
    answer: httpx.Response = await client.get("/api/palette")
    assert answer.status_code == 200, answer.text
    found: dict[str, list[dict[str, str]]] = answer.json()
    # By name, the owner's skill among the three that come with Dot (item 196).
    assert [(skill["name"], skill["source"]) for skill in found["skills"]] == [
        ("excel", "built-in"),
        ("onedrive", "built-in"),
        ("scan-catalogue", "home"),
        ("word", "built-in"),
    ]
    assert found["skills"][2]["description"] == "Scan a product catalogue page by page and report the items."
    assert found["servers"] == []


async def test_a_slash_opens_the_menu_letters_filter_it_and_enter_picks_without_sending(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await show_choices(page)
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                menu = page.locator(".composer .slash-menu")
                await expect(menu).to_be_hidden()
                await goal.click()
                await goal.type("/")
                await expect(menu).to_be_visible()
                # Five commands (Auto and the three modes, item 495, and /loop, item 400), the owner's skill and the
                # three that come with Dot (item 196).
                await expect(menu.locator("[role=option]")).to_have_count(9)
                # The sections have no titles on the page (item 537); a screen reader hears each.
                labels: list[str | None] = [
                    await section.get_attribute("aria-label") for section in await menu.locator("[role=group]").all()
                ]
                assert labels == ["Commands", "Skills"], labels
                await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("/auto")
                assert await goal.get_attribute("aria-expanded") == "true"
                await page.screenshot(path=str(tmp_path / f"slash-open-{width}.png"))
                # Letters filter; the first match is active; the arrow keys move; Enter picks instead of sending.
                await goal.type("sc")
                await expect(menu.locator("[role=option]")).to_have_count(1)
                await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("scan-catalogue")
                await goal.press("Enter")
                await expect(menu).to_be_hidden()
                # A skill is picked for the message as a chip (item 318): the slash word goes and nothing is typed.
                await expect(goal).to_have_value("")
                chip = page.locator(".composer .pick-chip[data-kind='skill']")
                await expect(chip).to_have_attribute("data-name", "scan-catalogue")
                await chip.get_by_role("button", name="Remove scan-catalogue").click()
                await expect(chip).to_have_count(0)
                await expect(page).to_have_url(f"http://{host}:{port}/#/runs")
                # A slash inside a word opens nothing; one after a space does, and Escape closes it.
                await goal.type("a/b")
                await expect(menu).to_be_hidden()
                await goal.type(" /")
                await expect(menu).to_be_visible()
                await goal.press("Escape")
                await expect(menu).to_be_hidden()
                # A command acts on the composer: /research picks the research mode and leaves no text behind.
                await goal.fill("")
                await goal.type("/res")
                await expect(menu.locator("[role=option]")).to_have_count(1)
                await goal.press("Tab")
                await expect(goal).to_have_value("")
                await expect(page.locator(".composer-pill[data-pill='mode']")).to_contain_text("Research")
                # Shift+Enter breaks a line and sends nothing.
                await goal.type("first")
                await goal.press("Shift+Enter")
                await goal.type("second")
                await expect(goal).to_have_value("first\nsecond")
                await expect(page).to_have_url(f"http://{host}:{port}/#/runs")
                await page.close()
        finally:
            await browser.close()


async def test_the_pills_open_their_menus_and_a_draft_survives_a_navigation(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await show_choices(page)
                refused: list[str] = []
                page.on("console", _collect_refusals(refused))
                await page.route("**/api/models", _with_a_thinking_model)
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                popover = page.locator(".composer .composer-popover[aria-label='Model']")
                await expect(popover).to_be_hidden()
                # The Model pill (item 108) wears the mark of its provider, an SVG of the page's own, and the model's
                # name.
                model_pill = page.locator(".composer-pill[data-pill='model']")
                await expect(model_pill).to_contain_text("scripted")
                mark = model_pill.locator(".provider-mark")
                await expect(mark).to_have_attribute("data-provider", "ollama")
                masked: str = await mark.evaluate("(node) => getComputedStyle(node).maskImage")
                assert "icons/providers/ollama.svg" in masked, masked
                served: httpx.Response = await client.get("/icons/providers/ollama.svg")
                assert served.status_code == 200, served.status_code
                assert served.headers["content-type"].startswith("image/svg+xml"), served.headers["content-type"]
                # The scripted model offers no reasoning levels, so the Reasoning pill is away.
                reasoning_pill = page.locator(".composer-pill[data-pill='reasoning']")
                await expect(reasoning_pill).to_be_hidden()
                # The menu: the daemon's default ticked, every model with its mark; a pick closes it and names the
                # model.
                await model_pill.click()
                await expect(popover).to_be_visible()
                await expect(popover.locator("[role=option][aria-selected=true]")).to_contain_text("Default (scripted)")
                await expect(popover.locator("[role=option] .provider-mark").first).to_have_attribute(
                    "data-provider", "ollama"
                )
                await page.screenshot(path=str(tmp_path / f"model-menu-{width}.png"), animations="disabled")
                await popover.get_by_role("option", name="thinker").click()
                await expect(popover).to_be_hidden()
                await expect(model_pill).to_contain_text("thinker")
                # A model with levels brings the Reasoning pill: the daemon's level until one is picked, then the one
                # picked.
                await expect(reasoning_pill).to_contain_text("Medium")
                await expect(reasoning_pill.locator("[data-icon='brain']")).to_have_count(1)
                await reasoning_pill.click()
                levels = page.locator(".composer .composer-popover[aria-label='Reasoning']")
                await expect(levels.locator("[role=option]")).to_have_count(4)
                await expect(levels.locator("[role=option][aria-selected=true]")).to_contain_text("Default (Medium)")
                await page.screenshot(path=str(tmp_path / f"reasoning-menu-{width}.png"), animations="disabled")
                await levels.get_by_role("option", name="High", exact=True).click()
                await expect(levels).to_be_hidden()
                await expect(reasoning_pill).to_contain_text("High")
                # Back to the daemon's default: the Reasoning pill goes again.
                await model_pill.click()
                await popover.get_by_role("option", name="Default (scripted)").click()
                await expect(reasoning_pill).to_be_hidden()
                assert await model_pill.get_attribute("aria-expanded") == "false"
                # The Mode pill is away on Auto (item 495), where the message's words settle the mode; a mode named
                # with its command brings it, with the mode's icon and name, its menu Auto and the three modes with
                # what each does.
                mode_pill = page.locator(".composer-pill[data-pill='mode']")
                await expect(mode_pill).to_be_hidden()
                await goal.type("/task")
                await goal.press("Tab")
                await expect(mode_pill).to_contain_text("Task")
                await mode_pill.click()
                modes = page.locator(".composer .composer-popover[aria-label='Mode']")
                await expect(modes.locator("[role=option]")).to_have_count(4)
                await modes.get_by_role("option", name="Chat").click()
                await expect(modes).to_be_hidden()
                await expect(mode_pill).to_contain_text("Chat")
                await expect(mode_pill.locator("[data-icon='message']")).to_have_count(1)
                await expect(goal).to_be_focused()
                # A chat has neither goal mode nor Keep going:
                await expect(page.get_by_role("button", name="Add files")).to_be_visible()
                await expect(page.locator(".composer .composer-popover[aria-label='Add']")).to_have_count(0)
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await options_pill.click()
                chat_options = page.locator(".composer .composer-popover[aria-label='Options']")
                await expect(chat_options.get_by_role("switch", name="Goal mode")).to_be_hidden()
                await expect(chat_options.get_by_role("switch", name="Keep going until done")).to_be_hidden()
                await page.keyboard.press("Escape")
                await mode_pill.click()
                await modes.get_by_role("option", name="Task").click()
                await expect(mode_pill).to_contain_text("Task")
                # The "+" opens the file picker directly; Options carries Approvals and Execution
                # (Keep going, Goal mode, Plan first).
                await expect(page.get_by_role("button", name="Add files")).to_be_visible()
                await expect(options_pill).to_contain_text("Edits")
                await options_pill.click()
                popover = page.locator(".composer .composer-popover[aria-label='Options']")
                await expect(popover.get_by_role("group", name="Approvals")).to_be_visible()
                keep_going = popover.get_by_role("switch", name="Keep going until done")
                await expect(keep_going).to_be_visible()
                await expect(popover.get_by_role("switch", name="Goal mode")).to_be_visible()
                # Switched on, it makes the task a campaign: goal mode steps aside and the line under the frame says so.
                await keep_going.click()
                await expect(keep_going).to_have_attribute("aria-checked", "true")
                await expect(popover.get_by_role("switch", name="Goal mode")).to_be_hidden()
                await expect(page.locator(".composer-stack > .composer-note")).to_be_visible()
                await keep_going.click()
                await expect(popover.get_by_role("switch", name="Goal mode")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"options-menu-{width}.png"), animations="disabled")
                # The four modes, strictest first, each with its icon and what it does (item 398).
                modes: Locator = popover.get_by_role("listbox", name="Approval mode").get_by_role("option")
                assert await modes.evaluate_all("(rows) => rows.map((row) => row.dataset.mode)") == [
                    "supervised",
                    "ask",
                    "auto",
                    "yolo",
                ]
                await expect(modes.locator(".pop-option-text > span:first-child")).to_have_text(
                    ["Supervised", "Auto-accept edits", "Auto", "YOLO"]
                )
                await popover.locator(".pop-option[data-mode='yolo']").click()
                await expect(options_pill).to_contain_text("YOLO")
                # YOLO warns of nothing (item 500): the pill wears no tint and the menu carries no note.
                await expect(options_pill).not_to_have_attribute("data-tone", "warn")
                await expect(popover.locator(".note.warn")).to_have_count(0)
                # The four modes show whole, with no scroll, where the window has the room.
                assert await popover.evaluate("(menu) => menu.scrollHeight <= menu.clientHeight")
                await expect(options_pill.locator("[data-icon='lockOpen']")).to_have_count(1)
                await popover.locator(".pop-option[data-mode='supervised']").click()
                await expect(options_pill).to_contain_text("Supervised")
                await expect(options_pill.locator("[data-icon='lock']")).to_have_count(1)
                await popover.locator(".pop-option[data-mode='ask']").click()
                await expect(options_pill).to_contain_text("Edits")
                await expect(options_pill).to_have_attribute("title", "Approvals: Auto-accept edits")
                await expect(options_pill.locator("[data-icon='pencil']")).to_have_count(1)
                # A click outside (on the empty page under the composer) closes the menu.
                await page.mouse.click(width - 20, 880)
                await expect(popover).to_be_hidden()
                # A draft keeps: the text typed and not sent is there again after a walk to another page.
                await goal.fill("keep me for later")
                await page.goto(f"http://{host}:{port}/#/agents")
                await page.goto(f"http://{host}:{port}/#/runs")
                await expect(page.locator(GOAL)).to_have_value("keep me for later")
                await page.locator(GOAL).fill("")
                await page.goto(f"http://{host}:{port}/#/agents")
                await page.goto(f"http://{host}:{port}/#/runs")
                await expect(page.locator(GOAL)).to_have_value("")
                assert refused == [], refused
                await page.close()
        finally:
            await browser.close()


# The composer's box, and the middle, the top, the bottom and the right edge of every control and of the box's field
# that shows (item 115, item 374).
_BAR: str = """(() => {
  const composer = document.querySelector('.composer').getBoundingClientRect();
  const name = (node) => node.dataset.pill
    ?? (node.classList.contains('send') ? 'send'
      : node.classList.contains('plus-button') ? 'plus'
      : node.classList.contains('mic-button') ? 'mic'
      : node.classList.contains('composer-field') ? 'field'
      : node.className);
  const parts = [...document.querySelectorAll(
    '.composer-bar > button, .composer-bar > .composer-field, .composer-options > :not([hidden])'
  )]
    .filter((node) => node.getClientRects().length > 0)
    .map((node) => {
      const box = node.getBoundingClientRect();
      return [name(node), box.top + box.height / 2, box.right, box.top, box.bottom, box.height];
    });
  return { right: composer.right, bottom: composer.bottom, parts };
})()"""
# The bar is as tall as its pills; on a phone they are sized for a finger (item 273).
_BAR_PX: int = 32
_PHONE_BAR_PX: int = 36
_PHONE_PX: int = 720


async def test_the_bar_keeps_one_line_from_the_desk_to_the_phone(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 115: with every pill out (an agent, a model with reasoning levels, YOLO), the bar is one line at 1280, 800,
    560 and 390 px, as tall as its pills (32 px, and 36 px on a phone, item 273), nothing past its right edge; the
    mode's name folds to its icon on a short bar, and YOLO wears no fill. The microphone (item 574, which Chrome's
    speech recognition gives the bar) stands on the send's line, as tall as the "+" button."""
    made: httpx.Response = await client.post(
        "/api/agents",
        json={
            "name": "Scout",
            "role": "Finds and checks sources",
            "instructions": "Prefer primary sources.",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": None,
        },
    )
    assert made.status_code == 201, made.text
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 800, 560, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await show_choices(page)
                await page.route("**/api/models", _with_a_thinking_model)
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator(".composer-pill[data-pill='agent']")).to_be_visible()
                await page.locator(".composer-pill[data-pill='model']").click()
                await (
                    page.locator(".composer-popover[aria-label='Model']").get_by_role("option", name="thinker").click()
                )
                await expect(page.locator(".composer-pill[data-pill='reasoning']")).to_be_visible()
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await options_pill.click()
                await page.locator(".composer-popover[aria-label='Options'] .pop-option[data-mode='yolo']").click()
                await page.mouse.click(width - 20, 880)
                await expect(page.locator(".composer-popover[aria-label='Options']")).to_be_hidden()
                await page.mouse.move(0, 0)
                bar: dict[str, float | list[list[str | float]]] = await page.evaluate(_BAR)
                right, parts = bar["right"], bar["parts"]
                assert isinstance(right, float | int)
                # On Auto (item 495) the bar has no mode pill; Chrome has speech recognition, so it has a microphone.
                assert isinstance(parts, list) and len(parts) == 8, (width, bar)
                by_name: dict[str, list[float]] = {str(part[0]): [float(value) for value in part[1:]] for part in parts}
                assert set(by_name) == {"plus", "mic", "send", "field", "agent", "model", "reasoning", "options"}, bar
                # The pills keep one line at every width, and their height is the bar's (item 273).
                pills = [by_name[name] for name in ("agent", "model", "reasoning", "options")]
                assert max(float(pill[0]) for pill in pills) - min(float(pill[0]) for pill in pills) <= 1, (width, bar)
                height_px = _BAR_PX if width > _PHONE_PX else _PHONE_BAR_PX
                assert all(round(float(pill[4])) == height_px for pill in pills), (width, bar)
                assert all(float(part[2]) <= right + 0.5 for part in parts), (width, bar)
                plus, send, field = by_name["plus"], by_name["send"], by_name["field"]
                # The microphone is as tall as the "+" (item 574).
                mic = by_name["mic"]
                assert round(float(mic[4])) == height_px, (width, bar)
                if width > _PHONE_PX:
                    # The desk keeps the box above its controls: one line of pills, the field over it, and the
                    # microphone on the send's line, left of it.
                    assert max(abs(float(plus[0]) - float(pill[0])) for pill in pills) <= 1, (width, bar)
                    assert abs(float(plus[0]) - float(send[0])) <= 1, (width, bar)
                    assert float(field[3]) < float(pills[0][2]) - 1, (width, bar)
                    assert abs(float(mic[0]) - float(send[0])) <= 1 and float(mic[1]) < float(send[1]), (width, bar)
                else:
                    # The phone puts the "+" and the send beside the box, and the pills on the line under it (item 374);
                    # the microphone is on the line of the pills, so that the words of an empty box keep their room.
                    assert abs(float(plus[0]) - float(send[0])) <= 1, (width, bar)
                    assert float(pills[0][2]) > float(plus[3]) - 1, (width, bar)
                    assert abs(float(field[3]) - float(plus[3])) <= 2, (width, bar)
                    assert abs(float(mic[0]) - float(pills[0][0])) <= 1, (width, bar)
                    assert float(mic[1]) <= float(send[1]), (width, bar)
                # On Auto (item 495) the mode has no pill at any width, and the Options pill shows YOLO with no tint
                # (item 500).
                await expect(page.locator(".composer-pill[data-pill='mode']")).to_be_hidden()
                await expect(options_pill).not_to_have_attribute("data-tone", "warn")
                # Closed, the pill rests with no fill; its colour fades there, so the check waits for it to settle.
                await expect(options_pill).to_have_css("background-color", "rgba(0, 0, 0, 0)")
                await page.locator(".composer-stack").screenshot(path=str(tmp_path / f"bar-{width}.png"))
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_a_photo_in_the_box_shows_its_picture_beside_the_files_it_came_with(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """A photo attached to a message shows the picture itself, a thumbnail tall, with its name as its tooltip and as the
    words of its buttons, where another file keeps its icon, its name and its size. The chips stand in one row at the
    desk, and on to a second line only when the phone's box has no width left."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await show_choices(page)
            await page.goto(ui_url(host, port, TOKEN))
            await page.locator(".composer input[type=file]").set_input_files(
                [
                    {"name": "sunset.png", "mimeType": "image/png", "buffer": b64decode(_SUNSET_PNG)},
                    {"name": "harbour.png", "mimeType": "image/png", "buffer": b64decode(_HARBOUR_PNG)},
                    {
                        "name": "a-rather-long-note-of-the-evening.txt",
                        "mimeType": "text/plain",
                        "buffer": b"one line\n",
                    },
                ]
            )
            chips: Locator = page.locator(".composer .attach-chip")
            await expect(chips).to_have_count(3)
            await expect(page.locator(".composer .attach-chip.uploading")).to_have_count(0)
            # A photo shows itself at 56 x 56, rounded, with no name or size beside it.
            photos: Locator = page.locator(".composer .attach-chip.picture")
            await expect(photos).to_have_count(2)
            first: Locator = photos.first
            await expect(first).to_have_attribute("title", "sunset.png")
            assert (await first.text_content() or "").strip() == ""
            # The thumbnail has an address once its file is read; it is measured once it has one and is decoded.
            await expect(first.locator("img.attach-thumb")).to_have_attribute("src", re.compile(r"."))
            drawn: dict[str, object] = await first.locator("img.attach-thumb").evaluate(
                "async (image) => { await image.decode(); const r = image.getBoundingClientRect();"
                " const style = getComputedStyle(image);"
                " return { width: Math.round(r.width), height: Math.round(r.height), radius: style.borderRadius,"
                " loaded: image.naturalWidth > 0 }; }"
            )
            assert (drawn["width"], drawn["height"]) == (56, 56), drawn
            assert drawn["radius"] not in ("", "0px"), drawn
            assert drawn["loaded"] is True, drawn
            # Its name is the tooltip and the words of its buttons.
            await expect(first.get_by_role("button", name="Show sunset.png")).to_be_visible()
            await expect(first.get_by_role("button", name="Remove sunset.png")).to_be_visible()
            # Another file keeps its chip: an icon, its name and its size.
            note: Locator = page.locator(".composer .attach-chip.ready").filter(has_text="a-rather-long-note")
            await expect(note).to_have_count(1)
            await expect(note.locator("img.attach-thumb")).to_have_count(0)
            await expect(note).to_contain_text("9 B")
            # One row at the desk.
            bottoms: list[int] = await chips.evaluate_all(
                "(chips) => chips.map((chip) => Math.round(chip.getBoundingClientRect().bottom))"
            )
            assert len(set(bottoms)) == 1, bottoms
            await page.locator(".composer .attach-list").screenshot(path=str(tmp_path / "photo-chips-1280.png"))
            # On a phone the photos stay side by side, inside the box, and another file's chip keeps its one line under
            # them rather than growing tall with its name.
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.wait_for_function(
                "() => window.innerWidth === 390"
                " && document.querySelector('.composer').getBoundingClientRect().width > 300"
            )
            photo_bottoms: list[int] = await photos.evaluate_all(
                "(photos) => photos.map((photo) => Math.round(photo.getBoundingClientRect().bottom))"
            )
            assert len(set(photo_bottoms)) == 1, photo_bottoms
            right: float = await page.locator(".composer").evaluate("(box) => box.getBoundingClientRect().right")
            edges: list[float] = await chips.evaluate_all(
                "(chips) => chips.map((chip) => chip.getBoundingClientRect().right)"
            )
            assert all(edge <= right for edge in edges), (right, edges)
            # The name keeps one line, clipped by the chip rather than folded into a second.
            assert (
                await note.locator(".attach-open > span").first.evaluate(
                    "(name) => name.getBoundingClientRect().height"
                )
                < 24
            )
            await page.locator(".composer .attach-list").screenshot(path=str(tmp_path / "photo-chips-390.png"))
            await page.screenshot(path=str(tmp_path / "photo-chips-390-page.png"))
            # A draft's photos come back as their pictures, not their names, when the page is loaded again.
            await page.set_viewport_size({"width": 1280, "height": 900})
            await page.reload()
            await page.wait_for_function("() => window.innerWidth === 1280")
            await expect(photos).to_have_count(2)
            await expect(photos.first).to_have_attribute("title", "sunset.png")
            assert (await photos.first.text_content() or "").strip() == ""
            await expect(photos.first.locator("img.attach-thumb")).to_have_attribute("src", re.compile(r"^blob:"))
        finally:
            await browser.close()


async def test_the_box_is_one_line_with_the_runs_choices_behind_the_plus(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 833: the box as one line, the "+", the words and the microphone, as a messaging app's; the run's choices
    wait behind the "+" menu's "Model and options", which shows and hides them and is kept; YOLO stays in sight while
    they are away; the microphone stands alone until words are typed, and the send then comes beside it."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                await page.goto(ui_url(host, port, TOKEN))
                composer: Locator = page.locator(".composer").last
                await expect(composer).to_have_attribute("data-quiet", "")
                await expect(composer.locator(".composer-pill[data-pill='model']")).to_be_hidden()
                # One line: the "+" and the words share it.
                plus: Locator = composer.locator(".plus-button")
                field: Locator = composer.locator(".composer-field")
                await expect(field).to_be_visible()
                # Both read in one frame: the home's box still settling between two reads put them apart.
                centres: list[float] = await composer.evaluate(
                    "box => ['.plus-button', '.composer-field'].map((part) => {"
                    " const rect = box.querySelector(part).getBoundingClientRect(); return rect.y + rect.height / 2; })"
                )
                assert abs(centres[0] - centres[1]) <= 8, centres
                # The microphone alone until words are typed, then the send beside it: more can still be dictated.
                mic: Locator = composer.locator(".mic-button")
                send: Locator = composer.locator(".btn.send")
                await expect(mic).to_be_visible()
                await expect(send).to_be_hidden()
                await composer.locator("textarea").fill("Plan the garden")
                await expect(send).to_be_visible()
                await expect(mic).to_be_visible()
                await composer.locator("textarea").fill("")
                # The "+" shows the choices, and the choice is kept.
                await plus.click()
                await page.get_by_role("menuitem", name="Model and options").click()
                await expect(composer).not_to_have_attribute("data-quiet", "")
                options: Locator = composer.locator(".composer-pill[data-pill='options']")
                await expect(options).to_be_visible()
                assert await page.evaluate("() => localStorage.getItem('dot.composer.choices')") == "shown"
                # YOLO chosen, then the choices hidden: its pill stays in sight, with no tint.
                await options.click()
                await page.locator(".composer-popover .pop-option[data-mode='yolo']").click()
                await page.keyboard.press("Escape")
                await plus.click()
                await page.get_by_role("menuitem", name="Hide the model and options").click()
                await expect(composer).to_have_attribute("data-quiet", "")
                await expect(options).to_be_visible()
                await expect(options).to_contain_text("YOLO")
                await expect(options).not_to_have_attribute("data-tone", "warn")
                await expect(composer.locator(".composer-pill[data-pill='model']")).to_be_hidden()
                assert await page.evaluate("() => localStorage.getItem('dot.composer.choices')") is None
                # Back to the mode the module's other tests expect.
                await plus.click()
                await page.get_by_role("menuitem", name="Model and options").click()
                await options.click()
                await page.locator(".composer-popover .pop-option[data-mode='ask']").click()
                await page.close()
        finally:
            await browser.close()
