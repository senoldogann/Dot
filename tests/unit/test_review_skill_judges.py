"""The review model's judgements about learned skills (item 585), asked through the router of a scripted model: the
overlap judge asks again for a verdict it cannot use (a pair judged one procedure that keeps neither skill, a pair
judged two that keeps one, a pair that was not shown) and uses the one that follows; the reviser is shown the owner's
words in a fence of their own only when it changes a skill as they say; the skill writer's answer without its
judgement of whether the steps recur is asked for again. No model service is used."""

import re

import pytest

from dotagent.agent.review import (
    _REVISE_MERGE,
    _REVISE_REWRITE,
    OVERLAP_TOOL,
    PLAYBOOK_TOOL,
    REVISE_TOOL,
    OverlapJudge,
    OverlapJudgement,
    OverlapRequest,
    PlaybookDraft,
    PlaybookReply,
    PlaybookRequest,
    ReviseRequest,
    SkillBrief,
    SkillReviser,
    overlap_judge_for,
    playbook_writer_for,
    skill_reviser_for,
)
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply

MODEL: str = "ollama:scripted"
_FIRST: SkillBrief = SkillBrief(
    kind="Write the weekly report",
    description="Write the report from the tracker.",
    when="A goal that asks for the weekly report.",
    steps=("Open the tracker.", "Write it."),
    parameters=("<week>: the week",),
)
_SECOND: SkillBrief = SkillBrief(
    kind="Draft the weekly status",
    description="Draft the status from the tracker.",
    when="A goal that asks for the weekly status.",
    steps=("Open the tracker.", "Draft it.", "Send it."),
    parameters=(),
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


def _overlap(pair: int, one: bool, keep: int) -> ScriptedTurn:
    return _answer(OVERLAP_TOOL, verdicts=[{"pair": pair, "one": one, "keep": keep, "reason": "one procedure"}])


async def test_a_pair_judged_one_procedure_must_keep_one_of_the_two_and_a_pair_that_is_not_keeps_none() -> None:
    request: OverlapRequest = OverlapRequest(pairs=((_FIRST, _SECOND),))
    router, provider = await _router(_overlap(1, True, 0), _overlap(1, True, 2))
    judge: OverlapJudge = overlap_judge_for(router, MODEL, (), 30.0, "reflection:test")
    judgement: OverlapJudgement = await judge(request)
    assert [(verdict.pair, verdict.one, verdict.keep) for verdict in judgement.verdicts] == [(1, True, 2)]
    prompt: str = str(provider.requests[0].messages[-1].content)
    assert re.search(r"<<PAIR 1 [0-9a-f]{12}>>\nSkill 1:\nKind of work: Write the weekly report", prompt)
    assert "Skill 2:\nKind of work: Draft the weekly status" in prompt and "3. Send it." in prompt
    for one, keep, other_pair in ((True, 0, 1), (False, 1, 1), (True, 1, 2)):
        refused_router, _ = await _router(_overlap(other_pair, one, keep), _overlap(other_pair, one, keep))
        with pytest.raises(ProviderProtocolError):
            await overlap_judge_for(refused_router, MODEL, (), 30.0, "reflection:test")(request)
    # Not one procedure, none kept: a verdict.
    router, _ = await _router(_overlap(1, False, 0))
    judgement = await overlap_judge_for(router, MODEL, (), 30.0, "reflection:test")(request)
    assert [(verdict.one, verdict.keep) for verdict in judgement.verdicts] == [(False, 0)]


def _draft_turn(*steps: str) -> ScriptedTurn:
    return _answer(
        REVISE_TOOL,
        kind="Report the week",
        description="Report the week from the tracker.",
        when="A goal that asks for the weekly report.",
        steps=list(steps),
        parameters=["<week>: the week"],
    )


async def test_the_reviser_is_shown_the_owners_words_in_a_fence_only_when_it_changes_a_skill_as_they_say() -> None:
    router, provider = await _router(
        _draft_turn("Open the tracker.", "Write and send it."), _draft_turn("Open the tracker.", "Write it.")
    )
    revise: SkillReviser = skill_reviser_for(router, MODEL, (), 30.0, "reflection:test")
    merged: PlaybookDraft = await revise(ReviseRequest(sources=(_FIRST, _SECOND), words=""))
    said: PlaybookDraft = await revise(ReviseRequest(sources=(_FIRST,), words="Send it on Fridays."))
    assert merged.steps == ["Open the tracker.", "Write and send it."] and said.steps == [
        "Open the tracker.",
        "Write it.",
    ]
    merge_call, rewrite_call = provider.requests
    # A merge is two skills and no words, and its task is to merge; a rewrite is one skill and the owner's words.
    assert _REVISE_MERGE in str(merge_call.messages[0].content) and _REVISE_REWRITE not in str(
        merge_call.messages[0].content
    )
    merge_prompt: str = str(merge_call.messages[-1].content)
    assert "<<SKILL 1 " in merge_prompt and "<<SKILL 2 " in merge_prompt and "THE OWNER'S WORDS" not in merge_prompt
    assert _REVISE_REWRITE in str(rewrite_call.messages[0].content)
    rewrite_prompt: str = str(rewrite_call.messages[-1].content)
    assert "<<SKILL 2 " not in rewrite_prompt
    found: re.Match[str] | None = re.search(
        r"<<THE OWNER'S WORDS ([0-9a-f]{12})>>\nSend it on Fridays\.\n", rewrite_prompt
    )
    assert found is not None and f"<<end THE OWNER'S WORDS {found.group(1)}>>" in rewrite_prompt


async def test_the_skill_writers_answer_without_its_judgement_of_whether_the_steps_recur_is_asked_for_again() -> None:
    steps: dict[str, object] = {
        "kind": "Keep a line in a notes file",
        "description": "Write a line to a notes file and read it back.",
        "when": "A goal that asks to keep a line in a file.",
        "steps": ["Write <line> to <file>.", "Read <file> back."],
        "parameters": ["<line>: the text"],
    }
    router, provider = await _router(_answer(PLAYBOOK_TOOL, **steps), _answer(PLAYBOOK_TOOL, recurs=False, **steps))
    write = playbook_writer_for(router, MODEL, (), 30.0, "thr:playbook", "run_1")
    reply: PlaybookReply = await write(
        PlaybookRequest(goal="Keep a line.", host="this Mac", digest="1. write_file", answer="Done.", kinds=())
    )
    assert reply.recurs is False and len(provider.requests) == 2
    assert "not usable" in str(provider.requests[1].messages[-1].content) and "recurs" in str(
        provider.requests[1].messages[-1].content
    )
