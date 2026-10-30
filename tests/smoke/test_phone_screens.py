"""The screens of the web UI that did not fit a phone (item 606), in WebKit as a phone's own engine, 390 by 844, a touch
screen, over a served daemon whose model is a scripted one. The faults are those of the audit of every screen (shot
screen by screen at that size, light and dark, in WebKit and Chrome):

- Customize › Plugins cut the name of a plugin to "Creative Desi…" with room to spare: WebKit gives a box of its own the
  width of its text with the space of the variable font's regular weight, and draws the bold name wider by a third of a
  pixel a space, so the ellipsis took the last letters of every name of two words;
- a sentence with a button beside it was squeezed into a column six lines high: the token this browser keeps (General)
  and the two layout choices that follow the theme (Appearance);
- what is tapped without being a button was under a finger's size: the tabs of the run's details not chosen (31 px), the
  name of a file the run changed (22), the link from an approval to a rule (20), the buttons that open the form and the
  texts of a version in Self-improvement (30) and the head of a licence's notice (23);
- how long ago a chat ended ("3m ago") broke in two lines in the list of chats a skill can be saved from, and a version
  in the list of Python packages of the licences ("0.143." over "1") was broken in two;
- a toast stood over the composer, and a version's three buttons in Self-improvement slid the page 9 px sideways on a
  phone 320 px wide;
- the head of the file beside a run gave its button's words the room of the file's name, a forgotten note's buttons
  stood in a column that left its words 140 px, and a ready team the
  owner has already (item 610) had its badge take the room of its description, which stood in 141 px.

No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from composer_choices import show_choices
from playwright.async_api import Browser, FloatRect, Locator, Page, Route, ViewportSize, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.plugins.gallery import GalleryEntry
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_gallery
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "phone-screens-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
# A phone's window and the least a finger has for a button.
_PHONE: ViewportSize = {"width": 390, "height": 844}
_FINGER_PX: int = 32
# A goal long enough to push the time of its chat to the edge of a row.
_GOAL: str = (
    "Find the cheapest flight to Berlin for Friday morning, check each airline's baggage rules and write it all down"
)
_FORGOTTEN: str = "The old router's password was on the fridge."
# Names of one to three words, three of them a plugin from its maker (the rosette after it).
_NAMES: tuple[tuple[str, bool], ...] = (
    ("Creative Design", True),
    ("Internal Comms", True),
    ("MCP Builder", True),
    ("Web App Builder", False),
    ("Superpowers", False),
)
_RUN_SCRIPT: list[ScriptedTurn] = [
    ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(tool="write_file", arguments={"path": "flights.md", "content": "# Flights\n\n- Pegasus\n"}),
        ),
    ),
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "It is written.", "success": True}),)
    ),
]


def _gallery() -> tuple[GalleryEntry, ...]:
    return tuple(
        GalleryEntry(
            id=name.lower().replace(" ", "-"),
            name=name,
            publisher="Anthropic" if official else "Acme",
            official=official,
            category="development",
            does=f"What the {name} plugin does, in a line or two of words.",
            repository="acme/skills",
            commit="d" * 40,
            skills=("skills/one",),
            files="0" * 64,
            licence="MIT",
            featured=index == 0,
            stars=100 - index,
            added=date(2026, 10, 1),
        )
        for index, (name, official) in enumerate(_NAMES)
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n[recap]\nenabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_gallery, "GALLERY", _gallery())
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _RUN_SCRIPT))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[str, str]]:
    """The daemon's address and the id of a run that wrote a file, with a version of the harness saved."""
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
        address: str = f"http://127.0.0.1:{port}"
        try:
            run: RunRecord = await manager.start(
                _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
            )
            async with asyncio.timeout(_RUN_WAIT_S):
                ended: RunRecord = await manager.wait(run.id)
            assert ended.status == "done", ended.status
            async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
                saved: httpx.Response = await client.post(
                    "/api/harness",
                    json={"habits": "- Read a file whole.", "tool_guidance": "", "summary": "Reads", "adopt": False},
                )
                assert saved.status_code == 200, saved.text
                # A note forgotten, which waits in the trash for the owner's Restore or Delete for good.
                note: httpx.Response = await client.post(
                    "/api/memories", json={"content": _FORGOTTEN, "tags": ["home"]}
                )
                assert note.status_code == 201, note.text
                forgotten: httpx.Response = await client.delete(f"/api/memories/{note.json()['record']['id']}")
                assert forgotten.status_code == 200, forgotten.text
                # A ready team of the owner's (item 610), which its card among the ready teams then says.
                teams: httpx.Response = await client.get("/api/agent-teams")
                assert teams.status_code == 200 and teams.json(), teams.text
                used: httpx.Response = await client.post(f"/api/agent-teams/{teams.json()[0]['id']}/use")
                assert used.status_code == 201, used.text
            yield address, run.id
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.webkit.launch(headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _phone(browser: Browser, address: str, route: str, inset: int = 0) -> Page:
    """A phone's window (a touch screen) on `route` of the daemon at `address`, signed in; with `inset`, the top safe
    area of the app added to an iPhone's home screen, which a browser of the tests cannot have otherwise: the page's own
    style sheet gives it as the page's variable, as the real one is there from the first paint."""
    page: Page = await (
        await browser.new_context(viewport=_PHONE, is_mobile=True, has_touch=True, reduced_motion="reduce")
    ).new_page()
    if inset:

        async def with_inset(served: Route) -> None:
            sheet = await served.fetch()
            body: str = await sheet.text()
            await served.fulfill(response=sheet, body=f"{body}\n:root {{ --safe-top: {inset}px !important; }}\n")

        await page.route("**/css/app.css*", with_inset)
    await show_choices(page)
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#{route}")
    await page.wait_for_selector("main", timeout=20_000)
    return page


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


async def _cut_by_ellipsis(names: Locator) -> list[str]:
    """The names an ellipsis cuts: those whose picture is not the picture of the same name with nothing to clip it."""
    cut: list[str] = []
    for index in range(await names.count()):
        name: Locator = names.nth(index)
        await name.scroll_into_view_if_needed()
        drawn: bytes = await name.screenshot(animations="disabled")
        await name.evaluate("(node) => { node.style.overflow = 'visible'; node.style.textOverflow = 'clip'; }")
        whole: bytes = await name.screenshot(animations="disabled")
        await name.evaluate("(node) => { node.style.overflow = ''; node.style.textOverflow = ''; }")
        if drawn != whole:
            cut.append(await name.inner_text())
    return cut


# The lines a text of the page is drawn in: a text in two lines has two boxes, or more.
_LINES: str = """(node) => {
  const range = document.createRange();
  range.selectNodeContents(node);
  return new Set([...range.getClientRects()].map((box) => Math.round(box.top))).size;
}"""


async def _broken(page: Page, selector: str) -> list[str]:
    """The texts matching `selector` that are drawn in more than one line."""
    broken: list[str] = []
    things: Locator = page.locator(selector)
    assert await things.count() > 0, f"nothing matches {selector}"
    for index in range(await things.count()):
        if await things.nth(index).evaluate(_LINES) > 1:
            broken.append(await things.nth(index).inner_text())
    return broken


async def test_a_plugin_name_with_room_to_spare_is_not_cut_by_an_ellipsis(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/customize/plugins")
    try:
        names: Locator = page.locator(".gallery-card-name")
        # The featured plugin has its card twice, once in New & noteworthy and once in the list.
        await expect(names).to_have_count(len(_NAMES) + 1)
        await page.evaluate("() => document.fonts.ready")
        assert await _cut_by_ellipsis(names) == []
    finally:
        await page.context.close()


async def test_a_sentence_with_a_button_beside_it_keeps_the_width_of_the_page(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/settings/general")
    try:
        card: Locator = page.locator(".card-pad.row", has_text="This browser is signed in to Dot")
        await expect(card).to_be_visible()
        words, button = await _box(card.locator(".grow")), await _box(card.get_by_role("button"))
        card_box: FloatRect = await _box(card)
        # The sentence has the card's width, and the button stands under it, not beside it.
        assert words["width"] > card_box["width"] * 0.8, (words, card_box)
        assert button["y"] >= words["y"] + words["height"] - 1, (words, button)
        await page.goto(f"{address}/#/settings/appearance")
        note: Locator = page.locator(".setting .row", has_text="These two follow")
        await expect(note).to_be_visible()
        words, button = await _box(note.locator(".hint")), await _box(note.get_by_role("button"))
        note_box: FloatRect = await _box(note)
        assert words["width"] > note_box["width"] * 0.8, (words, note_box)
        assert button["y"] >= words["y"] + words["height"] - 1, (words, button)
    finally:
        await page.context.close()


async def _too_small(page: Page, selector: str) -> list[str]:
    """What matches `selector`, in sight, and is under a finger's size in either measure."""
    small: list[str] = []
    things: Locator = page.locator(selector)
    assert await things.count() > 0, f"nothing matches {selector}"
    for index in range(await things.count()):
        box = await things.nth(index).bounding_box()
        if box is not None and min(box["width"], box["height"]) < _FINGER_PX:
            small.append(f"{selector} #{index}: {box['width']:.0f} x {box['height']:.0f}")
    return small


async def test_what_is_tapped_on_a_phone_is_a_fingers_size(browser: Browser, served: tuple[str, str]) -> None:
    address, run_id = served
    small: list[str] = []
    # The tabs of the run's details, and the files the run changed.
    page: Page = await _phone(browser, address, f"/runs/{run_id}")
    try:
        await expect(page.locator(".run-changes-file")).to_be_visible(timeout=15_000)
        small += await _too_small(page, ".run-changes-file")
        await page.get_by_role("button", name="Task details").click()
        await expect(page.locator(".inspector[data-mode='drawer']")).to_have_attribute("data-open", "true")
        small += await _too_small(page, ".inspector-tab")
    finally:
        await page.context.close()
    # The list of models the composer's model opens: a star for each and the word that changes the default.
    page = await _phone(browser, address, f"/runs/{run_id}")
    try:
        await page.locator(".dock .composer-pill", has_text="scripted").click()
        await expect(page.locator(".model-star").first).to_be_visible(timeout=15_000)
        small += await _too_small(page, ".model-star")
        small += await _too_small(page, ".model-change")
    finally:
        await page.context.close()
    # The link from an approval to a rule is drawn on the page of the controls, as it stands on a card.
    for route, selector in (
        ("/controls", ".approval-rule"),
        ("/settings/harness", ".harness-write"),
        ("/settings/harness", ".version-toggle"),
        ("/licences", ".notice-head"),
    ):
        page = await _phone(browser, address, route)
        try:
            await expect(page.locator(selector).first).to_be_visible(timeout=15_000)
            small += await _too_small(page, selector)
        finally:
            await page.context.close()
    assert small == [], "\n".join(small)


async def test_how_long_ago_a_chat_ended_stays_in_one_line_beside_a_long_goal(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/customize/skills")
    try:
        await page.get_by_role("button", name="Save from a chat").click()
        await expect(page.locator(".chat-skill-row").first).to_be_visible()
        assert await _broken(page, ".chat-skill-row > .hint") == []
    finally:
        await page.context.close()


# The words of each line a text is drawn in, a character at a time: a text in two lines has two of them.
_LINE_WORDS: str = """(node) => {
  const lines = new Map();
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text !== null; text = walker.nextNode()) {
    for (let index = 0; index < text.length; index += 1) {
      const range = document.createRange();
      range.setStart(text, index);
      range.setEnd(text, index + 1);
      const top = Math.round(range.getBoundingClientRect().top);
      lines.set(top, (lines.get(top) ?? '') + text.data[index]);
    }
  }
  return [...lines.entries()].sort((a, b) => a[0] - b[0]).map(([, words]) => words);
}"""


async def test_a_version_in_the_list_of_packages_breaks_after_a_dot_and_nowhere_else(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/licences")
    try:
        await expect(page.locator("tr[data-package]").first).to_be_visible(timeout=15_000)
        versions: Locator = page.locator("tr[data-package] td:nth-child(2)")
        misbroken: list[str] = []
        for index in range(await versions.count()):
            lines: list[str] = await versions.nth(index).evaluate(_LINE_WORDS)
            if any(not line.endswith(".") for line in lines[:-1]):
                misbroken.append(" / ".join(lines))
        assert misbroken == []
    finally:
        await page.context.close()


async def test_a_file_opened_beside_a_run_keeps_the_room_for_its_name(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, run_id = served
    page: Page = await _phone(browser, address, f"/runs/{run_id}")
    try:
        await page.locator(".run-changes-file").click(timeout=15_000)
        head: Locator = page.locator(".file-view .file-head")
        await expect(head.locator(".file-name")).to_have_text("flights.md")
        await expect(head.locator(".file-heading > span")).to_contain_text("Text, ")
        # The name and the facts under it are whole, and the button that opens the file on the Mac is its icon alone.
        cut: list[bool] = await head.locator(".file-heading > *").evaluate_all(
            "(nodes) => nodes.map((node) => node.scrollWidth > node.clientWidth)"
        )
        assert cut == [False, False], cut
        opener: Locator = head.get_by_role("button", name="Open on the Mac")
        assert (await _box(opener))["width"] <= 44
    finally:
        await page.context.close()


async def test_the_two_buttons_of_a_services_key_dialog_do_not_touch(
    browser: Browser, served: tuple[str, str], mac_at_rest: None
) -> None:
    """Add and Cancel of a service's key form touched when the phones were seen (item 606); item 615 made the form a
    dialog, whose foot keeps its buttons a gap apart, and this guards it."""
    address, _ = served
    page: Page = await _phone(browser, address, "/customize/connectors/github")
    try:
        await page.locator("button", has_text="Add").first.click(timeout=15_000)
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_be_visible()
        cancel: FloatRect = await _box(dialog.get_by_role("button", name="Cancel"))
        others: list[FloatRect] = [
            await _box(button)
            for button in await dialog.locator("button:not(.modal-close)").all()
            if await button.is_visible() and (await button.inner_text()).strip() not in {"", "Cancel"}
        ]
        beside: list[FloatRect] = [box for box in others if abs(box["y"] - cancel["y"]) < 4]
        assert beside, others
        for box in beside:
            gap: float = max(box["x"] - (cancel["x"] + cancel["width"]), cancel["x"] - (box["x"] + box["width"]))
            assert gap >= 4, (cancel, box)
    finally:
        await page.context.close()


async def test_the_buttons_of_a_forgotten_note_stand_under_its_words(browser: Browser, served: tuple[str, str]) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/settings/memory")
    try:
        row: Locator = page.locator(".note-row[data-decided]", has_text=_FORGOTTEN)
        await expect(row).to_be_visible(timeout=15_000)
        words, buttons, whole = (
            await _box(row.locator(".note-main")),
            await _box(row.locator(".note-decide")),
            await _box(row),
        )
        assert words["width"] > whole["width"] * 0.8, (words, whole)
        assert buttons["y"] >= words["y"] + words["height"] - 1, (words, buttons)
        await expect(row.get_by_role("button", name="Restore")).to_be_visible()
    finally:
        await page.context.close()


_TOAST: str = """async () => {
  const { toast } = await import('/js/ui.js');
  toast('ok', 'Opened flights.md on the Mac');
  toast('error', 'Could not open the file: the Mac said no, and the reason is a longer sentence that wraps');
}"""


async def test_a_toast_stands_under_the_bar_and_never_over_the_composer(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, run_id = served
    faults: list[str] = []
    # Also as the app added to an iPhone's home screen, whose bar the status bar's safe area makes taller (the review of
    # item 606): the bar's measured height holds it once, and the toasts stand right under it.
    for route, inset in (("/runs", 0), (f"/runs/{run_id}", 0), ("/runs", 50), (f"/runs/{run_id}", 50)):
        page: Page = await _phone(browser, address, route, inset)
        try:
            await expect(page.locator(".composer")).to_be_visible(timeout=15_000)
            await page.evaluate(_TOAST)
            await expect(page.locator(".toast")).to_have_count(2)
            region, bar, composer = (
                await _box(page.locator(".toast-region")),
                await _box(page.locator("header.stagebar")),
                await _box(page.locator(".composer")),
            )
            if region["y"] < bar["y"] + bar["height"] - 1:
                faults.append(f"{route} (inset {inset}): the toasts cover the bar: {region} {bar}")
            if region["y"] > bar["y"] + bar["height"] + 16:
                faults.append(f"{route} (inset {inset}): the toasts stand far under the bar: {region} {bar}")
            if (
                region["y"] < composer["y"] + composer["height"]
                and composer["y"] < region["y"] + region["height"]
                and region["x"] < composer["x"] + composer["width"]
                and composer["x"] < region["x"] + region["width"]
            ):
                faults.append(f"{route}: the toasts cover the composer: {region} {composer}")
            if region["x"] < 0 or region["x"] + region["width"] > _PHONE["width"]:
                faults.append(f"{route}: the toasts leave the screen: {region}")
        finally:
            await page.context.close()
    assert faults == [], "\n".join(faults)


# How far a page reaches past the screen's edge: none, but the rounding of a fraction.
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
_WIDTHS: tuple[int, ...] = (320, 360, 390, 430)


async def test_the_pages_that_were_mended_do_not_slide_sideways_at_any_phones_width(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, run_id = served
    slid: list[str] = []
    for width in _WIDTHS:
        for route in (
            "/settings/general",
            "/settings/appearance",
            "/settings/memory",
            "/settings/harness",
            "/customize/plugins",
            "/customize/skills",
            "/licences",
            "/controls",
            f"/runs/{run_id}",
        ):
            page: Page = await _phone(browser, address, route)
            try:
                await page.set_viewport_size({"width": width, "height": _PHONE["height"]})
                await page.wait_for_timeout(300)
                over: float = await page.evaluate(_OVERFLOW)
                if over > 1:
                    slid.append(f"{route} at {width}: {over} px past the screen's edge")
            finally:
                await page.context.close()
    assert slid == [], "\n".join(slid)


async def test_a_ready_team_the_owner_has_keeps_the_room_for_its_words(
    browser: Browser, served: tuple[str, str]
) -> None:
    address, _ = served
    page: Page = await _phone(browser, address, "/agents")
    try:
        card: Locator = page.locator(".team-card:has(.team-added)")
        await expect(card.first).to_be_visible(timeout=15_000)
        words, whole = await _box(card.first.locator(".template-role")), await _box(card.first)
        badge: FloatRect = await _box(card.first.locator(".team-added"))
        # The description has most of the card's width, and the badge that says the team is the owner's stands under it.
        assert words["width"] > whole["width"] * 0.6, (words, whole)
        assert badge["y"] >= words["y"] + words["height"] - 1, (words, badge)
    finally:
        await page.context.close()
