"""Settings › System's facts of the daemon in their groups (item 378), Diagnostics (item 370) and Open source licences
(item 371), on a served daemon in Chrome, wide and light, on a phone and dark. Diagnostics lists the daemon's own
process and those it started, the room its files take, and a log's last lines with its secrets masked and a
filter; a name that is not a log is refused. The rows that lead to the two pages end in a chevron that points right, the
processes are counted in the singular for one, and on a phone the table of processes wears a cue that more lies to its
right (item 437). The licences list the notices that come with the pages, the Python packages
beside the daemon with a search, and the tools Dot uses. Nothing outside this Mac is reached: the logs are a folder of
the test's own."""

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "about-pages-token"
# A key of the form the masking knows, which must not reach the page.
_SECRET: str = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@pytest.fixture
def logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dot's log folder for the test, with a log whose second line holds a key."""
    folder: Path = tmp_path / "logs"
    folder.mkdir()
    monkeypatch.setenv("DOT_LOG_DIR", str(folder))
    (folder / "dotx.log").write_text(
        f"first line\nAuthorization: Bearer {_SECRET}\nthe daemon started\n", encoding="utf-8"
    )
    return folder


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, str]]:
    """A daemon in the home of the environment, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        manager: RunManager = RunManager(runtime)
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


def _fold(page: Page, title: str) -> Locator:
    """The folding card of the System page whose title is ``title`` (item 411)."""
    return page.locator("section.card.fold", has=page.locator(".fold-title", has_text=title))


async def test_diagnostics_and_the_licences_show_what_is_there(
    login_env: dict[str, str], logs: Path, tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, address), async_playwright() as playwright:
            async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
                processes = (await client.get("/api/diagnostics/processes")).json()
                assert processes["daemon_pid"] == os.getpid() == processes["processes"][0]["pid"]
                assert processes["processes"][0]["depth"] == 0
                tail = (await client.get("/api/diagnostics/logs/dotx.log", params={"lines": 10})).json()
                assert tail["lines"][0] == "first line" and tail["lines"][-1] == "the daemon started"
                assert _SECRET not in "\n".join(tail["lines"])
                refused = await client.get("/api/diagnostics/logs/..%2Fdotx.log", params={"lines": 10})
                # The guard refuses a path with a dot segment before any route sees it (item 419).
                assert refused.status_code == 400 and "segment is refused" in refused.text, refused.text
                assert (await client.get("/api/diagnostics/logs/dotx.log", params={"lines": 0})).status_code == 422
                disk = (await client.get("/api/diagnostics/disk")).json()
                assert [folder["name"] for folder in disk["folders"]][-1] == "Dot's home in all"
                licences = (await client.get("/api/licences")).json()
                assert any(item["name"].lower() == "fastapi" and item["licence"] for item in licences["packages"])
                # The languages' logos carry Simple Icons' notice (item 547).
                assert {"Inter", "GeistMono", "Lucide", "js/appearance", "js/character", "icons/languages"} <= {
                    notice["name"] for notice in licences["bundled"]
                }
                assert [tool["name"] for tool in licences["tools"]] == [
                    "Google Chrome",
                    "Ollama",
                    "Node.js",
                    "Tailscale",
                    "OpenCode",
                ]
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/settings/system")
                    # The daemon's facts in four groups, each a heading over its rows of a label and a value.
                    groups = page.locator(".kv-group")
                    await expect(groups.get_by_role("heading")).to_have_text(
                        ["This Dot", "How it acts", "What it works with", "Models"]
                    )
                    await expect(groups.first.locator("dt")).to_have_text(
                        ["Version", "Process id", "Uptime", "Started", "Address", "Home"]
                    )

                    # Each section folds and stays as it was left, in this browser (item 411); the Permissions card says
                    # in its summary what is missing, and no part of it says "null", as an empty note did.
                    await expect(_fold(page, "Permissions").locator(".fold-summary .badge")).to_have_text(
                        re.compile(r"^(all granted|missing: .+)$")
                    )
                    await expect(_fold(page, "Permissions").locator(".fold-body")).not_to_contain_text("null")
                    for title in ("Daemon", "Emergency stop", "Health checks", "About this Dot"):
                        await expect(_fold(page, title)).to_have_count(1)
                    await _fold(page, "Daemon").locator(".fold-toggle").click()
                    await expect(_fold(page, "Daemon")).to_have_attribute("data-open", "false")
                    await page.reload()
                    await expect(_fold(page, "Daemon")).to_have_attribute("data-open", "false")
                    await _fold(page, "Daemon").locator(".fold-toggle").click()
                    await expect(_fold(page, "Daemon")).to_have_attribute("data-open", "true")
                    await page.screenshot(path=tmp_path / f"system-{width}-{scheme}.png", full_page=True)
                    # The rows that open the two pages end in a chevron that points right, as drawn: turned a quarter
                    # round it pointed up, as a fold does.
                    rows: Locator = page.locator(".about-row")
                    await expect(rows).to_have_count(2)
                    for row in await rows.all():
                        assert await row.locator(".icon").evaluate("(mark) => getComputedStyle(mark).rotate") == "none"
                    await expect(rows.first).to_contain_text("the room its files take, and its logs")
                    await page.get_by_role("link", name=re.compile(r"^Diagnostics")).click()
                    await expect(page).to_have_url(f"{address}/#/diagnostics")
                    await expect(page.locator(".page-sub")).to_have_text(
                        "Dot's processes and what they take, the room its files take, and its logs, read on this Mac."
                    )
                    # The processes are counted in the singular for one; the table scrolls sideways in its box on a
                    # phone, whose edge fades while more lies to the right.
                    await expect(page.locator(".card-head .small.muted").first).to_have_text(
                        re.compile(r"^\d+ process")
                    )
                    totals: str = await page.locator(".card-head .small.muted").first.inner_text()
                    counted = re.match(r"(\d+) (process|processes) · ", totals)
                    assert counted is not None, totals
                    assert (counted.group(2) == "process") == (counted.group(1) == "1"), totals
                    await expect(page.locator(".table-scroll").first).to_have_attribute(
                        "data-scroll", "more" if width < 720 else "end"
                    )
                    await expect(page.locator("tr[data-pid]").first).to_contain_text("(Dot's daemon)")
                    await expect(page.locator(".folder-row .row-title", has_text="Dot's home in all")).to_be_visible()
                    lines = page.locator(".log-lines")
                    await expect(lines).to_contain_text("the daemon started")
                    await expect(lines).not_to_contain_text(_SECRET)
                    await page.get_by_role("searchbox", name="Filter the lines").fill("first")
                    await expect(lines).to_have_text("first line")
                    await page.screenshot(path=tmp_path / f"diagnostics-{width}-{scheme}.png", full_page=True)
                    await page.goto(f"{address}/#/settings/system")
                    await page.get_by_role("link", name=re.compile(r"^Open source licences")).click()
                    await expect(page).to_have_url(f"{address}/#/licences")
                    await expect(page.locator(".notice-name", has_text="Inter")).to_be_visible()
                    await page.get_by_role("searchbox", name="Find a package").fill("fastapi")
                    await expect(page.locator("tr[data-package]:visible").first).to_contain_text("fastapi")
                    await expect(page.locator(".tool-row")).to_have_count(5)
                    await page.screenshot(path=tmp_path / f"licences-{width}-{scheme}.png", full_page=True)
                    await page.close()
            finally:
                await browser.close()
