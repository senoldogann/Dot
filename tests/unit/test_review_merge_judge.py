"""The merge judge of the nightly reflection (item 584), asked through the router of a scripted model: it asks again
for a verdict it cannot use (a merge of one note, a note kept that is not merged, the same note twice, a note or a group
that was not shown) and uses the one that follows, shows the notes fenced as data under a code of its own, and takes a
group it merges none of as a verdict. No model service is used."""

import re

import pytest

from dotagent.agent.review import MERGE_TOOL, MergeJudge, MergeJudgement, MergeRequest, NoteBrief, merge_judge_for
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply

MODEL: str = "ollama:scripted"
_GROUP: tuple[NoteBrief, ...] = (
    NoteBrief(text="pdftotext needs -layout.", day="2026-10-01"),
    NoteBrief(text="Use -layout with pdftotext for tables.", day="2026-10-05"),
    NoteBrief(text="pdftotext keeps the columns of a table with the -layout flag.", day="2026-10-09"),
)


class _Recording:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._script: ScriptedProvider = ScriptedProvider("ollama", turns)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


async def _ignore(call: ModelCall) -> None:
    return None


async def _router(*turns: ScriptedTurn) -> tuple[ModelRouter, _Recording]:
    provider: _Recording = _Recording(list(turns))
    router: ModelRouter = ModelRouter(load_default_config(), _ignore, {})
    await router.register("ollama", provider)
    return router, provider


def _answer(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _merged(group: int, merge: list[int], keep: int) -> ScriptedTurn:
    return _answer(MERGE_TOOL, verdicts=[{"group": group, "merge": merge, "keep": keep, "reason": "the same thing"}])


async def test_a_merge_verdict_that_cannot_be_used_is_asked_for_again_and_the_one_that_follows_is_used() -> None:
    router, provider = await _router(_merged(1, [1], 1), _merged(1, [1, 3], 3))
    judge: MergeJudge = merge_judge_for(router, MODEL, (), 30.0, "reflection:test")
    judgement: MergeJudgement = await judge(MergeRequest(groups=(_GROUP,)))
    assert [(verdict.group, verdict.merge, verdict.keep) for verdict in judgement.verdicts] == [(1, [1, 3], 3)]
    # The notes are fenced as data under a code of their own, and the second question says why the first answer failed.
    first, second = (str(request.messages[-1].content) for request in provider.requests)
    assert re.search(r"<<GROUP 1 ([0-9a-f]{12})>>\n1\. \(2026-10-01\) pdftotext needs -layout\.", first)
    assert "not usable" in second and "two different notes" in second


@pytest.mark.parametrize(
    ("group", "merge", "keep"),
    [
        pytest.param(1, [1, 2], 3, id="a note kept that is not merged"),
        pytest.param(1, [2, 2], 2, id="the same note twice"),
        pytest.param(1, [1, 4], 1, id="a note of no number"),
        pytest.param(1, [], 1, id="nothing merged, yet one kept"),
        pytest.param(1, [1, 2], 0, id="two merged, none kept"),
        pytest.param(2, [1, 2], 1, id="a group that was not shown"),
    ],
)
async def test_a_merge_verdict_that_stays_unusable_is_refused(group: int, merge: list[int], keep: int) -> None:
    router, _ = await _router(_merged(group, merge, keep), _merged(group, merge, keep))
    judge: MergeJudge = merge_judge_for(router, MODEL, (), 30.0, "reflection:test")
    with pytest.raises(ProviderProtocolError):
        await judge(MergeRequest(groups=(_GROUP,)))


async def test_a_group_the_judge_merges_none_of_is_a_verdict_and_not_an_error() -> None:
    router, _ = await _router(_merged(1, [], 0))
    judge: MergeJudge = merge_judge_for(router, MODEL, (), 30.0, "reflection:test")
    judgement: MergeJudgement = await judge(MergeRequest(groups=(_GROUP,)))
    assert [(verdict.merge, verdict.keep) for verdict in judgement.verdicts] == [([], 0)]
