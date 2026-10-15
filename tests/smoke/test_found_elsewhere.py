"""The skills and MCP servers of the Mac's other agents, in a home made here, through the API of a real in-process
server and through the real loop. ``GET /api/skills/elsewhere`` lists them by name, says which one Dot uses, and never
a key (item 164). The owner adds a skill and a server with ``PUT`` (item 165): the skill becomes a copy in Dot's skills
folder, offered to runs at once; the server is recorded by its name and the fingerprint of its entry and, when Dot
next starts, is started as its agent's file says, key and all, unless that entry changed since; a run may read but
never change the copy or the record. Removing them takes both back, the server even once its agent no longer has it.
And find_help tells a run of a skill another agent set up, with how to ask the owner to add it, as content others wrote, which the run counts as untrusted.

No network and no model service is used: the MCP server is the test fixture, and the scripted model plays the run.
"""

import asyncio
import json
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp.types import CallToolResult, TextContent

from dotagent.agent.manager import RunManager
from dotagent.agent.verify import new_facts
from dotagent.config import AppConfig, SkillsConfig, load_default_config
from dotagent.core.paths import added_servers_path, skills_dir, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message
from dotagent.mcp.types import McpTool
from dotagent.policy.paths import classify_path
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import RunRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
KEY: str = f"key-{secrets.token_hex(12)}"
PORT: int = 8765
ELSEWHERE: str = "/api/skills/elsewhere"
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
NEED: str = "fill in a PDF form"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_ASK: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="find_help", arguments={"need": NEED}),))
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The form is filled in.", "success": True}),)
)


def _other_agents(home: Path) -> Path:
    """A Claude Code skill and a Claude Desktop server in ``home``; the folder of Claude Code's skills."""
    skills: Path = home / ".claude" / "skills"
    (skills / "pdf-tools").mkdir(parents=True)
    (skills / "pdf-tools" / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: Fill and merge PDF forms.\n---\nUse pdftk.\n", encoding="utf-8"
    )
    desktop: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(
        json.dumps({"mcpServers": {"notion": {"command": "npx", "args": ["-y", "notion-mcp"], "env": {"KEY": KEY}}}}),
        encoding="utf-8",
    )
    return skills


def _desktop_servers(desktop: Path, servers: dict[str, object]) -> None:
    """Claude Desktop's file at ``desktop``, setting up ``servers``."""
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _fixture_server(env: dict[str, str]) -> dict[str, object]:
    """Claude Desktop's entry of the fixture MCP server, started with ``env``."""
    return {"command": sys.executable, "args": [str(FIXTURE)], "env": env}


def _fixture_agents(home: Path) -> Path:
    """A Codex skill in ``home``, and the fixture MCP server set up in Claude Desktop with a key in its environment;
    the Codex skill's folder."""
    folder: Path = home / ".codex" / "skills" / "csv-clean"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: csv-clean\ndescription: Clean a CSV file.\n---\nUse csvkit.\n", encoding="utf-8"
    )
    desktop: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    _desktop_servers(desktop, {"Fixture_Notes": _fixture_server({"KEY": KEY})})
    return folder


@asynccontextmanager
async def _api(config: AppConfig) -> AsyncIterator[httpx.AsyncClient]:
    """A client of the API of a server opened on ``config``; the server is closed with it."""
    runtime: Runtime = await open_runtime(config, {})
    try:
        manager: RunManager = RunManager(runtime)
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
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url=f"http://127.0.0.1:{PORT}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client:
                yield client
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def test_the_api_lists_what_other_agents_set_up_and_which_of_it_dot_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_skills: Path = _other_agents(tmp_path)
    with scratch_home() as home, eval_environment(home, {}):
        base: AppConfig = eval_config(load_default_config())
        config: AppConfig = base.model_copy(update={"skills": SkillsConfig(dirs=[str(claude_skills)])})
        async with _api(config) as client:
            response: httpx.Response = await client.get(ELSEWHERE)
    assert response.status_code == 200, response.text
    found: dict[str, list[dict[str, object]]] = response.json()
    assert [(skill["agent"], skill["name"], skill["used"]) for skill in found["skills"]] == [
        ("Claude Code", "pdf-tools", True)
    ]
    assert [(server["agent"], server["name"], server["reaches"], server["used"]) for server in found["servers"]] == [
        ("Claude Desktop", "notion", "npx", False)
    ]
    assert KEY not in response.text


class _Gate:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> ToolResult:
    """The result of the tool ``name`` called with ``arguments`` outside a run."""
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="found-test",
        agent_id=None,
        thread_id="found-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx)


def _marks(response: httpx.Response) -> list[tuple[object, ...]]:
    """Each skill and server of a found list, with whether Dot uses it and whether the owner added it."""
    found: dict[str, list[dict[str, object]]] = response.json()
    return [
        (item["agent"], item["name"], item["used"], item["added"]) for item in [*found["skills"], *found["servers"]]
    ]


async def test_the_owner_adds_what_other_agents_set_up_and_dot_uses_it_until_it_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_skill: Path = _fixture_agents(tmp_path)
    desktop: Path = tmp_path.joinpath("Library", "Application Support", "Claude", "claude_desktop_config.json")
    skill: dict[str, str] = {"kind": "skill", "agent": "Codex", "name": "csv-clean"}
    server: dict[str, str] = {"kind": "server", "agent": "Claude Desktop", "name": "Fixture_Notes"}
    with scratch_home() as home, eval_environment(home, {}):
        config: AppConfig = eval_config(load_default_config())
        async with _api(config) as client:
            skill_added: httpx.Response = await client.put(ELSEWHERE, json={**skill, "added": True})
            # The composer's menu lists the skills as a run is offered them.
            palette: httpx.Response = await client.get("/api/palette")
            server_added: httpx.Response = await client.put(ELSEWHERE, json={**server, "added": True})
            server_again: httpx.Response = await client.put(ELSEWHERE, json={**server, "added": True})
        assert (skill_added.status_code, server_added.status_code) == (200, 200), (skill_added.text, server_added.text)
        assert server_again.status_code == 409 and "already" in server_again.json()["message"], server_again.text
        copy: Path = skills_dir() / "csv-clean"
        copied: bool = await asyncio.to_thread((copy / "SKILL.md").is_file) and not await asyncio.to_thread(
            copy.is_symlink
        )
        record: str = added_servers_path().read_text(encoding="utf-8")

        # Dot's next start: the server is started as Claude Desktop writes it, under Dot's name for it.
        runtime: Runtime = await open_runtime(config, {})
        try:
            failures: int = len(runtime.mcp.failures)
            tools: dict[str, McpTool] = {
                tool.remote_name: tool for tool in runtime.mcp.tools if tool.server == "fixture-notes"
            }
            result: CallToolResult = await runtime.mcp.call(tools["environment"], {"name": "KEY"})
            # What a run may read but never change: the copy and the record.
            guarded: list[str] = [
                classify_path(path.resolve(), workspace_dir(), runtime.protected, runtime.dot_paths)
                for path in (copy / "SKILL.md", added_servers_path())
            ]
        finally:
            await close_runtime(runtime)

        # Changed in Claude Desktop after the owner's yes: the next start leaves it out, and the found list says why.
        _desktop_servers(desktop, {"Fixture_Notes": _fixture_server({"KEY": KEY, "MORE": "1"})})
        runtime = await open_runtime(config, {})
        try:
            changed_start: tuple[int, list[str]] = (
                len(runtime.mcp.failures),
                [tool.remote_name for tool in runtime.mcp.tools if tool.server == "fixture-notes"],
            )
        finally:
            await close_runtime(runtime)

        async with _api(config) as client:
            changed: httpx.Response = await client.get(ELSEWHERE)
            # Gone from Claude Desktop: it is still taken back by its agent and its name.
            _desktop_servers(desktop, {})
            gone: httpx.Response = await client.get(ELSEWHERE)
            skill_removed: httpx.Response = await client.put(ELSEWHERE, json={**skill, "added": False})
            server_removed: httpx.Response = await client.put(ELSEWHERE, json={**server, "added": False})
        deleted: bool = not await asyncio.to_thread(copy.exists)
        record_after: str = added_servers_path().read_text(encoding="utf-8")

    # Dot reads its copy, not Codex's folder.
    assert _marks(server_added) == [
        ("Codex", "csv-clean", False, True),
        ("Claude Desktop", "Fixture_Notes", False, True),
    ]
    assert copied and "csv-clean" in [entry["name"] for entry in palette.json()["skills"]], palette.text
    entries: list[dict[str, str]] = json.loads(record)["servers"]
    assert [(entry["agent"], entry["name"], len(entry["entry"])) for entry in entries] == [
        ("Claude Desktop", "Fixture_Notes", 64)
    ]
    assert (failures, {"add", "environment"} <= tools.keys()) == (0, True), sorted(tools)
    assert [part.text for part in result.content if isinstance(part, TextContent)] == [KEY]
    assert guarded == ["dot", "dot"]
    assert changed_start == (0, [])
    assert [(item["name"], item["added"], item["refusal"]) for item in changed.json()["servers"]] == [
        ("Fixture_Notes", True, "its entry changed after it was added; remove it and add it again")
    ]
    assert gone.json()["gone"] == [{"kind": "server", "agent": "Claude Desktop", "name": "Fixture_Notes"}]
    assert (skill_removed.status_code, server_removed.status_code) == (200, 200), server_removed.text
    assert (_marks(server_removed), server_removed.json()["gone"]) == ([("Codex", "csv-clean", False, False)], [])
    assert deleted and (codex_skill / "SKILL.md").is_file()
    assert json.loads(record_after) == {"servers": []}
    responses: tuple[httpx.Response, ...] = (skill_added, server_added, changed, gone, server_removed)
    assert not [text for text in (record, *(response.text for response in responses)) if KEY in text]


async def test_find_help_tells_a_run_of_a_skill_another_agent_set_up_and_to_ask_the_owner_to_add_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _other_agents(tmp_path)
    with scratch_home() as home, eval_environment(home, {}):
        base: AppConfig = eval_config(load_default_config())
        config: AppConfig = base.model_copy(update={"skills": SkillsConfig(dirs=[], elsewhere=True)})
        runtime: Runtime = await open_runtime(config, {})
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_ASK, _FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Fill in the form.",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    UI_SOURCE,
                    None,
                    None,
                    "standard",
                    None,
                    "yolo",
                    False,
                    (),
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
            # Others wrote what it lists: a run counts it as untrusted, as it does a web page.
            again: ToolResult = await _call(runtime, "find_help", {"need": NEED})
        finally:
            await close_runtime(runtime)
    answers: list[str] = [message.content for message in messages if message.role == "tool"]
    assert done.status == "done", done.result
    assert (again.untrusted, again.text.splitlines()[1]) == (True, answers[0].splitlines()[1]), again.text
    lines: list[str] = answers[0].splitlines()
    assert lines[1] == (
        "1. Skill found elsewhere pdf-tools: Fill and merge PDF forms (set up in Claude Code). Use: ask the owner to "
        'add it to Dot in Settings › Skills, under Found on this Mac; once it is added, call skill with name "pdf-tools".'
    ), answers[0]
    assert lines[-1].endswith(
        " Also searched 1 skill and 1 MCP server that other agents of this Mac set up and the owner can add."
    ), answers[0]
    assert KEY not in answers[0]
