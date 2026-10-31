"""What one agent knows of the others (item 890, the owner's ask of 2026-10-31: "the agents should see each other, know
of each other and be able to read each other's chats"; "go and help the agent of the engineer team" should be
understood). Two read-only tools in the group ``office``:

- ``agents_now``: Dot and every agent, each with its role, its teams and what it does now (working on a goal, waiting
  for the owner, free), so a run knows whom a name or a team means.
- ``read_agent_chat``: the newest messages of an agent's newest chat (or Dot's), with what its run does now, so a run
  sent to help can see where the other stands before it messages it (``message_agent``).

What either quotes may carry what a page or a mail said to the run that read it: the words of a chat whose runs read
content third parties can author are fenced and the result marked untrusted, so the reading run is tainted as that chat
was (item 243); the secrets Dot holds are masked. Neither tool changes anything, and an agent's chat is its owner's
data on this Mac, which every run of Dot's may already be shown by the owner."""

import asyncio
import secrets
from collections.abc import Sequence

from pydantic import Field

from dotagent.agent.gate import TAINT_EVENTS, tainted_after
from dotagent.core.errors import ToolInputError
from dotagent.core.text import redact_known, truncate_tail, unclosing
from dotagent.llm.types import Message
from dotagent.policy.types import ActionInfo
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, EventRecord, RunRecord, TeamRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext

AGENTS_NOW_TOOL: str = "agents_now"
READ_AGENT_CHAT_TOOL: str = "read_agent_chat"
# Dot, as the tools name it: the lead of the work, not an agent record.
DOT_NAME: str = "Dot"
_GOAL_CHARS: int = 160
_MESSAGE_CHARS: int = 700
_TOOL_CHARS: int = 240
_MOST_MESSAGES: int = 40
_RUNS_READ: int = 20
_NONCE_BYTES: int = 8
_WORKING: frozenset[str] = frozenset({"queued", "running"})


async def run_tainted(store: Store, run_id: str) -> bool:
    """Whether the run ``run_id`` is (or ended) tainted, from its events, or what pruning kept of them (item 556)."""
    found: list[EventRecord] = [
        event for kind in TAINT_EVENTS if (event := await store.last_event(run_id, kind)) is not None
    ]
    return tainted_after(found) if found else await store.pruned_run_tainted(run_id)


async def goal_tainted(store: Store, run_id: str) -> bool:
    """Whether the goal of ``run_id`` came of content third parties can author: the run started tainted (its
    ``run_queued`` event). What it read after does not touch the words of its goal (the pre-push review of item 890:
    ``agents_now`` quotes goals alone, and marked every reader untrusted once one agent had read a page)."""
    queued: EventRecord | None = await store.last_event(run_id, "run_queued")
    return queued is not None and queued.data.get("tainted") is True


def state_words(run: RunRecord | None, waiting: bool) -> str:
    """What an agent does now, in a few words, from its newest run."""
    if run is None:
        return "free (it has not worked yet)"
    goal: str = truncate_tail(" ".join(run.goal.split()), _GOAL_CHARS)
    if waiting or run.status == "blocked":
        return f"waiting for the owner, in its work on: {goal}"
    if run.status in _WORKING:
        return f"working on: {goal}"
    return f"free; it last {'finished' if run.status == 'done' else run.status}: {goal}"


def _fence(text: str, nonce: str, name: str) -> str:
    safe: str = unclosing(text, "agent_chat")
    return f'<agent_chat nonce="{nonce}" agent="{name}">\n{safe}\n</agent_chat nonce="{nonce}">'


def render_message(message: Message) -> str | None:
    """One message of a chat as a line for another run: the owner's and the agent's words, its calls by name and the
    start of their results; ``None`` for what says nothing (an empty message)."""
    if message.role == "user":
        text: str = message.content.strip()
        return None if text == "" else f"owner: {truncate_tail(text, _MESSAGE_CHARS)}"
    if message.role == "assistant":
        calls: str = ", ".join(call.name for call in message.tool_calls)
        said: str = truncate_tail(message.content.strip(), _MESSAGE_CHARS)
        parts: list[str] = [part for part in (said, f"[called {calls}]" if calls else "") if part]
        return None if not parts else f"agent: {' '.join(parts)}"
    if message.role == "tool":
        return f"  {message.tool_name or 'tool'} -> {truncate_tail(' '.join(message.content.split()), _TOOL_CHARS)}"
    return None


class AgentsNowArgs(ToolArgs):
    pass


class ReadAgentChatArgs(ToolArgs):
    agent: str = Field(min_length=1, description='Its name, or "Dot".')
    messages: int = Field(default=20, ge=1, le=_MOST_MESSAGES, description="How many newest messages.")


async def _waiting_runs(store: Store) -> set[str]:
    return {approval.run_id for approval in await store.list_approvals("pending", 200)}


def _teams_of(agent_id: str, teams: Sequence[TeamRecord]) -> list[str]:
    return [team.name for team in teams if agent_id in team.agents]


def _agents_now_tool() -> RegisteredTool:
    def classify(_args: AgentsNowArgs, _ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=AGENTS_NOW_TOOL, kind="read", risk="safe", summary="See what the agents do now", target="", flags=()
        )

    async def run(_args: AgentsNowArgs, ctx: ToolContext) -> ToolResult:
        store: Store = ctx.runtime.store
        agents: list[AgentRecord] = await store.list_agents()
        latest: dict[str, RunRecord] = await store.latest_agent_runs()
        dots: RunRecord | None = await store.latest_dot_run()
        teams: list[TeamRecord] = await store.list_teams()
        waiting: set[str] = await _waiting_runs(store)
        quoted: list[RunRecord] = [run for run in (dots, *latest.values()) if run is not None]
        tainted: bool = any([await goal_tainted(store, run.id) for run in quoted])
        lines: list[str] = [
            f"- {DOT_NAME} (leads the work and the teams): {state_words(dots, dots is not None and dots.id in waiting)}"
        ]
        for agent in agents:
            run: RunRecord | None = latest.get(agent.id)
            own: str = " (you)" if agent.id == ctx.agent_id else ""
            in_teams: list[str] = _teams_of(agent.id, teams)
            team_words: str = f"; teams: {', '.join(in_teams)}" if in_teams else ""
            lines.append(
                f"- {agent.name}{own}, {agent.role or 'no role given'}{team_words}: "
                f"{state_words(run, run is not None and run.id in waiting)}"
            )
        listing: str = "\n".join(lines)
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        listing = redact_known(listing, held)
        head: str = (
            "The agents of this Mac and what each does now. read_agent_chat shows where one stands; message_agent "
            "sends it work or help."
        )
        if tainted:
            return mark_untrusted(ok(f"{head}\n{_fence(listing, secrets.token_hex(_NONCE_BYTES), 'all')}"))
        return ok(f"{head}\n{listing}")

    return make_tool(
        name=AGENTS_NOW_TOOL,
        description="Dot and the agents: role, teams and what each does now.",
        args_model=AgentsNowArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )


def _read_agent_chat_tool() -> RegisteredTool:
    def classify(args: ReadAgentChatArgs, _ctx: ToolContext) -> ActionInfo:
        name: str = truncate_tail(args.agent.strip(), 60)
        return ActionInfo(
            tool=READ_AGENT_CHAT_TOOL, kind="read", risk="safe", summary=f"Read {name}'s chat", target=name, flags=()
        )

    async def run(args: ReadAgentChatArgs, ctx: ToolContext) -> ToolResult:
        store: Store = ctx.runtime.store
        wanted: str = args.agent.strip()
        newest: RunRecord | None
        name: str
        if wanted.casefold() == DOT_NAME.casefold():
            newest, name = await store.latest_dot_run(), DOT_NAME
        else:
            agents: list[AgentRecord] = await store.list_agents()
            agent: AgentRecord | None = next(
                (found for found in agents if found.id == wanted or found.name.casefold() == wanted.casefold()), None
            )
            if agent is None:
                raise ToolInputError(
                    "no agent has that name",
                    {"agent": wanted, "agents": ", ".join([DOT_NAME, *(found.name for found in agents)])},
                )
            newest, name = (await store.latest_agent_runs()).get(agent.id), agent.name
        if newest is None:
            return ok(f"{name} has no chat yet: it has not worked.")
        if newest.thread_id == ctx.thread_id:
            return ok(f"{name}'s newest chat is this one: what it says is above.")
        runs: list[RunRecord] = await store.list_runs(_RUNS_READ, newest.thread_id)
        tainted: bool = any([await run_tainted(store, run.id) for run in runs])
        messages: list[Message] = await store.load_messages(newest.thread_id, 0)
        rendered: list[str] = [line for line in map(render_message, messages) if line is not None]
        shown: list[str] = rendered[-args.messages :]
        waiting: bool = newest.id in await _waiting_runs(store)
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        body: str = redact_known("\n".join(shown) if shown else "(no message yet)", held)
        head: str = (
            f"{name} is {state_words(newest, waiting)}\nIts chat's newest {len(shown)} of {len(rendered)} messages:"
        )
        if tainted:
            # Its goal too may have come of a page or a mail: inside the fence with the messages (the pre-push review).
            fenced: str = _fence(f"{redact_known(head, held)}\n{body}", secrets.token_hex(_NONCE_BYTES), name)
            return mark_untrusted(ok(f"{name}'s chat read content third parties can author:\n{fenced}"))
        return ok(f"{redact_known(head, held)}\n{body}")

    return make_tool(
        name=READ_AGENT_CHAT_TOOL,
        description="Read an agent's newest chat and what it does now, before you help it.",
        args_model=ReadAgentChatArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )


def build_office_tools() -> tuple[RegisteredTool, ...]:
    return (_agents_now_tool(), _read_agent_chat_tool())
