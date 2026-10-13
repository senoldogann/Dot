"""Questions with options (item 192): what ``ask_user`` takes and offers the model, how a stored question's options read
(words alone, as before, or described), the one text that choosing options makes, a number typed at the terminal, and
what the run reads of the answer."""

import pytest
from pydantic import ValidationError

from dotagent.agent.questions import QuestionOption, answered, chosen_answer, stored_options, typed_answer
from dotagent.core.types import JsonObject
from dotagent.tools.meta import AskUserArgs
from dotagent.tools.schema import tool_parameters_schema

BLUE: QuestionOption = QuestionOption(label="Blue", description="The colour of the rest of the site.", recommended=True)
GREEN: QuestionOption = QuestionOption(label="Green", description=None, recommended=False)


def _plain(label: str) -> QuestionOption:
    return QuestionOption(label=label, description=None, recommended=False)


def test_options_given_as_words_alone_are_options_without_a_description() -> None:
    args: AskUserArgs = AskUserArgs.model_validate(
        {"question": "Which colour?", "options": ["Blue", {"label": "Green", "description": "Stands out."}]}
    )
    assert args.options == [_plain("Blue"), QuestionOption(label="Green", description="Stands out.", recommended=False)]
    assert stored_options({"question": "Which colour?", "options": ["Blue", "Green"]}) == (
        _plain("Blue"),
        _plain("Green"),
    )
    assert stored_options({"question": "Which colour?", "options": [BLUE.model_dump()]}) == (BLUE,)
    assert stored_options({"question": "Which colour?"}) == ()


def test_options_need_labels_of_their_own_and_one_recommendation_unless_several_may_be_chosen() -> None:
    with pytest.raises(ValidationError, match="a label of its own"):
        AskUserArgs.model_validate({"question": "Which colour?", "options": ["Blue", "Blue"]})
    both: list[JsonObject] = [{"label": "Blue", "recommended": True}, {"label": "Green", "recommended": True}]
    with pytest.raises(ValidationError, match="recommend one option at most"):
        AskUserArgs.model_validate({"question": "Which colour?", "options": both})
    assert AskUserArgs.model_validate({"question": "Which pages?", "options": both, "multiple": True}).multiple


def test_the_model_is_offered_options_with_a_description_and_a_recommendation() -> None:
    schema: JsonObject = tool_parameters_schema(AskUserArgs)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    options = properties["options"]
    assert isinstance(options, dict)
    items = options["items"]
    assert isinstance(items, dict)
    fields = items["properties"]
    assert isinstance(fields, dict)
    assert list(fields) == ["label", "description", "recommended"]
    assert items["required"] == ["label"]
    assert "multiple" in properties


def test_choosing_options_makes_one_answer() -> None:
    assert chosen_answer(["Home", "Runs"], "  keep Settings as it is ") == "Home; Runs\nkeep Settings as it is"
    assert chosen_answer(["Home"], "") == "Home"
    assert chosen_answer([], "Red") == "Red"


def test_a_number_typed_at_the_terminal_stands_for_its_option() -> None:
    options: tuple[QuestionOption, ...] = (BLUE, GREEN)
    assert typed_answer(options, False, " 2 ") == "Green"
    assert typed_answer(options, False, "3") == "3"
    assert typed_answer(options, False, "1, 2") == "1, 2"
    assert typed_answer(options, True, "2, 1, 2") == "Green; Blue"
    assert typed_answer(options, True, "red") == "red"
    assert typed_answer((), False, "1") == "1"


def test_the_run_reads_which_option_was_chosen() -> None:
    assert answered((BLUE, GREEN), "Blue") == "The user chose “Blue”, the one you recommended."
    assert answered((BLUE, GREEN), "Green") == "The user chose “Green”."
    assert answered((BLUE, GREEN), "Blue; Green") == "The user answered: Blue; Green"
    assert answered((), "Red") == "The user answered: Red"
