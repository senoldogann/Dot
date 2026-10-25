"""An MCP server over stdio whose words are the test's: what it says of itself (its ``instructions``) and the
description of its one tool come from the environment of the process, so that a test can have a server say anything,
a line break and an attempt to give the model instructions among it (item 659).

Run it as ``python mcp_words_server.py`` with ``DOT_TEST_INSTRUCTIONS`` and ``DOT_TEST_TOOL_WORDS`` set; one that is
unset is a server that says nothing.
"""

import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server: MCPServer = MCPServer("dot-words", instructions=os.environ.get("DOT_TEST_INSTRUCTIONS") or None)


@server.tool(
    name="ping",
    description=os.environ.get("DOT_TEST_TOOL_WORDS") or None,
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def ping() -> str:
    return "pong"


if __name__ == "__main__":
    server.run("stdio")
