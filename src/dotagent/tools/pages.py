"""Showing the user a page of HTML in the conversation (``show_page``): a chart, a table, a diagram, a 3D view or a
mockup that the run writes. The store keeps the page under an id nobody can guess, the run's ``page_shown`` event names
it, and the pages of the UI show it in a sandboxed frame (``static/js/pageframe.js``)."""

from typing import Annotated

from pydantic import Field, StringConstraints

from dotagent.agent.pages import LIBRARY_HOSTS, PAGE_EVENT, PAGE_MAX_CHARS, PAGE_TITLE_MAX_CHARS, THEME_VARIABLES
from dotagent.policy.types import ActionInfo
from dotagent.store.models import ShownPage
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

SHOW_PAGE_TOOL: str = "show_page"

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
    "Chart.js for charts, Mermaid for diagrams, D3 for a drawing of your own, three.js for 3D."
)


class ShowPageArgs(ToolArgs):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PAGE_TITLE_MAX_CHARS)] = (
        Field(description="A few words that name the page.")
    )
    html: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PAGE_MAX_CHARS)] = Field(
        description="The page in HTML."
    )


def _show_page_tool() -> RegisteredTool:
    def classify(args: ShowPageArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=SHOW_PAGE_TOOL,
            kind="meta",
            risk="safe",
            summary=f"Show the page “{args.title}”",
            target="",
            flags=(),
        )

    async def run(args: ShowPageArgs, ctx: ToolContext) -> ToolResult:
        page: ShownPage = await ctx.runtime.store.add_shown_page(ctx.run_id, args.title, args.html)
        await ctx.runtime.bus.emit(ctx.run_id, PAGE_EVENT, {"page_id": page.id, "title": page.title}, True)
        return ok(f"The page “{page.title}” is shown to the user in the conversation.")

    return make_tool(
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
