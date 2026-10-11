"""web_fetch against a local site (item 56): a page that is not there is reported with the links of the host's front
page that share a word with the address asked for, so the next turn opens the right page instead of guessing another.
The site is served the way an eval attempt serves its own; the web client is allowed the loopback address."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.site import serve_site
from dotagent.evals.types import SitePage
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PAGES: tuple[SitePage, ...] = (
    SitePage(
        path="/",
        status=200,
        body='<!doctype html><title>Acme</title><a href="/about/">About</a> <a href="/company/open-roles/">Roles</a> '
        '<a href="/blog/careers-day/">Careers day</a> <a href="https://elsewhere.example/careers/">Partner</a>',
        location=None,
    ),
    SitePage(path="/about/", status=200, body="<!doctype html><title>About</title><p>About Acme.</p>", location=None),
)


class _Gate:
    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    with serve_site(_PAGES) as address:
        yield address


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[web]\nallow_private_hosts = true\n\n[browser]\nheadless = true\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(home / "chrome"))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _fetch(runtime: Runtime, url: str) -> ToolResult:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="fetch-test",
        thread_id="fetch-test",
        mode="research",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=12000,
        facts=new_facts,
    )
    arguments: JsonObject = {"url": url}
    prepared = await runtime.registry.tools["web_fetch"].prepare(arguments, ctx)
    return await prepared.run(ctx)


async def test_a_page_that_is_not_there_is_reported_with_the_front_pages_likely_links(
    runtime: Runtime, site: str
) -> None:
    missing: ToolResult = await _fetch(runtime, f"{site}/careers/")
    assert missing.is_error
    assert missing.text.startswith(f"web_fetch got HTTP 404 from {site}/careers/.\nThe page is not there.")
    # The blog post shares the word; the partner's page is on another host; About shares nothing.
    assert f"- {site}/blog/careers-day/" in missing.text
    assert "elsewhere.example" not in missing.text and "/about/" not in missing.text.split("<web_content", 1)[0]
    unrelated: ToolResult = await _fetch(runtime, f"{site}/pricing/")
    assert unrelated.is_error and "links to nothing that shares a word" in unrelated.text
    there: ToolResult = await _fetch(runtime, f"{site}/about/")
    assert not there.is_error and "About Acme." in there.text
