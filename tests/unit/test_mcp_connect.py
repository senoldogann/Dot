"""Item 279's data: what Connect apps' add asks for, read into the server Dot tries (a service of the catalogue, a URL
over streamable HTTP or SSE, a command line), with the names and lines it refuses; and the catalogue itself."""

import pytest

from dotagent.config import McpServerConfig
from dotagent.connections.directory import PLUGINS
from dotagent.core.errors import ConfigError, McpError, NotFoundError
from dotagent.mcp.catalogue import MCP_SERVICES, McpService, StartedPackage, package_of, unversioned
from dotagent.mcp.launch import referenced_secrets
from dotagent.mcp.signin import checked_link
from dotagent.server.routes_mcp import (
    FIRST_START_S,
    chosen_tools,
    first_try,
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
            # Started on this Mac: a package of its registry (npx, uvx), or a tool of Xcode's own (xcrun, item 535).
            assert config.command in ("npx", "uvx", "xcrun") and config.url is None and not service.signs_in
        # Its secrets are referred to by the names Dot keeps them under, nothing else is.
        kept: set[str] = set(kept_secret_names(service).values())
        assert set(referenced_secrets(config)) == kept, service.id


def test_each_server_started_on_this_mac_runs_the_one_version_the_catalogue_pins() -> None:
    """Item 695: ``npx`` and ``uvx`` run what the registry has newest at each start, with the owner's rights, so each
    package the catalogue starts is written with its exact version (``name@x.y.z``, ``name==x.y.z``): a release that is
    compromised or breaks lands only when someone bumps the number (``scripts/catalogue_versions.py`` shows which)."""
    started: list[McpService] = [service for service in MCP_SERVICES if service.command in ("npx", "uvx")]
    assert len(started) > 30
    for service in started:
        package: StartedPackage | None = package_of(service)
        assert package is not None, service.id
        assert package.pinned, (service.id, package)
        assert "latest" not in " ".join(service.args), service.id
        # The version is written where its program reads it: after npm's ``@``, after PyPI's ``==``.
        word: str = next(arg for arg in service.args if not arg.startswith("-"))
        assert word == (
            f"{package.name}@{package.version}" if package.registry == "npm" else f"{package.name}=={package.version}"
        )
    # What else is started has no registry to pin by (Xcode's own bridge), and a service at an address starts nothing.
    assert [service.id for service in MCP_SERVICES if service.command not in (None, "npx", "uvx")] == ["xcode"]
    assert all(package_of(service) is None for service in MCP_SERVICES if service.command not in ("npx", "uvx"))
    # The directory names its ways by the catalogue's ids: each way started on this Mac is one of those pinned.
    for listing in PLUGINS:
        for way in listing.ways:
            if way.kind == "mcp":
                service: McpService = next(one for one in MCP_SERVICES if one.id == way.ref)
                assert service.command not in ("npx", "uvx") or service in started, way.ref


def test_a_package_word_is_read_as_its_registry_writes_it_and_a_server_added_before_the_pin_is_still_the_services() -> (
    None
):
    npm: McpService = McpService(id="a", name="A", does="d", command="npx", args=("-y", "@scope/pkg@1.2.3", "serve"))
    assert package_of(npm) == StartedPackage(registry="npm", name="@scope/pkg", version="1.2.3")
    unpinned: McpService = npm.model_copy(update={"args": ("-y", "pkg", "serve")})
    assert package_of(unpinned) == StartedPackage(registry="npm", name="pkg", version=None)
    assert not StartedPackage(registry="npm", name="pkg", version="latest").pinned
    assert not StartedPackage(registry="pypi", name="pkg", version=">=1.2").pinned
    assert StartedPackage(registry="pypi", name="pkg", version="0.0.1a8").pinned
    # uvx's own word after --from names the package, the command after it does not.
    moved: McpService = McpService(id="b", name="B", does="d", command="uvx", args=("--from", "pkg==2.0.1", "tool"))
    assert package_of(moved) == StartedPackage(registry="pypi", name="pkg", version="2.0.1")
    assert [unversioned(word) for word in ("@a/b@latest", "@a/b", "p==1.2", "p@1.2", "p", ":memory:")] == [
        "@a/b",
        "@a/b",
        "p",
        "p",
        "p",
        ":memory:",
    ]
    # A server that was added while the catalogue wrote ``@latest`` or no version at all is the service's all the same.
    for word, service_id in (("@playwright/mcp@latest", "playwright"), ("@playwright/mcp", "playwright")):
        assert service_of(McpServerConfig(name="mine", command="npx", args=["-y", word])) == service_id
    assert service_of(McpServerConfig(name="mine", command="uvx", args=["mcp-server-time"])) == "time"
    assert service_of(McpServerConfig(name="mine", command="uvx", args=["mcp-server-time==1.0.0"])) == "time"
    assert service_of(McpServerConfig(name="mine", command="uvx", args=["mcp-server-time-other"])) is None
    # Another source under the service's name is another package (the code and security reviews of item 695); a range
    # is no pin.
    for word in (
        "@playwright/mcp@npm:other-package",
        "@playwright/mcp@github:someone/fork",
        "@playwright/mcp@https://x",
    ):
        assert unversioned(word) == word
        assert service_of(McpServerConfig(name="mine", command="npx", args=["-y", word])) is None
    for loose in ("1.2.x", "^1.2.0", "~1.2", "1.2.3 || 2", "1.2.3a"):
        assert not StartedPackage(registry="npm", name="pkg", version=loose).pinned, loose
    for exact in ("4.0.0-beta.3", "1.2.3+build.5", "2026.10.24", "1.0.0rc2", "1.0.0.post1", "0.3.0.dev4"):
        assert StartedPackage(registry="npm", name="pkg", version=exact).pinned, exact


def test_the_try_that_adds_a_server_of_this_mac_waits_for_its_package_and_the_server_kept_does_not() -> None:
    """Item 535: npx and uvx fetch a server's package on its first start, which took from 52 seconds to more than five
    minutes on a cold cache; the try that adds it is given FIRST_START_S, the server kept keeps its own limit, which a
    daemon start waits for."""
    local: McpServerConfig = service_config(next(service for service in MCP_SERVICES if service.id == "firebase"))
    assert local.startup_timeout_s == 60.0
    tried: McpServerConfig = first_try(local)
    assert tried.startup_timeout_s == FIRST_START_S == 300.0
    assert tried.model_copy(update={"startup_timeout_s": local.startup_timeout_s}) == local
    by_hand: McpServerConfig = requested_server(_asked(None, "notes", "npx -y notes-server"))
    assert first_try(by_hand).startup_timeout_s == FIRST_START_S
    # A limit longer already, and a server at a URL, whose try waits for the sign-in instead, are left as they are.
    patient: McpServerConfig = local.model_copy(update={"startup_timeout_s": 900.0})
    assert first_try(patient) == patient
    remote: McpServerConfig = service_config(next(service for service in MCP_SERVICES if service.id == "notion"))
    assert first_try(remote) == remote


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
    assert (local.command, local.args[0], local.env) == (
        "npx",
        "-y",
        {"BRAVE_API_KEY": "${MCP_BRAVE_SEARCH_BRAVE_API_KEY}"},
    )
    assert local.args[1].startswith("@brave/brave-search-mcp-server@")
    # A server added by hand with the service's address or command wears the service's name.
    assert service_of(McpServerConfig(name="mine", url="https://mcp.notion.com/mcp")) == "notion"
    assert (
        service_of(McpServerConfig(name="mine", command="npx", args=["-y", "@playwright/mcp@latest"])) == "playwright"
    )
    # Item 514: Dot's own Playwright server keeps a page's WebMCP tools out of a run, and is still the service's.
    playwright: McpService = next(service for service in MCP_SERVICES if service.id == "playwright")
    assert playwright.args[0] == "-y" and playwright.args[2:] == ("--no-webmcp",)
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
