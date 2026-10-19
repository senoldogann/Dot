"""A tool that names files works on the files it was authorised for (item 181): it is classified again as it starts,
and a path that leads elsewhere by then (a link moved while the call waited for its approval) fails the call, saying
so, with nothing written or read; called again, it is authorised for where the path leads now. An application is named
by a path too, and the bundle it leads to is what decides whether it may be opened without a question (item 418).

No network and no model service is used: the calls are prepared, authorised and run as the executor runs them.
"""

import asyncio
import os
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


def _bundle_link(name: str) -> tuple[Path, Path]:
    """A link that leads to an application on the sealed system volume, and the path of one that is not, a bundle a run
    may have made."""
    folder: Path = workspace_dir() / name
    folder.mkdir(parents=True)
    link: Path = folder / "Tool.app"
    link.symlink_to("/System/Applications/Notes.app")
    return folder / "made.app", link


def _two_bundles(name: str) -> tuple[str, Path, Path]:
    """A link, the resolved path of the application it leads to when a call is authorised, and the path of another a run
    may have made, both of them outside the sealed system volume: the same question, with another answer."""
    folder: Path = workspace_dir() / name
    folder.mkdir(parents=True)
    link: Path = folder / "Tool.app"
    approved: Path = Path("/Applications/An Application.app")
    link.symlink_to(approved)
    return os.path.realpath(approved), folder / "made.app", link


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


async def test_an_application_named_by_its_name_is_named_so_for_the_other_runs(runtime: Runtime) -> None:
    """Item 418 with items 301 and 305: the action names the application as every other desktop tool names it, so a run
    at work in the same application is still seen, while the bundle the name resolved to is what is opened."""
    ctx: ToolContext = await _context(runtime)
    prepared: PreparedCall = await _authorized(ctx, "desktop_open_app", {"app": "Notes"})
    assert prepared.action.target == "Notes"
    assert prepared.action.bundle == "/System/Applications/Notes.app"


async def test_an_application_whose_link_moved_to_a_bundle_a_run_made_is_not_opened(runtime: Runtime) -> None:
    """Item 418: a bundle on the sealed system volume needs no question; the same link leading to a bundle a run could
    have made does, so a link moved while the call waited for its approval is refused rather than opened."""
    ctx: ToolContext = await _context(runtime)
    made, link = _bundle_link("open")
    prepared: PreparedCall = await _authorized(ctx, "desktop_open_app", {"app": str(link)})
    _move(link, made)
    with pytest.raises(ToolExecutionError, match=MOVED):
        await prepared.run(ctx)


async def test_an_application_whose_link_moved_to_another_bundle_outside_system_is_not_opened(
    runtime: Runtime,
) -> None:
    """Item 418: what is authorised is the bundle the link leads to, not the question it is asked about, so a second
    bundle outside the system volume put in place of the first while the call waited is not opened in its stead."""
    ctx: ToolContext = await _context(runtime)
    approved, made, link = _two_bundles("open-outside")
    prepared: PreparedCall = await _authorized(ctx, "desktop_open_app", {"app": str(link)})
    assert prepared.action.bundle == approved
    _move(link, made)
    with pytest.raises(ToolExecutionError, match=MOVED):
        await prepared.run(ctx)
