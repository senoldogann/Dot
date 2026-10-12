"""MCP servers end to end: a real server over stdio, its tools run through the registry the agent uses, and a server
that cannot start is reported with everything that helps to fix it.

The server is ``tests/fixtures/mcp_fixture_server.py``, started like any server a user would configure. Only the test
with a model needs the local Ollama server and a cloud model; it is skipped without them.
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.config import McpServerConfig, load_config
from dotagent.core.errors import ConfigError, McpError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.mcp.hub import McpHub, inspect_servers, open_hub
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.base import ACTING_MODES, ALL_MODES, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext
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


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {MODEL}")


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
    return _home_with(tmp_path_factory.mktemp("home"), f'[models]\nmain = "ollama:{MODEL}"\n\n{fixture_server}')


@pytest.fixture(scope="module")
def environment(home: Path) -> Iterator[None]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(home / "logs"))
    patch.setenv("DOT_TEST_MCP_VALUE", SECRET_VALUE)
    patch.setenv("DOT_TEST_NOT_FORWARDED", "leak")
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
    first, _ = await _call(runtime, "mcp__fixture__process_id", {})
    old_pid: int = int(_answer(first, "process_id"))
    os.kill(old_pid, signal.SIGKILL)

    with pytest.raises(McpError, match="closed the connection while running add"):
        await _call(runtime, "mcp__fixture__add", {"a": 1, "b": 1})

    again, _ = await _call(runtime, "mcp__fixture__add", {"a": 1, "b": 1})
    assert _answer(again, "add") == "2"
    second, _ = await _call(runtime, "mcp__fixture__process_id", {})
    assert int(_answer(second, "process_id")) != old_pid


# --------------------------------------------------------------------------------------------- the hub on its own


async def test_a_missing_command_is_reported_with_the_path_that_was_searched(login_env: dict[str, str]) -> None:
    ghost: McpServerConfig = McpServerConfig(name="ghost", command="dot-no-such-server-binary")
    with pytest.raises(McpError, match="the command of the MCP server was not found") as caught:
        await open_hub([ghost], login_env)
    context: JsonObject = caught.value.context
    assert context["server"] == "ghost"
    assert context["command"] == "dot-no-such-server-binary"
    assert context["searched_path"] == login_env["PATH"]
    assert str(context["log"]).endswith("mcp-ghost.log")


async def test_a_server_that_quits_shows_what_it_printed(login_env: dict[str, str]) -> None:
    quitter: McpServerConfig = _python_server(
        "quitter", "import sys; sys.stderr.write('configuration file is missing\\n'); sys.exit(3)", 30.0
    )
    with pytest.raises(McpError, match="closed the connection while starting") as caught:
        await open_hub([quitter], login_env)
    assert "configuration file is missing" in str(caught.value.context["stderr_tail"])


async def test_a_silent_server_runs_into_the_start_timeout_and_is_stopped(login_env: dict[str, str]) -> None:
    marker: str = f"dot-silent-{uuid.uuid4().hex}"
    silent: McpServerConfig = _python_server("silent", f"import time; time.sleep(120)  # {marker}", 2.0)
    started: float = time.monotonic()
    with pytest.raises(McpError, match="did not answer in time") as caught:
        await open_hub([silent], login_env)
    assert caught.value.context["startup_timeout_s"] == 2.0
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
    with pytest.raises(ConfigError, match="refers to secrets that are not set") as caught:
        await open_hub([needy], login_env)
    assert caught.value.context["missing"] == ["DOT_TEST_SURELY_UNSET_SECRET"]
    assert await _count_processes(marker) == 0


async def test_the_allow_list_picks_the_tools_and_must_name_existing_ones(login_env: dict[str, str]) -> None:
    picked: McpServerConfig = _fixture_server("subset").model_copy(update={"allow": ["echo", "add", "echo"]})
    (report,) = await inspect_servers([picked], login_env)
    assert report.error is None
    assert report.listed == len(FIXTURE_TOOLS)
    assert [tool.exposed_name for tool in report.tools] == ["mcp__subset__echo", "mcp__subset__add"]

    wrong: McpServerConfig = _fixture_server("wrong").model_copy(update={"allow": ["add", "nope"]})
    with pytest.raises(McpError, match="allow list names tools that the MCP server does not offer") as caught:
        await open_hub([wrong], login_env)
    assert caught.value.context["missing"] == ["nope"]


async def test_a_disabled_server_is_not_started(login_env: dict[str, str]) -> None:
    sleeping: McpServerConfig = McpServerConfig(name="sleeping", command="dot-no-such-server-binary", enabled=False)
    hub: McpHub = await open_hub([sleeping], login_env)
    try:
        assert hub.tools == ()
    finally:
        await hub.close()
    (report,) = await inspect_servers([sleeping], login_env)
    assert (report.enabled, report.error, report.tools) == (False, None, ())


async def test_when_one_server_fails_the_others_are_stopped_again(login_env: dict[str, str]) -> None:
    marker: str = f"dot-orphan-{uuid.uuid4().hex}"
    good: McpServerConfig = McpServerConfig(name="good", command=sys.executable, args=[str(FIXTURE), marker])
    ghost: McpServerConfig = McpServerConfig(name="ghost", command="dot-no-such-server-binary")
    with pytest.raises(McpError, match="the command of the MCP server was not found"):
        await open_hub([good, ghost], login_env)
    await _assert_gone(marker)


async def test_two_servers_with_the_same_tool_names_stay_apart(login_env: dict[str, str]) -> None:
    hub: McpHub = await open_hub([_fixture_server("one"), _fixture_server("two")], login_env)
    try:
        names: list[str] = [tool.exposed_name for tool in hub.tools]
        assert len(names) == len(set(names)) == 2 * len(FIXTURE_TOOLS)
        assert "mcp__one__add" in names and "mcp__two__add" in names
    finally:
        await hub.close()


# ------------------------------------------------------------------------------------------------- the command line


async def test_mcp_list_shows_the_tools_of_a_working_server(home: Path) -> None:
    outcome: CliOutcome = await _dotx(home, ["mcp", "list"])
    assert outcome.code == 0, outcome
    assert f"fixture: ready, {len(FIXTURE_TOOLS)} of {len(FIXTURE_TOOLS)} tools offered to the model" in outcome.out
    assert "mcp__fixture__add  [read-only]  Add two integers." in outcome.out
    assert "mcp__fixture__wipe  [acts: destructive, external_send]" in outcome.out
    assert "mcp__fixture__notes_save  [acts]" in outcome.out


async def test_a_broken_server_is_reported_by_mcp_list_and_stops_a_run_without_hanging(tmp_path: Path) -> None:
    broken: Path = _home_with(
        tmp_path / "broken-home", '[[mcp]]\nname = "ghost"\ncommand = "dot-no-such-server-binary"\n'
    )
    listed: CliOutcome = await _dotx(broken, ["mcp", "list"])
    assert listed.code == 1, listed
    assert "ghost: FAILED" in listed.out
    assert "the command of the MCP server was not found" in listed.out
    assert "searched_path" in listed.out

    started: float = time.monotonic()
    ran: CliOutcome = await _dotx(broken, ["run", "--no-input", "say hello"])
    assert ran.code == 2, ran
    assert "error: the command of the MCP server was not found" in ran.err
    assert time.monotonic() - started < CLI_TIMEOUT_S


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
