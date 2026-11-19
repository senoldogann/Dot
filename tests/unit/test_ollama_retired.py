"""A cloud model Ollama's service retired is left out of the list of models, not the list with it (item 1195)."""

import pytest

from dotagent.core.errors import ProviderHttpError
from dotagent.llm.ollama import DescribedModels, described_models
from dotagent.llm.types import ModelInfo

_RETIRED: str = (
    "provider answered HTTP 410: mistral-large-3:675b was retired on November 9, 2026. Try glm-5.3-flash:cloud."
)


def _info(name: str) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=False,
        tools=True,
        thinking=True,
        context_length=131_072,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=False,
    )


def test_a_retired_model_is_set_apart_with_the_servers_words_and_the_others_are_listed() -> None:
    names: tuple[str, ...] = ("deepseek-v4.1-flash:cloud", "mistral-large-3:675b-cloud", "gemma4:cloud")
    outcomes: list[ModelInfo | BaseException] = [
        _info(names[0]),
        ProviderHttpError(_RETIRED, 410, {"url": "http://127.0.0.1:11434/api/show"}),
        _info(names[2]),
    ]
    described: DescribedModels = described_models(names, outcomes)
    assert [info.id for info in described.models] == [names[0], names[2]]
    assert described.retired == ((names[1], _RETIRED),)


def test_any_other_failure_fails_the_list() -> None:
    refused: ProviderHttpError = ProviderHttpError("provider answered HTTP 500: boom", 500, {})
    with pytest.raises(ProviderHttpError, match="HTTP 500"):
        described_models(("a", "b"), [_info("a"), refused])
