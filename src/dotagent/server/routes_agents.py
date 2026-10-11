"""Routes for agents (item 86): the profiles the owner makes, each with a face, a role, a model and its own chats."""

from fastapi import APIRouter

from dotagent.core.errors import InvalidValueError
from dotagent.llm.router import parse_model_ref
from dotagent.server.context import ServerContext
from dotagent.server.views import AgentDetail, AgentRequest, AgentSummary
from dotagent.store.db import Store
from dotagent.store.models import DOT_AGENT, DOT_AGENT_ID, AgentRecord, RunRecord

# How many of its newest runs an agent's page shows.
_PAGE_RUNS: int = 50


def _checked(request: AgentRequest, known: frozenset[str]) -> AgentRequest:
    """A request whose model parses and whose tools are among the ``known`` ones."""
    if request.model != "":
        parse_model_ref(request.model)
    unknown: list[str] = sorted(set(request.tools) - known)
    if unknown:
        raise InvalidValueError("an agent names tools that do not exist", {"tools": unknown})
    return request


def agents_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store
    known: frozenset[str] = frozenset(ctx.runtime.registry.tools)

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
            return AgentDetail(record=DOT_AGENT, runs=[])
        return AgentDetail(record=await store.get_agent(agent_id), runs=await store.agent_runs(agent_id, _PAGE_RUNS))

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
