"""Playing eval tasks: each attempt is an unattended run in a home and a workspace of its own, judged by the checks of
the task, and everything it does is counted.

The process points at a throw-away Dot home for the length of an attempt (``DOT_HOME``), so the database, the
workspace and the screenshots of the user are never touched. The API keys of the models an attempt may call
(:func:`reached_models`) are looked up before the switch, because the home that holds the user's ``.env`` file is not
the current one then, and are passed on by name.
"""

import asyncio
import base64
import json
import os
import tempfile
import time
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from dotagent.agent.manager import RunManager, goal_mode_asked
from dotagent.config import AgentConfig, AppConfig, PluginsConfig, SkillsConfig
from dotagent.core.errors import EvalError
from dotagent.core.logs import get_logger
from dotagent.core.paths import skills_dir
from dotagent.core.text import truncate_middle
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.checks import evaluate_checks, workspace_path
from dotagent.evals.metrics import run_metrics, took_easy_way
from dotagent.evals.results import CheckOutcome, Cut, RunMetrics, TaskOutcome
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.site import serve_site, with_site
from dotagent.evals.types import EvalTask, FileSeed, SkillSeed
from dotagent.events import Subscription
from dotagent.harness.components import NO_HARNESS, GivenText
from dotagent.harness.service import install
from dotagent.harness.tasks import OWN_PREFIX
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatProvider
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.secrets import resolve_secret
from dotagent.skills.types import SKILL_FILE
from dotagent.store.models import EventRecord, RunRecord

log = get_logger(__name__)

# The model the offline check names: a scripted model that stands in for the ``ollama`` connector.
SCRIPTED_MODEL: str = "ollama:scripted"
_SCRIPTED_PROVIDER: str = "ollama"
_DECIDER: str = "eval"
_DENIAL_NOTE: str = "Nobody is there to answer: this is an unattended eval run."
_MAX_EVENTS: int = 100_000
_ANSWER_CHARS: int = 4000
# What a run that does nothing says.
_NOTHING_ANSWER: str = "Done."
# Python tells a source from its cached bytecode by size and whole-second modification time. A replayed script edits a
# file within the second in which it was compiled, so an edit that keeps the size would still run the old code; the
# attempts write no bytecode cache.
_ATTEMPT_ENV: dict[str, str] = {"PYTHONDONTWRITEBYTECODE": "1"}


def eval_config(config: AppConfig) -> AppConfig:
    """The configuration an eval run uses: the given one, without what must not take part in a measurement (the MCP
    servers of the user and those that come with Dot (item 583), whose tools would reach the web, the skills and plugins
    of the user, the Mac desktop, the embeddings of the memory, the lessons and the playbooks, which would carry what
    one attempt learnt into the next, the user's helper and standby models, which would answer for the model under
    measure, the user's limits of a run (a task's own time and token limits cut its runs) and the checkpoints of a long
    run, whose judge is the review model (item 909); the browser is Dot's own Chrome, whatever the owner's mode, so that
    a measurement never touches the owner's (item 579: a campaign follows the mode), and headless, since nobody watches
    an attempt."""
    return config.model_copy(
        update={
            "browser": config.browser.model_copy(update={"headless": True, "mode": "own"}),
            "models": config.models.model_copy(update={"helpers": [], "standby": []}),
            "agent": config.agent.model_copy(
                update={
                    "lessons": "off",
                    "playbooks": False,
                    "run_max_minutes": 0.0,
                    "run_max_tokens": 0,
                    # The checkpoints of a long run (item 909) ask the review model, which a measurement of the model
                    # under test leaves out, as it does the helper models.
                    "checkpoint_steps": 0,
                }
            ),
            "mcp": [],
            "builtin_mcp": config.builtin_mcp.model_copy(update={"web_research": False}),
            "skills": SkillsConfig(dirs=[], elsewhere=False),
            "plugins": PluginsConfig(dirs=[]),
            "desktop": config.desktop.model_copy(update={"enabled": False, "keep_awake": False}),
            "memory": config.memory.model_copy(update={"embed_enabled": False}),
        }
    )


def secret_names(config: AppConfig, models: Sequence[str]) -> tuple[str, ...]:
    """The names of the API keys that the providers of ``models`` need."""
    providers: set[str] = {parse_model_ref(model).provider for model in models}
    names: list[str] = []
    if "ollama" in providers:
        names.append(config.providers.ollama.api_key_env)
    if "openai" in providers:
        openai = config.providers.openai
        names.extend((openai.api_key_env, openai.organization_env, openai.project_env))
    if "opencode_go" in providers:
        names.append(config.providers.opencode_go.api_key_env)
    if "anthropic" in providers:
        names.append(config.providers.anthropic.api_key_env)
    if "openrouter" in providers:
        names.append(config.providers.openrouter.api_key_env)
    return tuple(names)


def _chosen_goal_mode(task: EvalTask) -> bool | None:
    """The goal mode an attempt at ``task`` asks for: on for a task that runs in goal mode, otherwise none, so that the
    run takes the configured one (``[agent] goal_mode``), which the report keeps as a setting of the eval run."""
    return True if task.goal_mode else None


def required_models(config: AppConfig, model: str, tasks: Sequence[EvalTask]) -> tuple[str, ...]:
    """The models the attempts at ``tasks`` cannot go without, whose keys an eval run needs before it starts: ``model``,
    the one under measure, and the review model when it checks the end of runs or gives second opinions: with
    ``[agent] goal_mode`` or ``stall_escalation`` on, or for a task that runs in goal mode (its own, or a goal that asks
    to keep going until it is done)."""
    consults: bool = (
        config.agent.goal_mode
        or config.agent.stall_escalation
        or any(goal_mode_asked(task.goal, task.mode, _chosen_goal_mode(task)) is True for task in tasks)
    )
    return (model, config.models.review) if consults else (model,)


def reached_models(config: AppConfig, model: str) -> tuple[str, ...]:
    """Every model an attempt may call: ``model`` and the models of Dot's own work (the fast, review and vision models).
    The keys of those that have one go to the attempt as the daemon would find them, since its home has no ``.env``
    file; only those of :func:`required_models` must be found."""
    return (model, config.models.fast, config.models.review, config.models.vision)


def required_secret_names(config: AppConfig, models: Sequence[str]) -> tuple[str, ...]:
    """The names, among those of :func:`secret_names`, of the keys without which a model cannot be reached (a local
    Ollama server needs none)."""
    providers: set[str] = {parse_model_ref(model).provider for model in models}
    names: list[str] = []
    if "openai" in providers:
        names.append(config.providers.openai.api_key_env)
    if "opencode_go" in providers:
        names.append(config.providers.opencode_go.api_key_env)
    if "anthropic" in providers:
        names.append(config.providers.anthropic.api_key_env)
    if "openrouter" in providers:
        names.append(config.providers.openrouter.api_key_env)
    return tuple(names)


def lookup_secrets(names: Sequence[str]) -> dict[str, str]:
    """The keys among ``names`` that are set, found the way the daemon finds them (the environment, the ``.env`` file of
    the home, the Keychain). Blocking."""
    found: dict[str, str] = {}
    for name in names:
        value: str | None = resolve_secret(name)
        if value is not None:
            found[name] = value
    return found


@contextmanager
def scratch_home() -> Iterator[Path]:
    """A new empty directory that is removed when the block ends. Its path is resolved, because the system keeps
    temporary directories behind a link and the policy compares real paths."""
    with tempfile.TemporaryDirectory(prefix="dot-eval-") as directory:
        yield Path(directory).resolve()


@contextmanager
def eval_environment(home: Path, secrets: Mapping[str, str]) -> Iterator[None]:
    """Make ``home`` the Dot home of this process, and give it ``secrets`` as environment variables, until the block
    ends; then put the environment back as it was. Nothing runs concurrently with an attempt, so the process-wide
    switch cannot be seen by anything else."""
    changes: dict[str, str] = {"DOT_HOME": str(home), "DOT_CHROME_DIR": str(home / "chrome"), **secrets}
    saved: dict[str, str | None] = {name: os.environ.get(name) for name in changes}
    os.environ.update(changes)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def site_config(config: AppConfig) -> AppConfig:
    """The configuration of an attempt at a task with a site of its own: the web tools may reach the loopback address
    the site is served on."""
    return config.model_copy(update={"web": config.web.model_copy(update={"allow_private_hosts": True})})


def held_to_workspace(config: AppConfig, task: EvalTask) -> AppConfig:
    """The configuration of an attempt at a task the improvement loop wrote (item 904, an id of
    :data:`dotagent.harness.tasks.OWN_PREFIX`): its run under the balanced preset at least, which asks before a write or
    a command outside the workspace, and an attempt declines what it asks. A model wrote the task from the owner's run,
    so what it steers a run to do is kept to the scratch workspace, as a task of the catalog, which a developer wrote,
    need not be (the security review). A task of the catalog is played as the owner's preset says."""
    if not task.id.startswith(OWN_PREFIX) or config.policy.preset != "aggressive":
        return config
    return config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "balanced"})})


def with_servers(config: AppConfig, task: EvalTask) -> AppConfig:
    """The configuration of an attempt at ``task``: its run given the MCP servers the task brings (item 167), and its
    tools in groups when the task asks (item 659), so that the groups of those servers start off, as they do in a run of
    more than 128 tools."""
    agent: AgentConfig = config.agent.model_copy(update={"tool_surface": "grouped"}) if task.grouped else config.agent
    return config.model_copy(update={"mcp": [*config.mcp, *task.servers], "agent": agent})


def seed_workspace(workspace: Path, files: Sequence[FileSeed]) -> None:
    """Write the files a task starts from. Blocking."""
    for seed in files:
        path: Path = workspace_path(workspace, seed.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if seed.encoding == "base64":
            path.write_bytes(base64.b64decode(seed.content, validate=True))
        else:
            path.write_text(seed.content, encoding="utf-8")


def seed_skills(folder: Path, skills: Sequence[SkillSeed]) -> None:
    """Write the skills a task brings into ``folder``, the skills folder of the attempt's home (item 167). The
    description is written as a quoted string, which any text can be. Blocking."""
    for skill in skills:
        frontmatter: str = f"---\nname: {skill.name}\ndescription: {json.dumps(skill.description)}\n---\n"
        seed_workspace(
            folder / skill.name, (FileSeed(path=SKILL_FILE, content=frontmatter + skill.body, encoding="text"),)
        )


def judge(
    task: EvalTask,
    attempt: int,
    record: RunRecord,
    cut: Cut,
    checks: Sequence[CheckOutcome],
    injection_checks: Sequence[CheckOutcome],
    metrics: RunMetrics,
    easy_way_taken: bool | None,
    seconds: float,
) -> TaskOutcome:
    """The outcome of an attempt: it passed when the run ended the way the task asks and every check held, the
    injection checks included; it followed the injection when one of those did not hold."""
    every_check: tuple[CheckOutcome, ...] = (*checks, *injection_checks)
    return TaskOutcome(
        task_id=task.id,
        category=task.category,
        attempt=attempt,
        passed=record.status == task.ending and all(check.passed for check in every_check),
        status=record.status,
        cut=cut,
        answer=truncate_middle(record.result, _ANSWER_CHARS),
        checks=every_check,
        injection_followed=None if not injection_checks else any(not check.passed for check in injection_checks),
        metrics=metrics,
        seconds=round(seconds, 2),
        easy_way_taken=easy_way_taken,
    )


async def _decline(runtime: Runtime, event: EventRecord) -> None:
    """Answer a question that holds the run, of the policy or of the agent, with a refusal: nobody is there."""
    approval_id: object = event.data.get("approval_id")
    if not isinstance(approval_id, str):
        raise EvalError("an approval request without an id", {"run_id": event.run_id})
    await runtime.approvals.resolve(approval_id, False, _DECIDER, _DENIAL_NOTE)


def _stop(manager: RunManager, run_id: str) -> None:
    if run_id in manager.active_run_ids():
        manager.cancel(run_id)


async def _stored_spending(runtime: Runtime, run_id: str) -> tuple[int, bool]:
    """What the store says of a run: the tokens it and the runs it delegated to used so far, and whether it ended."""
    run: RunRecord = await runtime.store.get_run(run_id)
    return await runtime.store.family_tokens(run_id), run.finished_at is not None


async def _supervise(
    runtime: Runtime, manager: RunManager, subscription: Subscription, run_id: str, task: EvalTask
) -> Cut:
    """Watch the run until it is over: refuse every question that holds it (a question the agent leaves holds
    nothing and stays unanswered), and cut the run when it takes longer or spends more
    tokens (of every model call of the run and of the runs it delegated to) than the task allows. Returns why it was
    cut, or ``none``. The subscription is opened before the run starts, so that no event is missed. The tokens are read
    from the store at every model call, as the recorder adds them there before the call's event goes out; when the
    queue was full and events were dropped, the tokens and the end of the run are read from the store too."""
    cut: Cut = "none"
    dropped: int = 0
    try:
        async with asyncio.timeout(task.time_limit_s):
            while True:
                event: EventRecord = await subscription.queue.get()
                ended: bool = event.kind == "run_finished" and event.run_id == run_id
                missed: bool = subscription.dropped != dropped
                dropped = subscription.dropped
                if event.kind == CALL_EVENT or missed:
                    spent, stored_end = await _stored_spending(runtime, run_id)
                    ended = ended or stored_end
                    if spent > task.token_budget and cut == "none":
                        cut = "token_budget"
                        _stop(manager, run_id)
                if event.kind == "approval_requested":
                    await _decline(runtime, event)
                if ended:
                    return cut
    except TimeoutError:
        _stop(manager, run_id)
        return "time_limit"


async def _run_in(
    runtime: Runtime, task: EvalTask, model: str, stand_in: ChatProvider | None, attempt: int, harness: GivenText
) -> TaskOutcome:
    """One attempt in an open runtime: seed the workspace, adopt ``harness`` in the attempt's own store as it stands
    when it is not Dot's own text alone (item 391: a probe of the harness; the owner's limits of now apply to new text
    only, item 589), run the goal, judge what is left."""
    if stand_in is not None:
        await runtime.router.register(parse_model_ref(model).provider, stand_in)
    if harness != NO_HARNESS:
        await install(runtime.store, harness, "probed text", "probe", time.time())
    await asyncio.to_thread(seed_workspace, runtime.workspace, task.files)
    manager: RunManager = RunManager(runtime)
    started: float = time.monotonic()
    try:
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await manager.start(
                task.goal,
                task.mode,
                model,
                None,
                EVAL_SOURCE,
                None,
                None,
                "standard",
                None,
                "ask",
                _chosen_goal_mode(task),
                (),
            )
            cut: Cut = await _supervise(runtime, manager, subscription, run.id, task)
        record: RunRecord = await manager.wait(run.id)
    finally:
        await manager.shutdown()
    seconds: float = time.monotonic() - started
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
    checks: tuple[CheckOutcome, ...] = await evaluate_checks(
        task.checks, runtime.workspace, record.result, runtime.shell_env, events
    )
    injection_checks: tuple[CheckOutcome, ...] = await evaluate_checks(
        task.injection_checks, runtime.workspace, record.result, runtime.shell_env, events
    )
    delegated_input, delegated_output = await runtime.store.delegated_usage(record.id)
    return judge(
        task,
        attempt,
        record,
        cut,
        checks,
        injection_checks,
        run_metrics(record, events, delegated_input, delegated_output),
        took_easy_way(task.easy_way, events),
        seconds,
    )


async def _attempt(
    task: EvalTask,
    config: AppConfig,
    login_env: Mapping[str, str],
    secrets: Mapping[str, str],
    model: str,
    scripted: bool,
    attempt: int,
    harness: GivenText,
) -> TaskOutcome:
    """One attempt in a throw-away home that is removed afterwards, holding the skills the task brings, its run given
    the MCP servers the task brings, with the task's site (if it has one) served for its length and its address put
    into the goal and the reference. ``scripted`` plays the reference through a scripted model instead of ``model``."""
    with scratch_home() as home, eval_environment(home, secrets), serve_site(task.site) as address:
        placed: EvalTask = with_site(task, address) if task.site else task
        stand_in: ChatProvider | None = ScriptedProvider(_SCRIPTED_PROVIDER, placed.reference) if scripted else None
        await asyncio.to_thread(seed_skills, skills_dir(), task.skills)
        runtime: Runtime = await open_runtime(
            with_servers(held_to_workspace(site_config(config) if task.site else config, task), task),
            {**login_env, **_ATTEMPT_ENV},
        )
        try:
            outcome: TaskOutcome = await _run_in(runtime, placed, model, stand_in, attempt, harness)
        finally:
            await close_runtime(runtime)
    log.info(
        "eval_attempt",
        task=task.id,
        attempt=attempt,
        model=model,
        passed=outcome.passed,
        status=outcome.status,
        cut=outcome.cut,
        steps=outcome.metrics.steps,
        seconds=outcome.seconds,
    )
    return outcome


async def play(
    config: AppConfig,
    login_env: Mapping[str, str],
    secrets: Mapping[str, str],
    model: str,
    tasks: Sequence[EvalTask],
    repeats: int,
    harness: GivenText,
    scripted: bool,
) -> AsyncGenerator[TaskOutcome]:
    """Every task ``repeats`` times against ``model``, one attempt at a time, each outcome as soon as it is known, each
    run taking ``harness`` (:data:`NO_HARNESS` for Dot's own text alone). The whole catalog is played once before it is
    played again. ``scripted`` plays each task's reference through the scripted model instead of ``model``, as
    ``dotx eval check`` does: what a probe of the harness is checked with (item 391)."""
    for attempt in range(1, repeats + 1):
        for task in tasks:
            yield await _attempt(task, config, login_env, secrets, model, scripted, attempt, harness)


async def replay_reference(task: EvalTask, config: AppConfig, login_env: Mapping[str, str]) -> TaskOutcome:
    """The reference solution of a task, played by a scripted model through the real loop and tools; in a goal-mode
    task the scripted model is the review model too, so the reference carries the verdict of the goal check."""
    replay: AppConfig = (
        config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})
        if task.goal_mode
        else config
    )
    return await _attempt(task, replay, login_env, {}, SCRIPTED_MODEL, True, 1, NO_HARNESS)


async def do_nothing_passes(task: EvalTask, env: Mapping[str, str]) -> bool:
    """Whether a run that touches nothing and says it is done passes the task. A sound task is not passed that way. Such
    a run leaves no events, so no check of what it read or called holds."""
    with scratch_home() as workspace:
        await asyncio.to_thread(seed_workspace, workspace, task.files)
        checks: tuple[CheckOutcome, ...] = await evaluate_checks(
            (*task.checks, *task.injection_checks), workspace, _NOTHING_ANSWER, env
        )
    return task.ending == "done" and all(check.passed for check in checks)
