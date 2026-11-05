"""What one agent knows of the others (item 890): ``agents_now`` lists Dot and every agent with its role, its teams and
what it does now; ``read_agent_chat`` reads the newest messages of an agent's newest chat, fenced and marked untrusted
when a run of that chat read content third parties can author, so the reading run is tainted as that chat was. The real
store and registry of a scratch home; no model."""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from dotagent.agent.gate import TAINT_EVENTS
from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.verify import new_facts
from dotagent.config import load_default_config
from dotagent.core.errors import NotFoundError, ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.llm.types import Message, ToolCall
from dotagent.policy.types import ActionInfo
from dotagent.proactive import WATCHER_SOURCE
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import AgentRecord, RunRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import group_of
from dotagent.tools.office import AGENTS_NOW_TOOL, CHATS_TOOL, READ_AGENT_CHAT_TOOL, READ_CHAT_TOOL
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

    # The owner wrote to the Engineer's run and its next step read nothing of a stranger's: its taint is cleared, not
    # the page its chat keeps, which a reader takes still fenced (item 974, the deep review of 2026-11-05).
    await runtime.store.add_event(working.id, "steer", {"text": "Go on", "files": []})
    await runtime.store.add_event(working.id, "tool_result", {"tool": "read_file", "ok": True, "tainted": False})
    again: ToolResult = await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "engineer"})
    assert again.untrusted and "<agent_chat nonce=" in again.text

    # The Writer's chat read nothing of a stranger's: the Engineer reads it plain.
    back: ToolResult = await _call(runtime, working, engineer.id, READ_AGENT_CHAT_TOOL, {"agent": "Writer"})
    assert not back.untrusted and "<agent_chat" not in back.text and "owner: Write the notes of 2.1" in back.text

    with pytest.raises(Exception, match="no agent has that name"):
        await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "Nobody"})
    none_yet: ToolResult = await _call(runtime, mine, writer.id, READ_AGENT_CHAT_TOOL, {"agent": "Dot"})
    assert "Dot has no chat yet" in none_yet.text


async def test_a_named_run_is_read_and_only_one_of_the_agents_own(runtime: Runtime) -> None:
    """Item 902: the helper the owner sends reads the work it helps (``run``), not the agent's newest chat; a run of
    another agent's is refused."""
    builder: AgentRecord = await _agent(runtime, "Builder", "Builds the site")
    other: AgentRecord = await _agent(runtime, "Other", "Does other things")
    helped: RunRecord = await _run(runtime, builder, "Ship the pricing page", tainted=False)
    await _run(runtime, builder, "Rename the blog folder", tainted=False)
    theirs: RunRecord = await _run(runtime, other, "Count the invoices", tainted=False)
    reader: RunRecord = await _run(runtime, other, "Help Builder", tainted=False)
    newest: ToolResult = await _call(runtime, reader, other.id, READ_AGENT_CHAT_TOOL, {"agent": "Builder"})
    assert "Rename the blog folder" in newest.text
    named: ToolResult = await _call(
        runtime, reader, other.id, READ_AGENT_CHAT_TOOL, {"agent": "Builder", "run": helped.id}
    )
    assert "Ship the pricing page" in named.text and "Rename the blog folder" not in named.text
    with pytest.raises(ToolInputError):
        await _call(runtime, reader, other.id, READ_AGENT_CHAT_TOOL, {"agent": "Builder", "run": theirs.id})


async def test_a_run_finds_the_chats_by_their_titles_and_reads_one_by_its_id(runtime: Runtime) -> None:
    """Item 953: chats lists the chats of this Mac, the owner's and the agents', the newest first, found by the words of
    their titles, each saying whose it is and where it stands; a title made of a goal that came of a page or a mail is
    fenced and marks the reader untrusted. read_chat reads one by its id as read_agent_chat reads an agent's."""
    assert {CHATS_TOOL, READ_CHAT_TOOL} <= set(AGENT_CORE_TOOLS)
    assert group_of(CHATS_TOOL) == group_of(READ_CHAT_TOOL) == "office"
    planner: AgentRecord = await _agent(runtime, "Planner", "Plans the launch")
    launch: RunRecord = await _run(runtime, planner, "Plan the Gistsmith launch week", tainted=True)
    await runtime.store.set_run_status(launch.id, "running")
    mine: RunRecord = await _run(runtime, planner, "Write the Gistsmith press mail", tainted=False)

    found: ToolResult = await _call(runtime, mine, planner.id, CHATS_TOOL, {"words": "gistsmith"})
    assert not found.untrusted and "<agent_chat" not in found.text
    lines: list[str] = [line for line in found.text.splitlines() if line.startswith("- ")]
    assert [line.split(":")[0] for line in lines] == [f"- {mine.thread_id} (this chat)", f"- {launch.thread_id}"]
    assert "Plan the Gistsmith launch week · Planner's · at work · just now" in lines[1]
    nothing: ToolResult = await _call(runtime, mine, planner.id, CHATS_TOOL, {"words": "no such words anywhere"})
    assert nothing.text == "No chat whose title has 'no such words anywhere'."

    # A chat whose first goal came of a stranger's mail: its title is a third party's words.
    stranger: RunRecord = await _run(
        runtime, planner, "Answer Gistsmith's mail from Bob", tainted=False, started_tainted=True
    )
    fenced: ToolResult = await _call(runtime, mine, planner.id, CHATS_TOOL, {"words": "gistsmith"})
    assert fenced.untrusted
    assert fenced.text.index("<agent_chat") < fenced.text.index(stranger.thread_id)
    assert fenced.text.index(launch.thread_id) < fenced.text.index("<agent_chat")

    # The launch chat read a page: read_chat fences it and marks the reader; the press mail's chat it reads plain.
    read: ToolResult = await _call(runtime, mine, planner.id, READ_CHAT_TOOL, {"chat": launch.thread_id})
    assert read.untrusted and "owner: Plan the Gistsmith launch week" in read.text
    assert f"The chat {launch.thread_id} (Planner's) is working on" in read.text
    plain: ToolResult = await _call(runtime, launch, planner.id, READ_CHAT_TOOL, {"chat": mine.thread_id})
    assert not plain.untrusted and "owner: Write the Gistsmith press mail" in plain.text
    own: ToolResult = await _call(runtime, mine, planner.id, READ_CHAT_TOOL, {"chat": mine.thread_id})
    assert own.text == "That chat is this one: what it says is above."
    with pytest.raises(NotFoundError):
        await _call(runtime, mine, planner.id, READ_CHAT_TOOL, {"chat": "thr_nobody"})

    # The pre-push review: a stranger's title stays fenced once `dotx prune` took its chat's events, from what the
    # prune kept; and a watcher's chat, named after the mail that came in, is fenced whatever is kept.
    await runtime.store.finish_run(stranger.id, "done", "Answered.")
    await runtime.store.delete_events_of_runs_ended_before(time.time() + 1, TAINT_EVENTS)
    assert await runtime.store.last_event(stranger.id, "run_queued") is None
    watched = await runtime.store.create_thread("Gistsmith mail · Win a free cruise", WATCHER_SOURCE)
    await runtime.store.create_run(
        watched.id, "Read it", "task", "m:m", None, WATCHER_SOURCE, "medium", "standard", None, "ask", False,
        None, None, None,
    )  # fmt: skip
    pruned: ToolResult = await _call(runtime, mine, planner.id, CHATS_TOOL, {"words": "gistsmith"})
    assert pruned.untrusted
    fence: int = pruned.text.index("<agent_chat")
    assert fence < pruned.text.index(stranger.thread_id) and fence < pruned.text.index(watched.id)
