"""A run that reads a learned skill that may carry content third parties can author is tainted from the read (item
585, the harness audit's M4): the skill tool's result is marked untrusted, so the run's gate is tainted from there,
every later result of the run says so, and the lessons it draws are marked as those of a run that read the outside. A
skill its record says is clean, one the owner taught and a run that reads none leave a run as it was; a record older
than the mark counts as marked. A skill picked in the composer starts its run tainted, since its instructions are in
the run's first message, unless the owner trusts it.

No network and no model service is used: the scripted model plays the runs and answers as the review model too; the
home is a throw-away directory, where the skills are written.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.lessons import RunLessons, run_lessons
from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import Playbook, playbook_name, taught_name, trust_playbook, write_playbook
from dotagent.agent.review import LESSONS_TOOL, PlaybookDraft
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.skills.types import SKILL_TOOL
from dotagent.store.models import NO_PICKS, EventRecord, RunPicks, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Keep the line hello in draft.txt and read it back."
DRAFT: str = "draft.txt"
KIND: str = "Keep a line in a notes file"
LESSON: str = "read_file fails on a file that does not exist yet; create draft.txt with write_file first."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 500


@dataclass(frozen=True)
class Home:
    """A runtime in a throw-away home and its run manager."""

    runtime: Runtime
    manager: RunManager


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on, the lessons of a run left on it for the owner to decide, and
    the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "lessons": "propose"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    """A runtime in a throw-away home whose model plays ``turns``: one model call each, in order."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(turns)))
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


# A run that reads a file before it exists, writes it and reads it back: a call that failed and then worked, which is
# what the lessons of a run are drawn from.
STUMBLE: list[ScriptedTurn] = [
    _turn(ScriptedCall(tool="read_file", arguments={"path": DRAFT})),
    _turn(ScriptedCall(tool="write_file", arguments={"path": DRAFT, "content": "hello\n"})),
    _turn(ScriptedCall(tool="read_file", arguments={"path": DRAFT})),
    _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The file holds hello.", "success": True})),
]
LESSONS: ScriptedTurn = _turn(ScriptedCall(tool=LESSONS_TOOL, arguments={"lessons": [LESSON]}))


def _reading(name: str) -> list[ScriptedTurn]:
    """The turns of a run that reads the skill ``name``, stumbles, finishes and is asked for its lessons."""
    return [_turn(ScriptedCall(tool=SKILL_TOOL, arguments={"name": name})), *STUMBLE, LESSONS]


def _skill(*, tainted: bool | None, taught: bool = False) -> str:
    """Write a skill to the throw-away home's playbooks: as a run that did (``True``) or did not (``False``) read
    content third parties can author wrote it, as the owner taught it, or (``None``) as a record older than the mark has
    it. Returns its name."""
    name: str = taught_name(KIND) if taught else playbook_name(KIND)
    write_playbook(
        playbooks_dir(),
        Playbook(
            name=name,
            kind=KIND,
            host="this Mac",
            draft=PlaybookDraft(
                kind=KIND,
                description="Write a line to a notes file and read it back to check it.",
                when="A goal that asks to keep a line in a file.",
                steps=["Write <line> to <file> with write_file.", "Read <file> back and check the line is there."],
                parameters=["<line>: the text to keep"],
            ),
            run_id="" if taught else "run-earlier",
            agent_id=None,
            goal=GOAL,
            written_at=time.time(),
            acting_steps=3,
            tainted=bool(tainted),
            taught=taught,
        ),
    )
    if tainted is None:
        record: Path = playbooks_dir() / name / "playbook.json"
        written: dict[str, object] = json.loads(record.read_text(encoding="utf-8"))
        record.write_text(json.dumps({**written, "tainted": None, "tainted_runs": []}), encoding="utf-8")
    return name


async def _played(home: Home, picks: RunPicks = NO_PICKS) -> RunRecord:
    """``GOAL`` as an unattended task run, sent with ``picks``, once it ended done and its lessons are drawn."""
    run: RunRecord = await home.manager.start_picked(
        GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask", False, (), picks
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.learned(run.id)
    assert record.status == "done", record
    return record


async def _results(home: Home, run: RunRecord) -> list[EventRecord]:
    """The ``tool_result`` events of ``run``, oldest first."""
    events: list[EventRecord] = await home.runtime.store.list_events(run.id, 0, _MAX_EVENTS)
    return [event for event in events if event.kind == "tool_result"]


async def _read_outside(home: Home, run: RunRecord) -> bool:
    """Whether the store says ``run`` read content third parties can author, as a learned skill's work reads it."""
    return await home.runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))


async def test_a_run_that_reads_a_tainted_skill_is_tainted_from_the_read_and_its_lessons_are_marked(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, _reading(playbook_name(KIND))) as home:
        _skill(tainted=True)
        run: RunRecord = await _played(home)
        results: list[EventRecord] = await _results(home, run)
        read: EventRecord = next(event for event in results if event.data["tool"] == SKILL_TOOL)
        later: list[EventRecord] = results[results.index(read) + 1 :]
        # The read taints the run, and what the run does after it is the work of a tainted run.
        assert read.data["tainted"] is True and later and all(event.data["tainted"] is True for event in later)
        assert await _read_outside(home, run) is True
        lessons: RunLessons | None = await run_lessons(home.runtime.store, run.id)
        assert lessons is not None and lessons.tainted is True and lessons.error is None
        assert [(view.text, view.state) for view in lessons.lessons] == [(LESSON, "proposed")]
        proposed: EventRecord | None = await home.runtime.store.last_event(run.id, "lessons_proposed")
        assert proposed is not None and proposed.data["tainted"] is True


@pytest.mark.parametrize(
    ("tainted", "taught", "marked"),
    [
        pytest.param(False, False, False, id="a skill whose record says its run read nothing outside"),
        pytest.param(False, True, False, id="a skill the owner taught"),
        pytest.param(None, False, True, id="a record older than the mark, offered as if a run had read outside"),
    ],
)
async def test_only_a_skill_that_may_carry_outside_content_taints_the_run_that_reads_it(
    login_env: dict[str, str], tainted: bool | None, taught: bool, marked: bool
) -> None:
    name: str = taught_name(KIND) if taught else playbook_name(KIND)
    async with _home(login_env, _reading(name)) as home:
        assert _skill(tainted=tainted, taught=taught) == name
        run: RunRecord = await _played(home)
        results: list[EventRecord] = await _results(home, run)
        read: EventRecord = next(event for event in results if event.data["tool"] == SKILL_TOOL)
        assert (read.data.get("tainted") is True) is marked
        assert (await _read_outside(home, run)) is marked
        lessons: RunLessons | None = await run_lessons(home.runtime.store, run.id)
        assert lessons is not None and lessons.tainted is marked
        assert [view.state for view in lessons.lessons] == ["proposed"]


async def test_a_run_that_reads_no_skill_is_not_tainted_by_the_one_that_is_there(login_env: dict[str, str]) -> None:
    async with _home(login_env, [*STUMBLE, LESSONS]) as home:
        _skill(tainted=True)
        run: RunRecord = await _played(home)
        assert not any(event.data.get("tainted") is True for event in await _results(home, run))
        assert await _read_outside(home, run) is False
        lessons: RunLessons | None = await run_lessons(home.runtime.store, run.id)
        assert lessons is not None and lessons.tainted is False


async def test_a_skill_picked_in_the_composer_starts_the_run_tainted_unless_the_owner_trusts_it(
    login_env: dict[str, str],
) -> None:
    """The picked skill's instructions are in the run's first message, so the run starts as one that read a page does:
    its first event says so, and so do its lessons. The same skill, trusted by the owner, starts the next run as any."""
    async with _home(login_env, [*STUMBLE, LESSONS, *STUMBLE, LESSONS]) as home:
        name: str = _skill(tainted=True)
        picked: RunPicks = RunPicks(plugins=(), skills=(name,), plan_first=False)
        first: RunRecord = await _played(home, picked)
        queued: EventRecord | None = await home.runtime.store.last_event(first.id, "run_queued")
        assert queued is not None and queued.data["tainted"] is True
        lessons: RunLessons | None = await run_lessons(home.runtime.store, first.id)
        assert lessons is not None and lessons.tainted is True
        trust_playbook(playbooks_dir(), name)
        second: RunRecord = await _played(home, picked)
        queued = await home.runtime.store.last_event(second.id, "run_queued")
        assert queued is not None and queued.data["tainted"] is False
