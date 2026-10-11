"""The team tools. ``message_agent`` (item 87): a run sends a message to one of the owner's agents, which answers in
a run of its own (the agent's instructions, tools and bounds, delegated by the sender), and the reply comes back as
the result. The runs behind the messages are the thread of the team (``GET /api/team``): a run with the ``team``
source. ``create_agent`` (item 99): a run makes a lasting agent the way the Agents page does, for a kind of work that
recurs; the record keeps the run that made it."""

import secrets

from pydantic import Field

from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.text import truncate_tail
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo
from dotagent.store.models import AgentRecord, RunMode, RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
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
    return f'<agent_reply nonce="{nonce}" run="{run_id}">\n{text}\n</agent_reply>'


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
            "Send a message to one of the owner's agents and get its reply. It works in a run of its own with its own "
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
    tools: list[str] = Field(default_factory=list, description="Tool names it may use; empty means every tool.")
    model: str = Field(default="", description="provider:model for its runs; empty means the configured one.")


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
        if args.model != "":
            parse_model_ref(args.model)
        unknown: list[str] = sorted(set(args.tools) - set(ctx.runtime.registry.tools))
        if unknown:
            raise ToolInputError("the tools named do not exist", {"tools": ", ".join(unknown)})
        agents: list[AgentRecord] = await ctx.runtime.store.list_agents()
        taken: AgentRecord | None = find_agent(agents, name)
        if taken is not None:
            raise ToolInputError(
                "an agent has that name already; message it, or choose another name",
                {"agent": taken.name, "role": taken.role},
            )
        made: AgentRecord = await ctx.runtime.store.create_agent(
            name, args.role.strip(), args.instructions.strip(), args.model, None, None, args.tools, ctx.run_id
        )
        return ok(render_made(made))

    return make_tool(
        name=CREATE_AGENT_TOOL,
        description=(
            "Make a lasting teammate: an agent with a name, a role, instructions and tools, as the owner does on the "
            "Agents page. Only for a kind of work that recurs, never for one step. It stays after this run; send it "
            "work with message_agent."
        ),
        args_model=CreateAgentArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_CREATE_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_team_tools() -> tuple[RegisteredTool, ...]:
    return (_message_agent_tool(), _create_agent_tool())
