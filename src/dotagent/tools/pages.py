"""Showing the user a page of HTML in the conversation (``show_page``): a chart, a table, a diagram, a 3D view or a
mockup that the run writes, or a page it made in its workspace, by its path (item 598). The store keeps the page under
an id nobody can guess, the run's ``page_shown`` event names it, and the pages of the UI show it in a sandboxed frame
(``static/js/pageframe.js``)."""

import asyncio
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from dotagent.agent.pages import (
    LIBRARY_HOSTS,
    PAGE_EVENT,
    PAGE_MAX_CHARS,
    PAGE_TITLE_MAX_CHARS,
    THEME_VARIABLES,
)
from dotagent.core.errors import ToolInputError
from dotagent.policy.paths import expand_path, within
from dotagent.policy.types import ActionInfo
from dotagent.store.models import ShownPage
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_path_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.files import read_action
from dotagent.tools.page_files import PageFiles, with_its_files
from dotagent.tools.third_party import holds_third_party

SHOW_PAGE_TOOL: str = "show_page"
# The files of the workspace shown by their path (item 598): a page, and a drawing in SVG, which a page's body holds.
PAGE_SUFFIXES: frozenset[str] = frozenset({".html", ".htm", ".xhtml", ".svg"})
# The bytes a page file may hold before it is read: four for each character a page may have, UTF-8's most.
_PAGE_MAX_BYTES: int = PAGE_MAX_CHARS * 4

# What the tool says of a page; since item 521 it asks for one that lives: animated, with values on hover.
_DESCRIPTION: str = (
    "Show the user a page you write in HTML, in the conversation where they read you: a chart, a table, a diagram, a "
    "3D view, a mockup or anything that says more drawn than written. It sits under its title in a frame as wide as "
    "the conversation that grows with the page, and the user can open it full size; do not repeat the title or draw "
    "an outer box. Write a whole document or only its body. Its scripts run but fetch nothing except the libraries "
    f"they load from {', '.join(LIBRARY_HOSTS)}: put the data and any image (as a data: URL) in the page itself. "
    "Leave the page without a background of its own, give charts and 3D views a height in pixels, never 100vh, and "
    "take the look of the user's theme, light or dark, from the CSS variables "
    f"{', '.join(name for name, _ in THEME_VARIABLES)}. "
    "Make it live: animate its entrance, show values on hover, let a table sort and a chart toggle its "
    "series, and keep still under prefers-reduced-motion. Libraries that fit, in a pinned version: Apache ECharts or "
    "Chart.js for charts, Mermaid for diagrams, D3 for a drawing of your own, three.js for 3D. A page you made in the "
    f"workspace ({', '.join(sorted(PAGE_SUFFIXES))}) is shown by its path instead of html, with the style sheets, "
    "scripts, pictures and fonts beside it that it names written in; what a script loads by itself does not load."
)


class ShowPageArgs(ToolArgs):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PAGE_TITLE_MAX_CHARS)] = (
        Field(description="A few words that name the page.")
    )
    html: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PAGE_MAX_CHARS)] | None = (
        Field(default=None, description="The page in HTML.")
    )
    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = Field(
        default=None,
        description="Instead of html, a page you made in the workspace. Relative paths resolve against the workspace.",
    )

    @model_validator(mode="after")
    def one_page(self) -> Self:
        if (self.html is None) == (self.path is None):
            raise ValueError("give the page as html or by its path, one of the two")
        return self


def page_file(path: Path, workspace: Path) -> str:
    """The text of the page at the resolved ``path`` that a run shows by its path (item 598): a file of
    :data:`PAGE_SUFFIXES` in ``workspace`` once its links are followed, which no third party wrote (a crawl's page, an
    MCP answer: item 494), of :data:`PAGE_MAX_CHARS` at most. What the page shows reaches the owner alone, sandboxed;
    the run reads nothing of it, but it can name no file outside its workspace, nor a page another wrote."""
    if not within(path, expand_path(str(workspace), workspace)):
        raise ToolInputError(
            f"{path} is not in the workspace: a page is shown by its path when you made it there; give another as html",
            {"path": str(path)},
        )
    if holds_third_party(path, workspace):
        raise ToolInputError(
            f"{path} holds what a crawl or an MCP service wrote, not a page you made: it is not shown",
            {"path": str(path)},
        )
    if path.suffix.lower() not in PAGE_SUFFIXES:
        raise ToolInputError(
            f"{path.name} is no page: a file of {', '.join(sorted(PAGE_SUFFIXES))} is shown by its path",
            {"path": str(path)},
        )
    if not path.is_file():
        raise ToolInputError(f"there is no page at {path}", {"path": str(path)})
    if path.stat().st_size > _PAGE_MAX_BYTES:
        raise ToolInputError(
            f"{path.name} is longer than a page may be ({PAGE_MAX_CHARS:,} characters)", {"path": str(path)}
        )
    try:
        text: str = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolInputError(f"{path.name} is not text in UTF-8", {"path": str(path)}) from exc
    if len(text) > PAGE_MAX_CHARS:
        raise ToolInputError(
            f"{path.name} is longer than a page may be ({PAGE_MAX_CHARS:,} characters)", {"path": str(path)}
        )
    if not text.strip():
        raise ToolInputError(f"{path.name} is empty", {"path": str(path)})
    return text


def whole_page_file(path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> PageFiles:
    """The page at the resolved ``path`` (:func:`page_file`) with the files beside it that it names written in while
    they fit in one page (item 600), and the names of those written in and left out."""
    return with_its_files(page_file(path, workspace), path, workspace, protected, dot_paths)


def shown_words(page: ShownPage, files: PageFiles | None) -> str:
    """What show_page tells the run: the page is shown, and, for a page by its path, the files written into it and
    those left out (item 600)."""
    words: str = f"The page “{page.title}” is shown to the user in the conversation."
    if files is None:
        return words
    if files.written:
        words += f" Written into it: {', '.join(dict.fromkeys(files.written))}."
    if files.left_out:
        words += (
            f" Not written in, so not loaded: {', '.join(dict.fromkeys(files.left_out))} (outside the workspace, "
            "protected, kept from a crawl or an MCP answer, or missing)."
        )
    if files.too_large:
        words += (
            f" Left out as the page would have been longer than {PAGE_MAX_CHARS:,} characters with them: "
            f"{', '.join(dict.fromkeys(files.too_large))} (make them smaller to show them)."
        )
    return words


def _show_page_tool() -> RegisteredTool:
    def classify(args: ShowPageArgs, ctx: ToolContext) -> ActionInfo:
        if args.path is not None:
            # A page shown by its path is a read of that file, which the rules on files judge as read_file's.
            shown: ActionInfo = read_action(
                SHOW_PAGE_TOOL,
                expand_path(args.path, ctx.workspace),
                ctx.workspace,
                ctx.runtime.protected,
                ctx.runtime.dot_paths,
                f"Show the page “{args.title}” from",
            )
            return shown
        return ActionInfo(
            tool=SHOW_PAGE_TOOL,
            kind="meta",
            risk="safe",
            summary=f"Show the page “{args.title}”",
            target="",
            flags=(),
        )

    async def run(args: ShowPageArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        files: PageFiles | None = None
        if args.path is not None:
            # The file that was judged (item 598's review): classified again as the call starts and refused if a link
            # moved since, then read where it was found, not by its path resolved once more.
            files = await asyncio.to_thread(
                whole_page_file, Path(authorized.target), ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
            )
            html: str = files.html
        elif args.html is not None:
            html = args.html
        else:
            raise ToolInputError("give the page as html or by its path", {})
        page: ShownPage = await ctx.runtime.store.add_shown_page(ctx.run_id, args.title, html)
        await ctx.runtime.bus.emit(ctx.run_id, PAGE_EVENT, {"page_id": page.id, "title": page.title}, True)
        return ok(shown_words(page, files))

    return make_path_tool(
        name=SHOW_PAGE_TOOL,
        description=_DESCRIPTION,
        args_model=ShowPageArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


def build_page_tools() -> tuple[RegisteredTool, ...]:
    return (_show_page_tool(),)
