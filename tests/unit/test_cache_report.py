"""Why a run's calls were read from the provider's cache or were not, and the hour a cache is written for (item 1742).

The owner's runs of 2026-11-10 to 11-24 read 94 % of their input from the cache; what was not came from a chat's first
step after a pause, a step that turned a tool group on and a prompt that changed between two runs of one chat. None of
them was told in a run's own numbers, so a cold step was a guess."""

import pytest

from dotagent.agent.cache_report import (
    CacheReport,
    Mark,
    StepCall,
    cache_report,
    cache_ttl_for,
    changed_parts,
)
from dotagent.agent.context_parts import ContextParts, parts_of
from dotagent.core.types import JsonObject
from dotagent.llm.anthropic import claude_traits
from dotagent.llm.codec_messages import MessagesOptions, _merge_usage, build_messages_body
from dotagent.llm.types import ChatRequest, ToolSpec, empty_usage, system_message, user_message

_CLAUDE: MessagesOptions = MessagesOptions(
    adaptive_thinking=True, prompt_cache=True, settled_mark=True, default_max_tokens=None, long_ttl=True
)
_GATEWAY: MessagesOptions = MessagesOptions(
    adaptive_thinking=False, prompt_cache=True, settled_mark=False, default_max_tokens=32768, long_ttl=False
)


def _call(ts: float, input_tokens: int, cached: int, seconds: float = 4.0) -> StepCall:
    return StepCall(ts=ts, seconds=seconds, input_tokens=input_tokens, cached_tokens=cached, cache_write_tokens=0)


def _parts(system: dict[str, str], tools: dict[str, str]) -> ContextParts:
    return ContextParts(
        system={name: len(text) for name, text in system.items()},
        tools={name: len(text) for name, text in tools.items()},
        tools_offered=len(tools),
        history={},
        tokens=0,
        system_hashes=system,
        tools_hashes=tools,
    )


@pytest.mark.parametrize(
    ("gap_s", "ttl"),
    [
        (None, "5m"),
        (60.0, "5m"),
        (300.0, "5m"),
        (301.0, "1h"),
        (1800.0, "1h"),
        (3300.0, "1h"),
        (3301.0, "5m"),
        (86400.0, "5m"),
    ],
)
def test_a_chat_called_again_after_a_pause_the_five_minutes_lose_writes_its_cache_for_an_hour(
    gap_s: float | None, ttl: str
) -> None:
    assert cache_ttl_for(gap_s) == ttl


def _request(ttl: str) -> ChatRequest:
    return ChatRequest(
        model="claude-opus-5-5",
        messages=(system_message("You are Dot."), user_message("Hello.", ())),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="high",
        speed="standard",
        session_key="thread",
        cache_mark=None,
        prompt_reuse="conversation",
        window=None,
        cache_ttl="1h" if ttl == "1h" else "5m",
    )


def test_an_hour_is_asked_of_the_provider_that_takes_it_and_never_of_the_gateway() -> None:
    traits = claude_traits(
        {"id": "claude-opus-5-5", "max_input_tokens": 1_000_000, "max_tokens": 128_000, "capabilities": {}}
    )
    long: JsonObject = build_messages_body(_request("1h"), traits, "anthropic", _CLAUDE)
    short: JsonObject = build_messages_body(_request("5m"), traits, "anthropic", _CLAUDE)
    gateway: JsonObject = build_messages_body(_request("1h"), traits, "go", _GATEWAY)
    assert long["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # The five minutes are the provider's own: no field, so a request of the usual kind is byte for byte what it was.
    assert short["cache_control"] == {"type": "ephemeral"}
    assert gateway["cache_control"] == {"type": "ephemeral"}


def test_the_tokens_a_provider_wrote_to_its_cache_are_counted_inside_the_input_and_apart() -> None:
    usage = _merge_usage(
        empty_usage(),
        {"input_tokens": 12, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 88, "output_tokens": 5},
    )
    assert (usage.input_tokens, usage.cached_tokens, usage.cache_write_tokens) == (1000, 900, 88)
    # A later, cumulative usage object that leaves the field out keeps what was said.
    later = _merge_usage(usage, {"output_tokens": 40})
    assert (later.cache_write_tokens, later.output_tokens) == (88, 40)


def test_the_parts_of_a_request_carry_fingerprints_that_move_when_a_section_does_and_not_when_it_does_not() -> None:
    def request(prompt: str, tool_description: str) -> ChatRequest:
        return _request("5m").model_copy(
            update={
                "messages": (system_message(prompt), user_message("Hi.", ())),
                "tools": (ToolSpec(name="shell", description=tool_description, parameters={"type": "object"}),),
            }
        )

    one: ContextParts = parts_of(request("Intro.\n\n# Skills\nfirst one\n\n# Environment\nToday: Monday", "runs"))
    same: ContextParts = parts_of(request("Intro.\n\n# Skills\nfirst one\n\n# Environment\nToday: Monday", "runs"))
    other: ContextParts = parts_of(request("Intro.\n\n# Skills\nsecond one\n\n# Environment\nToday: Monday", "runs"))
    assert one.system_hashes == same.system_hashes and one.tools_hashes == same.tools_hashes
    assert changed_parts(same, one) == ()
    # "first one" and "second one" differ in size too, but a change that keeps the size shows by its fingerprint.
    swapped: ContextParts = parts_of(request("Intro.\n\n# Skills\nfirst two\n\n# Environment\nToday: Monday", "runs"))
    assert one.system == swapped.system
    assert changed_parts(swapped, one) == ("system:Skills",)
    assert changed_parts(other, one) == ("system:Skills",)
    retooled: ContextParts = parts_of(request("Intro.\n\n# Skills\nfirst one\n\n# Environment\nToday: Monday", "runs!"))
    assert changed_parts(retooled, one) == ("tools:core",)


def test_a_run_recorded_before_fingerprints_is_compared_by_the_sizes_it_has() -> None:
    old = ContextParts(system={"Skills": 10, "Team": 5}, tools={"core": 100}, tools_offered=3, history={}, tokens=0)
    now = ContextParts(
        system={"Skills": 12, "Team": 5}, tools={"core": 100, "browser": 50}, tools_offered=4, history={}, tokens=0
    )
    assert changed_parts(now, old) == ("system:Skills", "tools:browser")


def test_a_first_step_is_warm_or_cold_for_a_reason_the_report_names() -> None:
    warm = cache_report([_call(100.0, 100_000, 95_000)], [], None, None, 120.0)
    assert warm is not None and warm.first_cause == "warm" and warm.share == 0.95
    new_chat = cache_report([_call(100.0, 22_000, 600)], [], None, None, None)
    assert new_chat is not None and new_chat.first_cause == "new_chat"
    before = _parts({"Skills": "a"}, {"core": "x"})
    after = _parts({"Skills": "b"}, {"core": "x"})
    changed = cache_report([_call(100.0, 110_000, 2_000)], [], after, before, 600.0)
    assert changed is not None and changed.first_cause == "prefix_changed" and changed.changed == ("system:Skills",)
    paused = cache_report([_call(100.0, 110_000, 2_000)], [], before, before, 7200.0)
    assert paused is not None and paused.first_cause == "after_pause" and paused.changed == ()
    odd = cache_report([_call(100.0, 110_000, 2_000)], [], before, before, 90.0)
    assert odd is not None and odd.first_cause == "unexplained"


def test_a_later_step_that_met_a_cold_cache_is_told_with_what_happened_before_it() -> None:
    calls = [
        _call(100.0, 100_000, 99_000),
        _call(110.0, 101_000, 99_500),
        # Turned a tool group on between the second step's end and this step's start (ends at 130, took 9 s).
        _call(130.0, 101_500, 800, seconds=9.0),
        _call(140.0, 102_000, 101_000),
        # Folded the history, then asked again.
        _call(160.0, 40_000, 1_000, seconds=12.0),
        # Cold, and nothing marked it: the provider did not hold.
        _call(175.0, 41_000, 500, seconds=5.0),
        # A step that only grew by a big tool result is no cold step: its start was read from the cache.
        _call(180.0, 80_000, 41_000),
    ]
    marks = [Mark(kind="tool_group", ts=119.0), Mark(kind="compaction", ts=148.0)]
    report: CacheReport | None = cache_report(calls, marks, None, None, 30.0)
    assert report is not None
    assert [(step.step, step.cause) for step in report.cold_steps] == [
        (3, "tool_group"),
        (5, "compaction"),
        (6, "other"),
    ]
    assert report.cold_steps[0].uncached_tokens == 100_700
    assert report.steps == 7 and report.first_cause == "warm"


def test_a_run_that_measured_no_step_has_no_report() -> None:
    assert cache_report([], [], None, None, None) is None
