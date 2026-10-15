"""The context size of a model's runs (item 250): the sizes Settings offers for a window, the budget and the point of
summary a chosen size gives a run, the config file that keeps it, and what a model's runs measured about it."""

import tomllib
from pathlib import Path

import pytest

from dotagent.agent.scaffold import Scaffold, context_choices, scaffold_event, scaffold_for, summarise_point
from dotagent.config import AgentConfig, load_config, load_default_config
from dotagent.config_edit import set_context_size, with_context_size
from dotagent.core.errors import ConfigError
from dotagent.llm.types import ModelInfo
from dotagent.store.stats import ContextCall, InputBucket, ModelProfile, context_evidence

# The packaged defaults: context_tokens 96 000, context_summarise_share 0.85, tool_output_chars 12 000.
DEFAULTS: AgentConfig = load_default_config().agent
MODEL: str = "anthropic:claude-opus-5-5"


def _info(window: int) -> ModelInfo:
    return ModelInfo(
        id="claude-opus-5-5",
        vision=True,
        tools=True,
        thinking=True,
        context_length=window,
        own_context_length=None,
        window_source="provider",
        efforts=(),
        free=False,
    )


def _profile() -> ModelProfile:
    return ModelProfile(
        model=MODEL,
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


def test_settings_offers_the_sizes_from_the_floor_to_three_quarters_of_the_window_besides_the_default() -> None:
    assert context_choices(DEFAULTS, 1_000_000, None) == (
        48_000,
        64_000,
        128_000,
        192_000,
        256_000,
        384_000,
        512_000,
        750_000,
    )
    # MiniMax M2.7's window: three quarters of it is the most.
    assert context_choices(DEFAULTS, 204_800, None) == (48_000, 64_000, 128_000, 153_600)
    # Three quarters of the window are the default already: the floor is the one other size.
    assert context_choices(DEFAULTS, 65_536, None) == (48_000,)
    # A window whose three quarters are below the floor, or one the provider does not say, offers none.
    assert context_choices(DEFAULTS, 32_768, None) == ()
    assert context_choices(DEFAULTS, None, None) == ()
    # A size written by hand is offered too, so the menu can show it.
    assert 100_000 in context_choices(DEFAULTS, 1_000_000, 100_000)


def test_a_chosen_size_sets_the_budget_the_tool_output_and_the_point_of_summary_up_to_the_window() -> None:
    large: Scaffold = scaffold_for(DEFAULTS, _info(1_000_000), _profile(), 192_000)
    assert (large.chosen, large.context_tokens, large.tool_output_chars) == (192_000, 192_000, 12_000)
    assert summarise_point(DEFAULTS, large.chosen, large.context_tokens) == pytest.approx(163_200)
    small: Scaffold = scaffold_for(DEFAULTS, _info(1_000_000), _profile(), 64_000)
    assert (small.context_tokens, small.tool_output_chars) == (64_000, 8_000)
    assert summarise_point(DEFAULTS, small.chosen, small.context_tokens) == pytest.approx(54_400)
    # The window still cuts a chosen size, and the event says the window did.
    cut: Scaffold = scaffold_for(DEFAULTS, _info(204_800), _profile(), 192_000)
    assert cut.context_tokens == 153_600
    event = scaffold_event(cut, DEFAULTS)
    assert (event["chosen"], event["context_tokens"], event["summarise_at"], event["cut_to_window"]) == (
        192_000,
        153_600,
        153_600,
        True,
    )
    assert scaffold_event(large, DEFAULTS)["cut_to_window"] is False


def test_a_size_is_written_under_its_own_table_with_the_model_quoted_and_the_rest_of_the_file_as_it_was(
    tmp_path: Path,
) -> None:
    path: Path = tmp_path / "config.toml"
    mine: str = '[models]\nmain = "ollama:x"  # mine\n'
    path.write_text(mine, encoding="utf-8")
    set_context_size(path, MODEL, 192_000)
    assert path.read_text(encoding="utf-8") == f'{mine}\n[models.context]\n"{MODEL}" = 192000\n'
    # Replaced where it is, its comment kept; another model's goes right below the header.
    path.write_text(path.read_text(encoding="utf-8").replace("192000", "192000  # roomy"), encoding="utf-8")
    set_context_size(path, MODEL, 256_000)
    set_context_size(path, "openai:gpt-5.5", 128_000)
    assert path.read_text(encoding="utf-8") == (
        f'{mine}\n[models.context]\n"openai:gpt-5.5" = 128000\n"{MODEL}" = 256000  # roomy\n'
    )
    # Taken out for Dot's default; taking out one that is not there changes nothing.
    set_context_size(path, MODEL, None)
    set_context_size(path, MODEL, None)
    assert path.read_text(encoding="utf-8") == f'{mine}\n[models.context]\n"openai:gpt-5.5" = 128000\n'
    assert load_config(path).models.context == {"openai:gpt-5.5": 128_000}


def test_a_key_in_single_quotes_is_found_and_a_header_with_spaces_too() -> None:
    text: str = f"[ models . context ]\n'{MODEL}' = 64000\n"
    assert with_context_size(text, MODEL, 96_000) == f"[ models . context ]\n'{MODEL}' = 96000\n"
    assert with_context_size(text, MODEL, None) == "[ models . context ]\n"


def test_sizes_written_as_an_inline_table_are_refused_and_the_file_left_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    text: str = f'[models]\ncontext = {{ "{MODEL}" = 64000 }}\n'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="by hand"):
        set_context_size(path, "openai:gpt-5.5", 128_000)
    assert path.read_text(encoding="utf-8") == text
    assert tomllib.loads(text)["models"]["context"] == {MODEL: 64_000}


def test_a_size_below_the_floor_in_the_file_is_refused_when_the_config_is_read(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text(f'[models.context]\n"{MODEL}" = 32000\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_the_evidence_of_a_models_runs_counts_its_steps_their_input_and_their_seconds_by_input() -> None:
    calls: list[ContextCall] = [
        ContextCall(input_tokens=20_000, cached_tokens=10_000, seconds=2.0, run_id="a"),
        ContextCall(input_tokens=40_000, cached_tokens=10_000, seconds=3.0, run_id="a"),
        ContextCall(input_tokens=70_000, cached_tokens=10_000, seconds=5.0, run_id="b"),
        ContextCall(input_tokens=250_000, cached_tokens=200_000, seconds=21.0, run_id="b"),
    ]
    evidence = context_evidence(MODEL, 30, calls, [20_000, 30_000], 3, 1)
    assert (evidence.runs, evidence.steps) == (2, 4)
    assert (evidence.input_median, evidence.input_p95, evidence.input_max) == (55_000, 250_000, 250_000)
    assert evidence.cached_share == pytest.approx(230_000 / 380_000)
    assert (evidence.first_step_median, evidence.compactions, evidence.compacted_runs) == (25_000, 3, 1)
    # The buckets that have steps, each up to its number of tokens; the last one is open.
    assert evidence.buckets == (
        InputBucket(above=0, up_to=32_000, steps=1, seconds_median=2.0),
        InputBucket(above=32_000, up_to=64_000, steps=1, seconds_median=3.0),
        InputBucket(above=64_000, up_to=96_000, steps=1, seconds_median=5.0),
        InputBucket(above=192_000, up_to=None, steps=1, seconds_median=21.0),
    )
    # A bucket says where it starts, as the empty ones are left out: steps at 40,000 and 150,000 tokens only.
    apart = context_evidence(MODEL, 30, [calls[1], calls[3].model_copy(update={"input_tokens": 150_000})], [], 0, 0)
    assert [(bucket.above, bucket.up_to) for bucket in apart.buckets] == [(32_000, 64_000), (96_000, 192_000)]
    nothing = context_evidence(MODEL, 30, [], [], 0, 0)
    assert (nothing.steps, nothing.input_median, nothing.cached_share, nothing.buckets) == (0, None, None, ())
