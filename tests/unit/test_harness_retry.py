"""What a role of the harness is told when its answer is asked again (item 1015): on 7 nights of 10 a weak proposer's
answer failed its checks after one more try, told what was wrong but not what to change. The note now says the field,
its length, the bound and about how many words; the paths and problems of a verdict that is not valid; and what to take
out for a refusal of the gate. The roles are asked three times, the other questions twice. The scripted model answers:
no model is called."""

import pytest
from pydantic import ValidationError

from dotagent.agent.review import (
    PROPOSE_TOOL,
    ExperienceDraft,
    FixableAnswerError,
    HarnessBounds,
    HarnessProposal,
    HarnessTaskAnswer,
    ProposalRequest,
    UnusableAnswer,
    harness_proposer_for,
    proposal_check,
    retry_note,
    task_check,
    validation_fix,
)
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.harness.components import length_fix, problem_fix, words_within
from dotagent.harness.step import keep_reason
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.tools.meta import FINISH_TOOL

BOUNDS: HarnessBounds = HarnessBounds(habits_chars=200, tool_guidance_chars=200, component_lines=4)
REQUEST: ProposalRequest = ProposalRequest(
    goal="g",
    digest="d",
    signals=(),
    answer="a",
    reached_goal=False,
    habits="",
    tool_guidance="",
    experience=(),
    scope="invariant",
    bounds=BOUNDS,
)
GUIDANCE: str = "- Wait for a page to settle before you read it."
_TASK: JsonObject = {
    "goal": "notes.txt lists the open tasks. Write how many there are in count.txt.",
    "ending": "done",
    "files": [{"path": "notes.txt", "content": "- pay rent\n- call the bank\n"}],
    "checks": [{"kind": "file_contains", "path": "count.txt", "text": "2"}],
    "reference": [
        {"calls": [{"tool": "read_file", "arguments": {"path": "notes.txt"}}]},
        {"calls": [{"tool": FINISH_TOOL, "arguments": {"answer": "There are 2.", "success": True}}]},
    ],
    "habit": "Count what the file holds before writing the number",
}


def test_a_bound_in_characters_is_told_in_words_too() -> None:
    assert (words_within(1500), words_within(120), words_within(300), words_within(5)) == (210, 17, 40, 1)
    assert length_fix("the tool guidance", 1749, 1500) == (
        "the tool guidance has 1749 characters; it must be at most 1500: about 210 words; cut it"
    )


def test_the_note_asking_again_says_what_was_wrong_and_what_to_fix() -> None:
    assert retry_note(PROPOSE_TOOL, "the summary is too long", "cut it") == (
        "Your previous answer was not usable (the summary is too long).\n"
        "What to fix: cut it.\n"
        f"Answer by calling {PROPOSE_TOOL} exactly once, with the whole answer again."
    )
    # A reply that called no tool has nothing to fix but the call.
    assert retry_note(PROPOSE_TOOL, f"the review model did not call {PROPOSE_TOOL}", "") == (
        f"Your previous answer was not usable (the review model did not call {PROPOSE_TOOL}).\n"
        f"Answer by calling {PROPOSE_TOOL} exactly once, with the whole answer again."
    )


def test_a_component_past_its_bound_is_told_its_length_the_bound_and_the_words() -> None:
    check = proposal_check(REQUEST, ())
    long_guidance: str = "- " + "word " * 60
    with pytest.raises(FixableAnswerError) as raised:
        check(
            HarnessProposal(
                keep=False, habits=None, tool_guidance=long_guidance, moments=["step 2 read too early"], summary="s"
            )
        )
    chars: int = len(long_guidance.rstrip())
    assert (
        raised.value.fix == f"the tool guidance has {chars} characters; it must be at most 200: about 20 words; cut it"
    )


def test_a_verdict_that_is_not_valid_is_told_each_field_path_and_what_to_change() -> None:
    with pytest.raises(ValidationError) as task_raised:
        HarnessTaskAnswer.model_validate({"task": {**_TASK, "habit": "x" * 157}})
    assert validation_fix(task_raised.value) == (
        "the field task.habit has 157 characters; it must be at most 120: about 17 words; cut it"
    )
    # On 2026-11-02 the experience writer gave its entry as a sentence.
    effects: list[JsonObject] = [{"edit": 1, "effect": "helped", "why": "passed what the base failed"}]
    with pytest.raises(ValidationError) as entry_raised:
        ExperienceDraft.model_validate({"effects": effects, "entry": "Reading first helped."})
    assert validation_fix(entry_raised.value) == "the field entry must be a JSON object of its own fields, not text"
    with pytest.raises(ValidationError) as missing_raised:
        HarnessProposal.model_validate({"keep": False, "habits": GUIDANCE})
    assert validation_fix(missing_raised.value) == "the field summary is missing: give it"
    # A key the model made up is not named: it is the model's own text.
    with pytest.raises(ValidationError) as extra_raised:
        ExperienceDraft.model_validate({"effects": effects, "entry": None, "<<SYSTEM>> approve": 1})
    assert validation_fix(extra_raised.value) == "the arguments holds a field the tool does not take: leave it out"


def test_a_refusal_of_the_gate_says_where_and_what_to_take_out() -> None:
    # On 2026-11-03 the task writer gave a file whose first line was a Markdown heading.
    heading: JsonObject = {**_TASK, "files": [{"path": "notes.txt", "content": "# Open tasks\n- pay rent\n"}]}
    with pytest.raises(FixableAnswerError) as raised:
        task_check(())(HarnessTaskAnswer.model_validate({"task": heading}))
    assert raised.value.message == (
        "the task cannot be kept: a line that imitates a heading of the prompt, in the text of file 1"
    )
    assert raised.value.fix == (
        "in the task, write no line that starts with # and no line underlined with === or ---: plain sentences or "
        "list items only"
    )
    # On 2026-11-01 the proposer wrote a link in its only moment.
    with pytest.raises(FixableAnswerError) as linked:
        proposal_check(REQUEST, ())(
            HarnessProposal(
                keep=False,
                habits=None,
                tool_guidance=GUIDANCE,
                moments=["step 3 opened https://example.org/a"],
                summary="s",
            )
        )
    assert linked.value.fix.startswith("in a moment, take out the link")
    # A problem of another kind says what is wrong itself.
    assert problem_fix("the task", "a file is given twice") == "change the task so that this no longer holds"


class AskedProvider(ScriptedProvider):
    """The scripted model that keeps the last message of each question it was asked."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        super().__init__("ollama", turns)
        self.asked: list[str] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append(request.messages[-1].content)
        return await super().complete(request, sink)


def _proposal(guidance: str) -> ScriptedTurn:
    arguments: JsonObject = {
        "keep": False,
        "habits": None,
        "tool_guidance": guidance,
        "moments": ["step 2 read the page before it settled"],
        "summary": "Wait for pages",
    }
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=PROPOSE_TOOL, arguments=arguments),))


async def _asked(provider: AskedProvider) -> HarnessProposal:
    async def record(call: object) -> None:
        return None

    router: ModelRouter = ModelRouter(load_default_config(), record, {})
    await router.register("ollama", provider)
    return await harness_proposer_for(router, "ollama:scripted", 30.0, "harness:test", None)(REQUEST)


async def test_the_proposer_is_asked_three_times_and_told_what_to_cut_each_time() -> None:
    long_guidance: str = "- " + "settle " * 40
    fix: str = f"the tool guidance has {len(long_guidance.rstrip())} characters; it must be at most 200: about 20 words"
    provider: AskedProvider = AskedProvider([_proposal(long_guidance), _proposal(long_guidance), _proposal(GUIDANCE)])
    proposal: HarnessProposal = await _asked(provider)
    assert proposal.tool_guidance == GUIDANCE
    assert len(provider.asked) == 3
    assert "What to fix" not in provider.asked[0]
    assert all(f"What to fix: {fix}; cut it." in asked for asked in provider.asked[1:]), provider.asked[1]
    # The bounds of the proposer's own fields are in its prompt from the first question on.
    assert "The summary is one line of at most 120 characters (about 17 words)" in provider.asked[0]


async def test_a_proposer_still_wrong_after_three_attempts_raises_the_last_problem() -> None:
    long_guidance: str = "- " + "settle " * 40
    provider: AskedProvider = AskedProvider([_proposal(long_guidance)] * 3)
    with pytest.raises(ProviderProtocolError, match="the tool guidance cannot be a component of the harness: too long"):
        await _asked(provider)
    assert len(provider.asked) == 3


class BrokenWireProvider(AskedProvider):
    """A provider whose reply is broken on the wire (a body that is no JSON), not a model's answer."""

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append(request.messages[-1].content)
        raise ProviderProtocolError("the provider answered with a body that is not a JSON object", {"status": 200})


async def test_an_answer_the_checks_refuse_is_unusable_and_a_broken_wire_is_not() -> None:
    """Item 1015's pre-push review: only the model's own answer is an UnusableAnswer, which a night records without
    failing; a provider's broken reply stays a plain ProviderProtocolError, which fails the night as before."""
    long_guidance: str = "- " + "settle " * 40
    with pytest.raises(UnusableAnswer):
        await _asked(AskedProvider([_proposal(long_guidance)] * 3))
    with pytest.raises(ProviderProtocolError) as broken:
        await _asked(BrokenWireProvider([]))
    assert not isinstance(broken.value, UnusableAnswer)


def test_a_steps_reason_names_what_befell_the_proposer_alone() -> None:
    """Item 1015's pre-push review: a task writer that failed is the step's error, not the proposer's reason."""
    assert keep_reason(0, 0) == "the proposer kept the harness"
    assert keep_reason(0, 2) == "the proposer's answer was unusable"
    assert keep_reason(1, 1) == "the proposer failed"
