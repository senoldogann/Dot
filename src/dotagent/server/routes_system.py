"""Routes for the state of the installation: health, the proof of the daemon, status, models and their context sizes,
checks, desktop permissions, the emergency stop and screenshots."""

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
from dotagent.agent.scaffold import context_budget, context_ceiling, context_choices, summarise_point
from dotagent.agent.sizes import ContextSizes
from dotagent.config import CONTEXT_FLOOR, AgentConfig, AppConfig, JudgeConfig, ModelsConfig
from dotagent.config_edit import (
    set_context_size,
    set_default_model,
    set_judge_provider,
    set_resume,
    set_role_model,
    set_shell_sandbox,
)
from dotagent.core.errors import (
    ConfigError,
    DotError,
    ElsewhereError,
    MissingSecretError,
    NotFoundError,
    ProviderAccessError,
    ProviderAuthError,
)
from dotagent.core.logs import get_logger
from dotagent.core.paths import (
    added_servers_path,
    config_path,
    dot_home,
    failsafe_path,
    playbooks_dir,
    screenshots_dir,
    skills_dir,
)
from dotagent.desktop.perms import missing_panes, program_of_this_process, request_permissions
from dotagent.desktop.types import Permissions
from dotagent.doctor import CheckResult, run_quick_checks
from dotagent.help.added import (
    AddedServers,
    add_server,
    add_skill,
    dot_has,
    read_added_servers,
    remove_server,
    remove_skill,
)
from dotagent.help.elsewhere import (
    SKILL_COPY_LIMIT,
    DotHas,
    FoundElsewhere,
    FoundServer,
    FoundSkill,
    found_elsewhere,
)
from dotagent.llm.router import ModelRef, ModelRouter, offers_fast_mode, parse_model_ref
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import KNOWN_PROVIDERS, Effort, ModelInfo
from dotagent.secrets import secret_state
from dotagent.server.context import ServerContext
from dotagent.server.guard import daemon_proof
from dotagent.server.palette import palette_view
from dotagent.server.views import (
    ContextChoice,
    ContextEvidenceQuery,
    ContextSizeRequest,
    ElsewhereChange,
    ElsewhereImport,
    ElsewhereImported,
    ElsewhereItem,
    ElsewhereRefusal,
    FailsafeView,
    HealthView,
    JudgeRequest,
    JudgeView,
    LearnedSkillsView,
    LearnedSkillView,
    MainModelRequest,
    ModelOption,
    PaletteView,
    PermissionsView,
    ProofQuery,
    ProofView,
    ProviderModels,
    ProviderProblem,
    PushKeyView,
    PushSubscribeRequest,
    ResumeRequest,
    ResumeView,
    RoleModelRequest,
    SandboxRequest,
    SandboxView,
    SkillShareRequest,
    StatusView,
)
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import SkillCatalog
from dotagent.store.models import ApprovalRecord, PushSubscription
from dotagent.store.stats import ContextEvidence

log = get_logger(__name__)

_IMAGE_NAME: re.Pattern[str] = re.compile(r"^[0-9a-f]{24}\.(png|jpg|webp|gif)$")
_PENDING_LIMIT: int = 500
# The days of runs a model's context evidence is drawn from (item 250).
_EVIDENCE_DAYS: int = 30


def _context_choice(config: AgentConfig, window: int | None, chosen: int | None) -> ContextChoice:
    """The context size of the runs of a model whose request holds ``window`` tokens and whose size is ``chosen``."""
    tokens: int = context_budget(config, window, chosen)
    return ContextChoice(
        tokens=tokens,
        chosen=chosen,
        default=context_budget(config, window, None),
        configured=config.context_tokens,
        floor=CONTEXT_FLOOR,
        ceiling=context_ceiling(window),
        sizes=list(context_choices(config, window, chosen)),
        summarise_at=round(summarise_point(config, chosen, tokens)),
    )


def _option(
    provider: str, info: ModelInfo, default_effort: Effort, agent: AgentConfig, sizes: ContextSizes
) -> ModelOption:
    ref: ModelRef = ModelRef(provider=provider, model=info.id)
    return ModelOption(
        ref=ref.label,
        vision=info.vision,
        tools=info.tools,
        thinking=info.thinking,
        context_length=info.context_length,
        own_context_length=info.own_context_length,
        window_source=info.window_source,
        context=_context_choice(agent, info.context_length, sizes.of(ref.label)),
        efforts=list(info.efforts),
        default_effort=effective_effort(info.efforts, default_effort),
        free=info.free,
        fast_mode=offers_fast_mode(ref),
    )


def _problem(exc: DotError) -> ProviderProblem:
    """The kind of failure of a provider: the page sends a missing or refused key to Settings › API keys, and says
    that the provider refused access, which a key does not change."""
    if isinstance(exc, MissingSecretError):
        return "no_key"
    if isinstance(exc, ProviderAuthError):
        return "key_refused"
    if isinstance(exc, ProviderAccessError):
        return "access_refused"
    return "failed"


async def _provider_models(
    router: ModelRouter, provider: str, default_effort: Effort, agent: AgentConfig, sizes: ContextSizes
) -> ProviderModels:
    """The models of one provider; a provider that cannot be reached is reported, it does not fail the others."""
    try:
        infos: tuple[ModelInfo, ...] = await (await router.provider(provider)).list_models()
    except DotError as exc:
        return ProviderModels(provider=provider, models=[], error=exc.message, problem=_problem(exc))
    return ProviderModels(
        provider=provider,
        models=[_option(provider, info, default_effort, agent, sizes) for info in infos],
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


def system_router(ctx: ServerContext, pages: str) -> APIRouter:
    """The daemon's own routes; ``pages`` is what the pages it serves are (``dotagent.server.app.pages_build``)."""
    router: APIRouter = APIRouter(prefix="/api")
    config: AppConfig = ctx.runtime.config

    async def engaged() -> bool:
        return await asyncio.to_thread(failsafe_path().exists)

    def models_now() -> ModelsConfig:
        """The configured models, the default one, those of Dot's own work and the context sizes as the owner last
        chose them (items 199, 232 and 250)."""
        return ctx.runtime.roles.applied(
            config.models.model_copy(
                update={"main": ctx.manager.main_model(), "context": ctx.runtime.context_sizes.all()}
            )
        )

    def resume_now() -> ResumeView:
        """Whether the daemon's next start resumes the runs it cut off, as the owner last chose (item 231)."""
        return ResumeView(resume=ctx.manager.resume_mode(), resume_within_s=config.agent.resume_within_s)

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
            pages=pages,
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
            schedule_history_runs=config.scheduler.history_runs,
            schedule_same_chat=config.scheduler.same_chat,
            browser_mode=ctx.runtime.browser.mode,
            desktop_enabled=config.desktop.enabled,
            mcp_tools=len(ctx.runtime.mcp.tools),
            mcp_failures=list(ctx.runtime.mcp.failures),
            models=models_now(),
            resume=resume_now(),
        )

    @router.get("/models")
    async def models() -> list[ProviderModels]:
        default_effort: Effort = config.models.effort
        return list(
            await asyncio.gather(
                *(
                    _provider_models(ctx.runtime.router, name, default_effort, config.agent, ctx.runtime.context_sizes)
                    for name in KNOWN_PROVIDERS
                )
            )
        )

    @router.post("/models/main")
    async def make_main_model(request: MainModelRequest) -> ModelsConfig:
        """Make ``model`` the default (item 199): the runs that name none start on it from now on, and the config
        file keeps it as ``[models] main``. A model its provider does not offer, or one that cannot call tools, is
        refused, and so is a config file that cannot be changed; nothing changes then."""
        info: ModelInfo = await ctx.runtime.router.model_info(parse_model_ref(request.model))
        if not info.tools:
            raise ConfigError("a run needs a model that can call tools", {"model": request.model})
        await asyncio.to_thread(set_default_model, config_path(), request.model)
        ctx.manager.set_main_model(request.model)
        return models_now()

    @router.put("/agent/resume")
    async def choose_resume(request: ResumeRequest) -> ResumeView:
        """Have the daemon's next start resume on its own the runs it cut off, or leave them to the owner (item 231):
        the config file keeps it as ``[agent] resume``; a config file that cannot be changed is refused, and nothing
        changes then. This start's runs were resumed or left as it began."""
        await asyncio.to_thread(set_resume, config_path(), request.resume)
        ctx.manager.set_resume_mode(request.resume)
        return resume_now()

    def judge_now() -> JudgeView:
        """The typed judge as the owner last switched it (item 255), and whether its service's key is set."""
        return JudgeView(
            provider=ctx.runtime.judge.provider,
            key_name=config.judge.api_key_env,
            key_set=secret_state(config.judge.api_key_env).source is not None,
        )

    @router.get("/judge")
    async def judge() -> JudgeView:
        return judge_now()

    @router.put("/judge")
    async def choose_judge(request: JudgeRequest) -> JudgeView:
        """Have the typed judge answer through TypeSafe, or answer nothing, from the next judgment on (item 255): the
        config file keeps it as ``[judge] provider``. Switching it on without the TypeSafe service (its key was not set
        when the daemon started) is refused, as is a config file that cannot be changed; nothing changes then."""
        ctx.runtime.judge.set_provider(request.provider)
        await asyncio.to_thread(set_judge_provider, config_path(), request.provider)
        return judge_now()

    @router.get("/shell/sandbox")
    async def sandbox() -> SandboxView:
        return SandboxView(sandbox=ctx.runtime.sandbox.on)

    @router.put("/shell/sandbox")
    async def switch_sandbox(request: SandboxRequest) -> SandboxView:
        """Start the processes of runs outside yolo in the macOS sandbox, or without it, from the next one on (item
        262): the config file keeps it as ``[shell] sandbox``. Switching it on where its profiles do not start is
        refused, and so is a config file that cannot be changed: the sandbox is then as it was."""
        before: bool = ctx.runtime.sandbox.on
        await ctx.runtime.sandbox.switch(request.sandbox)
        try:
            await asyncio.to_thread(set_shell_sandbox, config_path(), request.sandbox)
        except (ConfigError, OSError):
            await ctx.runtime.sandbox.switch(before)
            raise
        return SandboxView(sandbox=ctx.runtime.sandbox.on)

    @router.post("/models/roles")
    async def choose_role_model(request: RoleModelRequest) -> ModelsConfig:
        """Have ``model`` do a part of Dot's own work from the next call on (item 232): the fast model, which writes the
        summaries of long runs first, reads pages for the facts asked of them and may take a run's simpler steps; the
        review model, which checks runs and draws their lessons; or the vision model, which reads images for a model
        that cannot. The config file keeps it as ``[models] <role>``. A model its provider does not offer is refused, as
        is one that cannot call tools for the fast or the review model, or one that cannot see for the vision model;
        nothing changes then."""
        info: ModelInfo = await ctx.runtime.router.model_info(parse_model_ref(request.model))
        if request.role == "vision" and not info.vision:
            raise ConfigError("the vision model must be one that can see images", {"model": request.model})
        if request.role != "vision" and not info.tools:
            raise ConfigError(f"the {request.role} model must be one that can call tools", {"model": request.model})
        await asyncio.to_thread(set_role_model, config_path(), request.role, request.model)
        ctx.runtime.roles.choose(request.role, request.model)
        return models_now()

    @router.put("/models/context")
    async def choose_context_size(request: ContextSizeRequest) -> ModelOption:
        """Give the runs of ``model`` a context size of ``tokens`` from its next run on (item 250), or
        ``[agent] context_tokens`` for ``None``: the config file keeps it in ``[models.context]``. A model its provider
        does not offer is refused, as is a size below :data:`CONTEXT_FLOOR` or above three quarters of the model's
        window, any size for a model whose provider does not say its window, and a config file that cannot be
        changed; nothing changes then. The runs under way keep theirs."""
        ref: ModelRef = parse_model_ref(request.model)
        info: ModelInfo = await ctx.runtime.router.model_info(ref)
        ceiling: int | None = context_ceiling(info.context_length)
        if request.tokens is not None and ceiling is None:
            raise ConfigError(
                "the model's provider does not say how many tokens it holds, so its context size cannot be chosen",
                {"model": ref.label},
            )
        if request.tokens is not None and ceiling is not None and not CONTEXT_FLOOR <= request.tokens <= ceiling:
            raise ConfigError(
                f"the context size of {ref.label} must be from {CONTEXT_FLOOR:,} to {ceiling:,} tokens, three quarters "
                "of its window",
                {"model": ref.label, "tokens": request.tokens, "window": info.context_length},
            )
        await asyncio.to_thread(set_context_size, config_path(), ref.label, request.tokens)
        ctx.runtime.context_sizes.choose(ref.label, request.tokens)
        return _option(ref.provider, info, config.models.effort, config.agent, ctx.runtime.context_sizes)

    @router.get("/models/context")
    async def context_evidence(query: Annotated[ContextEvidenceQuery, Query()]) -> ContextEvidence:
        """What the runs of ``model`` of the last 30 days say about its context size (item 250): the input its steps
        sent, the share of it its provider served from its cache, the compactions, and the seconds of a step by its
        input. Read from the run history, which it does not change."""
        return await ctx.runtime.store.context_evidence(parse_model_ref(query.model).label, _EVIDENCE_DAYS)

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

    async def found_now() -> FoundElsewhere:
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        added: AddedServers = await asyncio.to_thread(read_added_servers, added_servers_path())
        has: DotHas = dot_has(config, catalog, ctx.runtime.plugins.plugins, added)
        return await asyncio.to_thread(found_elsewhere, Path.home(), has, SKILL_COPY_LIMIT)

    async def add_found(found: FoundElsewhere, wanted: ElsewhereItem) -> None:
        """Add the skill or the MCP server ``wanted`` names, as ``found`` has it."""
        context: dict[str, str] = {"kind": wanted.kind, "agent": wanted.agent, "name": wanted.name}
        if wanted.kind == "skill":
            skill: FoundSkill | None = next(
                (item for item in found.skills if (item.agent, item.name) == (wanted.agent, wanted.name)), None
            )
            if skill is None:
                raise ElsewhereError(f"no skill {wanted.name} of {wanted.agent} is found on this Mac", context)
            await asyncio.to_thread(add_skill, skills_dir(), skill, SKILL_COPY_LIMIT)
            return
        server: FoundServer | None = next(
            (item for item in found.servers if (item.agent, item.name) == (wanted.agent, wanted.name)), None
        )
        if server is None:
            raise ElsewhereError(f"no MCP server {wanted.name} of {wanted.agent} is found on this Mac", context)
        await asyncio.to_thread(add_server, added_servers_path(), Path.home(), server)

    @router.get("/skills/elsewhere")
    async def skills_elsewhere() -> FoundElsewhere:
        """The skills and MCP servers of the Mac's other agents (item 164), read from their files; Dot does not use
        them until the owner adds them (item 165). Each says whether Dot has it already, whether the owner added it
        from here, or why it cannot be added."""
        return await found_now()

    @router.put("/skills/elsewhere")
    async def change_elsewhere(change: ElsewhereChange) -> FoundElsewhere:
        """Add a skill or an MCP server found elsewhere to Dot, or take it back: the owner's yes (item 165). A skill is
        copied into Dot's skills folder, offered to runs from their next look at the skills; a server is started from
        Dot's next start. One is taken back by its agent and its name, whether or not the agent still has it. What is
        found after it."""
        if change.added:
            await add_found(await found_now(), ElsewhereItem(kind=change.kind, agent=change.agent, name=change.name))
        elif change.kind == "skill":
            await asyncio.to_thread(remove_skill, skills_dir(), change.agent, change.name)
        else:
            await asyncio.to_thread(remove_server, added_servers_path(), change.agent, change.name)
        log.info("elsewhere_changed", kind=change.kind, agent=change.agent, name=change.name, added=change.added)
        return await found_now()

    @router.post("/skills/elsewhere/import")
    async def import_elsewhere(request: ElsewhereImport) -> ElsewhereImported:
        """Add several skills and MCP servers found elsewhere at once: the owner's yes to each (item 219). Each is added
        as the one scan before them found it, as ``PUT /skills/elsewhere`` adds one; one that cannot be added (taken by
        an item before it, changed, gone) is told with its reason and the others go on. What it added, what it could
        not, and what is found after it."""
        found: FoundElsewhere = await found_now()
        added: list[ElsewhereItem] = []
        refused: list[ElsewhereRefusal] = []
        for item in request.items:
            try:
                await add_found(found, item)
            except ElsewhereError as error:
                refused.append(ElsewhereRefusal(item=item, reason=error.message))
                continue
            added.append(item)
        log.info("elsewhere_imported", asked=len(request.items), added=len(added), refused=len(refused))
        return ElsewhereImported(added=tuple(added), refused=tuple(refused), found=await found_now())

    @router.get("/palette")
    async def palette() -> PaletteView:
        """The skills and the MCP tools for the composer's slash menu, read from the folders as a run reads them."""
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        return palette_view(catalog.skills, ctx.runtime.mcp.tools)

    @router.get("/doctor")
    async def doctor() -> list[CheckResult]:
        # The models and the judge as they stand now, not as the config file said at start.
        judge_config: JudgeConfig = config.judge.model_copy(update={"provider": ctx.runtime.judge.provider})
        return await run_quick_checks(config.model_copy(update={"models": models_now(), "judge": judge_config}))

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
