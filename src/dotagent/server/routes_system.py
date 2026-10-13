"""Routes for the state of the installation: health, the proof of the daemon, status, models, checks, desktop
permissions, the emergency stop and screenshots."""

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from dotagent import __version__
from dotagent.agent.playbooks import PlaybookEntry, drop_playbook, list_playbooks, share_playbook
from dotagent.config import AppConfig
from dotagent.core.errors import DotError, MissingSecretError, NotFoundError, ProviderAuthError
from dotagent.core.paths import dot_home, failsafe_path, playbooks_dir, screenshots_dir
from dotagent.desktop.perms import missing_panes, program_of_this_process, request_permissions
from dotagent.desktop.types import Permissions
from dotagent.doctor import CheckResult, run_quick_checks
from dotagent.help.elsewhere import FoundElsewhere, found_elsewhere
from dotagent.llm.router import ModelRef, ModelRouter
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import KNOWN_PROVIDERS, Effort, ModelInfo
from dotagent.server.context import ServerContext
from dotagent.server.guard import daemon_proof
from dotagent.server.palette import palette_view
from dotagent.server.views import (
    FailsafeView,
    HealthView,
    LearnedSkillsView,
    LearnedSkillView,
    ModelOption,
    PaletteView,
    PermissionsView,
    ProofQuery,
    ProofView,
    ProviderModels,
    ProviderProblem,
    PushKeyView,
    PushSubscribeRequest,
    SkillShareRequest,
    StatusView,
)
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import SkillCatalog
from dotagent.store.models import ApprovalRecord, PushSubscription

_IMAGE_NAME: re.Pattern[str] = re.compile(r"^[0-9a-f]{24}\.(png|jpg|webp|gif)$")
_PENDING_LIMIT: int = 500


def _option(provider: str, info: ModelInfo, default_effort: Effort) -> ModelOption:
    return ModelOption(
        ref=ModelRef(provider=provider, model=info.id).label,
        vision=info.vision,
        tools=info.tools,
        thinking=info.thinking,
        context_length=info.context_length,
        efforts=list(info.efforts),
        default_effort=effective_effort(info.efforts, default_effort),
        free=info.free,
    )


def _problem(exc: DotError) -> ProviderProblem:
    """The kind of failure of a provider: the page sends a missing or refused key to Settings › API keys."""
    if isinstance(exc, MissingSecretError):
        return "no_key"
    if isinstance(exc, ProviderAuthError):
        return "key_refused"
    return "failed"


async def _provider_models(router: ModelRouter, provider: str, default_effort: Effort) -> ProviderModels:
    """The models of one provider; a provider that cannot be reached is reported, it does not fail the others."""
    try:
        infos: tuple[ModelInfo, ...] = await (await router.provider(provider)).list_models()
    except DotError as exc:
        return ProviderModels(provider=provider, models=[], error=exc.message, problem=_problem(exc))
    return ProviderModels(
        provider=provider,
        models=[_option(provider, info, default_effort) for info in infos],
        error=None,
        problem=None,
    )


def health_router() -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/health")
    async def health() -> HealthView:
        return HealthView(ok=True, version=__version__)

    return router


def proof_router(token: str) -> APIRouter:
    """The answer to a client's nonce with which the daemon shows that it holds ``token``, asked before the token is
    sent (:func:`dotagent.server.guard.daemon_proof`)."""
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/proof")
    async def proof(query: Annotated[ProofQuery, Query()]) -> ProofView:
        return ProofView(proof=daemon_proof(token, query.nonce))

    return router


def system_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    config: AppConfig = ctx.runtime.config

    async def engaged() -> bool:
        return await asyncio.to_thread(failsafe_path().exists)

    @router.get("/push/key")
    async def push_key() -> PushKeyView:
        """The key a page gives ``PushManager.subscribe``; the daemon signs its pushes with the private half."""
        return PushKeyView(public_key=ctx.runtime.push_keys.public_key)

    @router.post("/push/subscriptions", status_code=201)
    async def push_subscribe(request: PushSubscribeRequest) -> PushSubscription:
        return await ctx.runtime.store.add_push_subscription(request.endpoint, request.keys.p256dh, request.keys.auth)

    @router.delete("/push/subscriptions", status_code=204)
    async def push_unsubscribe(endpoint: str) -> None:
        """Drop the subscription at ``endpoint`` (a query parameter: a DELETE carries no body)."""
        await ctx.runtime.store.delete_push_subscription(endpoint)

    @router.get("/status")
    async def status() -> StatusView:
        pending: list[ApprovalRecord] = await ctx.runtime.store.list_approvals("pending", _PENDING_LIMIT)
        now: float = time.time()
        return StatusView(
            version=__version__,
            pid=os.getpid(),
            started_at=ctx.started_at,
            uptime_s=now - ctx.started_at,
            home=str(dot_home()),
            host=ctx.host,
            port=ctx.port,
            active_runs=ctx.manager.active_run_ids(),
            pending_approvals=len(pending),
            scheduler_running=ctx.scheduler is not None and ctx.scheduler.running(),
            failsafe_engaged=await engaged(),
            preset=config.policy.preset,
            approvals=config.policy.approvals,
            goal_mode=config.agent.goal_mode,
            browser_mode=ctx.runtime.browser.mode,
            desktop_enabled=config.desktop.enabled,
            mcp_tools=len(ctx.runtime.mcp.tools),
            mcp_failures=list(ctx.runtime.mcp.failures),
            models=config.models,
        )

    @router.get("/models")
    async def models() -> list[ProviderModels]:
        default_effort: Effort = config.models.effort
        return list(
            await asyncio.gather(
                *(_provider_models(ctx.runtime.router, name, default_effort) for name in KNOWN_PROVIDERS)
            )
        )

    @router.get("/skills/learned")
    async def learned_skills() -> LearnedSkillsView:
        """The skills Dot learned from its own work, active first, then the retired ones."""
        entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
        return LearnedSkillsView(items=tuple(learned_skill_view(entry) for entry in entries))

    @router.delete("/skills/learned/{name}")
    async def drop_learned_skill(name: str) -> LearnedSkillsView:
        """Remove a learned skill, active or retired; the list after it."""
        await asyncio.to_thread(drop_playbook, playbooks_dir(), name)
        entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
        return LearnedSkillsView(items=tuple(learned_skill_view(entry) for entry in entries))

    @router.put("/skills/learned/{name}")
    async def share_learned_skill(name: str, request: SkillShareRequest) -> LearnedSkillsView:
        """Offer an agent's learned skill to every run, or to its agent's alone again; the list after it."""
        await asyncio.to_thread(share_playbook, playbooks_dir(), name, request.shared)
        entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
        return LearnedSkillsView(items=tuple(learned_skill_view(entry) for entry in entries))

    @router.get("/skills/elsewhere")
    async def skills_elsewhere() -> FoundElsewhere:
        """The skills and MCP servers of the Mac's other agents (item 164), read from their files; Dot does not use
        them until the owner adds them. Each says whether Dot already reads its folder, or has a server under the name
        it would give it."""
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        servers: frozenset[str] = frozenset(
            [
                *(server.name for server in config.mcp),
                *(server.name for plugin in ctx.runtime.plugins.plugins for server in plugin.servers),
            ]
        )
        return await asyncio.to_thread(
            found_elsewhere, Path.home(), frozenset(skill.root for skill in catalog.skills), servers
        )

    @router.get("/palette")
    async def palette() -> PaletteView:
        """The skills and the MCP tools for the composer's slash menu, read from the folders as a run reads them."""
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        return palette_view(catalog.skills, ctx.runtime.mcp.tools)

    @router.get("/doctor")
    async def doctor() -> list[CheckResult]:
        return await run_quick_checks(config)

    @router.post("/desktop/permissions")
    async def ask_for_desktop_permissions() -> PermissionsView:
        """Make macOS ask, in the name of the program that runs this daemon, for what the desktop is missing."""
        granted: Permissions = await asyncio.to_thread(request_permissions)
        return PermissionsView(
            program=str(program_of_this_process()),
            accessibility=granted.accessibility,
            input_events=granted.post_event,
            screen_recording=granted.screen_recording,
            panes=list(missing_panes(granted)),
        )

    @router.get("/failsafe")
    async def failsafe_state() -> FailsafeView:
        return FailsafeView(engaged=await engaged(), cancelled_runs=[])

    @router.post("/failsafe")
    async def engage_failsafe() -> FailsafeView:
        """Block desktop control, stop every run that is going on as an emergency stop (their campaigns stop with them)
        and hold the scheduler until the stop is released."""
        await asyncio.to_thread(failsafe_path().touch)
        return FailsafeView(engaged=True, cancelled_runs=ctx.manager.emergency_stop())

    @router.delete("/failsafe")
    async def release_failsafe() -> FailsafeView:
        await asyncio.to_thread(failsafe_path().unlink, True)
        return FailsafeView(engaged=False, cancelled_runs=[])

    @router.get("/images/{name}")
    async def image(name: str) -> FileResponse:
        if _IMAGE_NAME.fullmatch(name) is None:
            raise NotFoundError("image not found", {"name": name})
        path: Path = screenshots_dir() / name
        if not await asyncio.to_thread(path.is_file):
            raise NotFoundError("image not found", {"name": name})
        return FileResponse(path)

    return router


def learned_skill_view(entry: PlaybookEntry) -> LearnedSkillView:
    return LearnedSkillView(
        name=entry.meta.name,
        description=entry.skill.description,
        kind=entry.meta.kind,
        host=entry.meta.host,
        runs=entry.meta.runs,
        uses=entry.meta.uses,
        failures=entry.meta.failures,
        retired=entry.retired,
        written_at=entry.meta.written_at,
        updated_at=entry.meta.updated_at,
        agent_id=entry.meta.agent_id,
        shared=entry.meta.shared,
    )
