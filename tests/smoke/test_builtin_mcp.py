"""The MCP server that comes with Dot, started for real (item 583): the web-research server of the package
``dotagent-web-research``, which every install has and which is on until the owner switches it off.

Nothing here searches or fetches a page: the server is started, its tools are listed, and the one call that is made
asks for a crawl job that does not exist, which reaches no site (the calls that the owner's host lists refuse, or that
are flagged, are prepared and never run, and one that is run goes to a stand-in of the server). What the tests check is
what Dot does with the server: it starts with the daemon's own Python and keeps its data in Dot's home, its tools are
offered and judged like any MCP server's, the owner's host lists and the long-address flag bind its addresses, it takes
no ``.env`` into its environment, a server of the owner's with its name replaces it, and the switch
(``[builtin_mcp] web_research``, on Customize › Connectors and in ``dotx mcp list``) turns it off and on.

The suite looks for the package under a name nothing has (``tests/conftest.py``); ``web_research_comes`` asks for the
real one back."""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp.client.stdio import get_default_environment
from mcp.types import CallToolResult, TextContent
from pydantic import JsonValue

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.config import AppConfig, McpServerConfig, load_config, load_default_config
from dotagent.core.errors import ToolInputError
from dotagent.core.paths import connected_servers_path, ensure_layout, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.mcp import builtin
from dotagent.mcp.builtin import OFFERED_TOOLS, address_arguments, builtin_servers, search_arguments
from dotagent.mcp.connected import keep_connected
from dotagent.mcp.hub import open_hub
from dotagent.mcp.launch import server_environment
from dotagent.mcp.types import McpTool
from dotagent.policy.engine import auto_review
from dotagent.policy.types import ActionInfo, Flag
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.tools import mcp as tools_mcp
from dotagent.tools import web as tools_web
from dotagent.tools.context import ToolContext
from dotagent.tools.mcp import classify_mcp_call

pytestmark = pytest.mark.usefixtures("web_research_comes")

FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
TOKEN: str = "builtin-mcp-token"
PORT: int = 8765
ORIGIN: str = f"http://127.0.0.1:{PORT}"
SERVER: str = "web-research"
# What the server lists: nine tools, of which a run is offered seven (item 655), in the order Dot names them. Its search
# is a rung of Dot's own web_search, which Dot asks, and its health check (since 1.3.0, the state of its search
# providers) is the doctor's. How the policy judges each offered tool: the five that only read are reads, a crawl that
# is queued may reach every site its pages link to, and one that is cancelled is stopped.
SERVER_LISTS: tuple[str, ...] = (
    "web_search",
    "web_scrape",
    "web_map",
    "web_extract",
    "web_health",
    "web_crawl_start",
    "web_crawl_status",
    "web_crawl_results",
    "web_crawl_cancel",
)
LISTED: int = len(SERVER_LISTS)
TOOLS: tuple[str, ...] = OFFERED_TOOLS
NOT_OFFERED: tuple[str, ...] = ("web_search", "web_health")
READS: frozenset[str] = frozenset(TOOLS) - {"web_crawl_start", "web_crawl_cancel"}
# The tools that take no string, which the test of the strings below does not meet.
NO_STRINGS: frozenset[str] = frozenset()
ANSWER_S: float = 90.0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _exists(path: Path) -> bool:
    return path.exists()


@dataclass(frozen=True)
class _World:
    client: httpx.AsyncClient
    runtime: Runtime
    home: Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home, logs and Chrome folder of this test's own, as every test of Dot has."""
    folder: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(folder))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    folder.mkdir()
    return folder


@asynccontextmanager
async def _runtime(home: Path, config: str = "") -> AsyncIterator[Runtime]:
    """A runtime over a home whose config.toml says ``config``, closed again when the block ends."""
    await asyncio.to_thread(_write, home / "config.toml", config)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


@asynccontextmanager
async def _world(home: Path, config: str = "") -> AsyncIterator[_World]:
    """The API of a daemon over such a runtime, called in this process."""
    async with _runtime(home, config) as runtime:
        manager: RunManager = RunManager(runtime)
        try:
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=TOKEN,
                host="127.0.0.1",
                port=PORT,
                started_at=time.time(),
                stopping=asyncio.Event(),
            )
            transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
            async with httpx.AsyncClient(
                transport=transport, base_url=ORIGIN, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=ANSWER_S
            ) as client:
                yield _World(client, runtime, home)
        finally:
            await manager.shutdown()


def _tool(runtime: Runtime, name: str) -> McpTool:
    return next(tool for tool in runtime.mcp.tools if tool.server == SERVER and tool.remote_name == name)


def _listed(answer: httpx.Response) -> dict[str, JsonObject]:
    assert answer.status_code == 200, answer.text
    return {server["name"]: server for server in answer.json()["servers"]}


async def test_a_daemon_with_no_config_starts_the_server_and_offers_its_tools(home: Path) -> None:
    async with _runtime(home) as runtime:
        assert runtime.mcp.failures == ()
        assert runtime.mcp.connected() == (SERVER,)
        assert runtime.builtin_names == frozenset({SERVER})
        assert tuple(tool.remote_name for tool in runtime.mcp.tools) == TOOLS
        assert all(f"mcp__{SERVER}__{name}" in runtime.registry.tools for name in TOOLS)
        assert not any(f"mcp__{SERVER}__{name}" in runtime.registry.tools for name in NOT_OFFERED)
        started = runtime.mcp.configs()[SERVER]
        assert started.command == sys.executable
        assert started.args[-2:] == ["-m", "web_scraper.mcp_server"]
        assert started.env == {"WEB_TOOLS_DATA_DIR": str(home / "web-research"), "PYTHON_DOTENV_DISABLED": "1"}
        # What the server writes to its standard error as it starts goes to a log of its own, as any server's does.
        log: str = await asyncio.to_thread(_read, Path(os.environ["DOT_LOG_DIR"]) / f"mcp-{SERVER}.log")
        assert "web-research mcp server started" in log


async def test_its_tools_are_judged_like_any_servers_the_six_that_read_are_reads(home: Path) -> None:
    async with _runtime(home) as runtime:
        judged: dict[str, ActionInfo] = {name: classify_mcp_call(_tool(runtime, name), {}) for name in TOOLS}
        assert {name for name, action in judged.items() if action.kind == "mcp_read"} == READS
        assert judged["web_crawl_start"].flags == ("external_send",)
        assert judged["web_crawl_cancel"].flags == ("destructive",)
        assert judged["web_scrape"].risk == "low" and judged["web_crawl_start"].risk == "medium"


async def test_a_file_of_the_workspace_named_like_a_module_the_server_imports_is_never_run(home: Path) -> None:
    """The server starts in the workspace, where a run's commands write. A module put there that is named like one it
    imports (pydantic's) would run in it, outside the sandbox and with the owner's rights, if the working directory were
    on its import path: it is not (``-P``), and the server starts."""
    marker: Path = home / "planted-code-ran"
    planted: str = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nraise SystemExit(3)\n"
    await asyncio.to_thread((home / "workspace").mkdir, parents=True)
    await asyncio.to_thread(_write, home / "workspace" / "pydantic.py", planted)
    async with _runtime(home) as runtime:
        assert runtime.mcp.failures == ()
        assert runtime.mcp.connected() == (SERVER,)
    assert not await asyncio.to_thread(_exists, marker)


async def test_the_data_folder_of_the_server_is_in_dots_home(home: Path) -> None:
    """``WEB_TOOLS_DATA_DIR`` reaches the server: its first look at a crawl job makes the store there. The job asked
    for does not exist, so no site is reached. The folder itself is there from the start: the server starts in it
    (item 637), and Dot makes it."""
    async with _runtime(home) as runtime:
        assert await asyncio.to_thread(_exists, home / "web-research")
        assert not await asyncio.to_thread(_exists, home / "web-research" / "crawl-jobs.sqlite3")
        answer = await runtime.mcp.call(_tool(runtime, "web_crawl_status"), {"job_id": "no-such-job"})
        assert answer.is_error is True
        assert await asyncio.to_thread(_exists, home / "web-research" / "crawl-jobs.sqlite3")


async def test_a_server_of_the_owners_with_the_same_name_replaces_it(home: Path) -> None:
    config: str = (
        f'[[mcp]]\nname = "web-research"\ncommand = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(FIXTURE))}]\n'
    )
    async with _world(home, config) as world:
        runtime: Runtime = world.runtime
        assert runtime.mcp.connected() == (SERVER,)
        assert runtime.mcp.configs()[SERVER].args == [str(FIXTURE)]
        assert runtime.builtin_names == frozenset()
        remote: set[str] = {tool.remote_name for tool in runtime.mcp.tools}
        assert "add" in remote and not remote & set(TOOLS)
        servers = _listed(await world.client.get("/api/mcp/servers"))
        assert servers[SERVER]["origin"] == "config"
        # Nothing was left behind for the built-in: no second entry, and no data folder.
        assert [name for name in servers if name == SERVER] == [SERVER]
        assert not await asyncio.to_thread(_exists, home / "web-research")


async def test_a_server_added_on_connectors_before_with_the_name_replaces_it(home: Path) -> None:
    keep_connected(
        connected_servers_path(),
        McpServerConfig(name=SERVER, command=sys.executable, args=[str(FIXTURE)]),
    )
    async with _world(home) as world:
        assert world.runtime.mcp.connected() == (SERVER,)
        assert world.runtime.mcp.configs()[SERVER].args == [str(FIXTURE)]
        assert world.runtime.builtin_names == frozenset()
        assert _listed(await world.client.get("/api/mcp/servers"))[SERVER]["origin"] == "connected"


async def test_connectors_refuses_a_new_server_of_that_name_while_the_built_in_is_there(home: Path) -> None:
    async with _world(home) as world:
        refused = await world.client.post(
            "/api/mcp/servers",
            json={"service": None, "name": SERVER, "address": f"{sys.executable} {FIXTURE}", "origin": ORIGIN},
        )
        assert refused.status_code == 409 and "another connected app has its name" in refused.text, refused.text
        assert world.runtime.mcp.connected() == (SERVER,)
        assert tuple(tool.remote_name for tool in world.runtime.mcp.tools) == TOOLS


async def test_the_switch_keeps_it_out(home: Path) -> None:
    async with _world(home, "[builtin_mcp]\nweb_research = false\n") as world:
        assert world.runtime.mcp.connected() == () and world.runtime.mcp.configs() == {}
        assert world.runtime.builtin_names == frozenset({SERVER})
        assert not any(name.startswith(f"mcp__{SERVER}__") for name in world.runtime.registry.tools)
        # It is still listed, for its switch.
        listed = _listed(await world.client.get("/api/mcp/servers"))[SERVER]
        assert (listed["origin"], listed["state"], listed["tools"]) == ("builtin", "disabled", [])


async def test_the_page_lists_it_as_one_that_comes_with_dot(home: Path) -> None:
    async with _world(home) as world:
        listed = _listed(await world.client.get("/api/mcp/servers"))[SERVER]
        assert (listed["origin"], listed["state"], listed["error"]) == ("builtin", "ready", None)
        assert listed["tools"] == list(TOOLS)
        assert (listed["picks_tools"], listed["signs_in"], listed["service"]) == (False, False, None)
        # What the page shows of where it is: the Python, not the path of the Mac's environment it is installed in.
        assert listed["address"] == "python -P -m web_scraper.mcp_server"


async def test_the_switch_of_the_page_stops_it_and_starts_it_again_and_the_config_file_keeps_it(home: Path) -> None:
    async with _world(home) as world:
        file: Path = home / "config.toml"
        off = await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": False})
        assert off.status_code == 200, off.text
        stopped = _listed(off)[SERVER]
        assert (stopped["origin"], stopped["state"], stopped["tools"]) == ("builtin", "disabled", [])
        assert world.runtime.mcp.connected() == ()
        # The tools leave the runs that start after, and the file says so for the next start of the daemon.
        assert not any(name.startswith(f"mcp__{SERVER}__") for name in world.runtime.registry.tools)
        assert "[builtin_mcp]\nweb_research = false\n" in await asyncio.to_thread(_read, file)
        assert load_config(file).builtin_mcp.web_research is False

        on = await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": True})
        assert on.status_code == 200, on.text
        started = _listed(on)[SERVER]
        assert (started["state"], started["tools"]) == ("ready", list(TOOLS))
        assert world.runtime.mcp.connected() == (SERVER,)
        assert all(f"mcp__{SERVER}__{name}" in world.runtime.registry.tools for name in TOOLS)
        assert load_config(file).builtin_mcp.web_research is True


async def test_a_server_that_cannot_start_is_not_switched_on(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file is written once the server answered: a switch that failed leaves it off, and says why."""
    async with _world(home, "[builtin_mcp]\nweb_research = false\n") as world:
        monkeypatch.setattr(builtin, "_WEB_RESEARCH_MODULE", "web_scraper.no_such_module")
        refused = await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": True})
        assert refused.status_code >= 400, refused.text
        assert world.runtime.mcp.connected() == ()
        assert "web_research = false" in await asyncio.to_thread(_read, home / "config.toml")
        assert _listed(await world.client.get("/api/mcp/servers"))[SERVER]["state"] == "disabled"


async def test_a_server_that_was_left_out_is_started_again_from_the_page(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start that failed leaves the server out with the reason, as any server's, and the tile's Try again starts it
    as Dot starts it now: here the module that was missing is there."""
    monkeypatch.setattr(builtin, "_WEB_RESEARCH_MODULE", "web_scraper.no_such_module")
    async with _world(home) as world:
        left = _listed(await world.client.get("/api/mcp/servers"))[SERVER]
        assert (left["origin"], left["state"], left["tools"]) == ("builtin", "left_out", [])
        assert left["error"], left
        assert [failure.server for failure in world.runtime.mcp.failures] == [SERVER]
        # Switched on already: the switch is on, and a second try is Try again's.
        monkeypatch.setattr(builtin, "_WEB_RESEARCH_MODULE", "web_scraper.mcp_server")
        again = await world.client.post(f"/api/mcp/servers/{SERVER}/connect", json={"origin": ORIGIN})
        assert again.status_code == 200, again.text
        assert (again.json()["origin"], again.json()["state"]) == ("builtin", "ready"), again.text
        assert world.runtime.mcp.connected() == (SERVER,)
        assert f"mcp__{SERVER}__web_scrape" in world.runtime.registry.tools
        # One that is switched off is not tried: its switch starts it.
        await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": False})
        refused = await world.client.post(f"/api/mcp/servers/{SERVER}/connect", json={"origin": ORIGIN})
        assert refused.status_code == 409 and "switched off" in refused.text, refused.text


async def test_a_config_file_that_cannot_be_changed_leaves_the_server_as_it_was(home: Path) -> None:
    async with _world(home) as world:
        await asyncio.to_thread(_write, home / "config.toml", "this is [not = valid toml\n")
        refused = await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": False})
        assert refused.status_code >= 400, refused.text
        assert world.runtime.mcp.connected() == (SERVER,)
        # And one that is off stays off when its switch cannot be written.
        await asyncio.to_thread(_write, home / "config.toml", "")
        assert (await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": False})).status_code == 200
        await asyncio.to_thread(_write, home / "config.toml", "this is [not = valid toml\n")
        refused = await world.client.put(f"/api/mcp/servers/{SERVER}/switch", json={"on": True})
        assert refused.status_code >= 400, refused.text
        assert world.runtime.mcp.connected() == ()


async def test_only_a_server_that_comes_with_dot_has_a_switch_and_it_cannot_be_removed(home: Path) -> None:
    config: str = (
        f'[[mcp]]\nname = "notes"\ncommand = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(FIXTURE))}]\n'
    )
    async with _world(home, config) as world:
        theirs = await world.client.put("/api/mcp/servers/notes/switch", json={"on": False})
        assert theirs.status_code == 409 and "does not come with Dot" in theirs.text, theirs.text
        gone = await world.client.put("/api/mcp/servers/no-such-server/switch", json={"on": False})
        assert gone.status_code == 404, gone.text
        removed = await world.client.delete(f"/api/mcp/servers/{SERVER}")
        assert removed.status_code == 409 and "comes with Dot" in removed.text, removed.text
        assert set(world.runtime.mcp.connected()) == {"notes", SERVER}


async def _dotx(home: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] = {**os.environ, "DOT_HOME": str(home), "DOT_LOG_DIR": str(home.parent / "logs")}
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "dotagent.cli", *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=ANSWER_S,
    )


async def test_dotx_mcp_list_marks_the_server_that_comes_with_dot_and_how_each_tool_is_judged(home: Path) -> None:
    await asyncio.to_thread(_write, home / "config.toml", "")
    listed = await _dotx(home, ["mcp", "list"])
    assert listed.returncode == 0, listed
    assert f"web-research (built-in): ready, {len(TOOLS)} of {LISTED} tools offered to the model" in listed.stdout
    assert "mcp__web-research__web_scrape  [read-only]" in listed.stdout
    assert "mcp__web-research__web_search" not in listed.stdout
    assert "mcp__web-research__web_crawl_start  [acts: external_send]" in listed.stdout
    assert "mcp__web-research__web_crawl_cancel  [acts: destructive]" in listed.stdout


async def test_dotx_mcp_list_says_which_setting_switches_it_on_when_it_is_off(home: Path) -> None:
    await asyncio.to_thread(_write, home / "config.toml", "[builtin_mcp]\nweb_research = false\n")
    listed = await _dotx(home, ["mcp", "list"])
    assert listed.returncode == 0, listed
    assert "web-research (built-in): disabled ([builtin_mcp] web_research = false)" in listed.stdout


async def test_dotx_mcp_list_shows_the_owners_server_of_the_name_as_theirs(home: Path) -> None:
    config: str = (
        f'[[mcp]]\nname = "web-research"\ncommand = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(FIXTURE))}]\n'
    )
    await asyncio.to_thread(_write, home / "config.toml", config)
    listed = await _dotx(home, ["mcp", "list"])
    assert listed.returncode == 0, listed
    assert "web-research: ready," in listed.stdout and "(built-in)" not in listed.stdout


async def test_the_real_server_answers_initialize_and_lists_its_tools_over_stdio(tmp_path: Path) -> None:
    """The server as Dot starts it, spoken to as an MCP client speaks: ``initialize``, then ``tools/list``. No search,
    no page: the process is started, asked what it offers and closed."""
    (server,) = builtin.builtin_servers(load_config(tmp_path / "none.toml"))
    assert server.command is not None
    environment: dict[str, str] = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), **server.env}
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        server.command,
        *server.args,
        env=environment,
        cwd=tmp_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None

    async def ask(request: JsonObject) -> JsonObject:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request).encode() + b"\n")
        await process.stdin.drain()
        line: bytes = await asyncio.wait_for(process.stdout.readline(), ANSWER_S)
        answer: JsonObject = json.loads(line)
        assert answer["id"] == request["id"], answer
        return answer

    try:
        started = await ask(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "dot-test", "version": "0"},
                },
            }
        )
        result = started["result"]
        assert isinstance(result, dict) and result["serverInfo"]["name"] == SERVER, started
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        listed = await ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = listed["result"]
        assert isinstance(tools, dict)
        names: list[str] = [tool["name"] for tool in tools["tools"]]
        assert names == list(SERVER_LISTS)
        # The search and the health check only read too; a run is offered neither (item 655).
        reads: frozenset[str] = READS | frozenset(NOT_OFFERED)
        assert all(tool["annotations"]["readOnlyHint"] is (tool["name"] in reads) for tool in tools["tools"])
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 10.0)
        except TimeoutError:
            process.kill()
            await process.wait()
    assert process.returncode == 0


# ------------------------------------------------------------------------------------- what the owner's lists bind


class _Gate:
    """Stands in for the run's gate, which lets every action through: what is looked at here is what each call is
    judged as, not who answers."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


def _context(runtime: Runtime) -> ToolContext:
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="builtin-mcp-test",
        agent_id=None,
        thread_id="builtin-mcp-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


ADDRESSED: tuple[tuple[str, JsonObject], ...] = (
    ("web_scrape", {}),
    ("web_extract", {"schema": {}}),
    ("web_map", {}),
    ("web_crawl_start", {}),
)


async def _prepare(runtime: Runtime, tool: str, arguments: JsonObject):  # type: ignore[no-untyped-def]
    return await runtime.registry.tools[f"mcp__{SERVER}__{tool}"].prepare(arguments, _context(runtime))


async def test_a_host_the_owner_blocked_is_refused_by_every_tool_of_the_server_as_web_fetch_refuses_it(
    home: Path,
) -> None:
    """The finding: the owner's lists bound ``web_fetch``, ``web_crawl`` and the browser, and not the tools of the
    server that is on in every install. Prepared, not run: nothing is fetched."""
    async with _runtime(home, '[browser]\nblocked_hosts = ["blocked.example"]\n') as runtime:
        url: str = "https://www.blocked.example/page"
        with pytest.raises(ToolInputError) as fetched:
            await runtime.registry.tools["web_fetch"].prepare({"url": url}, _context(runtime))
        for tool, extra in ADDRESSED:
            with pytest.raises(ToolInputError) as raised:
                await _prepare(runtime, tool, {"url": url, **extra})
            assert raised.value.message == fetched.value.message, tool
        with pytest.raises(ToolInputError):
            await _prepare(runtime, "web_scrape", {"urls": ["https://example.org/", url]})
        # An address of another host passes: the call is prepared, and it is judged as a read.
        fine = await _prepare(runtime, "web_scrape", {"url": "https://example.org/"})
        assert (fine.action.kind, fine.action.flags) == ("mcp_read", ())


async def test_a_host_outside_allowed_hosts_is_refused_by_every_tool_of_the_server_as_web_fetch_refuses_it(
    home: Path,
) -> None:
    async with _runtime(home, '[browser]\nallowed_hosts = ["docs.python.org"]\n') as runtime:
        with pytest.raises(ToolInputError) as fetched:
            await runtime.registry.tools["web_fetch"].prepare({"url": "https://example.org/"}, _context(runtime))
        for tool, extra in ADDRESSED:
            with pytest.raises(ToolInputError) as raised:
                await _prepare(runtime, tool, {"url": "https://example.org/", **extra})
            assert raised.value.message == fetched.value.message, tool
            assert "allowed_hosts" in raised.value.message
            allowed = await _prepare(runtime, tool, {"url": "https://docs.python.org/3/", **extra})
            assert allowed.action.target == SERVER


async def test_a_long_address_is_flagged_and_refused_by_the_policy_as_web_fetchs_is(home: Path) -> None:
    """``exfiltration``, which the policy refuses in every mode but yolo: for the tools of the server, as for
    ``web_fetch``."""
    async with _runtime(home) as runtime:
        long: str = "https://example.org/?q=" + "a" * 2400
        fetched = await runtime.registry.tools["web_fetch"].prepare({"url": long}, _context(runtime))
        assert fetched.action.flags == ("exfiltration",)
        for tool, extra in ADDRESSED:
            prepared = await _prepare(runtime, tool, {"url": long, **extra})
            # A crawl that is queued carries what it had (it may reach out: ``external_send``) and the new flag.
            assert "exfiltration" in prepared.action.flags, tool
            assert prepared.action.flags == (
                ("external_send", "exfiltration") if tool == "web_crawl_start" else ("exfiltration",)
            )
            decision = auto_review(prepared.action)
            assert decision is not None and decision.verdict == "deny", tool
        batch = await _prepare(runtime, "web_scrape", {"urls": ["https://example.org/", long]})
        assert batch.action.flags == ("exfiltration",)
        short = await _prepare(runtime, "web_scrape", {"url": "https://example.org/?q=short"})
        assert short.action.flags == ()


async def test_the_servers_search_is_web_searchs_rung_asked_with_the_query_and_the_count_alone(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 655: a run is offered no search of the server's; Dot's own web_search asks it as a rung, with the words the
    run gave web_search, which Dot judged, and how many results, and nothing else: no ``read`` of the pages it found,
    whatever the owner's lists. Its answer comes back as web_search's hits, each with its date. The call goes to a
    stand-in of the server: nothing is searched."""
    async with _runtime(home, '[browser]\nblocked_hosts = ["blocked.example"]\n') as runtime:
        asked: list[tuple[str, str, JsonObject]] = []

        async def stand_in(server: str, tool: str, arguments: JsonObject) -> CallToolResult:
            asked.append((server, tool, arguments))
            answer: JsonObject = {
                "query": arguments["query"],
                "results": [
                    {
                        "title": "SQLite FTS5",
                        "url": "https://sqlite.org/fts5.html",
                        "snippet": "Full-text search",
                        "date": "2026-10-01",
                    }
                ],
                "run": {"provider": "duckduckgo", "warnings": [], "fallback": None},
            }
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(answer))], isError=False)

        monkeypatch.setattr(runtime.mcp, "call_unoffered", stand_in)
        runtime.search_keys.order = ("web_research",)
        search = runtime.registry.tools["web_search"]
        context: ToolContext = _context(runtime)
        prepared = await search.prepare({"query": "sqlite fts5", "max_results": 3}, context)
        result = await prepared.run(context)
        assert asked == [(SERVER, "web_search", {"query": "sqlite fts5", "limit": 3})]
        assert "from web_research" in result.text and "https://sqlite.org/fts5.html" in result.text
        assert "2026-10-01 · Full-text search" in result.text


async def test_the_server_is_handed_the_address_dot_checked(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare host gets its scheme as Dot's own tools give it, so that the server cannot read the address another way.
    The call goes to a stand-in of the server: nothing is fetched."""
    async with _runtime(home) as runtime:
        handed: list[JsonObject] = []

        async def stand_in(tool: McpTool, arguments: JsonObject) -> CallToolResult:
            handed.append(arguments)
            return CallToolResult(content=[TextContent(type="text", text="{}")], isError=False)

        monkeypatch.setattr(runtime.mcp, "call", stand_in)
        prepared = await _prepare(runtime, "web_scrape", {"url": "  example.org/page ", "links": False})
        await prepared.run(_context(runtime))
        assert handed == [{"url": "https://example.org/page", "links": False}]


# ------------------------------------------------------------------ what a search or an address carries out of the Mac

# A key in a shape Dot knows (``policy.elements.looks_like_key``), put together here so that no file holds one whole.
KEY: str = "sk-" + "a1b2c3d4" * 4


async def test_a_key_in_a_search_is_flagged_and_refused_by_the_policy_as_web_searchs_is(home: Path) -> None:
    """The second review's finding: Dot's own ``web_search`` flags a query that holds a key (``exfiltration``, refused
    in every mode but yolo), and the search of the server, on in every install, was a read with no flag. Since item
    655 a run has no search but Dot's own, which asks the server as a rung: every query is judged by Dot's rule.
    Prepared, never run: nothing is searched."""
    async with _runtime(home) as runtime:
        context: ToolContext = _context(runtime)
        own = await runtime.registry.tools["web_search"].prepare({"query": f"what is {KEY}"}, context)
        assert own.action.flags == ("exfiltration",)
        decision = auto_review(own.action)
        assert decision is not None and decision.verdict == "deny"
        assert f"mcp__{SERVER}__web_search" not in runtime.registry.tools
        with pytest.raises(ToolInputError):
            await runtime.registry.tools["web_search"].prepare({"query": "a" * 401}, context)


async def test_a_key_in_the_query_string_of_an_address_is_flagged_and_refused_as_web_fetchs_is(home: Path) -> None:
    async with _runtime(home) as runtime:
        context: ToolContext = _context(runtime)
        url: str = "https://example.org/page?token=" + KEY
        for own in ("web_fetch", "web_crawl"):
            prepared = await runtime.registry.tools[own].prepare({"url": url}, context)
            assert prepared.action.flags == ("exfiltration",), own
        for tool, extra in ADDRESSED:
            prepared = await _prepare(runtime, tool, {"url": url, **extra})
            assert "exfiltration" in prepared.action.flags, tool
            decision = auto_review(prepared.action)
            assert decision is not None and decision.verdict == "deny", tool
        batch = await _prepare(runtime, "web_scrape", {"urls": ["https://example.org/", url]})
        assert batch.action.flags == ("exfiltration",)
        fine = await _prepare(runtime, "web_scrape", {"url": "https://example.org/page?token=none"})
        assert fine.action.flags == ()


async def test_a_rule_added_to_dots_own_web_tools_binds_the_servers_tools_too(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structure the second review asked for. The rule of ``web_search`` on its words, and the rule of ``web_fetch``
    and ``web_crawl`` on an address, are one function each (``tools.web``). Changed here to a rule that raises a flag
    nothing else raises, every tool of Dot's that has such words or such an address takes it, and so does every tool of
    the server that has them."""
    async with _runtime(home) as runtime:
        context: ToolContext = _context(runtime)

        def a_search(*texts: str) -> tuple[Flag, ...]:
            return ("install",)

        def an_address(value: str, config: AppConfig) -> tuple[str, tuple[Flag, ...]]:
            return value, ("install",)

        for module in (tools_web, tools_mcp):
            monkeypatch.setattr(module, "search_flags", a_search)
            monkeypatch.setattr(module, "checked_address", an_address)
        own = await runtime.registry.tools["web_search"].prepare({"query": "sqlite fts5"}, context)
        assert own.action.flags == ("install",)
        url: str = "https://example.org/"
        for name in ("web_fetch", "web_crawl"):
            assert (await runtime.registry.tools[name].prepare({"url": url}, context)).action.flags == ("install",)
        for tool, extra in ADDRESSED:
            assert "install" in (await _prepare(runtime, tool, {"url": url, **extra})).action.flags, tool
        # The tools that ask for neither words nor an address are judged by what the server declares, as before.
        status = await _prepare(runtime, "web_crawl_status", {"job_id": "a-job"})
        assert status.action.flags == ()


# What each string argument of the server's tools is, that Dot does not judge as words that leave the Mac or an address:
# worked on in the server and never sent. Read in its code (web_scraper 1.3.0, ``web_tools/service.py``, ``search.py``).
STAYS_IN_THE_SERVER: dict[str, str] = {
    "web_scrape.content_id": "names a page the server read before and keeps, to read on from an offset (1.3.0)",
    "web_map.search": "filters the addresses the server already found",
    "web_map.include_patterns[]": "globs that filter the addresses the server already found",
    "web_map.exclude_patterns[]": "globs that filter the addresses the server already found",
    "web_extract.schema{}": "a CSS selector, run on the page the server already fetched",
    "web_extract.schema{}.selector": "a CSS selector, run on the page the server already fetched",
    "web_extract.schema{}.fallback_selectors[]": "CSS selectors, run on the page the server already fetched",
    "web_extract.schema{}.attribute": "the name of an attribute of the page the server already fetched",
    "web_extract.schema{}.type": "the type the server gives a value it read from the page",
    "web_crawl_start.options.include_patterns[]": "globs that filter the links a crawl finds",
    "web_crawl_start.options.exclude_patterns[]": "globs that filter the links a crawl finds",
    "web_crawl_status.job_id": "names a crawl job the server holds",
    "web_crawl_results.job_id": "names a crawl job the server holds",
    "web_crawl_cancel.job_id": "names a crawl job the server holds",
}


def _strings(node: JsonValue, root: JsonObject, path: str) -> set[str]:
    """The paths of the strings that a node of a tool's input schema takes: ``query``, ``sites[]`` for the items of a
    list, ``schema{}`` for the values of an object of any keys, ``options.include_patterns[]`` for those of an object. A
    string of fixed values (an ``enum``) is none, since it cannot carry words."""
    if not isinstance(node, dict):
        return set()
    if isinstance(node.get("$ref"), str):
        defs: JsonValue = root.get("$defs")
        assert isinstance(defs, dict)
        return _strings(defs[str(node["$ref"]).rsplit("/", 1)[-1]], root, path)
    found: set[str] = set()
    for key in ("anyOf", "oneOf", "allOf"):
        options: JsonValue = node.get(key)
        if isinstance(options, list):
            for option in options:
                found |= _strings(option, root, path)
    if node.get("type") == "string" and "enum" not in node and "const" not in node:
        found.add(path)
    if node.get("type") == "array":
        found |= _strings(node.get("items"), root, f"{path}[]")
    properties: JsonValue = node.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            found |= _strings(child, root, f"{path}.{name}" if path else name)
    extra: JsonValue = node.get("additionalProperties")
    if isinstance(extra, dict):
        found |= _strings(extra, root, f"{path}{{}}")
    return found


def _judged(leaf: str) -> bool:
    """Whether Dot judges the string argument ``leaf`` (``tool.argument`` or ``tool.argument[]``): it holds words that
    go to the search engines, or an address the server will read."""
    tool, _, argument = leaf.partition(".")
    return argument.removesuffix("[]") in (*search_arguments(tool), *address_arguments(tool))


async def test_every_string_the_servers_tools_take_is_judged_by_dots_rules_or_known_to_stay_in_the_server(
    home: Path,
) -> None:
    """The class of the second review's finding, closed: it found the address arguments (round 1) and then the words of
    a search, field by field. The server's tools are listed as Dot sees them and every argument that takes a string is
    looked at, so that one added in a later version of the package is a failing test and not a call that Dot does not
    judge. Judge it in ``mcp/builtin.py`` (as words that go to the engines, or an address the server reads), or put
    it in ``STAYS_IN_THE_SERVER`` above, with what the server does with it."""
    async with _runtime(home) as runtime:
        leaves: set[str] = {
            f"{tool.remote_name}.{leaf}"
            for tool in runtime.mcp.tools
            if tool.server == SERVER
            for leaf in _strings(tool.input_schema, tool.input_schema, "")
        }
    # Every tool of the server that takes a string was looked at.
    assert {leaf.partition(".")[0] for leaf in leaves} == set(TOOLS) - NO_STRINGS
    unknown: set[str] = {leaf for leaf in leaves if not _judged(leaf) and leaf not in STAYS_IN_THE_SERVER}
    assert not unknown, (
        f"a string the server's tools take, that Dot neither judges nor knows stays in it: {sorted(unknown)}"
    )
    # Neither list names an argument the server no longer has.
    assert set(STAYS_IN_THE_SERVER) <= leaves
    known: set[str] = {
        f"{tool}.{argument}" for tool in TOOLS for argument in (*search_arguments(tool), *address_arguments(tool))
    }
    assert known <= {leaf.removesuffix("[]") for leaf in leaves}


# ------------------------------------------------------------------------------- a .env the server must not take


def _environment_of_the_server(marker: str) -> str:
    """The environment the server this process started was started with, as ``ps`` shows it for a process of the same
    user (the environment at its start: what the process puts there itself later, ``ps`` does not show): the one of the
    children of this process that holds ``marker``."""
    children: list[str] = subprocess.run(
        ["/usr/bin/pgrep", "-P", str(os.getpid())], capture_output=True, text=True, check=False
    ).stdout.split()
    for child in children:
        shown: str = subprocess.run(
            ["/bin/ps", "eww", "-p", child, "-o", "command="], capture_output=True, text=True, check=False
        ).stdout
        if marker in shown:
            return shown
    raise AssertionError(f"no child of this process holds {marker}: {children}")


async def test_the_server_is_started_with_python_dotenv_disabled_in_its_environment(home: Path) -> None:
    """The variable reaches the process that Dot's hub starts, whatever the client does with the environment it is
    given."""
    marker: str = f"DOT_TEST_MARKER={uuid.uuid4().hex}"
    (server,) = builtin_servers(load_default_config())
    key, _, value = marker.partition("=")
    started = server.model_copy(update={"env": {**server.env, key: value}})
    await asyncio.to_thread(ensure_layout)
    hub = await open_hub([started], {"PATH": "/usr/bin:/bin"})
    try:
        assert hub.failures == () and hub.connected() == (SERVER,)
        seen: str = await asyncio.to_thread(_environment_of_the_server, marker)
    finally:
        await hub.close()
    assert "PYTHON_DOTENV_DISABLED=1" in seen and "WEB_TOOLS_DATA_DIR=" in seen, seen


# What the server does at its start with the environment, as its own code does it: the settings it reads and the
# configuration module it imports are the ones that read a ``.env``. Printed after, from the process's own environment.
_STARTUP_PROBE: str = """
import asyncio, json, os
import web_scraper.config
from web_scraper.web_tools.service import WebToolsService

async def main():
    service = WebToolsService.from_env()
    print(json.dumps({name: os.environ.get(name) for name in ("PLANTED_PROVIDER_KEY", "NODE_OPTIONS")}))
    await service.close()

asyncio.run(main())
"""
PLANTED_KEY: str = "sk-planted-0123456789abcdef"
PLANTED_NODE: str = "--require=/nonexistent/planted.js"


async def _taken_from_a_dotenv(layout: str, tmp_path: Path, dotenv_disabled: str | None) -> dict[str, str | None]:
    """What the server's start takes of a ``.env`` that has a key and a ``NODE_OPTIONS`` in it, with the environment
    and the working folder Dot's hub gives the process, and its command (the program and its flags; its start is
    replaced by what the server does first). The ``.env`` lies in the workspace, the server's working folder, or above
    the folder of a copy of its package, as Dot's own key file lies above the package in the app's layout.
    ``dotenv_disabled`` replaces the variable Dot sets; ``None`` leaves it as it is."""
    await asyncio.to_thread(ensure_layout)
    planted: str = f"PLANTED_PROVIDER_KEY={PLANTED_KEY}\nNODE_OPTIONS={PLANTED_NODE}\n"
    extra: dict[str, str] = {}
    if layout == "workspace":
        await asyncio.to_thread(_write, workspace_dir() / ".env", planted)
    else:
        package: Path = Path(str(importlib.util.find_spec("web_scraper").origin)).parent  # type: ignore[union-attr]
        site: Path = tmp_path / "layout" / "home" / "runtime" / "lib" / "python3.13" / "site-packages"
        await asyncio.to_thread(
            shutil.copytree, package, site / "web_scraper", ignore=shutil.ignore_patterns("__pycache__")
        )
        await asyncio.to_thread(_write, tmp_path / "layout" / "home" / ".env", planted)
        extra["PYTHONPATH"] = str(site)
    (server,) = builtin_servers(load_default_config())
    if dotenv_disabled is not None:
        extra["PYTHON_DOTENV_DISABLED"] = dotenv_disabled
    given: McpServerConfig = server.model_copy(update={"env": {**server.env, **extra}})
    environment: dict[str, str] = {
        **get_default_environment(),
        **server_environment(given, {"PATH": "/usr/bin:/bin"}, {}),
    }
    assert server.command is not None and server.args[:1] == ["-P"]
    done: subprocess.CompletedProcess[str] = await asyncio.to_thread(
        subprocess.run,
        [server.command, "-P", "-c", _STARTUP_PROBE],
        env=environment,
        cwd=workspace_dir(),
        capture_output=True,
        text=True,
        check=False,
        timeout=ANSWER_S,
    )
    assert done.returncode == 0, done.stderr
    taken: dict[str, str | None] = json.loads(done.stdout.strip().splitlines()[-1])
    return taken


@pytest.mark.parametrize("layout", ["workspace", "above the package"])
async def test_a_dotenv_file_is_not_taken_by_the_servers_start(layout: str, home: Path, tmp_path: Path) -> None:
    """The finding: python-dotenv took the first ``.env`` above the package's folder, in the app's layout Dot's own key
    file, and a ``NODE_OPTIONS`` in one reached the browser's driver, a file run outside the sandbox. The package reads
    only the working folder's now, and ``PYTHON_DOTENV_DISABLED=1`` closes that one too: the workspace is the working
    folder, where a run's commands write."""
    taken: dict[str, str | None] = await _taken_from_a_dotenv(layout, tmp_path, None)
    assert taken == {"PLANTED_PROVIDER_KEY": None, "NODE_OPTIONS": None}


async def test_the_server_would_take_the_workspaces_dotenv_without_the_variable(home: Path, tmp_path: Path) -> None:
    """The control: that layout is one that is read, so the test above fails without ``PYTHON_DOTENV_DISABLED``."""
    taken: dict[str, str | None] = await _taken_from_a_dotenv("workspace", tmp_path, "0")
    assert taken == {"PLANTED_PROVIDER_KEY": PLANTED_KEY, "NODE_OPTIONS": PLANTED_NODE}
