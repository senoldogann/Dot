"""The chat's settings in the browser tests (item 2015): the composer's one choice in its bar is the model's pill, which
opens the "Chat settings" sheet on every width, as DotNext's chip does. The sheet holds the models, then the Options
(Reasoning, Mode, Approvals: rows that open in place) and How Dot works; a pick keeps it open, Done or Escape closes it.
"""

from playwright.async_api import Locator, Page, expect

SHEET_NAME: str = "Chat settings"


def settings(page: Page) -> Locator:
    """The chat's settings sheet."""
    return page.get_by_role("dialog", name=SHEET_NAME)


async def open_settings(scope: Page | Locator, page: Page) -> Locator:
    """Open the chat's settings from the model's pill in `scope` (the page, or a dock); returns the sheet."""
    await scope.locator(".composer-pill[data-pill='model']").first.click()
    sheet: Locator = settings(page)
    await expect(sheet).to_be_visible()
    return sheet


async def open_row(sheet: Locator, row: str) -> Locator:
    """Open the Options row named `row` ("Reasoning", "Mode", "Approvals") in place; returns its body."""
    disclosure: Locator = sheet.locator("details.sheet-disclosure", has=sheet.page.locator("summary", has_text=row))
    if await disclosure.get_attribute("open") is None:
        await disclosure.locator("summary").click()
    body: Locator = disclosure.locator(".sheet-disclosure-body")
    await expect(body).to_be_visible()
    return body


async def close_settings(page: Page) -> None:
    """Put the sheet away with its Done."""
    await settings(page).get_by_role("button", name="Done").click()
    await expect(settings(page)).to_be_hidden()


async def pick_model(scope: Page | Locator, page: Page, name: str) -> None:
    """Pick the model named `name` in the chat's settings and close them."""
    sheet: Locator = await open_settings(scope, page)
    await sheet.get_by_role("option", name=name).first.click()
    await close_settings(page)


async def pick_option(scope: Page | Locator, page: Page, row: str, name: str) -> None:
    """Pick `name` under the Options row `row` (Reasoning's level, a Mode) and close the settings."""
    sheet: Locator = await open_settings(scope, page)
    body: Locator = await open_row(sheet, row)
    await body.get_by_role("option", name=name).first.click()
    await close_settings(page)


# The pills a test once opened and the rows of the chat's settings that hold their choices now.
_ROWS: dict[str, str] = {"reasoning": "Reasoning", "mode": "Mode", "options": "Approvals"}
# The menus a test once read and what holds their choices now: the sheet, or one of its rows.
_MENU_ROWS: dict[str, str] = {"Reasoning": "Reasoning", "Mode": "Mode"}


async def open_for(scope: Page | Locator, page: Page, pill: str) -> None:
    """Open what a pill's menu held: the chat's settings for the model, and in them the row of the reasoning, the mode
    or the approvals."""
    sheet: Locator = settings(page)
    if not await sheet.is_visible():
        await open_settings(scope, page)
    row: str | None = _ROWS.get(pill)
    if row is not None:
        await open_row(sheet, row)


def menu(page: Page, label: str) -> Locator:
    """What holds the choices a menu named `label` held ("Model", "Options", "Reasoning", "Mode")."""
    sheet: Locator = settings(page)
    row: str | None = _MENU_ROWS.get(label)
    if row is None:
        return sheet
    return sheet.locator("details.sheet-disclosure", has=page.locator("summary", has_text=row)).locator(
        ".sheet-disclosure-body"
    )
