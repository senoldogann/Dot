"""The MCP server that comes with Dot (item 583): when Dot starts it, what replaces it, what it is started with and
where its switch is written. The server itself, started for real, is ``tests/smoke/test_builtin_mcp.py``.

The suite looks for the server's package under a name nothing has (``tests/conftest.py``), so that no runtime a test
builds starts it; the tests here that need it ask for the real name back with ``web_research_comes``."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from web_scraper.web_tools.search import normalize_domains

from dotagent.browser.safety import url_flags
from dotagent.cli_mcp import render_report
from dotagent.config import AppConfig, McpServerConfig, load_config, load_default_config
from dotagent.config_edit import set_builtin_server
from dotagent.core.errors import ConfigError, ToolInputError
from dotagent.core.paths import web_research_dir, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.doctor import mcp_checks
from dotagent.evals.runner import eval_config
from dotagent.mcp import builtin
from dotagent.mcp.builtin import (
    WEB_RESEARCH,
    address_arguments,
    builtin_servers,
    host_lists_set,
    reads_found_pages,
    reads_the_archive,
    search_arguments,
    switch_of,
)
from dotagent.mcp.hub import ServerReport, server_folder
from dotagent.mcp.naming import exposed_name
from dotagent.mcp.types import McpTool, ToolHints
from dotagent.runtime import _dot_paths
from dotagent.tools import mcp as tools_mcp
from dotagent.tools import web as tools_web
from dotagent.tools.browser import checked_url
from dotagent.tools.mcp import checked_builtin_call
from dotagent.tools.web import QUERY_MAX_CHARS, SearchArgs, checked_address, search_flags


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of this test's own, which Dot's data folder for the server is under."""
    folder: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(folder))
    return folder


def _config(web_research: bool) -> AppConfig:
    """The packaged defaults with the switch as given."""
    defaults: AppConfig = load_default_config()
    return defaults.model_copy(
        update={"builtin_mcp": defaults.builtin_mcp.model_copy(update={"web_research": web_research})}
    )


def _taken(name: str) -> McpServerConfig:
    """A server of someone's own, as a ``[[mcp]]`` table would make it."""
    return McpServerConfig(name=name, command="uv", args=["run", "web-research-mcp"])


def test_the_packaged_defaults_switch_the_server_on() -> None:
    assert load_default_config().builtin_mcp.web_research is True


def test_the_switch_is_in_a_table_that_can_stand_beside_the_mcp_tables(tmp_path: Path) -> None:
    """``[mcp]`` itself cannot hold a key: TOML has no table of that name beside the ``[[mcp]]`` tables."""
    file: Path = tmp_path / "config.toml"
    file.write_text(
        '[[mcp]]\nname = "notes"\ncommand = "notes-server"\n\n[builtin_mcp]\nweb_research = false\n', encoding="utf-8"
    )
    config = load_config(file)
    assert [server.name for server in config.mcp] == ["notes"]
    assert config.builtin_mcp.web_research is False


def test_the_server_comes_when_its_package_is_installed_and_no_other_server_has_its_name(
    web_research_comes: None, home: Path
) -> None:
    (server,) = builtin_servers(load_default_config())
    assert (server.name, server.enabled) == (WEB_RESEARCH, True)
    assert server.transport_kind() == "stdio"
    assert (server.startup_timeout_s, server.call_timeout_s) == (60.0, 120.0)
    assert server.allow == []


def test_the_command_is_the_python_the_daemon_runs_on_and_its_data_is_kept_in_dots_home(
    web_research_comes: None, home: Path
) -> None:
    """The Python of the daemon is the one that has the package, wherever Dot is installed; ``-P`` leaves the working
    directory, the workspace a run's commands write in, out of what the server imports from."""
    (server,) = builtin_servers(load_default_config())
    assert server.command == sys.executable
    assert server.args == ["-P", "-m", "web_scraper.mcp_server"]
    assert server.env == {"WEB_TOOLS_DATA_DIR": str(home / "web-research"), "PYTHON_DOTENV_DISABLED": "1"}
    assert web_research_dir() == home / "web-research"


def test_without_its_package_nothing_comes(home: Path) -> None:
    """The suite's own state: the package is not found, so a runtime built by a test starts no server of its own."""
    assert builtin_servers(load_default_config()) == ()


def test_the_package_is_found_where_it_is_installed(web_research_comes: None) -> None:
    assert builtin.web_research_installed()


def test_a_server_of_the_same_name_replaces_it(web_research_comes: None, home: Path) -> None:
    assert builtin_servers(load_default_config(), taken={WEB_RESEARCH}) == ()
    assert [server.name for server in builtin_servers(load_default_config(), taken={"notes", "docs"})] == [WEB_RESEARCH]


def test_a_config_entry_of_the_name_is_the_owners_and_the_one_dot_starts_is_theirs(
    web_research_comes: None, home: Path
) -> None:
    """What the runtime gives ``taken`` is every name the config, the plugins and the owner's records hold: the owner's
    own checkout of the server, started their way, is the only one that starts."""
    ours = _taken(WEB_RESEARCH)
    config = load_default_config().model_copy(update={"mcp": [ours]})
    started = [*config.mcp, *builtin_servers(config, {server.name for server in config.mcp})]
    assert started == [ours]


def test_the_switch_turns_it_off_and_it_is_still_listed_for_the_screens_that_show_the_switch(
    web_research_comes: None, home: Path
) -> None:
    (server,) = builtin_servers(_config(False))
    assert (server.name, server.enabled) == (WEB_RESEARCH, False)


def test_the_switch_of_a_server_is_the_key_of_the_builtin_table_named_for_it() -> None:
    assert switch_of(WEB_RESEARCH) == "web_research"
    assert switch_of("notes") is None


def test_the_switch_is_written_under_its_table_beside_the_owners_servers_and_the_rest_stays(tmp_path: Path) -> None:
    file: Path = tmp_path / "config.toml"
    original: str = '# mine\n[[mcp]]\nname = "notes"\ncommand = "notes-server"\n\n[models]\nmain = "ollama:x"\n'
    file.write_text(original, encoding="utf-8")
    set_builtin_server(file, "web_research", False)
    text: str = file.read_text(encoding="utf-8")
    assert text.startswith(original.rstrip("\n"))
    assert text.endswith("\n[builtin_mcp]\nweb_research = false\n")
    assert load_config(file).builtin_mcp.web_research is False
    set_builtin_server(file, "web_research", True)
    assert file.read_text(encoding="utf-8").count("web_research") == 1
    assert load_config(file).builtin_mcp.web_research is True
    parsed = tomllib.loads(file.read_text(encoding="utf-8"))
    assert parsed["mcp"] == [{"name": "notes", "command": "notes-server"}]
    assert parsed["models"] == {"main": "ollama:x"}


def test_a_config_file_that_cannot_be_changed_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    file: Path = tmp_path / "config.toml"
    file.write_text("this is [not = valid toml\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        set_builtin_server(file, "web_research", False)
    assert file.read_text(encoding="utf-8") == "this is [not = valid toml\n"


def test_a_measurement_leaves_the_server_out(web_research_comes: None, home: Path) -> None:
    """The eval's configuration has no MCP server of the user's, and none that comes with Dot: its tools would reach the
    web and change what is measured."""
    measured = eval_config(load_default_config())
    assert measured.mcp == [] and measured.builtin_mcp.web_research is False
    assert builtin_servers(measured)[0].enabled is False


def test_the_data_folder_of_the_server_is_what_dot_loads_and_runs_so_a_command_cannot_change_it(
    home: Path,
) -> None:
    """The server, started with the daemon, reads its crawl jobs back from this folder: it is among the paths a run's
    commands may read and not write (``dot_paths``)."""
    assert web_research_dir() in _dot_paths(load_default_config())


def _report(**fields: object) -> ServerReport:
    base: dict[str, object] = {
        "server": WEB_RESEARCH,
        "enabled": True,
        "listed": 0,
        "tools": (),
        "error": None,
        "detail": {},
        "log": "/logs/mcp-web-research.log",
    }
    return ServerReport.model_validate({**base, **fields})


def test_mcp_list_marks_the_server_that_comes_with_dot_and_says_which_setting_switches_it() -> None:
    assert render_report(_report(), built_in=True).splitlines()[0] == (
        "web-research (built-in): ready, 0 of 0 tools offered to the model"
    )
    assert render_report(_report(enabled=False), built_in=True) == (
        "web-research (built-in): disabled ([builtin_mcp] web_research = false)"
    )
    assert render_report(_report(error="it did not start"), built_in=True).splitlines()[0] == (
        "web-research (built-in): FAILED"
    )
    # A server of the owner's own under that name is not marked.
    assert render_report(_report()).startswith("web-research: ready")
    assert render_report(_report(enabled=False)) == "web-research: disabled (enabled = false)"


async def test_the_doctor_has_a_row_for_the_server_that_comes_with_dot(web_research_comes: None, home: Path) -> None:
    (row,) = await mcp_checks(list(builtin_servers(_config(True))), 5.0, frozenset({WEB_RESEARCH}))
    assert (row.name, row.ok, row.required) == ("mcp web-research", True, False)
    assert row.detail.startswith(f"built in, {sys.executable};")
    assert os.access(sys.executable, os.X_OK)
    (switched_off,) = await mcp_checks(list(builtin_servers(_config(False))), 5.0, frozenset({WEB_RESEARCH}))
    assert switched_off.ok and switched_off.detail == "switched off ([builtin_mcp] web_research = false)"


# ---------------------------------------------------------------------------------------- what binds the server's tools


def _tool(name: str) -> McpTool:
    """A tool of the server as it lists it: the six that read declare themselves read-only."""
    reads: bool = name not in {"web_crawl_start", "web_crawl_cancel"}
    return McpTool(
        server=WEB_RESEARCH,
        remote_name=name,
        exposed_name=exposed_name(WEB_RESEARCH, name),
        description=f"the tool {name}",
        input_schema={"type": "object", "properties": {}},
        hints=ToolHints(read_only=reads, destructive=None if reads else False, open_world=True),
        startup_timeout_s=60.0,
        call_timeout_s=120.0,
    )


def _lists(allowed: list[str] | None = None, blocked: list[str] | None = None) -> AppConfig:
    """The packaged defaults with the owner's host lists, as ``[browser] allowed_hosts`` and ``blocked_hosts`` say."""
    defaults: AppConfig = load_default_config()
    browser = defaults.browser.model_copy(update={"allowed_hosts": allowed or [], "blocked_hosts": blocked or []})
    return defaults.model_copy(update={"browser": browser})


# The tools that take an address, with the argument that holds it.
ADDRESSED: tuple[tuple[str, str], ...] = (
    ("web_scrape", "url"),
    ("web_extract", "url"),
    ("web_map", "url"),
    ("web_crawl_start", "url"),
)


def test_the_arguments_that_hold_an_address_are_known_for_each_tool_that_reads_one() -> None:
    assert {name: address_arguments(name) for name in ("web_scrape", "web_extract", "web_map", "web_crawl_start")} == {
        "web_scrape": ("url", "urls"),
        "web_extract": ("url",),
        "web_map": ("url",),
        "web_crawl_start": ("url",),
    }
    for none in ("web_search", "web_crawl_status", "web_crawl_results", "web_crawl_cancel", "no_such_tool"):
        assert address_arguments(none) == ()


@pytest.mark.parametrize(("tool", "argument"), ADDRESSED)
def test_a_host_the_owner_blocked_is_refused_as_web_fetch_refuses_it(tool: str, argument: str) -> None:
    """Item 583's review: the owner's host lists bound ``web_fetch``, ``web_crawl`` and the browser, and not the tools
    of the server that comes with Dot, which was on in every install."""
    config: AppConfig = _lists(blocked=["blocked.example"])
    url: str = "https://www.blocked.example/page"
    with pytest.raises(ToolInputError) as expected:
        checked_url(url, config)
    with pytest.raises(ToolInputError) as raised:
        checked_builtin_call(_tool(tool), {argument: url}, config)
    assert raised.value.message == expected.value.message
    assert "blocked.example" in raised.value.message
    # Another host, and the same call with no list set, pass.
    assert checked_builtin_call(_tool(tool), {argument: "https://example.org/"}, config)[0][argument] == (
        "https://example.org/"
    )
    assert checked_builtin_call(_tool(tool), {argument: url}, _lists())[0][argument] == url


@pytest.mark.parametrize(("tool", "argument"), ADDRESSED)
def test_a_host_outside_allowed_hosts_is_refused_as_web_fetch_refuses_it(tool: str, argument: str) -> None:
    config: AppConfig = _lists(allowed=["docs.python.org"])
    with pytest.raises(ToolInputError) as expected:
        checked_url("https://example.org/", config)
    with pytest.raises(ToolInputError) as raised:
        checked_builtin_call(_tool(tool), {argument: "https://example.org/"}, config)
    assert raised.value.message == expected.value.message
    assert "allowed_hosts" in raised.value.message
    for allowed in ("https://docs.python.org/3/", "https://peps.docs.python.org/"):
        assert checked_builtin_call(_tool(tool), {argument: allowed}, config)[0][argument] == allowed


def test_every_address_of_a_batch_is_checked_and_one_refused_refuses_the_call() -> None:
    config: AppConfig = _lists(blocked=["blocked.example"])
    batch: JsonObject = {"urls": ["https://example.org/a", "https://blocked.example/b", "https://example.net/c"]}
    with pytest.raises(ToolInputError) as raised:
        checked_builtin_call(_tool("web_scrape"), batch, config)
    assert "blocked.example" in raised.value.message
    fine: JsonObject = {"urls": ["https://example.org/a", " example.net/c "], "max_chars": 2000}
    checked, flags = checked_builtin_call(_tool("web_scrape"), fine, config)
    assert checked == {"urls": ["https://example.org/a", "https://example.net/c"], "max_chars": 2000}
    assert flags == ()


def test_a_long_address_earns_the_flag_a_long_address_earns_in_web_fetch() -> None:
    """``exfiltration``: the policy refuses it in every mode but yolo, whichever tool carries it."""
    long: str = "https://example.org/?q=" + "a" * 2400
    assert url_flags(long) == ("exfiltration",)
    for tool, argument in ADDRESSED:
        assert checked_builtin_call(_tool(tool), {argument: long}, _lists())[1] == ("exfiltration",)
    checked, flags = checked_builtin_call(_tool("web_scrape"), {"urls": ["https://example.org/", long]}, _lists())
    assert flags == ("exfiltration",) and checked["urls"] == ["https://example.org/", long]
    assert checked_builtin_call(_tool("web_scrape"), {"url": "https://example.org/?q=short"}, _lists())[1] == ()


def test_the_server_is_handed_the_address_dot_checked_not_the_one_it_was_given() -> None:
    """A bare host gets its scheme as Dot's own tools give it, so that the server cannot read it another way."""
    checked, _ = checked_builtin_call(_tool("web_scrape"), {"url": "  example.org/page  ", "links": False}, _lists())
    assert checked == {"url": "https://example.org/page", "links": False}


# ------------------------------------------------------------------ what leaves in a search or an address is Dot's own

# Keys in the shapes Dot knows (``policy.elements.looks_like_key``), put together here so that no file holds one whole.
KEYS: tuple[str, ...] = (
    "sk-" + "a1b2c3d4" * 4,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "a1B2c3D4e5" * 4,
    "xoxb-" + "1234567890-abcdef",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
)


def test_the_tools_of_the_server_and_those_of_dot_are_judged_by_the_same_functions() -> None:
    """The structure the second review asked for: the rule of ``web_search`` on its words and the rule of ``web_fetch``
    and ``web_crawl`` on an address are each one function, which the server's tools are judged by as well, so that a
    rule added to Dot's own tools binds them too (tests/smoke/test_builtin_mcp.py changes a rule and watches both)."""
    assert tools_mcp.search_flags is tools_web.search_flags
    assert tools_mcp.checked_address is tools_web.checked_address


def test_the_arguments_whose_words_go_to_the_search_engines_are_known_for_the_search_only() -> None:
    assert search_arguments("web_search") == ("query", "sites", "exclude_sites")
    for none in (
        "web_scrape",
        "web_extract",
        "web_map",
        "web_crawl_start",
        "web_crawl_status",
        "web_crawl_results",
        "web_crawl_cancel",
        "no_such_tool",
    ):
        assert search_arguments(none) == ()


@pytest.mark.parametrize("key", KEYS)
def test_a_key_in_the_query_earns_the_flag_web_search_gives_it(key: str) -> None:
    """The review's finding: Dot's own ``web_search`` flags a query that holds a key (``exfiltration``, which the policy
    refuses in every mode but yolo), and the search of the server, on in every install, was a read with no flag."""
    query: str = f"what is {key} used for"
    assert search_flags(query) == ("exfiltration",)
    asked: JsonObject = {"query": query, "limit": 5}
    assert checked_builtin_call(_tool("web_search"), asked, _lists()) == (asked, ("exfiltration",))


@pytest.mark.parametrize("argument", ["sites", "exclude_sites"])
@pytest.mark.parametrize("key", KEYS[:3])
def test_a_key_in_the_sites_of_a_search_earns_the_flag_too(argument: str, key: str) -> None:
    """The server makes ``site:`` words of the sites and sends them with the query. A key written as a domain is a
    domain to the server's own check (shown below), so it is Dot that must read it."""
    domain: str = f"{key}.example.com"
    if key == KEYS[0]:
        assert normalize_domains([domain], argument) == [domain]
    asked: JsonObject = {"query": "sqlite fts5", argument: ["docs.python.org", domain]}
    assert checked_builtin_call(_tool("web_search"), asked, _lists()) == (asked, ("exfiltration",))


def test_a_plain_search_earns_no_flag_and_is_handed_on_as_it_is() -> None:
    plain: JsonObject = {
        "query": "sqlite fts5 virtual tables",
        "sites": ["docs.python.org"],
        "exclude_sites": ["example.com"],
        "depth": "fast",
        "limit": 5,
    }
    assert checked_builtin_call(_tool("web_search"), plain, _lists()) == (plain, ())
    assert search_flags("sqlite fts5 virtual tables") == ()
    # Words that only look like the start of a key are none.
    for query in ("what does the sk- prefix of a key mean", "sk-short", "AKIA is a prefix", "ghp_ tokens"):
        assert checked_builtin_call(_tool("web_search"), {"query": query}, _lists()) == ({"query": query}, ())


def test_a_query_longer_than_web_search_takes_is_refused_as_it_refuses_it() -> None:
    longest: str = "a" * QUERY_MAX_CHARS
    assert QUERY_MAX_CHARS == 400 and SearchArgs(query=longest).query == longest
    with pytest.raises(ValidationError):
        SearchArgs(query=longest + "a")
    assert checked_builtin_call(_tool("web_search"), {"query": longest}, _lists())[1] == ()
    for asked in ({"query": longest + "a"}, {"query": "sqlite", "sites": [longest + ".com"]}):
        with pytest.raises(ToolInputError) as raised:
            checked_builtin_call(_tool("web_search"), asked, _lists())
        assert "400" in raised.value.message and longest not in str(raised.value)


@pytest.mark.parametrize(("tool", "argument"), ADDRESSED)
def test_a_key_in_the_query_string_of_an_address_earns_the_flag_it_earns_in_web_fetch(tool: str, argument: str) -> None:
    url: str = "https://example.org/page?token=" + KEYS[2]
    assert url_flags(url) == ("exfiltration",)
    assert checked_address(url, _lists()) == (url, ("exfiltration",))
    checked, flags = checked_builtin_call(_tool(tool), {argument: url}, _lists())
    assert (checked[argument], flags) == (url, ("exfiltration",))


def test_a_key_in_one_address_of_a_batch_flags_the_call() -> None:
    url: str = "https://example.org/page?token=" + KEYS[2]
    assert checked_builtin_call(_tool("web_scrape"), {"urls": ["https://example.org/", url]}, _lists())[1] == (
        "exfiltration",
    )


@pytest.mark.parametrize("read", [1, 3, True, 2.0, "2", " 5 ", "many"])
@pytest.mark.parametrize("lists", [{"allowed": ["docs.python.org"]}, {"blocked": ["blocked.example"]}])
def test_a_search_that_reads_the_pages_it_found_is_refused_while_a_host_list_is_set(
    read: object, lists: dict[str, list[str]]
) -> None:
    config: AppConfig = _lists(**lists)
    with pytest.raises(ToolInputError) as raised:
        checked_builtin_call(_tool("web_search"), {"query": "sqlite fts5", "read": read}, config)  # type: ignore[dict-item]
    assert "read" in raised.value.message and "allowed_hosts" in raised.value.message
    assert reads_found_pages("web_search", {"read": read})  # type: ignore[dict-item]


@pytest.mark.parametrize("arguments", [{"query": "x"}, {"query": "x", "read": 0}, {"query": "x", "read": None}])
def test_a_search_that_reads_nothing_is_not_refused_by_a_host_list(arguments: JsonObject) -> None:
    config: AppConfig = _lists(allowed=["docs.python.org"], blocked=["blocked.example"])
    assert checked_builtin_call(_tool("web_search"), arguments, config) == (arguments, ())
    assert not reads_found_pages("web_search", arguments)
    assert not reads_found_pages("web_search", {"read": False}) and not reads_found_pages("web_search", {"read": "0"})


def test_a_search_may_read_the_pages_it_found_when_no_host_list_is_set() -> None:
    arguments: JsonObject = {"query": "x", "read": 3}
    assert checked_builtin_call(_tool("web_search"), arguments, _lists()) == (arguments, ())
    assert reads_found_pages("web_search", arguments)
    assert not reads_found_pages("web_scrape", {"read": 3})


def test_the_other_tools_are_handed_on_as_they_are() -> None:
    config: AppConfig = _lists(allowed=["docs.python.org"], blocked=["blocked.example"])
    for tool, arguments in (
        ("web_crawl_status", {"job_id": "a-job"}),
        ("web_crawl_results", {"job_id": "a-job", "offset": 0, "limit": 20}),
        ("web_crawl_cancel", {"job_id": "a-job"}),
    ):
        assert checked_builtin_call(_tool(tool), arguments, config) == (arguments, ())


def test_the_hosts_dot_ships_blocked_are_no_list_the_owner_set() -> None:
    """``[browser] blocked_hosts`` ships with the cloud metadata addresses (which the server refuses itself): a search
    that reads the pages it found is not refused in every install for that."""
    shipped: AppConfig = load_default_config()
    assert shipped.browser.blocked_hosts and not shipped.browser.allowed_hosts
    assert not host_lists_set(shipped)
    asked: JsonObject = {"query": "sqlite fts5", "read": 3}
    assert checked_builtin_call(_tool("web_search"), asked, shipped) == (asked, ())
    assert not host_lists_set(_lists())


def test_a_list_the_owner_set_is_a_list() -> None:
    shipped: AppConfig = load_default_config()
    assert host_lists_set(_lists(allowed=["docs.python.org"]))
    assert host_lists_set(_lists(blocked=["blocked.example"]))
    added: AppConfig = shipped.model_copy(
        update={
            "browser": shipped.browser.model_copy(
                update={"blocked_hosts": [*shipped.browser.blocked_hosts, "Blocked.Example"]}
            )
        }
    )
    assert host_lists_set(added)
    # The shipped hosts written in other letters are the shipped ones.
    same: AppConfig = shipped.model_copy(
        update={
            "browser": shipped.browser.model_copy(
                update={"blocked_hosts": [host.upper() for host in shipped.browser.blocked_hosts]}
            )
        }
    )
    assert not host_lists_set(same)


def test_the_server_reads_no_page_of_its_own_while_the_owner_has_a_list_of_sites(
    web_research_comes: None, home: Path
) -> None:
    """Item 623: since 1.3.0 the server reads the heads of a search's results for their dates and looks for another copy
    of a walled page, which no list of the owner's binds; with a list set, both are off. Item 630: since 1.4.0 it asks a
    page's host for an address that cannot exist, to tell a missing page; with a list set, that is off too. Item 637:
    since 1.4.5 it downloads Camoufox's browser from GitHub and Mozilla by itself; with a list set, it does not."""
    config: AppConfig = load_default_config()
    assert "WEB_TOOLS_SEARCH_RESOLVE_DATES" not in builtin_servers(config)[0].env
    assert "WEB_TOOLS_SOFT_404_CHECK" not in builtin_servers(config)[0].env
    assert "WEB_TOOLS_BROWSER_STEALTH_FETCH" not in builtin_servers(config)[0].env
    allowed: AppConfig = config.model_copy(
        update={"browser": config.browser.model_copy(update={"allowed_hosts": ["example.org"]})}
    )
    env: dict[str, str] = dict(builtin_servers(allowed)[0].env)
    assert env["WEB_TOOLS_SEARCH_RESOLVE_DATES"] == "off" and env["WEB_TOOLS_SCRAPE_RECOVER"] == "off"
    assert env["WEB_TOOLS_SOFT_404_CHECK"] == "off" and env["WEB_TOOLS_BROWSER_STEALTH_FETCH"] == "off"
    more: list[str] = [*config.browser.blocked_hosts, "news.example"]
    blocked: AppConfig = config.model_copy(
        update={"browser": config.browser.model_copy(update={"blocked_hosts": more})}
    )
    assert builtin_servers(blocked)[0].env["WEB_TOOLS_SCRAPE_RECOVER"] == "off"
    fewer: AppConfig = config.model_copy(update={"browser": config.browser.model_copy(update={"blocked_hosts": []})})
    assert "WEB_TOOLS_SCRAPE_RECOVER" not in builtin_servers(fewer)[0].env


def test_the_archive_is_judged_as_a_site_the_call_reaches_while_a_host_list_is_set() -> None:
    """Item 623, the review: ``archive: true`` reaches web.archive.org whatever address the call names; while a list is
    set, that site is judged against it, and the call is refused where the list leaves it out."""
    arguments: JsonObject = {"url": "https://docs.python.org/3/", "archive": True}
    with pytest.raises(ToolInputError):
        checked_builtin_call(_tool("web_scrape"), arguments, _lists(allowed=["docs.python.org"]))
    allowed: AppConfig = _lists(allowed=["docs.python.org", "web.archive.org"])
    assert checked_builtin_call(_tool("web_scrape"), arguments, allowed)[0]["archive"] is True
    with pytest.raises(ToolInputError):
        checked_builtin_call(_tool("web_scrape"), {**arguments, "archive": "true"}, _lists(blocked=["web.archive.org"]))
    # Every value the server reads as true is judged too (the review: the check failed open), and only a plain off is
    # off.
    for on in (1, 1.0, "t", "y", "True", " yes ", "on", []):
        with pytest.raises(ToolInputError):
            checked_builtin_call(_tool("web_scrape"), {**arguments, "archive": on}, _lists(allowed=["docs.python.org"]))
    for off in (False, 0, 0.0, "0", "false", "F", "no", "n", "off", None):
        assert not reads_the_archive("web_scrape", {**arguments, "archive": off})
    # With no list, and without archive, nothing changes.
    assert checked_builtin_call(_tool("web_scrape"), arguments, _lists())[0]["archive"] is True
    plain: JsonObject = {"url": "https://docs.python.org/3/"}
    assert checked_builtin_call(_tool("web_scrape"), plain, _lists(allowed=["docs.python.org"]))[0] == plain


def test_the_installed_server_reads_the_switches_dot_sets(
    web_research_comes: None, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 623, the review: the names Dot sets are the ones the installed server reads, so that a later version that
    renamed one does not quietly let the lists stop binding (the missing-page check's too, item 630, and the download of
    Camoufox's browser, item 637)."""
    from web_scraper.web_tools.settings import WebToolsSettings  # type: ignore[import-untyped]

    config: AppConfig = load_default_config()
    bound: AppConfig = config.model_copy(
        update={"browser": config.browser.model_copy(update={"allowed_hosts": ["example.org"]})}
    )
    for name, value in builtin_servers(bound)[0].env.items():
        monkeypatch.setenv(name, value)
    settings = WebToolsSettings.from_env()
    assert settings.search_resolve_dates == "off" and settings.scrape_recover == "off"
    assert settings.soft_404_check is False
    assert settings.browser_stealth_fetch is False


def test_the_server_starts_in_its_own_folder_and_a_file_a_run_leaves_in_the_workspace_is_not_on_its_path(
    web_research_comes: None, home: Path
) -> None:
    """Item 637, the security review: Camoufox's fetch is ``python -m camoufox fetch`` with no ``-P``, so it found its
    module in the folder it started in, which was the run's workspace: a ``camoufox.py`` a run wrote there ran as the
    fetch, outside the sandbox. The server starts in its data folder now, one of Dot's own, which a run cannot write,
    and which Dot makes when it is not there yet."""
    workspace: Path = workspace_dir()
    workspace.mkdir(parents=True)
    server: McpServerConfig = builtin_servers(load_default_config())[0]
    assert server.cwd == str(web_research_dir())
    assert not web_research_dir().exists()
    folder: Path = server_folder(server)
    assert folder == web_research_dir() and folder.is_dir() and folder.is_relative_to(home)
    assert not folder.is_relative_to(workspace)
    # A module a run plants in the workspace is what a program started there with -m or -c imports, and not what one
    # started in the server's folder does (a name no installed package has, so that only the planted file can answer).
    (workspace / "dot_planted_probe.py").write_text("raise SystemExit('planted')\n", encoding="utf-8")
    look: list[str] = [
        sys.executable,
        "-c",
        "import importlib.util as u; spec = u.find_spec('dot_planted_probe'); print(spec.origin if spec else 'none')",
    ]
    # PYTHONSAFEPATH, where whoever runs the tests sets it, keeps the folder off the path as -P does: not this test's.
    plain: dict[str, str] = {name: value for name, value in os.environ.items() if name != "PYTHONSAFEPATH"}
    from_workspace: str = subprocess.run(
        look, cwd=workspace, env=plain, capture_output=True, text=True, check=True
    ).stdout
    from_folder: str = subprocess.run(look, cwd=folder, env=plain, capture_output=True, text=True, check=True).stdout
    assert Path(from_workspace.strip()).resolve() == (workspace / "dot_planted_probe.py").resolve()
    assert from_folder.strip() == "none"


def test_a_server_names_a_whole_folder_of_its_own_and_only_a_command_has_one(home: Path, tmp_path: Path) -> None:
    """Item 637: ``cwd`` is a whole path, for a server Dot starts; a folder outside Dot's home is not made by Dot."""
    elsewhere: Path = tmp_path / "not-there"
    server: McpServerConfig = McpServerConfig(name="mine", command="mine-server", cwd=str(elsewhere))
    assert server_folder(server) == elsewhere and not elsewhere.exists()
    assert server_folder(McpServerConfig(name="mine", command="mine-server")).name != "not-there"
    with pytest.raises(ValidationError, match="whole path"):
        McpServerConfig(name="mine", command="mine-server", cwd="relative/folder")
    with pytest.raises(ValidationError, match="cwd belongs to a command"):
        McpServerConfig(name="mine", url="https://mcp.example/sse", cwd="/tmp")
    # A folder that only seems to be in Dot's home, by "..", is judged where it is, and not made.
    outside: Path = home / ".." / "outside-dots-home"
    assert server_folder(McpServerConfig(name="mine", command="mine-server", cwd=str(outside))) == outside
    assert not outside.resolve().exists()
    # And so is one behind a link in Dot's home that leads out of it.
    home.mkdir(parents=True, exist_ok=True)
    (home / "link-out").symlink_to(tmp_path / "led-to", target_is_directory=True)
    linked: Path = home / "link-out" / "folder"
    assert server_folder(McpServerConfig(name="mine", command="mine-server", cwd=str(linked))) == linked
    assert not (tmp_path / "led-to").exists()
