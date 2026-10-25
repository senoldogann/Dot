"""The MCP servers that come with Dot (item 583): one, ``web-research``, which searches the web and reads pages (the
package ``dotagent-web-research``, a dependency of Dot's own). Dot starts it with the servers of ``[[mcp]]``, as it
starts those, and its tools are offered and judged like any server's tools; what makes it Dot's is only that nobody had
to configure it.

It starts when the package can be imported, ``[builtin_mcp] web_research`` is on and no other server has its name: a
``[[mcp]]`` table, a plugin, a server added from another agent or one added on Customize › Connectors that is called
``web-research`` replaces it, so that the owner's own checkout of the server wins over this one.
"""

import functools
import importlib.util
import sys
from collections.abc import Collection

from pydantic import JsonValue

from dotagent.config import AppConfig, McpServerConfig, load_default_config
from dotagent.config_edit import BuiltinMcpSetting
from dotagent.core.paths import web_research_dir
from dotagent.core.types import JsonObject

WEB_RESEARCH: str = "web-research"
# The package the server is in, as it is imported (the distribution is called dotagent-web-research), and its module.
# A test that must not start the server looks for the package under a name nothing has (tests/conftest.py).
WEB_RESEARCH_PACKAGE: str = "web_scraper"
_WEB_RESEARCH_MODULE: str = "web_scraper.mcp_server"
# The tools of the server a run is offered (item 655). Its search is not among them: it is a rung of Dot's own
# web_search, asked by Dot with the query the run gave web_search, so that a run has one search and Settings › Web
# search orders it; its health check is the doctor's. A tool a later version adds is offered once it is named here,
# after Dot has judged its arguments (``_SEARCH_ARGUMENTS``, ``_ADDRESS_ARGUMENTS``).
OFFERED_TOOLS: tuple[str, ...] = (
    "web_scrape",
    "web_extract",
    "web_map",
    "web_crawl_start",
    "web_crawl_status",
    "web_crawl_results",
    "web_crawl_cancel",
)
# The server's search, which web_search's rung calls.
SEARCH_TOOL: str = "web_search"


def web_research_installed() -> bool:
    """Whether this Python can import the package that has the web-research server. The package is looked for, not
    imported: the server runs in a process of its own."""
    return importlib.util.find_spec(WEB_RESEARCH_PACKAGE) is not None


@functools.cache
def _packaged_blocked_hosts() -> frozenset[str]:
    """The hosts ``[browser] blocked_hosts`` names as Dot ships it: the cloud metadata addresses, which the server
    refuses itself."""
    return frozenset(host.lower() for host in load_default_config().browser.blocked_hosts)


def host_lists_set(config: AppConfig) -> bool:
    """Whether the owner has set a list of hosts: an ``allowed_hosts`` at all, or ``blocked_hosts`` beyond those Dot
    ships (the cloud metadata addresses, so that the list as shipped is not a list the owner set). The lists bind the
    addresses a call of the web-research server holds (item 583), not the pages the server reaches on its own: a
    search's found pages that ``read`` asks for (``tools.mcp`` judges those), and since 1.3.0 the heads of a search's
    top results read for their dates and another copy of a walled page (the archive, another outlet's), and since 1.4.0
    an address of a page's host that cannot exist, read to tell a missing page, and since 1.4.5 the download of
    Camoufox's browser, which the server does not do while a list is set (:func:`builtin_servers`, items 623, 630 and
    637)."""
    own: set[str] = {host.lower() for host in config.browser.blocked_hosts} - _packaged_blocked_hosts()
    return bool(config.browser.allowed_hosts) or bool(own)


def _web_research(enabled: bool, bound: bool) -> McpServerConfig:
    """The server as Dot starts it: the Python the daemon runs on, so that it finds the package wherever Dot is
    installed, and its data in Dot's home. ``-P`` keeps the working directory off its import path: the server starts in
    the workspace, where a run's commands write, and a file there named like a module it imports would run in it.
    ``PYTHON_DOTENV_DISABLED=1`` has python-dotenv read no ``.env`` into its environment: it started with the first one
    above the package's folder, in the app's layout Dot's own key file, and a ``.env`` that a program left in the
    workspace, the working folder, is read still; what is in the environment reaches the headless browser's driver, and
    a ``NODE_OPTIONS`` there is a file run outside the sandbox. The package reads only the working folder's now (its
    own change), and the variable closes that one too. With ``bound`` (:func:`host_lists_set`) the server reaches no
    host of its own accord: no dates from the heads of a search's results, no other copy of a walled page, since 1.4.0
    no address of a page's host that cannot exist, which tells a missing page from a real one (item 630), and since
    1.4.5 no download of Camoufox's browser from GitHub and Mozilla (item 637): a page walled to Chrome is then read in
    Camoufox only if its browser and its fingerprint model are on the Mac already. It starts in its own data folder,
    not the workspace (item 637, the security review): a program it starts with ``-m`` and no ``-P`` (Camoufox's
    fetch) would otherwise run a file of that name a run left in the workspace, outside the sandbox; the data folder is
    one of Dot's own, which a run cannot write."""
    env: dict[str, str] = {"WEB_TOOLS_DATA_DIR": str(web_research_dir()), "PYTHON_DOTENV_DISABLED": "1"}
    if bound:
        env |= {
            "WEB_TOOLS_SEARCH_RESOLVE_DATES": "off",
            "WEB_TOOLS_SCRAPE_RECOVER": "off",
            "WEB_TOOLS_SOFT_404_CHECK": "off",
            "WEB_TOOLS_BROWSER_STEALTH_FETCH": "off",
        }
    return McpServerConfig(
        name=WEB_RESEARCH,
        command=sys.executable,
        args=["-P", "-m", _WEB_RESEARCH_MODULE],
        env=env,
        enabled=enabled,
        cwd=str(web_research_dir()),
        allow=list(OFFERED_TOOLS),
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


# The Internet Archive's copy of a page, which ``web_scrape`` reads with ``archive: true``: a site the call reaches
# whatever the address it names (item 623).
ARCHIVE_URL: str = "https://web.archive.org/"


# The words the server reads as false (Pydantic's lax booleans): any other value of ``archive`` may be read as true.
_ARCHIVE_OFF: frozenset[str] = frozenset({"0", "false", "f", "no", "n", "off"})


def reads_the_archive(tool: str, arguments: JsonObject) -> bool:
    """Whether the call may ask ``web_scrape`` for the Internet Archive's copy of the page. The server reads
    ``archive`` as Pydantic's lax booleans do (``1``, ``1.0``, ``"t"``, ``"y"`` are true), so only a plain off is off:
    absent, ``false``, zero, or a word it reads as false; anything else is taken for on, as a count of ``read`` that
    cannot be read is (the review of item 623: the check failed open)."""
    if tool != "web_scrape":
        return False
    archive: JsonValue = arguments.get("archive")
    if archive is None or archive is False:
        return False
    if isinstance(archive, int | float) and not isinstance(archive, bool):
        return archive != 0
    if isinstance(archive, str):
        return archive.strip().lower() not in _ARCHIVE_OFF
    return True


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
        servers.append(_web_research(config.builtin_mcp.web_research, host_lists_set(config)))
    return tuple(servers)
