"""The questions a run asks the user (``ask_user``): the options a question offers, as it stores them, and the one text
that answers it. Choosing options is a way of writing that text: the labels chosen, joined, then what the user wrote on
a line of its own. The page composes it the same way (``static/js/approvals.js``)."""

from collections.abc import Sequence
from typing import Annotated

from pydantic import BeforeValidator, Field, JsonValue, StringConstraints, TypeAdapter

from dotagent.core.types import FrozenModel, JsonObject

# What joins the labels of the options chosen in one answer, when a question takes several.
CHOICES_JOIN: str = "; "
# What separates the numbers of several options typed at the terminal's prompt.
_NUMBERS_SEPARATOR: str = ","


# One answer a question offers: the words the user picks, what choosing it leads to (an empty description is none),
# and whether the run recommends it. A comment, not a docstring: the model would read a docstring in the tool's schema.
class QuestionOption(FrozenModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(description="A few words.")
    description: Annotated[str, StringConstraints(strip_whitespace=True)] | None = Field(
        default=None, description="What it leads to, if the label does not say."
    )
    recommended: bool = Field(default=False, description="True on the one you recommend.")


def _option_of(value: object) -> object:
    """An option given as its words alone, as a question stored it before options had descriptions."""
    return {"label": value} if isinstance(value, str) else value


# An option as it arrives; its words alone stand for an option without a description that nobody recommends.
type Option = Annotated[QuestionOption, BeforeValidator(_option_of)]
_OPTIONS: TypeAdapter[list[Option]] = TypeAdapter(list[Option])


def stored_options(args: JsonObject) -> tuple[QuestionOption, ...]:
    """The options of the question a record or an event stores (its ``args``); none when it offers none."""
    options: JsonValue = args.get("options")
    return () if options is None else tuple(_OPTIONS.validate_python(options))


def takes_several(args: JsonObject) -> bool:
    """Whether the stored question lets the user choose several of its options."""
    return args.get("multiple") is True


def chosen_answer(chosen: Sequence[str], written: str) -> str:
    """The answer of a user who chose the options labelled ``chosen`` and wrote ``written``; either may be empty."""
    return "\n".join(part for part in (CHOICES_JOIN.join(chosen), written.strip()) if part)


def typed_answer(options: Sequence[QuestionOption], several: bool, line: str) -> str:
    """The answer typed at the terminal's prompt: the number of an option, or the numbers of several separated by commas
    when the question takes several, stands for those options; anything else is the user's own words."""
    typed: str = line.strip()
    parts: list[str] = [part.strip() for part in typed.split(_NUMBERS_SEPARATOR)] if several else [typed]
    if typed == "" or not all(part.isdigit() and 1 <= int(part) <= len(options) for part in parts):
        return typed
    numbers: list[int] = list(dict.fromkeys(int(part) for part in parts))
    return chosen_answer([options[number - 1].label for number in numbers], "")


def answered(options: Sequence[QuestionOption], answer: str) -> str:
    """What the run reads of the user's answer: one option chosen alone is named as an option, with whether it is the one
    the run recommended; any other answer is quoted as it was given."""
    for option in options:
        if option.label == answer:
            recommended: str = ", the one you recommended" if option.recommended else ""
            return f"The user chose “{option.label}”{recommended}."
    return f"The user answered: {answer}"
