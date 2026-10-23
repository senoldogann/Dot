"""A night of the reflection with the learned skills (item 585), in the real runtime on the scripted model: the skills
whose kinds are close in meaning are put to the review model, the pair it says is one procedure gets a proposal that
holds the merged steps a second call wrote, and no skill is changed until the owner says yes; the pair that waits is
not asked about again the next night; a skill a run read before the dates were kept is given one so that it does not
rest at once. The summary of the night says what was proposed.

No model service is used: the scripted model plays the review model, a stub the embedding service.
"""

import dataclasses
import math
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import Playbook, PlaybookMeta, playbook_name, read_meta, save_meta, write_playbook
from dotagent.agent.reflection import last_reflection
from dotagent.agent.reflection_record import summary_line
from dotagent.agent.review import OVERLAP_TOOL, REVISE_TOOL, PlaybookDraft
from dotagent.agent.skill_proposals import SkillProposal, accept_proposal, list_proposals
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.memory.ranking import Vector
from dotagent.memory.service import MemoryIndex
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.skills.types import SKILL_FILE
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

DAY_S: float = 86400.0
FIRST: str = "Manage a cold email campaign"
SECOND: str = "Track cold email progress"
LONE: str = "Check the weather in Ankara"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _unit(angle: float) -> Vector:
    return (math.cos(angle), math.sin(angle))


class _Embedder:
    """The embedding service: each text where the test placed it, any other far away."""

    def __init__(self) -> None:
        self.places: dict[str, Vector] = {}

    @property
    def model(self) -> str:
        return "stub"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self.places.setdefault(text, _unit(1.0 + 0.7 * len(self.places))) for text in texts]

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager
    embedder: _Embedder


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    """An eval run's configuration with learned skills on and the scripted model as the review model, whose runtime
    remembers by a stub embedding service (the eval configuration has the embeddings off)."""
    with scratch_home() as home, eval_environment(home, {}):
        config: AppConfig = eval_config(load_default_config())
        config = config.model_copy(
            update={
                "agent": config.agent.model_copy(update={"playbooks": True}),
                "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            }
        )
        opened: Runtime = await open_runtime(config, login_env)
        try:
            await opened.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            embedder: _Embedder = _Embedder()
            runtime: Runtime = dataclasses.replace(
                opened, memory=MemoryIndex(store=opened.store, embedder=embedder, config=opened.config.memory)
            )
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, embedder=embedder)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(opened)


def _skill(kind: str, run: str, *steps: str) -> str:
    """A learned skill of ``kind`` written by the run ``run`` with ``steps``, in the throw-away home. Returns its
    name."""
    name: str = playbook_name(kind)
    write_playbook(
        playbooks_dir(),
        Playbook(
            name=name,
            kind=kind,
            host="mail.example.com",
            draft=PlaybookDraft(
                kind=kind,
                description=f"{kind}: what is done and where.",
                when=f"A goal that asks for {kind.lower()}.",
                steps=list(steps),
                parameters=["<list>: the contacts"],
            ),
            run_id=run,
            agent_id=None,
            goal=f"Goal of {run}",
            written_at=time.time(),
            acting_steps=len(steps),
            tainted=False,
        ),
    )
    return name


def _legacy(name: str) -> None:
    """Make the skill ``name`` look as one written before the dates of its last read were kept: its record counts uses
    and has no date."""
    folder: Path = playbooks_dir() / name
    save_meta(folder, read_meta(folder).model_copy(update={"uses": 2}))


def _overlap(*verdicts: tuple[int, bool, int]) -> ScriptedTurn:
    """The review model's verdicts on the pairs: ``(pair, one procedure, the skill kept)``."""
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=OVERLAP_TOOL,
                arguments={
                    "verdicts": [
                        {"pair": pair, "one": one, "keep": keep, "reason": "the same procedure"}
                        for pair, one, keep in verdicts
                    ]
                },
            ),
        ),
    )


def _revision(kind: str, *steps: str) -> ScriptedTurn:
    """The review model's skill as it writes it after a merge."""
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=REVISE_TOOL,
                arguments={
                    "kind": kind,
                    "description": f"{kind}: what is done and where.",
                    "when": "A goal that asks for a cold email campaign.",
                    "steps": list(steps),
                    "parameters": ["<list>: the contacts"],
                },
            ),
        ),
    )


async def test_a_night_proposes_the_merge_of_two_skills_that_say_the_same_and_changes_none(
    login_env: dict[str, str],
) -> None:
    merged: tuple[str, ...] = ("Open the inbox.", "Send the first mails.", "Count the replies.")
    turns: list[ScriptedTurn] = [_overlap((1, True, 1)), _revision("Run a cold email campaign", *merged)]
    async with _home(login_env, turns) as home:
        root: Path = playbooks_dir()
        one: str = _skill(FIRST, "run-1", "Open the inbox.", "Send the first mails.")
        two: str = _skill(SECOND, "run-2", "Open the inbox.", "Count the replies.")
        lone: str = _skill(LONE, "run-3", "Open the weather page.", "Read the forecast.")
        # A skill a run read before the dates were kept: its record counts uses and has none.
        _legacy(lone)
        home.embedder.places.update({FIRST: _unit(0.0), SECOND: _unit(0.2), LONE: _unit(2.5)})
        before: dict[str, str] = {name: (root / name / SKILL_FILE).read_text(encoding="utf-8") for name in (one, two)}
        now: float = time.time()

        reflection = await home.manager.reflect(now)

        assert reflection.skills_proposed == 1 and reflection.merged == 0
        assert (
            summary_line(reflection) == "Reflected on 0 runs: 0 lessons, 0 merged, 0 retired, 1 skill merge proposed."
        )
        assert await last_reflection(home.runtime.store) == reflection
        (proposal,) = list_proposals(root)
        assert (proposal.kind, proposal.names, proposal.draft.steps) == ("merge", (one, two), list(merged))
        assert proposal.draft.kind == "Run a cold email campaign" and proposal.tainted is False
        # No skill was changed: the files, the versions and the rest are as they were.
        assert {name: (root / name / SKILL_FILE).read_text(encoding="utf-8") for name in (one, two)} == before
        for name in (one, two):
            held: PlaybookMeta = read_meta(root / name)
            assert (held.version, held.rested_at, held.merged_into) == (1, None, None)
        # The skill that counted uses with no date of its last read was given one, so that it does not rest at once.
        assert read_meta(root / lone).last_read_at == pytest.approx(now, abs=5.0)

        # The next night the pair that waits is not asked about again: the scripted model has no turn left to give.
        again = await home.manager.reflect(now + DAY_S)
        assert again.skills_proposed == 0 and list_proposals(root) == (proposal,)

        # The owner's yes writes what they were shown, as a new version of the first skill, and puts the other to rest.
        meta: PlaybookMeta = accept_proposal(root, proposal.id, now + 2 * DAY_S)
        assert (meta.version, meta.kind) == (2, "Run a cold email campaign")
        assert read_meta(root / two).merged_into == one and list_proposals(root) == ()
        assert isinstance(proposal, SkillProposal)
