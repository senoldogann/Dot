"""What every form in a dialog does (item 615), asked the same way of each page that has one.

A form opens as a dialog over the page, from the button that asks for it, and the page behind keeps its height; the
caret is in its first field; a failure shows inside the dialog, above its foot, with what was typed still there; Escape
closes it, asking first ("Discard your changes?") when a field was changed, and gives the focus back to the button that
opened it; on a phone it is a sheet that fits the window with its foot in view. The pages' own tests call these, so that
a form moved back into the page, or a dialog whose foot goes out of sight, fails where the page's other checks would not
notice."""

import re
from collections.abc import Awaitable, Callable

from playwright.async_api import Locator, Page, expect

# The question the dialog asks before it drops what was typed.
DISCARD: str = "Discard your changes?"


async def page_height(page: Page) -> int:
    """How tall the page is, as its scroll height says: a form added inside it makes this grow."""
    return int(await page.evaluate("document.documentElement.scrollHeight"))


async def settled_height(page: Page) -> int:
    """The page's height once it holds still for a few tenths of a second: the lists a page reads after it opened move
    it, and a form does not have to."""
    last: int = await page_height(page)
    still: int = 0
    for _ in range(60):
        await page.wait_for_timeout(100)
        now: int = await page_height(page)
        still = still + 1 if now == last else 0
        last = now
        if still >= 4:
            break
    return last


async def open_form(page: Page, opener: Locator, title: str | re.Pattern[str]) -> Locator:
    """Press `opener`: the dialog named `title` opens over the page, which keeps its height, with the focus in a field
    or on a button of the dialog (never left on the page behind). Returns the dialog."""
    return await open_form_by(page, opener.click, title)


async def open_form_by(page: Page, press: Callable[[], Awaitable[None]], title: str | re.Pattern[str]) -> Locator:
    """As :func:`open_form`, the dialog opened by `press`: a choice of a row's "…" (item 848), whose menu is opened
    once the page holds still, since a scroll closes it."""
    before: int = await settled_height(page)
    await press()
    dialog: Locator = page.get_by_role("dialog", name=title)
    await expect(dialog).to_be_visible()
    assert await page_height(page) == before, "the page grew when the form opened: it stands in the page, not over it"
    focus_in: bool = await page.evaluate(
        "() => { const open = document.querySelector('dialog.form-dialog[open]');"
        " return open !== null && open.contains(document.activeElement); }"
    )
    assert focus_in, "the focus is not inside the dialog that opened"
    return dialog


async def foot_is_in_view(page: Page, dialog: Locator) -> None:
    """The dialog is inside the window, and every button of its foot is on the screen."""
    box = await dialog.bounding_box()
    size = page.viewport_size
    assert box is not None and size is not None
    assert box["x"] >= 0 and box["y"] >= 0, box
    assert box["x"] + box["width"] <= size["width"] and box["y"] + box["height"] <= size["height"], (box, size)
    foot: Locator = dialog.locator(".modal-actions")
    await expect(foot).to_be_in_viewport()
    for button in await foot.get_by_role("button").all():
        await expect(button).to_be_in_viewport()
        # The words of a button stay inside it: on the narrowest phone three buttons once shared a line in equal
        # widths, and their words ran into each other.
        over: float = await button.evaluate("(node) => node.scrollWidth - node.clientWidth")
        assert over <= 1, (await button.inner_text(), over)


async def keeps_inside_the_window(page: Page) -> None:
    """Neither the dialog nor the page behind it reaches past the window's right edge."""
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    wider: bool = await page.evaluate(
        "() => { const open = document.querySelector('dialog.form-dialog[open]');"
        " return open !== null && open.scrollWidth > open.clientWidth + 1; }"
    )
    assert not wider, "something in the dialog is wider than the dialog"


async def shows_the_failure(dialog: Locator, words: str | re.Pattern[str]) -> None:
    """The reason of a failed submit is in the dialog, above its foot, and the dialog is still open."""
    problem: Locator = dialog.locator(".form-dialog-error")
    await expect(problem).to_be_visible()
    await expect(problem).to_contain_text(words)
    await expect(dialog).to_be_visible()
    problem_box = await problem.bounding_box()
    foot_box = await dialog.locator(".modal-actions").bounding_box()
    assert problem_box is not None and foot_box is not None
    assert problem_box["y"] + problem_box["height"] <= foot_box["y"] + 1, (problem_box, foot_box)


async def closes_with_escape(page: Page, dialog: Locator, opener: Locator) -> None:
    """Escape on a form nobody changed closes it at once, and the focus goes back to the button that opened it."""
    await page.keyboard.press("Escape")
    await expect(dialog).to_have_count(0)
    await expect(opener).to_be_focused()


async def asks_before_dropping(page: Page, dialog: Locator, change: Locator, text: str) -> None:
    """Escape on a changed form asks first: Keep editing leaves it open with what was typed, Discard closes it."""
    await change.fill(text)
    await page.keyboard.press("Escape")
    # By its words, not its role: a closed dialog leaves the accessibility tree at once but the page takes it out of the
    # document a moment later, when its answer is given, and only then is Escape asked again.
    question: Locator = page.locator("dialog", has_text=DISCARD)
    await expect(question).to_be_visible()
    await expect(question.get_by_role("button", name="Keep editing")).to_be_focused()
    await question.get_by_role("button", name="Keep editing").click()
    await expect(question).to_have_count(0)
    await expect(dialog).to_be_visible()
    await expect(change).to_have_value(text)
    await page.keyboard.press("Escape")
    await expect(question).to_be_visible()
    await question.get_by_role("button", name="Discard").click()
    await expect(dialog).to_have_count(0)
