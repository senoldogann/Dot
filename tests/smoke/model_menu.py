"""A model box of Settings › Models (item 1044): it opens the composer's model menu, the favourites and the providers on
a rail, the models of the provider pressed. The browser tests read and choose through it, as a reader does."""

from playwright.async_api import Locator, Page, expect


def model_box(page: Page, label: str) -> Locator:
    """The box of the role `label` ("Fast model"), whose name says the model chosen after the role's."""
    return page.get_by_role("button", name=f"{label}:")


def open_menu(page: Page) -> Locator:
    """The model menu open now."""
    return page.locator(".composer-popover:has(> .model-picker):not([hidden])")


async def open_box(page: Page, label: str) -> Locator:
    """Open the box of the role `label`; returns its menu."""
    await model_box(page, label).click()
    menu: Locator = open_menu(page)
    await expect(menu).to_be_visible()
    return menu


async def close_box(page: Page) -> None:
    """Close the menu open, with Escape."""
    await page.keyboard.press("Escape")
    await expect(open_menu(page)).to_have_count(0)


async def offered(page: Page, label: str) -> dict[str, list[str]]:
    """What the box of the role `label` offers, by provider: the references of the models each provider's view lists,
    none for a provider on the rail whose models cannot play the role. The menu is closed again."""
    menu: Locator = await open_box(page, label)
    found: dict[str, list[str]] = {}
    for view in await menu.locator(".model-rail-item:not([data-view='favourites'])").all():
        provider: str | None = await view.get_attribute("data-view")
        assert provider is not None
        await view.click()
        found[provider] = [
            ref
            for ref in await menu.locator(".model-option[title]").evaluate_all("(rows) => rows.map((row) => row.title)")
        ]
    await close_box(page)
    return found


async def why_not(page: Page, label: str, provider: str) -> str:
    """What the view of `provider` says in the box of the role `label`, the menu closed again."""
    menu: Locator = await open_box(page, label)
    await menu.locator(f".model-rail-item[data-view='{provider}']").click()
    text: str = await menu.locator(".model-note").inner_text()
    await close_box(page)
    return text


async def pick(page: Page, label: str, ref: str) -> None:
    """Choose `ref` in the box of the role `label`, "" for its blank row: its provider's view pressed, then its row."""
    menu: Locator = await open_box(page, label)
    if ref == "":
        await menu.locator(".model-option").first.click()
    else:
        await menu.locator(f".model-rail-item[data-view='{ref.partition(':')[0]}']").click()
        await menu.locator(f".model-option[title='{ref}']").click()
    await expect(open_menu(page)).to_have_count(0)


async def expect_chosen(page: Page, label: str, ref: str) -> None:
    """The box of the role `label` shows `ref` chosen ("" for its blank row)."""
    await expect(model_box(page, label)).to_have_attribute("title", ref)
