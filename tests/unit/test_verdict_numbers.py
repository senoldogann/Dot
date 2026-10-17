"""Verdicts on several things at once name the thing each is about (item 350): the judge of outdated notes gets a
verdict that names a pair it was not shown asked for again, and the one that comes back is matched to its pair by
number. Before, the verdicts were matched to the pairs in turn, so one left out moved the next one's to another note."""

from dotagent.agent.review import (
    OUTDATED_TOOL,
    NotePair,
    OutdatedJudge,
    OutdatedJudgement,
    OutdatedRequest,
    outdated_judge_for,
)
from dotagent.config import load_default_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter

_PAIRS: tuple[NotePair, ...] = (
    NotePair(
        older="The invoices are in ~/Documents.",
        older_day="2026-10-01",
        newer="The printer is new.",
        newer_day="2026-10-16",
    ),
    NotePair(older="The car is blue.", older_day="2026-10-02", newer="The car is red now.", newer_day="2026-10-16"),
)


def _judged(*verdicts: tuple[int, bool]) -> ScriptedTurn:
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=OUTDATED_TOOL,
                arguments={
                    "verdicts": [
                        {"pair": pair, "outdated": outdated, "reason": "a reason"} for pair, outdated in verdicts
                    ]
                },
            ),
        ),
    )


async def _ignore(call: ModelCall) -> None:
    return None


async def test_a_verdict_on_a_pair_not_shown_is_asked_for_again_and_each_names_its_pair() -> None:
    # The first answer names a third pair of two; the second judges the second pair alone.
    provider: ScriptedProvider = ScriptedProvider(
        "ollama",
        (
            _judged((1, False), (3, True)),
            _judged(
                (2, True),
            ),
        ),
    )
    router: ModelRouter = ModelRouter(load_default_config(), _ignore, {})
    await router.register("ollama", provider)
    judge: OutdatedJudge = outdated_judge_for(router, "ollama:scripted", (), 30.0, "reflection:test")
    judgement: OutdatedJudgement = await judge(OutdatedRequest(pairs=_PAIRS))
    assert [(verdict.pair, verdict.outdated) for verdict in judgement.verdicts] == [(2, True)]
