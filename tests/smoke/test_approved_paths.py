"""A tool that names files works on the files it was authorised for (item 181): it is classified again as it starts,
and a path that leads elsewhere by then (a link moved while the call waited for its approval) fails the call, saying
so, with nothing written or read; called again, it is authorised for where the path leads now.

No network and no model service is used: the calls are prepared, authorised and run as the executor runs them.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import ToolExecutionError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Change the notes."
MOVED: str = "what it names changed while it waited"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
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


async def _context(runtime: Runtime) -> ToolContext:
    thread: ThreadRecord = await runtime.store.create_thread("paths", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, GOAL, "task", "ollama:none", None, "test", "medium", "yolo", False, None, None, None
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
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _authorized(ctx: ToolContext, tool: str, args: JsonObject) -> PreparedCall:
    """``tool`` prepared and authorised, as the executor prepares a call before it runs it."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[tool].prepare(args, ctx)
    await ctx.gate.authorize(prepared.action)
    return prepared


def _files(name: str) -> tuple[Path, Path, Path]:
    """A file a link leads to when a call is authorised, another file, and the link."""
    folder: Path = workspace_dir() / name
    folder.mkdir(parents=True)
    authorized, other, link = folder / "authorized.txt", folder / "other.txt", folder / "link.txt"
    authorized.write_text("authorized\n", encoding="utf-8")
    other.write_text("other\n", encoding="utf-8")
    link.symlink_to(authorized)
    return authorized, other, link


def _move(link: Path, to: Path) -> None:
    link.unlink()
    link.symlink_to(to)


async def test_a_write_whose_link_moved_after_it_was_authorised_writes_nothing(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    authorized, other, link = _files("write")
    write: JsonObject = {"path": str(link), "content": "changed\n"}
    prepared: PreparedCall = await _authorized(ctx, "write_file", write)
    _move(link, other)
    with pytest.raises(ToolExecutionError, match=MOVED):
        await prepared.run(ctx)
    assert (authorized.read_text(encoding="utf-8"), other.read_text(encoding="utf-8")) == ("authorized\n", "other\n")
    await (await _authorized(ctx, "write_file", write)).run(ctx)
    assert other.read_text(encoding="utf-8") == "changed\n"


async def test_a_read_whose_link_moved_after_it_was_authorised_reads_nothing(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    _, other, link = _files("read")
    prepared: PreparedCall = await _authorized(ctx, "read_file", {"path": str(link)})
    _move(link, other)
    with pytest.raises(ToolExecutionError, match=MOVED):
        await prepared.run(ctx)
    read: ToolResult = await (await _authorized(ctx, "read_file", {"path": str(link)})).run(ctx)
    assert "other" in read.text
