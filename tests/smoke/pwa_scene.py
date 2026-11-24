"""What the browser tests of the web app on a bad link share (items 1520 to 1524): a phone as WebKit draws the owner's
iPhone, a page that has the service worker and a copy of the shell, and the reads of the page's own data cache. The
daemon is a scene of ``dock_scenario.py`` (a chat whose run is done), served for real in a throw-away home.

``context.set_offline(True)`` is the phone with no link at all; a daemon that stops serving is the phone with a link and
no Mac; a route that answers late or aborts is the bad link in between.
"""

import asyncio
import time
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright

from dotagent.server.links import ui_url

OWNERS_PHONE: str = "iPhone 17 Pro Max"
# The longest the page's caches may take to hold what a test waits for.
UNTIL_S: float = 20.0


async def phone(
    browser: Browser,
    playwright: Playwright,
    width: int = 390,
    height: int = 844,
    scheme: str = "light",
    workers: str = "allow",
) -> BrowserContext:
    """The owner's phone, at ``width`` by ``height``, in ``scheme``: WebKit's iPhone, or, for a Chromium ``browser``,
    the same screen with a touch and a phone's scale (WebKit cannot be sent offline: Playwright's offline emulation
    fails every navigation of it, a service worker's copy included). ``workers="block"`` where a test answers for the
    daemon itself with routes, which the service worker would not let the page's requests reach."""
    if browser.browser_type.name == "webkit":
        options: dict[str, Any] = dict(playwright.devices[OWNERS_PHONE])
    else:
        options = {"has_touch": True, "is_mobile": True, "device_scale_factor": 3}
    context: BrowserContext = await browser.new_context(
        **{
            **options,
            "viewport": {"width": width, "height": height},
            "color_scheme": scheme,
            "reduced_motion": "reduce",
            "service_workers": workers,
        }
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    return context


async def until_true(page: Page, script: str, arg: Any = None) -> None:
    """Wait until the async function ``script`` of the page, given ``arg``, gives true: ``wait_for_function`` takes the
    promise such a function returns for true at once."""
    deadline: float = time.monotonic() + UNTIL_S
    while await page.evaluate(script, arg) is not True:
        assert time.monotonic() < deadline, script
        await asyncio.sleep(0.1)


async def open_chat(context: BrowserContext, address: str, token: str, run_id: str) -> Page:
    """A page of ``context`` signed in at ``address`` and opened on the chat of ``run_id``."""
    page: Page = await context.new_page()
    port: int = int(address.rpartition(":")[2])
    await page.goto(ui_url("127.0.0.1", port, token))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


async def keep_shell(page: Page) -> None:
    """Let the service worker take the page; loaded again, the page leaves it a copy of the shell, as a phone that
    opened Dot before has one."""
    await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
    await page.reload()
    await until_true(page, "async () => (await caches.match('/js/main.js')) !== undefined")


# What the data cache holds, read the way the page keeps it: the number of chats, and of the lists.
_KEPT: str = """async () => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("dot-cache");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  const count = (store) => new Promise((resolve) => {
    const request = db.transaction(store).objectStore(store).count();
    request.onsuccess = () => resolve(request.result);
  });
  const result = { chats: await count("chats"), lists: await count("lists") };
  db.close();
  return result;
}"""


async def kept(page: Page) -> dict[str, int]:
    """How many chats and lists the page's data cache holds."""
    found: object = await page.evaluate(_KEPT)
    assert isinstance(found, dict), found
    return {str(name): int(count) for name, count in found.items()}


async def until_kept(page: Page, chats: int = 1, lists: int = 1) -> None:
    """Wait until the data cache holds at least ``chats`` chats and ``lists`` lists."""
    deadline: float = time.monotonic() + UNTIL_S
    while True:
        held: dict[str, int] = await kept(page)
        if held["chats"] >= chats and held["lists"] >= lists:
            return
        assert time.monotonic() < deadline, f"the page kept {held}"
        await asyncio.sleep(0.2)
