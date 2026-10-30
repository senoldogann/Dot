"""A row's "…" (item 848): the rows of the pages show one action and keep the rest in a menu a press away. The browser
tests reach those actions through it, as a reader does."""

import re

from form_dialogs import open_form_by
from playwright.async_api import Locator, Page, expect


def more_of(row: Locator) -> Locator:
    """The row's "…", where the focus goes back once a choice of its menu is done."""
    return row.locator(".row-more")


async def open_more(row: Locator) -> Locator:
    """Open the row's menu, the pointer on the row first (a pointer's page shows a row's actions where it rests);
    returns the menu."""
    await row.hover()
    await more_of(row).click()
    menu: Locator = row.page.get_by_role("menu")
    await expect(menu).to_be_visible()
    return menu


async def item_of(row: Locator, name: str) -> Locator:
    """The choice `name` of the row's menu, the menu opened."""
    return (await open_more(row)).get_by_role("menuitem", name=name, exact=True)


async def choose(row: Locator, name: str) -> None:
    """Press the choice `name` of the row's menu."""
    await (await item_of(row, name)).click()


async def open_row_form(page: Page, row: Locator, name: str, title: str | re.Pattern[str]) -> Locator:
    """The dialog named `title` that the choice `name` of the row's menu opens, as :func:`form_dialogs.open_form` checks
    one: the page keeps its height and the focus is in the dialog. Returns the dialog."""
    return await open_form_by(page, lambda: choose(row, name), title)
