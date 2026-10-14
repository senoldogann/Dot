"""The window of each model as its provider gives it (item 221): OpenAI's from its model pages, read again 2026-10-14;
an Ollama model on Ollama's servers told by its tag, whichever of the two forms Ollama gives it, and asked for the cloud
window; a model whose own window is larger than the one Dot asks of it keeps it in view."""

import pytest

from dotagent.config import OllamaConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.llm.ollama import is_cloud_model, num_ctx_for, parse_model_details
from dotagent.llm.openai import openai_traits
from dotagent.llm.types import ModelInfo

_OPENAI_WINDOWS: list[tuple[str, int]] = [
    ("gpt-5.1", 400_000),
    ("gpt-5.1-codex-max", 400_000),
    ("gpt-5.1-chat-latest", 128_000),
    ("gpt-5.2", 400_000),
    ("gpt-5.2-pro-2025-12-11", 400_000),
    ("gpt-5.2-chat-latest", 128_000),
    ("gpt-5.3-codex", 400_000),
    ("gpt-5.3-chat-latest", 128_000),
    ("gpt-5.4", 1_050_000),
    ("gpt-5.4-2026-03-05", 1_050_000),
    ("gpt-5.4-pro", 1_050_000),
    ("gpt-5.4-mini", 400_000),
    ("gpt-5.4-nano-2026-03-17", 400_000),
    ("gpt-5.5", 1_050_000),
    ("gpt-5.5-pro-2026-04-23", 1_050_000),
    ("gpt-5.6-sol", 1_050_000),
    ("gpt-6.1-sol", 1_050_000),
    ("gpt-5-chat-latest", 128_000),
]


@pytest.mark.parametrize(("model", "window"), _OPENAI_WINDOWS)
def test_an_openai_model_has_the_window_its_page_gives(model: str, window: int) -> None:
    assert openai_traits(model).context_length == window


def test_a_cloud_model_of_ollama_is_told_by_its_tag_in_either_form() -> None:
    ollama: OllamaConfig = load_default_config().providers.ollama
    for model in ("kimi-k3:cloud", "gpt-oss:120b-cloud", "mistral-large-3:675b-cloud"):
        assert is_cloud_model(model) and num_ctx_for(model, ollama) == ollama.num_ctx_cloud, model
    for model in ("gpt-oss:20b", "qwen3:latest", "cloud:8b", "cloud", ":cloud"):
        assert not is_cloud_model(model) and num_ctx_for(model, ollama) == ollama.num_ctx, model


def test_a_model_whose_own_window_is_larger_than_the_one_asked_keeps_it_in_view() -> None:
    large: JsonObject = {"capabilities": ["tools"], "model_info": {"kimi.context_length": 1_048_576}}
    capped: ModelInfo = parse_model_details("kimi-k3:cloud", large, 262_144).info
    assert (capped.context_length, capped.own_context_length) == (262_144, 1_048_576)
    small: JsonObject = {"capabilities": ["tools"], "model_info": {"gptoss.context_length": 131_072}}
    whole: ModelInfo = parse_model_details("gpt-oss:120b-cloud", small, 262_144).info
    assert (whole.context_length, whole.own_context_length) == (131_072, None)
    unsaid: ModelInfo = parse_model_details("m", {"capabilities": ["tools"]}, 65_536).info
    assert (unsaid.context_length, unsaid.own_context_length) == (65_536, None)
