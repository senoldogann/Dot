"""What Dot knows about the tools of an MCP server."""

from dotagent.core.types import FrozenModel, JsonObject


class ToolHints(FrozenModel):
    """The behaviour a server declares for a tool through its annotations; ``None`` means the server did not say."""

    read_only: bool | None
    destructive: bool | None
    open_world: bool | None


class McpTool(FrozenModel):
    """A tool of a connected server with the name the model calls it by and the time its calls may take."""

    server: str
    remote_name: str
    exposed_name: str
    description: str
    input_schema: JsonObject
    hints: ToolHints
    startup_timeout_s: float
    call_timeout_s: float


class McpPurpose(FrozenModel):
    """What the line of a server's tool group in a run's instructions says the server is for (item 659): ``text``, one
    plain line. ``claimed`` is whether it is what the server says of itself, third-party words that stand in quotes as
    its claim; otherwise they are the owner's (the ``description`` of its ``[[mcp]]`` table) or Dot's, for a server that
    comes with it, and stand as Dot's own text does."""

    text: str
    claimed: bool


class McpFailure(FrozenModel):
    """An enabled server that Dot left out because it could not start: why, the error's context, and the file its
    stderr goes to. Its tools are not offered until Dot starts again."""

    server: str
    error: str
    detail: JsonObject
    log: str
