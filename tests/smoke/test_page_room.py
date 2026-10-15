"""The text of a page in a small model window, on a real headless Chrome: the browser's tools shorten the page to what
one tool result of the run may carry and never leave it out. No model is called.
"""

import asyncio
import re
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.scaffold import output_budget
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

GOAL: str = "Read the quarterly report."
# The budget of a run whose model holds 32 768 tokens: three quarters of its window.
SMALL_BUDGET: int = 24_576
PARAGRAPHS: int = 80
_PAGE: bytes = (
    "<!doctype html><title>Report</title><h1>Quarterly report</h1>"
    + "".join(
        f"<p>Paragraph {number} of the quarterly report: sales grew in the north and fell in the south.</p>"
        for number in range(1, PARAGRAPHS + 1)
    )
).encode()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _small_window_context(runtime: Runtime) -> ToolContext:
    """The context of a run whose tool results are as small as a 32 768-token window makes them."""
    thread: ThreadRecord = await runtime.store.create_thread("page room", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "yolo",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=output_budget(runtime.config.agent, SMALL_BUDGET),
        facts=new_facts,
    )


async def _call(ctx: ToolContext, name: str, arguments: JsonObject) -> ToolResult:
    """A call as the executor makes it: classified, authorised, then run."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[name].prepare(arguments, ctx)
    await ctx.gate.authorize(prepared.action)
    return await prepared.run(ctx)


async def test_a_page_opened_in_a_small_window_is_shortened_not_left_out(runtime: Runtime, site: str) -> None:
    ctx: ToolContext = await _small_window_context(runtime)
    assert ctx.output_chars == 3072
    opened: ToolResult = await _call(ctx, "browser_open", {"url": site})
    assert "(empty page)" not in opened.text
    assert "Paragraph 1 of the quarterly report" in opened.text
    assert f"Paragraph {PARAGRAPHS} of" not in opened.text
    assert re.search(r"… \d+ more lines not shown", opened.text), opened.text
    # The result fits what the run allows one tool result, so the executor has nothing to cut out of it.
    assert len(opened.text) <= ctx.output_chars


async def test_the_text_of_a_page_read_in_a_small_window_comes_in_pages_that_fit(runtime: Runtime, site: str) -> None:
    ctx: ToolContext = await _small_window_context(runtime)
    await _call(ctx, "browser_open", {"url": site})
    first: ToolResult = await _call(ctx, "browser_read_text", {})
    assert len(first.text) <= ctx.output_chars, first.text[:200]
    assert "Paragraph 1 of the quarterly report" in first.text
    found: re.Match[str] | None = re.search(r"next offset (\d+)", first.text)
    assert found is not None, first.text[:200]
    second: ToolResult = await _call(ctx, "browser_read_text", {"offset": int(found.group(1))})
    assert len(second.text) <= ctx.output_chars
    assert "Paragraph 1 of" not in second.text and "of the quarterly report" in second.text
