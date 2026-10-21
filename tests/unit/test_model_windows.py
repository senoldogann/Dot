"""The window of each model as its provider gives it (item 221): OpenAI's from its model pages, read again 2026-10-14;
an Ollama model on Ollama's servers told by its tag, whichever of the two forms Ollama gives it, and asked for the cloud
window; a model whose own window is larger than the one Dot asks of it keeps it in view."""

import pytest

from dotagent.agent.scaffold import Scaffold, context_ceiling, most_window, run_window, scaffold_for, window_choices
from dotagent.config import OllamaConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.llm.ollama import asked_window, is_cloud_model, num_ctx_for, parse_model_details
from dotagent.llm.openai import openai_traits
from dotagent.llm.types import ModelInfo
from dotagent.store.stats import ModelProfile

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


def test_a_context_size_chosen_for_an_ollama_model_of_this_mac_asks_for_the_window_it_needs() -> None:
    """Item 520: the owner's Ollama gave its models 128K, Dot asked every one for num_ctx 65,536, and a 9B model of a
    262,144-token window had a budget of 49,152, under the 53,172 its prompt and tools take, so no run started. A
    context size chosen for such a model asks for the window it needs, three quarters of it being the size, up to the
    model's own, and the run plans with that same window."""
    ollama: OllamaConfig = load_default_config().providers.ollama
    shown: JsonObject = {"capabilities": ["tools"], "model_info": {"qwen35.context_length": 262_144}}
    info: ModelInfo = parse_model_details("bonsai-2:9b", shown, num_ctx_for("bonsai-2:9b", ollama)).info
    assert (info.context_length, info.own_context_length) == (65_536, 262_144)
    # No size chosen, or one the default window holds: the window of num_ctx.
    assert asked_window("bonsai-2:9b", info, ollama, None) == 65_536
    assert asked_window("bonsai-2:9b", info, ollama, 48_000) == 65_536
    # 96K needs 128K; 128K a whole number of steps over 170,666; more than the model holds stops at its own.
    assert asked_window("bonsai-2:9b", info, ollama, 98_304) == 131_072
    assert asked_window("bonsai-2:9b", info, ollama, 131_072) == 175_104
    assert asked_window("bonsai-2:9b", info, ollama, 196_608) == 262_144
    assert asked_window("bonsai-2:9b", info, ollama, 512_000) == 262_144
    # The run plans with the window the request asks for.
    for chosen in (None, 48_000, 98_304, 131_072, 196_608):
        assert run_window(info, chosen) == asked_window("bonsai-2:9b", info, ollama, chosen)
    # A model whose window Ollama does not say, or one smaller than num_ctx, is asked as before.
    unsaid: ModelInfo = parse_model_details("m", {"capabilities": ["tools"]}, 65_536).info
    assert asked_window("m", unsaid, ollama, 98_304) == 65_536
    small: ModelInfo = parse_model_details("tiny", {"model_info": {"llama.context_length": 8_192}}, 65_536).info
    assert asked_window("tiny", small, ollama, 98_304) == 8_192


def test_settings_offer_an_ollama_model_of_this_mac_sizes_up_to_its_own_window() -> None:
    """Item 520: Settings › Models offered such a model nothing past three quarters of num_ctx; it offers sizes up to
    three quarters of the model's own window, and a run given one has the budget it names."""
    config = load_default_config()
    shown: JsonObject = {"capabilities": ["tools"], "model_info": {"qwen35.context_length": 262_144}}
    info: ModelInfo = parse_model_details("bonsai-2:9b", shown, config.providers.ollama.num_ctx).info
    assert window_choices(config.agent, info, None) == (48_000, 64_000, 96_000, 128_000, 192_000, 196_608)
    assert context_ceiling(most_window(info)) == 196_608
    quiet: ModelProfile = ModelProfile(
        model="ollama:bonsai-2:9b",
        runs=0,
        steps=0,
        tool_calls=0,
        tool_errors=0,
        refused_calls=0,
        invalid_arguments=0,
        empty_replies=0,
        stalls=0,
        compactions=0,
    )
    given: Scaffold = scaffold_for(config.agent, info, quiet, 98_304)
    assert (given.window, given.context_tokens) == (131_072, 98_304)
    # A model of another provider is offered as before: up to three quarters of the window its provider says.
    openai_info: ModelInfo = ModelInfo(
        id="m",
        vision=False,
        tools=True,
        thinking=False,
        context_length=204_800,
        own_context_length=None,
        window_source="provider",
        efforts=(),
        free=False,
    )
    assert window_choices(config.agent, openai_info, None) == (48_000, 64_000, 96_000, 128_000)
