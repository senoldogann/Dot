"""What one agent knows of the others (item 890): ``agents_now`` lists Dot and every agent with its role, its teams and
what it does now; ``read_agent_chat`` reads the newest messages of an agent's newest chat, fenced and marked untrusted
when a run of that chat read content third parties can author, so the reading run is tainted as that chat was. The real
store and registry of a scratch home; no model."""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.verify import new_facts
from dotagent.config import load_default_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.llm.types import Message, ToolCall
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import AgentRecord, RunRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import group_of
from dotagent.tools.office import AGENTS_NOW_TOOL, READ_AGENT_CHAT_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")


class _Gate:
    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime() -> AsyncIterator[Runtime]:
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        built: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            yield built
        finally:
            await close_runtime(built)


async def _agent(runtime: Runtime, name: str, role: str) -> AgentRecord:
    return await runtime.store.create_agent(name, role, "", "", None, None, ("web_search",), None, None)


async def _run(
    runtime: Runtime, agent: AgentRecord, goal: str, tainted: bool, started_tainted: bool = False
) -> RunRecord:
    store = runtime.store
    thread = await store.create_thread(goal, "ui")
    run: RunRecord = await store.create_run(
        thread.id, goal, "task", "m:m", None, "ui", "medium", "standard", None, "ask", False, None, agent.id, None
    )
    await store.add_event(run.id, "run_queued", {"tainted": started_tainted})
    await store.append_messages(
        thread.id,
        [
            Message(
                role="user",
                content=goal,
                images=(),
                tool_calls=(),
                tool_call_id=None,
                tool_name=None,
                thinking=None,
                raw=(),
                raw_origin=None,
            ),
            Message(
                role="assistant",
                content="Looking at the failing test.",
                images=(),
                tool_calls=(ToolCall(id="c1", name="read_file", arguments={"path": "app.py"}),),
                tool_call_id=None,
                tool_name=None,
                thinking=None,
                raw=(),
                raw_origin=None,
            ),
            Message(
                role="tool",
                content="def main():\n    return 1",
                images=(),
                tool_calls=(),
                tool_call_id="c1",
                tool_name="read_file",
                thinking=None,
                raw=(),
                raw_origin=None,
            ),
        ],
    )
    if tainted:
        await store.add_event(
            run.id, "tool_result", {"call_id": "c2", "tool": "web_fetch", "ok": True, "tainted": True}
        )
    return run


async def _call(runtime: Runtime, run: RunRecord, agent_id: str | None, name: str, arguments: JsonObject) -> ToolResult:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=agent_id,
        thread_id=run.thread_id,
        mode=run.mode,
        approvals=run.approvals,
        questions="wait",
        workspace=workspace_dir(),
        gate=_Gate(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    assert prepared.action.kind == "read"
    return await prepared.run(ctx)


async def test_an_agent_sees_the_others_and_reads_one_s_chat_tainted_as_that_chat_was(runtime: Runtime) -> None:
    assert {AGENTS_NOW_TOOL, READ_AGENT_CHAT_TOOL} <= set(AGENT_CORE_TOOLS)
    assert group_of(AGENTS_NOW_TOOL) == group_of(READ_AGENT_CHAT_TOOL) == "office"
    engineer: AgentRecord = await _agent(runtime, "Engineer", "Fixes the app's bugs")
    writer: AgentRecord = await _agent(runtime, "Writer", "Writes the release notes")
    await runtime.store.create_team("Engineer team", "Builds the app", [engineer.id], None)
    working: RunRecord = await _run(runtime, engineer, "Fix the failing login test", tainted=True)
    await runtime.store.set_run_status(working.id, "running")
    mine: RunRecord = await _run(runtime, writer, "Write the notes of 2.1", tainted=False)

    seen: ToolResult = await _call(runtime, mine, writer.id, AGENTS_NOW_TOOL, {})
    assert "- Dot (leads the work and the teams)" in seen.text
    assert "- Engineer, Fixes the app's bugs; teams: Engineer team:" in seen.text
    assert "Fix the failing login test" in seen.text
    assert "- Writer (you), Writes the release notes" in seen.text
    # The Engineer's run read a page after it started: the list quotes its goal alone, written before: plain.
    assert not seen.untrusted and "<agent_chat" not in seen.text
    # A run whose goal came of a page (a watcher's mail, a page's todo) makes the list fenced and the reader tainted.
    scout: AgentRecord = await _agent(runtime, "Scout", "Reads the mail")
    await _run(runtime, scout, "Answer the mail of a stranger", tainted=False, started_tainted=True)
    fenced: ToolResult = await _call(runtime, mine, writer.id, AGENTS_NOW_TOOL, {})
    assert fenced.untrusted and "<agent_chat nonce=" in fenced.text

    read: ToolResult = await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "engineer"})
    assert read.untrusted and not read.is_error
    assert "owner: Fix the failing login test" in read.text
    # Its goal too stands inside the fence: it may have come of what the run read.
    assert read.text.index("<agent_chat") < read.text.index("working on: Fix the failing login test")
    assert "agent: Looking at the failing test. [called read_file]" in read.text
    assert "  read_file -> def main(): return 1" in read.text

    # The Writer's chat read nothing of a stranger's: the Engineer reads it plain.
    back: ToolResult = await _call(runtime, working, engineer.id, READ_AGENT_CHAT_TOOL, {"agent": "Writer"})
    assert not back.untrusted and "<agent_chat" not in back.text and "owner: Write the notes of 2.1" in back.text

    with pytest.raises(Exception, match="no agent has that name"):
        await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "Nobody"})
    none_yet: ToolResult = await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "Dot"})
    assert "Dot has no chat yet" in none_yet.text
