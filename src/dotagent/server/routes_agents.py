"""Routes for agents (item 86): the profiles the owner makes, each with a face, a role, a model, its own chats and
what it learned for itself (item 94)."""

import asyncio
import time

from fastapi import APIRouter

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.offers import HelpOffer, LetHelpRequest, OfferBook, at_work_and_free, help_note
from dotagent.agent.playbooks import PlaybookEntry, list_playbooks, release_playbooks
from dotagent.agent.templates import (
    TEAMS,
    TEMPLATES,
    AgentFile,
    AgentTeam,
    AgentTemplate,
    agent_file,
    find_team,
    find_template,
    with_answers,
)
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import playbooks_dir
from dotagent.llm.router import parse_model_ref
from dotagent.secrets import known_secrets
from dotagent.server.activity import start_of_today
from dotagent.server.context import ServerContext
from dotagent.server.routes_system import learned_skill_view
from dotagent.server.views import (
    AgentDetail,
    AgentLearning,
    AgentRequest,
    AgentSummary,
    AgentUsage,
    TeamMessage,
    TeamUse,
    TeamView,
    TemplateUse,
)
from dotagent.store.db import Store
from dotagent.store.models import (
    DOT_AGENT,
    DOT_AGENT_ID,
    AgentLook,
    AgentRecord,
    AgentToolUse,
    MemoryRecord,
    RunRecord,
    TeamRecord,
)
from dotagent.tools.team import MESSAGE_AGENT_TOOL, find_agent

# How many of its newest runs an agent's page shows.
_PAGE_RUNS: int = 50
# How many of the newest messages an agent sent or received its page shows.
_PAGE_MESSAGES: int = 50
# How many of its newest ended runs an agent's page reads for the tools it used (item 874), and how many must have
# ended before it names the ones none of them called: one run says little of what a role needs.
_USAGE_WINDOW: int = 20
_USAGE_ENOUGH: int = 3
# What trimming never takes away: the tools every agent's run has, and its way to its team.
_ALWAYS_KEPT: frozenset[str] = frozenset({*AGENT_CORE_TOOLS, MESSAGE_AGENT_TOOL})
# How many of its newest lessons an agent's page shows.
_PAGE_LESSONS: int = 100
# What Dot's page says it learned: nothing here, since Dot's lessons and skills are the Memory and Skills pages.
_NOTHING_LEARNED: AgentLearning = AgentLearning(lessons=[], scores={}, skills=())


def _checked(request: AgentRequest, known: frozenset[str]) -> AgentRequest:
    """A request whose model parses and whose tools are among the ``known`` ones."""
    if request.model != "":
        parse_model_ref(request.model)
    unknown: list[str] = sorted(set(request.tools or ()) - known)
    if unknown:
        raise InvalidValueError(
            f"an agent names actions that do not exist here: {', '.join(unknown)}", {"tools": ", ".join(unknown)}
        )
    return request


def _template_request(chosen: AgentTemplate) -> AgentRequest:
    """What a template's Use asks for: a copy of it, which the owner then changes as any agent."""
    return AgentRequest(
        name=chosen.name,
        role=chosen.role,
        instructions=chosen.instructions,
        model="",
        effort=None,
        approvals=None,
        tools=list(chosen.tools) or None,
        look=chosen.look,
        refuses="\n".join(chosen.refuses),
    )


def _cannot_message(record: AgentRecord) -> bool:
    """Whether ``record`` names its tools without ``message_agent``; one with every tool (none named) has it."""
    return bool(record.tools) and MESSAGE_AGENT_TOOL not in record.tools


def new_agent_tools(said: list[str] | None) -> tuple[str, ...]:
    """The tools a new agent may use, as an :class:`AgentRequest` said them (``said``): every tool (empty) for
    ``None``, the named ones otherwise. An empty list keeps the tools an agent has, and a new one has none to keep."""
    if said is None:
        return ()
    if not said:
        raise InvalidValueError(
            "a new agent has no actions to keep: name the actions it may use, or leave actions out for every action",
            {"tools": "[]"},
        )
    return tuple(said)


def changed_agent_tools(said: list[str] | None, saved: tuple[str, ...]) -> tuple[str, ...]:
    """The tools an agent may use after a change that said ``said`` (:class:`AgentRequest`): every tool (empty) for
    ``None``, the ones it has (``saved``) for an empty list, the named ones otherwise (the code review's Y5)."""
    if said is None:
        return ()
    return tuple(said) if said else saved


def agent_usage(record: AgentRecord, use: AgentToolUse, known: frozenset[str]) -> AgentUsage:
    """What the page says of the tools ``record`` used in its newest ended runs (``use``, item 874): once enough of
    them ended, the tools it names that none called, but for the ones every agent's run has (finishing, asking) and
    ``message_agent``, its way to its team, which trimming keeps."""
    enough: bool = use.runs >= _USAGE_ENOUGH
    unused: tuple[str, ...] = (
        tuple(tool for tool in record.tools if tool not in use.calls and tool not in _ALWAYS_KEPT) if enough else ()
    )
    return AgentUsage(
        window=_USAGE_WINDOW,
        runs=use.runs,
        tokens=use.tokens,
        newest=use.newest,
        calls=use.calls,
        enough=enough,
        unused=unused,
        # What **Keep only these** keeps besides message_agent: as :func:`trimmed_tools` keeps them.
        own=tuple(tool for tool in use.calls if tool in known and tool not in _ALWAYS_KEPT),
    )


def trimmed_tools(record: AgentRecord, usage: AgentUsage, known: frozenset[str]) -> tuple[str, ...]:
    """The tools ``record`` keeps once the ones its newest runs never called go (item 874): for an agent that names
    its tools, those but the ``unused``; for one with every tool, the ones its runs called that are tools here
    (``known``), but for the ones every agent's run has. ``message_agent`` stays either way, its way to its team.
    Refused before enough runs ended, and when nothing would go or nothing would be left."""
    if not usage.enough:
        raise InvalidValueError(
            f"an agent's actions are trimmed once {_USAGE_ENOUGH} of its runs have ended; it has {usage.runs}",
            {"runs": str(usage.runs)},
        )
    kept: list[str] = [tool for tool in record.tools if tool not in usage.unused] if record.tools else list(usage.own)
    if (not record.tools or MESSAGE_AGENT_TOOL in record.tools) and MESSAGE_AGENT_TOOL not in kept:
        kept.append(MESSAGE_AGENT_TOOL)
    # One with every action whose runs called none but the ones every run has keeps nothing of its own: refused, not
    # cut down to message_agent alone (the review of item 874).
    if not kept or (record.tools and tuple(kept) == record.tools) or (not record.tools and not usage.own):
        raise InvalidValueError("no action of the agent's would go, or none would be left", {"agent_id": record.id})
    return tuple(kept)


async def dot_record(store: Store) -> AgentRecord:
    """Dot as an agent record: ``DOT_AGENT`` wearing the look the owner chose (item 102), if any."""
    return DOT_AGENT.model_copy(update={"look": await store.dot_look()})


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

    def known() -> frozenset[str]:
        """The tools an agent may be given now: the registry changes while the daemon runs, as an app of the owner's
        joins or leaves (item 689)."""
        return frozenset(ctx.runtime.registry.tools)

    @router.get("/team")
    async def team() -> TeamView:
        """Today's messages between agents, oldest first."""
        return TeamView(messages=await team_messages(store, await store.team_runs(start_of_today(time.time()))))

    @router.get("/tools")
    async def list_tools() -> list[str]:
        """The names of the tools an agent may be given, in alphabetical order."""
        return sorted(known())

    @router.get("/agents")
    async def list_agents() -> list[AgentSummary]:
        """Dot first, then the agents the owner made, oldest first, each with its newest run and the title of the chat
        that run is in: Dot's in a chat of its own (item 633), which its card said none of while the owner chatted
        with it every day."""
        latest: dict[str, RunRecord] = await store.latest_agent_runs()
        dots: RunRecord | None = await store.latest_dot_run()
        titles: dict[str, str] = await store.thread_titles(
            [run.thread_id for run in latest.values()] + ([] if dots is None else [dots.thread_id])
        )
        dot: AgentSummary = AgentSummary(
            record=await dot_record(store),
            latest=dots,
            latest_title=None if dots is None else titles.get(dots.thread_id),
        )
        return [dot] + [
            AgentSummary(
                record=agent,
                latest=latest.get(agent.id),
                latest_title=titles.get(latest[agent.id].thread_id) if agent.id in latest else None,
            )
            for agent in await store.list_agents()
        ]

    book: OfferBook = OfferBook()

    @router.get("/agents/offers")
    async def help_offers() -> list[HelpOffer]:
        """The free agents that could help the runs at work (item 896), as the fast model matched each run once; a
        helper that has started working since is left out."""
        agents: list[AgentRecord] = await store.list_agents()
        waiting: set[str] = {approval.run_id for approval in await store.list_approvals("pending", 200)}
        work, free = at_work_and_free(agents, await store.latest_agent_runs(), await store.latest_dot_run(), waiting)
        if not work or not free:
            return []
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        fast: str = ctx.runtime.roles.fast()
        found: list[list[HelpOffer]] = await asyncio.gather(
            *(book.offers(ctx.runtime.router, fast, item, free, held) for item in work)
        )
        ids: set[str] = {agent.id for agent in free}
        return [offer for offers in found for offer in offers if offer.helper_id in ids]

    @router.post("/agents/offers/help", status_code=202)
    async def let_help(request: LetHelpRequest) -> None:
        """The owner lets a free agent help a run at work (item 896): the run is told, as Dot's note, the helper's name
        and role and to hand it a part with message_agent if one fits; 404 for an agent that is not, 409 for a run that
        is not at work."""
        helper: AgentRecord = await store.get_agent(request.helper_id)
        await store.get_run(request.run_id)
        ctx.manager.tell(request.run_id, help_note(helper), "note")

    async def created(request: AgentRequest) -> AgentRecord:
        # A new agent never takes the lead's name (the review of 2026-11-01); one named so before is still changed.
        if request.name.strip().casefold() == DOT_AGENT.name.casefold():
            raise InvalidValueError("Dot is the lead's name; choose another for the agent", {"name": request.name})
        return await saved(_checked(request, known()))

    async def saved(checked: AgentRequest) -> AgentRecord:
        """The agent of a request ``_checked`` passed."""
        return await store.create_agent(
            checked.name,
            checked.role,
            checked.instructions,
            checked.model,
            checked.effort,
            checked.approvals,
            new_agent_tools(checked.tools),
            None,
            checked.look,
            checked.refuses or "",
        )

    @router.post("/agents", status_code=201)
    async def create_agent(request: AgentRequest) -> AgentRecord:
        return await created(request)

    @router.get("/agent-templates")
    async def agent_templates() -> list[AgentTemplate]:
        """The agents to start from (item 315), in the gallery's order."""
        return list(TEMPLATES)

    @router.post("/agent-templates/{template_id}/use", status_code=201)
    async def use_template(template_id: str, use: TemplateUse | None = None) -> AgentRecord:
        """An agent made from a template: a copy, which the owner then changes as any agent, with what the owner
        answered its questions in its instructions (item 876)."""
        chosen: AgentTemplate = find_template(template_id)
        request: AgentRequest = _template_request(chosen)
        if use is not None and any(answer.strip() for answer in use.answers):
            known_now: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
            request = request.model_copy(update={"instructions": with_answers(chosen, use.answers, known_now)})
        return await created(request)

    @router.get("/agent-teams")
    async def agent_teams() -> list[AgentTeam]:
        """The teams to start from (item 504), in the gallery's order."""
        return list(TEAMS)

    @router.post("/agent-teams/{team_id}/use", status_code=201)
    async def use_team(team_id: str) -> TeamUse:
        """A team's agents (item 504), each made as its template's Use makes one. A member with the name of an agent
        the owner has, read without regard to case as the team tools read it, is that agent, kept as it is but for one
        thing (item 512): one that names its tools without ``message_agent`` is given it, so that it can hand its
        teammates their parts (``joined``). The others are all checked (their model and tools) before any is made or
        given anything, so a team one of whose agents this home cannot make makes none. Past the checks they are made
        one by one, not in one transaction: a store that fails part way leaves the ones made, and two Uses at once may
        make an agent twice, as two clicks of a template's Use may.

        The team is then the owner's (item 610): a record of the agents, in the team's order, made when the owner has
        none from this ready team and found, completed with the members it lacked, when they have. The owner may
        have renamed it or taken an agent out; a Use brings the missing members back and keeps the rest as it is."""
        chosen: AgentTeam = find_team(team_id)
        agents: list[AgentRecord] = await store.list_agents()
        have_them: list[AgentRecord] = []
        to_make: list[AgentRequest] = []
        for template in (find_template(member) for member in chosen.members):
            have: AgentRecord | None = find_agent(agents, template.name)
            if have is None:
                to_make.append(_checked(_template_request(template), known()))
            else:
                have_them.append(have)
        made: list[AgentRecord] = [await saved(request) for request in to_make]
        joined: list[str] = [record.name for record in have_them if _cannot_message(record)]
        kept: list[AgentRecord] = [await _joined(record) if _cannot_message(record) else record for record in have_them]
        by_name: dict[str, AgentRecord] = {record.name.casefold(): record for record in [*made, *kept]}
        members: list[AgentRecord] = [by_name[find_template(each).name.casefold()] for each in chosen.members]
        team: TeamRecord | None = await store.find_ready_team(chosen.id)
        if team is None:
            team = await store.create_team(chosen.name, chosen.purpose, [member.id for member in members], chosen.id)
            return TeamUse(made=made, kept=kept, joined=joined, team=team, created=True, added=[])
        lacking: list[AgentRecord] = [member for member in members if member.id not in team.agents]
        if lacking:
            team = await store.update_team(
                team.id, team.name, team.purpose, [*team.agents, *(member.id for member in lacking)]
            )
        return TeamUse(
            made=made, kept=kept, joined=joined, team=team, created=False, added=[member.name for member in lacking]
        )

    async def _joined(record: AgentRecord) -> AgentRecord:
        """``record`` able to message its teammates: ``message_agent`` added to the tools it names."""
        return await store.update_agent(
            record.id,
            record.name,
            record.role,
            record.instructions,
            record.model,
            record.effort,
            record.approvals,
            (*record.tools, MESSAGE_AGENT_TOOL),
            record.look,
        )

    @router.post("/agents/import", status_code=201)
    async def import_agent(file: AgentFile) -> AgentRecord:
        """An agent from its file (item 315): the same agent, whose model and tools must be at hand in this home."""
        return await created(
            AgentRequest(
                name=file.name,
                role=file.role,
                instructions=file.instructions,
                model=file.model,
                effort=file.effort,
                approvals=file.approvals,
                tools=list(file.tools) or None,
                look=file.look,
                refuses=file.refuses,
            )
        )

    @router.get("/agents/{agent_id}/export")
    async def export_agent(agent_id: str) -> AgentFile:
        """An agent as a file (item 315): its name, role, instructions, model, bounds and face; not its memory, its
        chats or a secret."""
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to export", {"agent_id": agent_id})
        # A refusal, so the values filled into a page are not among the secrets it looks for (item 621); the file is
        # made with them masked.
        return agent_file(await store.get_agent(agent_id), await asyncio.to_thread(known_secrets, filled=False))

    @router.get("/agents/{agent_id}")
    async def get_agent(agent_id: str) -> AgentDetail:
        if agent_id == DOT_AGENT_ID:
            return AgentDetail(record=await dot_record(store), runs=[], messages=[], learned=_NOTHING_LEARNED)
        record: AgentRecord = await store.get_agent(agent_id)
        return AgentDetail(
            record=record,
            runs=await store.agent_runs(agent_id, _PAGE_RUNS),
            messages=await team_messages(store, await store.agent_messages(agent_id, _PAGE_MESSAGES)),
            learned=await agent_learning(store, agent_id, ctx.runtime.config.agent.skill_rest_days),
            usage=agent_usage(record, await store.agent_tool_use(agent_id, _USAGE_WINDOW), known()),
        )

    @router.put("/agents/{agent_id}")
    async def update_agent(agent_id: str, request: AgentRequest) -> AgentRecord:
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to change", {"agent_id": agent_id})
        checked: AgentRequest = _checked(request, known())
        saved: AgentRecord = await store.get_agent(agent_id)
        return await store.update_agent(
            agent_id,
            checked.name,
            checked.role,
            checked.instructions,
            checked.model,
            checked.effort,
            checked.approvals,
            changed_agent_tools(checked.tools, saved.tools),
            checked.look,
            checked.refuses,
        )

    @router.post("/agents/{agent_id}/trim-tools")
    async def trim_tools(agent_id: str) -> AgentRecord:
        """Take away the actions the agent's newest runs never called (item 874); the record as it is now."""
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to change", {"agent_id": agent_id})
        saved: AgentRecord = await store.get_agent(agent_id)
        usage: AgentUsage = agent_usage(saved, await store.agent_tool_use(agent_id, _USAGE_WINDOW), known())
        return await store.update_agent(
            agent_id,
            saved.name,
            saved.role,
            saved.instructions,
            saved.model,
            saved.effort,
            saved.approvals,
            trimmed_tools(saved, usage, known()),
            saved.look,
        )

    @router.put("/agents/{agent_id}/look")
    async def set_look(agent_id: str, look: AgentLook) -> AgentRecord:
        """Give an agent, or Dot, a look (item 102); the record as it is now."""
        if agent_id == DOT_AGENT_ID:
            await store.set_dot_look(look)
            return await dot_record(store)
        return await store.set_agent_look(agent_id, look)

    @router.post("/agents/{agent_id}/pause")
    async def pause_agent(agent_id: str) -> AgentRecord:
        """Pause the agent (item 465): its schedules start nothing by themselves until it is resumed. What the owner
        sends it, Run now of its schedules and the messages of other agents still reach it."""
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError(
                "Dot is not an agent to pause; switch its schedules off instead", {"agent_id": agent_id}
            )
        return await store.set_agent_paused(agent_id, time.time())

    @router.post("/agents/{agent_id}/resume")
    async def resume_agent(agent_id: str) -> AgentRecord:
        """Let a paused agent's schedules fire again (item 465)."""
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to resume", {"agent_id": agent_id})
        return await store.set_agent_paused(agent_id, None)

    @router.delete("/agents/{agent_id}", status_code=204)
    async def delete_agent(agent_id: str) -> None:
        if agent_id == DOT_AGENT_ID:
            raise InvalidValueError("Dot is not an agent to delete", {"agent_id": agent_id})
        await store.delete_agent(agent_id)
        await asyncio.to_thread(release_playbooks, playbooks_dir(), agent_id)

    return router


async def agent_learning(store: Store, agent_id: str, rest_days: int) -> AgentLearning:
    """What the agent learned (item 94): its lessons with the scores of its own runs, and its learned skills, which rest
    when no run read them for ``rest_days`` days (item 585)."""
    lessons: list[MemoryRecord] = await store.agent_memories(agent_id, _PAGE_LESSONS)
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
    return AgentLearning(
        lessons=lessons,
        scores=await store.agent_memory_scores([lesson.id for lesson in lessons], 0.0, agent_id),
        skills=tuple(
            learned_skill_view(entry, time.time(), rest_days) for entry in entries if entry.meta.agent_id == agent_id
        ),
    )
