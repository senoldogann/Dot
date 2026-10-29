"""Routes for the state of the installation: health, the proof of the daemon, status, models and their context sizes,
checks, desktop permissions, the emergency stop and screenshots."""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from dotagent import __version__
from dotagent.agent.playbooks import (
    PlaybookEntry,
    PlaybookMeta,
    drop_playbook,
    list_playbooks,
    share_playbook,
    skill_resting,
)
from dotagent.agent.scaffold import (
    context_budget,
    context_ceiling,
    most_bound,
    most_window,
    run_window,
    summarise_point,
    window_choices,
)
from dotagent.agent.sizes import ContextSizes
from dotagent.agent.skill_history import (
    pin_playbook,
    rest_playbook,
    restore_playbook,
    versions_of,
    wake_playbook,
)
from dotagent.agent.skill_proposals import accept_proposal, dismiss_proposal, waiting_proposals
from dotagent.config import CONTEXT_FLOOR, AgentConfig, AppConfig, JudgeConfig, JudgeProvider, ModelsConfig
from dotagent.config_edit import (
    set_context_size,
    set_default_model,
    set_judge_provider,
    set_resume,
    set_role_model,
    set_shell_sandbox,
    set_standby_models,
)
from dotagent.connections.directory import ServiceMark, mark_of_service
from dotagent.core.errors import (
    ConfigError,
    DotError,
    ElsewhereError,
    MissingSecretError,
    NotFoundError,
    OpenCodeMissing,
    ProviderAccessError,
    ProviderAuthError,
    ServiceError,
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
from dotagent.desktop.perms import (
    PANE_TITLES,
    check_permissions,
    missing_panes,
    open_settings,
    request_permissions,
    responsible_program,
)
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
    copied_skill_names,
    found_elsewhere,
)
from dotagent.launchd import JobState, inspect_job, service_runs
from dotagent.llm.opencode import OPENCODE_PROVIDER
from dotagent.llm.router import ModelRef, ModelRouter, offers_fast_mode, parse_model_ref
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import KNOWN_PROVIDERS, Effort, ModelInfo
from dotagent.opencode.server import learns_from_data
from dotagent.secrets import secret_state
from dotagent.server.context import ServerContext
from dotagent.server.guard import daemon_proof, refuse_from_a_phone
from dotagent.server.palette import palette_view
from dotagent.server.phones import PairedPhones
from dotagent.server.routes_mcp import service_of
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
    PaneRequest,
    PermissionsView,
    PinRequest,
    ProofQuery,
    ProofView,
    ProviderModels,
    ProviderProblem,
    PushKeyView,
    PushSubscribeRequest,
    RestartView,
    ResumeRequest,
    ResumeView,
    RoleModelRequest,
    SandboxRequest,
    SandboxView,
    SkillProposalsView,
    SkillRestoreRequest,
    SkillShareRequest,
    SkillVersionsView,
    StandbyModelsRequest,
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


def _context_choice(config: AgentConfig, info: ModelInfo, chosen: int | None) -> ContextChoice:
    """The context size of the runs of the model ``info`` describes, whose size is ``chosen``: up to three quarters of
    the most window it may be given, its own for an Ollama model, which is asked for the window its size needs (item
    520)."""
    window: int | None = run_window(info, chosen)
    tokens: int = context_budget(config, window, chosen)
    return ContextChoice(
        tokens=tokens,
        chosen=chosen,
        default=context_budget(config, info.context_length, None),
        configured=config.context_tokens if isinstance(config.context_tokens, int) else None,
        most=most_bound(config),
        floor=CONTEXT_FLOOR,
        ceiling=context_ceiling(most_window(info)),
        sizes=list(window_choices(config, info, chosen)),
        summarise_at=round(summarise_point(config, window, chosen, tokens)),
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
        context=_context_choice(agent, info, sizes.of(ref.label)),
        efforts=list(info.efforts),
        default_effort=effective_effort(info.efforts, default_effort),
        free=info.free,
        learns=provider == OPENCODE_PROVIDER and learns_from_data(info.id),
        fast_mode=offers_fast_mode(ref),
    )


def _problem(exc: DotError) -> ProviderProblem:
    """The kind of failure of a provider: the page sends a missing or refused key to Settings › Providers, and says
    that the provider refused access, which a key does not change."""
    if isinstance(exc, MissingSecretError):
        return "no_key"
    if isinstance(exc, OpenCodeMissing):
        return "not_installed"
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


def proof_router(token: str, phones: PairedPhones) -> APIRouter:
    """The answer to a client's nonce with which the daemon shows that it holds ``token``, asked before the token is
    sent (:func:`dotagent.server.guard.daemon_proof`). A phone with a key of its own asks with its id, and the proof is
    keyed with the hash of its key, the one thing the daemon holds of it (item 670); an id that is not listed is
    answered under a key of its own that a secret of this daemon makes, the same at every ask as a listed one's, so the
    answer tells nothing of which ids are, nor whether a phone was removed, and the phone signs in again."""
    router: APIRouter = APIRouter(prefix="/api")
    unknown: bytes = secrets.token_bytes(32)

    @router.get("/proof")
    async def proof(query: Annotated[ProofQuery, Query()]) -> ProofView:
        if query.phone is None:
            return ProofView(proof=daemon_proof(token, query.nonce))
        key: str | None = phones.proof_key(query.phone)
        if key is None:
            key = hmac.new(unknown, query.phone.encode("ascii"), hashlib.sha256).hexdigest()
        return ProofView(proof=daemon_proof(key, query.nonce))

    return router


def system_router(ctx: ServerContext, pages: str) -> APIRouter:
    """The daemon's own routes; ``pages`` is what the pages it serves are
    (``dotagent.server.kept_pages.pages_build``)."""
    router: APIRouter = APIRouter(prefix="/api")
    config: AppConfig = ctx.runtime.config

    async def engaged() -> bool:
        return await asyncio.to_thread(failsafe_path().exists)

    def models_now() -> ModelsConfig:
        """The configured models, the default one, those of Dot's own work, the standby models and the context sizes as
        the owner last chose them (items 199, 232, 250 and 478)."""
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
            schedule_stop_after_failures=config.scheduler.stop_after_failures,
            schedule_notify=config.scheduler.notify,
            browser_mode=ctx.runtime.browser.mode,
            desktop_enabled=config.desktop.enabled,
            mcp_tools=len(ctx.runtime.mcp.tools),
            mcp_failures=list(ctx.runtime.mcp.failures),
            models=models_now(),
            resume=resume_now(),
            night_work=[] if ctx.scheduler is None else list(ctx.scheduler.night_work()),
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
            raise ConfigError("a task needs a model that can use actions", {"model": request.model})
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
    async def choose_judge(request: JudgeRequest, http: Request) -> JudgeView:
        """Have the typed judge answer through TypeSafe, or answer nothing, from the next judgment on (item 255): the
        config file keeps it as ``[judge] provider``. Switching it on without the TypeSafe service (its key was not set
        when the daemon started) is refused, as is a config file that cannot be changed; nothing changes then. Switching
        it off is done on the Mac (item 678): the clicks no pattern knows would then go through unasked."""
        if request.provider == "none":
            refuse_from_a_phone(http.scope, "switch the extra checker off")
        before: JudgeProvider = ctx.runtime.judge.provider
        ctx.runtime.judge.set_provider(request.provider)
        try:
            await asyncio.to_thread(set_judge_provider, config_path(), request.provider)
        except (ConfigError, OSError):
            # A config file that cannot be changed leaves the judge as it was, as the docstring says (item 558).
            ctx.runtime.judge.set_provider(before)
            raise
        return judge_now()

    @router.get("/shell/sandbox")
    async def sandbox() -> SandboxView:
        return SandboxView(sandbox=ctx.runtime.sandbox.on)

    @router.put("/shell/sandbox")
    async def switch_sandbox(request: SandboxRequest, http: Request) -> SandboxView:
        """Start the processes of runs outside yolo in the macOS sandbox, or without it, from the next one on (item
        262): the config file keeps it as ``[shell] sandbox``. Switching it on where its profiles do not start is
        refused, and so is a config file that cannot be changed: the sandbox is then as it was. Switching it off is done
        on the Mac (item 678)."""
        if not request.sandbox:
            refuse_from_a_phone(http.scope, "switch the safe box of Dot's commands off")
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
        is one that cannot call tools for the fast or the review model, or one that cannot see for the vision model,
        and one of Zen's free models, which answer only as OpenCode's own agent (item 283); nothing changes then."""
        ref: ModelRef = parse_model_ref(request.model)
        if ref.provider == OPENCODE_PROVIDER:
            raise ConfigError(
                f"Zen's free models answer only as OpenCode's own agent, so none can be the {request.role} model;"
                " pick another",
                {"model": request.model},
            )
        info: ModelInfo = await ctx.runtime.router.model_info(ref)
        if request.role == "vision" and not info.vision:
            raise ConfigError("the vision model must be one that can see images", {"model": request.model})
        if request.role != "vision" and not info.tools:
            raise ConfigError(f"the {request.role} model must be one that can use actions", {"model": request.model})
        await asyncio.to_thread(set_role_model, config_path(), request.role, request.model)
        ctx.runtime.roles.choose(request.role, request.model)
        return models_now()

    @router.put("/models/standby")
    async def choose_standby_models(request: StandbyModelsRequest) -> ModelsConfig:
        """Have a run whose own model cannot answer (a quota, rate-limit, key or access error, or its content filter's
        stop) move to ``models``, in their order and each once at most, from its next move on (item 478); none ends such
        a run with its model's error. The config file keeps them as ``[models] standby``. A model named twice, one its
        provider does not offer, one that cannot call tools and one of Zen's free models, which answer only as
        OpenCode's own agent, are refused, as is a config file that cannot be changed; nothing changes then. A run that
        moved already keeps the model it moved to."""
        refs: list[ModelRef] = [parse_model_ref(model) for model in request.models]
        labels: list[str] = [ref.label for ref in refs]
        if len(set(labels)) != len(labels):
            raise ConfigError("a standby model is named twice", {"models": ", ".join(labels)})
        for ref in refs:
            if ref.provider == OPENCODE_PROVIDER:
                raise ConfigError(
                    "Zen's free models answer only as OpenCode's own agent, so none can be a standby model;"
                    " pick another",
                    {"model": ref.label},
                )
            info: ModelInfo = await ctx.runtime.router.model_info(ref)
            if not info.tools:
                raise ConfigError("a standby model must be one that can use actions", {"model": ref.label})
        await asyncio.to_thread(set_standby_models, config_path(), labels)
        ctx.runtime.roles.choose_standby(labels)
        return models_now()

    @router.put("/models/context")
    async def choose_context_size(request: ContextSizeRequest) -> ModelOption:
        """Give the runs of ``model`` a context size of ``tokens`` from its next run on (item 250), or the default one
        ``[agent] context_tokens`` gives for ``None``: the config file keeps it in ``[models.context]``. A model its
        provider does not offer is refused, as is a size below :data:`CONTEXT_FLOOR` or above three quarters of the
        model's window, any size for a model whose provider does not say its window, and a config file that cannot be
        changed; nothing changes then. The runs under way keep theirs."""
        ref: ModelRef = parse_model_ref(request.model)
        info: ModelInfo = await ctx.runtime.router.model_info(ref)
        # An Ollama model may be given up to three quarters of its own window, which it is then asked for (item 520).
        ceiling: int | None = context_ceiling(most_window(info))
        if request.tokens is not None and ceiling is None:
            raise ConfigError(
                "the model's service does not say how much it can take in, so its chat size cannot be chosen",
                {"model": ref.label},
            )
        if request.tokens is not None and ceiling is not None and not CONTEXT_FLOOR <= request.tokens <= ceiling:
            raise ConfigError(
                f"the chat size of {ref.label} must be from {CONTEXT_FLOOR:,} to {ceiling:,}, three quarters of what "
                "the model can take in",
                {"model": ref.label, "tokens": request.tokens, "window": most_window(info)},
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

    async def learned_list() -> LearnedSkillsView:
        """The learned skills as the page shows them now (item 585: which rest, and after how many days)."""
        entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
        days: int = config.agent.skill_rest_days
        now: float = time.time()
        return LearnedSkillsView(items=tuple(learned_skill_view(entry, now, days) for entry in entries), rest_days=days)

    @router.get("/skills/learned")
    async def learned_skills() -> LearnedSkillsView:
        """The skills Dot learned from its own work, active first, then the retired ones."""
        return await learned_list()

    @router.delete("/skills/learned/{name}")
    async def drop_learned_skill(name: str) -> LearnedSkillsView:
        """Remove a learned skill, active or retired; the list after it. Only the owner deletes a skill: nothing Dot
        does on its own does (item 585)."""
        await asyncio.to_thread(drop_playbook, playbooks_dir(), name)
        return await learned_list()

    @router.put("/skills/learned/{name}")
    async def share_learned_skill(name: str, request: SkillShareRequest) -> LearnedSkillsView:
        """Offer an agent's learned skill to every run, or to its agent's alone again; the list after it."""
        await asyncio.to_thread(share_playbook, playbooks_dir(), name, request.shared)
        return await learned_list()

    @router.get("/skills/learned/{name}/versions")
    async def learned_skill_versions(name: str) -> SkillVersionsView:
        """The versions of a learned skill, the oldest first and its own now last, with the run that made each and what
        its file says (item 585)."""
        return SkillVersionsView(name=name, items=await asyncio.to_thread(versions_of, playbooks_dir(), name))

    @router.post("/skills/learned/{name}/restore")
    async def restore_learned_skill(name: str, request: SkillRestoreRequest) -> LearnedSkillsView:
        """Put a version of a learned skill back as its steps, as a new version of its own, so that it too can be taken
        back; the list after it (item 585)."""
        await asyncio.to_thread(restore_playbook, playbooks_dir(), name, request.version, time.time())
        return await learned_list()

    @router.put("/skills/learned/{name}/pin")
    async def pin_learned_skill(name: str, request: PinRequest) -> LearnedSkillsView:
        """Pin a learned skill so that it never rests (a skill that rests wakes), or take the pin off; the list after it
        (item 585)."""
        await asyncio.to_thread(pin_playbook, playbooks_dir(), name, request.pinned, time.time())
        return await learned_list()

    @router.post("/skills/learned/{name}/rest")
    async def rest_learned_skill(name: str) -> LearnedSkillsView:
        """Put a learned skill to rest by hand: no run is offered it until the owner wakes it; the list after it (item
        585)."""
        await asyncio.to_thread(rest_playbook, playbooks_dir(), name, time.time())
        return await learned_list()

    @router.post("/skills/learned/{name}/wake")
    async def wake_learned_skill(name: str) -> LearnedSkillsView:
        """Wake a learned skill that rests, whether for disuse, by hand or for a merge; the list after it (item 585)."""
        await asyncio.to_thread(wake_playbook, playbooks_dir(), name, time.time())
        return await learned_list()

    @router.get("/skills/proposals")
    async def skill_proposals() -> SkillProposalsView:
        """The merges and rewrites of learned skills that wait for the owner's yes (item 585)."""
        return SkillProposalsView(items=await asyncio.to_thread(waiting_proposals, playbooks_dir()))

    @router.post("/skills/proposals/{proposal_id}/accept")
    async def accept_skill_proposal(proposal_id: str) -> LearnedSkillsView:
        """The owner's yes: the draft they were shown is written as a new version of the skill it names first (the file
        it replaces kept), and a merge puts the other skill to rest; 409 for a proposal whose skills changed since;
        the skills after it (item 585)."""
        await asyncio.to_thread(accept_proposal, playbooks_dir(), proposal_id, time.time())
        return await learned_list()

    @router.delete("/skills/proposals/{proposal_id}")
    async def dismiss_skill_proposal(proposal_id: str) -> SkillProposalsView:
        """The owner's no: the proposal is dropped, and a merge of the same skills is proposed again only after one of
        them changes; the proposals after it (item 585)."""
        await asyncio.to_thread(dismiss_proposal, playbooks_dir(), proposal_id)
        return SkillProposalsView(items=await asyncio.to_thread(waiting_proposals, playbooks_dir()))

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
            raise ElsewhereError(f"no connected app {wanted.name} of {wanted.agent} is found on this Mac", context)
        await asyncio.to_thread(add_server, added_servers_path(), Path.home(), server)

    @router.get("/skills/elsewhere")
    async def skills_elsewhere() -> FoundElsewhere:
        """The skills and MCP servers of the Mac's other agents (item 164), read from their files; Dot does not use
        them until the owner adds them (item 165). Each says whether Dot has it already, whether the owner added it
        from here, or why it cannot be added."""
        return await found_now()

    @router.put("/skills/elsewhere")
    async def change_elsewhere(change: ElsewhereChange, http: Request) -> FoundElsewhere:
        """Add a skill or an MCP server found elsewhere to Dot, or take it back: the owner's yes (item 165). A skill is
        copied into Dot's skills folder, offered to runs from their next look at the skills; a server is started from
        Dot's next start. One is taken back by its agent and its name, whether or not the agent still has it. What is
        found after it. A server is added on the Mac (item 678): Dot starts it with its command outside the sandbox."""
        if change.added and change.kind == "server":
            refuse_from_a_phone(http.scope, "add a connected app")
        if change.added:
            await add_found(await found_now(), ElsewhereItem(kind=change.kind, agent=change.agent, name=change.name))
        elif change.kind == "skill":
            await asyncio.to_thread(remove_skill, skills_dir(), change.agent, change.name)
        else:
            await asyncio.to_thread(remove_server, added_servers_path(), change.agent, change.name)
        log.info("elsewhere_changed", kind=change.kind, agent=change.agent, name=change.name, added=change.added)
        return await found_now()

    @router.post("/skills/elsewhere/import")
    async def import_elsewhere(request: ElsewhereImport, http: Request) -> ElsewhereImported:
        """Add several skills and MCP servers found elsewhere at once: the owner's yes to each (item 219). Each is added
        as the one scan before them found it, as ``PUT /skills/elsewhere`` adds one; one that cannot be added (taken by
        an item before it, changed, gone) is told with its reason and the others go on. What it added, what it could
        not, and what is found after it. Servers are added on the Mac (item 678)."""
        if any(item.kind == "server" for item in request.items):
            refuse_from_a_phone(http.scope, "add a connected app")
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
        imported: frozenset[str] = await asyncio.to_thread(copied_skill_names, catalog.skills)
        # Each server's mark, the one its Customize tile wears (item 408): the composer's menus draw it as the page
        # does (items 447 and 477).
        marks: dict[str, ServiceMark | None] = {
            name: mark_of_service(service) if (service := service_of(server)) is not None else None
            for name, server in ctx.runtime.mcp.configs().items()
        }
        return palette_view(
            catalog.skills, imported, ctx.runtime.mcp.tools, marks, await ctx.runtime.accounts.accounts()
        )

    @router.get("/doctor")
    async def doctor() -> list[CheckResult]:
        # The models and the judge as they stand now, not as the config file said at start.
        judge_config: JudgeConfig = config.judge.model_copy(update={"provider": ctx.runtime.judge.provider})
        return await run_quick_checks(config.model_copy(update={"models": models_now(), "judge": judge_config}))

    async def _service() -> tuple[bool, str | None]:
        """Whether this daemon is the service's own process (launchd's job runs Dot.app's launcher, the daemon's
        parent), so that a stop has launchd start it again; and why that could not be told, when launchd did not
        answer, which the page shows and the log keeps."""
        try:
            job: JobState | None = await inspect_job(os.getuid())
        except ServiceError as error:
            log.warning("launchd_unanswered", error=error.message, context=error.context)
            return False, error.message
        return service_runs(job, (os.getpid(), os.getppid())), None

    async def _permissions_view(granted: Permissions) -> PermissionsView:
        service, problem = await _service()
        return PermissionsView(
            program=str(responsible_program()),
            accessibility=granted.accessibility,
            input_events=granted.post_event,
            screen_recording=granted.screen_recording,
            panes=list(missing_panes(granted)),
            titles=dict(PANE_TITLES),
            service=service,
            service_problem=problem,
        )

    @router.get("/desktop/permissions")
    async def desktop_permissions() -> PermissionsView:
        """What macOS has granted the program it holds responsible for this daemon, asked without a prompt (item
        409)."""
        return await _permissions_view(await asyncio.to_thread(check_permissions))

    @router.post("/desktop/permissions")
    async def ask_for_desktop_permissions() -> PermissionsView:
        """Make macOS ask, in the name of the program it holds responsible for this daemon, for what the desktop is
        missing; the asking puts the program in the panes' lists."""
        return await _permissions_view(await asyncio.to_thread(request_permissions))

    @router.post("/desktop/permissions/open")
    async def open_permission_pane(request: PaneRequest) -> PermissionsView:
        """Show the pane of System Settings where ``pane`` is switched on (item 409)."""
        await asyncio.to_thread(open_settings, request.pane)
        return await _permissions_view(await asyncio.to_thread(check_permissions))

    @router.post("/system/restart")
    async def restart_daemon() -> RestartView:
        """Stop the daemon so that its service starts it again (item 409): a running process keeps the permissions it
        had when it started. Refused when nothing would start it again: a daemon run by hand, or one whose service
        launchd could not be asked about."""
        service, problem = await _service()
        if problem is not None:
            raise ConfigError(
                "Dot could not tell whether it starts again by itself, so it was not stopped", {"error": problem}
            )
        if not service:
            raise ConfigError(
                "Dot was not started the way that lets it start itself again: close it and open it again yourself, or "
                "set it up to start by itself (in Terminal: dotx service install)",
                {},
            )
        ctx.restart()
        return RestartView(restarting=True)

    @router.get("/failsafe")
    async def failsafe_state() -> FailsafeView:
        return FailsafeView(engaged=await engaged(), cancelled_runs=[])

    @router.post("/failsafe")
    async def engage_failsafe() -> FailsafeView:
        """Block desktop control, stop every run that is going on as an emergency stop (their campaigns stop with them)
        and hold the scheduler until the stop is released."""
        await asyncio.to_thread(failsafe_path().touch)
        return FailsafeView(engaged=True, cancelled_runs=await ctx.manager.emergency_stop())

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


def learned_skill_view(entry: PlaybookEntry, now: float, rest_days: int) -> LearnedSkillView:
    """A learned skill as the page shows it at ``now``, with the days after which a skill no run read rests."""
    meta: PlaybookMeta = entry.meta
    resting: bool = not entry.retired and skill_resting(meta, now, rest_days)
    reason: Literal["disuse", "owner", "merged"] | None = None
    if resting:
        reason = "merged" if meta.merged_into is not None else ("owner" if meta.rested_at is not None else "disuse")
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
        taught=meta.taught,
        tainted=meta.tainted,
        pinned=meta.pinned,
        resting=resting,
        rest_reason=reason,
        last_read_at=meta.last_read_at,
        merged_into=meta.merged_into,
        version=meta.version,
    )
