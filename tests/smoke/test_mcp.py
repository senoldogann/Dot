"""MCP servers end to end: a real server over stdio, its tools run through the registry the agent uses, and a server
that cannot start is left out, reported with everything that helps to fix it, while Dot starts without its tools.

The server is ``tests/fixtures/mcp_fixture_server.py``, started like any server a user would configure. Only the test
with a model needs the local Ollama server and a cloud model; it is skipped without them.
"""

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from ollama_live import live_with

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.config import McpServerConfig, load_config
from dotagent.core.errors import McpError
from dotagent.core.paths import ensure_layout, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.mcp import hub as mcp_hub
from dotagent.mcp.connection import McpConnection
from dotagent.mcp.hub import McpHub, ServerReport, inspect_servers, open_hub
from dotagent.mcp.types import McpFailure
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.base import ACTING_MODES, ALL_MODES, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.mcp import build_mcp_tools
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.usefixtures("environment")]

FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
SECRET_VALUE: str = "from-the-environment"
FIXTURE_TOOLS: frozenset[str] = frozenset(
    {"add", "echo", "notes_save", "wipe", "explode", "picture", "slow", "environment", "process_id", "counts"}
)
CALL_TIMEOUT_S: float = 3.0
RUN_TIMEOUT_S: float = 180.0
CLI_TIMEOUT_S: float = 90.0
GONE_TIMEOUT_S: float = 15.0
DAEMON_START_S: float = 90.0
# A server whose two tools would both be called mcp__clash__a_b: the model could not tell them apart.
CLASHING_SERVER: str = (
    "from mcp.server.mcpserver import MCPServer\n"
    "server = MCPServer('clash')\n"
    "@server.tool(name='a.b')\n"
    "def first() -> str:\n"
    "    return 'one'\n"
    "@server.tool(name='a_b')\n"
    "def second() -> str:\n"
    "    return 'two'\n"
    "server.run('stdio')\n"
)
FENCE: re.Pattern[str] = re.compile(
    r'<mcp_content nonce="(?P<nonce>[0-9a-f]+)" server="(?P<server>[^"]+)" tool="(?P<tool>[^"]+)">\n'
    r'(?P<body>.*)\n</mcp_content nonce="(?P=nonce)">',
    re.DOTALL,
)


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


# Live (item 632): the cloud model answers from off this Mac.
requires_model = live_with(model_is_listed(), f"needs the local Ollama server with {MODEL}")


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@dataclass(frozen=True)
class CliOutcome:
    code: int
    out: str
    err: str


# A ``dotx`` or a daemon these tests start has the web-research server that comes with Dot (item 583), which would start
# beside the fixture and be counted with it: the homes switch it off.
NO_BUILTIN: str = "[builtin_mcp]\nweb_research = false\n\n"


def _server_table(name: str, extra: str) -> str:
    return "\n".join(
        [
            "[[mcp]]",
            f"name = {json.dumps(name)}",
            f"command = {json.dumps(sys.executable)}",
            f"args = [{json.dumps(str(FIXTURE))}]",
            extra,
        ]
    )


def _home_with(root: Path, config: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(config, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    fixture_server: str = _server_table(
        "fixture", 'env = { GREETING = "hello", FROM_SECRET = "${DOT_TEST_MCP_VALUE}" }\ncall_timeout_s = 3\n'
    )
    return _home_with(
        tmp_path_factory.mktemp("home"), f'{NO_BUILTIN}[models]\nmain = "ollama:{MODEL}"\n\n{fixture_server}'
    )


@pytest.fixture(scope="module")
def environment(home: Path) -> Iterator[None]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(home / "logs"))
    patch.setenv("DOT_TEST_MCP_VALUE", SECRET_VALUE)
    patch.setenv("DOT_TEST_NOT_FORWARDED", "leak")
    # The home is laid out as every dotx command and the daemon lay it out: a stdio server starts in its workspace.
    ensure_layout()
    yield
    patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path, environment: None) -> AsyncIterator[Runtime]:
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env(environment: None) -> dict[str, str]:
    return await read_login_env(10.0, ())


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="mcp-test",
        agent_id=None,
        thread_id="mcp-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared.action


def _answer(result: ToolResult, remote_tool: str) -> str:
    """The text inside the fence of an MCP result; the fence itself must be intact and the result untrusted."""
    found: re.Match[str] | None = FENCE.fullmatch(result.text)
    assert found is not None, result.text
    assert (found["server"], found["tool"]) == ("fixture", remote_tool)
    assert result.untrusted
    return found["body"]


async def _count_processes(marker: str) -> int:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "pgrep", "-f", marker, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return len(out.split())


async def _assert_gone(marker: str) -> None:
    deadline: float = time.monotonic() + GONE_TIMEOUT_S
    while await _count_processes(marker) > 0:
        assert time.monotonic() < deadline, f"a process with {marker} is still running"
        await asyncio.sleep(0.25)


async def _dotx(home: Path, arguments: list[str]) -> CliOutcome:
    environment: dict[str, str] = {
        **os.environ,
        "DOT_HOME": str(home),
        "DOT_LOG_DIR": str(home / "logs"),
        "DOT_TEST_MCP_VALUE": SECRET_VALUE,
    }
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "dotagent.cli",
        *arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), CLI_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    assert process.returncode is not None
    return CliOutcome(code=process.returncode, out=out.decode(), err=err.decode())


def _fixture_server(name: str) -> McpServerConfig:
    return McpServerConfig(name=name, command=sys.executable, args=[str(FIXTURE)])


def _python_server(name: str, source: str, startup_timeout_s: float) -> McpServerConfig:
    return McpServerConfig(name=name, command=sys.executable, args=["-c", source], startup_timeout_s=startup_timeout_s)


async def _left_out(config: McpServerConfig, login_env: dict[str, str]) -> McpFailure:
    """Open a hub with one server that cannot start: the hub opens without its tools and says why it left it out."""
    hub: McpHub = await open_hub([config], login_env)
    await hub.close()
    assert hub.tools == ()
    (failure,) = hub.failures
    assert failure.server == config.name
    assert failure.log.endswith(f"mcp-{config.name}.log")
    return failure


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _status_once_up(daemon: asyncio.subprocess.Process, home: Path, port: int) -> JsonObject:
    """``/api/status`` of a daemon that is starting, once it answers; fails when the daemon ends first."""
    deadline: float = time.monotonic() + DAEMON_START_S
    token_file: Path = home / "api-token"
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        while True:
            assert daemon.returncode is None, f"the daemon ended with code {daemon.returncode} while it started"
            assert time.monotonic() < deadline, "the daemon did not answer in time"
            if await asyncio.to_thread(token_file.exists):
                token: str = (await asyncio.to_thread(token_file.read_text, encoding="utf-8")).strip()
                try:
                    answer: httpx.Response = await client.get(
                        "/api/status", headers={"Authorization": f"Bearer {token}"}
                    )
                except httpx.TransportError:
                    answer = httpx.Response(503)
                if answer.status_code == 200:
                    status: JsonObject = answer.json()
                    return status
            await asyncio.sleep(0.25)


# ----------------------------------------------------------------------------------------- the registered tools


async def test_the_tools_of_the_server_are_registered_with_modes_and_schemas(runtime: Runtime) -> None:
    registered: dict[str, RegisteredTool] = {
        name: tool for name, tool in runtime.registry.tools.items() if name.startswith("mcp__")
    }
    assert {name.removeprefix("mcp__fixture__") for name in registered} == FIXTURE_TOOLS

    add: RegisteredTool = registered["mcp__fixture__add"]
    assert add.spec.description.startswith("[MCP server fixture] ")
    assert add.spec.parameters == {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    assert (add.modes, add.parallel_safe) == (ALL_MODES, True)

    save: RegisteredTool = registered["mcp__fixture__notes_save"]
    assert save.spec.parameters == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["title"],
    }
    assert (save.modes, save.parallel_safe) == (ACTING_MODES, False)
    assert registered["mcp__fixture__wipe"].modes == ACTING_MODES
    assert all(tool.timeout_s > CALL_TIMEOUT_S for tool in registered.values())


async def test_a_read_only_call_returns_a_fenced_untrusted_answer(runtime: Runtime) -> None:
    result, action = await _call(runtime, "mcp__fixture__add", {"a": 2, "b": 3})
    assert not result.is_error
    assert _answer(result, "add") == "5"
    assert (action.tool, action.kind, action.risk, action.flags, action.target) == (
        "mcp__fixture__add",
        "mcp_read",
        "low",
        (),
        "fixture",
    )
    assert action.summary == 'fixture: add({"a":2,"b":3})'


async def test_structured_results_and_pictures_arrive_intact(runtime: Runtime) -> None:
    counted, _ = await _call(runtime, "mcp__fixture__counts", {})
    assert json.loads(_answer(counted, "counts")) == {"apples": 3, "pears": 5}

    picture, _ = await _call(runtime, "mcp__fixture__picture", {})
    assert not picture.is_error
    assert len(picture.images) == 1
    assert "[image 1: 8x8px]" in _answer(picture, "picture")


async def test_a_long_answer_is_kept_whole_in_a_file_whose_reading_is_untrusted(runtime: Runtime) -> None:
    kept_dir: Path = workspace_dir() / "mcp-results"
    short, _ = await _call(runtime, "mcp__fixture__echo", {"text": "short"})
    assert _answer(short, "echo") == "short" and not await asyncio.to_thread(kept_dir.exists)

    body: str = "\n".join(f"row {number:06d} " + "z" * 30 for number in range(1, 5001))
    long, _ = await _call(runtime, "mcp__fixture__echo", {"text": body})
    assert long.untrusted and not long.is_error
    assert len(long.text) <= runtime.config.agent.tool_output_chars
    (kept,) = await asyncio.to_thread(lambda: list(kept_dir.iterdir()))
    assert await asyncio.to_thread(kept.read_text, encoding="utf-8") == body
    assert f"{len(body)} characters in 5000 lines" in long.text and str(kept) in long.text

    # Read back as the model is told to, the file still counts as what the server wrote.
    read, _ = await _call(runtime, "read_file", {"path": str(kept), "offset": 4999})
    assert read.untrusted and "row 005000" in read.text
    searched, _ = await _call(runtime, "search_files", {"pattern": "row 004321"})
    assert searched.untrusted and "row 004321" in searched.text
    searched_one, _ = await _call(runtime, "search_files", {"pattern": "row 004321", "path": str(kept)})
    assert searched_one.untrusted and "row 004321" in searched_one.text
    tailed, _ = await _call(runtime, "shell", {"command": "tail -n 1 mcp-results/*.txt"})
    assert tailed.untrusted and "row 005000" in tailed.text
    plain, _ = await _call(runtime, "shell", {"command": "echo hello"})
    assert not plain.untrusted


async def test_a_failing_tool_is_an_error_result_and_the_server_goes_on(runtime: Runtime) -> None:
    exploded, _ = await _call(runtime, "mcp__fixture__explode", {"reason": "boom"})
    assert exploded.is_error
    assert "Error executing tool explode" in _answer(exploded, "explode")

    invalid, _ = await _call(runtime, "mcp__fixture__add", {"a": "x"})
    assert invalid.is_error
    assert "validation error" in _answer(invalid, "add")

    fine, _ = await _call(runtime, "mcp__fixture__add", {"a": 1, "b": 1})
    assert _answer(fine, "add") == "2"


async def test_tools_without_a_read_only_hint_are_treated_with_care(runtime: Runtime) -> None:
    declared, declared_action = await _call(runtime, "mcp__fixture__notes_save", {"title": "t"})
    assert _answer(declared, "notes.save") == "saved t (0 characters)"
    assert (declared_action.kind, declared_action.risk, declared_action.flags) == ("mcp", "medium", ())

    undeclared, undeclared_action = await _call(runtime, "mcp__fixture__wipe", {"target": "x"})
    assert _answer(undeclared, "wipe") == "wiped x"
    assert (undeclared_action.kind, undeclared_action.risk, undeclared_action.flags) == (
        "mcp",
        "medium",
        ("destructive", "external_send"),
    )


async def test_the_server_gets_only_what_it_is_configured_with(runtime: Runtime) -> None:
    seen: dict[str, str] = {}
    for name in ("GREETING", "FROM_SECRET", "DOT_TEST_NOT_FORWARDED", "DOT_HOME", "DOT_TEST_MCP_VALUE"):
        result, _ = await _call(runtime, "mcp__fixture__environment", {"name": name})
        seen[name] = _answer(result, "environment")
    assert seen == {
        "GREETING": "hello",
        "FROM_SECRET": SECRET_VALUE,
        "DOT_TEST_NOT_FORWARDED": "<unset>",
        "DOT_HOME": "<unset>",
        "DOT_TEST_MCP_VALUE": "<unset>",
    }


async def test_calls_to_one_server_overlap(runtime: Runtime) -> None:
    started: float = time.monotonic()
    results: list[tuple[ToolResult, ActionInfo]] = await asyncio.gather(
        *(_call(runtime, "mcp__fixture__slow", {"seconds": 1.0}) for _ in range(3))
    )
    elapsed: float = time.monotonic() - started
    assert [_answer(result, "slow") for result, _ in results] == ["done"] * 3
    assert elapsed < 2.5, elapsed


async def test_a_slow_call_times_out_and_the_server_stays_usable(runtime: Runtime) -> None:
    started: float = time.monotonic()
    with pytest.raises(McpError, match="timed out") as caught:
        await _call(runtime, "mcp__fixture__slow", {"seconds": 8.0})
    assert caught.value.context["timeout_s"] == CALL_TIMEOUT_S
    assert caught.value.context["tool"] == "slow"
    assert time.monotonic() - started < CALL_TIMEOUT_S + 2.0

    after, _ = await _call(runtime, "mcp__fixture__add", {"a": 1, "b": 1})
    assert _answer(after, "add") == "2"


async def test_the_settings_and_the_database_are_out_of_reach_of_the_agent(runtime: Runtime, home: Path) -> None:
    for name in ("config.toml", "dot.sqlite3", "dot.sqlite3-wal", "dot.sqlite3-shm"):
        assert (home / name).resolve() in runtime.protected, name


async def test_a_server_that_died_is_started_again_by_the_next_call(runtime: Runtime) -> None:
    """A call whose server died is not repeated when the tool may change something (wipe declares no hints): it fails,
    and the next call starts the server again. A read-only one is called once more on the new server (item 513)."""
    first, _ = await _call(runtime, "mcp__fixture__process_id", {})
    old_pid: int = int(_answer(first, "process_id"))
    os.kill(old_pid, signal.SIGKILL)

    # What came with the failure is the server's side: fenced, the result untrusted (the review of 2026-11-01).
    died, _ = await _call(runtime, "mcp__fixture__wipe", {"target": "the logs"})
    assert died.is_error and died.untrusted and "closed the connection while running wipe" in died.text

    again, _ = await _call(runtime, "mcp__fixture__add", {"a": 1, "b": 1})
    assert _answer(again, "add") == "2"
    second, _ = await _call(runtime, "mcp__fixture__process_id", {})
    restarted: int = int(_answer(second, "process_id"))
    assert restarted != old_pid
    os.kill(restarted, signal.SIGKILL)

    repeated, _ = await _call(runtime, "mcp__fixture__add", {"a": 2, "b": 2})
    assert _answer(repeated, "add") == "4"
    third, _ = await _call(runtime, "mcp__fixture__process_id", {})
    assert int(_answer(third, "process_id")) not in (old_pid, restarted)


# --------------------------------------------------------------------------------------------- the hub on its own


async def test_a_missing_command_is_reported_with_the_path_that_was_searched(login_env: dict[str, str]) -> None:
    ghost: McpServerConfig = McpServerConfig(name="ghost", command="dot-no-such-server-binary")
    failure: McpFailure = await _left_out(ghost, login_env)
    assert "the command of the MCP server was not found" in failure.error
    context: JsonObject = failure.detail
    assert context["server"] == "ghost"
    assert context["command"] == "dot-no-such-server-binary"
    assert context["searched_path"] == login_env["PATH"]
    assert str(context["log"]).endswith("mcp-ghost.log")


async def test_a_server_that_quits_shows_what_it_printed(login_env: dict[str, str]) -> None:
    quitter: McpServerConfig = _python_server(
        "quitter", "import sys; sys.stderr.write('configuration file is missing\\n'); sys.exit(3)", 30.0
    )
    failure: McpFailure = await _left_out(quitter, login_env)
    assert "closed the connection while starting" in failure.error
    assert "configuration file is missing" in str(failure.detail["stderr_tail"])


async def test_a_silent_server_runs_into_the_start_timeout_and_is_stopped(login_env: dict[str, str]) -> None:
    marker: str = f"dot-silent-{uuid.uuid4().hex}"
    silent: McpServerConfig = _python_server("silent", f"import time; time.sleep(120)  # {marker}", 2.0)
    started: float = time.monotonic()
    failure: McpFailure = await _left_out(silent, login_env)
    assert "did not answer in time" in failure.error
    assert failure.detail["startup_timeout_s"] == 2.0
    assert time.monotonic() - started < 12.0
    await _assert_gone(marker)


async def test_an_unset_secret_is_reported_before_anything_starts(login_env: dict[str, str]) -> None:
    marker: str = f"dot-secret-{uuid.uuid4().hex}"
    needy: McpServerConfig = McpServerConfig(
        name="needy",
        command=sys.executable,
        args=[str(FIXTURE), marker],
        env={"TOKEN": "${DOT_TEST_SURELY_UNSET_SECRET}"},
    )
    failure: McpFailure = await _left_out(needy, login_env)
    assert "refers to secrets that are not set" in failure.error
    assert failure.detail["missing"] == ["DOT_TEST_SURELY_UNSET_SECRET"]
    assert await _count_processes(marker) == 0


async def test_the_allow_list_picks_the_tools_and_must_name_existing_ones(login_env: dict[str, str]) -> None:
    picked: McpServerConfig = _fixture_server("subset").model_copy(update={"allow": ["echo", "add", "echo"]})
    (report,) = await inspect_servers([picked], login_env)
    assert report.error is None
    assert report.listed == len(FIXTURE_TOOLS)
    assert [tool.exposed_name for tool in report.tools] == ["mcp__subset__echo", "mcp__subset__add"]

    wrong: McpServerConfig = _fixture_server("wrong").model_copy(update={"allow": ["add", "nope"]})
    failure: McpFailure = await _left_out(wrong, login_env)
    assert "allow list names tools that the MCP server does not offer" in failure.error
    assert failure.detail["missing"] == ["nope"]


async def test_a_server_whose_tools_would_share_a_name_is_left_out(login_env: dict[str, str]) -> None:
    marker: str = f"dot-clash-{uuid.uuid4().hex}"
    clash: McpServerConfig = _python_server("clash", f"{CLASHING_SERVER}# {marker}\n", 30.0)
    failure: McpFailure = await _left_out(clash, login_env)
    assert failure.error == "MCP tools would share one name"
    assert failure.detail["clashes"] == {"mcp__clash__a_b": ["clash/a.b", "clash/a_b"]}
    await _assert_gone(marker)


async def test_a_disabled_server_is_not_started(login_env: dict[str, str]) -> None:
    sleeping: McpServerConfig = McpServerConfig(name="sleeping", command="dot-no-such-server-binary", enabled=False)
    hub: McpHub = await open_hub([sleeping], login_env)
    try:
        assert hub.tools == ()
    finally:
        await hub.close()
    (report,) = await inspect_servers([sleeping], login_env)
    assert (report.enabled, report.error, report.tools) == (False, None, ())


async def test_when_one_server_fails_the_others_serve_their_tools(login_env: dict[str, str]) -> None:
    """Item 153: a server that cannot start is left out with the reason, and the others start and serve their tools."""
    marker: str = f"dot-kept-{uuid.uuid4().hex}"
    good: McpServerConfig = McpServerConfig(name="good", command=sys.executable, args=[str(FIXTURE), marker])
    ghost: McpServerConfig = McpServerConfig(name="ghost", command="dot-no-such-server-binary")
    hub: McpHub = await open_hub([good, ghost], login_env)
    try:
        assert {tool.remote_name.replace(".", "_") for tool in hub.tools} == FIXTURE_TOOLS
        assert {tool.server for tool in hub.tools} == {"good"}
        assert [failure.server for failure in hub.failures] == ["ghost"]
        assert "the command of the MCP server was not found" in hub.failures[0].error
        add = next(tool for tool in hub.tools if tool.remote_name == "add")
        assert (await hub.call(add, {"a": 2, "b": 3})).structured_content == {"result": 5}
    finally:
        await hub.close()
    await _assert_gone(marker)


async def test_two_servers_with_the_same_tool_names_stay_apart(login_env: dict[str, str]) -> None:
    hub: McpHub = await open_hub([_fixture_server("one"), _fixture_server("two")], login_env)
    try:
        names: list[str] = [tool.exposed_name for tool in hub.tools]
        assert len(names) == len(set(names)) == 2 * len(FIXTURE_TOOLS)
        assert "mcp__one__add" in names and "mcp__two__add" in names
    finally:
        await hub.close()


# A server whose one tool takes a tree: its input schema refers to itself, which Dot cannot flatten for a model.
_TREE_SERVER: str = """
from pydantic import BaseModel
from mcp.server.mcpserver import MCPServer


class Node(BaseModel):
    name: str
    children: list["Node"] = []


server = MCPServer("dot-tree")


@server.tool()
def count(root: Node) -> int:
    \"\"\"How many children the root has.\"\"\"
    return len(root.children)


server.run("stdio")
"""


async def test_a_server_whose_tool_has_a_schema_that_refers_to_itself_serves_with_that_part_cut(
    login_env: dict[str, str],
) -> None:
    """Item 671: a tool whose schema is a tree (a node holding nodes) is handed to the model with the part past the
    depth Dot follows cut to a loose one that says so, and its server serves beside the others. Before (item 175), the
    schema left the whole server out, as Vercel's ``update_flag`` left Vercel out."""
    marker: str = f"dot-kept-{uuid.uuid4().hex}"
    good: McpServerConfig = McpServerConfig(name="good", command=sys.executable, args=[str(FIXTURE), marker])
    hub: McpHub = await open_hub([good, _python_server("tree", _TREE_SERVER, 30.0)], login_env)
    try:
        assert {tool.server for tool in hub.tools} == {"good", "tree"}
        assert hub.failures == ()
        (count,) = (tool for tool in hub.tools if tool.server == "tree")
        assert count.remote_name == "count"
        built = {tool.spec.name: tool.spec for tool in build_mcp_tools(hub)}
        assert "nest deeper than shown here" in str(built[count.exposed_name].parameters)
        assert len(built) == len(hub.tools)
    finally:
        await hub.close()
    await _assert_gone(marker)


async def test_mcp_list_reports_a_failure_that_is_not_one_of_dots_own_and_the_other_servers(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = mcp_hub._connect_kept

    async def crashing(config: McpServerConfig, env: Mapping[str, str]) -> object:
        if config.name == "crashing":
            raise RuntimeError("a fault in the MCP library")
        return await connect(config, env)

    monkeypatch.setattr(mcp_hub, "_connect_kept", crashing)
    reports: tuple[ServerReport, ...] = await inspect_servers(
        [_fixture_server("fine"), _fixture_server("crashing")], login_env
    )
    assert [(report.server, report.error) for report in reports] == [
        ("fine", None),
        ("crashing", "RuntimeError: a fault in the MCP library"),
    ]


async def test_a_missing_working_directory_is_named_rather_than_the_command(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    missing: Path = tmp_path / "gone"
    connection: McpConnection = McpConnection(
        _fixture_server("homeless"), dict(login_env), {}, None, missing, tmp_path / "mcp-homeless.log"
    )
    try:
        with pytest.raises(McpError) as raised:
            await connection.list_tools()
    finally:
        await connection.close()
    assert raised.value.message == "the working directory of the MCP server does not exist"
    assert raised.value.context["cwd"] == str(missing)
    assert "searched_path" not in raised.value.context


# ------------------------------------------------------------------------------------------------- the command line


async def test_mcp_list_shows_the_tools_of_a_working_server(home: Path) -> None:
    outcome: CliOutcome = await _dotx(home, ["mcp", "list"])
    assert outcome.code == 0, outcome
    assert f"fixture: ready, {len(FIXTURE_TOOLS)} of {len(FIXTURE_TOOLS)} tools offered to the model" in outcome.out
    assert "mcp__fixture__add  [read-only]  Add two integers." in outcome.out
    assert "mcp__fixture__wipe  [acts: destructive, external_send]" in outcome.out
    assert "mcp__fixture__notes_save  [acts]" in outcome.out


async def test_a_broken_server_is_reported_by_mcp_list_and_named_before_a_run(tmp_path: Path) -> None:
    broken: Path = _home_with(
        tmp_path / "broken-home", f'{NO_BUILTIN}[[mcp]]\nname = "ghost"\ncommand = "dot-no-such-server-binary"\n'
    )
    listed: CliOutcome = await _dotx(broken, ["mcp", "list"])
    assert listed.code == 1, listed
    assert "ghost: FAILED" in listed.out
    assert "the command of the MCP server was not found" in listed.out
    assert "searched_path" in listed.out

    # The run goes on without the server's tools; a thread that does not exist stops it before any model is asked.
    started: float = time.monotonic()
    ran: CliOutcome = await _dotx(broken, ["run", "--no-input", "--thread", "no-such-thread", "say hello"])
    assert ran.code == 2, ran
    assert "MCP server ghost is left out: the command of the MCP server was not found" in ran.err
    assert "error: thread not found" in ran.err
    assert time.monotonic() - started < CLI_TIMEOUT_S


# ------------------------------------------------------------------------------------------------------- the daemon


async def test_the_daemon_starts_without_a_server_that_cannot_start_and_names_it(tmp_path: Path) -> None:
    """Item 153: a real daemon whose configuration has a server that cannot start serves the tools of the others, and
    its status names the one it left out with the reason."""
    port: int = _free_port()
    home: Path = _home_with(
        tmp_path / "home",
        "\n".join(
            [
                f"{NO_BUILTIN}[server]\nport = {port}\n",
                '[[mcp]]\nname = "ghost"\ncommand = "dot-no-such-server-binary"\n',
                _server_table("fixture", ""),
            ]
        ),
    )
    daemon: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "dotagent",
        "daemon",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            **os.environ,
            "DOT_HOME": str(home),
            "DOT_LOG_DIR": str(tmp_path / "logs"),
            "DOT_CHROME_DIR": str(tmp_path / "chrome"),
        },
    )
    try:
        status: JsonObject = await _status_once_up(daemon, home, port)
    finally:
        if daemon.returncode is None:
            daemon.send_signal(signal.SIGTERM)
        async with asyncio.timeout(DAEMON_START_S):
            ended: int = await daemon.wait()
    assert status["mcp_tools"] == len(FIXTURE_TOOLS)
    failures = status["mcp_failures"]
    assert isinstance(failures, list) and len(failures) == 1, failures
    (failure,) = failures
    assert isinstance(failure, dict)
    assert failure["server"] == "ghost"
    assert "the command of the MCP server was not found" in str(failure["error"])
    assert ended == 0


# ----------------------------------------------------------------------------------------------------- with a model


@requires_model
@pytest.mark.live
async def test_a_model_calls_an_mcp_tool_and_uses_its_answer(runtime: Runtime) -> None:
    manager: RunManager = RunManager(runtime)
    started: RunRecord = await manager.start(
        "Use the tool mcp__fixture__add to add 1234 and 8766. Reply with the sum as digits only.",
        "task",
        None,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        None,
        None,
        (),
    )
    try:
        finished: RunRecord = await asyncio.wait_for(manager.wait(started.id), RUN_TIMEOUT_S)
    finally:
        await manager.shutdown()
    assert finished.status == "done", finished
    assert "10000" in re.sub(r"[ ,._]", "", finished.result), finished.result

    events: list[EventRecord] = await runtime.store.list_events(started.id, 0, 500)
    called: list[EventRecord] = [
        event for event in events if event.kind == "tool_call" and event.data["tool"] == "mcp__fixture__add"
    ]
    answered: list[EventRecord] = [
        event for event in events if event.kind == "tool_result" and event.data["tool"] == "mcp__fixture__add"
    ]
    assert called and answered
    assert answered[0].data["ok"] is True
