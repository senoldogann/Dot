"""Item 279's data: what Connect apps' add asks for, read into the server Dot tries (a service of the catalogue, a URL
over streamable HTTP or SSE, a command line), with the names and lines it refuses; and the catalogue itself."""

import pytest

from dotagent.config import McpServerConfig
from dotagent.core.errors import ConfigError, McpError, NotFoundError
from dotagent.mcp.catalogue import MCP_SERVICES, McpService
from dotagent.mcp.launch import referenced_secrets
from dotagent.mcp.signin import checked_link
from dotagent.server.routes_mcp import (
    chosen_tools,
    kept_secret_names,
    requested_server,
    secret_entries,
    service_config,
    service_of,
)
from dotagent.server.views import McpServerAdd

_ORIGIN: str = "http://127.0.0.1:8765"


def _asked(service: str | None, name: str | None, address: str | None, **secrets: str) -> McpServerAdd:
    return McpServerAdd(service=service, name=name, address=address, origin=_ORIGIN, secrets=secrets)


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
        config: McpServerConfig = service_config(service)
        if service.kind == "remote":
            assert config.url is not None and config.url.startswith("https://") and config.command is None
        else:
            assert config.command in ("npx", "uvx") and config.url is None and not service.signs_in
        # Its secrets are referred to by the names Dot keeps them under, nothing else is.
        kept: set[str] = set(kept_secret_names(service).values())
        assert set(referenced_secrets(config)) == kept, service.id


def test_a_service_s_secrets_are_asked_for_by_name_and_kept_under_dot_s_own_names() -> None:
    """Item 408: a service that takes a key asks for it on the page; Dot keeps it in its .env under
    ``MCP_<SERVICE>_<NAME>`` and the server's headers or env refer to it by that name."""
    github: McpService = next(service for service in MCP_SERVICES if service.id == "github")
    assert kept_secret_names(github) == {"GITHUB_PAT": "MCP_GITHUB_GITHUB_PAT"}
    assert secret_entries(github, {"GITHUB_PAT": " ghp_x "}) == {"MCP_GITHUB_GITHUB_PAT": "ghp_x"}
    assert service_config(github).headers == {"Authorization": "Bearer ${MCP_GITHUB_GITHUB_PAT}"}
    assert requested_server(_asked("github", None, None, GITHUB_PAT="ghp_x")).name == "github"
    with pytest.raises(ConfigError) as refused:
        requested_server(_asked("github", None, None))
    assert refused.value.context == {"service": "github", "missing": ["GITHUB_PAT"], "unknown": []}
    with pytest.raises(ConfigError) as unknown:
        secret_entries(github, {"GITHUB_PAT": "x", "OTHER": "y"})
    assert unknown.value.context["unknown"] == ["OTHER"]
    brave: McpService = next(service for service in MCP_SERVICES if service.id == "brave-search")
    local: McpServerConfig = service_config(brave)
    assert (local.command, local.args, local.env) == (
        "npx",
        ["-y", "@brave/brave-search-mcp-server"],
        {"BRAVE_API_KEY": "${MCP_BRAVE_SEARCH_BRAVE_API_KEY}"},
    )
    # A server added by hand with the service's address or command wears the service's name.
    assert service_of(McpServerConfig(name="mine", url="https://mcp.notion.com/mcp")) == "notion"
    assert (
        service_of(McpServerConfig(name="mine", command="npx", args=["-y", "@playwright/mcp@latest"])) == "playwright"
    )
    # Item 514: Dot's own Playwright server keeps a page's WebMCP tools out of a run, and is still the service's.
    playwright: McpService = next(service for service in MCP_SERVICES if service.id == "playwright")
    assert playwright.args == ("-y", "@playwright/mcp@latest", "--no-webmcp")
    assert service_of(McpServerConfig(name="mine", command="npx", args=list(playwright.args))) == "playwright"
    assert service_of(McpServerConfig(name="mine", command="npx", args=["-y", "something-else"])) is None


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


def test_the_tools_chosen_for_a_server_become_its_allow_list_in_its_own_order() -> None:
    offered: tuple[str, ...] = tuple(f"tool_{index:02d}" for index in range(80))
    assert chosen_tools(("tool_05", "tool_01", "tool_05"), offered) == ["tool_01", "tool_05"]
    with pytest.raises(ConfigError) as unknown:
        chosen_tools(("tool_01", "nope"), offered)
    assert unknown.value.context["missing"] == ["nope"]
    with pytest.raises(ConfigError):
        chosen_tools((), offered)
    # Any number of them may be chosen (item 524), and every one of them keeps no list: the server then offers all
    # of its tools, those it adds later too.
    assert chosen_tools(offered[:79], offered) == list(offered[:79])
    assert chosen_tools(tuple(reversed(offered)), offered) == []
