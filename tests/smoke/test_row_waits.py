"""A chat's row says what it waits on that is not the owner (item 2010, as DotNext's rows say it, item 1920), on a
daemon in Chrome, wide and at a phone's width, in English and Turkish. A run hands two jobs to two helpers that each
wait in a shell command, and waits on them: its row in the sidebar says "Waiting on 2 helpers…" ("2 yardımcıyı
bekliyor…") in its last line, where its last words stood, shimmering as the work does, while its right still says
Working and for how long, and no count of the helpers stands beside the words that say it. Once one helper is stopped,
the open page's row says "Waiting on 1 helper…" without a reload.

No network and no model service is used: the scripted model plays the run and its helpers.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from test_subagent_rows import TOKEN, _both_working, _daemon

from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_a_row_says_its_run_waits_on_its_helpers(login_env: dict[str, str], tmp_path: Path) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            parent: RunRecord = await manager.start(
                "Ship the dictation work",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            helpers: list[RunRecord] = await _both_working(runtime, parent.id)
            port: int = int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width, language, words in (
                    (390, "tr", "2 yardımcıyı bekliyor…"),
                    (1440, "en", "Waiting on 2 helpers…"),
                ):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.add_init_script(f"localStorage.setItem('dot.uiLanguage', '{language}')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    if width < 860:
                        # On a phone the sidebar is a drawer, opened by the button at the top left.
                        await page.locator(".stagebar-left button").first.click()
                    row: Locator = page.locator(f'.side-run[href="#/runs/{parent.id}"]').first
                    await expect(row.locator(".side-chat-live")).to_have_text(words, timeout=15_000)
                    await expect(row.locator(".side-working")).to_be_visible()
                    # The words say how many: no count of the helpers beside them.
                    await expect(row.locator(".side-helpers")).to_have_count(0)
                    await page.screenshot(path=str(tmp_path / f"row-waits-{width}.png"))
                    if width > 860:
                        # One helper stopped: the open page's row says so without a reload, the run still waiting on
                        # the other.
                        manager.cancel(helpers[0].id)
                        await expect(row.locator(".side-chat-live")).to_have_text(
                            "Waiting on 1 helper…", timeout=15_000
                        )
                    await page.close()
            finally:
                await browser.close()
