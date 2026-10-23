"""A page a run made in its workspace is shown by its path (item 598): show_page reads the file and keeps it as it keeps
a page given as HTML, and the call is judged as a read of that file, as read_file's is. A path out of the workspace,
through a link or "..", a page a crawl kept, a file that is no page, a missing, an empty and a too long one are refused
and nothing is kept; so is a call that gives both the HTML and a path, or neither."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.pages import PAGE_MAX_CHARS
from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.types import JsonObject
from dotagent.store.models import ShownPage
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.pages import SHOW_PAGE_TOOL, build_page_tools
from dotagent.tools.third_party import CRAWL_FOLDER

# The owner's page in short: its own styles and scripts, and one library from a CDN a shown page may load.
PAGE: str = (
    "<!doctype html><title>AI Herald</title><style>p{margin:0}</style><p>The herald</p><script "
    'src="https://cdn.jsdelivr.net/npm/page-flip@2.0.7/dist/js/page-flip.browser.js"></script>'
)
DRAWING: str = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


class _Store:
    """The store's side of show_page: the pages it kept, by title."""

    def __init__(self) -> None:
        self.kept: dict[str, str] = {}

    async def add_shown_page(self, run_id: str, title: str, html: str) -> ShownPage:
        self.kept[title] = html
        return ShownPage(id="page_x", run_id=run_id, title=title, html=html, created_at=0.0)


class _Bus:
    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> None:
        return None


def _context(home: Path, protected: tuple[Path, ...] = ()) -> SimpleNamespace:
    """What show_page reads of the tool context: the workspace, the run, the places the rules on files guard, the store
    and the bus."""
    return SimpleNamespace(
        workspace=home / "workspace",
        run_id="run_1",
        runtime=SimpleNamespace(protected=protected, dot_paths=(), store=_Store(), bus=_Bus()),
    )


def _home(tmp_path: Path) -> Path:
    """A home with its workspace, the page Dot made there and a page outside it."""
    home: Path = Path(os.path.realpath(tmp_path))
    (home / "workspace" / "ai-herald").mkdir(parents=True)
    (home / "workspace" / "ai-herald" / "index.html").write_text(PAGE, encoding="utf-8")
    (home / "outside.html").write_text(PAGE, encoding="utf-8")
    return home


async def _prepared(ctx: SimpleNamespace, arguments: JsonObject) -> PreparedCall:
    tool: RegisteredTool = next(tool for tool in build_page_tools() if tool.spec.name == SHOW_PAGE_TOOL)
    return await tool.prepare(arguments, ctx)  # type: ignore[arg-type]


async def _shown(ctx: SimpleNamespace, arguments: JsonObject) -> ToolResult:
    prepared: PreparedCall = await _prepared(ctx, arguments)
    return await prepared.run(ctx)  # type: ignore[arg-type]


async def test_a_page_made_in_the_workspace_is_shown_by_its_path_as_a_read_of_it(tmp_path: Path) -> None:
    home: Path = _home(tmp_path)
    (home / "workspace" / "chart.svg").write_text(DRAWING, encoding="utf-8")
    ctx: SimpleNamespace = _context(home, protected=(home / "workspace" / "keys",))
    prepared: PreparedCall = await _prepared(ctx, {"title": "AI Herald", "path": "ai-herald/index.html"})
    # Judged as read_file's read of the same file.
    page: Path = home / "workspace" / "ai-herald" / "index.html"
    assert (prepared.action.kind, prepared.action.target, prepared.action.flags) == ("read", str(page), ())
    result: ToolResult = await prepared.run(ctx)  # type: ignore[arg-type]
    assert not result.is_error
    assert ctx.runtime.store.kept == {"AI Herald": PAGE}
    # A drawing in SVG by its absolute path, and a page given as HTML as before.
    await _shown(ctx, {"title": "Chart", "path": str(home / "workspace" / "chart.svg")})
    await _shown(ctx, {"title": "Sales", "html": "<p>Sales</p>"})
    assert ctx.runtime.store.kept == {"AI Herald": PAGE, "Chart": DRAWING, "Sales": "<p>Sales</p>"}
    # A protected place in the workspace is a read of secrets, which the policy asks about, as for read_file.
    guarded: PreparedCall = await _prepared(ctx, {"title": "Keys", "path": "keys/index.html"})
    assert guarded.action.flags == ("secret_access",)


@pytest.mark.parametrize(
    ("named", "refused"),
    [
        ("../outside.html", "is not in the workspace"),
        ("ai-herald/../../outside.html", "is not in the workspace"),
        ("link.html", "is not in the workspace"),
        (f"{CRAWL_FOLDER}/page.html", "holds what a crawl or an MCP service wrote"),
        ("notes.md", "is no page"),
        ("gone.html", "there is no page at"),
        ("empty.html", "is empty"),
        ("long.html", "is longer than a page may be"),
    ],
)
async def test_a_path_that_is_no_page_the_run_made_is_refused(tmp_path: Path, named: str, refused: str) -> None:
    home: Path = _home(tmp_path)
    workspace: Path = home / "workspace"
    # A link in the workspace to the page outside it, which a command of the run can make.
    (workspace / "link.html").symlink_to(home / "outside.html")
    (workspace / CRAWL_FOLDER).mkdir()
    (workspace / CRAWL_FOLDER / "page.html").write_text(PAGE, encoding="utf-8")
    (workspace / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (workspace / "empty.html").write_text(" \n", encoding="utf-8")
    (workspace / "long.html").write_text("x" * (PAGE_MAX_CHARS + 1), encoding="utf-8")
    ctx: SimpleNamespace = _context(home)
    with pytest.raises(ToolInputError, match=refused):
        await _shown(ctx, {"title": "Page", "path": named})
    assert ctx.runtime.store.kept == {}


@pytest.mark.parametrize("arguments", [{"title": "Page"}, {"title": "Page", "html": "<p>x</p>", "path": "x.html"}])
async def test_a_page_is_given_as_html_or_by_its_path_one_of_the_two(tmp_path: Path, arguments: JsonObject) -> None:
    with pytest.raises(ToolInputError, match="one of the two"):
        await _prepared(_context(_home(tmp_path)), arguments)


async def test_a_link_moved_after_the_call_was_judged_is_not_followed(tmp_path: Path) -> None:
    """Item 598's review: a page shown by its path is judged again as the call starts, and read where it was found. A
    link to an ordinary page, judged a safe read, then pointed at a protected place while the call waited (a parallel
    command, an approval), is refused, and nothing of the protected file is kept."""
    home: Path = _home(tmp_path)
    workspace: Path = home / "workspace"
    (workspace / "keys").mkdir()
    (workspace / "keys" / "secret.html").write_text("<p>the key</p>", encoding="utf-8")
    (workspace / "x.html").symlink_to(workspace / "ai-herald" / "index.html")
    ctx: SimpleNamespace = _context(home, protected=(workspace / "keys",))
    prepared: PreparedCall = await _prepared(ctx, {"title": "Herald", "path": "x.html"})
    assert prepared.action.flags == ()
    (workspace / "x.html").unlink()
    (workspace / "x.html").symlink_to(workspace / "keys" / "secret.html")
    with pytest.raises(ToolExecutionError, match="what it names changed while it waited"):
        await prepared.run(ctx)  # type: ignore[arg-type]
    assert ctx.runtime.store.kept == {}
