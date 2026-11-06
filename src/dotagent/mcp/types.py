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


# Where the running daemon answers the tools it left out (``dotx doctor`` asks it there, item 1001).
LEFT_OUT_PATH: str = "/api/mcp/left-out"


class LeftOutTool(FrozenModel):
    """One tool ``usable_tools`` did not hand to the model: its name on the server and why. The server's other tools
    still serve. This is the ``mcp_tool_left_out`` log line, kept so doctor and the connector page can say it."""

    server: str
    tool: str
    reason: str


class McpFailure(FrozenModel):
    """An enabled server that Dot left out because it could not start: why, the error's context, and the file its
    stderr goes to. Its tools are not offered until Dot starts again."""

    server: str
    error: str
    detail: JsonObject
    log: str
