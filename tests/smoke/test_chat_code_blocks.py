"""What a chat shows of code, lists, tables and the marks of a line (item 537), in a real Chrome on a daemon of its own,
wide and on a phone, light and dark; the scripted model plays the runs, and nothing reaches the network.

- **A block of code is a box with a head**: the language it was written for (or the title the writer gave it, or
  `text`) at its left, a switch that wraps its long lines and a copy of it at its right, in an answer, in the owner's
  message and in what a step shows (its command, its result, its error, whose copy is the whole result when the page
  shows the first part of it). Before, a block had a copy button floating over its edge and no way to wrap one block.
- **What the writer wrote is read as written**: a fence that says more than its language (` ```python title="x.py" `)
  opens its block, so the blocks after it stand as blocks (before, the one after it swallowed the next); a list nests
  as far as its items are indented, with the blocks under an item inside it, and numbers on from the number it begins
  at; a table's columns align as its rule line says; a task shows its box; strikethrough, a `<br>` and an escaped mark
  are what they say.
- **The slash menu is drawn as a menu of skills, MCP tools and commands**: sections with a hairline between them and no
  title on the page (a screen reader hears each), a line icon for each (a terminal in a box, a puzzle piece), the name
  and the words under it, and under the list what the keys do (not on a touch screen).
"""

from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect
from test_chat_calm import _OWNERS_PHONE, TOKEN, _daemon, _ended, _finish

from dotagent.config import load_default_config
from dotagent.evals.runner import eval_environment, scratch_home
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
# Every block and mark a chat draws, written the way models write them: a fence with more than its language, a list that
# nests with a block of code under an item and goes on after it, a table with aligned columns, tasks, a heading of the
# fifth level, a quote that holds a list, strikethrough and escapes.
_ANSWER: str = "\n".join(
    [
        "Here is the plan:",
        "",
        "1. Install the dependencies:",
        "   ```bash",
        "   npm install",
        "   ```",
        "2. Run the checks",
        "   - unit tests",
        "     - with coverage",
        "   - smoke tests",
        "3. Ship it.",
        "",
        "## The service",
        "",
        '```python title="service.py"',
        "def greet(name: str) -> str:",
        '    return f"Hello, {name}!"',
        "```",
        "",
        "```text",
        "Bu projede Dot Desktop'a entegre edilecek, yerel çalışan bir web araştırma ve scraping sistemi geliştir.",
        "```",
        "",
        "```",
        "no language here",
        "```",
        "",
        "> Steps:",
        "> - one",
        "> - two",
        "",
        "| Tool | Does | Notes |",
        "|:--|:-:|--:|",
        "| `web_search` | searches | provider |",
        "| `web_scrape` | scrapes a page | markdown |",
        "",
        "- [ ] a task not done",
        "- [x] a task done",
        "",
        "5. The list goes on at five",
        "6. and six",
        "",
        "##### A fifth level heading",
        "",
        "Done: ~~gone~~, 2 \\* 3, a<br>break and &lt;tag&gt;.",
    ]
)
# The head's label of each block of code above, in order.
_LABELS: list[str] = ["bash", "service.py", "text", "text"]
# The width and the height of each node, to see that the buttons of a head are as large as a thumb needs.
_SIZES: str = """nodes => nodes.map((node) => {
  const box = node.getBoundingClientRect();
  return [box.width, box.height];
})"""
# A result longer than the page shows of one: the page shows the first part, and the copy is the whole.
_LONG: str = "x" * 7000 + "END"
# A step that came back with a result, drawn by the page's own module as a run's steps are, however much the tool kept.
_LONG_STEP: str = """async (text) => {
  const { toolRow } = await import('/js/tools.js');
  const row = toolRow({ tool: 'shell', args: { command: 'cat big.txt' } });
  row.finish({ ok: true, text, summary: '', seconds: 1, images: [] });
  row.node.id = 'long-step';
  document.body.append(row.node);
  row.node.querySelector('details').open = true;
}"""
# What a skill of the owner's looks like at home, for the menu to list.
_SKILL: str = """---
name: scan-catalogue
description: "Scan a product catalogue page by page and report the items."
---
# Scan the catalogue

Read every page.
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def _browser_page(
    playwright, chromium: Browser, webkit: Browser, address: str, phone: bool, scheme: Literal["light", "dark"]
) -> tuple[BrowserContext, Page, list[str]]:
    """A page of the chat server, signed in: in Chrome as wide as a desktop, or in WebKit as an iPhone."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    options = (
        {**playwright.devices[_OWNERS_PHONE], "color_scheme": scheme, "reduced_motion": "reduce"}
        if phone
        else {
            "viewport": {"width": 1280, "height": 1000},
            "color_scheme": scheme,
            "reduced_motion": "reduce",
            "permissions": ["clipboard-read", "clipboard-write"],
        }
    )
    context: BrowserContext = await (webkit if phone else chromium).new_context(**options)
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.goto(ui_url(host, port, TOKEN))
    return context, page, errors


async def test_a_chat_draws_code_lists_and_tables_as_they_were_written(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    turns: list[ScriptedTurn] = [_finish(_ANSWER) for _ in range(4)]
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for scheme in _SCHEMES:
                    for phone in (False, True):
                        context, page, errors = await _browser_page(
                            playwright, chromium, webkit, address, phone, scheme
                        )
                        run = await _ended(runtime, manager, "Fix this:\n\n```python\nprint(1)\n```\n\nthen `run` it.")
                        await page.goto(f"{address}/#/runs/{run.id}")
                        answer: Locator = page.locator(".answer .prose").last
                        await expect(answer).to_be_visible()
                        name: str = f"{'phone' if phone else 'wide'}-{scheme}"
                        await page.screenshot(path=tmp_path / f"chat-{name}.png")
                        # The owner's message: its code in a box with a head too.
                        goal: Locator = page.locator(".bubble.user .code-block")
                        await expect(goal).to_have_count(1)
                        await expect(goal.locator(".code-label")).to_have_text("python")
                        # The blocks of code, each with its head: the fence that said more than its language did not
                        # swallow the one after it.
                        blocks: Locator = answer.locator(".code-block")
                        await expect(blocks).to_have_count(len(_LABELS))
                        await expect(blocks.locator(".code-label")).to_have_text(_LABELS)
                        languages: list[str | None] = [
                            await block.get_attribute("data-language") for block in await blocks.all()
                        ]
                        assert languages == ["bash", "python", "text", ""], languages
                        await expect(answer.locator("pre code").nth(1)).to_contain_text("def greet(name: str) -> str:")
                        # A list nests: the code under its first item is inside it, the bullets inside the second, and
                        # the numbers run 1, 2, 3 and then on from the five the writer began the next list at.
                        numbered: Locator = answer.locator("ol").first
                        await expect(numbered.locator(":scope > li")).to_have_count(3)
                        await expect(numbered.locator(":scope > li").first.locator(".code-block")).to_have_count(1)
                        await expect(numbered.locator(":scope > li").nth(1).locator("ul ul li")).to_have_text(
                            "with coverage"
                        )
                        assert await answer.locator("ol").nth(1).get_attribute("start") == "5"
                        # The quote holds its list; the table's columns align; the tasks have their boxes.
                        await expect(answer.locator("blockquote ul li")).to_have_text(["one", "two"])
                        classes: list[str | None] = [
                            await cell.get_attribute("class") for cell in await answer.locator("table thead th").all()
                        ]
                        assert classes == ["align-left", "align-center", "align-right"], classes
                        await expect(answer.locator("table tbody td.align-right").first).to_have_text("provider")
                        boxes: Locator = answer.locator("input.task-box")
                        await expect(boxes).to_have_count(2)
                        assert [await box.is_checked() for box in await boxes.all()] == [False, True]
                        assert await boxes.first.is_disabled()
                        # Marks: a heading of the fifth level, strikethrough, an escaped star, a break and an entity.
                        await expect(answer.locator("h4").last).to_have_text("A fifth level heading")
                        await expect(answer.locator("del")).to_have_text("gone")
                        last: Locator = answer.locator("p.para").last
                        await expect(last).to_contain_text("2 * 3")
                        await expect(last).to_contain_text("<tag>")
                        await expect(last.locator("br")).to_have_count(1)
                        # The code shows the letters it holds: no ligature turns `->` into an arrow.
                        ligatures: str = (
                            await blocks.nth(1)
                            .locator("pre")
                            .evaluate("node => getComputedStyle(node).fontVariantLigatures")
                        )
                        assert ligatures == "none", ligatures
                        # Nothing makes the page wider than the window.
                        wide: bool = await page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        assert not wide
                        assert errors == [], errors
                        await context.close()
            finally:
                await chromium.close()
                await webkit.close()


async def test_a_block_of_code_wraps_its_lines_when_asked_and_copies_what_it_holds(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    long_line: str = "word " * 80
    answer: str = f"```text\n{long_line.strip()}\n```\n\n```text\nshort\n```"
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env, [_finish(answer) for _ in range(2)]) as (runtime, manager, address),
            async_playwright() as playwright,
        ):
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for phone in (False, True):
                    context, page, _ = await _browser_page(playwright, chromium, webkit, address, phone, "light")
                    run = await _ended(runtime, manager, "Show me the text")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    block: Locator = page.locator(".answer .code-block").first
                    await expect(block).to_be_visible()
                    pre: Locator = block.locator("pre")
                    wrap: Locator = block.get_by_role("button", name="Wrap long lines")
                    # The lines are kept whole by default: the block scrolls sideways.
                    assert await pre.evaluate("node => getComputedStyle(node).whiteSpace") == "pre"
                    assert await pre.evaluate("node => node.scrollWidth > node.clientWidth")
                    assert await wrap.get_attribute("aria-pressed") == "false"
                    # The switch wraps this block's lines, and only this block's.
                    await wrap.click()
                    assert await wrap.get_attribute("aria-pressed") == "true"
                    assert await pre.evaluate("node => getComputedStyle(node).whiteSpace") == "pre-wrap"
                    assert not await pre.evaluate("node => node.scrollWidth > node.clientWidth")
                    other: Locator = page.locator(".answer .code-block").nth(1).locator("pre")
                    assert await other.evaluate("node => getComputedStyle(node).whiteSpace") == "pre"
                    await wrap.click()
                    assert await pre.evaluate("node => getComputedStyle(node).whiteSpace") == "pre"
                    # The buttons of the head are as large as a thumb needs on a phone, and in sight.
                    sizes = await block.locator(".code-head .btn").evaluate_all(_SIZES)
                    assert len(sizes) == 2 and all(width >= 28 and height >= 28 for width, height in sizes), sizes
                    if not phone:
                        # A copy puts the code on the clipboard as it was written.
                        await block.get_by_role("button", name="Copy the code").click()
                        assert await page.evaluate("navigator.clipboard.readText()") == long_line.strip()
                    await page.screenshot(path=tmp_path / f"wrap-{'phone' if phone else 'wide'}.png")
                    await context.close()
            finally:
                await chromium.close()
                await webkit.close()


async def test_what_a_step_shows_is_a_box_of_code_whose_copy_is_the_whole_of_it(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    def shell(command: str) -> ScriptedTurn:
        return ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": command}),))

    turns: list[ScriptedTurn] = [shell("echo hello"), shell("echo oops >&2; exit 3"), _finish("Done.")]
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                context, page, _ = await _browser_page(playwright, chromium, chromium, address, False, "dark")
                run = await _ended(runtime, manager, "Run two commands")
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".answer").last).to_be_visible()
                await page.get_by_text("Ran 2 commands").first.click()
                for head in await page.locator("details.tool > summary").all():
                    await head.click()
                labels: Locator = page.locator(".tool-body .code-label")
                await expect(labels).to_have_text(["Command", "Result", "Command", "Error"])
                await expect(page.locator(".tool-body .code-block.bad")).to_have_count(1)
                # Each box has its lines wrapped from the start, and its switch says so.
                result: Locator = page.locator(".tool-body .code-block").nth(1)
                await expect(result.locator("pre code")).to_contain_text("hello")
                wrap: Locator = result.get_by_role("button", name="Wrap long lines")
                assert await wrap.get_attribute("aria-pressed") == "true"
                # A result longer than the page shows is cut where the page says so; its copy is the whole of it, and
                # Show all puts the whole of it in the box.
                await page.evaluate(_LONG_STEP, _LONG)
                long: Locator = page.locator("#long-step .code-block").nth(1)
                await expect(long.locator(".code-label")).to_have_text("Result")
                shown: str = await long.locator("pre code").inner_text()
                assert 0 < len(shown) < len(_LONG), len(shown)
                more: Locator = page.locator("#long-step").get_by_role("button", name="Show all")
                await expect(more).to_have_text(f"Show all ({len(_LONG):,} characters)")
                await long.get_by_role("button", name="Copy the code").click()
                assert await page.evaluate("navigator.clipboard.readText()") == _LONG
                await more.click()
                await expect(more).to_have_count(0)
                assert await long.locator("pre code").inner_text() == _LONG
                await page.screenshot(path=tmp_path / "steps.png")
                await context.close()
            finally:
                await chromium.close()


async def test_the_slash_menu_is_sections_of_line_icons_with_the_keys_under_them(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        (home / "skills" / "scan-catalogue").mkdir(parents=True)
        (home / "skills" / "scan-catalogue" / "SKILL.md").write_text(_SKILL, encoding="utf-8")
        async with (
            _daemon(login_env, [_finish("ok") for _ in range(4)]) as (runtime, manager, address),
            async_playwright() as playwright,
        ):
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for scheme in _SCHEMES:
                    for phone in (False, True):
                        context, page, _ = await _browser_page(playwright, chromium, webkit, address, phone, scheme)
                        run = await _ended(runtime, manager, "Hello")
                        await page.goto(f"{address}/#/runs/{run.id}")
                        box: Locator = page.locator(".dock .composer textarea")
                        await box.click()
                        await box.fill("/")
                        menu: Locator = page.locator(".dock .slash-menu")
                        await expect(menu).to_be_visible()
                        sections: Locator = menu.locator("[role=group]")
                        # The skills come after the commands have drawn: wait for the section of each.
                        await expect(sections).to_have_count(2)
                        titles: list[str | None] = [
                            await one.get_attribute("aria-label") for one in await sections.all()
                        ]
                        assert titles[:2] == ["Commands", "Skills"], titles
                        # No title stands on the page: a hairline is all that parts the sections.
                        await expect(menu.locator(".slash-divider")).to_have_count(len(titles) - 1)
                        assert "Commands" not in await menu.inner_text()
                        # A line icon for each: a terminal in a box for a command, a puzzle piece for a skill.
                        commands: Locator = sections.first.locator(".slash-mark svg")
                        assert set(await commands.evaluate_all("nodes => nodes.map((node) => node.dataset.icon)")) == {
                            "squareTerminal",
                            "refresh",
                        }
                        skills: Locator = sections.nth(1).locator(".slash-mark svg")
                        assert set(await skills.evaluate_all("nodes => nodes.map((node) => node.dataset.icon)")) == {
                            "puzzle"
                        }
                        # Name, then its words, which are cut with an ellipsis rather than wrapped.
                        first: Locator = menu.get_by_role("option").first
                        assert (
                            await first.locator(".slash-hint").evaluate("node => getComputedStyle(node).whiteSpace")
                            == "nowrap"
                        )
                        # The keys: shown under the list on a screen with a pointer, left off a touch screen.
                        foot: Locator = menu.locator(".slash-foot")
                        await expect(foot).to_have_count(1)
                        await expect(foot).to_contain_text("Select · Enter/Tab to add · Esc to close")
                        shown: bool = await foot.is_visible()
                        assert shown == (not phone), (phone, shown)
                        await page.screenshot(path=tmp_path / f"slash-{'phone' if phone else 'wide'}-{scheme}.png")
                        # The arrows move, Escape closes.
                        await box.press("ArrowDown")
                        await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("/auto")
                        await box.press("Escape")
                        await expect(menu).to_be_hidden()
                        await context.close()
            finally:
                await chromium.close()
                await webkit.close()
