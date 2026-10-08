"""The scaffold of a run: the budget that follows the model's window, the tier that follows its refused tool calls, the
reminders of a weak model, and the profile those come from, read from a real database file."""

import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.scaffold import (
    Scaffold,
    choose_tier,
    reminder_due,
    require_room,
    scaffold_event,
    scaffold_for,
)
from dotagent.cli_profile import render_profile
from dotagent.config import AgentConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.llm.ollama import parse_model_details
from dotagent.llm.types import INVALID_ARGUMENTS_KEY, ModelInfo
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.store.stats import ModelProfile, read_model_profile

# The packaged defaults: context_tokens 48 000, tool_output_chars 12 000, scaffold "fixed", profile_min_calls 50,
# weak_refusal_share 0.1, reminder_every 6.
DEFAULTS: AgentConfig = load_default_config().agent
MEASURED: AgentConfig = DEFAULTS.model_copy(update={"scaffold": "measured"})


def _info(window: int | None) -> ModelInfo:
    return ModelInfo(id="m", vision=False, tools=True, thinking=False, context_length=window, efforts=())


def _profile(model: str, tool_calls: int, refused_calls: int) -> ModelProfile:
    return ModelProfile(
        model=model,
        runs=30,
        steps=300,
        tool_calls=tool_calls,
        tool_errors=refused_calls,
        refused_calls=refused_calls,
        invalid_arguments=0,
        empty_replies=0,
        stalls=0,
        compactions=0,
    )


def test_the_budget_follows_a_small_window_and_the_tool_output_shrinks_with_it() -> None:
    quiet: ModelProfile = _profile("ollama:a", 0, 0)
    small: Scaffold = scaffold_for(DEFAULTS, _info(32768), quiet)
    assert (small.window, small.context_tokens, small.tool_output_chars) == (32768, 24576, 6144)
    # Tool output never shrinks below 2000 characters.
    tiny: Scaffold = scaffold_for(DEFAULTS, _info(8192), quiet)
    assert (tiny.context_tokens, tiny.tool_output_chars) == (6144, 2000)
    # A large window, or none reported, leaves the configured budgets as they are.
    for window in (64000, 1_048_576, None):
        roomy: Scaffold = scaffold_for(DEFAULTS, _info(window), quiet)
        assert (roomy.context_tokens, roomy.tool_output_chars) == (48000, 12000)
        assert scaffold_event(roomy, DEFAULTS)["cut_to_window"] is False
    assert scaffold_event(small, DEFAULTS) == {
        "model": "ollama:a",
        "window": 32768,
        "context_tokens": 24576,
        "tool_output_chars": 6144,
        "cut_to_window": True,
        "tier": "standard",
        "reason": '[agent] scaffold is "fixed"',
        "reminder_every": None,
    }


def test_the_tier_is_measured_only_when_asked_and_a_hand_set_tier_always_wins() -> None:
    flaky: ModelProfile = _profile("ollama:a", 100, 10)
    assert choose_tier(DEFAULTS, flaky) == ("standard", '[agent] scaffold is "fixed"')
    assert choose_tier(MEASURED, flaky) == (
        "weak",
        "10 of 100 tool calls in the newest 30 runs were refused before they ran (10.0%), at or above 10%",
    )
    assert choose_tier(MEASURED, _profile("ollama:a", 100, 9))[0] == "standard"
    assert choose_tier(MEASURED, _profile("ollama:a", 49, 49)) == (
        "standard",
        "49 tool calls measured, 50 needed for a judgement",
    )
    by_hand: AgentConfig = DEFAULTS.model_copy(update={"tiers": {"ollama:a": "weak", "ollama:b": "standard"}})
    assert choose_tier(by_hand, _profile("ollama:a", 0, 0)) == ("weak", "set by hand in [agent] tiers")
    measured_by_hand: AgentConfig = by_hand.model_copy(update={"scaffold": "measured"})
    assert choose_tier(measured_by_hand, _profile("ollama:b", 100, 90))[0] == "standard"
    assert scaffold_for(by_hand, _info(None), _profile("ollama:a", 0, 0)).reminder_every == 6
    assert scaffold_for(by_hand, _info(None), _profile("ollama:b", 0, 0)).reminder_every is None


def test_a_weak_model_is_reminded_every_few_steps_of_a_task_or_research_run() -> None:
    weak: Scaffold = scaffold_for(MEASURED, _info(None), _profile("ollama:a", 100, 50))
    assert not reminder_due(weak, "task", 5, 0)
    assert reminder_due(weak, "task", 6, 0)
    assert not reminder_due(weak, "task", 11, 6)
    assert reminder_due(weak, "task", 12, 6)
    assert reminder_due(weak, "research", 6, 0)
    assert not reminder_due(weak, "chat", 6, 0)
    standard: Scaffold = scaffold_for(MEASURED, _info(None), _profile("ollama:a", 100, 0))
    assert not reminder_due(standard, "task", 60, 0)


def test_a_window_that_cannot_hold_the_fixed_part_of_the_prompt_refuses_the_run() -> None:
    tiny: Scaffold = scaffold_for(DEFAULTS, _info(8192), _profile("ollama:a", 0, 0))
    require_room(tiny, 6143)
    with pytest.raises(ConfigError, match="window is too small"):
        require_room(tiny, 6144)


def test_ollama_reports_the_smaller_of_the_model_window_and_num_ctx() -> None:
    shown: JsonObject = {"capabilities": ["completion", "tools"], "model_info": {"gptoss.context_length": 131072}}
    assert parse_model_details("gpt-oss:20b", shown, 65536).info.context_length == 65536
    assert parse_model_details("gpt-oss:20b", shown, 262144).info.context_length == 131072
    # A model that does not say its window gets the one every request is sent with.
    assert parse_model_details("m", {"capabilities": ["tools"]}, 65536).info.context_length == 65536


def _call(call_id: str, args: JsonObject) -> tuple[str, JsonObject]:
    return "tool_call", {"call_id": call_id, "tool": "shell", "args": args}


def _result(call_id: str, ok: bool, risk: str) -> tuple[str, JsonObject]:
    return "tool_result", {"call_id": call_id, "tool": "shell", "ok": ok, "text": "", "images": [], "risk": risk}


def _reply(text: str, tools: list[str]) -> tuple[str, JsonObject]:
    return "assistant", {"step": 1, "text": text, "tool_calls": list(tools)}


async def _run(store: Store, thread_id: str, model: str, events: list[tuple[str, JsonObject]]) -> RunRecord:
    run: RunRecord = await store.create_run(thread_id, "goal", "task", model, None, "test", "medium", "ask", False)
    for kind, data in events:
        await store.add_event(run.id, kind, data)
    await store.update_run_progress(run.id, len(events), 1000, 100)
    return run


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Path]:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    newest: RunRecord = await _run(
        store,
        thread.id,
        "ollama:a",
        [
            _call("1", {"command": "ls"}),
            _result("1", True, "low"),
            # Refused before it ran: arguments that were not JSON.
            _call("2", {INVALID_ARGUMENTS_KEY: "{"}),
            _result("2", False, ""),
            # Ran and failed: the environment's doing, not a refusal.
            _call("3", {"command": "false"}),
            _result("3", False, "low"),
            _reply("  ", []),
            _reply("", ["shell"]),
            _reply("The answer.", []),
            ("stall", {"warnings": 1}),
            ("compacted", {"compaction": 1}),
        ],
    )
    await store.finish_run(newest.id, "done", "answer")
    older: RunRecord = await _run(store, thread.id, "ollama:a", [_call("1", {"command": "x"}), _result("1", False, "")])
    await store.finish_run(older.id, "failed", "error")
    # Neither an unfinished run nor another model's run counts.
    await _run(store, thread.id, "ollama:a", [_call("1", {"command": "y"}), _result("1", False, "")])
    other: RunRecord = await _run(store, thread.id, "ollama:b", [_call("1", {"command": "z"}), _result("1", False, "")])
    await store.finish_run(other.id, "done", "answer")
    await store.close()
    now: float = time.time()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (now - 100, older.id))
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (now - 50, newest.id))
    yield path


def test_the_profile_counts_the_newest_finished_runs_of_one_model(database: Path) -> None:
    profile: ModelProfile = read_model_profile(database, "ollama:a", 30)
    assert (profile.runs, profile.steps) == (2, 13)
    assert (profile.tool_calls, profile.tool_errors, profile.refused_calls) == (4, 3, 2)
    assert (profile.invalid_arguments, profile.empty_replies, profile.stalls, profile.compactions) == (1, 1, 1, 1)
    newest: ModelProfile = read_model_profile(database, "ollama:a", 1)
    assert (newest.runs, newest.tool_calls, newest.refused_calls) == (1, 3, 1)
    assert read_model_profile(database, "ollama:none", 30).runs == 0


def test_a_profile_and_the_scaffold_of_the_next_run_are_rendered_in_plain_words(database: Path) -> None:
    profile: ModelProfile = read_model_profile(database, "ollama:a", 30)
    assert render_profile(profile, scaffold_for(MEASURED, _info(32768), profile)) == [
        "ollama:a, its newest 2 finished runs: 13 steps",
        "  tools: 4 calls, 3 failed (75 %), 2 refused before they ran (50 %), 1 with arguments that were not valid JSON",
        "  replies with neither text nor a tool call 1, stalls 1, compactions 1",
        "Its next run:",
        "  window 32,768 tokens, context budget 24,576 tokens, tool output up to 6,144 characters",
        "  tier standard: 4 tool calls measured, 50 needed for a judgement",
        "  no reminders of the goal",
    ]
    unknown: ModelProfile = read_model_profile(database, "ollama:none", 30)
    weak: AgentConfig = DEFAULTS.model_copy(update={"tiers": {"ollama:none": "weak"}})
    assert render_profile(unknown, scaffold_for(weak, _info(None), unknown)) == [
        "ollama:none: no finished runs yet",
        "Its next run:",
        "  window not reported by its provider, context budget 48,000 tokens, tool output up to 12,000 characters",
        "  tier weak: set by hand in [agent] tiers",
        "  reminded of the goal every 6 steps in task and research runs",
    ]
