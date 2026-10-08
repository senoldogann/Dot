"""What a model can do (images, reasoning labels, sampling) and how the neutral effort maps onto its labels."""

from collections.abc import Iterable

from pydantic import JsonValue

from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel
from dotagent.llm.types import Effort, ModelInfo

# Meaning of every reasoning label on one scale, so a neutral effort can be matched to the closest label of a model.
# ``none`` is the word OpenAI uses for ``off``; ``on`` stands for the default amount of thinking of a plain switch.
_LABEL_RANK: dict[str, int] = {
    "none": 0,
    "off": 0,
    "minimal": 1,
    "low": 2,
    "on": 3,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
    "max": 6,
}
_NEUTRAL_OF_LABEL: dict[str, Effort] = {
    "none": "off",
    "off": "off",
    "on": "on",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}
_SWITCH: tuple[Effort, ...] = ("off", "on")


class ModelTraits(FrozenModel):
    """Static facts about one model.

    ``efforts`` are its reasoning labels (fewer than two means there is no choice); ``thinking_toggle`` marks models
    whose Messages route takes an explicit thinking switch.
    """

    vision: bool
    context_length: int
    max_output_tokens: int
    efforts: tuple[str, ...]
    reasoning: bool
    thinking_toggle: bool
    temperature: bool


def label_rank(label: str) -> int:
    rank: int | None = _LABEL_RANK.get(label)
    if rank is None:
        known: list[JsonValue] = [*sorted(_LABEL_RANK)]
        raise ConfigError("unknown reasoning effort label", {"label": label, "known": known})
    return rank


def is_known_label(label: str) -> bool:
    return label in _LABEL_RANK


def neutral_effort(label: str) -> Effort:
    """A provider's label as a neutral effort (OpenAI's ``none`` is ``off``)."""
    level: Effort | None = _NEUTRAL_OF_LABEL.get(label)
    if level is None:
        known: list[JsonValue] = [*sorted(_LABEL_RANK)]
        raise ConfigError("unknown reasoning effort label", {"label": label, "known": known})
    return level


def effort_ladder(labels: Iterable[str]) -> tuple[Effort, ...]:
    """The labels as neutral efforts, each once, lowest first."""
    levels: set[Effort] = {neutral_effort(label) for label in labels}
    return tuple(sorted(levels, key=label_rank))


def choose_effort(ladder: tuple[str, ...], effort: Effort) -> str | None:
    """The label of ``ladder`` closest to the neutral ``effort``; ``None`` when the model offers no choice.

    ``off`` picks the lowest label, ``max`` the highest, and the others the nearest by meaning (ties go lower).
    """
    if len(ladder) < 2:
        return None
    ordered: list[str] = sorted(ladder, key=label_rank)
    if effort == "off":
        return ordered[0]
    if effort == "max":
        return ordered[-1]
    target: int = label_rank(effort)
    return min(ordered, key=lambda label: (abs(label_rank(label) - target), label_rank(label)))


def effective_effort(ladder: tuple[Effort, ...], effort: Effort) -> Effort | None:
    """The level of a model's neutral ``ladder`` that ``effort`` selects; ``None`` when the model offers no choice."""
    chosen: str | None = choose_effort(ladder, effort)
    return None if chosen is None else neutral_effort(chosen)


def efforts_of(traits: ModelTraits) -> tuple[Effort, ...]:
    """The levels a model offers: its thinking switch, its labels, or none."""
    if traits.thinking_toggle:
        return _SWITCH
    return effort_ladder(traits.efforts) if traits.reasoning else ()


def info_from_traits(model: str, traits: ModelTraits) -> ModelInfo:
    return ModelInfo(
        id=model,
        vision=traits.vision,
        tools=True,
        thinking=traits.reasoning,
        context_length=traits.context_length,
        efforts=efforts_of(traits),
    )
