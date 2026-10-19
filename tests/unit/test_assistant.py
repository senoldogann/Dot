"""Who Dot is to its owner (item 420): the owner's first name from their account's full name, the lines the prompt
lists among what Dot knows about the owner, and the names a change may carry."""

import pytest
from pydantic import ValidationError

from dotagent.assistant import called_lines, first_name, owner_name_lines
from dotagent.server.views import AssistantRequest


def test_the_first_name_is_the_first_word_of_the_full_name_before_any_other_field() -> None:
    assert first_name("Ada Lovelace") == "Ada"
    assert first_name("  Şenol Doğan ") == "Şenol"
    assert first_name("Grace Hopper,Room 7,555") == "Grace"
    assert first_name("") is None
    assert first_name(" , ") is None
    assert first_name("x" * 60) == "x" * 40


def test_the_prompt_says_the_owners_name_as_theirs_or_as_their_accounts_and_dots_name_once_given() -> None:
    assert owner_name_lines("Ada", "Ahmet") == ("(you said) My name is Ada.",)
    assert owner_name_lines("", "Ahmet") == ("The user's name is Ahmet, as their account on this Mac has it.",)
    assert owner_name_lines("", None) == ()
    assert called_lines("Dot") == ()
    assert called_lines("Miro") == ("(you said) I call you Miro: answer to that name.",)


@pytest.mark.parametrize(
    "wrong", [{"name": "   "}, {"name": "M\niro"}, {"name": "x" * 25}, {"owner": "A\tda"}, {"owner": "x" * 41}]
)
def test_a_change_with_a_blank_name_a_control_character_or_too_long_a_name_is_refused(wrong: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(wrong)


def test_a_change_strips_its_names_and_a_blank_owner_forgets_the_given_name() -> None:
    assert AssistantRequest.model_validate({"name": "  Miro "}).name == "Miro"
    assert AssistantRequest.model_validate({"owner": "  "}).owner == ""
    assert AssistantRequest.model_validate({}).model_dump() == {"name": None, "owner": None, "introduced": None}
