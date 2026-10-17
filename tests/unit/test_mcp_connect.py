"""Item 279's data: what Connect apps' add asks for, read into the server Dot tries (a service of the catalogue, a URL
over streamable HTTP or SSE, a command line), with the names and lines it refuses; and the catalogue itself."""

import pytest

from dotagent.config import McpServerConfig
from dotagent.core.errors import ConfigError, McpError, NotFoundError
from dotagent.mcp.catalogue import MCP_SERVICES
from dotagent.mcp.signin import checked_link
from dotagent.server.routes_mcp import requested_server
from dotagent.server.views import McpServerAdd

_ORIGIN: str = "http://127.0.0.1:8765"


def _asked(service: str | None, name: str | None, address: str | None) -> McpServerAdd:
    return McpServerAdd(service=service, name=name, address=address, origin=_ORIGIN)


def test_an_add_names_a_service_a_url_or_a_command_line() -> None:
    notion: McpServerConfig = requested_server(_asked("notion", None, None))
    assert (notion.name, notion.url, notion.transport, notion.oauth) == (
        "notion",
        "https://mcp.notion.com/mcp",
        "http",
        False,
    )
    legacy: McpServerConfig = requested_server(_asked(None, " legacy ", " https://example.com/sse/ "))
    assert (legacy.name, legacy.url, legacy.transport) == ("legacy", "https://example.com/sse/", "sse")
    local: McpServerConfig = requested_server(_asked(None, "notes", "npx -y 'notes server' --root ~/Notes"))
    assert (local.command, local.args, local.url) == ("npx", ["-y", "notes server", "--root", "~/Notes"], None)


def test_an_add_that_cannot_be_read_is_refused_with_why() -> None:
    with pytest.raises(ConfigError, match="lowercase letters"):
        requested_server(_asked(None, "Not A Name", "https://example.com/mcp"))
    with pytest.raises(ConfigError, match="cannot be read"):
        requested_server(_asked(None, "notes", "npx 'unclosed"))
    with pytest.raises(ConfigError, match="name a service"):
        requested_server(_asked(None, "notes", "  "))
    with pytest.raises(NotFoundError):
        requested_server(_asked("myspace", None, None))


def test_each_service_of_the_catalogue_is_a_server_dot_can_have() -> None:
    ids: list[str] = [service.id for service in MCP_SERVICES]
    assert len(set(ids)) == len(ids)
    for service in MCP_SERVICES:
        config: McpServerConfig = McpServerConfig(name=service.id, url=service.url, transport="http")
        assert config.url is not None and config.url.startswith("https://")


def test_a_sign_in_page_is_opened_only_at_an_https_address_or_one_of_this_mac() -> None:
    """The authorization server is the MCP server's: the page it sends the owner to is a link on Dot's page, so only an
    https address, or an http one on this Mac, is taken (review S1)."""
    for link in (
        "https://auth.example.com/authorize?state=s",
        "http://127.0.0.1:8123/authorize?state=s",
        "http://localhost/authorize?state=s",
    ):
        assert checked_link(link) == link
    for link in (
        "javascript:alert(document.cookie)//?state=s",
        "data:text/html,<script>x</script>?state=s",
        "http://auth.example.com/authorize?state=s",
        "file:///etc/passwd?state=s",
    ):
        with pytest.raises(McpError, match="not an https address"):
            checked_link(link)
