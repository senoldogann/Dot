"""The team tools. ``message_agent`` (item 87): a run sends a message to one of the owner's agents, which answers in
a run of its own (the agent's instructions, tools and bounds, delegated by the sender), and the reply comes back as
the result. The runs behind the messages are the thread of the team (``GET /api/team``): a run with the ``team``
source. ``create_agent`` (item 99): a run makes a lasting agent the way the Agents page does, for a role the work
needs again or in parallel; the record keeps the run that made it. An agent a run makes can do no more than the run
(item 307): only tools the run has, a helper model or the run's own, and a run that only reads (research) makes none."""

import secrets
from collections.abc import Sequence

from pydantic import Field

from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.text import truncate_tail, unclosing
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo
from dotagent.store.models import AgentRecord, RunMode, RunRecord
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import DelegateOutcome, Spawner, ToolContext
from dotagent.tools.delegate import default_child_mode

MESSAGE_AGENT_TOOL: str = "message_agent"
CREATE_AGENT_TOOL: str = "create_agent"
TEAM_SOURCE: str = "team"
_NONCE_BYTES: int = 8
_TIMEOUT_S: float = 3600.0
_CREATE_TIMEOUT_S: float = 30.0
_SUMMARY_CHARS: int = 100
_NO_REPLY: str = "(it replied nothing)"
# The bounds of a name, a role and instructions, the same as the Agents page's (AgentRequest).
_NAME_CHARS: int = 60
_ROLE_CHARS: int = 120
_INSTRUCTIONS_CHARS: int = 8000
# The most agents one run makes (item 307): a team for the work at hand, not a crowd.
_MOST_MADE_PER_RUN: int = 3


class MessageAgentArgs(ToolArgs):
    agent: str = Field(min_length=1, description="The agent's name, as the team is listed.")
    message: str = Field(
        min_length=1,
        description="What you ask, every input it needs (paths, URLs, values) and what its reply must contain.",
    )


def find_agent(agents: list[AgentRecord], name: str) -> AgentRecord | None:
    """The agent whose name or id is ``name``, the name read without regard to case; ``None`` when there is none."""
    wanted: str = name.strip().casefold()
    return next((agent for agent in agents if agent.id == name.strip() or agent.name.casefold() == wanted), None)


def _fence(text: str, nonce: str, run_id: str) -> str:
    """Wrap a reply that may carry third-party text so that only ``nonce`` closes it."""
    safe: str = unclosing(text, "agent_reply")
    return f'<agent_reply nonce="{nonce}" run="{run_id}">\n{safe}\n</agent_reply nonce="{nonce}">'


def render_reply(agent: AgentRecord, outcome: DelegateOutcome, nonce: str) -> ToolResult:
    """The tool's result: who answered, how the run ended, and the reply, fenced when the run read untrusted content."""
    run: RunRecord = outcome.run
    reply: str = run.result if run.result.strip() else _NO_REPLY
    body: str = _fence(reply, nonce, run.id) if outcome.tainted else reply
    text: str = f"{agent.name} (run {run.id}) ended as {run.status} after {run.steps} steps.\n{body}"
    result: ToolResult = ok(text) if run.status == "done" else fail(text)
    return mark_untrusted(result) if outcome.tainted else result


def _message_agent_tool() -> RegisteredTool:
    def classify(args: MessageAgentArgs, ctx: ToolContext) -> ActionInfo:
        shown: str = truncate_tail(" ".join(args.message.split()), _SUMMARY_CHARS)
        return ActionInfo(
            tool=MESSAGE_AGENT_TOOL,
            kind="delegate",
            risk="safe",
            summary=f"Message {args.agent.strip()}: {shown}",
            target="",
            flags=(),
        )

    async def run(args: MessageAgentArgs, ctx: ToolContext) -> ToolResult:
        spawner: Spawner | None = ctx.spawner
        if spawner is None:
            raise ToolExecutionError("this run cannot message agents", {"run_id": ctx.run_id})
        agents: list[AgentRecord] = await ctx.runtime.store.list_agents()
        agent: AgentRecord | None = find_agent(agents, args.agent)
        if agent is None:
            raise ToolInputError(
                "no agent has that name", {"agent": args.agent, "team": ", ".join(found.name for found in agents)}
            )
        own: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
        if own.agent_id == agent.id:
            raise ToolInputError("an agent does not message itself", {"agent": agent.name})
        mode: RunMode = default_child_mode(ctx.mode)
        outcome: DelegateOutcome = await spawner.delegate(ctx.run_id, args.message, mode, None, agent.id)
        return render_reply(agent, outcome, secrets.token_hex(_NONCE_BYTES))

    return make_tool(
        name=MESSAGE_AGENT_TOOL,
        description=(
            "Send a message to an agent of the team and get its reply. It works in a run of its own with its own "
            "instructions and tools, cannot see this conversation and answers once: send everything it needs. Use it "
            "when a part fits an agent's role better than yours."
        ),
        args_model=MessageAgentArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class CreateAgentArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=_NAME_CHARS, description="A short name.")
    role: str = Field(min_length=1, max_length=_ROLE_CHARS, description="What it is for, in one line.")
    instructions: str = Field(
        max_length=_INSTRUCTIONS_CHARS, description="How it works; they open the system prompt of its runs."
    )
    tools: list[str] = Field(default_factory=list, description="Tool names it may use, of yours; empty gives it yours.")
    model: str = Field(
        default="", description="A helper model (provider:model) for its runs; empty: the configured one."
    )


def agent_tools(asked: Sequence[str], run_tools: frozenset[str], maker_tools: Sequence[str]) -> tuple[str, ...]:
    """The tools of an agent a run makes (item 307): the ones ``asked``, each one the run has (``run_tools``), or with
    none asked the bound of the agent whose run it is (``maker_tools``; empty, every tool, for Dot's run and for an
    agent the owner gave every tool). An agent gets no tool its maker lacks."""
    beyond: list[str] = sorted(set(asked) - run_tools)
    if beyond:
        raise ToolInputError(
            f"an agent gets only tools this run has, not {', '.join(beyond)}", {"tools": ", ".join(beyond)}
        )
    return tuple(dict.fromkeys(asked)) if asked else tuple(maker_tools)


def agent_model(asked: str, allowed: Sequence[str]) -> str:
    """The model of an agent a run makes (item 307): empty for the configured one, or one of ``allowed`` (the helper
    models and the run's own, as ``provider:model``), compared by their labels."""
    if not asked.strip():
        return ""
    label: str = parse_model_ref(asked.strip()).label
    labels: list[str] = [parse_model_ref(model).label for model in allowed]
    if label not in labels:
        models: str = ", ".join(dict.fromkeys(labels))
        raise ToolInputError(
            f"an agent's model is a helper model or this run's own ({models}); leave it empty for the configured one",
            {"model": asked, "models": models},
        )
    return label


def render_made(agent: AgentRecord) -> str:
    """The tool's result: the teammate, what it is, and how to work with it."""
    tools: str = ", ".join(agent.tools) if agent.tools else "every tool"
    return (
        f"Made the agent {agent.name} ({agent.role}); tools: {tools}. It lasts beyond this run: it has its own page "
        f"under Agents and the owner can change it there. Send it work with message_agent (agent: {agent.name!r}); "
        "it answers in a run of its own that cannot see this conversation."
    )


def _create_agent_tool() -> RegisteredTool:
    def classify(args: CreateAgentArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=CREATE_AGENT_TOOL,
            kind="meta",
            risk="low",
            summary=f"Make the agent {args.name.strip()}: {args.role.strip()}",
            target="",
            # A teammate shapes every later run, the way a saved note does.
            flags=("persistence",),
        )

    async def run(args: CreateAgentArgs, ctx: ToolContext) -> ToolResult:
        name: str = args.name.strip()
        if ctx.run_tools is None:
            raise ToolExecutionError("this run cannot make agents", {"run_id": ctx.run_id})
        own: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
        model: str = agent_model(args.model, [*ctx.runtime.config.models.helpers, own.model])
        unknown: list[str] = sorted(set(args.tools) - set(ctx.runtime.registry.tools))
        if unknown:
            raise ToolInputError(f"no tool has the name {', '.join(unknown)}", {"tools": ", ".join(unknown)})
        maker: AgentRecord | None = None if ctx.agent_id is None else await ctx.runtime.store.get_agent(ctx.agent_id)
        tools: tuple[str, ...] = agent_tools(args.tools, ctx.run_tools, () if maker is None else maker.tools)
        agents: list[AgentRecord] = await ctx.runtime.store.list_agents()
        made_here: list[str] = [agent.name for agent in agents if agent.made_by == ctx.run_id]
        if len(made_here) >= _MOST_MADE_PER_RUN:
            raise ToolInputError(
                f"a run makes at most {_MOST_MADE_PER_RUN} agents; send the work to the ones it made: "
                f"{', '.join(made_here)}",
                {"made": ", ".join(made_here)},
            )
        taken: AgentRecord | None = find_agent(agents, name)
        if taken is not None:
            raise ToolInputError(
                "an agent has that name already; message it, or choose another name",
                {"agent": taken.name, "role": taken.role},
            )
        made: AgentRecord = await ctx.runtime.store.create_agent(
            name, args.role.strip(), args.instructions.strip(), model, None, None, tools, ctx.run_id, None
        )
        return ok(render_made(made))

    return make_tool(
        name=CREATE_AGENT_TOOL,
        description=(
            "Make a lasting teammate: an agent with a name, a role, instructions and tools, listed under Agents. "
            "For a role the work needs again or in parallel, never for one step; send it work with message_agent."
        ),
        args_model=CreateAgentArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_CREATE_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_team_tools() -> tuple[RegisteredTool, ...]:
    return (_message_agent_tool(), _create_agent_tool())
