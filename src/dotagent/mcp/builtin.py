"""The MCP servers that come with Dot (item 583): one, ``web-research``, which searches the web and reads pages (the
package ``dotagent-web-research``, a dependency of Dot's own). Dot starts it with the servers of ``[[mcp]]``, as it
starts those, and its tools are offered and judged like any server's tools; what makes it Dot's is only that nobody had
to configure it.

It starts when the package can be imported, ``[builtin_mcp] web_research`` is on and no other server has its name: a
``[[mcp]]`` table, a plugin, a server added from another agent or one added on Customize › Connectors that is called
``web-research`` replaces it, so that the owner's own checkout of the server wins over this one.
"""

import importlib.util
import sys
from collections.abc import Collection

from pydantic import JsonValue

from dotagent.config import AppConfig, McpServerConfig
from dotagent.config_edit import BuiltinMcpSetting
from dotagent.core.paths import web_research_dir
from dotagent.core.types import JsonObject

WEB_RESEARCH: str = "web-research"
# The package the server is in, as it is imported (the distribution is called dotagent-web-research), and its module.
# A test that must not start the server looks for the package under a name nothing has (tests/conftest.py).
WEB_RESEARCH_PACKAGE: str = "web_scraper"
_WEB_RESEARCH_MODULE: str = "web_scraper.mcp_server"


def web_research_installed() -> bool:
    """Whether this Python can import the package that has the web-research server. The package is looked for, not
    imported: the server runs in a process of its own."""
    return importlib.util.find_spec(WEB_RESEARCH_PACKAGE) is not None


def _web_research(enabled: bool) -> McpServerConfig:
    """The server as Dot starts it: the Python the daemon runs on, so that it finds the package wherever Dot is
    installed, and its data in Dot's home. ``-P`` keeps the working directory off its import path: the server starts in
    the workspace, where a run's commands write, and a file there named like a module it imports would run in it.
    ``PYTHON_DOTENV_DISABLED=1`` has python-dotenv read no ``.env`` into its environment: it started with the first one
    above the package's folder, in the app's layout Dot's own key file, and a ``.env`` that a program left in the
    workspace, the working folder, is read still; what is in the environment reaches the headless browser's driver, and
    a ``NODE_OPTIONS`` there is a file run outside the sandbox. The package reads only the working folder's now (its
    own change), and the variable closes that one too."""
    return McpServerConfig(
        name=WEB_RESEARCH,
        command=sys.executable,
        args=["-P", "-m", _WEB_RESEARCH_MODULE],
        env={"WEB_TOOLS_DATA_DIR": str(web_research_dir()), "PYTHON_DOTENV_DISABLED": "1"},
        enabled=enabled,
    )


# What Dot knows of the server's tools: which of their arguments hold words that leave the Mac, and so are judged by
# the rules of the tool of Dot's own that does the same (``tools.mcp.checked_builtin_call`` applies them, and
# ``tools.web`` holds them, so that a rule added to those tools binds these). Every other string a tool takes is
# worked on in the server (a filter of addresses it already found, a selector, the name of a crawl job), and the test
# of the server's schemas (tests/smoke/test_builtin_mcp.py) fails for a string argument that is in neither list.
#
# The words of a search, judged as ``web_search``'s query is: the query, and the sites the server makes ``site:``
# words of it.
_SEARCH_ARGUMENTS: dict[str, tuple[str, ...]] = {"web_search": ("query", "sites", "exclude_sites")}
# The addresses a tool will read, judged as the address of ``web_fetch`` and of ``web_crawl`` is: the owner's host
# lists and the long-address flag bind them.
_ADDRESS_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "web_scrape": ("url", "urls"),
    "web_extract": ("url",),
    "web_map": ("url",),
    "web_crawl_start": ("url",),
}


def search_arguments(tool: str) -> tuple[str, ...]:
    """The arguments of the tool ``tool`` of the server whose words go to the search engines: none for a tool that asks
    none (the pages it reads are asked for by their addresses, :func:`address_arguments`)."""
    return _SEARCH_ARGUMENTS.get(tool, ())


def address_arguments(tool: str) -> tuple[str, ...]:
    """The arguments of the tool ``tool`` of the server that hold an address it will read: none for a tool that reads
    none of those it is given (a search, the status of a crawl)."""
    return _ADDRESS_ARGUMENTS.get(tool, ())


def reads_found_pages(tool: str, arguments: JsonObject) -> bool:
    """Whether the call asks ``web_search`` to read the pages the search chose (``read`` above 0), which are addresses
    the server picks and Dot cannot check beforehand. The server takes a count written as text too; a count that cannot
    be read is taken for one that reads, as the server will refuse it anyway."""
    if tool != "web_search":
        return False
    count: JsonValue = arguments.get("read")
    if count is None or (isinstance(count, bool) and not count):
        return False
    if isinstance(count, str):
        try:
            return int(count.strip()) > 0
        except ValueError:
            return True
    if isinstance(count, int | float):
        return count > 0
    return True


def switch_of(server: str) -> BuiltinMcpSetting | None:
    """The key of ``[builtin_mcp]`` that switches the built-in server ``server``; ``None`` for a name that is none."""
    return "web_research" if server == WEB_RESEARCH else None


def builtin_servers(config: AppConfig, taken: Collection[str] = ()) -> tuple[McpServerConfig, ...]:
    """The servers that come with this Dot, each as it starts, ``enabled`` as ``config`` switches it: one that is off is
    here too, for ``dotx mcp list``, the doctor and Customize › Connectors to show. A server whose package is not
    installed is not, and neither is one whose name another server has (``taken``): that server replaces it."""
    servers: list[McpServerConfig] = []
    if web_research_installed() and WEB_RESEARCH not in taken:
        servers.append(_web_research(config.builtin_mcp.web_research))
    return tuple(servers)
