"""The pages a turn read on the web, under its answer (item 1184), in a real Chrome and WebKit on a daemon of its own:
the scripted model fetches two pages of a site on this Mac and answers, and nothing reaches the network.

- **Folded, one quiet line**: the first letter of each site and how many pages, under the answer and over its foot.
- **Open, each page**: its title (the page's own) over its site, a link that opens the page in a tab of its own.
- **On the run's page and as an earlier turn**: the row is drawn from the run's stream as it ends and from its stored
  events when a later message's page shows it, the same.
- **An answer that read nothing has no row**, and the row makes nothing wider than a phone.
"""

from collections.abc import Iterator

import pytest
import pytest_asyncio
import test_chat_calm
from playwright.async_api import Browser, Locator, async_playwright, expect
from test_chat_calm import _OWNERS_PHONE, TOKEN, _daemon, _done

from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home, site_config
from dotagent.evals.site import serve_site
from dotagent.evals.types import ScriptedCall, ScriptedTurn, SitePage
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PAGES: tuple[SitePage, ...] = (
    SitePage(
        path="/cloud",
        status=200,
        body="<html><head><title>Cloud servers and pricing</title></head><body><p>CX22: 4.51</p></body></html>",
        location=None,
    ),
    SitePage(
        path="/pricing",
        status=200,
        body="<html><head><title>Pricing of the platform</title></head><body><p>shared: 5.70</p></body></html>",
        location=None,
    ),
)
_ANSWER: str = "Hetzner is cheaper for a small app: 4.51 a month against 5.70."
_FOLLOW: str = "Then write the deploy script."


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The pages, served on this Mac, and the daemon's web tools allowed to reach them."""
    defaults: AppConfig = site_config(load_default_config())
    monkeypatch.setattr(test_chat_calm, "load_default_config", lambda: defaults)
    with serve_site(_PAGES) as address:
        yield address.replace("127.0.0.1", "localhost")


def _turns(site: str) -> list[ScriptedTurn]:
    fetch = [ScriptedCall(tool="web_fetch", arguments={"url": f"{site}{page.path}"}) for page in _PAGES]
    return [
        ScriptedTurn(text="", calls=tuple(fetch)),
        ScriptedTurn(text=_ANSWER, calls=()),
        ScriptedTurn(text="Done: deploy.sh is written.", calls=()),
    ]


async def _chat(manager: test_chat_calm.RunManager, goal: str, thread: str | None) -> RunRecord:
    return await manager.start(
        goal, "chat", SCRIPTED_MODEL, thread, "test", None, None, "standard", None, "yolo", False, ()
    )


async def _check_row(answer: Locator) -> None:
    """The row under `answer`: folded, the letter of the site and two pages; open, their titles and site, links out."""
    row: Locator = answer.locator(":scope > .answer-sources")
    await expect(row).to_have_count(1)
    head: Locator = row.locator(".sources-head")
    await expect(head.locator(".sources-count")).to_have_text("2 sources")
    await expect(head.locator(".source-mark")).to_have_text(["L"])
    # Over the foot, under the words.
    assert await row.evaluate("node => node.nextElementSibling.classList.contains('answer-foot')")
    links: Locator = row.locator(".source-link")
    await expect(links.first).to_be_hidden()
    await head.click()
    # Calls made together start in any order, and the row lists the pages as they started.
    titles: list[str] = await row.locator(".source-title").all_inner_texts()
    assert sorted(titles) == ["Cloud servers and pricing", "Pricing of the platform"], titles
    await expect(row.locator(".source-host")).to_have_text(["localhost", "localhost"])
    for link in await links.all():
        assert await link.get_attribute("target") == "_blank"
        assert "noopener" in (await link.get_attribute("rel") or "")
    box = await head.bounding_box()
    assert box is not None and box["height"] >= 28, box


async def test_an_answer_lists_the_pages_its_turn_read(login_env: dict[str, str], site: str) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, _turns(site)) as (runtime, manager, address), async_playwright() as playwright:
            first: RunRecord = await _done(runtime, await _chat(manager, "Hetzner or Fly.io?", None))
            second: RunRecord = await _done(runtime, await _chat(manager, _FOLLOW, first.thread_id))
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for phone in (False, True):
                    options = (
                        {**playwright.devices[_OWNERS_PHONE], "color_scheme": "light", "reduced_motion": "reduce"}
                        if phone
                        else {"viewport": {"width": 1280, "height": 900}, "reduced_motion": "reduce"}
                    )
                    context = await (webkit if phone else chromium).new_context(**options)
                    page = await context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                    await page.goto(ui_url(host, port, TOKEN))
                    # The run's own page: the row drawn from its stream.
                    await page.goto(f"{address}/#/runs/{first.id}")
                    await _check_row(page.locator("article.answer").last)
                    # The later message's page: the first turn drawn from its stored events, its row the same, and the
                    # answer that read nothing has none.
                    await page.goto(f"{address}/#/runs/{second.id}")
                    answers: Locator = page.locator("article.answer")
                    await expect(answers).to_have_count(2)
                    await expect(answers.last).to_contain_text("deploy.sh")
                    await answers.first.scroll_into_view_if_needed()
                    await _check_row(answers.first)
                    await expect(answers.last.locator(".answer-sources")).to_have_count(0)
                    assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth")
                    assert errors == [], errors
                    await context.close()
            finally:
                await chromium.close()
                await webkit.close()
