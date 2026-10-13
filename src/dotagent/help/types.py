"""One thing on this Mac that can help a run, as find_help ranks and shows it."""

from typing import Literal

from dotagent.core.types import FrozenModel

type HelpKind = Literal["skill", "mcp_tool", "app"]


class HelpItem(FrozenModel):
    """A skill, an MCP tool or a scriptable application. ``summary`` is what the run is told of it, ``use`` how the run
    uses it, and ``text`` the words it is ranked by, its name counted twice: a name says most of what a thing is for."""

    kind: HelpKind
    name: str
    summary: str
    use: str
    text: str
