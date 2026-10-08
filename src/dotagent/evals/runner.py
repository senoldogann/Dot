"""Playing eval tasks: each attempt is an unattended run in a home and a workspace of its own, judged by the checks of the
task, and everything it does is counted.

The process points at a throw-away Dot home for the length of an attempt (``DOT_HOME``), so the database, the
workspace and the screenshots of the user are never touched. The API keys the model needs are looked up before the
switch, because the home that holds the user's ``.env`` file is not the current one then, and are passed on by name.
"""

import asyncio
import os
import tempfile
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, PluginsConfig, SkillsConfig
from dotagent.core.errors import EvalError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.checks import evaluate_checks, workspace_path
from dotagent.evals.metrics import run_metrics
from dotagent.evals.results import CheckOutcome, Cut, RunMetrics, TaskOutcome
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import EvalTask, FileSeed
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatProvider
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.secrets import resolve_secret
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
    servers, skills and plugins of the user, the Mac desktop, the embeddings of the memory and the lessons, which would
    carry what one attempt learnt into the next)."""
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"lessons": "off"}),
            "mcp": [],
            "skills": SkillsConfig(dirs=[]),
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
    return tuple(names)


def required_secret_names(config: AppConfig, models: Sequence[str]) -> tuple[str, ...]:
    """The names, among those of :func:`secret_names`, of the keys without which a model cannot be reached (a local
    Ollama server needs none)."""
    providers: set[str] = {parse_model_ref(model).provider for model in models}
    names: list[str] = []
    if "openai" in providers:
        names.append(config.providers.openai.api_key_env)
    if "opencode_go" in providers:
        names.append(config.providers.opencode_go.api_key_env)
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


def seed_workspace(workspace: Path, files: Sequence[FileSeed]) -> None:
    """Write the files a task starts from. Blocking."""
    for seed in files:
        path: Path = workspace_path(workspace, seed.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(seed.content, encoding="utf-8")


def judge(
    task: EvalTask,
    attempt: int,
    record: RunRecord,
    cut: Cut,
    checks: Sequence[CheckOutcome],
    metrics: RunMetrics,
    seconds: float,
) -> TaskOutcome:
    """The outcome of an attempt: it passed when the run ended the way the task asks and every check held."""
    return TaskOutcome(
        task_id=task.id,
        category=task.category,
        attempt=attempt,
        passed=record.status == task.ending and all(check.passed for check in checks),
        status=record.status,
        cut=cut,
        answer=truncate_middle(record.result, _ANSWER_CHARS),
        checks=tuple(checks),
        metrics=metrics,
        seconds=round(seconds, 2),
    )


def _count(data: JsonObject, key: str, event: EventRecord) -> int:
    value: object = data.get(key)
    if not isinstance(value, int):
        raise EvalError("an event lacks a number the harness counts", {"kind": event.kind, "key": key})
    return value


async def _decline(runtime: Runtime, event: EventRecord) -> None:
    """Answer a question of the policy, or of the agent, with a refusal: nobody is there."""
    approval_id: object = event.data.get("approval_id")
    if not isinstance(approval_id, str):
        raise EvalError("an approval request without an id", {"run_id": event.run_id})
    await runtime.approvals.resolve(approval_id, False, _DECIDER, _DENIAL_NOTE)


def _stop(manager: RunManager, run_id: str) -> None:
    if run_id in manager.active_run_ids():
        manager.cancel(run_id)


async def _supervise(
    runtime: Runtime, manager: RunManager, subscription: Subscription, run_id: str, task: EvalTask
) -> Cut:
    """Watch the run until it is over: refuse every question, and cut the run when it takes longer or spends more
    tokens than the task allows. Returns why it was cut, or ``none``. The subscription is opened before the run starts,
    so that no event is missed."""
    spent: int = 0
    cut: Cut = "none"
    try:
        async with asyncio.timeout(task.time_limit_s):
            while True:
                event: EventRecord = await subscription.queue.get()
                if event.kind == "approval_requested":
                    await _decline(runtime, event)
                elif event.kind == "assistant":
                    spent += _count(event.data, "input_tokens", event) + _count(event.data, "output_tokens", event)
                    if spent > task.token_budget and cut == "none":
                        cut = "token_budget"
                        _stop(manager, run_id)
                elif event.kind == "run_finished" and event.run_id == run_id:
                    return cut
    except TimeoutError:
        _stop(manager, run_id)
        return "time_limit"


async def _run_in(
    runtime: Runtime, task: EvalTask, model: str, stand_in: ChatProvider | None, attempt: int
) -> TaskOutcome:
    """One attempt in an open runtime: seed the workspace, run the goal, judge what is left."""
    if stand_in is not None:
        await runtime.router.register(parse_model_ref(model).provider, stand_in)
    await asyncio.to_thread(seed_workspace, runtime.workspace, task.files)
    manager: RunManager = RunManager(runtime)
    started: float = time.monotonic()
    try:
        async with runtime.bus.subscribe(None) as subscription:
            run: RunRecord = await manager.start(
                task.goal, task.mode, model, None, EVAL_SOURCE, None, None, "ask", None, ()
            )
            cut: Cut = await _supervise(runtime, manager, subscription, run.id, task)
        record: RunRecord = await manager.wait(run.id)
    finally:
        await manager.shutdown()
    seconds: float = time.monotonic() - started
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
    checks: tuple[CheckOutcome, ...] = await evaluate_checks(
        task.checks, runtime.workspace, record.result, runtime.shell_env
    )
    return judge(task, attempt, record, cut, checks, run_metrics(record, events), seconds)


async def _attempt(
    task: EvalTask,
    config: AppConfig,
    login_env: Mapping[str, str],
    secrets: Mapping[str, str],
    model: str,
    stand_in: ChatProvider | None,
    attempt: int,
) -> TaskOutcome:
    """One attempt in a throw-away home that is removed afterwards."""
    with scratch_home() as home, eval_environment(home, secrets):
        runtime: Runtime = await open_runtime(config, {**login_env, **_ATTEMPT_ENV})
        try:
            outcome: TaskOutcome = await _run_in(runtime, task, model, stand_in, attempt)
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
) -> AsyncIterator[TaskOutcome]:
    """Every task ``repeats`` times against ``model``, one attempt at a time, each outcome as soon as it is known. The
    whole catalog is played once before it is played again."""
    for attempt in range(1, repeats + 1):
        for task in tasks:
            yield await _attempt(task, config, login_env, secrets, model, None, attempt)


async def replay_reference(task: EvalTask, config: AppConfig, login_env: Mapping[str, str]) -> TaskOutcome:
    """The reference solution of a task, played by a scripted model through the real loop and tools."""
    stand_in: ScriptedProvider = ScriptedProvider(_SCRIPTED_PROVIDER, task.reference)
    return await _attempt(task, config, login_env, {}, SCRIPTED_MODEL, stand_in, 1)


async def do_nothing_passes(task: EvalTask, env: Mapping[str, str]) -> bool:
    """Whether a run that touches nothing and says it is done passes the task. A sound task is not passed that way."""
    with scratch_home() as workspace:
        await asyncio.to_thread(seed_workspace, workspace, task.files)
        checks: tuple[CheckOutcome, ...] = await evaluate_checks(task.checks, workspace, _NOTHING_ANSWER, env)
    return task.ending == "done" and all(check.passed for check in checks)
