"""Routes for the owner's teams (item 610): agents put together under a name and a purpose. A chat with a team works as
the team: Dot leads its agents, hands each its part with ``message_agent`` and puts their answers together. A team is a
record, and nothing it names goes with it: removing a team keeps its agents and its chats."""

from collections.abc import Mapping, Sequence

from fastapi import APIRouter

from dotagent.server.context import ServerContext
from dotagent.server.views import TeamDetail, TeamRequest, TeamSummary
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, TeamRecord, ThreadSummary

# How many of a team's newest chats its page lists.
_PAGE_CHATS: int = 100
# How many of a team's chats are read to find the one that was busy last, for its card: the list puts a pinned chat
# first, whatever its age.
_CARD_CHATS: int = 20


def members_of(team: TeamRecord, agents: Mapping[str, AgentRecord]) -> list[AgentRecord]:
    """The agents of ``team`` as they are now (``agents``, by id), in its order. One that is gone is left out: the
    store takes a deleted agent out of its teams, and the page never names one that is not there."""
    return [agents[each] for each in team.agents if each in agents]


async def agents_by_id(store: Store) -> dict[str, AgentRecord]:
    return {agent.id: agent for agent in await store.list_agents()}


def teams_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def chats(team: TeamRecord, limit: int) -> Sequence[ThreadSummary]:
        return await store.list_thread_summaries(limit, ctx.runtime.config.scheduler.same_chat, team.id)

    @router.get("/teams")
    async def list_teams() -> list[TeamSummary]:
        """The owner's teams, oldest first, each with its agents as they are now and its newest chat."""
        agents: dict[str, AgentRecord] = await agents_by_id(store)
        return [
            TeamSummary(
                record=team,
                members=members_of(team, agents),
                latest=max(await chats(team, _CARD_CHATS), key=lambda chat: chat.updated_at, default=None),
            )
            for team in await store.list_teams()
        ]

    @router.post("/teams", status_code=201)
    async def create_team(request: TeamRequest) -> TeamRecord:
        """A team of agents the owner has (422 for an id that is no agent of theirs, or named twice)."""
        return await store.create_team(request.name, request.purpose, request.agents, None)

    @router.get("/teams/{team_id}")
    async def get_team(team_id: str) -> TeamDetail:
        team: TeamRecord = await store.get_team(team_id)
        return TeamDetail(
            record=team, members=members_of(team, await agents_by_id(store)), chats=list(await chats(team, _PAGE_CHATS))
        )

    @router.put("/teams/{team_id}")
    async def update_team(team_id: str, request: TeamRequest) -> TeamRecord:
        """Rename a team, say again what it is for, and name its agents anew (the agents the owner has, in the order
        the pages show them). Its chats stay with it; an agent it no longer names stays the owner's."""
        return await store.update_team(team_id, request.name, request.purpose, request.agents)

    @router.delete("/teams/{team_id}", status_code=204)
    async def delete_team(team_id: str) -> None:
        """Remove the team and nothing else: its agents stay the owner's, and its chats stay, as Dot's."""
        await store.delete_team(team_id)

    return router
