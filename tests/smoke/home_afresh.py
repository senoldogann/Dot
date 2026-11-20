"""The home goes on in Dot's conversation (item 1245) and has no New chat (item 1279): a test whose messages from the
home each began a chat of their own leaves the conversation first, as the owner could only do by deleting it."""

from playwright.async_api import Page, expect

_LEAVE: str = """async () => {
  const { api, request } = await import("/js/api.js");
  const ids = (await api.get("/agents")).map((summary) => summary.home?.id ?? null).filter((id) => id !== null);
  if (ids.length > 0) {
    await request("DELETE", "/threads", { ids });
  }
}"""


async def start_afresh(page: Page) -> None:
    """The home's next message starts a chat of its own: the conversation is deleted, once the home has read where the
    box writes. The home asks the daemon again where a message goes when it is sent, and finds none."""
    await expect(page.locator(".home-conversation[data-state='read']")).to_have_count(1)
    await page.evaluate(_LEAVE)
