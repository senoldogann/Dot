"""The composer (items 64, 104 and 115): typing ``/`` on the new chat page opens the menu of commands and skills,
letters filter it, the arrows move, Enter picks without sending (a skill as a chip, item 318), Escape closes;
Shift+Enter breaks a line. The bar's pills open the Model and the Options menus, and the "+" goal mode; a draft
survives a navigation; a photo in the box shows the picture itself, its name its tooltip, in one row beside the other
files; on a desk the words have a line of their own and the controls the line under it (item 1249). Wide and on a
phone."""

import asyncio
import re
import sys
import time
from base64 import b64decode
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from chat_settings import close_settings, open_row, open_settings, pick_model
from PIL import Image
from playwright.async_api import Browser, ConsoleMessage, Locator, Page, Route, async_playwright, expect

import dotagent.skills as skills_pkg
from dotagent.agent.attachments import ATTACHMENTS_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import McpServerConfig, load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL

_MCP_STARTER_FIXTURE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_catalogue_server.py"
_VIDEO_SAMPLE: Path = Path(__file__).resolve().parents[2] / "ios/Next/UITests/attachment-sample.mp4"

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

# The skills that come with Dot: a folder each (skills/builtin).
_BUILT_IN: tuple[str, ...] = tuple(
    sorted(
        path.name for path in (Path(skills_pkg.__file__).parent / "builtin").iterdir() if (path / "SKILL.md").is_file()
    )
)

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
        # No floor of free space: an upload is not refused by how full this Mac's disk is (item 1938 has its own test).
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n'
        "[agent]\ngoal_mode = false\n\n[store]\nmin_free_gb = 0",
        encoding="utf-8",
    )
    (home / "skills" / "scan-catalogue").mkdir(parents=True)
    (home / "skills" / "scan-catalogue" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    await built.router.register("ollama", ScriptedProvider("ollama", [finish] * 30))
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
    # By name, the owner's skill among those that come with Dot (item 196), whichever they are now.
    listed: list[tuple[str, str]] = [(skill["name"], skill["source"]) for skill in found["skills"]]
    assert listed == sorted([*((name, "built-in") for name in _BUILT_IN), ("scan-catalogue", "home")])
    (own,) = [skill for skill in found["skills"] if skill["name"] == "scan-catalogue"]
    assert own["description"] == "Scan a product catalogue page by page and report the items."
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
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                menu = page.locator(".composer .slash-menu")
                await expect(menu).to_be_hidden()
                await goal.click()
                await goal.type("/")
                await expect(menu).to_be_visible()
                # Five commands (Auto and the three modes, item 495, and /loop, item 400), the owner's skill and those
                # that come with Dot (item 196).
                await expect(menu.locator("[role=option]")).to_have_count(5 + 1 + len(_BUILT_IN))
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


async def test_the_model_opens_the_chats_settings_and_a_draft_survives_a_navigation(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 2015: the model's pill is the one choice in the bar on every width, as DotNext's chip; it opens the chat's
    settings, which hold the models, the reasoning, the mode, the approvals and how Dot works. One row while the words
    are short (the mark and its chevron), the words over the controls once they are long (the mark, the name, the level
    in grey)."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            width: int = 1280
            page: Page = await browser.new_page(viewport={"width": width, "height": 900})
            refused: list[str] = []
            page.on("console", _collect_refusals(refused))
            await page.route("**/api/models", _with_a_thinking_model)
            await page.goto(ui_url(host, port, TOKEN))
            goal = page.locator(GOAL)
            composer = page.locator(".composer").first
            # The pill wears the mark of its provider, an SVG of the page's own (items 108 and 959).
            model_pill = page.locator(".composer-pill[data-pill='model']")
            await expect(model_pill).to_contain_text("scripted")
            mark = model_pill.locator(".provider-mark")
            await expect(mark).to_have_attribute("data-provider", "ollama")
            await expect(mark.locator("svg")).to_have_count(1)
            await expect(mark).to_have_css("mask-image", "none")
            served: httpx.Response = await client.get("/icons/providers/ollama.svg")
            assert served.status_code == 200, served.status_code
            assert served.headers["content-type"].startswith("image/svg+xml"), served.headers["content-type"]
            # One row at rest: the name is for a screen reader, the chevron shows; no other choice is in the bar.
            await expect(composer).to_have_attribute("data-shape", "compact")
            name_box = await model_pill.locator(".composer-pill-value").bounding_box()
            assert name_box is not None and name_box["width"] <= 1, name_box
            await expect(model_pill.locator(":scope > .icon").last).to_be_visible()
            for pill in ("mode", "reasoning", "options"):
                await expect(page.locator(f".composer-pill[data-pill='{pill}']")).to_be_hidden()
            # The settings: the daemon's default ticked, every model with its mark; a pick keeps them open.
            sheet: Locator = await open_settings(page, page)
            await expect(sheet.locator("[role=option][aria-selected=true]").first).to_contain_text("Default (scripted)")
            await page.screenshot(path=str(tmp_path / f"chat-settings-{width}.png"), animations="disabled")
            await sheet.get_by_role("option", name="thinker").first.click()
            await expect(sheet).to_be_visible()
            # A model with levels: the Reasoning row offers the daemon's level and the three it has.
            levels: Locator = await open_row(sheet, "Reasoning")
            await expect(levels.locator("[role=option]")).to_have_count(4)
            await expect(levels.locator("[role=option][aria-selected=true]")).to_contain_text("Default (Medium)")
            await levels.get_by_role("option", name="High", exact=True).click()
            await close_settings(page)
            # Long words open the box: the model is named, with its level in grey after it.
            await goal.fill("Look at this repository and tell me what it does")
            await expect(composer).to_have_attribute("data-shape", "expanded")
            await expect(model_pill.locator(".composer-pill-value")).to_have_text("thinker")
            await expect(model_pill.locator(".composer-pill-extra")).to_have_text("High")
            await goal.fill("")
            await expect(composer).to_have_attribute("data-shape", "compact")
            # Back to the daemon's default.
            await pick_model(page, page, "Default (scripted)")
            await expect(model_pill).to_contain_text("scripted")
            # The mode, from the words and from the Mode row: a mode named with its command is said after the name.
            await goal.type("/task")
            await goal.press("Tab")
            await expect(model_pill.locator(".composer-pill-extra")).to_contain_text("Task")
            sheet = await open_settings(page, page)
            modes: Locator = await open_row(sheet, "Mode")
            await expect(modes.locator("[role=option]")).to_have_count(4)
            await modes.get_by_role("option", name="Chat").click()
            await expect(model_pill.locator(".composer-pill-extra")).to_contain_text("Chat")
            # A chat has neither goal mode nor Keep going.
            await expect(sheet.get_by_role("switch", name="Goal mode")).to_have_count(0)
            await expect(sheet.get_by_role("switch", name="Keep going until done")).to_have_count(0)
            await modes.get_by_role("option", name="Task").click()
            # How Dot works holds Keep going and goal mode; Keep going steps goal mode aside, said under the frame.
            keep_going: Locator = sheet.get_by_role("switch", name="Keep going until done")
            await expect(keep_going).to_be_visible()
            await expect(sheet.get_by_role("switch", name="Goal mode")).to_be_visible()
            await keep_going.click()
            await expect(keep_going).to_have_attribute("aria-checked", "true")
            await expect(sheet.get_by_role("switch", name="Goal mode")).to_be_hidden()
            await expect(page.locator(".composer-stack > .composer-note")).to_be_attached()
            await keep_going.click()
            await expect(sheet.get_by_role("switch", name="Goal mode")).to_be_visible()
            # The approvals, strictest first; YOLO leaves its lock in the bar, as the run asks nothing in it.
            approvals: Locator = await open_row(sheet, "Approvals")
            rows: Locator = approvals.get_by_role("listbox", name="Approval mode").get_by_role("option")
            assert await rows.evaluate_all("(rows) => rows.map((row) => row.dataset.mode)") == [
                "supervised",
                "ask",
                "auto",
                "yolo",
            ]
            await approvals.locator(".pop-option[data-mode='yolo']").click()
            await close_settings(page)
            options_pill = page.locator(".composer-pill[data-pill='options']")
            await expect(options_pill).to_be_visible()
            await expect(options_pill).not_to_have_attribute("data-tone", "warn")
            sheet = await open_settings(page, page)
            approvals = await open_row(sheet, "Approvals")
            await approvals.locator(".pop-option[data-mode='ask']").click()
            await close_settings(page)
            await expect(options_pill).to_be_hidden()
            await expect(options_pill).to_have_attribute("title", "Approvals: Auto-accept edits")
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
      : node.classList.contains('choices-toggle') ? 'choices'
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
            # The same capsule at every width (item 2015); a phone's line is `_phone_box_rests_until_used`'s too.
            for width in (1280, 800):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await page.route("**/api/models", _with_a_thinking_model)
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator(".composer-pill[data-pill='agent']")).to_be_visible()
                await pick_model(page, page, "thinker")
                sheet: Locator = await open_settings(page, page)
                approvals: Locator = await open_row(sheet, "Approvals")
                await approvals.locator(".pop-option[data-mode='yolo']").click()
                await close_settings(page)
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await page.mouse.move(0, 0)
                # One row at rest: the "+", the words, the agent, the model's mark, YOLO's lock, the microphone and the
                # send on one centre line; the reasoning and the mode are in the settings, not in the bar.
                bar: dict[str, float | list[list[str | float]]] = await page.evaluate(_BAR)
                right, parts = bar["right"], bar["parts"]
                assert isinstance(right, float | int)
                assert isinstance(parts, list), (width, bar)
                by_name: dict[str, list[float]] = {str(part[0]): [float(value) for value in part[1:]] for part in parts}
                assert set(by_name) == {"plus", "mic", "send", "field", "agent", "model", "options"}, (width, bar)
                centre: float = by_name["send"][0]
                assert all(abs(float(part[1]) - centre) <= 1 for part in parts), (width, bar)
                assert all(float(part[2]) <= right + 0.5 for part in parts), (width, bar)
                # Long words: the words over the controls, which keep one line, the model named with its level.
                await page.locator(GOAL).fill("Look at this repository and say what it does")
                await expect(page.locator(".composer").first).to_have_attribute("data-shape", "expanded")
                bar = await page.evaluate(_BAR)
                parts = bar["parts"]
                assert isinstance(parts, list)
                by_name = {str(part[0]): [float(value) for value in part[1:]] for part in parts}
                field = by_name.pop("field")
                line = list(by_name.values())
                assert max(part[0] for part in line) - min(part[0] for part in line) <= 1, (width, bar)
                assert field[3] < min(part[2] for part in line) - 1, (width, bar)
                await expect(page.locator(".composer-pill[data-pill='model'] .composer-pill-extra")).to_be_visible()
                await page.locator(GOAL).fill("")
                await expect(page.locator(".composer-pill[data-pill='mode']")).to_be_hidden()
                await expect(options_pill).not_to_have_attribute("data-tone", "warn")
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


def _noisy_picture(path: Path) -> int:
    """Write a picture of noise at ``path`` and give its size in bytes."""
    Image.effect_noise((3000, 1800), 64).save(path)
    return path.stat().st_size


def _picture_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as picture:
        return picture.size


@pytest.mark.browser
async def test_a_large_photo_goes_smaller_and_a_send_while_it_uploads_says_to_wait_once(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 990, the owner's word of 2026-11-05: a phone's photos uploaded slowly, and each press of Send while they did
    said "Wait until the files are uploaded" once more. A picture past a megabyte and 2048 pixels goes as a JPEG of 2048
    on its longer side, and Send pressed while it uploads says to wait once, however often it is pressed."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    picture: Path = tmp_path / "harbour.png"
    # A noisy picture, which no PNG makes small: 3000 x 1800, several megabytes.
    picture_bytes: int = await asyncio.to_thread(_noisy_picture, picture)
    assert picture_bytes > 1024 * 1024
    goal: str = "What is in this photo?"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
            await page.goto(ui_url(host, port, TOKEN))
            # The upload takes a while, as on a phone's network.
            held: asyncio.Event = asyncio.Event()

            async def slow(route: Route) -> None:
                await held.wait()
                await route.continue_()

            await page.route("**/api/attachments", slow)
            await page.locator(".composer input[type=file]").set_input_files(str(picture))
            await expect(page.locator(".composer .attach-chip.uploading")).to_have_count(1)
            box: Locator = page.locator(".composer textarea")
            await box.fill(goal)
            # Send pressed while it uploads says to wait, once however often it is pressed.
            await box.press("Enter")
            await box.press("Enter")
            await box.press("Enter")
            waits: Locator = page.locator(".toast", has_text="Wait until the files are uploaded")
            await expect(waits).to_have_count(1)
            async with page.expect_response("**/api/attachments") as answered:
                held.set()
            stored = await (await answered.value).json()
            assert (stored["name"], stored["media_type"]) == ("harbour.jpg", "image/jpeg")
            assert stored["size"] < picture_bytes
            assert await asyncio.to_thread(_picture_size, Path(stored["path"])) == (2048, 1229)
            # Once the file is there, the message goes with it.
            await expect(page.locator(".composer .attach-chip.ready")).to_have_count(1)
            await box.press("Enter")
            await expect(page).to_have_url(re.compile(r"#/runs/run_"))
            runs = [run for run in await runtime.store.list_runs(20, None) if run.goal == goal]
            assert len(runs) == 1
            files = await runtime.store.events_of_kind(runs[0].id, ATTACHMENTS_EVENT, 5)
            assert [file["id"] for file in files[0].data["files"]] == [stored["id"]]
        finally:
            await browser.close()


async def test_video_from_the_plus_menu_and_drag_and_drop_reaches_the_run(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            uploads: list[str] = []
            page.on(
                "request",
                lambda request: (
                    uploads.append(f"{request.method} /api{request.url.split('?')[0].split('/api')[1]}")
                    if "/api/attachments" in request.url
                    else None
                ),
            )
            await page.goto(ui_url(client.base_url.host, port, TOKEN))
            composer: Locator = page.locator(".composer")
            # The picker's own input: whether the "+" is a menu or the picker itself depends on the mode an earlier
            # test of this module left the daemon in, and this test is about what a video does, not the "+".
            await composer.locator("input[type=file]").set_input_files(_VIDEO_SAMPLE)
            await expect(composer.locator(".attach-chip.ready")).to_have_count(1)
            transfer = await page.evaluate_handle(
                """bytes => {
                    const transfer = new DataTransfer();
                    transfer.items.add(new File([new Uint8Array(bytes)], 'drop.mp4', { type: 'video/mp4' }));
                    return transfer;
                }""",
                list(await asyncio.to_thread(_VIDEO_SAMPLE.read_bytes)),
            )
            await composer.dispatch_event("drop", {"dataTransfer": transfer})
            await expect(composer.locator(".attach-chip.ready")).to_have_count(2)
            # Both went as the raw body of the streamed upload, not as base64 in JSON (item 1933).
            assert uploads == ["PUT /api/attachments/stream"] * 2
            await page.locator(GOAL).fill("Two ways to attach a video")
            async with page.expect_response(
                lambda answer: answer.url.endswith("/api/runs") and answer.request.method == "POST"
            ) as sent:
                await page.locator(GOAL).press("Enter")
            answer = await sent.value
            assert answer.status == 201, await answer.text()
            run = await answer.json()
            files = await runtime.store.events_of_kind(run["id"], ATTACHMENTS_EVENT, 5)
            assert [(file["name"], file["kind"]) for file in files[0].data["files"]] == [
                ("attachment-sample.mp4", "video"),
                ("drop.mp4", "video"),
            ]
        finally:
            await browser.close()


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (390, 844)], ids=["desktop", "phone"])
async def test_a_video_is_a_tile_with_its_length_in_the_box_and_over_the_sent_message_and_plays_full_screen(
    runtime: Runtime, client: httpx.AsyncClient, width: int, height: int
) -> None:
    """Item 1992, on the web as on DotNext: a video added stands as a tile (its first frame, a play glyph, its length, a
    ×), the same tile stands over the message once sent, and a click plays it full screen until Escape."""
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": width, "height": height})
            await page.goto(ui_url(client.base_url.host, port, TOKEN))
            composer: Locator = page.locator(".composer")
            # The picker's own input: the "+" is a menu on a desktop and the picker itself on a phone.
            await composer.locator("input[type=file]").set_input_files(_VIDEO_SAMPLE)
            tile: Locator = composer.locator(".attach-chip.picture.ready")
            await expect(tile).to_have_count(1)
            await expect(tile.locator(".media-length")).to_have_text("0:01")
            await expect(tile.locator(".media-play")).to_be_visible()
            await expect(tile.locator(".attach-thumb")).to_have_attribute("src", re.compile("^data:image/jpeg"))
            await expect(tile.get_by_role("button", name="Remove attachment-sample.mp4")).to_be_visible()
            await expect(tile.get_by_role("button", name="Play video attachment-sample.mp4")).to_be_visible()
            await page.locator(GOAL).fill("What happens in this video")
            async with page.expect_response(
                lambda answer: answer.url.endswith("/api/runs") and answer.request.method == "POST"
            ) as sent:
                await page.locator(GOAL).press("Enter")
            assert (await sent.value).status == 201
            sent_tile: Locator = page.locator(".sent-video").first
            await expect(sent_tile).to_be_visible()
            await expect(sent_tile.locator(".media-length")).to_have_text("0:01")
            await expect(sent_tile.locator(".sent-picture-image")).to_have_attribute(
                "src", re.compile("^data:image/jpeg")
            )
            await expect(sent_tile.locator(".media-play")).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                "no sideways scroll"
            )
            box = await sent_tile.bounding_box()
            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, box
            await sent_tile.click()
            player: Locator = page.locator(".player-overlay")
            await expect(player).to_be_visible()
            await expect(player.locator("video")).to_have_attribute("src", re.compile("^blob:"))
            # Polled here: wait_for_function fails under the page's CSP once it has to wait (see the memory notes).
            deadline: float = time.monotonic() + 15
            while not await page.evaluate("document.querySelector('.player-video').readyState >= 1"):
                assert time.monotonic() < deadline, "the video never loaded its start"
                await asyncio.sleep(0.2)
            await page.keyboard.press("Escape")
            await expect(player).to_have_count(0)
        finally:
            await browser.close()


async def _phone_box_has_its_line(browser: Browser, host: str, port: int) -> None:
    """Items 1120, 1121 and 1330 on a phone's width: the box is the words with, on the line under them, the "+", the
    model's pill, the microphone and the send, always in sight; the model's pill alone stands for the choices, and its
    sheet (item 1202) holds the approvals and the mode; the "+" menu has no Model and options."""
    page: Page = await browser.new_page(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
    await page.goto(ui_url(host, port, TOKEN))
    composer: Locator = page.locator(".composer").last
    model: Locator = composer.locator(".composer-pill[data-pill='model']")
    await expect(composer).not_to_have_attribute("data-quiet", "")
    for part in (".plus-button", ".composer-field", ".btn.send", ".mic-button", ".composer-pill[data-pill='model']"):
        await expect(composer.locator(part)).to_be_visible()
    await expect(composer.locator(".composer-pill[data-pill='options']")).to_be_hidden()
    # At rest one row (item 2015): the "+", the words, the model's mark, the microphone and the send on one centre line,
    # the microphone as tall as the send and left of it (read in one frame).
    measure: str = (
        "box => ['.composer-field', '.plus-button', \".composer-pill[data-pill='model']\", '.mic-button', '.btn.send']"
        ".map((part) => { const r = box.querySelector(part).getBoundingClientRect();"
        " return [r.top, r.bottom, r.left, r.right, r.height]; })"
    )
    rects: list[list[float]] = await composer.evaluate(measure)
    field, plus, pill, mic, send = rects
    centres: list[float] = [(part[0] + part[1]) / 2 for part in (field, plus, pill, mic, send)]
    assert max(centres) - min(centres) <= 2, rects
    assert round(mic[4]) == round(send[4]) == 36, rects
    assert plus[3] <= field[2] and field[3] <= pill[2] and pill[3] <= mic[2] and mic[3] <= send[2] + 1, rects
    # Long words: the words over the line, on which the "+", the model, the microphone and the send stand.
    await composer.locator("textarea").fill("Look at this repository and say what it does")
    await expect(composer).to_have_attribute("data-shape", "expanded")
    rects = await composer.evaluate(measure)
    field, plus, pill, mic, send = rects
    centres = [(part[0] + part[1]) / 2 for part in (plus, pill, mic, send)]
    assert max(centres) - min(centres) <= 2, rects
    assert all(part[0] >= field[1] - 1 for part in (plus, pill, mic, send)), rects
    await composer.locator("textarea").fill("")
    await model.click()
    # The chat's settings (item 1202): a sheet with the models, then the Mode and the Approvals as rows that open in
    # place.
    menu: Locator = page.get_by_role("dialog", name="Chat settings")
    await expect(menu).to_be_visible()
    await menu.locator("summary", has_text="Mode").click()
    await expect(menu.get_by_role("listbox", name="Mode", exact=True)).to_be_visible()
    await menu.locator("summary", has_text="Approvals").click()
    # The approval mode is no part of the model's name (the owner read "Pay or send" as one).
    await menu.locator(".pop-option[data-mode='yolo']").click()
    await expect(menu.locator(".pop-option[data-mode='yolo']")).to_have_attribute("aria-selected", "true")
    await expect(model).not_to_contain_text("Pay or send")
    # The row says the mode chosen as it is chosen.
    await expect(menu.locator("summary", has_text="Approvals")).to_contain_text("Ask only to pay or send")
    # A pick repaints the sheet: the row stays open, and the sheet stays within the screen.
    await menu.locator(".pop-option[data-mode='ask']").click()
    await expect(menu.locator(".pop-option[data-mode='ask']")).to_have_attribute("aria-selected", "true")
    shown = await menu.bounding_box()
    assert shown is not None
    assert shown["y"] >= 0 and shown["y"] + shown["height"] <= 900, shown
    await page.keyboard.press("Escape")
    # A phone's "+" holds what adds to the message; the choices are in the bar, and have no button.
    await expect(composer.locator(".choices-toggle")).to_have_count(0)
    await page.close()


async def test_the_words_have_their_own_line_and_the_runs_choices_stand_in_the_bar(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Items 833, 1249 and 1330: the choices of the run are always in the bar (the model's pill with its reasoning, the
    approvals under the box), with no button of their own beside the "+" and no resting state that puts them away; the
    microphone and the send stand side by side from the start. On a Mac the words have a line of their own, and the
    "+", the microphone and the send the line under them, however many lines the words take."""
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280,):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                await page.goto(ui_url(host, port, TOKEN))
                composer: Locator = page.locator(".composer").last
                await expect(composer).not_to_have_attribute("data-quiet", "")
                await expect(composer.locator(".composer-pill[data-pill='model']")).to_be_visible()
                await expect(composer.locator(".choices-toggle")).to_have_count(0)
                # Short words share the row with the "+" and the send (item 2015); long ones take a line of their
                # own, the "+" under them, read in one frame.
                plus: Locator = composer.locator(".plus-button")
                field: Locator = composer.locator(".composer-field")
                await expect(field).to_be_visible()
                await expect(composer).to_have_attribute("data-shape", "compact")
                await composer.locator("textarea").fill("Look at this repository and say what it does")
                await expect(composer).to_have_attribute("data-shape", "expanded")
                edges: list[float] = await composer.evaluate(
                    "box => [box.querySelector('.plus-button').getBoundingClientRect().top,"
                    " box.querySelector('.composer-field').getBoundingClientRect().bottom]"
                )
                assert edges[0] >= edges[1] - 2, edges
                await composer.locator("textarea").fill("")
                # The microphone and the send side by side from the start: more can still be dictated after words.
                mic: Locator = composer.locator(".mic-button")
                send: Locator = composer.locator(".btn.send")
                await expect(mic).to_be_visible()
                await expect(send).to_be_visible()
                await composer.locator("textarea").fill("Plan the garden")
                await expect(send).to_be_visible()
                await expect(mic).to_be_visible()
                # A box that grows keeps the "+", the microphone and the send on one line under the words.
                await composer.locator("textarea").fill("Plan the garden\nwhat to sow\nwhat to plant\nwhat to dig")
                tops: list[float] = await composer.evaluate(
                    "box => ['.plus-button', '.mic-button', '.btn.send'].map((part) =>"
                    " box.querySelector(part).getBoundingClientRect().top + box.querySelector(part).offsetHeight / 2)"
                    ".concat([box.querySelector('.composer-field').getBoundingClientRect().bottom])"
                )
                assert all(abs(top - tops[0]) <= 4 for top in tops[:3]), tops
                assert tops[0] > tops[3], tops
                await composer.locator("textarea").fill("")
                # The "+" adds to the message alone: the choices are in the bar.
                await plus.click()
                await expect(page.get_by_role("menuitem", name="Add files")).to_be_visible()
                await expect(page.get_by_role("menuitem", name="Model and options")).to_have_count(0)
                await page.keyboard.press("Escape")
                options: Locator = composer.locator(".composer-pill[data-pill='options']")
                await expect(options).to_be_hidden()
                # YOLO chosen in the chat's settings: its lock stays in the bar, with no tint.
                sheet: Locator = await open_settings(composer, page)
                approvals: Locator = await open_row(sheet, "Approvals")
                await approvals.locator(".pop-option[data-mode='yolo']").click()
                await close_settings(page)
                await expect(options).to_be_visible()
                await expect(options).to_contain_text("Pay or send")
                await expect(options).not_to_have_attribute("data-tone", "warn")
                # On a line of 440 px or less (the bar's own width, which its container query reads) its open lock
                # alone, so that the model keeps its room; its name still says the mode.
                await expect(options).to_have_accessible_name(re.compile(r"\bPay or send\b"))
                pill = await options.bounding_box()
                assert pill is not None
                line: float = await composer.locator(".composer-bar").evaluate(
                    "bar => { const style = getComputedStyle(bar);"
                    " return bar.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight); }"
                )
                assert (pill["width"] <= 44) == (line <= 440), (
                    f"{width}: the YOLO pill is {pill['width']} px wide on a line of {line} px"
                )
                # Back to the mode the module's other tests expect.
                await options.click()
                await page.locator(".composer-popover .pop-option[data-mode='ask']").click()
                await page.close()
            await _phone_box_has_its_line(browser, host, port)
        finally:
            await browser.close()


async def test_an_mcp_prompt_inserts_a_draft_and_connectors_list_the_catalogue(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    await runtime.mcp.connect(
        McpServerConfig(name="starter-notes", command=sys.executable, args=[str(_MCP_STARTER_FIXTURE)])
    )
    port: int | None = client.base_url.port
    assert port is not None
    address: str = ui_url(client.base_url.host, port, TOKEN)
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width in (1280, 390):
                    page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        await page.goto(address)
                        goal: Locator = page.locator(GOAL)
                        await goal.press_sequentially("/summarize")
                        await expect(
                            page.get_by_role("option", name=re.compile("starter-notes · summarize"))
                        ).to_be_visible()
                        await goal.press("Enter")
                        dialog: Locator = page.get_by_role("dialog", name="starter-notes · summarize")
                        insert: Locator = dialog.get_by_role("button", name="Insert draft")
                        await expect(insert).to_be_disabled()
                        await dialog.get_by_role("textbox", name=re.compile("topic")).fill("weekly notes")
                        await insert.click()
                        await expect(goal).to_have_value("Summarize weekly notes.")
                        await expect(page).to_have_url(f"http://{client.base_url.host}:{port}/#/runs")
                        await page.goto(f"http://{client.base_url.host}:{port}/#/customize/connectors")
                        await page.get_by_role("searchbox", name="Search connectors").fill("starter-notes")
                        server: Locator = page.locator("[data-server='starter-notes']")
                        await server.get_by_text("2 resources", exact=True).click()
                        await expect(server.get_by_text("Weekly notes", exact=True)).to_be_visible()
                        await server.get_by_text("2 prompts", exact=True).click()
                        await expect(server.get_by_text("summarize", exact=True)).to_be_visible()
                    finally:
                        await page.close()
            finally:
                await browser.close()
    finally:
        await runtime.mcp.disconnect("starter-notes")
