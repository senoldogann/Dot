"""Where the read of a learned skill is dated (item 585, round 3): a skill rests when no run read it for
``[agent] skill_rest_days``, and the date was written only by the count of a task run of its own that ended done or
failed, so a skill that only chats, helper runs or runs that were stopped read rested, and left every prompt, while
they went on reading it. The read dates the skill where it happens (the ``skill`` tool, a skill picked in the
composer), whatever the run's mode, parent or end; the counts of uses, which judge how runs ended, are as they were.

No network and no model service is used: the scripted model plays the runs, and the home is a throw-away directory.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import (
    Playbook,
    PlaybookMeta,
    playbook_name,
    read_meta,
    skill_resting,
    taught_name,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.skills.types import SKILL_TOOL
from dotagent.store.models import NO_PICKS, RunMode, RunPicks, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Find the price of the blue kettle."
KIND: str = "Find a product's price"
OTHER_KIND: str = "Fill the old expense form"
DAY_S: float = 86400.0
REST_DAYS: int = 60
# A run of the owner's own, not an eval: a task run of this source gives its skill work its count.
SOURCE: str = "test"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


class Hanging:
    """The scripted model, which answers its first ``answers`` calls and leaves every later one without an answer;
    ``hanging`` is set once one waits."""

    def __init__(self, script: ScriptedProvider, answers: int) -> None:
        self._script: ScriptedProvider = script
        self._answers: int = answers
        self.hanging: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self._answers > 0:
            self._answers -= 1
            return await self._script.complete(request, sink)
        self.hanging.set()
        await asyncio.Event().wait()
        raise AssertionError("an event nobody sets was set")

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@dataclass(frozen=True)
class Home:
    """A runtime in a throw-away home, its run manager and the model that plays its runs."""

    runtime: Runtime
    manager: RunManager
    model: Hanging


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on and the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "skill_rest_days": REST_DAYS}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


@asynccontextmanager
async def _home(
    login_env: dict[str, str], turns: Sequence[ScriptedTurn], answers: int | None = None
) -> AsyncIterator[Home]:
    """A runtime in a throw-away home whose model plays ``turns``: one model call each, in order; with ``answers`` it
    answers that many and leaves the next call hanging."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            model: Hanging = Hanging(
                ScriptedProvider(_PROVIDER, list(turns)), len(turns) if answers is None else answers
            )
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, model=model)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


READ: ScriptedTurn = _turn(ScriptedCall(tool=SKILL_TOOL, arguments={"name": playbook_name(KIND)}))
FINISH: ScriptedTurn = _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "It costs 12.", "success": True}))
# A chat turn ends with a plain reply.
REPLY: ScriptedTurn = ScriptedTurn(text="It costs 12.", calls=())


def _skill(kind: str, written_days_ago: float, taught: bool = False) -> str:
    """A skill in the throw-away home's playbooks, written ``written_days_ago`` days ago (a learned one, or one the
    owner taught): one that no run has read."""
    name: str = taught_name(kind) if taught else playbook_name(kind)
    write_playbook(
        playbooks_dir(),
        Playbook(
            name=name,
            kind=kind,
            host="shop.example.com",
            draft=PlaybookDraft(
                kind=kind,
                description=f"{kind}: what is done and where.",
                when="A goal that asks for it.",
                steps=["Search for <product>.", "Read the price of the first result."],
                parameters=["<product>: what to look for"],
            ),
            run_id="" if taught else "run-earlier",
            agent_id=None,
            goal=GOAL,
            written_at=time.time() - written_days_ago * DAY_S,
            acting_steps=3,
            tainted=False,
            taught=taught,
        ),
    )
    return name


async def _ended(home: Home, mode: RunMode, picks: RunPicks = NO_PICKS) -> RunRecord:
    """``GOAL`` as a run of ``mode``, sent with ``picks``, once it ended and its skill work is done."""
    run: RunRecord = await home.manager.start_picked(
        GOAL, mode, SCRIPTED_MODEL, None, SOURCE, None, None, "standard", None, "ask", False, (), picks
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.distilled(run.id)
    return record


def _dated(name: str, started: float) -> PlaybookMeta:
    """The record of the skill ``name``, which must say a read since ``started``: the read of a run that is over."""
    meta: PlaybookMeta = read_meta(playbooks_dir() / name)
    assert meta.last_read_at is not None and started - 1.0 <= meta.last_read_at <= time.time() + 1.0, meta.last_read_at
    return meta


def _counts(meta: PlaybookMeta) -> tuple[int, int, int]:
    return meta.uses, meta.failures, meta.failing


@pytest.mark.parametrize("taught", [False, True], ids=["a learned skill", "a skill the owner taught"])
async def test_a_chat_dates_the_skill_it_reads_and_counts_no_use(login_env: dict[str, str], taught: bool) -> None:
    read: ScriptedTurn = _turn(
        ScriptedCall(tool=SKILL_TOOL, arguments={"name": taught_name(KIND) if taught else playbook_name(KIND)})
    )
    async with _home(login_env, [read, REPLY]) as home:
        name: str = _skill(KIND, written_days_ago=59, taught=taught)
        started: float = time.time()
        run: RunRecord = await _ended(home, "chat")
        assert (run.status, run.mode) == ("done", "chat")
        # The read dated it; a chat is no task run of its own, so no use is counted: the counts are as they were.
        assert _counts(_dated(name, started)) == (0, 0, 0)


async def test_a_task_run_that_ends_done_dates_the_skill_it_reads_and_counts_its_use_as_before(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, [READ, FINISH]) as home:
        name: str = _skill(KIND, written_days_ago=59)
        started: float = time.time()
        run: RunRecord = await _ended(home, "task")
        assert run.status == "done"
        assert _counts(_dated(name, started)) == (1, 0, 0)


async def test_a_helper_run_dates_the_skill_it_reads_and_the_run_that_delegated_it_counts_nothing(
    login_env: dict[str, str],
) -> None:
    """A run another run delegated is no run the skill work looks at (``parent_run_id`` is set), so its reads were never
    dated; the run that delegated it read nothing itself, so it counts nothing."""
    delegating: ScriptedTurn = _turn(
        ScriptedCall(tool="delegate", arguments={"goal": "Read the skill for the kettle and tell me its steps."})
    )
    # The model is asked in the order the runs ask it: the delegation, the helper's read and its end, the run's end.
    async with _home(login_env, [delegating, READ, FINISH, FINISH]) as home:
        name: str = _skill(KIND, written_days_ago=59)
        started: float = time.time()
        run: RunRecord = await _ended(home, "task")
        assert run.status == "done"
        (helper,) = await home.runtime.store.list_child_runs(run.id)
        assert (helper.status, helper.parent_run_id) == ("done", run.id)
        assert _counts(_dated(name, started)) == (0, 0, 0)


async def test_a_run_that_is_stopped_after_the_read_has_dated_the_skill_it_read(login_env: dict[str, str]) -> None:
    async with _home(login_env, [READ], answers=1) as home:
        name: str = _skill(KIND, written_days_ago=59)
        started: float = time.time()
        run: RunRecord = await home.manager.start(
            GOAL, "task", SCRIPTED_MODEL, None, SOURCE, None, None, "standard", None, "ask", False, ()
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            await home.model.hanging.wait()
            home.manager.cancel(run.id)
            record: RunRecord = await home.manager.wait(run.id)
            await home.manager.distilled(run.id)
        assert record.status == "cancelled"
        # A run that is cancelled is not counted (its skill work only looks at runs that ended done or failed).
        assert _counts(_dated(name, started)) == (0, 0, 0)


async def test_a_skill_picked_in_the_composer_is_dated_when_the_run_starts_with_it(login_env: dict[str, str]) -> None:
    async with _home(login_env, [FINISH]) as home:
        name: str = _skill(KIND, written_days_ago=59)
        started: float = time.time()
        picked: RunPicks = RunPicks(plugins=(), skills=(name,), plan_first=False)
        run: RunRecord = await _ended(home, "task", picked)
        assert run.status == "done"
        # The run never read it with the tool, so no use is counted; its instructions were in the first message.
        assert _counts(_dated(name, started)) == (0, 0, 0)


async def test_a_skill_that_only_chats_read_does_not_rest_when_the_days_pass_and_one_nobody_read_does(
    login_env: dict[str, str],
) -> None:
    """Both skills were written 59 days ago, so each rests in a day when nothing reads it. A chat reads one: its days
    are counted from the read, so it is awake for the sixty days after it, and the other, which nothing read, rests."""
    async with _home(login_env, [READ, REPLY]) as home:
        read: str = _skill(KIND, written_days_ago=59)
        unread: str = _skill(OTHER_KIND, written_days_ago=59)
        now: float = time.time()
        assert (await _ended(home, "chat")).status == "done"
        for days, reads, nobody in ((5, False, True), (59, False, True), (61, True, True)):
            then: float = now + days * DAY_S
            assert skill_resting(read_meta(playbooks_dir() / read), then, REST_DAYS) is reads, days
            assert skill_resting(read_meta(playbooks_dir() / unread), then, REST_DAYS) is nobody, days
