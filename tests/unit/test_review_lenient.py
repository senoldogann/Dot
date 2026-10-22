"""A skill the review model writes with its lists as text (item 543): "Teach a task" failed with "the arguments of
write_playbook are not a valid verdict" when DeepSeek V4.1 Flash gave the steps as one string (and Claude Haiku 5.5
left the parameters out). The steps and parameters are read as the list they mean, a skill with no parameters has none,
and a verdict that is still wrong says which field was and how, to the owner and to the model asked once more."""

import pytest
from pydantic import ValidationError

from dotagent.agent.review import PLAYBOOK_TOOL, PlaybookDraft, PlaybookRequest, PlaybookWriter, playbook_writer_for
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply

REQUEST: PlaybookRequest = PlaybookRequest(
    goal="How Instagram works, a little bit, shown once.",
    host="www.instagram.com",
    digest="page: https://www.instagram.com/\nclick: Search\ntype: <search term>",
    answer="No run answered this: the owner showed the task once.",
    kinds=(),
)
DRAFT: JsonObject = {
    "kind": "look up an Instagram account",
    "description": "Opens Instagram and searches for an account by name.",
    "when": "The goal names an Instagram account to look up.",
    "steps": ["Open instagram.com.", "Click Search.", "Type <account> and open the first result."],
    "parameters": ["<account>: the name of the account"],
}


def _turn(arguments: JsonObject) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=PLAYBOOK_TOOL, arguments=arguments),))


class AskedProvider(ScriptedProvider):
    """The scripted model that keeps what it was asked: the last message of each question."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        super().__init__("ollama", turns)
        self.asked: list[str] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append(request.messages[-1].content)
        return await super().complete(request, sink)


async def _writer(provider: ScriptedProvider) -> PlaybookWriter:
    async def record(call: object) -> None:
        return None

    router: ModelRouter = ModelRouter(load_default_config(), record, {})
    await router.register("ollama", provider)
    return playbook_writer_for(router, "ollama:scripted", (), 30.0, "teach:test", None)


@pytest.mark.parametrize(
    ("text", "steps"),
    [
        ("1. Open the page\n2. Click Search\n3) Read the result", ["Open the page", "Click Search", "Read the result"]),
        ("- Open the page\n* Click Search\n• Read the result", ["Open the page", "Click Search", "Read the result"]),
        ('["Open the page", "Click Search"]', ["Open the page", "Click Search"]),
        ("  Open the page\n\n Click Search \n", ["Open the page", "Click Search"]),
        ("Open the page", ["Open the page"]),
        # A text that opens with a bracket and is no JSON is lines like any other.
        ("[draft] Open the page\nClick Search", ["[draft] Open the page", "Click Search"]),
    ],
)
def test_steps_written_as_text_are_read_as_the_list_they_mean(text: str, steps: list[str]) -> None:
    assert PlaybookDraft.model_validate({**DRAFT, "steps": text}).steps == steps


def test_parameters_written_as_text_or_left_out_or_null_are_a_list_too() -> None:
    assert PlaybookDraft.model_validate({**DRAFT, "parameters": "1. <account>: the name"}).parameters == [
        "<account>: the name"
    ]
    left_out: JsonObject = {key: value for key, value in DRAFT.items() if key != "parameters"}
    assert PlaybookDraft.model_validate(left_out).parameters == []
    assert PlaybookDraft.model_validate({**DRAFT, "parameters": None}).parameters == []


@pytest.mark.parametrize("steps", ["", " \n \n", None, 7, [], ["ok", ""], "\n".join(f"step {n}" for n in range(21))])
def test_steps_that_hold_no_usable_step_or_too_many_are_still_refused(steps: object) -> None:
    with pytest.raises(ValidationError) as raised:
        PlaybookDraft.model_validate({**DRAFT, "steps": steps})
    assert {error["loc"][0] for error in raised.value.errors()} == {"steps"}


async def test_the_writer_takes_a_skill_whose_steps_came_as_text_at_the_first_answer() -> None:
    steps: str = "1. Open instagram.com.\n2. Click Search.\n3. Type <account> and open the first result."
    provider: AskedProvider = AskedProvider([_turn({**DRAFT, "steps": steps})])
    written: PlaybookDraft = await (await _writer(provider))(REQUEST)
    assert written.steps == DRAFT["steps"]
    assert len(provider.asked) == 1


async def test_a_verdict_that_stays_wrong_names_its_fields_to_the_owner_and_to_the_model() -> None:
    wrong: JsonObject = {key: value for key, value in DRAFT.items() if key != "kind"} | {"steps": 7}
    provider: AskedProvider = AskedProvider([_turn(wrong), _turn(wrong)])
    with pytest.raises(ProviderProtocolError) as raised:
        await (await _writer(provider))(REQUEST)
    problem: str = "kind: Field required; steps: Input should be a valid list"
    assert raised.value.message == f"the arguments of write_playbook are not a valid verdict ({problem})"
    assert len(provider.asked) == 2
    assert (
        f"Your previous answer was not usable (the arguments of write_playbook are not a valid verdict ({problem}))"
        in (provider.asked[1])
    )
