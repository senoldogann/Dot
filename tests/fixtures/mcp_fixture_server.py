"""A small MCP server over stdio for the MCP hub tests: tools with and without hints, failures, images, slow calls.

Run it as ``python mcp_fixture_server.py``; it speaks MCP on stdin/stdout like any server a user would configure.
"""

import asyncio
import io
import os

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations
from PIL import Image as PilImage

server: MCPServer = MCPServer("dot-fixture")

_READ_ONLY_CLOSED: ToolAnnotations = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@server.tool(annotations=_READ_ONLY_CLOSED)
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool(annotations=_READ_ONLY_CLOSED)
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool(name="notes.save", annotations=ToolAnnotations(destructive_hint=False, open_world_hint=False))
def save_note(title: str, body: str | None = None) -> str:
    """Pretend to save a note; the body is optional, as many real servers declare optional arguments."""
    return f"saved {title} ({len(body or '')} characters)"


@server.tool()
def wipe(target: str) -> str:
    """Pretend to wipe something. The tool declares no hints at all, like most servers in the wild."""
    return f"wiped {target}"


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def explode(reason: str) -> str:
    """Always fails."""
    raise ValueError(reason)


@server.tool(annotations=_READ_ONLY_CLOSED)
def picture() -> Image:
    """Return a small red square as a PNG image."""
    buffer: io.BytesIO = io.BytesIO()
    PilImage.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return Image(data=buffer.getvalue(), format="png")


@server.tool(annotations=_READ_ONLY_CLOSED)
async def slow(seconds: float) -> str:
    """Wait, then answer."""
    await asyncio.sleep(seconds)
    return "done"


@server.tool(annotations=_READ_ONLY_CLOSED)
def environment(name: str) -> str:
    """Read one environment variable of this server process."""
    return os.environ.get(name, "<unset>")


@server.tool(annotations=_READ_ONLY_CLOSED)
def process_id() -> int:
    """The process id of this server, so a test can end it."""
    return os.getpid()


@server.tool(annotations=_READ_ONLY_CLOSED)
def counts() -> dict[str, int]:
    """Structured output: a mapping of names to numbers."""
    return {"apples": 3, "pears": 5}


if __name__ == "__main__":
    server.run("stdio")
