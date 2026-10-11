"""Routes for agents (item 86): the profiles the owner makes, each with a face, a role, a model and its own chats."""

import time

from fastapi import APIRouter

from dotagent.core.errors import InvalidValueError
from dotagent.llm.router import parse_model_ref
from dotagent.server.activity import start_of_today
from dotagent.server.context import ServerContext
from dotagent.server.views import AgentDetail, AgentRequest, AgentSummary, TeamMessage, TeamView
from dotagent.store.db import Store
from dotagent.store.models import DOT_AGENT, DOT_AGENT_ID, AgentRecord, RunRecord

# How many of its newest runs an agent's page shows.
_PAGE_RUNS: int = 50
# How many of the newest messages an agent sent or received its page shows.
_PAGE_MESSAGES: int = 50


def _checked(request: AgentRequest, known: frozenset[str]) -> AgentRequest:
    """A request whose model parses and whose tools are among the ``known`` ones."""
    if request.model != "":
        parse_model_ref(request.model)
    unknown: list[str] = sorted(set(request.tools) - known)
    if unknown:
        raise InvalidValueError("an agent names tools that do not exist", {"tools": ", ".join(unknown)})
    return request


async def team_messages(store: Store, runs: list[RunRecord]) -> list[TeamMessage]:
    """The messages behind ``runs`` (runs of the ``team`` source, each the answer of an agent to the run that sent
    it); a sender or receiver whose agent is gone shows as Dot."""
    names: dict[str, str] = {agent.id: agent.name for agent in await store.list_agents()}
    messages: list[TeamMessage] = []
    for run in runs:
        if run.parent_run_id is None:
            continue
        sender: RunRecord = await store.get_run(run.parent_run_id)
        from_id: str = DOT_AGENT_ID if sender.agent_id is None or sender.agent_id not in names else sender.agent_id
        to_id: str = DOT_AGENT_ID if run.agent_id is None or run.agent_id not in names else run.agent_id
        messages.append(
            TeamMessage(
                run_id=run.id,
                parent_run_id=run.parent_run_id,
                from_id=from_id,
                from_name=names.get(from_id, DOT_AGENT.name),
                to_id=to_id,
                to_name=names.get(to_id, DOT_AGENT.name),
                message=run.goal,
                reply=None if run.finished_at is None else run.result,
                status=run.status,
                ts=run.created_at,
                finished_at=run.finished_at,
            )
        )
    return messages


def agents_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store
    known: frozenset[str] = frozenset(ctx.runtime.registry.tools)

    @router.get("/team")
    async def team() -> TeamView:
        """Today's messages between agents, oldest first."""
        return TeamView(messages=await team_messages(store, await store.team_runs(start_of_today(time.time()))))

    @router.get("/tools")
    async def list_tools() -> list[str]:
        """The names of the tools an agent may be given, in alphabetical order."""
        return sorted(known)

    @router.get("/agents")
    async def list_agents() -> list[AgentSummary]:
        """Dot first, then the agents the owner made, oldest first, each with its newest run."""
        latest: dict[str, RunRecord] = await store.latest_agent_runs()
        dot: AgentSummary = AgentSummary(record=DOT_AGENT, latest=None)
        return [dot] + [AgentSummary(record=agent, latest=latest.get(agent.id)) for agent in await store.list_agents()]

    @router.post("/agents", status_code=201)
    async def create_agent(request: AgentRequest) -> AgentRecord:
        checked: AgentRequest = _checked(request, known)
        return await store.create_agent(
            checked.name,
            checked.role,
            checked.instructions,
            checked.model,
            checked.effort,
            checked.approvals,
            checked.tools,
        )

    @router.get("/agents/{agent_id}")
    async def get_agent(agent_id: str) -> AgentDetail:
        if agent_id == DOT_AGENT_ID:
            return AgentDetail(record=DOT_AGENT, runs=[], messages=[])
        return AgentDetail(
            record=await store.get_agent(agent_id),
            runs=await store.agent_runs(agent_id, _PAGE_RUNS),
            messages=await team_messages(store, await store.agent_messages(agent_id, _PAGE_MESSAGES)),
        )

    @router.put("/agents/{agent_id}")
    async def update_agent(agent_id: str, request: AgentRequest) -> AgentRecord:
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to change", {"agent_id": agent_id})
        checked: AgentRequest = _checked(request, known)
        return await store.update_agent(
            agent_id,
            checked.name,
            checked.role,
            checked.instructions,
            checked.model,
            checked.effort,
            checked.approvals,
            checked.tools,
        )

    @router.delete("/agents/{agent_id}", status_code=204)
    async def delete_agent(agent_id: str) -> None:
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to delete", {"agent_id": agent_id})
        await store.delete_agent(agent_id)

    return router
