"""What a run handed whole to another program's agent shares, whichever program it is: OpenCode's for Zen's free models
(item 283, :mod:`dotagent.opencode.run`) and Claude Code on the owner's plan (item 1013,
:mod:`dotagent.claude_code.run`). Each keeps the program's session of a chat from run to run (:class:`ChatSession`),
gives the agent Dot's own tools at the chat's door of the bridge, a call of one run as a call of Dot's own loop
(:func:`bridged_call`, in the context :func:`tool_context` makes), and lets go of the questions a run that ends still
waits for (:func:`let_go`)."""

import asyncio
from pathlib import Path

from dotagent.agent.executor import ToolOutcome, execute_calls
from dotagent.agent.gate import RunGate
from dotagent.agent.loop import AGENT_CORE_TOOLS, AgentRun, browser_of
from dotagent.agent.progress import can_ask_user, question_mode
from dotagent.agent.scaffold import PROFILE_RUNS, Scaffold, scaffold_for
from dotagent.agent.verify import RunFacts, new_facts
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.llm.router import ModelRef
from dotagent.llm.types import ModelInfo, ToolCall
from dotagent.runtime import Runtime
from dotagent.store.models import AgentRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import ToolRegistry

log = get_logger(__name__)

# How long a run that ends lets the questions it still decides finish (:func:`let_go`).
ASK_GRACE_S: float = 10.0


class ChatSession(FrozenModel):
    """The session of a chat in another program's agent, and how many of the chat's messages it has seen."""

    session_id: str
    seen: int


def read_chat(path: Path) -> ChatSession | None:
    """The session kept at ``path``, or ``None`` when none is. Blocking."""
    return ChatSession.model_validate_json(path.read_bytes()) if path.exists() else None


def write_chat(path: Path, chat: ChatSession) -> None:
    """Keep ``chat`` at ``path``, in a folder only the owner reads, whole or not at all. Blocking."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged: Path = path.with_suffix(".tmp")
    staged.write_text(chat.model_dump_json() + "\n", encoding="utf-8")
    staged.replace(path)


async def let_go(runtime: Runtime, run_id: str, asks: tuple[asyncio.Task[None], ...]) -> None:
    """End the permissions a run that ends still decides. The questions it waits for are let go, and each task stores
    its question as decided before it ends, so no page keeps offering one; a task cancelled in the middle of that would
    leave its question open. One still under way after ``ASK_GRACE_S`` is cancelled, and one that failed is logged:
    the run's own ending is what it reports."""
    runtime.approvals.release_run(run_id)
    if not asks:
        return
    _, late = await asyncio.wait(asks, timeout=ASK_GRACE_S)
    for task in late:
        task.cancel()
    for ended in await asyncio.gather(*asks, return_exceptions=True):
        if isinstance(ended, Exception):
            log.warning("handover_permission_failed", run_id=run_id, error=str(ended), error_type=type(ended).__name__)


def _ignore(outcome: ToolOutcome) -> None:
    """A call of the agent to one of Dot's tools ends alone: only its caller waits for its outcome."""


async def bridged_call(registry: ToolRegistry, context: ToolContext, gate: RunGate, call: ToolCall) -> ToolResult:
    """A call of the agent to one of Dot's tools, run as Dot's loop runs a call of its own model's: checked, decided by
    the run's gate (the owner's rules, the preset and the approvals), held to the tool's time limit and shown in the
    run's timeline under the tool's own name."""
    outcomes: list[ToolOutcome] = await execute_calls((call,), registry, {}, context, gate, 1, _ignore)
    return outcomes[0].result


async def tool_context(agent: AgentRun, ref: ModelRef, info: ModelInfo, run_tools: frozenset[str]) -> ToolContext:
    """What Dot's tools get while they run for the agent: what a run of Dot's own loop gives them, its ``run_tools``
    (the tools the agent was given) and the run's spawner, through which the team's tools hand work on (item 1063)."""
    runtime: Runtime = agent.runtime
    run = agent.run
    scaffold: Scaffold = scaffold_for(
        runtime.config.agent,
        info,
        await runtime.store.model_profile(ref.label, PROFILE_RUNS),
        runtime.context_sizes.of(ref.label),
    )
    facts: RunFacts = new_facts()
    return ToolContext(
        runtime=runtime,
        browser=await browser_of(runtime, run),
        run_id=run.id,
        agent_id=run.agent_id,
        thread_id=run.thread_id,
        mode=run.mode,
        approvals=run.approvals,
        questions=question_mode(
            runtime.config.agent.questions,
            can_ask_user(run.source, run.parent_run_id, run.approvals),
            agent.spawner.answers_reach_runs,
        ),
        workspace=runtime.workspace,
        gate=agent.gate,
        cancel=agent.cancel,
        output_chars=scaffold.tool_output_chars,
        facts=lambda: facts,
        spawner=agent.spawner,
        run_tools=run_tools,
        # The owner's words, which a skill saved in their words keeps (skill_save), as in Dot's own loop.
        user_text=lambda: "\n".join(agent.gate.instructions),
        tainted=lambda: agent.gate.tainted,
        ever_tainted=lambda: agent.gate.ever_tainted,
    )


async def agent_tools(runtime: Runtime, agent_id: str | None) -> frozenset[str] | None:
    """The tools the agent ``agent_id`` is bounded to, with the core ones (item 86); ``None`` when it is not bounded."""
    record: AgentRecord | None = None if agent_id is None else await runtime.store.get_agent(agent_id)
    if record is None or not record.tools:
        return None
    return frozenset(record.tools) | frozenset(AGENT_CORE_TOOLS)
