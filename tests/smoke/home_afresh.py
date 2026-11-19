"""The home goes on in Dot's conversation (item 1245): a test whose messages from the home each began a chat of their
own presses **New chat** under the box first, as the owner would, once the home has read where the box writes."""

from playwright.async_api import Locator, Page, expect


async def start_afresh(page: Page) -> None:
    """The home's next message starts a chat of its own: **New chat** pressed when there is a conversation to leave."""
    await expect(page.locator(".home-conversation[data-state='read']")).to_have_count(1)
    fresh: Locator = page.locator(".home-conversation").get_by_role("button", name="New chat")
    if await fresh.is_visible():
        await fresh.click()
