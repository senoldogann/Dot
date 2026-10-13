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


class McpFailure(FrozenModel):
    """An enabled server that Dot left out because it could not start: why, the error's context, and the file its
    stderr goes to. Its tools are not offered until Dot starts again."""

    server: str
    error: str
    detail: JsonObject
    log: str
